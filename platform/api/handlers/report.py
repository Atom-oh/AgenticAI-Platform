"""F7 보고서 생성 오케스트레이터 (SPEC §5 F7, §6.1 화면 8).

Reader(외부 콘텐츠 전용, 내부 도구 권한 없음) → [구조화 JSON 인계] → Writer(내부 문서 접근, 외부 원문 미접근).
두 역할은 **별도 Lambda + 별도 IAM 역할**(READER_FN / WRITER_FN)이며 이 핸들러는 invoke 만 한다.
Reader 의 내부 도구 호출 시도가 IAM AccessDeniedException 으로 거부된 기록을 그대로 화면에 올린다.

단계 이벤트는 **실제 실행 시점**에만 내보낸다:
  reader_fetch(running) → [Reader invoke: fetch+요약이 한 invoke] → reader_fetch(done) · reader_summarize(done)
  → handoff(done) → writer_search(running) → [Writer invoke phase=search] → writer_search(done)
  → writer_generate(running) → [Writer invoke phase=generate, 워커 스레드] ‖ [이 스레드: STREAM_TABLE 릴레이 항목 폴링 →
    새 조각을 report.token 으로 즉시 전달] → writer_generate(done, delivery=stream|partial_fallback|single_event) → report.done

토큰 릴레이가 DynamoDB 인 이유: Writer 는 인터넷 경로가 없는 격리 서브넷에 있고, API Gateway execute-api 인터페이스
엔드포인트는 프라이빗 REST API 전용이라 리저널 WebSocket API 의 @connections 관리 엔드포인트에 닿지 않는다.
DynamoDB 게이트웨이 엔드포인트는 격리 서브넷에서 동작한다. Writer 가 조각을 쓰지 못했거나 WsFn 이 읽지 못하면
미전송 잔여분을 단일 이벤트로 보내고 delivery·streamError·relayError 에 그대로 남긴다 — 스트리밍인 척하지 않는다.

액션:
  report        (스트리밍) body {url?, audience?}
  report_sample (요청/응답) 샘플 경로 · 심어둔 인젠션 지시문 · 배포 상태
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from common import costguard, pii, tracing
from common.ctx import Ctx
from common.log import log_event
from report.common import INJECTED_INSTRUCTION, SAMPLE_PATH

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
KIND = "report"
STEPS = ["reader_fetch", "reader_summarize", "handoff", "writer_search", "writer_generate"]
ROLE_READER = "ReaderRole — 외부 콘텐츠 전용 · 내부 도구 invoke 권한 없음"
ROLE_WRITER = "WriterRole — 내부 문서 검색 허용 · 외부 원문 미접근(격리 서브넷)"
STREAM_POLL_S = 0.25          # 릴레이 폴링 주기 — 클라이언트가 보는 토큰 지연 상한
_KEY_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _env() -> dict:
    return {k: os.environ.get(k, "") for k in ("READER_FN", "WRITER_FN", "INTERNAL_TOOL_FN", "WEB_URL", "STREAM_TABLE")}


def _lambda_client():
    import boto3
    return boto3.client("lambda", region_name=REGION)


def _ddb_client():
    import boto3
    return boto3.client("dynamodb", region_name=REGION)


def default_url() -> str:
    web = os.environ.get("WEB_URL", "").rstrip("/")
    return web + SAMPLE_PATH if web else ""


def invoke_fn(name: str, payload: dict, label: str) -> dict:
    """Lambda 동기 invoke. 함수 오류는 RuntimeError 로 — 실패를 성공처럼 보이게 하지 않는다."""
    r = _lambda_client().invoke(FunctionName=name, Payload=json.dumps(payload, ensure_ascii=False).encode())
    body = json.loads(r["Payload"].read().decode() or "{}")
    if r.get("FunctionError") or (isinstance(body, dict) and "errorMessage" in body):
        et = body.get("errorType", "") if isinstance(body, dict) else ""
        em = body.get("errorMessage", str(body)) if isinstance(body, dict) else str(body)
        raise RuntimeError(f"{label} Lambda 오류 {et}: {str(em)[:300]}")
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"{label}: {str(body['error'])[:300]}")
    return body if isinstance(body, dict) else {}


def _deployed(env: dict) -> dict:
    return {"reader": bool(env["READER_FN"]), "writer": bool(env["WRITER_FN"]),
            "internalTool": bool(env["INTERNAL_TOOL_FN"]), "stream": bool(env["STREAM_TABLE"])}


def relay_key(ctx: Ctx) -> str:
    """릴레이 항목 키 — Writer 의 키 정책('stream#<traceId>[#<reqId>]')과 같은 형식."""
    tid = _KEY_SAFE.sub("", str(ctx.trace_id))[:64] or "notrace"
    rid = _KEY_SAFE.sub("", str(ctx.rid))[:32]
    return f"stream#{tid}" + (f"#{rid}" if rid else "")


def relay_read(ddb, table: str, key: str) -> str:
    """릴레이 항목의 조각을 이어붙인 현재 텍스트 (Writer 가 list_append 로 순서대로 쓴다)."""
    it = ddb.get_item(TableName=table, Key={"pk": {"S": key}}, ConsistentRead=True).get("Item") or {}
    return "".join(c.get("S", "") for c in (it.get("chunks") or {}).get("L", []) if isinstance(c, dict))


def handle_report(ctx: Ctx, body: dict) -> None:
    env = _env()
    url = str(body.get("url") or default_url())[:500].strip()
    audience = str(body.get("audience") or "")[:60]
    if not env["READER_FN"] or not env["WRITER_FN"]:
        ctx.done(KIND, error="미배포: Reader/Writer Lambda 가 배포되지 않았습니다 (READER_FN·WRITER_FN 미설정)",
                 deployed=_deployed(env), url=url)
        return
    if not url:
        ctx.done(KIND, error="읽을 URL 이 없습니다 (url 미지정 · WEB_URL 미설정)", deployed=_deployed(env))
        return

    state: dict = {"reader": {}, "writer": {}, "denied": [], "injection": False, "pii": {"count": 0}, "docs": 0,
                   "delivery": ""}

    def run(c: Ctx) -> None:
        # ① Reader — 별도 Lambda(ReaderRole). fetch + 요약 + 도구 호출 시도(거부) 가 한 번의 invoke 안에서 일어난다.
        #    따라서 reader_fetch 의 done 은 Reader 종료 시점에 reader_summarize 와 함께 도착한다 (UI 도 그렇게 표기).
        c.stage(KIND, "reader_fetch", status="running", url=url, plane="cloud", role=ROLE_READER,
                note="Reader Lambda 1회 invoke 안에서 fetch·요약이 함께 실행됨 — 단계 경계는 Reader 종료 시 함께 도착")
        rd = invoke_fn(env["READER_FN"], {"url": url, "traceId": c.trace_id}, "Reader")
        state["reader"] = rd
        c.stage(KIND, "reader_fetch", status="done", url=url, chars=int(rd.get("textChars", 0)), fetch=rd.get("fetch", {}),
                textExcerpt=str(rd.get("textExcerpt", ""))[:1500], plane="cloud", role=rd.get("role", ROLE_READER))
        summary = rd.get("summary") or {}
        denied = list(rd.get("deniedAttempts") or [])
        state["denied"] = denied
        state["injection"] = bool(summary.get("injectionDetected"))
        c.stage(KIND, "reader_summarize", status="done", summary=summary, deniedAttempts=denied,
                deniedCount=sum(1 for d in denied if d.get("error")),
                allowedUnexpected=[d for d in denied if d.get("allowed")],
                toolCalls=rd.get("toolCalls") or [], usage=rd.get("usage") or {}, rounds=rd.get("rounds"),
                heuristicHits=rd.get("heuristicHits") or [], model=rd.get("model", ""),
                plane="cloud", role=rd.get("role", ROLE_READER))

        # ② 인계 — 스키마 JSON 만. 규칙 스캐너로 경계 페이로드를 실측(개인식별자 0 이 정상).
        handoff = json.dumps(summary, ensure_ascii=False)
        scan = pii.scan_outbound(handoff, use_guardrail=False)
        state["pii"] = scan
        c.stage(KIND, "handoff", status="done", summary=summary, bytes=len(handoff.encode()), keys=sorted(summary),
                note="구조화 JSON만 통과 — 외부 원문·transcript·도구 응답은 Writer 에 전달되지 않는다",
                piiScan=scan, plane="boundary")

        # ③ Writer 검색 — 별도 Lambda(WriterRole, 격리 서브넷) invoke #1: 내부 문서 검색만.
        c.stage(KIND, "writer_search", status="running", plane="internal", role=ROLE_WRITER)
        ws = invoke_fn(env["WRITER_FN"], {"phase": "search", "summary": summary, "traceId": c.trace_id}, "Writer")
        docs = ws.get("internalDocs") or []
        state["docs"] = len(docs)
        c.stage(KIND, "writer_search", status="done", internalDocs=docs, searchQueries=ws.get("searchQueries") or [],
                searchError=ws.get("searchError", ""), plane="internal", role=ws.get("role", ROLE_WRITER))

        # ④ Writer 생성 — invoke #2 (워커 스레드). Writer 는 토큰을 STREAM_TABLE 항목에 릴레이하고, 이 스레드는 그 항목을
        #    폴링해 report.token 으로 즉시 전달한다. 릴레이 테이블이 없으면 단발 생성 → 단일 이벤트 (그렇게 표기).
        table = env["STREAM_TABLE"]
        key = relay_key(c) if table else ""
        target = {"key": key} if table else {}
        c.stage(KIND, "writer_generate", status="running", plane="cloud", role=ws.get("role", ROLE_WRITER),
                delivery="stream" if target else "single_event",
                note="Writer 가 converse_stream 토큰을 DynamoDB 릴레이에 쓰고 WsFn 이 폴링해 전달" if target
                else "STREAM_TABLE 미설정 — 단발 생성 후 단일 이벤트")
        t_gen = time.time()
        sent, events, relay_err, first_ms = 0, 0, "", None
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(invoke_fn, env["WRITER_FN"],
                            {"phase": "generate", "summary": summary, "internalDocs": docs,
                             "searchNote": ws.get("searchNote", ""), "audience": audience,
                             "traceId": c.trace_id, "stream": target}, "Writer")
            ddb = _ddb_client() if target else None

            def pump() -> bool:
                """릴레이에서 새 조각을 읽어 전달. 읽기 실패 시 False (폴링 중단, 잔여분은 Writer 결과로 보낸다)."""
                nonlocal sent, events, relay_err, first_ms
                try:
                    text = relay_read(ddb, table, key)
                except Exception as e:
                    resp = getattr(e, "response", None) or {}
                    code = ((resp.get("Error") or {}).get("Code") if isinstance(resp, dict) else "") or type(e).__name__
                    relay_err = f"{code}: {str(e)[:200]}"
                    log_event("report.relay_read_failed", c.trace_id, code=code, sent=sent)
                    return False
                if len(text) > sent:
                    c.token(KIND, text[sent:])
                    events += 1
                    sent = len(text)
                    if first_ms is None:
                        first_ms = int((time.time() - t_gen) * 1000)
                return True

            polling = bool(target)
            while polling and not fut.done():
                polling = pump()
                if polling and not fut.done():
                    time.sleep(STREAM_POLL_S)
            wg = fut.result()          # Writer 오류는 여기서 RuntimeError 로 — 성공처럼 보이지 않는다
            if polling:
                pump()                 # Writer 종료 직후 마지막 조각 회수
        state["writer"] = {"search": ws, "generate": wg}
        report = str(wg.get("report", ""))
        sent = min(sent, len(report))
        if sent < len(report):
            # 미전송 잔여분 — 릴레이 쓰기/읽기 실패 또는 릴레이 미설정 시 단일 이벤트 폴백. delivery 에 그대로 남긴다.
            c.token(KIND, report[sent:])
        delivery = "stream" if report and events and sent >= len(report) else ("partial_fallback" if sent else "single_event")
        state["delivery"] = delivery
        if wg.get("streamTable") and table and wg.get("streamTable") != table:
            log_event("report.relay_table_mismatch", c.trace_id, wsfn=table, writer=str(wg.get("streamTable"))[:80])
        c.stage(KIND, "writer_generate", status="done", chars=len(report), usage=wg.get("usage") or {},
                model=wg.get("model", ""), delivery=delivery, streamedChars=sent, tokenEvents=events,
                firstTokenMs=first_ms, writerStreamedChars=wg.get("streamedChars", 0), writerEvents=wg.get("tokenEvents", 0),
                writerFirstTokenMs=wg.get("firstTokenMs"), streamError=wg.get("streamError", ""), relayError=relay_err,
                plane="cloud", role=wg.get("role", ROLE_WRITER))
        c.done(KIND, report=report, summary=summary, deniedAttempts=len([d for d in denied if d.get("error")]),
               deniedList=denied, injectionDetected=state["injection"], internalDocs=docs,
               readerUsage=rd.get("usage") or {}, writerUsage=wg.get("usage") or {}, url=url, delivery=delivery,
               roles={"reader": rd.get("role", ROLE_READER), "writer": wg.get("role", ROLE_WRITER)},
               piiOutbound=scan["count"], deployed=_deployed(env))

    res = costguard.guarded(ctx, KIND, url, run)
    ru = state["reader"].get("usage") or {}
    wu = (state["writer"].get("generate") or {}).get("usage") or {}
    tin = int(ru.get("inputTokens", 0) or 0) + int(wu.get("inputTokens", 0) or 0)
    tout = int(ru.get("outputTokens", 0) or 0) + int(wu.get("outputTokens", 0) or 0)
    costguard.add_usage(tin + tout)
    denied_n = len([d for d in state["denied"] if d.get("error")])
    tracing.record_trace({"traceId": ctx.trace_id, "scenario": "F7", "email": ctx.email, "query": url,
                          "blocked": False, "piiOutbound": state["pii"]["count"], "maskedFields": [],
                          "piiDetectors": state["pii"].get("detectors", []),
                          "tokensIn": tin, "tokensOut": tout, "cached": res["cached"], "plane": "cloud",
                          "deniedAttempts": denied_n, "toolCalls": len(state["reader"].get("toolCalls") or []),
                          "injectionDetected": state["injection"], "internalDocs": state["docs"],
                          "delivery": state["delivery"],
                          "readerRole": "no-internal-tool", "elapsedMs": ctx.elapsed_ms()})
    log_event("report.done", ctx.trace_id, denied=denied_n, injection=state["injection"], docs=state["docs"],
              delivery=state["delivery"], cached=res["cached"], ms=ctx.elapsed_ms())


def handle_report_sample(ctx: Ctx, body: dict) -> None:
    env = _env()
    ctx.post({"type": "report_sample", "path": SAMPLE_PATH, "url": default_url() or None,
              "injectedInstruction": INJECTED_INSTRUCTION,
              "placements": ["본문 하단 회색 소형 텍스트", "display:none 블록", "HTML 주석"],
              "deployed": _deployed(env), "roles": {"reader": ROLE_READER, "writer": ROLE_WRITER},
              "steps": STEPS})


ROUTES = {"report": handle_report, "report_sample": handle_report_sample}
