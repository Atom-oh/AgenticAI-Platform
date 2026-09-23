"""Design compatibility emits measured rule evidence for calls and refusals."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api")]
from tests.test_runtime_model_selection import runtime_app, approved_run


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("blocked", [False, True, "policy"])
def test_design_reports_boundary_events_and_refusal_without_originals(runtime_app, monkeypatch, blocked):
    boundary = load("design_boundary", ROOT / "agents/boundary_gate.py")
    monkeypatch.setitem(sys.modules, "boundary_gate", boundary)
    verified = []
    def verify(text):
        verified.append(text)
        if blocked == "policy":
            from common.pii import GuardrailPolicyDenied
            raise GuardrailPolicyDenied("Synthetic denied policy")
        return {"count": 0, "hits": [], "detectors": ["rules", "guardrail"]}
    monkeypatch.setattr(boundary, "verify_independent", verify)
    calls = []

    class Result:
        metrics = SimpleNamespace(accumulated_usage={"inputTokens": 2, "outputTokens": 1})
        def __str__(self):
            return "safe result"

    class Agent:
        def __init__(self, **kwargs):
            self.system, self.gate = kwargs["system_prompt"], kwargs["hooks"][0]

        def __call__(self, user):
            self.gate.check([{"role": "user", "content": [{"text": user}]}], self.system)
            assert verified[-1] == self.system + "\nuser\n" + user
            calls.append("model")
            return Result()

    monkeypatch.setitem(sys.modules, "strands", SimpleNamespace(Agent=Agent))
    deps_module = load("design_deps_evidence", ROOT / "agents/design_deps.py")
    monkeypatch.setattr(deps_module, "_model", lambda *args: object())
    monkeypatch.setitem(sys.modules, "design_deps", deps_module)
    import design_loop

    def run(product, state, checks, deps, *, emit, output_type):
        deps["generate"]("Synthetic system", product["text"], lambda text: emit({"type": "token", "text": text}))
        deps["llm_judge"]({"id": "rule", "text": "Synthetic requirement", "target": "screen"},
                          {"prd": {"steps": [{"id": "step-one"}]}, "flowText": "Synthetic complete flow"})
        return {"ok": True}

    monkeypatch.setattr(design_loop, "run", run)
    app = load("design_app_evidence", ROOT / "agents/app.py")
    text = "CUST-0042" if blocked is True else "Synthetic public content"

    async def exercise():
        payload = {"agent": "design_flow_agent",
                   "design": {"productSpec": {"id": "synthetic", "text": text}, "smModel": {"id": "synthetic"}}}
        return [event async for event in approved_run(app, payload, "design-evidence-" + "s" * 40)]

    events = asyncio.run(exercise())
    measurements = [event for event in events if event["type"] == "boundary"]
    assert len(measurements) == (1 if blocked else 2) and measurements[0]["chars"] > 0
    assert measurements[0]["piiDetectors"] == (["rules"] if blocked is True else ["rules", "guardrail"])
    if blocked:
        assert not calls
        assert measurements[0]["source"] == ("design-preflight" if blocked is True else "design-model")
        assert measurements[0]["piiRules"] == (1 if blocked is True else 0)
        assert measurements[0]["blocked"] is True
        assert measurements[0]["policyDenied"] is (blocked == "policy")
        assert events[-1]["stopReason"] == "gate_refused" and events[-1]["usage"]["inputTokens"] == 0
        assert any(event.get("code") == 422 for event in events)
        assert "CUST-0042" not in json.dumps(events)
    else:
        assert calls == ["model", "model"]
        assert len(verified) == 2 and "Synthetic complete flow" in verified[1] and "step-one" in verified[1]
        assert measurements[0]["source"] == "design-model" and measurements[0]["piiRules"] == 0
        assert events[-1]["usage"] == {"inputTokens": 4, "outputTokens": 2}
        first_text = next(i for i, event in enumerate(events) if event["type"] == "text")
        first_boundary = next(i for i, event in enumerate(events) if event["type"] == "boundary")
        done_index = next(i for i, event in enumerate(events) if event["type"] == "design_done")
        assert first_boundary < first_text < done_index
        assert events[first_text]["modelCallSeq"] == measurements[0]["seq"] == 1
        assert max(i for i, event in enumerate(events) if event["type"] == "boundary") < done_index
    from agentcore import runtime
    from handlers import design
    from registry import api
    approved = {"name": "design_flow_agent", "recordVersion": "v1",
                "payload": {"runtimeArn": "synthetic", "runtimeSourceHash": "a" * 64}}
    monkeypatch.setattr(api, "list_approved", lambda *args: [approved])
    monkeypatch.setattr(design, "RUNTIME_ARN", "synthetic")
    monkeypatch.setattr(runtime, "invoke_stream", lambda *args, **kwargs: runtime.to_tuples(events, "s" * 64))
    output = []
    ctx = SimpleNamespace(user_sub="synthetic-actor", token=lambda *args: output.append(args),
                          stage=lambda *args, **kwargs: None)
    result, meta, errors = design._relay_runtime(ctx, {}, None)
    if blocked:
        assert result is None and meta["blocked"] and meta["code"] == 422 and meta["stopReason"] == "gate_refused"
        assert not output
    else:
        assert result == {"ok": True} and output and not errors
