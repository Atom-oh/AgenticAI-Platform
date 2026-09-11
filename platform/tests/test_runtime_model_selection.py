"""Container app model validation; no Strands, MCP, AWS, or network required."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASTRA = "global.openai.gpt-6-astra"
ENV_MODEL = "unapproved-env-model"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _no_live_dependency(*args, **kwargs):
    raise AssertionError("Live runtime dependencies must not be used")


@pytest.fixture
def runtime_app(monkeypatch):
    # app.py can add its generated _ctx directory to sys.path.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("GEN_MODEL", ENV_MODEL)
    for name, path in (
        ("agent_specs", ROOT / "agentcore" / "agent_specs.py"),
        ("model_catalog", ROOT / "engine" / "model_catalog.py"),
    ):
        monkeypatch.setitem(sys.modules, name, _load(name, path))

    boundary = ModuleType("boundary_gate")
    boundary.BoundaryGateHook = _no_live_dependency
    boundary.find_gate_refusal = _no_live_dependency
    boundary.scan_rules = _no_live_dependency
    gateway = ModuleType("mcp_gateway")
    gateway.open_tools = _no_live_dependency

    class StubApp:
        def entrypoint(self, function):
            return function

    runtime = ModuleType("bedrock_agentcore.runtime")
    runtime.BedrockAgentCoreApp = StubApp
    agentcore = ModuleType("bedrock_agentcore")
    agentcore.runtime = runtime
    for module in (boundary, gateway, agentcore, runtime):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    app = _load("_container_model_selection_app", ROOT / "agents" / "app.py")
    calls = []

    async def run_design(payload, model_id, meta, started):
        calls.append((payload, model_id))
        yield {"type": "design_done", "result": {"ok": True}}
        yield {**meta, "stopReason": "end_turn"}

    async def to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(app, "asyncio", SimpleNamespace(to_thread=to_thread))
    monkeypatch.setattr(app, "_run_design", run_design)
    monkeypatch.setattr(app, "_Session", _no_live_dependency)
    return app, calls


def _events(app, payload):
    async def collect():
        return [event async for event in app.run(payload)]

    return asyncio.run(collect())


@pytest.mark.parametrize("model", [
    "global.anthropic.claude-sonnet-5",
    "global.anthropic.claude-opus-5",
    ASTRA,
    "global.anthropic.claude-fable-5-1",
    "global.anthropic.claude-fable-5",
    None,
])
def test_design_catalog_models_reach_run_design(runtime_app, model):
    app, calls = runtime_app
    payload = {"agent": "design_flow_agent", "design": {"productSpec": {}, "smModel": {}}}
    if model is not None:
        payload["model"] = model
    events = _events(app, payload)
    expected = model or "global.anthropic.claude-sonnet-5"
    assert not [event for event in events if event["type"] == "error"], events
    assert calls == [(payload, expected)]
    assert events[-1]["modelId"] == expected


@pytest.mark.parametrize("agent", ["design_flow_agent", "regulation_impact_agent"])
@pytest.mark.parametrize("model", ["unapproved-request-model", ENV_MODEL])
def test_uncataloged_request_and_env_models_are_rejected(runtime_app, agent, model):
    app, calls = runtime_app
    events = _events(app, {"agent": agent, "prompt": "Hello", "model": model})
    assert events[0]["type"] == "error" and events[0]["code"] == 400
    assert events[0]["message"] == f"model not allowed: {model}"
    assert set(events[0]["allowed"]) == set(sys.modules["model_catalog"].MODEL_IDS)
    assert events[-1]["stopReason"] == "error"
    assert not calls


@pytest.mark.parametrize("agent", ["design_flow_agent", "regulation_impact_agent"])
@pytest.mark.parametrize("explicit_model", [False, True])
def test_uncataloged_spec_model_is_not_an_allowlist_exception(runtime_app, monkeypatch, agent, explicit_model):
    app, calls = runtime_app
    spec = {**app.agent_specs.spec_by_name(agent), "model": "unapproved-spec-model"}
    monkeypatch.setattr(app.agent_specs, "spec_by_name", lambda name: spec)
    payload = {"agent": agent, "prompt": "Hello"}
    if explicit_model:
        payload["model"] = spec["model"]
    events = _events(app, payload)
    assert events[0]["type"] == "error" and events[0]["code"] == 400
    assert events[0]["message"] == "model not allowed: unapproved-spec-model"
    assert events[-1]["stopReason"] == "error"
    assert not calls
