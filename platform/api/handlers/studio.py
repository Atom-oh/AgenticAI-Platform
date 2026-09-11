# platform/api/handlers/studio.py
"""디자인 스튜디오 액션 — 잡 실행(워커 Lambda 비동기 invoke)·잡/시안 조회·승인·상품/명세 미리보기·자산 프록시.

studio_run 은 요청/응답형 ack(`studio_run`) 1건을 보내고, 이후 이벤트(studio.stage/.token/.done)는 StudioLoopFn 이
같은 커넥션으로 push 한다 (studio/worker_handler.py). 워커가 미배포면 흉내내지 않고 .done(error) 로 알린다.
"""
from __future__ import annotations

import json
import os
import uuid

from common import studio_proxy
from common.ctx import Ctx
from common.log import log_event
from studio import loop, spec as specmod
from studio.store import StudioStore
from engine import model_catalog
from studio.asset_import import validated_content

KIND = "studio"
_store: StudioStore | None = None
_graph = None


def store() -> StudioStore:
    global _store
    if _store is None:
        _store = StudioStore()
    return _store


def graph():
    global _graph
    if _graph is None:
        from graph.store import get_store
        _graph = get_store()
    return _graph


def _invoke(fn_name: str, payload: dict) -> None:
    import boto3  # 지연 import — 테스트 오프라인
    boto3.client("lambda").invoke(FunctionName=fn_name, InvocationType="Event",
                                  Payload=json.dumps(payload, ensure_ascii=False).encode())


def studio_run(ctx: Ctx, body: dict) -> None:
    try:
        job = loop.clamp_job(body)
    except ValueError as error:
        ctx.done(KIND, error=str(error), stopReason="error", passed=False)
        return
    if not job["productCode"]:
        ctx.done(KIND, error="productCode 가 필요합니다 — 상품을 선택하세요")
        return
    if job["mode"] == "refine" and not job["baseDraftId"]:
        ctx.done(KIND, error="refine 모드에는 baseDraftId 가 필요합니다")
        return
    fn = os.environ.get("STUDIO_LOOP_FN", "")
    if not fn:
        ctx.done(KIND, error="미배포: STUDIO_LOOP_FN 미설정 — 워커 Lambda 가 배포되지 않았습니다")
        return
    job["jobId"] = uuid.uuid4().hex[:12]
    store().put_job(job, actor=ctx.email)
    payload = {"connId": ctx.conn_id, "endpoint": getattr(getattr(ctx.apigw, "meta", None), "endpoint_url", ""),
               "reqId": ctx.rid, "email": ctx.email, "traceId": ctx.trace_id, "job": job}
    try:
        _invoke(fn, payload)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
        store().update_job(job["jobId"], status="failed", stopReason="error", error=err)
        log_event("studio.invoke_failed", ctx.trace_id, jobId=job["jobId"], error=err)
        ctx.done(KIND, jobId=job["jobId"], error="워커 호출 실패: " + err, stopReason="error", rounds=0, score=0, passed=False)
        return
    log_event("studio.run", ctx.trace_id, jobId=job["jobId"], productCode=job["productCode"], maxRounds=job["maxRounds"], mode=job["mode"])
    ctx.post({"type": "studio_run", "jobId": job["jobId"], "maxRounds": job["maxRounds"], "passScore": job["passScore"],
              "backend": store().backend, "model": job["model"]})


def studio_models(ctx: Ctx, body: dict) -> None:
    ctx.post({"type": "studio_models", "models": model_catalog.options(), "defaultModel": model_catalog.resolve(),
              "scope": "platform-allowed-bedrock"})


def studio_jobs(ctx: Ctx, body: dict) -> None:
    jid = str(body.get("jobId") or "")
    if jid:
        ctx.post({"type": "studio_jobs", "job": store().get_job(jid)})
    else:
        ctx.post({"type": "studio_jobs", "jobs": store().list_jobs(loop._int(body.get("limit"), 20, 1, 200))})


def studio_round(ctx: Ctx, body: dict) -> None:
    number = body.get("round")
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= loop.MAX_ROUNDS:
        ctx.post({"type": "studio_round", "error": "검수할 라운드를 다시 선택하세요."})
        return
    result = store().get_round(str(body.get("jobId", "")), number)
    ctx.post({"type": "studio_round", "round": result, "error": None if result else "해당 라운드의 검수 기록이 없습니다."})


def studio_drafts(ctx: Ctx, body: dict) -> None:
    ctx.post({"type": "studio_drafts", "drafts": store().list_drafts(loop._int(body.get("limit"), 60, 1, 200)), "backend": store().backend})


def studio_feedback(ctx: Ctx, body: dict) -> None:
    decision = body.get("decision")
    status = {"approve": "승인됨", "reject": "반려"}.get(decision)
    if not status:
        ctx.post({"type": "studio_feedback", "error": f"decision 은 approve|reject 여야 합니다: {decision!r}"})
        return
    if decision == "approve":
        draft = store().get_draft(str(body.get("draftId", ""))) or {}
        job = store().get_job(draft.get("jobId", "")) or {}
        result = next((r for r in job.get("rounds", []) if r.get("round") == draft.get("bestRound")), None)
        if (draft.get("validationScope") != "static-design" or draft.get("passed") is not True
                or not result or result.get("passed") is not True
                or "undetermined" not in result or result["undetermined"] or result.get("reviewerError")
                or not draft.get("url") or result.get("url") != draft["url"]
                or any(f.get("id") == "STABLE" and f.get("weight", 0) > 0 and f.get("verdict") != "pass"
                       for f in result.get("failures", []))):
            ctx.post({"type": "studio_feedback", "error": "이 시안의 정적 검수가 완료되지 않았습니다. 미판정·실패 항목을 해결하고 다시 검수하세요.",
                      "code": "static-review-required"})
            return
    d = store().set_draft_status(str(body.get("draftId", "")), status, str(body.get("comment", "")), actor=ctx.email)
    if not d:
        ctx.post({"type": "studio_feedback", "error": "시안을 찾을 수 없습니다"})
        return
    log_event("studio.feedback", ctx.trace_id, draftId=d["draftId"], status=status, email=ctx.email)  # 키 email → common.log 가 해시로 치환
    ctx.post({"type": "studio_feedback", "draft": d, "approvalScope": "static-design"})


def studio_products(ctx: Ctx, body: dict) -> None:
    g = graph()
    ctx.post({"type": "studio_products", "products": specmod.list_products(g), "graphBackend": getattr(g, "name", "unknown"),
              "model": loop.MODEL})


def studio_spec(ctx: Ctx, body: dict) -> None:
    try:
        s = specmod.build_spec(graph(), str(body.get("productCode", "")), str(body.get("outputType", "design")))
        ctx.post({"type": "studio_spec", "spec": s})
    except specmod.SpecError as e:
        ctx.post({"type": "studio_spec", "error": str(e)})


def studio_asset(ctx: Ctx, body: dict) -> None:
    import urllib.parse
    aid = urllib.parse.quote(str(body.get("assetId", "")))
    content = studio_proxy.studio_get(f"/api/assets/content?asset_id={aid}")
    history = studio_proxy.studio_get(f"/api/assets/history?asset_id={aid}")
    ctx.post({"type": "studio_asset", "content": content, "history": history.get("history", history)})


def _scope(v) -> str:
    """레지스트리 scope 는 허용값만 통과시킨다 — 임의 문자열이 그대로 프록시로 나가지 않게."""
    return v if v in ("shared", "mine") else "shared"


def studio_register(ctx: Ctx, body: dict) -> None:
    try:
        content = validated_content(body.get("content"))
    except ValueError as error:
        ctx.post({"type": "studio_register", "error": str(error)})
        return
    r = studio_proxy.studio("POST", "/api/assets", str(body.get("studioToken", "")),
                            {"name": str(body.get("name", ""))[:80], "type": str(body.get("assetType", "")),
                             "content": content, "scope": _scope(body.get("scope"))})
    ctx.post({"type": "studio_register", **r})


ROUTES = {"studio_run": studio_run, "studio_models": studio_models, "studio_jobs": studio_jobs, "studio_round": studio_round, "studio_drafts": studio_drafts, "studio_feedback": studio_feedback,
          "studio_products": studio_products, "studio_spec": studio_spec, "studio_asset": studio_asset, "studio_register": studio_register}
