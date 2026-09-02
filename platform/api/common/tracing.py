"""F6 경계 계측 저장소 — 전용 TRACE_TABLE (연결 테이블과 분리).

레코드에는 프롬프트/질문 원문을 넣지 않는다 (§12.3): queryHash·queryLen만.
S4 Two-Plane 뷰와 대시보드 카운터는 이 테이블의 실측값 합이다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import boto3

from common.log import hash8, log_event

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
TRACE_TABLE = os.environ.get("TRACE_TABLE", "")
_tbl = boto3.resource("dynamodb", region_name=REGION).Table(TRACE_TABLE) if TRACE_TABLE else None

TTL_DAYS = 7


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def record_trace(rec: dict) -> None:
    """rec 필수: traceId, scenario. 선택: email(→userHash), query(→queryHash/Len), 그 외 메트릭."""
    now = time.time()
    rec = dict(rec)
    if "email" in rec:
        rec["userHash"] = hash8(rec.pop("email"))
    if "query" in rec:
        q = rec.pop("query") or ""
        rec["queryHash"], rec["queryLen"] = hash8(q), len(q)
    item = {"traceId": rec["traceId"], "day": _day(now), "ts": int(now * 1000),
            "ttl": int(now) + 86400 * TTL_DAYS, **rec}
    item = json.loads(json.dumps(item, default=str), parse_float=str)
    log_event("trace.recorded", rec["traceId"], **{k: v for k, v in item.items()
                                                    if k not in ("traceId", "ttl")})
    if _tbl is not None:
        _tbl.put_item(Item=item)


def list_traces(limit: int = 60) -> list[dict]:
    """최근 트레이스 (오늘·어제, 최신순)."""
    if _tbl is None:
        return []
    from boto3.dynamodb.conditions import Key
    now = time.time()
    items: list[dict] = []
    for day in (_day(now), _day(now - 86400)):
        r = _tbl.query(IndexName="byDay", KeyConditionExpression=Key("day").eq(day),
                       ScanIndexForward=False, Limit=limit)
        items += r.get("Items", [])
        if len(items) >= limit:
            break
    items.sort(key=lambda x: int(x.get("ts", 0)), reverse=True)
    return items[:limit]


def summarize(items: list[dict]) -> dict:
    return {
        "piiOutboundTotal": sum(int(i.get("piiOutbound", 0) or 0) for i in items),
        "requests": len(items),
        "blocked": sum(1 for i in items if i.get("blocked")),
        "cached": sum(1 for i in items if i.get("cached")),
        "tokensOutTotal": sum(int(i.get("tokensOut", 0) or 0) for i in items),
    }
