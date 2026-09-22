"""The actual Gateway Lambda boundary must block raw identifiers and diagnostics."""
import json
from types import SimpleNamespace

import pytest

from agentcore import gateway_tools


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
    assert result["code"] in {"BOUNDARY_REFUSED", "TOOL_REJECTED"}
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


def test_gateway_measurement_failure_blocks_output(monkeypatch):
    from engine import gate
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: {"safe": True})
    monkeypatch.setattr(gate, "measure", lambda *args: (_ for _ in ()).throw(RuntimeError("scan failed")))
    assert gateway_tools.handler({}, context())["code"] == "TOOL_FAILED"


def test_gateway_input_is_inspected_before_any_nested_tool_model_call(monkeypatch):
    calls = []
    monkeypatch.setitem(gateway_tools.TOOLS, "synthetic", lambda args: calls.append(args))
    assert gateway_tools.handler({"question": "CUST-0042"}, context())["code"] == "BOUNDARY_REFUSED"
    assert calls == []


def test_gate_summary_keeps_verdicts_without_backend_diagnostic_bodies():
    body = {"ok": False, **{name: {"ok": True} for name in ("build", "types", "lint", "a11y", "visual")}}
    body["types"] = {"ok": False, "errors": [{"code": 2322, "line": 8, "column": 3,
                                            "message": "UPSTREAM_BODY_SENTINEL /var/task/private.py"}]}
    summary = gateway_tools._gate_summary(body)
    assert summary["ok"] is False and summary["types"]["ok"] is False
    assert summary["types"]["errors"] == [{"code": 2322, "line": 8, "column": 3}]
    assert "UPSTREAM_BODY_SENTINEL" not in json.dumps(summary)
    with pytest.raises(ValueError):
        gateway_tools._gate_summary({"statusCode": 500, "body": {"stackTrace": ["private"]}})
