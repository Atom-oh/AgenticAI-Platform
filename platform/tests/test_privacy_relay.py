"""The relay must not become an HTTP proxy or an error/receipt PII channel."""
import importlib.util
import io
import json
from pathlib import Path

import pytest


HANDLER = Path(__file__).parents[1] / "privacy" / "relay" / "handler.py"


@pytest.fixture
def relay(monkeypatch):
    assert HANDLER.is_file(), "private relay handler has not been implemented"
    spec = importlib.util.spec_from_file_location("privacy_relay", HANDLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv(
        "PRIVACY_GATEWAY_URL",
        "http://bank-privacy-0123456789abcdef.elb.ap-northeast-2.amazonaws.com:8080",
    )
    monkeypatch.setenv("PRIVACY_VPC_CIDR", "10.0.0.0/16")
    return module


def event(**overrides):
    return {"operation": "deidentify", "text": "합성 고객 예시",
            "model": "qwen", "purpose": "query", "requestId": "request-1",
            **overrides}


def receipt(**overrides):
    return {
        "status": "pass", "processor": "eks-sllm", "method": "redaction",
        "model": "qwen", "modelId": "Qwen/Qwen3-8B", "modelRevision": "unverified",
        "entityCounts": [{"type": "PERSON", "count": 1}],
        "total": 1, "modelDetections": 1, "ruleSupplements": 0,
        "sourceChars": 8, "outputChars": 17, "ruleResidualCount": 0,
        "independentNer": "not-configured", "promptVersion": "pii-v1",
        "latencyMs": 22, "scope": "configured-identifiers-not-a-guarantee-of-anonymity",
        **overrides,
    }


def success(**overrides):
    return {"ok": True, "text": "⟨PERSON:0123abcd⟩", "evidence": receipt(), **overrides}


def test_forward_only_fixed_operation_and_body(relay, monkeypatch):
    calls = []

    def request(method, path, body, context):
        calls.append((method, path, body))
        return 200, success(evidence=receipt(entities=[{"original": "PRIVATE"}]),
                            raw_entities=["PRIVATE"])

    monkeypatch.setattr(relay, "_request", request)
    result = relay.handler(event(), None)
    assert result["ok"] is True
    assert result["text"] == "⟨PERSON:0123abcd⟩"
    assert "PRIVATE" not in json.dumps(result)
    assert calls == [("POST", "/deidentify", {
        "text": "합성 고객 예시", "model": "qwen",
        "purpose": "query", "requestId": "request-1"})]


@pytest.mark.parametrize("payload", [
    {"operation": "http", "url": "http://example.com"},
    event(url="http://example.com"),
    event(text="가" * 2731),  # UTF-8 bytes, not character count.
    event(text=""), event(text=None), event(model="../models"),
    event(requestId="bad\nheader"), event(purpose={"url": "http://example.com"}),
    [], None,
])
def test_invalid_input_never_calls_gateway(relay, monkeypatch, payload):
    def forbidden(*args):
        pytest.fail("invalid request reached private gateway")
    monkeypatch.setattr(relay, "_request", forbidden)
    result = relay.handler(payload, None)
    assert result["ok"] is False
    assert result["error"]["code"] == "PRIVACY_INVALID_REQUEST"


def test_models_receipt_has_no_internal_endpoint_or_entities(relay, monkeypatch):
    monkeypatch.setattr(relay, "_request", lambda *args: (200, {
        "ok": True, "defaultModel": "qwen", "models": [
            {"id": "qwen", "family": "qwen", "modelId": "Qwen/Qwen3-8B",
             "configured": True, "ready": True, "status": "ready",
             "endpoint": "http://internal-secret", "raw": "PRIVATE"},
            {"id": "gemma", "family": "gemma", "configured": False,
             "ready": False, "status": "notconfigured"},
        ]}))
    result = relay.handler({"operation": "models"}, None)
    assert result["ok"] is True
    assert result["models"][1]["ready"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert "endpoint" not in json.dumps(result)


@pytest.mark.parametrize("status, body", [
    (302, {"text": "PRIVATE"}),
    (500, {"error": "PRIVATE"}),
    (200, {"ok": False, "error": {"code": "SECRET_PRIVATE", "message": "PRIVATE"}}),
    (200, {"ok": True, "text": 12, "evidence": {}}),
    (200, {"ok": True, "text": "X" * 32769, "evidence": {}}),
    (200, {"ok": True, "text": "ok", "evidence": []}),
])
def test_failure_has_no_raw_gateway_error(relay, monkeypatch, capsys, status, body):
    monkeypatch.setattr(relay, "_request", lambda *args: (status, body))
    result = relay.handler(event(), None)
    assert result["ok"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert capsys.readouterr() == ("", "")


def test_transport_exception_is_scrubbed(relay, monkeypatch, capsys):
    def unavailable(*args):
        raise TimeoutError("PRIVATE customer value")
    monkeypatch.setattr(relay, "_request", unavailable)
    result = relay.handler(event(), None)
    assert result["error"]["code"] == "PRIVACY_UNAVAILABLE"
    assert "PRIVATE" not in json.dumps(result)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("url", [
    "https://example.com", "http://169.254.169.254:8080",
    "http://bank.elb.ap-northeast-2.amazonaws.com:8080/deidentify",
    "http://user:pass@bank.elb.ap-northeast-2.amazonaws.com:8080",
])
def test_bad_config_cannot_make_http_request(relay, monkeypatch, url):
    monkeypatch.setenv("PRIVACY_GATEWAY_URL", url)
    result = relay.handler(event(), None)
    assert result["ok"] is False


def test_transport_does_not_follow_redirect_or_read_error_body(relay, monkeypatch):
    class Response:
        status = 302
        def getheader(self, name, default=None):
            return "http://169.254.169.254/" if name == "Location" else default
        def read1(self, size):
            pytest.fail("redirect body must not be read")

    calls = []
    class Connection:
        sock = None
        def __init__(self, host, port, timeout):
            assert host == "10.0.1.8"
        def request(self, method, path, body=None, headers=None):
            calls.append((method, path))
        def getresponse(self):
            return Response()
        def close(self):
            pass

    monkeypatch.setattr(relay.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("10.0.1.8", 8080))])
    monkeypatch.setattr(relay.http.client, "HTTPConnection", Connection)
    result = relay.handler(event(), None)
    assert result["ok"] is False
    assert calls == [("POST", "/deidentify")]


def test_transport_rejects_dns_outside_fixed_vpc(relay, monkeypatch):
    monkeypatch.setattr(relay.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("192.0.2.15", 8080))])
    monkeypatch.setattr(relay.http.client, "HTTPConnection",
                        lambda *a, **k: pytest.fail("out-of-VPC address connected"))
    assert relay.handler(event(), None)["ok"] is False


def test_transport_response_limit(relay, monkeypatch):
    class Response(io.BytesIO):
        status = 200
        def getheader(self, name, default=None):
            return "application/json" if name == "Content-Type" else default

    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response(b"X" * 65537)
        def close(self):
            pass

    monkeypatch.setattr(relay.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("10.0.1.8", 8080))])
    monkeypatch.setattr(relay.http.client, "HTTPConnection", Connection)
    assert relay.handler(event(), None)["ok"] is False


def test_preserves_current_gateway_receipt_contract(relay, monkeypatch):
    evidence = receipt()
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(evidence=evidence)))
    assert relay.handler(event(), None)["evidence"] == evidence


@pytest.mark.parametrize("missing", [
    "processor", "status", "method", "model", "modelId", "modelRevision",
    "promptVersion", "entityCounts", "total", "sourceChars", "outputChars",
    "ruleResidualCount", "independentNer", "latencyMs",
])
def test_missing_required_evidence_blocks_output(relay, monkeypatch, capsys, missing):
    evidence = receipt()
    del evidence[missing]
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(evidence=evidence)))
    result = relay.handler(event(), None)
    assert result["error"]["code"] == "PRIVACY_INVALID_RESPONSE"
    assert "text" not in result and "evidence" not in result
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("changes", [
    {"processor": "rules"}, {"status": "blocked"}, {"method": "rewrite"},
    {"model": "gemma4"}, {"modelId": ""}, {"modelRevision": ""},
    {"promptVersion": "PRIVATE\n"}, {"entityCounts": []},
    {"entityCounts": [{"type": "PERSON", "count": 1.0}]},
    {"entityCounts": [{"type": "PERSON", "count": True}]},
    {"entityCounts": [{"type": "PERSON", "count": 0}]},
    {"entityCounts": [{"type": "PERSON", "count": -1}]},
    {"entityCounts": [{"type": "PRIVATE", "count": 1}]},
    {"entityCounts": [{"type": "PERSON", "count": 1}, {"type": "PERSON", "count": 1}], "total": 2},
    {"total": 2}, {"total": True}, {"total": 1.0},
    {"sourceChars": 999}, {"sourceChars": True}, {"outputChars": 999},
    {"outputChars": 17.0}, {"ruleResidualCount": 1},
    {"ruleResidualCount": False}, {"independentNer": "failed"},
    {"independentNer": "skipped"}, {"latencyMs": float("nan")},
    {"latencyMs": float("inf")}, {"latencyMs": -1},
    {"modelDetections": True}, {"modelDetections": -1},
    {"ruleSupplements": 2}, {"modelDetections": 0, "ruleSupplements": 0},
    {"scope": "full-anonymity"},
])
def test_contradictory_evidence_blocks_output(relay, monkeypatch, capsys, changes):
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(evidence=receipt(**changes))))
    result = relay.handler(event(), None)
    assert result["ok"] is False
    assert "text" not in result and "evidence" not in result
    assert "PRIVATE" not in json.dumps(result)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("text", ["", " ", "합성 고객 예시"])
def test_empty_or_unchanged_redaction_is_not_success(relay, monkeypatch, text):
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(
        text=text, evidence=receipt(outputChars=len(text)))))
    assert relay.handler(event(), None)["ok"] is False


def test_zero_detections_must_preserve_input(relay, monkeypatch):
    clean = event(text="금리 3%")
    evidence = receipt(entityCounts=[], total=0, modelDetections=0,
                       sourceChars=5, outputChars=5)
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(text="금리 3%", evidence=evidence)))
    assert relay.handler(clean, None)["ok"] is True
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(text="금리 9%", evidence=evidence)))
    assert relay.handler(clean, None)["ok"] is False


@pytest.mark.parametrize("output", [
    "redacted", "⟨PASSPORT:0123abcd⟩", "⟨PERSON:0123abcd⟩ ⟨PERSON:0123abcd⟩",
])
def test_replacement_tokens_must_match_receipt_counts(relay, monkeypatch, output):
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(
        text=output, evidence=receipt(outputChars=len(output)))))
    assert relay.handler(event(), None)["ok"] is False


def test_existing_tokens_are_preserved_and_not_counted_as_new_redactions(relay, monkeypatch):
    request = event(text="⟨PERSON:aaaaaaaa⟩ 고객")
    output = "⟨PERSON:aaaaaaaa⟩ ⟨PERSON:0123abcd⟩"
    evidence = receipt(sourceChars=20, outputChars=35)
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(text=output, evidence=evidence)))
    assert relay.handler(request, None)["ok"] is True
    monkeypatch.setattr(relay, "_request", lambda *a: (200, success(
        text=output.replace("aaaaaaaa", "bbbbbbbb"), evidence=evidence)))
    assert relay.handler(request, None)["ok"] is False


def test_real_gateway_receipts_support_passport_and_overlapping_detections(relay, monkeypatch):
    from privacy.gateway.engine import deidentify
    from privacy.gateway.models import Model

    model = Model("qwen", "Qwen", "qwen", "Qwen/Qwen3-8B", "http://unused")
    text = "합성 고객 M12345678"
    response = {"model": model.model_id, "choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps({"entities": [
            {"type": "PERSON", "original": "합성 고객"},
            {"type": "PERSON", "original": "고객"},
        ]})},
    }]}
    value = deidentify(text, model, infer=lambda *a: response)
    monkeypatch.setattr(relay, "_request", lambda *a: (200, value))
    result = relay.handler(event(text=text), None)
    assert result["ok"] is True
    assert result["evidence"] == value["evidence"]
    assert {i["type"] for i in result["evidence"]["entityCounts"]} == {"PERSON", "PASSPORT"}
