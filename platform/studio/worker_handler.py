# platform/studio/worker_handler.py
"""StudioLoopFn — 비동기 워커. WsFn(handlers/studio.py) 이 Event 로 invoke 한다.

같은 WebSocket 커넥션에 studio.stage/.token/.done 을 push 하고(common.ctx.Ctx 재사용), 라운드마다 S3(웹 버킷 studio/drafts/)와
STUDIO_TABLE 에 기록한다. 모델 호출은 engine.bedrock(익명화 게이트) 만 쓴다. 커넥션이 끊겨도(GoneException) 기록은 계속한다.
"""
from __future__ import annotations

import os
import time
import uuid

from common import tracing
from common.ctx import Ctx
from common.log import log_event
from studio import loop, spec as specmod
from studio.store import StudioStore

KIND = "studio"
BUCKET = os.environ.get("WEB_BUCKET", "")
WEB_URL = os.environ.get("WEB_URL", "").rstrip("/")
PREFIX = "studio/drafts"


# ---------- 주입 지점 (테스트가 monkeypatch) ----------
def _apigw_client(endpoint: str):
    import boto3
    return boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)


def _s3_client():
    import boto3
    return boto3.client("s3")


def _graph():
    from graph.store import get_store
    return get_store()


def _store() -> StudioStore:
    return StudioStore()


def _generate(system: str, user: str, max_tokens: int):
    from engine import bedrock  # 경계 통과 지점
    return bedrock.Stream(system, user, max_tokens=max_tokens, purpose="studio.generate")


def _review_generate(system: str, user: str, max_tokens: int):
    from engine import bedrock
    return bedrock.generate(system, user, max_tokens=max_tokens, purpose="studio.review")


def _assets_text(asset_ids: list) -> str:
    from common import studio_proxy
    return studio_proxy.asset_text(asset_ids)


def _agent_preset(agent_id: str) -> str:
    from common import studio_proxy
    return studio_proxy.agent_preset(agent_id)


# ---------- 도우미 ----------
class _TolerantApigw:
    """끊긴 커넥션(GoneException)은 조용히 버리고, 그 외 일시 오류(스로틀 등)는 재시도한다 — 기록이 우선이지만
    마지막 프레임(.done)이 조용히 사라지면 클라이언트가 영원히 매달리므로 무조건 삼키지 않는다."""

    _RETRY_DELAYS = (0.2, 0.5)

    def __init__(self, inner, trace_id: str = "") -> None:
        self.inner = inner
        self.trace_id = trace_id
        self.meta = getattr(inner, "meta", None)

    def post_to_connection(self, **kw) -> None:
        attempts = len(self._RETRY_DELAYS) + 1
        for i in range(attempts):
            try:
                self.inner.post_to_connection(**kw)
                return
            except Exception as e:  # noqa: BLE001
                if "Gone" in type(e).__name__ or "Gone" in str(e):
                    return
                if i < len(self._RETRY_DELAYS):
                    time.sleep(self._RETRY_DELAYS[i])
                    continue
                log_event("studio.ws_post_failed", self.trace_id, error=f"{type(e).__name__}: {str(e)[:120]}")


class _CtxEmitter:
    """stage → WS push + 라운드 기록. review 와 publish 가 같은 라운드 키를 쓰므로 병합해서 put 한다(덮어쓰기 금지)."""

    def __init__(self, ctx: Ctx, store: StudioStore, job_id: str) -> None:
        self.ctx, self.store, self.job_id = ctx, store, job_id
        self.rounds: dict = {}

    def _put(self, n: int, **fields) -> None:
        rec = self.rounds.setdefault(n, {"round": n})
        rec.update({k: v for k, v in fields.items() if v is not None})
        self.store.put_round(self.job_id, rec)

    def stage(self, step: str, **kw) -> None:
        self.ctx.stage(KIND, step, **kw)
        if step == "review":
            self._put(int(kw.get("round", 0)), score=kw.get("score"), passed=kw.get("passed"),
                      failures=[i for i in kw.get("items", []) if i.get("verdict") != "pass"][:30],
                      undetermined=kw.get("undetermined", []), reviewerError=kw.get("reviewerError"))
        elif step == "publish":
            self._put(int(kw.get("round", 0)), url=kw.get("url"))

    def token(self, text: str) -> None:
        self.ctx.token(KIND, text)


def _s3_key(job_id: str, n: int) -> str:
    return f"{PREFIX}/{job_id}-r{n}.html"


def _time_cap(context) -> int:
    """Lambda 남은 시간에서 여유(120s)를 빼 루프 시간 캡을 정한다 — 타임아웃 직전에 강제 종료돼 기록을 잃는 것을 막는다."""
    if context is None or not hasattr(context, "get_remaining_time_in_millis"):
        return loop.TIME_CAP_S
    return max(60, min(loop.TIME_CAP_S, int(context.get_remaining_time_in_millis() / 1000) - 120))


def _fewshot(store: StudioStore, s3, bucket: str, limit: int = 2, trace_id: str = "") -> list:
    out = []
    for d in store.approved_drafts(limit):
        try:
            body = s3.get_object(Bucket=bucket, Key=d["key"])["Body"].read().decode("utf-8", "ignore")
            out.append(body[:12000])
        except Exception as e:  # noqa: BLE001
            log_event("studio.fewshot_failed", trace_id, draftId=d.get("draftId"), error=str(e)[:80])
    return out


def handler(event, context):
    t0 = time.time()
    ctx = None
    store = None
    job: dict = {}
    try:
        job = loop.clamp_job(event.get("job") or {})
        trace_id = event.get("traceId") or uuid.uuid4().hex[:12]
        # store 를 먼저 만들어 둔다 — WS 클라이언트 구성이 실패해도 잡을 failed 로 남길 수 있도록.
        store = _store()
        ctx = Ctx(apigw=_TolerantApigw(_apigw_client(event.get("endpoint", "")), trace_id=trace_id), conn_id=event.get("connId", ""),
                  email=event.get("email", ""), rid=event.get("reqId", ""), trace_id=trace_id)
        s3 = _s3_client()
        emitter = _CtxEmitter(ctx, store, job["jobId"])
        graph = _graph()
        spec = specmod.build_spec(graph, job["productCode"], job["outputType"])
        base_html, parent_id = "", ""
        if job["mode"] == "refine":
            base = store.get_draft(job["baseDraftId"])
            if not base:
                raise specmod.SpecError(f"원본 시안을 찾을 수 없습니다: {job['baseDraftId']}")
            if base.get("productCode") and base["productCode"] != job["productCode"]:
                raise specmod.SpecError(f"원본 시안의 상품({base['productCode']})과 선택한 상품({job['productCode']})이 다릅니다")
            base_html = s3.get_object(Bucket=BUCKET, Key=base["key"])["Body"].read().decode("utf-8", "ignore")
            parent_id = base["draftId"]

        def publish(job_id: str, n: int, html: str) -> str:
            s3.put_object(Bucket=BUCKET, Key=_s3_key(job_id, n), Body=html.encode("utf-8"),
                          ContentType="text/html; charset=utf-8", CacheControl="no-cache")
            return f"{WEB_URL}/studio/drafts/{job_id}-r{n}.html"

        out = loop.run(job, emitter, spec=spec, generate=_generate, review_generate=_review_generate, publish=publish,
                       assets_text=_assets_text(job["assetIds"]), fewshot=_fewshot(store, s3, BUCKET, trace_id=trace_id),
                       agent_preset=_agent_preset(job["agentId"]), base_html=base_html, time_cap_s=_time_cap(context))
        draft = None
        if out["url"]:
            title = (job["brief"][:40] or spec["productName"]) + (" (수정)" if job["mode"] == "refine" else "")
            draft = store.put_draft({"draftId": job["jobId"], "jobId": job["jobId"], "title": title, "axis": "수정" if job["mode"] == "refine" else job["axis"],
                                     "outputType": job["outputType"], "productCode": spec["productCode"], "productName": spec["productName"],
                                     "score": out["score"], "passed": out["passed"], "rounds": out["rounds"], "bestRound": out["bestRound"],
                                     "stopReason": out["stopReason"], "url": out["url"], "key": _s3_key(job["jobId"], out["bestRound"]),
                                     "parentId": parent_id, "createdBy": ctx.email, "model": out["model"]})
        job_fields = {"status": "done" if not out.get("error") else "failed", "score": out["score"], "passed": out["passed"],
                     "stopReason": out["stopReason"], "rounds": out["rounds"], "draftId": draft["draftId"] if draft else None,
                     "error": out.get("error")}
        store.update_job(job["jobId"], **{k: v for k, v in job_fields.items() if v is not None})
        try:
            tracing.record_trace({"traceId": ctx.trace_id, "scenario": "studio", "email": ctx.email, "query": job["brief"],
                                  "tokensIn": out["usage"]["inputTokens"], "tokensOut": out["usage"]["outputTokens"], "plane": "cloud",
                                  "rounds": out["rounds"], "score": out["score"], "passed": out["passed"], "stopReason": out["stopReason"],
                                  "elapsedMs": int((time.time() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001 — 계측 실패가 결과 전달을 막지 않는다 (TRACE_TABLE 미설정 등)
            log_event("studio.trace_failed", ctx.trace_id, error=str(e)[:80])
        done = {k: v for k, v in out.items() if k != "html"}
        ctx.done(KIND, **done, draftId=draft["draftId"] if draft else None, backend=store.backend, graphBackend=getattr(graph, "name", "unknown"))
        return {"statusCode": 200}
    except Exception as e:  # noqa: BLE001 — 사용자에게 보여야 하는 실패
        msg = f"{type(e).__name__}: {str(e)[:200]}" if not isinstance(e, specmod.SpecError) else str(e)
        job_id = job.get("jobId", "")
        if store is not None:
            try:
                store.update_job(job_id, status="failed", error=msg)
            except Exception:
                pass
        if ctx is not None:
            ctx.done(KIND, jobId=job_id, error=msg, stopReason="error", rounds=0, score=0, passed=False)
        else:
            log_event("studio.worker_failed", event.get("traceId", ""), jobId=job_id, error=msg)
        return {"statusCode": 200}
