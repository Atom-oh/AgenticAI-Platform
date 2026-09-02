"""공통 모듈 테스트 — 독립 PII 스캔 규칙, 비용가드/캐시 로직, 트레이스 원문 비저장, 로그 마스킹."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

from common import log as clog  # noqa: E402
from common import pii  # noqa: E402


def test_pii_rules_catch_korean_identifiers():
    text = "고객 CUST-0042 / 계좌 ACCT-0007 / 주민번호 900101-1234567 / 010-1234-5678 / a@b.co"
    hits = pii.scan_rules(text)
    kinds = {h["type"] for h in hits}
    assert {"CUSTOMER_TOKEN", "ACCOUNT_TOKEN", "KR_RRN", "PHONE", "EMAIL"} <= kinds


def test_pii_rules_ignore_masked_tokens_and_amounts():
    # 마스킹 게이트가 내보내는 형태 — 규칙 탐지기는 0건이어야 한다
    text = "고객 ⟨CUSTOMER_ID:1a2b3c4d⟩ 계좌 ⟨ACCOUNT_ID:9f8e7d6c⟩ 적용금리 3.90% 한도 200,000,000원 보증금 300,000,000원"
    assert pii.scan_rules(text) == []


def test_pii_card_requires_luhn():
    assert pii.scan_rules("카드 4111 1111 1111 1111") and not pii.scan_rules("번호 1234 5678 9012 3456")


def test_scan_outbound_without_guardrail_is_rules_only():
    r = pii.scan_outbound("CUST-0001", use_guardrail=False)
    assert r["count"] == 1 and r["detectors"] == ["rules"]


def test_log_redacts_forbidden_keys(capsys):
    clog.log_event("x", "t1", prompt="비밀 프롬프트 원문", count=3)
    out = capsys.readouterr().out
    assert "비밀 프롬프트 원문" not in out and "promptHash" in out and '"count": 3' in out


class _FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        it = self.items.get(Key["pk"])
        return {"Item": it} if it else {}

    def put_item(self, Item):
        self.items[Item["pk"]] = Item

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues, ReturnValues):
        it = self.items.setdefault(Key["pk"], {"pk": Key["pk"], "tokens": 0})
        it["tokens"] += ExpressionAttributeValues[":t"]
        return {"Attributes": {"tokens": it["tokens"]}}


class _Ctx:
    def __init__(self):
        self.rid = "r1"
        self.trace_id = "t"
        self.recording = None
        self.sent = []

    def post(self, payload):
        if self.recording is not None and payload.get("type") != "error":
            self.recording.append(payload)
        self.sent.append(payload)


def test_costguard_caches_and_replays_on_failure(monkeypatch):
    from common import costguard
    monkeypatch.setattr(costguard, "_tbl", _FakeTable())
    monkeypatch.setattr(costguard, "DAILY_TOKEN_CAP", 1000)
    ctx = _Ctx()

    def ok(c):
        c.post({"type": "s1.token", "t": "안녕"})
        c.post({"type": "s1.done"})
    r = costguard.guarded(ctx, "s1", "질문", ok)
    assert r["cached"] is False and costguard.get_cached("s1", "질문")

    def boom(c):
        c.post({"type": "s1.token", "t": "부분"})
        raise RuntimeError("bedrock down")
    ctx2 = _Ctx()
    r2 = costguard.guarded(ctx2, "s1", "질문", boom)
    assert r2["cached"] is True
    types = [e["type"] for e in ctx2.sent]
    assert "cache.replay" in types and all(e.get("cached") for e in ctx2.sent if e["type"] == "s1.done")


def test_costguard_budget_exceeded_uses_cache_or_raises(monkeypatch):
    from common import costguard
    monkeypatch.setattr(costguard, "_tbl", _FakeTable())
    monkeypatch.setattr(costguard, "DAILY_TOKEN_CAP", 10)
    costguard.add_usage(50)
    ctx = _Ctx()
    try:
        costguard.guarded(ctx, "s2", "없는질문", lambda c: None)
        assert False, "should raise"
    except costguard.BudgetExceeded:
        pass


def test_trace_record_strips_query_and_email(monkeypatch, capsys):
    from common import tracing
    saved = {}
    class T:
        def put_item(self, Item):
            saved.update(Item)
    monkeypatch.setattr(tracing, "_tbl", T())
    tracing.record_trace({"traceId": "abc", "scenario": "S2", "email": "demo@atomai.click",
                          "query": "우대금리 얼마나 받아요?", "piiOutbound": 0})
    assert "query" not in saved and "email" not in saved
    assert saved["queryLen"] == len("우대금리 얼마나 받아요?") and len(saved["userHash"]) == 8
    assert "우대금리" not in capsys.readouterr().out
