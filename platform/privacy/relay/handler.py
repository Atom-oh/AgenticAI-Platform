"""IAM-only relay to one private gateway. Never log payloads or exception text."""
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import time
from collections import Counter
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 8192
MAX_TEXT_BYTES = 32768
MAX_RESPONSE_BYTES = 65536
MAX_SECONDS = 135
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,159}\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TOKEN = re.compile(r"⟨([A-Z][A-Z0-9_]{0,39}):(?:[0-9a-f]{8}|[0-9a-f]{32}|[a-p]{32})⟩")
_TYPES = frozenset({
    "PERSON", "NAME", "RRN", "PHONE", "EMAIL", "ADDRESS", "ACCOUNT", "CARD",
    "DOB", "REL", "IP", "PASSPORT", "DATE", "ORG", "LOCATION", "URL",
    "CUSTOMER_ID", "ACCOUNT_ID", "RESIDENT_ID", "FOREIGNER_ID",
})
_STATUSES = frozenset({
    "ready", "available", "unavailable", "notconfigured", "not_configured",
    "not-configured", "disabled", "unknown", "passed", "pass", "blocked", "failed", "error",
    "ok", "clean", "checked", "skipped", "configured", "unconfigured",
})
_ERRORS = {
    "PRIVACY_INVALID_REQUEST": "Invalid privacy request.",
    "PRIVACY_NOT_CONFIGURED": "Private privacy gateway is not configured.",
    "PRIVACY_UNAVAILABLE": "Private privacy gateway is unavailable.",
    "PRIVACY_GATEWAY_REJECTED": "Private privacy inspection did not pass.",
    "PRIVACY_INVALID_RESPONSE": "Private privacy gateway returned an invalid response.",
}


def _failure(code):
    return {"ok": False, "error": {"code": code, "message": _ERRORS[code]}}


def _identifier(value):
    return (isinstance(value, str) and _IDENTIFIER.fullmatch(value)
            and ".." not in value)


def _number(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= 1_000_000_000)


def _evidence(value, request, output):
    """Validate the gateway's proof before releasing text; never return raw fields."""
    if not isinstance(value, dict):
        raise ValueError("invalid receipt")

    required = {
        "processor": "eks-sllm", "status": "pass", "method": "redaction",
        "model": request["model"],
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise ValueError("invalid inspection")
    result = dict(required)
    for key in ("modelId", "modelRevision", "promptVersion"):
        if not _identifier(value.get(key)):
            raise ValueError("missing model identity")
        result[key] = value[key]

    for key in ("total", "sourceChars", "outputChars", "ruleResidualCount"):
        if type(value.get(key)) is not int or not _number(value[key]):
            raise ValueError("invalid receipt count")
        result[key] = value[key]
    if (value["sourceChars"] != len(request["text"])
            or value["outputChars"] != len(output)
            or value["total"] > value["sourceChars"]
            or value["ruleResidualCount"] != 0):
        raise ValueError("inconsistent inspection")
    # A detector that reports no replacements must not rewrite financial facts.
    if (value["total"] == 0) != (output == request["text"]):
        raise ValueError("inconsistent redaction")

    counts = value.get("entityCounts")
    if not isinstance(counts, list) or len(counts) > len(_TYPES):
        raise ValueError("missing entity counts")
    seen, clean_counts = set(), []
    for item in counts:
        if (not isinstance(item, dict) or not isinstance(item.get("type"), str)
                or item["type"] not in _TYPES or item["type"] in seen
                or type(item.get("count")) is not int
                or not 0 < item["count"] <= value["sourceChars"]):
            raise ValueError("invalid entity count")
        seen.add(item["type"])
        clean_counts.append({"type": item["type"], "count": item["count"]})
    if sum(item["count"] for item in clean_counts) != value["total"]:
        raise ValueError("inconsistent entity counts")
    # Count occurrences, not unique tokens: repeated originals share a token.
    # Previously redacted payloads keep their markers and do not inflate total.
    before = Counter(match.group(0) for match in _TOKEN.finditer(request["text"]))
    after = Counter(match.group(0) for match in _TOKEN.finditer(output))
    if any(after[token] < count for token, count in before.items()):
        raise ValueError("existing redaction changed")
    added = Counter()
    for token, count in (after - before).items():
        added[_TOKEN.fullmatch(token).group(1)] += count
    if added != Counter({item["type"]: item["count"] for item in clean_counts}):
        raise ValueError("replacement evidence mismatch")
    result["entityCounts"] = clean_counts

    if value.get("independentNer") not in ("pass", "not-configured"):
        raise ValueError("invalid independent inspection")
    if not _number(value.get("latencyMs")):
        raise ValueError("invalid inspection latency")
    result["independentNer"] = value["independentNer"]
    result["latencyMs"] = value["latencyMs"]
    for key, limit in (("modelDetections", 64 * value["sourceChars"]),
                       ("ruleSupplements", value["total"])):
        if key in value:
            if type(value[key]) is not int or not 0 <= value[key] <= limit:
                raise ValueError("invalid detector count")
            result[key] = value[key]
    if "modelDetections" in value and "ruleSupplements" in value:
        # Gateway modelDetections counts all candidate occurrences BEFORE
        # overlap/deduplication. Supplements count chosen rule-only spans; the
        # remaining chosen spans cannot exceed the model's candidate count.
        if (value["total"] - value["ruleSupplements"] > value["modelDetections"]
                or (value["total"] == 0 and value["modelDetections"] != 0)):
            raise ValueError("inconsistent detector counts")
    if "scope" in value:
        if value["scope"] != "configured-identifiers-not-a-guarantee-of-anonymity":
            raise ValueError("invalid privacy scope")
        result["scope"] = value["scope"]
    return result


def _models(value):
    entries = value.get("models")
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError("invalid catalog")
    models = []
    for item in entries:
        if not isinstance(item, dict) or not _identifier(item.get("id")):
            raise ValueError("invalid model")
        model = {"id": item["id"]}
        # Model labels come from the server-maintained catalog, never the request
        # or generative model. Other free-form fields are not forwarded.
        label = item.get("label")
        if isinstance(label, str) and 0 < len(label) <= 200 and not any(ord(c) < 32 for c in label):
            model["label"] = label
        for key in ("modelId", "revision", "modelRevision", "processor"):
            if _identifier(item.get(key)):
                model[key] = item[key]
        if item.get("family") in ("qwen", "gemma", "deepseek"):
            model["family"] = item["family"]
        for key in ("configured", "ready", "available"):
            if type(item.get(key)) is bool:
                model[key] = item[key]
        if isinstance(item.get("status"), str) and item["status"] in _STATUSES:
            model["status"] = item["status"]
        if item.get("reason") in {
                "model-not-configured", "model-identity-mismatch", "model-unavailable",
                "invalid-model-output", "private-endpoint-required", "model-redirect-refused"}:
            model["reason"] = item["reason"]
        models.append(model)
    result = {"ok": True, "models": models}
    if value.get("processor") == "eks-sllm":
        result["processor"] = "eks-sllm"
    if value.get("defaultModel") in [m["id"] for m in models]:
        result["defaultModel"] = value["defaultModel"]
    return result


def _request(method, path, body, context):
    # A fixed AWS NLB hostname plus VPC-CIDR DNS validation; callers cannot choose
    # a host, port, path, header or proxy. Connect to the validated IP to avoid a
    # second DNS lookup changing the destination. NLB SG accepts only this relay.
    endpoint = urlsplit(os.environ.get("PRIVACY_GATEWAY_URL", ""))
    if (endpoint.scheme != "http" or endpoint.port != 8080
            or endpoint.username or endpoint.password or endpoint.path
            or endpoint.query or endpoint.fragment
            or not re.fullmatch(
                r"[a-z0-9-]+\.(?:elb\.ap-northeast-2|ap-northeast-2\.elb)\.amazonaws\.com",
                endpoint.hostname or "")):
        raise ValueError("invalid private endpoint")
    network = ipaddress.ip_network(os.environ.get("PRIVACY_VPC_CIDR", ""), strict=True)
    if (network.version != 4 or not network.subnet_of(ipaddress.ip_network("10.0.0.0/8"))
            or not 16 <= network.prefixlen <= 28):
        # Deployment uses the existing FSI VPC. Do not accept default-route CIDRs.
        raise ValueError("invalid private network")
    seconds = MAX_SECONDS
    if context is not None:
        seconds = min(seconds, context.get_remaining_time_in_millis() / 1000 - 3)
    if seconds <= 0:
        raise TimeoutError()
    deadline = time.monotonic() + seconds
    addresses = socket.getaddrinfo(endpoint.hostname, 8080, socket.AF_INET, socket.SOCK_STREAM)
    if not addresses or any(ipaddress.ip_address(a[4][0]) not in network for a in addresses):
        raise ValueError("endpoint outside VPC")
    connection = http.client.HTTPConnection(addresses[0][4][0], 8080, timeout=min(5, seconds))
    try:
        encoded = None if body is None else json.dumps(
            body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        connection.request(method, path, body=encoded, headers={
            "Host": f"{endpoint.hostname}:8080",
            "Content-Type": "application/json", "Accept": "application/json",
        })
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        # http.client never follows redirects. Do not read non-200 bodies at all.
        if response.status != 200:
            return response.status, None
        if response.getheader("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise ValueError("invalid content type")
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise ValueError("encoded response")
        declared = response.getheader("Content-Length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES):
            raise ValueError("oversized response")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("oversized response")
            chunks.append(chunk)
        return 200, json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        connection.close()


def handler(event, context):
    if not isinstance(event, dict):
        return _failure("PRIVACY_INVALID_REQUEST")
    operation = event.get("operation")
    if operation == "models" and set(event) == {"operation"}:
        method, path, body = "GET", "/models", None
    elif operation == "deidentify" and set(event) == {
            "operation", "text", "model", "purpose", "requestId"}:
        text = event.get("text")
        try:
            valid_text = isinstance(text, str) and bool(text.strip()) and len(text.encode("utf-8")) <= MAX_INPUT_BYTES
        except UnicodeError:
            valid_text = False
        if (not valid_text or not _identifier(event.get("model"))
                or not isinstance(event.get("purpose"), str)
                or event["purpose"] not in ("query", "payload", "evaluation")
                or not isinstance(event.get("requestId"), str)
                or not _REQUEST_ID.fullmatch(event["requestId"])):
            return _failure("PRIVACY_INVALID_REQUEST")
        method, path = "POST", "/deidentify"
        body = {k: event[k] for k in ("text", "model", "purpose", "requestId")}
    else:
        return _failure("PRIVACY_INVALID_REQUEST")
    if not os.environ.get("PRIVACY_GATEWAY_URL"):
        return _failure("PRIVACY_NOT_CONFIGURED")
    try:
        status, value = _request(method, path, body, context)
    except Exception:
        return _failure("PRIVACY_UNAVAILABLE")
    if status != 200:
        return _failure("PRIVACY_GATEWAY_REJECTED")
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        return _failure("PRIVACY_INVALID_RESPONSE")
    if value["ok"] is False:
        return _failure("PRIVACY_GATEWAY_REJECTED")
    try:
        if operation == "models":
            return _models(value)
        text = value.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("invalid output text")
        return {"ok": True, "text": text, "evidence": _evidence(value.get("evidence"), body, text)}
    except Exception:
        return _failure("PRIVACY_INVALID_RESPONSE")
