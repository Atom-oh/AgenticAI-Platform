"""Single Boundary 뷰 집계 (SPEC §8-3) — core.traces / core.hub 오프라인 테스트.

플레인·트레이스 테이블·외부 서피스는 전부 페이크로 주입한다. AWS 호출 없음.
검증 포인트: 실측값은 플레인 응답에서만 오고, 플레인이 없으면 null + 'unavailable' (숫자를 꾸미지 않는다 §12.8).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

from common import plane  # noqa: E402
from handlers import core  # noqa: E402


class _Ctx:
    def __init__(self):
        self.rid, self.trace_id, self.email, self.sent = "r1", "t1", "demo@atomai.click", []

    def post(self, payload):
        self.sent.append(payload)


class _Store:
    def stats(self):
        return {"backend": "local", "nodes": 3812, "edges": 11020, "by_label": {}}


ITEMS = [
    {"traceId": "a", "scenario": "S2", "plane": "bridge", "modelId": "global.anthropic.claude-sonnet-5",
     "tokensIn": 1200, "tokensOut": 300, "blocked": False, "maskedFields": ["customerName", "customerId"]},
    {"traceId": "b", "scenario": "S1", "plane": "cloud", "modelId": "global.anthropic.claude-sonnet-5",
     "tokensIn": 800, "tokensOut": 500, "blocked": False},
    {"traceId": "c", "scenario": "S2", "plane": "bridge", "modelId": "google.gemma-4-31b",
     "tokensIn": 10, "tokensOut": 0, "blocked": True, "topics": ["investment-advice"]},
    {"traceId": "d", "scenario": "S2", "plane": "bridge", "tokensIn": 0, "tokensOut": 0,
     "blocked": True, "blockedBy": "gate"},
]


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(core.tracing, "list_traces", lambda limit=60: list(ITEMS)[:limit])
    monkeypatch.setattr(core, "lazy_store", lambda: _Store())
    monkeypatch.setattr(core, "_control_room", lambda *a, **k: {"agents": [{"status": "APPROVED"}]})
    monkeypatch.setattr(core, "_studio", lambda *a, **k: {"assets": []})
    monkeypatch.setattr(core, "_agentcore_records", lambda: [])
    monkeypatch.setattr(core, "_registry_counts", lambda: {"total": 5, "approved": 3, "byType": {}})
    monkeypatch.delenv("GEN_MODEL", raising=False)
    yield


def _plane_ok(path, body, timeout=20):
    if path == "/health":
        return {"ok": True, "store": "rds", "vectorChunks": 177, "vectorReady": True}
    if path == "/audit/recent":
        assert body == {"n": 1}
        return {"count": 1, "total": 42, "store": "rds", "items": [{"promptLen": 300}]}
    raise AssertionError(path)


def test_traces_retained_is_measured_from_plane(offline, monkeypatch):
    monkeypatch.setattr(plane, "mode", lambda: "bridge")
    monkeypatch.setattr(plane, "call", _plane_ok)
    ctx = _Ctx()
    core.traces(ctx, {"limit": 60})
    e = ctx.sent[0]
    assert e["type"] == "traces"
    # 기존 키 유지
    assert {"piiOutboundTotal", "requests", "blocked", "cached", "tokensOutTotal", "items", "plane",
            "planeLabel", "backend"} <= set(e)
    assert e["requests"] == 4 and e["blocked"] == 2 and e["tokensOutTotal"] == 800
    r = e["retained"]
    assert r["source"] == "plane-health" and r["store"] == "rds"
    assert r["vectorChunks"] == 177 and r["auditRecords"] == 42 and r["ontologyNodes"] == 3812
    assert r["ledgerRows"] is None  # /health 가 노출하지 않으면 꾸미지 않는다
    assert e["models"] == [{"modelId": "global.anthropic.claude-sonnet-5", "count": 2},
                           {"modelId": "google.gemma-4-31b", "count": 1}]
    assert e["gateRejected"] == 1 and e["guardrailBlocked"] == 1 and e["tokensInTotal"] == 2010
    assert e["llmRoute"] == "claude"


def test_traces_unavailable_when_plane_unreachable(offline, monkeypatch):
    monkeypatch.setattr(plane, "mode", lambda: "bridge")

    def boom(path, body, timeout=20):
        raise plane.PlaneUnavailable("bridge down")
    monkeypatch.setattr(plane, "call", boom)
    ctx = _Ctx()
    core.traces(ctx, {})
    r = ctx.sent[0]["retained"]
    assert r["source"] == "unavailable"
    assert r["vectorChunks"] is None and r["auditRecords"] is None and r["store"] is None
    assert r["ontologyNodes"] == 3812  # 그래프 통계는 플레인과 무관하게 실측
    assert "PlaneUnavailable" in r["reason"]


def test_traces_unavailable_without_plane(offline, monkeypatch):
    monkeypatch.setattr(plane, "mode", lambda: "none")
    called = []
    monkeypatch.setattr(plane, "call", lambda *a, **k: called.append(a))
    ctx = _Ctx()
    core.traces(ctx, {})
    r = ctx.sent[0]["retained"]
    assert r["source"] == "unavailable" and r["vectorChunks"] is None and not called


def test_traces_skips_plane_when_with_retained_false(offline, monkeypatch):
    monkeypatch.setattr(plane, "mode", lambda: "bridge")
    monkeypatch.setattr(plane, "call", lambda *a, **k: pytest.fail("플레인을 호출하면 안 된다"))
    ctx = _Ctx()
    core.traces(ctx, {"limit": 1, "withRetained": False})
    e = ctx.sent[0]
    assert e["retained"] is None and e["requests"] == 1 and "llmRoute" in e and "genModel" in e


def test_hub_exposes_llm_route_and_model(offline, monkeypatch):
    monkeypatch.setattr(plane, "mode", lambda: "none")
    monkeypatch.setenv("GEN_MODEL", "global.anthropic.claude-sonnet-5")
    ctx = _Ctx()
    core.hub(ctx, {"idToken": ""})
    e = ctx.sent[0]
    assert e["type"] == "hub" and e["llmRoute"] == "claude"
    assert e["genModel"] == "global.anthropic.claude-sonnet-5"
    assert e["graphNodes"] == 3812 and e["registry"] == 5 and e["registryApproved"] == 3

