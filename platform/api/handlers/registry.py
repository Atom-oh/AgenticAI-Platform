"""F4 Agent Registry 액션 (SPEC §5 F4, §6.1 화면 6, S3).

요청/응답형(액션명 = 응답 type):
  registry_list       {type?, status?, subtype?, q?}   → {records(임베딩 제외), counts, backend, bootstrapped?}
  registry_get        {name, version}                  → {record, audit, versionChain}  (없으면 code 404)
  registry_transition {name, version, to, reason?}     → {ok, record, audit, transition} | {ok:false, error, code}
  registry_search     {q, type?}                       → {hits[{record, score, match, ...}], dense, note}
  registry_consumer   {subtype?, type?}                → Consumer API 결과 그대로 (APPROVED 만) — "에이전트가 보는 것"
  registry_create     {record}                         → {ok, record} (DRAFT 로 시작)
  기준선 시드는 IAM 전용 admin_handler에서만 수행한다.
actor 는 항상 ctx.email(Cognito 검증 사용자). 레지스트리 오류(400/404/409)는 ok:false 로 돌려주고, 그 외 예외는 진입점이 처리한다.
로그에는 사유·설명 원문을 남기지 않는다 (길이만).
"""
from __future__ import annotations

from common.ctx import Ctx
from common.log import log_event
from registry import api
from registry.model import RegistryError, STATUSES


def _fail(ctx: Ctx, kind: str, e: RegistryError, **extra) -> None:
    ctx.post({"type": kind, "ok": False, "error": str(e)[:300], "code": getattr(e, "code", 400),
              "errorType": type(e).__name__, **extra})


def registry_list(ctx: Ctx, body: dict) -> None:
    filters = {"type": body.get("type"), "status": body.get("status"), "subtype": body.get("subtype"), "q": body.get("q")}
    c = api.counts()
    ctx.post({"type": "registry_list", "records": [api.public_record(r) for r in api.list_records(filters)], "counts": c,
              "filters": filters, "backend": api.backend(), "embeddingsEnabled": api.embeddings_enabled(),
              "bootstrapped": None})


def registry_get(ctx: Ctx, body: dict) -> None:
    name, version = str(body.get("name", "")).strip(), str(body.get("version", "") or body.get("recordVersion", "")).strip()
    rec = api.get_record(name, version)
    if rec is None:
        ctx.post({"type": "registry_get", "ok": False, "code": 404, "error": f"레코드 없음: {name} {version}"})
        return
    audit = api.audit_trail(name, version)
    if rec.get("recordType") == "AGENT":
        audit = api.public_agent_audit(audit)
    ctx.post({"type": "registry_get", "ok": True, "record": api.public_record(rec), "audit": audit,
              "versionChain": api.version_chain(name, version)})


def registry_transition(ctx: Ctx, body: dict) -> None:
    name = str(body.get("name", "")).strip()
    version = str(body.get("version", "") or body.get("recordVersion", "")).strip()
    to = str(body.get("to", "")).strip().upper()
    reason = str(body.get("reason", "") or "").strip()[:500]
    try:
        record = api.get_record(name, version, include_internal=True)
        if record and record.get("subtype") == "AGENT_ADMIN_REQUEST":
            ctx.post({"type": "registry_transition", "ok": False, "code": 403,
                      "error": "관리자 요청의 처리 상태는 IAM 관리자만 변경할 수 있습니다."})
            return
        if record and record.get("recordType") == "AGENT":
            from agentcore.administration import request_transition
            ctx.post({"type": "registry_transition", "ok": True,
                      **request_transition(name, version, to, ctx.email, reason)})
            return
        rec, ev = api.transition(name, version, to, actor=ctx.email, reason=reason)
    except RegistryError as e:
        log_event("registry.transition_rejected", ctx.trace_id, name=name, version=version, to=to if to in STATUSES else "invalid",
                  code=e.code, errorType=type(e).__name__)
        _fail(ctx, "registry_transition", e, name=name, version=version, to=to)
        return
    log_event("registry.transition", ctx.trace_id, name=name, version=version, transition=ev.get("transition"),
              fromStatus=ev["from"], toStatus=ev["to"], reasonLen=len(reason), email=ctx.email,
              recordType=rec.get("recordType"), subtype=rec.get("subtype"))
    ctx.post({"type": "registry_transition", "ok": True, "record": api.public_record(rec), "audit": ev,
              "transition": ev.get("transition"), "auditTrail": api.audit_trail(name, version)})


def registry_search(ctx: Ctx, body: dict) -> None:
    q = str(body.get("q", "") or body.get("query", "")).strip()[:200]
    res = api.search_detailed(q, body.get("type"))
    res["hits"] = [{**hit, "record": api.public_record(hit["record"])} for hit in res["hits"]]
    log_event("registry.search", ctx.trace_id, queryLen=len(q), hits=len(res["hits"]), dense=res["dense"])
    ctx.post({"type": "registry_search", "q": q, "recordType": body.get("type"), **res})


def registry_consumer(ctx: Ctx, body: dict) -> None:
    """화면 생성 에이전트 등 Consumer 가 실제로 받는 목록 — APPROVED 만. 필터 인자만 받고 상태 인자는 받지 않는다."""
    recs = [api.public_record(r) for r in api.list_approved(body.get("type"), body.get("subtype"))]
    ctx.post({"type": "registry_consumer", "records": recs, "count": len(recs),
              "subtype": body.get("subtype"), "recordType": body.get("type"),
              "note": "Consumer API(registry.api.list_approved) — GSI byStatus 를 APPROVED 로만 질의. 다른 상태는 읽는 경로가 없다."})


def registry_create(ctx: Ctx, body: dict) -> None:
    record = body.get("record") or {}
    if isinstance(record, dict) and (str(record.get("recordType", "")).strip().upper() == "AGENT"
                                   or str(record.get("subtype", "")).strip().upper() == "AGENT_ADMIN_REQUEST"
                                   or str(record.get("name", "")).strip().startswith("agent-request-")):
        ctx.post({"type": "registry_create", "ok": False, "code": 403,
                  "error": "에이전트 명세와 관리 요청은 에이전트 전용 요청 경로를 사용하세요."})
        return
    try:
        rec = api.create_record(record, actor=ctx.email)
    except RegistryError as e:
        _fail(ctx, "registry_create", e)
        return
    log_event("registry.create", ctx.trace_id, name=rec["name"], version=rec["recordVersion"],
              recordType=rec["recordType"], email=ctx.email)
    ctx.post({"type": "registry_create", "ok": True, "record": api.public_record(rec),
              "auditTrail": api.audit_trail(rec["name"], rec["recordVersion"])})


def registry_seed(ctx: Ctx, body: dict) -> None:
    ctx.post({"type": "registry_seed", "ok": False, "code": 403,
              "error": "Shared seeding requires the IAM-only administrative entry point."})


ROUTES = {
    "registry_list": registry_list, "registry_get": registry_get, "registry_transition": registry_transition,
    "registry_search": registry_search, "registry_consumer": registry_consumer, "registry_create": registry_create,
}
