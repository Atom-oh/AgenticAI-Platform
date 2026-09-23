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
        assert request["guardrailIdentifier"] == "synthetic-guardrail"
        assert request["guardrailVersion"] == "7" and request["source"] == "INPUT"
        assert len(request["content"]) == 1
        text = request["content"][0]["text"]["text"]
        return {"action": "NONE", "assessments": [],
                "usage": {"sensitiveInformationPolicyUnits": 1},
                "guardrailCoverage": {"textCharacters": {"total": len(text), "guarded": len(text)}}}

    client = SimpleNamespace(apply_guardrail=apply_guardrail)
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "synthetic-guardrail")
    monkeypatch.setattr(pii, "GUARDRAIL_VER", "7")
    monkeypatch.setattr(pii.boto3, "client", lambda *args, **kwargs: client)
    return client


def context():
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "platform___synthetic"}))

def captured_text(capsys):
    captured = capsys.readouterr()
    return captured.out + captured.err


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
    serialized = json.dumps(result) + captured_text(capsys)
    for original in ["CUST-0042", "person@example.invalid", "UPSTREAM_BODY_SENTINEL", "lambda.py"]:
        assert original not in serialized


def test_gateway_exception_body_is_never_returned_or_logged(monkeypatch, capsys):
    def fail(args):
        raise RuntimeError("UPSTREAM_BODY_SENTINEL Traceback")
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", fail)
    result = gateway_tools.handler({}, context())
    assert result["code"] == "TOOL_FAILED"
    assert "UPSTREAM_BODY_SENTINEL" not in json.dumps(result) + captured_text(capsys)


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
    captured = capsys.readouterr()
    logs = captured.out
    assert marker not in json.dumps(result) + logs + captured.err
    boundary = [json.loads(line) for line in logs.splitlines()][-1]
    assert boundary["piiDetectors"] == ["rules", "guardrail"] and boundary["blocked"]


@pytest.mark.parametrize("failure", ["missing", "coverage", "service", "length"])
@pytest.mark.parametrize("side", ["input", "output"])
def test_gateway_strict_verification_failure_blocks_input_and_result(monkeypatch, verified_guardrail, failure, side, capsys):
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
    assert "PRIVATE_UPSTREAM_MARKER" not in json.dumps(result) + captured_text(capsys)
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
    assert requests == ['{"reg_code":"REG-LN-001"}', serialized]
    large_source = {"code": "const x = 'synthetic';\\n" * 300}
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: {"ok": True})
    assert gateway_tools.handler(large_source, context()) == {"ok": True}
    assert requests[2:] == [json.dumps(large_source, ensure_ascii=False, separators=(",", ":")), '{"ok":true}']


def test_shared_verifier_keeps_the_existing_default_bound(verified_guardrail):
    from common import pii
    verified_guardrail.apply_guardrail = lambda **kwargs: pytest.fail("default verifier sent oversized text")
    with pytest.raises(pii.PiiVerificationUnavailable):
        pii.scan_outbound("safe" * 1001, strict=True)
    for invalid in [0, -1, 100001, True]:
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


def test_gate_summary_keeps_indeterminate_accessibility_and_blocks_success():
    body = {"ok": True, **{name: {"ok": True} for name in ("build", "types", "lint", "a11y", "visual")}}
    body["a11y"]["incomplete"] = [{"id": "color-contrast", "help": "PRIVATE_HELP_MARKER"}]
    result = gateway_tools._gate_summary(body)
    assert result["ok"] is False and result["a11y"]["ok"] is False
    assert result["a11y"]["incompleteCount"] == 1
    assert result["a11y"]["incomplete"] == [{"id": "color-contrast"}]
    assert "PRIVATE_HELP_MARKER" not in json.dumps(result)


def test_screen_gate_aggregate_rejects_unapproved_component_evidence(monkeypatch):
    import io
    import boto3
    from registry import api
    body = {"ok": True, **{name: {"ok": True} for name in ("build", "types", "lint", "a11y", "visual")}}
    monkeypatch.setenv("GATES_FN", "synthetic-gates")
    monkeypatch.setattr(api, "list_approved", lambda **kwargs: [
        {"name": "Button", "recordVersion": "v2", "payload": {}}])
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: SimpleNamespace(
        invoke=lambda **request: {"Payload": io.BytesIO(json.dumps(body).encode())}))
    result = gateway_tools.tool_run_screen_gates({"code": "// Button@v3\nexport default function Screen() {}"})
    assert result["registry"]["ok"] is False and result["ok"] is False


@pytest.mark.parametrize("failure", ["name", "coverage", "service", "missing"])
def test_harness_requires_independent_verification_before_transport(monkeypatch, verified_guardrail, failure):
    from agentcore import harness
    from common import pii
    from engine.gate import GateRefused
    normal = verified_guardrail.apply_guardrail

    def inspect(**request):
        result = normal(**request)
        if failure == "name":
            result["assessments"] = [{"sensitiveInformationPolicy": {
                "piiEntities": [{"type": "NAME", "action": "NONE", "detected": True}]}}]
        elif failure == "coverage":
            result["guardrailCoverage"]["textCharacters"]["guarded"] = 0
        elif failure == "service":
            raise RuntimeError("PRIVATE_UPSTREAM_MARKER")
        return result

    verified_guardrail.apply_guardrail = inspect
    if failure == "missing":
        monkeypatch.setattr(pii, "GUARDRAIL_ID", "")
    monkeypatch.setattr(harness, "data", lambda: pytest.fail("unverified Harness input reached transport"))
    expected = GateRefused if failure == "name" else pii.PiiVerificationUnavailable
    with pytest.raises(expected):
        list(harness.invoke_stream("synthetic-harness", "Synthetic natural language", "s" * 64))
    monkeypatch.setattr(harness, "GATEWAY_ARN", "arn:synthetic:gateway")
    with pytest.raises(expected):
        harness.build_config({"name": "synthetic", "allowedTools": ["list_regulations"],
                              "skills": [], "skillBindings": [], "systemPrompt": "Synthetic natural language"})


def test_harness_admitted_input_reports_both_real_detector_paths(monkeypatch):
    from agentcore import harness
    calls = []
    def invoke(**request):
        calls.append(request)
        return {"stream": [
            {"contentBlockDelta": {"delta": {"text": "safe"}}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1}}},
        ]}
    monkeypatch.setattr(harness, "data", lambda: SimpleNamespace(invoke_harness=invoke))
    events = list(harness.invoke_stream("synthetic-harness", "safe", "s" * 64))
    assert len(calls) == 1 and events[0][0] == "boundary"
    assert events[0][1]["piiDetectors"] == ["rules", "guardrail"]


def test_domain_not_found_keeps_actionable_guidance(monkeypatch):
    from graph.store import LocalGraphStore
    monkeypatch.setattr(gateway_tools, "_store", LocalGraphStore())
    ctx = SimpleNamespace(client_context=SimpleNamespace(custom={
        "bedrockAgentCoreToolName": "platform___analyze_regulation_impact"}))
    result = gateway_tools.handler({"reg_code": "UNKNOWN"}, ctx)
    assert result["found"] is False and result["reason"] == "regulation-not-found"
    assert "list_regulations" in result["hint"] and "error" not in result


def test_residual_tool_identifier_is_a_privacy_refusal(monkeypatch):
    def refused(args):
        raise gateway_tools.ToolBoundaryRefused(["CUSTOMER_TOKEN"])
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", refused)
    result = gateway_tools.handler({}, context())
    assert result["code"] == "BOUNDARY_REFUSED" and result["piiTypes"] == ["CUSTOMER_TOKEN"]
    assert result["piiDetectors"] == ["rules"]


def test_guardrail_only_passport_variant_blocks_output(monkeypatch, verified_guardrail):
    original = verified_guardrail.apply_guardrail
    def inspect(**request):
        result = original(**request)
        if "m12345678" in request["content"][0]["text"]["text"]:
            result["assessments"] = [{"sensitiveInformationPolicy": {"regexes": [
                {"name": "KR_PASSPORT", "action": "BLOCKED", "match": "m12345678"}]}}]
        return result
    verified_guardrail.apply_guardrail = inspect
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: {"reference": "m12345678"})
    assert gateway_tools.handler({}, context())["code"] == "BOUNDARY_REFUSED"


def test_rule_positive_output_never_reaches_cloud_inspection(monkeypatch, verified_guardrail):
    requests = []
    original = verified_guardrail.apply_guardrail
    def inspect(**request):
        text = request["content"][0]["text"]["text"]
        assert "CUST-0042" not in text
        requests.append(text)
        return original(**request)
    verified_guardrail.apply_guardrail = inspect
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: {"value": "CUST-0042"})
    assert gateway_tools.handler({}, context())["code"] == "BOUNDARY_REFUSED"
    assert requests == ["{}"]


@pytest.mark.parametrize("boundary", ["gateway", "harness", "runtime"])
def test_non_pii_policy_intervention_blocks_without_inventing_pii(monkeypatch, verified_guardrail, boundary):
    from common import pii
    from agentcore import harness
    from engine.gate import GateRefused
    normal = verified_guardrail.apply_guardrail
    def denied(**request):
        value = normal(**request)
        value.update(action="GUARDRAIL_INTERVENED", assessments=[{"topicPolicy": {"topics": [{"action": "BLOCKED"}]}}])
        return value
    verified_guardrail.apply_guardrail = denied
    if boundary == "gateway":
        monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: pytest.fail("denied input executed"))
        assert gateway_tools.handler({}, context())["code"] == "BOUNDARY_REFUSED"
    elif boundary == "harness":
        monkeypatch.setattr(harness, "data", lambda: pytest.fail("denied input reached model"))
        with pytest.raises(GateRefused) as error:
            list(harness.invoke_stream("synthetic", "Synthetic content"))
        assert error.value.count == 0 and error.value.boundary["verification"]["policyDenied"]
    else:
        from tests.test_agents_runtime import boundary_gate
        gate = boundary_gate.BoundaryGateHook()
        with pytest.raises(boundary_gate.GateRefused):
            gate.check([{"role": "user", "content": [{"text": "Synthetic content"}]}])
        assert gate.last["policyDenied"] and gate.last["piiCount"] == 0


@pytest.mark.parametrize("terminal", [False, True])
def test_harness_requires_terminal_event(monkeypatch, terminal):
    from agentcore import harness
    events = [{"contentBlockDelta": {"delta": {"text": "partial"}}}]
    if terminal:
        events.append({"messageStop": {"stopReason": "end_turn"}})
    monkeypatch.setattr(harness, "data", lambda: SimpleNamespace(invoke_harness=lambda **kwargs: {"stream": events}))
    result = list(harness.invoke_stream("synthetic", "Synthetic content"))
    assert result[-1][1]["incomplete"] is not terminal
    assert any(kind == "error" for kind, _ in result) is not terminal


@pytest.mark.parametrize("outcome", ["incomplete", "transport_error", "close", "complete"])
def test_harness_session_reuse_requires_known_terminal_history(monkeypatch, outcome):
    from agentcore import harness
    from agentcore.session_store import HarnessSessionUnavailable
    from registry import api
    from registry.store import RegistryStore
    store = api.reset_for_tests()
    calls = []
    def transport(**kwargs):
        calls.append(kwargs["runtimeSessionId"])
        if outcome == "transport_error":
            raise RuntimeError("Synthetic transport failure")
        events = [{"contentBlockDelta": {"delta": {"text": "partial"}}}]
        if outcome == "complete":
            events.append({"messageStop": {"stopReason": "end_turn"}})
        return {"stream": events}
    monkeypatch.setattr(harness, "data", lambda: SimpleNamespace(invoke_harness=transport))
    sid = "opaque-harness-session-" + "a" * 40
    stream = harness.invoke_stream("synthetic", "Synthetic content", sid)
    if outcome == "close":
        assert next(stream)[0] == "boundary"
        assert next(stream)[0] == "text"
        stream.close()
    elif outcome == "transport_error":
        with pytest.raises(RuntimeError):
            list(stream)
    else:
        list(stream)
    # A distinct store instance uses the same durable table, as another WsFn would.
    monkeypatch.setattr(api, "get_store", lambda: RegistryStore(table=store.table()))
    if outcome == "complete":
        list(harness.invoke_stream("synthetic", "Next safe turn", sid))
        assert calls == [sid, sid]
    else:
        with pytest.raises(HarnessSessionUnavailable):
            list(harness.invoke_stream("synthetic", "Next safe turn", sid))
        assert calls == [sid]
    assert not store.all_records(), "Internal session state must not enter Registry discovery"


def test_harness_rule_refusal_exposes_measured_rules_only_evidence(monkeypatch):
    from agentcore import harness
    from engine.gate import GateRefused
    monkeypatch.setattr(harness, "data", lambda: pytest.fail("rule-positive input reached service"))
    stream = harness.invoke_stream("synthetic", "person@example.invalid")
    kind, boundary = next(stream)
    assert kind == "boundary" and boundary["chars"] > 0 and boundary["estTokens"] > 0
    assert boundary["piiCount"] > 0 and boundary["refusedTypes"] == ["EMAIL"]
    assert boundary["piiDetectors"] == ["rules"]
    with pytest.raises(GateRefused):
        next(stream)
