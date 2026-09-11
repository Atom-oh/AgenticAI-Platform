"""사용자 모델 선택이 실제 Bedrock 호출까지 전달되고 경계 검사를 유지하는지 검증."""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from engine import gate, llm
from handlers import agents, studio
from studio import loop

ASTRA = "global.openai.gpt-6-astra"
FABLE = "global.anthropic.claude-fable-5-1"


class Runtime:
    def __init__(self):
        self.calls = []

    def converse_stream(self, **kw):
        self.calls.append(kw)
        return {"stream": [
            {"contentBlockDelta": {"delta": {"text": kw["modelId"]}}},
            {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2}}},
        ]}

    def converse(self, **kw):
        self.calls.append(kw)
        return {"output": {"message": {"content": [{"text": kw["modelId"]}]}},
                "usage": {"inputTokens": 5, "outputTokens": 1}}


@pytest.fixture
def runtime(monkeypatch):
    gate.reset_adapters()
    client = Runtime()
    monkeypatch.setattr(llm.ClaudeAdapter, "_client", lambda self: client)
    yield client
    gate.reset_adapters()


def test_catalog_exposes_verified_astra_and_fable_profiles():
    from engine import model_catalog
    rows = model_catalog.options()
    by_id = {r["id"]: r for r in rows}
    assert by_id[ASTRA]["label"] == "GPT-6 Astra"
    assert by_id[FABLE]["label"] == "Claude Fable 5.1"
    assert "global.anthropic.claude-fable-5" in by_id
    assert model_catalog.resolve("") == "global.anthropic.claude-sonnet-5"


def test_concurrent_model_choices_do_not_mutate_default_or_other_requests(runtime):
    default = gate.adapter("claude")

    def invoke(model):
        stream = gate.Stream("디자인 도우미", "화면 제목을 만드세요", model_id=model, route="bedrock", max_tokens=40)
        text = "".join(stream)
        return text, stream.model_id, stream.usage

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(invoke, [ASTRA, FABLE, ASTRA]))
    assert [(text, model) for text, model, _ in results] == [(ASTRA, ASTRA), (FABLE, FABLE), (ASTRA, ASTRA)]
    assert all(usage["outputTokens"] == 2 for _, _, usage in results)
    assert gate.adapter("claude") is default
    assert default.model_id == "global.anthropic.claude-sonnet-5"
    assert all(call["inferenceConfig"]["maxTokens"] == 40 for call in runtime.calls)


def test_selected_model_is_used_for_nonstream_review(runtime):
    text, usage, info = gate.generate("검수자", "문구를 검수하세요", route="bedrock", model_id=FABLE, max_tokens=60)
    assert text == FABLE and info["modelId"] == FABLE and info["route"] == "bedrock"
    assert runtime.calls[-1]["modelId"] == FABLE and usage["outputTokens"] == 1


def test_bedrock_route_metadata_reports_global_tier_zero_one(runtime):
    info = gate.route_info("bedrock")
    assert info["tier"] == "0/1"
    assert info["inferenceRouting"] == "global"
    assert "Tier 0/1" in info["badge"]["title"]
    assert "IDC" not in info["badge"]["region"]


def test_selected_model_cannot_bypass_pii_gate(runtime):
    with pytest.raises(gate.GateRefused):
        gate.Stream("검수자", "고객 CUST-0001의 전화번호 010-1234-5678", route="bedrock", model_id=ASTRA)
    assert runtime.calls == []


def test_unknown_model_does_not_silently_fall_back(runtime):
    with pytest.raises(ValueError, match="모델"):
        loop.clamp_job({"model": "https://outside.example/model"})
    with pytest.raises(ValueError, match="모델"):
        gate.generate("s", "u", route="bedrock", model_id="unapproved-model")
    assert runtime.calls == []


def test_studio_catalog_and_agents_share_allowed_models(monkeypatch):
    events = []
    ctx = type("Ctx", (), {"post": lambda self, event: events.append(event)})()
    assert "studio_models" in studio.ROUTES
    studio.ROUTES["studio_models"](ctx, {})
    assert ASTRA in {m["id"] for m in events[-1]["models"]}
    monkeypatch.setattr(agents, "_tool_schema", lambda: [])
    monkeypatch.setattr(agents, "_skill_names", lambda: [])
    for model in (ASTRA, FABLE):
        spec, error = agents._validate_create({"name": "design_helper", "systemPrompt": "디자인 검수 도우미", "model": model})
        assert error is None and spec["model"] == model
