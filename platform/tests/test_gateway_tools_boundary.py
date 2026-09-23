"""The actual Gateway Lambda boundary must block raw identifiers and diagnostics."""
import json
from types import SimpleNamespace

import pytest

from agentcore import gateway_tools


@pytest.fixture(autouse=True)
def verified_guardrail(monkeypatch):
    """Exercise the real strict verifier with synthetic service responses."""
    from common import pii

    def apply_guardrail(**request):
        text = request["content"][0]["text"]["text"]
        return {"action": "NONE", "assessments": [],
                "usage": {"sensitiveInformationPolicyUnits": 1},
                "guardrailCoverage": {"textCharacters": {"total": len(text), "guarded": len(text)}}}

    client = SimpleNamespace(apply_guardrail=apply_guardrail)
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "synthetic-guardrail")
    monkeypatch.setattr(pii.boto3, "client", lambda *args, **kwargs: client)
    return client


def context():
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "platform___synthetic"}))


@pytest.mark.parametrize("output", [
    {"nested": {"customerId": "CUST-0042"}},
    {"items": [{"contact": "person@example.invalid"}]},
    {"error": "UPSTREAM_BODY_SENTINEL Traceback lambda.py"},
    {"errorMessage": "UPSTREAM_BODY_SENTINEL", "stackTrace": ["lambda.py:1"]},
    {"statusCode": 500, "body": {"stackTrace": ["UPSTREAM_BODY_SENTINEL"]}},
    {"result": [{"nested": {"errorMessage": "UPSTREAM_BODY_SENTINEL"}}]},
])
def test_gateway_outgoing_payload_is_independently_checked(monkeypatch, capsys, output):
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: output)
    result = gateway_tools.handler({}, context())
    expected = "BOUNDARY_REFUSED" if "nested" in output or "items" in output else "TOOL_REJECTED"
    assert result["code"] == expected
    serialized = json.dumps(result) + capsys.readouterr().out
    for original in ["CUST-0042", "person@example.invalid", "UPSTREAM_BODY_SENTINEL", "lambda.py"]:
        assert original not in serialized


def test_gateway_exception_body_is_never_returned_or_logged(monkeypatch, capsys):
    def fail(args):
        raise RuntimeError("UPSTREAM_BODY_SENTINEL Traceback")
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", fail)
    result = gateway_tools.handler({}, context())
    assert result["code"] == "TOOL_FAILED"
    assert "UPSTREAM_BODY_SENTINEL" not in json.dumps(result) + capsys.readouterr().out


def test_gateway_boundary_preserves_exact_financial_values(monkeypatch, capsys):
    output = {"rate": "3.250", "limit": 123456789, "deductions": ["0.15", "0.20"]}
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: output)
    assert gateway_tools.handler({}, context()) == output
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    boundary = next(row for row in logs if row["event"] == "tool.boundary")
    assert boundary["chars"] > 0 and boundary["piiCount"] == 0 and boundary["blocked"] is False


@pytest.mark.parametrize("failed_call", [1, 2])
def test_gateway_measurement_failure_blocks_input_or_output(monkeypatch, failed_call):
    from engine import gate
    calls, measurements = [], []
    measure = gate.measure

    def inspect(*args):
        measurements.append(True)
        if len(measurements) == failed_call:
            raise RuntimeError("scan failed")
        return measure(*args)

    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: calls.append(args) or {"safe": True})
    monkeypatch.setattr(gate, "measure", inspect)
    assert gateway_tools.handler({}, context())["code"] == "TOOL_FAILED"
    assert len(calls) == failed_call - 1
    assert len(measurements) == failed_call


@pytest.mark.parametrize("side", ["input", "output"])
def test_gateway_guardrails_only_identifier_blocks_boundary(monkeypatch, verified_guardrail, capsys, side):
    calls = []
    marker = "Synthetic Private Person"
    normal = verified_guardrail.apply_guardrail

    def inspect(**request):
        response = normal(**request)
        if marker in request["content"][0]["text"]["text"]:
            response["assessments"] = [{"sensitiveInformationPolicy": {
                "piiEntities": [{"type": "NAME", "action": "NONE", "detected": True, "match": marker}]}}]
        return response

    verified_guardrail.apply_guardrail = inspect
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic",
                        lambda args: calls.append(args) or {"name": marker})
    result = gateway_tools.handler({"name": marker} if side == "input" else {}, context())
    assert result["code"] == "BOUNDARY_REFUSED"
    assert len(calls) == (0 if side == "input" else 1)
    logs = capsys.readouterr().out
    assert marker not in json.dumps(result) + logs
    boundary = [json.loads(line) for line in logs.splitlines()][-1]
    assert boundary["piiDetectors"] == ["rules", "guardrail"] and boundary["blocked"]


@pytest.mark.parametrize("failure", ["missing", "coverage", "service", "length"])
@pytest.mark.parametrize("side", ["input", "output"])
def test_gateway_strict_verification_failure_blocks_input_and_result(monkeypatch, verified_guardrail, failure, side):
    calls, requests = [], []
    normal = verified_guardrail.apply_guardrail

    def inspect(**request):
        requests.append(request)
        response = normal(**request)
        if len(requests) == (1 if side == "input" else 2):
            if failure == "service":
                raise RuntimeError("PRIVATE_UPSTREAM_MARKER")
            if failure == "coverage":
                response["guardrailCoverage"]["textCharacters"]["guarded"] -= 1
            if failure == "missing":
                response["usage"] = {}
        return response

    def tool(args):
        calls.append(args)
        return {"result": "safe" * (5100 if failure == "length" and side == "output" else 1)}

    verified_guardrail.apply_guardrail = inspect
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", tool)
    args = {"question": "safe" * 5100} if failure == "length" and side == "input" else {}
    result = gateway_tools.handler(args, context())
    assert calls == ([{}] if side == "output" else []) and result["code"] == "TOOL_FAILED"
    assert "PRIVATE_UPSTREAM_MARKER" not in json.dumps(result)
    assert len(requests) == (1 if side == "input" else 2) - (1 if failure == "length" else 0)


def test_gateway_missing_verifier_blocks_before_tool(monkeypatch):
    from common import pii
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "")
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: pytest.fail("unverified input reached tool"))
    assert gateway_tools.handler({}, context())["code"] == "TOOL_FAILED"


def test_gateway_input_is_inspected_before_tool_execution(monkeypatch, verified_guardrail):
    calls = []
    verified_guardrail.apply_guardrail = lambda **kwargs: pytest.fail("identified input reached cloud inspection")
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: calls.append(args))
    assert gateway_tools.handler({"question": "CUST-0042"}, context())["code"] == "BOUNDARY_REFUSED"
    assert calls == []


def test_gateway_uses_context_tool_name_and_rejects_unknown_tools(monkeypatch):
    calls = []
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: calls.append("synthetic") or {"ok": True})
    monkeypatch.setitem(gateway_tools.TOOLS, "other", lambda args: pytest.fail("payload selected tool"))
    assert gateway_tools.handler({"tool": "other"}, context()) == {"ok": True}
    assert calls == ["synthetic"]
    ctx = SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "platform___unknown"}))
    assert gateway_tools.handler({"tool": "synthetic"}, ctx)["code"] == "UNKNOWN_TOOL"
    for missing in [None, SimpleNamespace(), SimpleNamespace(client_context=SimpleNamespace(custom={})),
                    SimpleNamespace(client_context=SimpleNamespace(custom=["invalid"]))]:
        assert gateway_tools.handler({"tool": "synthetic"}, missing)["code"] == "UNKNOWN_TOOL"
    assert calls == ["synthetic"]


def test_gateway_checks_complete_seed_sized_impact_and_large_source(monkeypatch, verified_guardrail):
    from pathlib import Path
    from graph.store import get_store
    monkeypatch.setenv("GRAPH_BACKEND", "local")
    monkeypatch.setattr(gateway_tools, "_store", get_store(Path(__file__).resolve().parents[1] / "seed" / "out"))
    expected = gateway_tools.tool_analyze_regulation_impact({"reg_code": "REG-LN-001"})
    serialized = json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    assert 4000 < len(serialized) < gateway_tools.MAX_VERIFICATION_CHARS
    requests = []
    normal = verified_guardrail.apply_guardrail

    def inspect(**request):
        requests.append(request["content"][0]["text"]["text"])
        return normal(**request)

    verified_guardrail.apply_guardrail = inspect
    ctx = SimpleNamespace(client_context=SimpleNamespace(custom={
        "bedrockAgentCoreToolName": "platform___analyze_regulation_impact"}))
    assert gateway_tools.handler({"reg_code": "REG-LN-001"}, ctx) == expected
    assert requests[-1] == serialized
    large_source = {"code": "const x = 'synthetic';\\n" * 300}
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: {"ok": True})
    assert gateway_tools.handler(large_source, context()) == {"ok": True}
    assert json.dumps(large_source, ensure_ascii=False, separators=(",", ":")) in requests


def test_shared_verifier_keeps_the_existing_default_bound(verified_guardrail):
    from common import pii
    verified_guardrail.apply_guardrail = lambda **kwargs: pytest.fail("default verifier sent oversized text")
    with pytest.raises(pii.PiiVerificationUnavailable):
        pii.scan_outbound("safe" * 1001, strict=True)
    for invalid in [0, -1, 20001, True]:
        with pytest.raises(pii.PiiVerificationUnavailable):
            pii.scan_guardrail("safe", strict=True, max_chars=invalid)


def test_gate_summary_keeps_verdicts_without_backend_diagnostic_bodies():
    body = {"ok": False, **{name: {"ok": True} for name in ("build", "types", "lint", "a11y", "visual")}}
    body["types"] = {"ok": False, "errors": [{"code": 2322, "line": 8, "column": 3,
                                            "message": "UPSTREAM_BODY_SENTINEL /var/task/private.py"}]}
    summary = gateway_tools._gate_summary(body)
    assert summary["ok"] is False and summary["types"]["ok"] is False
    assert summary["types"]["errors"] == [{"code": 2322, "line": 8, "column": 3}]
    assert "UPSTREAM_BODY_SENTINEL" not in json.dumps(summary)
    body["types"]["errors"] = [{"code": "TS2322"}, {"code": "MODULE_NOT_FOUND"}, {"code": "private body"}]
    assert gateway_tools._gate_summary(body)["types"]["errors"] == [
        {"code": "TS2322"}, {"code": "MODULE_NOT_FOUND"}, {}]
    with pytest.raises(ValueError):
        gateway_tools._gate_summary({"statusCode": 500, "body": {"stackTrace": ["private"]}})
