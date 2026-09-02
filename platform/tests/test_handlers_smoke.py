"""진입점·디스패치 스모크 — AWS 없이 라우팅과 오류 변환을 검증한다."""
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


class _Apigw:
    def __init__(self):
        self.sent = []

    def post_to_connection(self, ConnectionId, Data):
        self.sent.append(json.loads(Data))


def test_routes_cover_spec_actions():
    required = {"s1", "s2", "hub", "traces", "explore", "reset", "surfaces"}
    assert required <= set(ws_handler.ROUTES), set(ws_handler.ROUTES)
    assert "load_neptune" not in ws_handler.ROUTES, "관리 작업은 사용자 경로에 없어야 한다"


def test_ctx_post_adds_req_and_trace_ids():
    a = _Apigw()
    ctx = Ctx(apigw=a, conn_id="c", email="u@x", rid="r9")
    ctx.stage("s2", "lookup", values={"a": 1})
    ctx.done("s2", ok=True)
    assert a.sent[0]["type"] == "s2.stage" and a.sent[0]["reqId"] == "r9" and a.sent[0]["traceId"] == ctx.trace_id
    assert a.sent[1]["type"] == "s2.done" and "elapsedMs" in a.sent[1]


def test_recording_excludes_errors():
    a = _Apigw()
    ctx = Ctx(apigw=a, conn_id="c", email="u@x", rid="r1")
    ctx.recording = []
    ctx.token("s1", "x")
    ctx.error("boom")
    assert [e["type"] for e in ctx.recording] == ["s1.token"]


def test_unknown_action_returns_400(monkeypatch):
    a = _Apigw()
    monkeypatch.setattr(ws_handler, "_ddb", type("T", (), {"get_item": lambda self, Key: {"Item": {"connId": "c", "email": "u@x"}}})())
    monkeypatch.setattr(ws_handler.boto3, "client", lambda *args, **kw: a)
    ev = {"requestContext": {"routeKey": "$default", "connectionId": "c", "domainName": "d", "stage": "prod"},
          "body": json.dumps({"action": "nope", "reqId": "r1"})}
    assert ws_handler.handler(ev, None)["statusCode"] == 400
    assert a.sent[0]["type"] == "error"


def test_connect_without_token_is_401():
    ev = {"requestContext": {"routeKey": "$connect", "connectionId": "c"}, "queryStringParameters": None}
    assert ws_handler.handler(ev, None)["statusCode"] == 401
