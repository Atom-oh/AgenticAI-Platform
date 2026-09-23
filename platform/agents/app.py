"""bank-platform 시나리오 에이전트 컨테이너 — Strands Agents on AgentCore Runtime.

HTTP 8080: POST /invocations (SSE 스트림) · GET /ping — bedrock_agentcore.runtime.BedrockAgentCoreApp 이 제공.

요청 payload: {"agent": "<name>", "prompt": "<text>", "model"?: str}
  - 세션은 SDK context만 사용한다. 기존 payload sessionId는 무시하고 boolean 메타데이터로만 보고한다.
  - agent: agent_specs.SCENARIO_AGENTS 의 name (regulation_impact_agent 등). 모르는 이름 → {type:'error', code:404}.
  - 도구: AgentCore Gateway(MCP, IAM 인바운드) 도구 중 spec.allowedTools 만.
  - 익명화 게이트: BoundaryGateHook 이 모델 호출 직전에 나가는 메시지를 스캔한다 (히트 → 모델 호출 없음).
응답 이벤트(SSE 'data: {...}' 한 줄씩):
  {type:'text', t} · {type:'tool_start', name, toolUseId} · {type:'tool_input', name, toolUseId, input}
  {type:'tool_result', name, toolUseId, chars, status}  (값 아닌 크기만) · {type:'boundary', chars, estTokens, piiRules, seq}
  {type:'error', message, code?} · 마지막 {type:'meta', usage:{inputTokens, outputTokens}, modelId, stopReason, sessionId, runtime}
세션: AgentCore Runtime 이 runtimeSessionId 별 microVM 을 유지한다. 이 프로세스는 sessionId 별 대화 이력을 최대 20개까지
인메모리로 보관해 멀티턴을 지원한다 (게이트 거부가 난 턴은 이력에 남기지 않는다).
로그: 프롬프트·응답 원문은 남기지 않는다 — 길이·도구 이름·토큰 수만 (SPEC §12.5).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from asyncio import CancelledError, create_task, shield
from collections import OrderedDict
from contextlib import aclosing
from pathlib import Path
from typing import Any, AsyncIterator, Optional

HERE = Path(__file__).resolve().parent
for cand in (HERE, HERE / "_ctx"):  # 컨테이너: /app/agent_specs.py · 로컬 실행: agents/_ctx/agent_specs.py
    if (cand / "agent_specs.py").exists() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

import agent_specs  # noqa: E402
from model_catalog import MODEL_IDS  # noqa: E402
from boundary_gate import BoundaryGateHook, find_gate_refusal, scan_rules  # noqa: E402

import mcp_gateway  # noqa: E402

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("agents.app")

RUNTIME_LABEL = "agentcore-runtime/strands"
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "").strip()
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "").strip() or "DRAFT"
MAX_PROMPT_CHARS = int(os.environ.get("MAX_PROMPT_CHARS", "8000"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
MAX_SESSIONS = 20
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR") or next(
    (str(p) for p in (HERE / "skills", HERE / "_ctx" / "skills") if p.is_dir()), str(HERE / "skills")))
ALLOWED_MODELS = frozenset(MODEL_IDS)

app = BedrockAgentCoreApp()

# ---------------- 세션 이력 (sessionId → messages), LRU 20 ----------------
_sessions: "OrderedDict[str, list]" = OrderedDict()
_sessions_lock = threading.Lock()
_active_sessions: set[str] = set()
_quarantined_sessions: set[str] = set()
_quarantine_exhausted = False


def _quarantine(sid):
    global _quarantine_exhausted
    with _sessions_lock:
        if sid in _quarantined_sessions:
            return
        if len(_quarantined_sessions) >= MAX_SESSIONS:
            _quarantine_exhausted = True
        else:
            _quarantined_sessions.add(sid)


class _SessionCleanupFailed(RuntimeError):
    pass


class _DesignStopped(RuntimeError):
    pass


async def _finish_task(task, *, propagate_cancel=True):
    """Keep ownership until a thread-backed task really finishes."""
    cancelled = False
    while not task.done():
        try:
            await shield(task)
        except CancelledError:
            cancelled = True
    result = task.result()
    if cancelled and propagate_cancel:
        raise CancelledError
    return result


def _session_get(sid: str) -> list:
    with _sessions_lock:
        msgs = _sessions.get(sid)
        if msgs is not None:
            _sessions.move_to_end(sid)
        return list(msgs or [])


def _session_put(sid: str, messages: list) -> None:
    with _sessions_lock:
        _sessions[sid] = list(messages)
        _sessions.move_to_end(sid)
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)


def _session_drop(sid: str) -> None:
    with _sessions_lock:
        _sessions.pop(sid, None)


# ---------------- 명세 → 시스템 프롬프트 / 모델 ----------------
def skill_text(name: str) -> Optional[str]:
    p = SKILLS_DIR / f"{name}.md"
    if not p.is_file():
        p2 = SKILLS_DIR / name / "SKILL.md"
        p = p2 if p2.is_file() else p
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8")


def build_system_prompt(spec: dict) -> tuple[str, list[str], list[str]]:
    """spec.systemPrompt + '\\n\\n' + [SKILL <name>] 본문들. 반환 (prompt, loaded_skills, missing_skills)."""
    parts = [str(spec.get("systemPrompt", "")).strip()]
    loaded, missing = [], []
    for name in spec.get("skills") or []:
        body = skill_text(str(name))
        if body is None:
            missing.append(str(name))
            continue
        loaded.append(str(name))
        parts.append(f"[SKILL {name}]\n{body.strip()}")
    return "\n\n".join(p for p in parts if p), loaded, missing


def build_model(model_id: str):
    if not GUARDRAIL_ID:
        raise RuntimeError("A configured bank Guardrail is required")
    from strands.models import BedrockModel

    # Claude 5 는 `temperature` 를 거부한다 (ConverseStream ValidationException: "`temperature` is deprecated for this model")
    # → 넘기지 않는다. 필요하면 TEMPERATURE 환경변수로 구형 모델에만 켠다.
    kw: dict[str, Any] = {"model_id": model_id, "region_name": REGION, "streaming": True, "max_tokens": MAX_TOKENS}
    if os.environ.get("TEMPERATURE"):
        kw["temperature"] = float(os.environ["TEMPERATURE"])
    if GUARDRAIL_ID:
        kw.update({"guardrail_id": GUARDRAIL_ID, "guardrail_version": GUARDRAIL_VERSION,
                   "guardrail_trace": "enabled"})
    return BedrockModel(**kw)


def _err(e: BaseException) -> str:
    return type(e).__name__


class _Session:
    """요청 1건의 에이전트 묶음 — 별도 스레드에서 만든다 (MCP start 는 블로킹)."""

    def __init__(self, spec: dict, model_id: str, sid: str):
        from strands import Agent

        self.sid = sid
        self.gate = BoundaryGateHook()
        self.system_prompt, self.skills_loaded, self.skills_missing = build_system_prompt(spec)
        sys_hits = scan_rules(self.system_prompt)
        if sys_hits:  # 시스템 프롬프트/스킬 자체가 식별자 규칙에 걸리면 배포 결함 — 즉시 실패
            raise RuntimeError("system prompt/skills contain identifier-rule hits: " + ",".join(sorted({h["type"] for h in sys_hits})))
        try:
            self.client, self.tools, self.discovered = mcp_gateway.open_tools(spec.get("allowedTools") or [])
        except mcp_gateway.GatewayCleanupFailed:
            _quarantine(sid)
            raise _SessionCleanupFailed("Gateway setup cleanup failed") from None
        try:
            self.agent = Agent(model=build_model(model_id), system_prompt=self.system_prompt, tools=self.tools,
                               hooks=[self.gate], callback_handler=None, messages=_session_get(sid),
                               name=spec["name"], description=spec.get("description"))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        try:
            self.client.stop(None, None, None)
        except Exception as e:  # noqa: BLE001
            log.warning("mcp client stop failed: %s", _err(e))
            _quarantine(self.sid)
            raise _SessionCleanupFailed("Gateway cleanup failed") from None


async def _start_session(spec, model_id, sid):
    task = create_task(asyncio.to_thread(_Session, spec, model_id, sid))
    try:
        return await shield(task)
    except CancelledError:
        # A cancelled to_thread await does not stop its constructor.
        try:
            session = await _finish_task(task, propagate_cancel=False)
        except Exception:
            pass  # Constructor failures close their initialized client.
        else:
            try:
                await _finish_task(create_task(asyncio.to_thread(session.close)), propagate_cancel=False)
            except _SessionCleanupFailed:
                pass  # close() quarantines the session on failed cleanup.
        raise


async def run(payload: Any, runtime_session_id: Optional[str] = None) -> AsyncIterator[dict]:
    sid = runtime_session_id if isinstance(runtime_session_id, str) else ""
    claimed = False
    if 33 <= len(sid) <= 256:
        with _sessions_lock:
            quarantined = _quarantine_exhausted or sid in _quarantined_sessions
            if not quarantined and sid not in _active_sessions:
                _active_sessions.add(sid)
                claimed = True
        if not claimed:
            yield {"type": "error", "code": 503 if quarantined else 409,
                   "message": "Runtime session cleanup failed; use a new conversation" if quarantined
                   else "Runtime session is busy; retry this turn"}
            meta = {"type": "meta", "usage": {"inputTokens": 0, "outputTokens": 0},
                    "stopReason": "cleanup_failed" if quarantined else "session_busy",
                    "sessionId": sid, "runtime": RUNTIME_LABEL}
            if isinstance(payload, dict) and "sessionId" in payload:
                meta["ignoredPayloadSessionId"] = True
            yield meta
            return
    try:
        async with aclosing(_run(payload, runtime_session_id)) as stream:
            async for event in stream:
                yield event
    finally:
        if claimed:
            with _sessions_lock:
                _active_sessions.discard(sid)


async def _run(payload: Any, runtime_session_id: Optional[str] = None) -> AsyncIterator[dict]:
    started = time.time()
    payload = payload if isinstance(payload, dict) else {}
    try:
        # Bound the full request before constructing design prompts or queues.
        request_chars = 0
        for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(payload):
            request_chars += len(chunk)
            if request_chars > 100_000:
                break
    except (TypeError, ValueError, RecursionError):
        yield {"type": "error", "code": 400, "message": "Invalid Runtime request"}
        return
    if request_chars > 100_000:
        yield {"type": "error", "code": 413, "message": "Runtime request exceeds the admitted length"}
        return
    name = str(payload.get("agent") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    sid = runtime_session_id if isinstance(runtime_session_id, str) else ""
    spec = agent_specs.spec_by_name(name) if name else None
    if spec:
        try:
            matches = payload.get("approvedSourceHash") == agent_specs.source_hash(spec, SKILLS_DIR)
        except (OSError, ValueError):
            matches = False
        if not matches:
            yield {"type": "error", "code": 409, "message": "Runtime source changed after approval"}
            return
    model_id = str(payload.get("model") or (spec or {}).get("model") or agent_specs.DEFAULT_MODEL)

    meta: dict[str, Any] = {"type": "meta", "usage": {"inputTokens": 0, "outputTokens": 0}, "modelId": model_id,
                            "stopReason": "", "sessionId": sid, "runtime": RUNTIME_LABEL, "agent": name,
                            "toolCalls": 0, "toolNames": [], "boundary": None, "elapsedMs": 0}
    if "sessionId" in payload:
        meta["ignoredPayloadSessionId"] = True
    if not 33 <= len(sid) <= 256:
        yield {"type": "error", "code": 400, "message": "A verified Runtime session is required"}
        meta["stopReason"] = "error"
        yield meta
        return
    if len(prompt) > MAX_PROMPT_CHARS:
        yield {"type": "error", "code": 413, "message": "Prompt exceeds the admitted length"}
        yield {**meta, "stopReason": "error"}
        return

    if spec is None:
        yield {"type": "error", "code": 404, "message": f"unknown agent: {name or '(empty)'}",
               "available": [s["name"] for s in agent_specs.SCENARIO_AGENTS]}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return
    if spec.get("mode") == "design_loop":
        # 디자인 스튜디오: Strands 대화 루프 대신 design_loop(유계 루프)를 스레드에서 돌리고 이벤트를 그대로 흘린다
        if model_id not in ALLOWED_MODELS:
            yield {"type": "error", "code": 400, "message": f"model not allowed: {model_id}", "allowed": sorted(ALLOWED_MODELS)}
            meta["stopReason"] = "error"
            yield meta
            return
        async with aclosing(_run_design(payload, model_id, meta, started)) as stream:
            async for ev in stream:
                yield ev
        return
    if not prompt:
        yield {"type": "error", "code": 400, "message": "prompt is required"}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return
    if model_id not in ALLOWED_MODELS:
        yield {"type": "error", "code": 400, "message": f"model not allowed: {model_id}", "allowed": sorted(ALLOWED_MODELS)}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return

    log.info("invoke agent=%s promptChars=%d sid=%s model=%s", name, len(prompt), sid[:12] + "…", model_id)
    session: Optional[_Session] = None
    aborted = False
    try:
        try:
            session = await _start_session(spec, model_id, sid)
        except Exception as e:  # noqa: BLE001 — Gateway/MCP/자격증명 실패는 그대로 보고한다 (도구 결과를 흉내내지 않음)
            log.error("session build failed agent=%s: %s", name, _err(e))
            yield {"type": "error", "code": 502, "message": "agent setup failed — " + _err(e)}
            meta["stopReason"] = "error"
            return
        meta["toolNames"] = [t.tool_name for t in session.tools]
        meta["toolsMissing"] = mcp_gateway.missing_tool_names(session.discovered, spec.get("allowedTools"))
        meta["gatewayTools"] = len(session.discovered)
        meta["skills"] = {"loaded": session.skills_loaded, "missing": session.skills_missing}
        if session.skills_missing:
            yield {"type": "error", "code": 500, "message": "skills missing in image: " + ",".join(session.skills_missing)}
            meta["stopReason"] = "skills_unavailable"
            return
        if meta["toolsMissing"]:
            yield {"type": "error", "code": 502,
                   "message": "Configured Gateway tools are unavailable"}
            meta["stopReason"] = "tools_unavailable"
            return

        gate = session.gate
        agent = session.agent
        seen_tool_use: set[str] = set()
        tool_name_by_id: dict[str, str] = {}
        stop_reason = ""
        text_chars = 0
        refused = False
        completed = False
        try:
            async for ev in agent.stream_async(prompt):
                for m in gate.drain():
                    yield {"type": "boundary", "chars": m["chars"], "estTokens": m["estTokens"],
                           "piiRules": m["piiRules"], "piiCount": m.get("piiCount", m["piiRules"]),
                           "piiDetectors": m.get("piiDetectors", ["rules"]),
                           "seq": m.get("seq"), "messages": m.get("messages")}
                if not isinstance(ev, dict):
                    continue
                if "data" in ev and isinstance(ev["data"], str):
                    text_chars += len(ev["data"])
                    yield {"type": "text", "t": ev["data"]}
                elif "current_tool_use" in ev and isinstance(ev["current_tool_use"], dict):
                    tu = ev["current_tool_use"]
                    tid = str(tu.get("toolUseId") or "")
                    if tid and tid not in seen_tool_use:
                        seen_tool_use.add(tid)
                        tool_name_by_id[tid] = str(tu.get("name") or "")
                        meta["toolCalls"] += 1
                        yield {"type": "tool_start", "name": tool_name_by_id[tid], "toolUseId": tid}
                elif "message" in ev and isinstance(ev["message"], dict):
                    msg = ev["message"]
                    for block in msg.get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        if "toolUse" in block:
                            tu = block["toolUse"]
                            tid = str(tu.get("toolUseId") or "")
                            tool_name_by_id.setdefault(tid, str(tu.get("name") or ""))
                            if tid and tid not in seen_tool_use:
                                seen_tool_use.add(tid)
                                meta["toolCalls"] += 1
                                yield {"type": "tool_start", "name": tool_name_by_id[tid], "toolUseId": tid}
                            yield {"type": "tool_input", "name": tool_name_by_id.get(tid, ""), "toolUseId": tid,
                                   "chars": len(json.dumps(tu.get("input"), ensure_ascii=False, default=str)),
                                   "inputRedacted": True}
                        elif "toolResult" in block:
                            tr = block["toolResult"]
                            tid = str(tr.get("toolUseId") or "")
                            size = len(json.dumps(tr.get("content"), ensure_ascii=False, default=str))
                            yield {"type": "tool_result", "name": tool_name_by_id.get(tid, ""), "toolUseId": tid,
                                   "chars": size, "status": tr.get("status", "")}
                elif "result" in ev:
                    res = ev["result"]
                    stop_reason = str(getattr(res, "stop_reason", "") or "")
                    completed = bool(stop_reason)
                    metrics = getattr(res, "metrics", None)
                    usage = dict(getattr(metrics, "accumulated_usage", {}) or {})
                    meta["usage"] = {"inputTokens": int(usage.get("inputTokens", 0) or 0),
                                     "outputTokens": int(usage.get("outputTokens", 0) or 0)}
        except Exception as e:  # noqa: BLE001
            gr = find_gate_refusal(e)
            for m in gate.drain():
                yield {"type": "boundary", "chars": m["chars"], "estTokens": m["estTokens"],
                       "piiRules": m["piiRules"], "piiCount": m.get("piiCount", m["piiRules"]),
                       "piiDetectors": m.get("piiDetectors", ["rules"]),
                       "seq": m.get("seq"), "messages": m.get("messages"),
                       "refusedTypes": m.get("hits") if m.get("piiCount", m["piiRules"]) else []}
            if gr is not None:
                refused = True
                stop_reason = "gate_refused"
                log.warning("gate refused agent=%s types=%s", name, ",".join(gr.types))
                yield {"type": "error", "code": 422, "gate": "refused", "message": str(gr), "types": gr.types}
            else:
                stop_reason = "error"
                log.error("stream failed agent=%s: %s", name, _err(e))
                yield {"type": "error", "code": 500, "message": _err(e)}
        for m in gate.drain():
            yield {"type": "boundary", "chars": m["chars"], "estTokens": m["estTokens"],
                   "piiRules": m["piiRules"], "piiCount": m.get("piiCount", m["piiRules"]),
                   "piiDetectors": m.get("piiDetectors", ["rules"]),
                   "seq": m.get("seq"), "messages": m.get("messages")}
        if not completed and stop_reason not in {"error", "gate_refused"}:
            stop_reason = "incomplete"
            meta["incomplete"] = True
            yield {"type": "error", "code": 502, "message": "Model stream ended without a terminal result"}
        meta["stopReason"] = stop_reason
        meta["boundary"] = gate.summary()
        meta["textChars"] = text_chars
        if refused:
            _session_drop(sid)  # 식별자가 든 턴은 이력에 남기지 않는다
        elif completed and stop_reason != "error":
            _session_put(sid, agent.messages)
    except (CancelledError, GeneratorExit):
        aborted = True
        raise
    finally:
        if session is not None:
            try:
                await _finish_task(create_task(asyncio.to_thread(session.close)))
            except _SessionCleanupFailed:
                meta.update(cleanupFailed=True, incomplete=True)
                if meta.get("stopReason") != "gate_refused":
                    meta["stopReason"] = "cleanup_failed"
                if not aborted:
                    yield {"type": "error", "code": 503, "message": "Runtime session cleanup failed"}
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        log.info("done agent=%s stop=%s toolCalls=%d in=%s out=%s ms=%d", name, meta["stopReason"], meta["toolCalls"],
                 meta["usage"]["inputTokens"], meta["usage"]["outputTokens"], meta["elapsedMs"])
        if not aborted:
            yield meta


async def _run_design(payload: dict, model_id: str, meta: dict, started: float) -> AsyncIterator[dict]:
    """design_flow_agent — payload["design"] = {productSpec, smModel, checklists[], outputType?}.
    이벤트: {type:'stage', step, ...} · {type:'text', t} · {type:'design_done', result} · 마지막 meta."""
    design = payload.get("design") if isinstance(payload.get("design"), dict) else None
    if not design or not design.get("productSpec") or not design.get("smModel"):
        yield {"type": "error", "code": 400, "message": "design.productSpec / design.smModel 이 필요합니다"}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return
    from design_loop import run as loop_run
    import design_deps as _dd

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    DONE = object()
    deps = _dd.make_deps(model_id)
    stopped = threading.Event()

    def guard(fn):
        def call(*args, **kwargs):
            if stopped.is_set():
                raise _DesignStopped
            return fn(*args, **kwargs)
        return call

    deps = {key: guard(value) if key in {"generate", "llm_judge"} else value
            for key, value in deps.items()}

    def emit(ev: dict) -> None:
        if stopped.is_set():
            return
        if ev.get("type") == "token":
            loop.call_soon_threadsafe(q.put_nowait, {"type": "text", "t": ev.get("text", "")})
        else:
            loop.call_soon_threadsafe(q.put_nowait, ev)

    def work() -> None:
        try:
            res = loop_run(design["productSpec"], design["smModel"], list(design.get("checklists") or []), deps, emit=emit,
                           output_type=str(design.get("outputType") or "design"))
            if not stopped.is_set():
                loop.call_soon_threadsafe(q.put_nowait, {"type": "design_done", "result": res})
        except _DesignStopped:
            pass
        except Exception as e:  # noqa: BLE001
            refusal = find_gate_refusal(e)
            loop.call_soon_threadsafe(q.put_nowait, {
                "type": "error", "code": 422 if refusal else 500, "message": _err(e),
                **({"gate": "refused", "types": refusal.types} if refusal else {})})
        finally:
            try:
                for event in deps.get("drain_boundary", lambda: [])():
                    emit(event)
            except Exception:
                loop.call_soon_threadsafe(q.put_nowait, {
                    "type": "error", "code": 500, "message": "Boundary evidence unavailable"})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, DONE)

    log.info("design_loop start model=%s", model_id)
    fut = loop.run_in_executor(None, work)
    stop = "end_turn"
    try:
        while True:
            ev = await q.get()
            if ev is DONE:
                break
            if ev.get("type") == "error":
                stop = "gate_refused" if ev.get("gate") == "refused" else "error"
            yield ev
    finally:
        stopped.set()
        await _finish_task(fut)
    meta["usage"] = {k: v for k, v in deps["usage"]().items() if k in ("inputTokens", "outputTokens")}
    meta["llmCalls"] = deps["usage"]().get("calls", 0)
    meta["stopReason"] = stop
    meta["elapsedMs"] = int((time.time() - started) * 1000)
    log.info("design_loop done stop=%s calls=%s in=%s out=%s ms=%d", stop, meta["llmCalls"],
             meta["usage"]["inputTokens"], meta["usage"]["outputTokens"], meta["elapsedMs"])
    yield meta


@app.entrypoint
async def invoke(payload, context):
    """AgentCore Runtime 엔트리포인트 — async generator → SSE."""
    async with aclosing(run(payload, getattr(context, "session_id", None))) as stream:
        async for ev in stream:
            yield ev


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))
