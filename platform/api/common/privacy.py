"""IAM invocation of the private EKS privacy relay. No raw exception forwarding."""
from __future__ import annotations

import json
import os
import re

import boto3
from botocore.config import Config
from . import pii

TYPES = frozenset(("PERSON", "PHONE", "EMAIL", "ADDRESS", "RRN", "ACCOUNT", "CARD", "DOB", "REL", "PASSPORT"))
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,159}\Z")
MAX_BYTES = 8192


class PrivacyUnavailable(Exception):
    def __init__(self, code="PRIVACY_UNAVAILABLE"):
        self.code = code if re.fullmatch(r"[A-Z][A-Z_]{0,79}", str(code)) else "PRIVACY_UNAVAILABLE"
        super().__init__("개인정보 처리를 완료하지 못해 상담 모델 호출을 차단했습니다.")


def _invoke(payload: dict) -> dict:
    function = os.environ.get("MYDATA_PRIVACY_FUNCTION_ARN", "")
    if not re.fullmatch(r"arn:aws:lambda:ap-northeast-2:\d{12}:function:[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?", function):
        raise PrivacyUnavailable("PRIVACY_NOT_CONFIGURED")
    try:
        client = boto3.client("lambda", region_name="ap-northeast-2", config=Config(
            connect_timeout=5, read_timeout=155, retries={"total_max_attempts": 1}))
        response = client.invoke(FunctionName=function, InvocationType="RequestResponse",
                                 Payload=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
        if response.get("FunctionError"):
            raise PrivacyUnavailable()
        raw = response["Payload"].read(65537)
        if len(raw) > 65536:
            raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = (result.get("error") or {}).get("code") if isinstance(result, dict) else ""
            raise PrivacyUnavailable(code)
        return result
    except PrivacyUnavailable:
        raise
    except Exception:
        raise PrivacyUnavailable() from None


def models() -> dict:
    result = _invoke({"operation": "models"})
    if result.get("processor") != "eks-sllm" or not isinstance(result.get("models"), list) or len(result["models"]) > 16:
        raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
    return result


def process(text: str, model: str, purpose: str, request_id: str) -> dict:
    if (not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_BYTES
            or not isinstance(model, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", model)
            or purpose not in ("query", "payload")
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id)):
        raise PrivacyUnavailable("PRIVACY_INVALID_REQUEST")
    if purpose == "query" and pii.tokens_in(text):
        raise PrivacyUnavailable("PRIVACY_RESERVED_TOKEN_INPUT")
    result = _invoke({"operation": "deidentify", "text": text, "model": model,
                      "purpose": purpose, "requestId": request_id})
    output, evidence = result.get("text"), result.get("evidence")
    if not isinstance(output, str) or not output.strip() or len(output.encode()) > 32768 or not isinstance(evidence, dict):
        raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
    if (evidence.get("processor") != "eks-sllm" or evidence.get("method") != "redaction"
            or evidence.get("status") != "pass" or evidence.get("model") != model
            or type(evidence.get("ruleResidualCount")) is not int or evidence["ruleResidualCount"] != 0
            or evidence.get("independentNer") not in ("not-configured", "pass")
            or type(evidence.get("sourceChars")) is not int or type(evidence.get("outputChars")) is not int
            or evidence.get("sourceChars") != len(text) or evidence.get("outputChars") != len(output)):
        raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
    for key in ("modelId", "modelRevision", "promptVersion"):
        if not isinstance(evidence.get(key), str) or not IDENTIFIER.fullmatch(evidence[key]):
            raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
    counts = evidence.get("entityCounts")
    if (not isinstance(counts, list) or len(counts) > len(TYPES) or type(evidence.get("total")) is not int
            or not 0 <= evidence["total"] <= 4096):
        raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
    seen, total = set(), 0
    for item in counts:
        if (not isinstance(item, dict) or item.get("type") not in TYPES or item["type"] in seen
                or type(item.get("count")) is not int or not 1 <= item["count"] <= 4096):
            raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
        seen.add(item["type"])
        total += item["count"]
    if total != evidence["total"]:
        raise PrivacyUnavailable("PRIVACY_INVALID_RESPONSE")
    # Only named receipt fields go to stages/traces. Never forward debug/original
    # entity fields even if a future processor adds them.
    allowed = ("status", "processor", "method", "model", "modelId", "modelRevision", "promptVersion",
               "entityCounts", "total", "sourceChars", "outputChars", "ruleResidualCount", "independentNer")
    public = {key: evidence[key] for key in allowed}
    public["entityCounts"] = [{"type": item["type"], "count": item["count"]} for item in counts]
    if type(evidence.get("latencyMs")) is int and 0 <= evidence["latencyMs"] <= 150000:
        public["latencyMs"] = evidence["latencyMs"]
    return {"ok": True, "text": output, "evidence": public}
