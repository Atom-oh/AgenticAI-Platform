"""design 핸들러 스모크 — 카탈로그(시드 폴백)·PRD 미리보기·design_flow 이벤트 3종 계약·런타임 이벤트 통과·Registry 시드 레코드 유효성."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")
os.environ.setdefault("CONN_TABLE", "test-conn")
os.environ.pop("AGENTS_RUNTIME_ARN", None)
os.environ.pop("WEB_BUCKET", None)

from common.ctx import Ctx  # noqa: E402
from handlers import design  # noqa: E402
import handlers  # noqa: E402
from agentcore import runtime  # noqa: E402
from design_loop import derive_prd  # noqa: E402


class _Apigw:
    def __init__(self):
        self.sent = []

    def post_to_connection(self, ConnectionId, Data):
        self.sent.append(json.loads(Data))


def _ctx():
    a = _Apigw()
    c = Ctx(apigw=a, conn_id="c", email="u@x", rid="r1")
    c.token_batch_chars = 0
    return a, c


def test_routes_registered():
    assert {"design_catalog", "design_preview", "design_flow", "design_runs", "design_run", "design_review"} <= set(handlers.ROUTES)


def test_catalog_falls_back_to_seed(monkeypatch):
    monkeypatch.setattr(design, "_registry_assets", lambda: {"productSpecs": [], "smModels": [], "checklists": []})
    a, c = _ctx()
    design.catalog(c, {})
    ev = a.sent[0]
    assert ev["type"] == "design_catalog" and ev["source"] == "seed-fallback"
    ids = {s["id"] for s in ev["productSpecs"]}
    assert "ps-soccer-club-savings" in ids and len(ev["checklists"]) == 2
    soccer = next(s for s in ev["productSpecs"] if s["id"] == "ps-soccer-club-savings")
    assert soccer["inputConditions"] == 1 and soccer["partners"] == ["아톰 FC"]


def test_preview_derives_prd_and_checklist(monkeypatch):
    monkeypatch.setattr(design, "_registry_assets", lambda: {"productSpecs": [], "smModels": [], "checklists": []})
    a, c = _ctx()
    design.preview(c, {"productSpecId": "ps-soccer-club-savings"})
    ev = a.sent[0]
    assert ev["type"] == "design_preview" and "evidence-soccer-club" in [s["id"] for s in ev["prd"]["steps"]]
    assert ev["counts"]["derived"] > 0 and ev["counts"]["branchSteps"] == 1


def test_flow_local_emits_stage_token_done(monkeypatch):
    """런타임 ARN 없음 → Lambda 내 실행 경로. Bedrock 대신 가짜 deps. 이벤트는 design.stage/token/done 만."""
    monkeypatch.setattr(design, "_registry_assets", lambda: {"productSpecs": [], "smModels": [], "checklists": []})
    monkeypatch.setattr(design, "RUNTIME_ARN", "")
    monkeypatch.setattr(design, "WEB_BUCKET", "")
    seeds = design._seed_assets()
    spec = next(s for s in seeds["productSpecs"] if s["id"] == "ps-soccer-club-savings")
    prd = derive_prd(spec, seeds["smModels"][0])

    def fake_generate(system, user, on_token):
        parts = []
        for s in prd["steps"]:
            req = "".join(f"<p>{r}</p>" for r in s["required"])
            btns = "".join(f"<button>{t['trigger']}</button>" for t in prd["transitions"] if t["from"] == s["id"])
            partner = "<p>아톰 FC 제휴 · 이벤트 기간 2026-09-01~12-31 · 심의필</p>" if s["id"] == "intro" else ""
            parts.append(f"<<<STEP id=\"{s['id']}\" title=\"{s['title']}\">>>\n<!doctype html><html lang=\"ko\"><body><h1>{s['title']}</h1>"
                         f"{req}{partner}<p>제휴 종료 안내</p>{btns}</body></html>\n<<<END>>>")
        parts.append("<<<FLOW>>>" + json.dumps({"transitions": prd["transitions"]}, ensure_ascii=False) + "<<<END>>>")
        text = "\n".join(parts)
        on_token(text[:10])
        return text

    from engine import gate as _gate
    monkeypatch.setattr(_gate, "design_deps", lambda *a, **k: {"generate": fake_generate, "llm_judge": None,
                                                               "usage": lambda: {"inputTokens": 1, "outputTokens": 2, "calls": 1}})
    recorded = []
    monkeypatch.setattr(design.tracing, "record_trace", lambda rec: recorded.append(rec))
    a, c = _ctx()
    design.flow(c, {"productSpecId": "ps-soccer-club-savings"})
    types = {e["type"] for e in a.sent}
    assert types <= {"design.stage", "design.token", "design.done"}, types
    done = a.sent[-1]
    assert done["type"] == "design.done" and done["ok"] is True and done["attempts"] == 1
    assert done["runtime"] == "lambda-local" and "데모 대체" in done["runtimeBadge"]
    assert [s["id"] for s in done["steps"]] == [s["id"] for s in prd["steps"]]
    assert done["report"]["score"]["fail"] == 0
    steps = [e["step"] for e in a.sent if e["type"] == "design.stage"]
    assert steps[0] == "gate" and "prd" in steps and "review" in steps and steps[-1] == "report"
    assert recorded and recorded[0]["scenario"] == "STUDIO" and "query" not in recorded[0]


def test_runtime_tuples_pass_stage_and_design_done():
    evs = [{"type": "stage", "step": "prd", "status": "done"}, {"type": "text", "t": "x"},
           {"type": "design_done", "result": {"ok": True}}, {"type": "meta", "usage": {"inputTokens": 1, "outputTokens": 1}}]
    out = list(runtime.to_tuples(evs, "sid"))
    kinds = [k for k, _ in out]
    assert kinds == ["stage", "text", "design_done", "meta"]
    assert out[0][1] == {"step": "prd", "status": "done"} and out[2][1]["result"]["ok"] is True


def test_design_seed_records_are_valid_registry_records():
    from registry import api as reg, seed as rseed
    reg.reset_for_tests()
    recs = rseed.design_records()
    assert len(recs) == 6
    for r in recs:
        saved = reg.create_record(r, "test", status="APPROVED", reason="seed", embed=False)
        assert saved["status"] == "APPROVED"
    assert len(reg.list_approved("CUSTOM", "PRODUCT_SPEC")) == 3
    assert len(reg.list_approved("SKILL", "CHECKLIST")) == 2
    assert reg.list_approved("CUSTOM", "SM_MODEL")[0]["payload"]["kind"] == "sm-model"
