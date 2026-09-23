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


def test_process_step_previews_have_offline_policy(monkeypatch):
    saved = {}
    monkeypatch.setattr(design, "WEB_BUCKET", "web")
    monkeypatch.setattr(design, "_put", lambda key, data, content_type: saved.update({key: data}))
    monkeypatch.setattr(design, "_get_json", lambda key, default: default)
    design.store_run("offline", {"flow": {"steps": [{"id": "intro", "html": "<html><body>시안</body></html>"}]}}, {})
    html = next(data.decode() for key, data in saved.items() if key.endswith(".html"))
    assert 'http-equiv="Content-Security-Policy"' in html and "connect-src 'none'" in html


def test_process_approval_rejects_undetermined_items_without_writing(monkeypatch):
    monkeypatch.setattr(design, "WEB_BUCKET", "web")
    writes = []
    monkeypatch.setattr(design, "_put", lambda *args: writes.append(args))
    full = {"runId": "pending", "ok": True, "validationScope": "static-design", "report": {"items": [{"id": "a", "verdict": "incomplete"}],
                                                     "score": {"incomplete": 1, "fail": 0}}}
    monkeypatch.setattr(design, "_get_json", lambda key, default: {"runs": [{"runId": "pending"}]} if key == design.INDEX_KEY else full)
    a, ctx = _ctx()
    design.review_decision(ctx, {"runId": "pending", "decision": "approve"})
    assert a.sent[-1]["ok"] is False and a.sent[-1]["error"]
    assert writes == []


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


def test_flow_preserves_stage_token_done_contract(monkeypatch):
    """The handler projects a bounded Runtime result into its existing UI contract."""
    monkeypatch.setattr(design, "_registry_assets", lambda: {"productSpecs": [], "smModels": [], "checklists": []})
    monkeypatch.setattr(design, "RUNTIME_ARN", "synthetic-runtime")
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
    def relay(ctx, value, model):
        from design_loop import run
        def emit(event):
            if event.get("type") == "token":
                ctx.token("design", event.get("text", ""))
            elif event.get("type") == "stage":
                fields = {key: item for key, item in event.items() if key not in {"type", "step"}}
                ctx.stage("design", event.get("step", "stage"), **fields)
        result = run(value["productSpec"], value["smModel"], value["checklists"],
                     _gate.design_deps(), emit=emit, output_type=value.get("outputType") or "design")
        return result, {"runtime": "agentcore-runtime/strands", "usage": {}}, []
    monkeypatch.setattr(design, "_relay_runtime", relay)
    recorded = []
    monkeypatch.setattr(design.tracing, "record_trace", lambda rec: recorded.append(rec))
    a, c = _ctx()
    design.flow(c, {"productSpecId": "ps-soccer-club-savings"})
    types = {e["type"] for e in a.sent}
    assert types <= {"design.stage", "design.token", "design.done"}, types
    done = a.sent[-1]
    assert done["type"] == "design.done" and done["ok"] is False and done["attempts"] == 2
    assert done["report"]["score"]["incomplete"] > 0
    assert done["runtime"] == "agentcore-runtime/strands" and "AgentCore Runtime" in done["runtimeBadge"]
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
    assert kinds == ["stage", "text_boundary", "text", "design_done", "meta"]
    assert out[0][1] == {"step": "prd", "status": "done"} and out[3][1]["result"]["ok"] is True


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


def test_configured_runtime_refusal_reaches_terminal_design_response(monkeypatch):
    recorded = []
    monkeypatch.setattr(design.tracing, 'record_trace', recorded.append)
    monkeypatch.setattr(design, 'RUNTIME_ARN', 'synthetic-runtime')
    monkeypatch.setenv('DESIGN_USE_RUNTIME', '0')
    monkeypatch.setattr(design, '_relay_runtime', lambda *args: (
        None, {'blocked': True, 'stopReason': 'gate_refused', 'code': 422}, ['Synthetic policy refusal']))
    a, ctx = _ctx()
    design.flow(ctx, {'productSpecId': 'ps-soccer-club-savings'})
    done = a.sent[-1]
    assert done['type'] == 'design.done' and done['blocked'] and done['code'] == 422
    assert done['stopReason'] == 'gate_refused' and done['runtime'] == 'agentcore-runtime/strands'
    assert recorded[-1]['blocked'] and recorded[-1]['stopReason'] == 'gate_refused'
    assert 'query' not in recorded[-1] and 'payload' not in recorded[-1]



def test_design_generation_without_runtime_is_blocked(monkeypatch):
    monkeypatch.setattr(design, "RUNTIME_ARN", "")
    monkeypatch.setattr(design, "_relay_runtime", lambda *args: (_ for _ in ()).throw(AssertionError("unconfigured call")))
    a, ctx = _ctx()
    design.flow(ctx, {"productSpecId": "ps-soccer-club-savings"})
    assert a.sent[-1]["code"] == 503 and a.sent[-1]["runtime"] == "unconfigured"
    assert all(event["type"] != "design.token" for event in a.sent)
