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
