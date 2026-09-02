"""비용 가드 + 오프라인 폴백 (SPEC §6.3, §10).

- 일일 토큰 상한(DAILY_TOKEN_CAP): 초과 시 Bedrock을 호출하지 않고 캐시 응답을 재생한다.
- Bedrock 호출 실패 시에도 캐시 응답을 재생한다.
- 캐시 재생 이벤트에는 항상 cached=True 가 붙고, 프론트는 "캐시 응답" 배지를 띄운다
  (가짜를 진짜처럼 보이게 하지 않는다).

캐시 키: scenario + sha256(query). 저장: CACHE_TABLE (pk). 사용량: pk=usage#YYYY-MM-DD.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import boto3

from common.log import log_event

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
CACHE_TABLE = os.environ.get("CACHE_TABLE", "")
DAILY_TOKEN_CAP = int(os.environ.get("DAILY_TOKEN_CAP", "2000000"))
_tbl = boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE) if CACHE_TABLE else None


class BudgetExceeded(Exception):
    pass


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _key(scenario: str, query: str) -> str:
    return f"cache#{scenario}#{hashlib.sha256((query or '').strip().encode()).hexdigest()[:24]}"


def usage_today() -> int:
    if _tbl is None:
        return 0
    it = _tbl.get_item(Key={"pk": f"usage#{_today()}"}).get("Item")
    return int(it.get("tokens", 0)) if it else 0


def add_usage(tokens: int) -> int:
    if _tbl is None or tokens <= 0:
        return 0
    r = _tbl.update_item(Key={"pk": f"usage#{_today()}"},
                         UpdateExpression="ADD tokens :t SET #ttl = :ttl",
                         ExpressionAttributeNames={"#ttl": "ttl"},
                         ExpressionAttributeValues={":t": tokens, ":ttl": int(time.time()) + 86400 * 3},
                         ReturnValues="UPDATED_NEW")
    return int(r["Attributes"]["tokens"])


def budget_ok() -> bool:
    return usage_today() < DAILY_TOKEN_CAP


def get_cached(scenario: str, query: str) -> list[dict] | None:
    if _tbl is None:
        return None
    it = _tbl.get_item(Key={"pk": _key(scenario, query)}).get("Item")
    return json.loads(it["events"]) if it else None


def put_cached(scenario: str, query: str, events: list[dict]) -> None:
    if _tbl is None or not events:
        return
    _tbl.put_item(Item={"pk": _key(scenario, query), "scenario": scenario, "ts": int(time.time() * 1000),
                        "events": json.dumps(events, ensure_ascii=False, default=str)})


def replay(ctx, events: list[dict], reason: str) -> None:
    ctx.post({"type": "cache.replay", "reason": reason, "count": len(events)})
    for ev in events:
        ev = dict(ev)
        ev["cached"] = True
        ev["reqId"] = ctx.rid
        ctx.post(ev)


def guarded(ctx, scenario: str, query: str, run) -> dict:
    """run(ctx)를 실행하되, 예산 초과·실패 시 캐시 재생. 반환: {"cached": bool, "reason": str}."""
    cached = get_cached(scenario, query)
    if not budget_ok():
        if cached:
            log_event("costguard.budget_exceeded", ctx.trace_id, scenario=scenario, replay=True)
            replay(ctx, cached, "일일 Bedrock 토큰 상한 초과")
            return {"cached": True, "reason": "budget"}
        raise BudgetExceeded("일일 Bedrock 토큰 상한을 초과했고 캐시된 응답이 없습니다.")
    ctx.recording = []
    try:
        run(ctx)
        put_cached(scenario, query, ctx.recording)
        return {"cached": False, "reason": ""}
    except Exception as e:
        log_event("costguard.run_failed", ctx.trace_id, scenario=scenario, error=str(e)[:200],
                  replay=bool(cached))
        if cached:
            replay(ctx, cached, f"Bedrock 호출 실패: {type(e).__name__}")
            return {"cached": True, "reason": "error"}
        raise
    finally:
        ctx.recording = None
