"""bank-platform 시나리오 에이전트 컨테이너 — Strands Agents on AgentCore Runtime.

HTTP 8080: POST /invocations (SSE 스트림) · GET /ping — bedrock_agentcore.runtime.BedrockAgentCoreApp 이 제공.

요청 payload: {"agent": "<name>", "prompt": "<text>", "sessionId"?: str, "model"?: str}
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
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator, Optional

HERE = Path(__file__).resolve().parent
for cand in (HERE, HERE / "_ctx"):  # 컨테이너: /app/agent_specs.py · 로컬 실행: agents/_ctx/agent_specs.py
    if (cand / "agent_specs.py").exists() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

import agent_specs  # noqa: E402
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
ALLOWED_MODELS = {m for m in (agent_specs.DEFAULT_MODEL, agent_specs.QUALITY_MODEL, os.environ.get("GEN_MODEL", "")) if m}

app = BedrockAgentCoreApp()

# ---------------- 세션 이력 (sessionId → messages), LRU 20 ----------------
_sessions: "OrderedDict[str, list]" = OrderedDict()
_sessions_lock = threading.Lock()


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


def _new_session_id() -> str:
    return uuid.uuid4().hex + "-" + uuid.uuid4().hex[:8]  # 41자 (AgentCore runtimeSessionId 최소 33자)


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e)[:300]}"


class _Session:
    """요청 1건의 에이전트 묶음 — 별도 스레드에서 만든다 (MCP start 는 블로킹)."""

    def __init__(self, spec: dict, model_id: str, sid: str):
        from strands import Agent

        self.gate = BoundaryGateHook()
        self.system_prompt, self.skills_loaded, self.skills_missing = build_system_prompt(spec)
        sys_hits = scan_rules(self.system_prompt)
        if sys_hits:  # 시스템 프롬프트/스킬 자체가 식별자 규칙에 걸리면 배포 결함 — 즉시 실패
            raise RuntimeError("system prompt/skills contain identifier-rule hits: " + ",".join(sorted({h["type"] for h in sys_hits})))
        self.client, self.tools, self.discovered = mcp_gateway.open_tools(spec.get("allowedTools") or [])
        self.agent = Agent(model=build_model(model_id), system_prompt=self.system_prompt, tools=self.tools,
                           hooks=[self.gate], callback_handler=None, messages=_session_get(sid),
                           name=spec["name"], description=spec.get("description"))

    def close(self) -> None:
        try:
            self.client.stop(None, None, None)
        except Exception as e:  # noqa: BLE001
            log.warning("mcp client stop failed: %s", _err(e))


async def run(payload: Any, runtime_session_id: Optional[str] = None) -> AsyncIterator[dict]:
    started = time.time()
    payload = payload if isinstance(payload, dict) else {}
    name = str(payload.get("agent") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()[:MAX_PROMPT_CHARS]
    sid = str(payload.get("sessionId") or runtime_session_id or _new_session_id())[:256]
    spec = agent_specs.spec_by_name(name) if name else None
    model_id = str(payload.get("model") or (spec or {}).get("model") or agent_specs.DEFAULT_MODEL)

    meta: dict[str, Any] = {"type": "meta", "usage": {"inputTokens": 0, "outputTokens": 0}, "modelId": model_id,
                            "stopReason": "", "sessionId": sid, "runtime": RUNTIME_LABEL, "agent": name,
                            "toolCalls": 0, "toolNames": [], "boundary": None, "elapsedMs": 0}

    if spec is None:
        yield {"type": "error", "code": 404, "message": f"unknown agent: {name or '(empty)'}",
               "available": [s["name"] for s in agent_specs.SCENARIO_AGENTS]}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return
    if spec.get("mode") == "design_loop":
        # 디자인 스튜디오: Strands 대화 루프 대신 design_loop(유계 루프)를 스레드에서 돌리고 이벤트를 그대로 흘린다
        if model_id not in ALLOWED_MODELS and model_id != spec.get("model"):
            yield {"type": "error", "code": 400, "message": f"model not allowed: {model_id}", "allowed": sorted(ALLOWED_MODELS)}
            meta["stopReason"] = "error"
            yield meta
            return
        async for ev in _run_design(payload, model_id, meta, started):
            yield ev
        return
    if not prompt:
        yield {"type": "error", "code": 400, "message": "prompt is required"}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return
    if model_id not in ALLOWED_MODELS and model_id != spec.get("model"):
        yield {"type": "error", "code": 400, "message": f"model not allowed: {model_id}", "allowed": sorted(ALLOWED_MODELS)}
        meta["stopReason"] = "error"
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        yield meta
        return

    log.info("invoke agent=%s promptChars=%d sid=%s model=%s", name, len(prompt), sid[:12] + "…", model_id)
    session: Optional[_Session] = None
    try:
        try:
            session = await asyncio.to_thread(_Session, spec, model_id, sid)
        except Exception as e:  # noqa: BLE001 — Gateway/MCP/자격증명 실패는 그대로 보고한다 (도구 결과를 흉내내지 않음)
            log.error("session build failed agent=%s: %s", name, _err(e))
            yield {"type": "error", "code": 502, "message": "agent setup failed — " + _err(e)}
            meta["stopReason"] = "error"
            return
        meta["toolNames"] = [t.tool_name for t in session.tools]
        meta["gatewayTools"] = len(session.discovered)
        meta["skills"] = {"loaded": session.skills_loaded, "missing": session.skills_missing}
        if session.skills_missing:
            yield {"type": "error", "code": 500, "message": "skills missing in image: " + ",".join(session.skills_missing)}
        if spec.get("allowedTools") and not session.tools:
            yield {"type": "error", "code": 502,
                   "message": f"no Gateway tools matched allowedTools {spec.get('allowedTools')} (discovered {len(session.discovered)})"}

        gate = session.gate
        agent = session.agent
        seen_tool_use: set[str] = set()
        tool_name_by_id: dict[str, str] = {}
        stop_reason = ""
        text_chars = 0
        refused = False
        try:
            async for ev in agent.stream_async(prompt):
                for m in gate.drain():
                    yield {"type": "boundary", "chars": m["chars"], "estTokens": m["estTokens"],
                           "piiRules": m["piiRules"], "seq": m.get("seq"), "messages": m.get("messages")}
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
                                   "input": json.dumps(tu.get("input"), ensure_ascii=False, default=str)[:2000]}
                        elif "toolResult" in block:
                            tr = block["toolResult"]
                            tid = str(tr.get("toolUseId") or "")
                            size = len(json.dumps(tr.get("content"), ensure_ascii=False, default=str))
                            yield {"type": "tool_result", "name": tool_name_by_id.get(tid, ""), "toolUseId": tid,
                                   "chars": size, "status": tr.get("status", "")}
                elif "result" in ev:
                    res = ev["result"]
                    stop_reason = str(getattr(res, "stop_reason", "") or "")
                    metrics = getattr(res, "metrics", None)
                    usage = dict(getattr(metrics, "accumulated_usage", {}) or {})
                    meta["usage"] = {"inputTokens": int(usage.get("inputTokens", 0) or 0),
                                     "outputTokens": int(usage.get("outputTokens", 0) or 0)}
        except Exception as e:  # noqa: BLE001
            gr = find_gate_refusal(e)
            for m in gate.drain():
                yield {"type": "boundary", "chars": m["chars"], "estTokens": m["estTokens"],
                       "piiRules": m["piiRules"], "seq": m.get("seq"), "messages": m.get("messages"),
                       "refusedTypes": m.get("hits") if m.get("piiRules") else []}
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
                   "piiRules": m["piiRules"], "seq": m.get("seq"), "messages": m.get("messages")}
        meta["stopReason"] = stop_reason
        meta["boundary"] = gate.summary()
        meta["textChars"] = text_chars
        if refused:
            _session_drop(sid)  # 식별자가 든 턴은 이력에 남기지 않는다
        else:
            _session_put(sid, agent.messages)
    finally:
        if session is not None:
            await asyncio.to_thread(session.close)
        meta["elapsedMs"] = int((time.time() - started) * 1000)
        log.info("done agent=%s stop=%s toolCalls=%d in=%s out=%s ms=%d", name, meta["stopReason"], meta["toolCalls"],
                 meta["usage"]["inputTokens"], meta["usage"]["outputTokens"], meta["elapsedMs"])
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

    def emit(ev: dict) -> None:
        if ev.get("type") == "token":
            loop.call_soon_threadsafe(q.put_nowait, {"type": "text", "t": ev.get("text", "")})
        else:
            loop.call_soon_threadsafe(q.put_nowait, ev)

    def work() -> None:
        try:
            res = loop_run(design["productSpec"], design["smModel"], list(design.get("checklists") or []), deps, emit=emit,
                           output_type=str(design.get("outputType") or "design"))
            loop.call_soon_threadsafe(q.put_nowait, {"type": "design_done", "result": res})
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "code": 500, "message": _err(e)})
        finally:
            loop.call_soon_threadsafe(q.put_nowait, DONE)

    log.info("design_loop start spec=%s model=%s", str(design["productSpec"].get("id")), model_id)
    fut = loop.run_in_executor(None, work)
    stop = "end_turn"
    while True:
        ev = await q.get()
        if ev is DONE:
            break
        if ev.get("type") == "error":
            stop = "error"
        yield ev
    await fut
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
    async for ev in run(payload, getattr(context, "session_id", None)):
        yield ev


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))
