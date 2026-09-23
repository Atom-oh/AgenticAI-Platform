import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api")]
from common import pii


@pytest.fixture
def boundary(monkeypatch):
    spec = importlib.util.spec_from_file_location("runtime_independent_gate", ROOT / "agents/boundary_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    def inspect(**request):
        calls.append(request)
        text = request["content"][0]["text"]["text"]
        return {"action": "NONE", "assessments": [], "usage": {"sensitiveInformationPolicyUnits": 1},
                "guardrailCoverage": {"textCharacters": {"guarded": len(text), "total": len(text)}}}
    client = SimpleNamespace(apply_guardrail=inspect)
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "synthetic")
    monkeypatch.setattr(pii.boto3, "client", lambda *args, **kwargs: client)
    return module, client, calls


def test_runtime_verifies_system_content_messages_and_tool_specs(boundary):
    module, client, calls = boundary
    hook = module.BoundaryGateHook()
    agent = SimpleNamespace(messages=[{"role": "user", "content": [{"text": "question"}]}],
        system_prompt="obsolete", _system_prompt_content=[{"text": "current system"}],
        tool_registry=SimpleNamespace(get_all_tool_specs=lambda: [{"name": "lookup", "description": "tool metadata"}]))
    hook.before_model_call(SimpleNamespace(agent=agent))
    assert len(calls) == 1
    text = calls[0]["content"][0]["text"]["text"]
    assert all(value in text for value in ["question", "current system", "tool metadata"])
    assert "obsolete" not in text
    result = hook.drain()[0]
    assert result["piiDetectors"] == ["rules", "guardrail"] and result["piiCount"] == 0


@pytest.mark.parametrize("failure", ["name", "partial", "unavailable"])
def test_runtime_blocks_failed_or_positive_independent_inspection(boundary, failure):
    module, client, calls = boundary
    original = client.apply_guardrail
    def inspect(**request):
        result = original(**request)
        if failure == "name":
            result["assessments"] = [{"sensitiveInformationPolicy": {
                "piiEntities": [{"type": "NAME", "action": "NONE", "detected": True}]}}]
        elif failure == "partial":
            result["guardrailCoverage"]["textCharacters"]["guarded"] = 0
        else:
            raise RuntimeError("PRIVATE_UPSTREAM")
        return result
    client.apply_guardrail = inspect
    hook = module.BoundaryGateHook()
    with pytest.raises(module.GateRefused if failure == "name" else pii.PiiVerificationUnavailable):
        hook.check([{"role": "user", "content": [{"text": "Synthetic person"}]}])
    if failure == "name":
        result = hook.drain()[0]
        assert result["piiRules"] == 0 and result["piiCount"] == 1
        assert result["hits"] == ["NAME"] and result["piiDetectors"] == ["rules", "guardrail"]


def test_runtime_local_refusal_does_not_send_identifiers_for_remote_inspection(boundary):
    module, client, calls = boundary
    hook = module.BoundaryGateHook()
    with pytest.raises(module.GateRefused):
        hook.check([{"role": "user", "content": [{"text": "CUST-0042"}]}])
    assert calls == [] and hook.drain()[0]["piiDetectors"] == ["rules"]


def test_runtime_retains_complete_larger_context_and_rejects_opaque_media(boundary):
    module, client, calls = boundary
    hook = module.BoundaryGateHook()
    text = "context " * 4000
    hook.check([{"role": "user", "content": [{"text": text}]}])
    assert calls[0]["content"][0]["text"]["text"] == "user\n" + text
    for content in [{"image": {"source": {"bytes": b"private"}}},
                    {"document": {"source": {"s3Location": {"uri": "s3://private"}}}}]:
        with pytest.raises(ValueError):
            hook.check([{"role": "user", "content": [content]}])
    assert len(calls) == 1
