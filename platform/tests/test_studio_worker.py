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
    monkeypatch.setattr(w, "_generate", lambda system, user, max_tokens: FakeStream(["```html\n", GOOD, "\n```"]))
    monkeypatch.setattr(w, "_review_generate", lambda system, user, max_tokens: (json.dumps({"items": []}), {}))
    monkeypatch.setattr(w, "_assets_text", lambda ids: "팔레트" if ids else "")
    monkeypatch.setattr(w, "_agent_preset", lambda aid: "")


def _event(**job):
    return {"connId": "c1", "endpoint": "https://ws/prod", "reqId": "r1", "email": "u@x", "traceId": "t1",
            "job": {"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001", "outputType": "ux-flow", "maxRounds": 1, **job}}


def test_worker_streams_publishes_and_records(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    out = w.handler(_event(), None)
    assert out["statusCode"] == 200
    types = [e["type"] for e in apigw.sent]
    assert types[0] == "studio.stage" and types[-1] == "studio.done" and "studio.token" in types
    assert all(e["reqId"] == "r1" and e["traceId"] == "t1" for e in apigw.sent)
    done = apigw.sent[-1]
    assert done["draftId"] == "job123456789" and "html" not in done and done["url"] == "https://agent.example/studio/drafts/job123456789-r1.html"
    assert ("web-bkt", "studio/drafts/job123456789-r1.html") in s3.objects
    job = store.get_job("job123456789")
    assert job["status"] == "done" and job["rounds"][0]["round"] == 1 and job["draftId"] == "job123456789"
    d = store.get_draft("job123456789")
    assert d["status"] == "검토중" and d["productName"] == "아톰 축구사랑 적금" and d["bestRound"] == 1 and d["key"].endswith("-r1.html")


def test_worker_survives_gone_connection(monkeypatch):
    apigw, s3, store = _Apigw(gone=True), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    assert w.handler(_event(), None)["statusCode"] == 200
    assert store.get_job("job123456789")["status"] == "done" and len(s3.objects) == 1


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
    monkeypatch.setattr(w, "_generate", lambda system, user, max_tokens: seen.setdefault("user", user) and FakeStream(["```html\n", GOOD, "\n```"]))
    w.handler(_event(mode="refine", baseDraftId="base1", selector="h2", instruction="크게", maxRounds=1), None)
    assert "크게" in seen["user"] and GOOD in seen["user"]
    assert store.get_draft("job123456789")["parentId"] == "base1"
