# platform/tests/test_studio_handler.py
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

import ws_handler  # noqa: E402
from common.ctx import Ctx  # noqa: E402
from graph.store import LocalGraphStore  # noqa: E402
from handlers import studio as h  # noqa: E402
from studio.store import StudioStore  # noqa: E402


class _Apigw:
    def __init__(self):
        self.sent = []
        self.meta = type("M", (), {"endpoint_url": "https://ws.example/prod"})()

    def post_to_connection(self, ConnectionId, Data):
        self.sent.append(json.loads(Data))


def _ctx():
    a = _Apigw()
    return Ctx(apigw=a, conn_id="c1", email="u@x", rid="r1"), a


def setup_module(module):
    h._store = StudioStore()
    h._graph = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


def test_routes_registered():
    for a in ("studio_run", "studio_jobs", "studio_drafts", "studio_feedback", "studio_products", "studio_spec", "studio_asset", "studio_register"):
        assert a in ws_handler.ROUTES, a
    assert "studio_generate" not in ws_handler.ROUTES, "옛 프록시 생성 라우트는 제거"


def test_products_and_spec():
    ctx, a = _ctx()
    h.studio_products(ctx, {})
    e = a.sent[-1]
    assert e["type"] == "studio_products" and e["products"][0]["code"] == "PRD-DEP-001" and e["graphBackend"] == "local"
    h.studio_spec(ctx, {"productCode": "PRD-DEP-001", "outputType": "ux-flow"})
    s = a.sent[-1]
    assert s["type"] == "studio_spec" and s["spec"]["hasPreferential"] and any(i["id"] == "FLOW-COND" for i in s["spec"]["items"])
    h.studio_spec(ctx, {"productCode": "PRD-NOPE"})
    assert a.sent[-1]["type"] == "studio_spec" and "찾을 수 없" in a.sent[-1]["error"]


def test_run_without_worker_is_honest(monkeypatch):
    monkeypatch.setenv("STUDIO_LOOP_FN", "")
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "b", "productCode": "PRD-DEP-001"})
    assert a.sent[-1]["type"] == "studio.done" and "미배포" in a.sent[-1]["error"]


def test_run_invokes_worker_with_clamped_job(monkeypatch):
    monkeypatch.setenv("STUDIO_LOOP_FN", "studio-fn")
    calls = []
    monkeypatch.setattr(h, "_invoke", lambda fn, payload: calls.append((fn, payload)))
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "축구 적금", "productCode": "PRD-DEP-001", "maxRounds": 50, "passScore": 20, "outputType": "ux-flow", "axis": "흐름", "assetIds": ["palette:hana"]})
    fn, p = calls[0]
    assert fn == "studio-fn" and p["connId"] == "c1" and p["endpoint"] == "https://ws.example/prod" and p["reqId"] == "r1" and p["email"] == "u@x"
    assert p["job"]["maxRounds"] == 20 and p["job"]["passScore"] == 50 and p["job"]["assetIds"] == ["palette:hana"] and len(p["job"]["jobId"]) == 12
    ack = a.sent[-1]
    assert ack["type"] == "studio_run" and ack["jobId"] == p["job"]["jobId"] and ack["maxRounds"] == 20
    assert h._store.get_job(p["job"]["jobId"])["status"] == "running"


def test_run_invoke_failure_marks_job_failed(monkeypatch):
    monkeypatch.setenv("STUDIO_LOOP_FN", "studio-fn")

    def _boom(fn, payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(h, "_invoke", _boom)
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "b", "productCode": "PRD-DEP-001"})
    last = a.sent[-1]
    job_id = last["jobId"]  # list_jobs()[0] 은 같은 ms 에 만들어진 다른 테스트의 잡을 집을 수 있다
    assert last["type"] == "studio.done" and "boom" in last["error"]
    assert h._store.get_job(job_id)["status"] == "failed"


def test_run_rejects_missing_product():
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "b"})
    assert a.sent[-1]["type"] == "studio.done" and "productCode" in a.sent[-1]["error"]


def test_jobs_drafts_feedback():
    h._store.put_draft({"draftId": "dA", "jobId": "jA", "title": "t", "axis": "흐름", "outputType": "design", "productCode": "PRD-DEP-001",
                        "productName": "아톰 축구사랑 적금", "score": 91, "passed": True, "rounds": 2, "bestRound": 2, "url": "https://x/d.html",
                        "key": "studio/drafts/dA.html", "createdAt": 5, "createdBy": "u@x", "model": "m"})
    ctx, a = _ctx()
    h.studio_drafts(ctx, {})
    assert a.sent[-1]["type"] == "studio_drafts" and a.sent[-1]["drafts"][0]["draftId"] == "dA" and a.sent[-1]["backend"] == "memory"
    h.studio_feedback(ctx, {"draftId": "dA", "decision": "reject", "comment": "CTA 두 개"})
    assert a.sent[-1]["type"] == "studio_feedback" and a.sent[-1]["draft"]["status"] == "반려" and a.sent[-1]["draft"]["comment"] == "CTA 두 개"
    h.studio_feedback(ctx, {"draftId": "dA", "decision": "bogus"})
    assert a.sent[-1]["error"]
    h.studio_jobs(ctx, {})
    assert a.sent[-1]["type"] == "studio_jobs" and isinstance(a.sent[-1]["jobs"], list)
    h.studio_jobs(ctx, {"jobId": "nope"})
    assert a.sent[-1]["job"] is None


def test_approval_rejects_failed_or_undetermined_review(monkeypatch):
    store = StudioStore()
    monkeypatch.setattr(h, "_store", store)
    store.put_job({"jobId": "unverified"}, actor="u@x")
    store.put_round("unverified", {"round": 1, "passed": True, "undetermined": ["OPTIONAL"], "url": "https://x/u.html"})
    store.put_draft({"draftId": "unverified", "jobId": "unverified", "passed": True, "bestRound": 1, "url": "https://x/u.html", "validationScope": "static-design"})
    ctx, a = _ctx()
    h.studio_feedback(ctx, {"draftId": "unverified", "decision": "approve"})
    assert a.sent[-1].get("error")
    assert store.get_draft("unverified")["status"] == "검토중"


def test_approval_requires_report_for_same_artifact_and_records_static_scope(monkeypatch):
    store = StudioStore()
    monkeypatch.setattr(h, "_store", store)
    store.put_job({"jobId": "verified"}, actor="u@x")
    store.put_round("verified", {"round": 1, "passed": True, "undetermined": [], "url": "https://x/old.html", "failures": []})
    store.put_draft({"draftId": "verified", "jobId": "verified", "passed": True, "bestRound": 1, "url": "https://x/current.html", "validationScope": "static-design"})
    ctx, a = _ctx()
    h.studio_feedback(ctx, {"draftId": "verified", "decision": "approve"})
    assert a.sent[-1].get("error")
    store.put_round("verified", {"round": 1, "passed": True, "undetermined": [], "url": "https://x/current.html", "failures": []})
    h.studio_feedback(ctx, {"draftId": "verified", "decision": "approve"})
    assert a.sent[-1]["draft"]["status"] == "승인됨"
    assert a.sent[-1]["approvalScope"] == "static-design"


def test_informational_generation_stability_does_not_disagree_with_approval(monkeypatch):
    store = StudioStore()
    monkeypatch.setattr(h, "_store", store)
    store.put_job({"jobId": "generated"}, actor="u@x")
    store.put_round("generated", {"round": 2, "passed": True, "undetermined": [], "url": "https://x/g.html",
                                  "failures": [{"id": "STABLE", "weight": 0, "required": False, "verdict": "fail"}]})
    store.put_draft({"draftId": "generated", "jobId": "generated", "passed": True, "bestRound": 2, "url": "https://x/g.html", "validationScope": "static-design"})
    ctx, a = _ctx()
    h.studio_feedback(ctx, {"draftId": "generated", "decision": "approve"})
    assert not a.sent[-1].get("error")


def test_round_report_is_bound_to_requested_round(monkeypatch):
    store = StudioStore()
    monkeypatch.setattr(h, "_store", store)
    store.put_job({"jobId": "two_rounds"}, actor="u@x")
    store.put_round("two_rounds", {"round": 1, "items": [{"id": "rule", "verdict": "fail"}], "url": "r1"})
    store.put_round("two_rounds", {"round": 2, "items": [{"id": "rule", "verdict": "pass"}], "url": "r2"})
    assert "studio_round" in h.ROUTES
    ctx, a = _ctx()
    h.ROUTES["studio_round"](ctx, {"jobId": "two_rounds", "round": 1})
    result = a.sent[-1]["round"]
    assert result["round"] == 1 and result["url"] == "r1"
    assert result["items"] == [{"id": "rule", "verdict": "fail"}] and result["itemsComplete"] is True
    assert all("items" not in r for r in store.get_job("two_rounds")["rounds"])


def test_historical_score_is_not_new_validation_evidence(monkeypatch):
    store = StudioStore()
    monkeypatch.setattr(h, "_store", store)
    store.put_job({"jobId": "historical"}, actor="u@x")
    store.put_round("historical", {"round": 1, "passed": True, "undetermined": [], "url": "historical"})
    store.put_draft({"draftId": "historical", "jobId": "historical", "passed": True, "bestRound": 1, "url": "historical"})
    ctx, a = _ctx()
    h.studio_feedback(ctx, {"draftId": "historical", "decision": "approve"})
    assert a.sent[-1].get("error")
    assert store.get_draft("historical")["status"] == "검토중"
