"""F4 Agent Registry 액션 (SPEC §5 F4, §6.1 화면 6, S3).

요청/응답형(액션명 = 응답 type):
  registry_list       {type?, status?, subtype?, q?}   → {records(임베딩 제외), counts, backend, bootstrapped?}
  registry_get        {name, version}                  → {record, audit, versionChain}  (없으면 code 404)
  registry_transition {name, version, to, reason?}     → {ok, record, audit, transition} | {ok:false, error, code}
  registry_search     {q, type?}                       → {hits[{record, score, match, ...}], dense, note}
  registry_consumer   {subtype?, type?}                → Consumer API 결과 그대로 (APPROVED 만) — "에이전트가 보는 것"
  registry_create     {record}                         → {ok, record} (DRAFT 로 시작)
  registry_seed       {reset?}                         → 기준선 시드 결과 (멱등)
actor 는 항상 ctx.email(Cognito 검증 사용자). 레지스트리 오류(400/404/409)는 ok:false 로 돌려주고, 그 외 예외는 진입점이 처리한다.
로그에는 사유·설명 원문을 남기지 않는다 (길이만).
"""
from __future__ import annotations

from common.ctx import Ctx
from common.log import log_event
from registry import api
from registry.model import RegistryError


def _fail(ctx: Ctx, kind: str, e: RegistryError, **extra) -> None:
    ctx.post({"type": kind, "ok": False, "error": str(e)[:300], "code": getattr(e, "code", 400),
              "errorType": type(e).__name__, **extra})


def registry_list(ctx: Ctx, body: dict) -> None:
    filters = {"type": body.get("type"), "status": body.get("status"), "subtype": body.get("subtype"), "q": body.get("q")}
    c = api.counts()
    bootstrapped = None
    if c["total"] == 0 and body.get("bootstrap", True):
        # 빈 테이블이면 기준선을 심는다 — 시연 중 "레코드 0건" 화면을 막는 안전장치 (멱등, 감사 이벤트 남음)
        from registry.seed import seed
        bootstrapped = seed(actor=ctx.email)
        log_event("registry.bootstrap", ctx.trace_id, created=bootstrapped["created"],
                  embedded=bootstrapped["embedded"], embedFailed=bootstrapped["embedFailed"])
        c = api.counts()
    ctx.post({"type": "registry_list", "records": api.list_records(filters), "counts": c,
              "filters": filters, "backend": api.backend(), "embeddingsEnabled": api.embeddings_enabled(),
              "bootstrapped": bootstrapped})


def registry_get(ctx: Ctx, body: dict) -> None:
    name, version = str(body.get("name", "")).strip(), str(body.get("version", "") or body.get("recordVersion", "")).strip()
    rec = api.get_record(name, version)
    if rec is None:
        ctx.post({"type": "registry_get", "ok": False, "code": 404, "error": f"레코드 없음: {name} {version}"})
        return
    ctx.post({"type": "registry_get", "ok": True, "record": rec, "audit": api.audit_trail(name, version),
              "versionChain": api.version_chain(name, version)})


def registry_transition(ctx: Ctx, body: dict) -> None:
    name = str(body.get("name", "")).strip()
    version = str(body.get("version", "") or body.get("recordVersion", "")).strip()
    to = str(body.get("to", "")).strip().upper()
    reason = str(body.get("reason", "") or "").strip()[:500]
    try:
        rec, ev = api.transition(name, version, to, actor=ctx.email, reason=reason)
    except RegistryError as e:
        log_event("registry.transition_rejected", ctx.trace_id, name=name, version=version, to=to,
                  code=e.code, errorType=type(e).__name__)
        _fail(ctx, "registry_transition", e, name=name, version=version, to=to)
        return
    log_event("registry.transition", ctx.trace_id, name=name, version=version, transition=ev.get("transition"),
              fromStatus=ev["from"], toStatus=ev["to"], reasonLen=len(reason), email=ctx.email,
              recordType=rec.get("recordType"), subtype=rec.get("subtype"))
    ctx.post({"type": "registry_transition", "ok": True, "record": rec, "audit": ev,
              "transition": ev.get("transition"), "auditTrail": api.audit_trail(name, version)})


def registry_search(ctx: Ctx, body: dict) -> None:
    q = str(body.get("q", "") or body.get("query", "")).strip()[:200]
    res = api.search_detailed(q, body.get("type"))
    log_event("registry.search", ctx.trace_id, query=q, hits=len(res["hits"]), dense=res["dense"])
    ctx.post({"type": "registry_search", "q": q, "recordType": body.get("type"), **res})


def registry_consumer(ctx: Ctx, body: dict) -> None:
    """화면 생성 에이전트 등 Consumer 가 실제로 받는 목록 — APPROVED 만. 필터 인자만 받고 상태 인자는 받지 않는다."""
    recs = api.list_approved(body.get("type"), body.get("subtype"))
    ctx.post({"type": "registry_consumer", "records": recs, "count": len(recs),
              "subtype": body.get("subtype"), "recordType": body.get("type"),
              "note": "Consumer API(registry.api.list_approved) — GSI byStatus 를 APPROVED 로만 질의. 다른 상태는 읽는 경로가 없다."})


def registry_create(ctx: Ctx, body: dict) -> None:
    record = body.get("record") or {}
    try:
        rec = api.create_record(record, actor=ctx.email)
    except RegistryError as e:
        _fail(ctx, "registry_create", e)
        return
    log_event("registry.create", ctx.trace_id, name=rec["name"], version=rec["recordVersion"],
              recordType=rec["recordType"], email=ctx.email)
    ctx.post({"type": "registry_create", "ok": True, "record": rec,
              "auditTrail": api.audit_trail(rec["name"], rec["recordVersion"])})


def registry_seed(ctx: Ctx, body: dict) -> None:
    from registry.seed import seed
    res = seed(actor=ctx.email, reset=bool(body.get("reset")))
    log_event("registry.seed", ctx.trace_id, reset=bool(body.get("reset")), **{k: v for k, v in res.items()
                                                                                if isinstance(v, (int, bool, str))})
    ctx.post({"type": "registry_seed", "ok": True, "result": res, "counts": api.counts()})


ROUTES = {
    "registry_list": registry_list, "registry_get": registry_get, "registry_transition": registry_transition,
    "registry_search": registry_search, "registry_consumer": registry_consumer, "registry_create": registry_create,
    "registry_seed": registry_seed,
}
