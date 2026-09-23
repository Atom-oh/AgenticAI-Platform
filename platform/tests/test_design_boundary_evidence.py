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


@pytest.mark.parametrize("blocked", [False, True])
def test_design_reports_boundary_events_and_refusal_without_originals(runtime_app, monkeypatch, blocked):
    boundary = load("design_boundary", ROOT / "agents/boundary_gate.py")
    monkeypatch.setitem(sys.modules, "boundary_gate", boundary)
    verified = []
    def verify(text):
        verified.append(text)
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
        deps["generate"]("Synthetic system", product["text"], None)
        deps["llm_judge"]({"id": "rule", "text": "Synthetic requirement", "target": "screen"},
                          {"prd": {"steps": [{"id": "step-one"}]}, "flowText": "Synthetic complete flow"})
        return {"ok": True}

    monkeypatch.setattr(design_loop, "run", run)
    app = load("design_app_evidence", ROOT / "agents/app.py")
    text = "CUST-0042" if blocked else "Synthetic public content"

    async def exercise():
        payload = {"agent": "design_flow_agent",
                   "design": {"productSpec": {"id": "synthetic", "text": text}, "smModel": {"id": "synthetic"}}}
        return [event async for event in approved_run(app, payload, "design-evidence-" + "s" * 40)]

    events = asyncio.run(exercise())
    measurements = [event for event in events if event["type"] == "boundary"]
    assert len(measurements) == (1 if blocked else 2) and measurements[0]["chars"] > 0
    assert measurements[0]["piiDetectors"] == (["rules"] if blocked else ["rules", "guardrail"])
    if blocked:
        assert not calls
        assert measurements[0]["source"] == "design-preflight" and measurements[0]["piiRules"] == 1
        assert events[-1]["stopReason"] == "gate_refused" and events[-1]["usage"]["inputTokens"] == 0
        assert any(event.get("code") == 422 for event in events)
        assert "CUST-0042" not in json.dumps(events)
    else:
        assert calls == ["model", "model"]
        assert len(verified) == 2 and "Synthetic complete flow" in verified[1] and "step-one" in verified[1]
        assert measurements[0]["source"] == "design-model" and measurements[0]["piiRules"] == 0
        assert events[-1]["usage"] == {"inputTokens": 4, "outputTokens": 2}
