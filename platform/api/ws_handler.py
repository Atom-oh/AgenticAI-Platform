"""WebSocket 진입점 — 인증 연결 + 액션 디스패치 (얇게 유지한다).

  $connect    : ?token=<Cognito access token> → cognito-idp GetUser로 검증. 무토큰/무효 토큰은 거부.
  $disconnect : 연결 레코드 삭제
  $default    : {"action": "...", "reqId": "...", ...} → handlers.ROUTES[action](ctx, body)

관리 작업(Neptune 적재·Registry 시드)은 여기 없다 — admin_handler.py (IAM invoke 전용).
"""
from __future__ import annotations

import json
import os
import time

import boto3

from common.ctx import Ctx
from common.log import log_event
from handlers import ROUTES

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
CONN_TABLE = os.environ["CONN_TABLE"]
_ddb = boto3.resource("dynamodb", region_name=REGION).Table(CONN_TABLE)
_idp = boto3.client("cognito-idp", region_name=REGION)


def _connect(event, conn_id: str) -> dict:
    token = (event.get("queryStringParameters") or {}).get("token", "")
    if not token:
        return {"statusCode": 401}
    try:
        u = _idp.get_user(AccessToken=token)
        email = next((a["Value"] for a in u["UserAttributes"] if a["Name"] == "email"), u["Username"])
    except Exception:
        log_event("ws.connect_rejected", connId=conn_id)
        return {"statusCode": 403}
    _ddb.put_item(Item={"connId": conn_id, "email": email, "ts": int(time.time()),
                        "ttl": int(time.time()) + 3600 * 3})
    log_event("ws.connected", connId=conn_id, email=email)
    return {"statusCode": 200}


def handler(event, context):
    rc = event.get("requestContext", {})
    route = rc.get("routeKey")
    conn_id = rc.get("connectionId")

    if route == "$connect":
        return _connect(event, conn_id)
    if route == "$disconnect":
        _ddb.delete_item(Key={"connId": conn_id})
        return {"statusCode": 200}

    rec = _ddb.get_item(Key={"connId": conn_id}).get("Item")
    if not rec:  # $connect에서 검증되지 않은 연결은 도달 불가
        return {"statusCode": 403}
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        body = {}
    action = str(body.get("action", ""))
    rid = str(body.get("reqId", ""))[:32]
    endpoint = f"https://{rc['domainName']}/{rc['stage']}"
    ctx = Ctx(apigw=boto3.client("apigatewaymanagementapi", endpoint_url=endpoint),
              conn_id=conn_id, email=rec["email"], rid=rid)

    fn = ROUTES.get(action)
    if fn is None:
        ctx.error("지원하지 않는 요청입니다.")
        return {"statusCode": 400}
    log_event("ws.action", ctx.trace_id, action=action, email=rec["email"])
    try:
        fn(ctx, body)
    except Exception as e:
        log_event("ws.action_failed", ctx.trace_id, action=action, error=f"{type(e).__name__}: {str(e)[:200]}")
        try:
            ctx.error(f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return {"statusCode": 500}
    return {"statusCode": 200}
