import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb, call, indexed
from workspace.collaboration import CollaborationError


def rpc(wb, method, params=None, **kwargs):
    return call(wb, "POST", "mcp", {"jsonrpc": "2.0", "id": 7, "method": method,
                                  "params": params or {}}, **kwargs)


def test_mcp_negotiates_and_reads_real_index_without_owner_override(wb):
    indexed(wb)
    result = rpc(wb, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}})
    assert result["result"]["capabilities"]["tools"] == {"listChanged": False}
    tools = rpc(wb, "tools/list")["result"]["tools"]
    assert {"knowledge.search", "knowledge.read", "impact.read", "skills.read"} <= {x["name"] for x in tools}
    found = rpc(wb, "tools/call", {"name": "knowledge.search", "arguments": {"q": "withdrawal"}})
    assert found["result"]["structuredContent"]["items"][0]["title"] == "Synthetic withdrawal guidance"
    assert found["result"]["isError"] is False
    bad = rpc(wb, "tools/call", {"name": "knowledge.search", "arguments": {"owner": "other", "q": "x"}})
    assert bad["error"]["code"] == -32602
    assert rpc(wb, "tools/call", {"name": "shell.execute"})["error"]["code"] == -32602


def test_tool_registration_is_operator_only_and_never_enables_arbitrary_execution(wb):
    request = {"requestId": "tool", "name": "evidence", "description": "근거 읽기",
               "toolNames": ["knowledge.search", "knowledge.read"]}
    with pytest.raises(CollaborationError):
        call(wb, "POST", "tools", request)
    tool = call(wb, "POST", "tools", request, groups=["admin"])["tool"]
    assert tool["toolNames"] == request["toolNames"]
    with pytest.raises(CollaborationError):
        call(wb, "POST", "tools", {**request, "requestId": "shell", "toolNames": ["shell.execute"]}, groups=["admin"])


def test_mcp_only_resolves_exact_current_approved_bytes_and_rejects_deprecation(wb):
    from test_workbench_skills import draft
    skill = draft(wb)
    args = {"id": skill["id"], "version": skill["version"], "contentHash": skill["contentHash"]}
    assert rpc(wb, "tools/call", {"name": "skills.read", "arguments": args})["result"]["isError"]


    wb.api.workbench_gate = lambda **kw: {"allowed": True, "receipt": {"inspected": True, "bytes": 10}}
    wb.api.workbench_evaluator = lambda **kw: {
        "status": "passed", "cases": [{"input": "안내 검토", "passed": True}], "evaluator": "test"}
    validated = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})["skill"]
    approved = call(wb, "POST", f"skills/{skill['id']}/approve",
                    {"version": validated["version"], "contentHash": skill["contentHash"]})["skill"]
    args["version"] = approved["version"]
    result = rpc(wb, "tools/call", {"name": "skills.read", "arguments": args})["result"]
    assert result["isError"] is False and "근거를 읽고" in result["structuredContent"]["files"]["SKILL.md"]
    args["contentHash"] = "0" * 64
    assert rpc(wb, "tools/call", {"name": "skills.read", "arguments": args})["result"]["isError"]
    args["contentHash"] = approved["contentHash"]
    call(wb, "POST", f"skills/{skill['id']}/deprecate", {"version": approved["version"], "reason": "교체"})
    assert rpc(wb, "tools/call", {"name": "skills.read", "arguments": args})["result"]["isError"]


@pytest.mark.parametrize("name", [[], {}, 12, None])
def test_mcp_malformed_tool_name_is_a_bounded_protocol_error(wb, name):
    result = rpc(wb, "tools/call", {"name": name, "arguments": {}})
    assert result["error"]["code"] == -32602
