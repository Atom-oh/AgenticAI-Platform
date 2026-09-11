# platform/tests/test_studio_worker.py
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ["WEB_BUCKET"] = "web-bkt"
os.environ["WEB_URL"] = "https://agent.example"

from graph.store import LocalGraphStore  # noqa: E402
from studio import worker_handler as w  # noqa: E402
from studio.store import StudioStore  # noqa: E402

GRAPH = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


class _Apigw:
    def __init__(self, gone=False):
        self.sent, self.gone = [], gone

    def post_to_connection(self, ConnectionId, Data):
        if self.gone:
            raise Exception("GoneException: connection gone")
        self.sent.append(json.loads(Data))


class _S3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeStream(list):
    usage = {"inputTokens": 1, "outputTokens": 1}


GOOD = "<html><head><style>.c{background:#008485}</style></head><body>" + "".join(
    f'<section data-step="{i}"><h2>{n}</h2><p>기본금리 연 3.0% 적금 우대금리 축구클럽 회원 인증 시 우대금리 +1.0%p 자동이체 월 납입금액 가입기간 만기 예상 이자</p><label><input type="checkbox">동의</label><input type="number"><button>다음</button></section>'
    for i, n in enumerate(["상품안내", "기간 선택", "금액 입력", "축구클럽 인증", "납입 방식", "약관 동의", "가입 완료"], start=1)) + "</body></html>"


def _wire(monkeypatch, apigw, s3, store):
    monkeypatch.setattr(w, "_apigw_client", lambda endpoint: apigw)
    monkeypatch.setattr(w, "_s3_client", lambda: s3)
    monkeypatch.setattr(w, "_graph", lambda: GRAPH)
    monkeypatch.setattr(w, "_store", lambda: store)
    monkeypatch.setattr(w, "_generate", lambda system, user, max_tokens, **kw: FakeStream(["```html\n", GOOD, "\n```"]))
    monkeypatch.setattr(w, "_review_generate", lambda system, user, max_tokens, **kw: (json.dumps({"items": []}), {}))
    monkeypatch.setattr(w, "_assets_text", lambda ids: "팔레트" if ids else "")
    monkeypatch.setattr(w, "_agent_preset", lambda aid: "")
    monkeypatch.setattr(w.costguard, "add_usage", lambda tokens: 0)


def _event(**job):
    return {"connId": "c1", "endpoint": "https://ws/prod", "reqId": "r1", "email": "u@x", "traceId": "t1",
            "job": {"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001", "outputType": "ux-flow", "maxRounds": 2, **job}}


def test_worker_streams_publishes_and_records(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    spent = []
    monkeypatch.setattr(w.costguard, "add_usage", lambda tokens: spent.append(tokens))
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    out = w.handler(_event(), None)
    assert out["statusCode"] == 200
    types = [e["type"] for e in apigw.sent]
    assert types[0] == "studio.stage" and types[-1] == "studio.done" and "studio.token" in types
    assert all(e["reqId"] == "r1" and e["traceId"] == "t1" for e in apigw.sent)
    done = apigw.sent[-1]
    # 리뷰어 모의는 항상 미판정을 반환하므로 두 라운드 모두 동일 점수 — loop.run 의 tie-break 는 후행 라운드를 우선한다.
    assert done["draftId"] == "job123456789" and "html" not in done and done["url"] == "https://agent.example/studio/drafts/job123456789-r2.html"
    assert ("web-bkt", "studio/drafts/job123456789-r2.html") in s3.objects
    job = store.get_job("job123456789")
    assert job["status"] == "done" and len(job["rounds"]) == 2 and job["draftId"] == "job123456789"
    d = store.get_draft("job123456789")
    assert d["status"] == "검토중" and d["productName"] == "아톰 축구사랑 적금" and d["bestRound"] == 2 and d["key"].endswith("-r2.html")
    # 일일 토큰 상한(costguard)에 실측 사용량이 등록된다 — 합계는 done 프레임의 usage 와 같다
    assert spent == [done["usage"]["inputTokens"] + done["usage"]["outputTokens"]] and spent[0] > 0


def test_worker_survives_gone_connection(monkeypatch):
    apigw, s3, store = _Apigw(gone=True), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    assert w.handler(_event(), None)["statusCode"] == 200
    assert store.get_job("job123456789")["status"] == "done" and len(s3.objects) == 2


def test_worker_reports_spec_error(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-NOPE"}, actor="u@x")
    w.handler(_event(productCode="PRD-NOPE"), None)
    done = apigw.sent[-1]
    assert done["type"] == "studio.done" and "찾을 수 없" in done["error"] and store.get_job("job123456789")["status"] == "failed"


def test_refine_loads_base_draft_from_s3(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    s3.put_object(Bucket="web-bkt", Key="studio/drafts/base1-r1.html", Body=GOOD.encode(), ContentType="text/html", CacheControl="no-cache")
    store.put_draft({"draftId": "base1", "jobId": "base1", "title": "t", "axis": "흐름", "outputType": "ux-flow", "productCode": "PRD-DEP-001",
                     "productName": "아톰 축구사랑 적금", "score": 90, "passed": True, "rounds": 1, "bestRound": 1, "url": "u", "key": "studio/drafts/base1-r1.html",
                     "createdAt": 1, "createdBy": "u@x", "model": "m"})
    store.put_job({"jobId": "job123456789", "brief": "", "productCode": "PRD-DEP-001"}, actor="u@x")
    seen = {}
    monkeypatch.setattr(w, "_generate", lambda system, user, max_tokens, **kw: seen.setdefault("user", user) and FakeStream(["```html\n", GOOD, "\n```"]))
    w.handler(_event(mode="refine", baseDraftId="base1", selector="h2", instruction="크게", maxRounds=1), None)
    assert "크게" in seen["user"] and GOOD in seen["user"]
    assert store.get_draft("job123456789")["parentId"] == "base1"


def test_ws_transient_error_is_retried(monkeypatch):
    baseline_apigw, s3b, storeb = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, baseline_apigw, s3b, storeb)
    storeb.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    w.handler(_event(), None)
    expected = len(baseline_apigw.sent)

    class _Flaky:
        def __init__(self):
            self.sent, self._seen = [], set()

        def post_to_connection(self, ConnectionId, Data):
            if Data not in self._seen:
                self._seen.add(Data)
                raise Exception("TooManyRequestsException")
            self.sent.append(json.loads(Data))

    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    flaky, s3, store = _Flaky(), _S3(), StudioStore()
    _wire(monkeypatch, flaky, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    w.handler(_event(), None)
    assert len(flaky.sent) == expected


def test_time_cap_derives_from_context():
    class _Ctx:
        def __init__(self, ms):
            self.ms = ms

        def get_remaining_time_in_millis(self):
            return self.ms

    assert w._time_cap(_Ctx(300_000)) == 180
    assert w._time_cap(None) == 780
    assert w._time_cap(_Ctx(900_000)) == 780


def test_worker_uses_selected_model_for_generation_review_and_saved_draft(monkeypatch):
    model = "global.openai.gpt-6-astra"
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    calls = []

    def generate(system, user, max_tokens, *, model_id):
        calls.append(("generate", model_id))
        result = FakeStream(["```html\n", GOOD, "\n```"])
        result.model_id = model_id
        return result

    def review_generate(system, user, max_tokens, *, model_id):
        calls.append(("review", model_id))
        return json.dumps({"items": []}), {}

    monkeypatch.setattr(w, "_generate", generate)
    monkeypatch.setattr(w, "_review_generate", review_generate)
    store.put_job({"jobId": "job123456789", "model": model}, actor="u@x")
    w.handler(_event(model=model, maxRounds=1), None)
    assert calls == [("generate", model), ("review", model)]
    assert apigw.sent[-1]["model"] == model
    assert store.get_draft("job123456789")["model"] == model
    assert store.get_job("job123456789")["model"] == model


def test_refine_loads_the_round_the_designer_is_viewing(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    first = GOOD.replace("<body>", '<body data-version="visible-round">')
    best = GOOD.replace("<body>", '<body data-version="best-round">')
    store.put_job({"jobId": "base1"}, actor="u@x")
    for n, html in [(1, first), (2, best)]:
        s3.objects[("web-bkt", f"studio/drafts/base1-r{n}.html")] = html.encode()
        store.put_round("base1", {"round": n, "url": f"https://agent.example/studio/drafts/base1-r{n}.html"})
    store.put_draft({"draftId": "base1", "jobId": "base1", "productCode": "PRD-DEP-001", "bestRound": 2,
                     "key": "studio/drafts/base1-r2.html"})
    store.put_job({"jobId": "job123456789"}, actor="u@x")
    seen = []

    def generate(system, user, max_tokens, **kw):
        seen.append(user)
        return FakeStream(["```html\n", first, "\n```"])

    monkeypatch.setattr(w, "_generate", generate)
    w.handler(_event(mode="refine", baseDraftId="base1", baseRound=1, maxRounds=1), None)
    assert "visible-round" in seen[0] and "best-round" not in seen[0]
    assert store.get_draft("job123456789")["parentRound"] == 1


def test_worker_publishes_previews_with_offline_policy(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789"}, actor="u@x")
    w.handler(_event(maxRounds=1), None)
    html = s3.objects[("web-bkt", "studio/drafts/job123456789-r1.html")].decode()
    assert 'http-equiv="Content-Security-Policy"' in html
    assert "connect-src 'none'" in html


def test_client_construction_failure_does_not_raise(monkeypatch):
    store = StudioStore()
    monkeypatch.setattr(w, "_store", lambda: store)

    def _boom(endpoint):
        raise RuntimeError("boom")

    monkeypatch.setattr(w, "_apigw_client", _boom)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    out = w.handler(_event(), None)
    assert out["statusCode"] == 200
    assert store.get_job("job123456789")["status"] == "failed"


def test_refine_rejects_other_product_base(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    s3.put_object(Bucket="web-bkt", Key="studio/drafts/base1-r1.html", Body=GOOD.encode(), ContentType="text/html", CacheControl="no-cache")
    store.put_draft({"draftId": "base1", "jobId": "base1", "title": "t", "axis": "흐름", "outputType": "ux-flow", "productCode": "PRD-DEP-002",
                     "productName": "다른상품", "score": 90, "passed": True, "rounds": 1, "bestRound": 1, "url": "u", "key": "studio/drafts/base1-r1.html",
                     "createdAt": 1, "createdBy": "u@x", "model": "m"})
    store.put_job({"jobId": "job123456789", "brief": "", "productCode": "PRD-DEP-001"}, actor="u@x")
    w.handler(_event(mode="refine", baseDraftId="base1", selector="h2", instruction="크게", maxRounds=1), None)
    done = apigw.sent[-1]
    assert done["type"] == "studio.done" and "다릅니다" in done["error"]
    assert store.get_job("job123456789")["status"] == "failed"


def test_missing_web_url_fails_the_job_honestly(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    monkeypatch.setattr(w, "WEB_URL", "")
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    w.handler(_event(), None)
    done = apigw.sent[-1]
    assert done["type"] == "studio.done" and "WEB_URL/WEB_BUCKET 미설정" in done["error"]
    assert store.get_job("job123456789")["status"] == "failed"


def test_oversized_done_frame_drops_items(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    monkeypatch.setattr(w, "DONE_FRAME_MAX", 200)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    w.handler(_event(maxRounds=1), None)
    done = apigw.sent[-1]
    assert done["items"] == [] and done["itemsTruncated"] is True and done["score"] > 0
