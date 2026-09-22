"""에이전트 빌더 — AgentCore Harness 기반 에이전트의 생성·카탈로그·승인·호출 (SPEC §7, §11-4).

요청/응답형(액션명 = 응답 type):
  agents_catalog   {}                                  → {agents[], tools[], skills[], models[], gateway, commonRules}
  agent_create     {name, title, description, model, systemPrompt, allowedTools[], skills[], memory}
                                                       → {ok, record, harness:{arn,status}, agentcoreRegistry} | {ok:false, error}
  agent_transition {name, version, to, reason}         → {ok, record, audit, agentcoreRegistry} | {ok:false, error, code}
  agent_get        {name, version?}                    → {ok, record, harness(요약 — executionRoleArn 제외), audit}
스트리밍형(kind = 'agent'):
  agent_invoke     {name, message, sessionId?, version?}
    agent.stage(gate | tool_start | tool_input | error) · agent.token · agent.done{usage, stopReason, sessionId, modelId, runtime}
    ★ Consumer 게이트: 플랫폼 Registry 상태가 APPROVED 가 아니면 Harness 를 호출하지 않고 agent.done(error) 로 끝낸다.

정직성 규칙: 사용자 경로는 생성/승인 요청만 기록하며 실제 제어 작업은 IAM AdminFn이 수행한다.
Runtime 훅 또는 Harness 입력 어댑터가 모델 호출 전에 검사한다. 수신한 경계 검사와 차단 상태를 기록한다.
boto3 클라이언트는 agentcore 모듈 안에서만 지연 생성된다. 로그에는 프롬프트·메시지 원문을 남기지 않는다 (§12.5).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from common import tracing
from common.ctx import Ctx
from common.log import log_event
from registry import api as registry_api
from registry.model import RegistryError, STATUSES
from engine import model_catalog

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
MODELS = list(model_catalog.MODEL_IDS)
RUNTIME = "AgentCore Harness"
SCENARIOS = ("S1", "S2", "S3", "F7")
MAX_PROMPT = 6000
MAX_MESSAGE = 4000
# Harness 상세에서 클라이언트로 내보내는 키 — executionRoleArn·clientToken 은 화이트리스트에 없다
HARNESS_KEYS = ("harnessId", "harnessName", "arn", "harnessArn", "status", "model", "tools", "skills",
                "allowedTools", "memory", "maxIterations", "maxTokens", "timeoutSeconds", "tags", "createdAt",
                "updatedAt")


# ---------- 지연 import (boto3 클라이언트는 agentcore 모듈 안에서만 만들어진다) ----------
def _harness():
    from agentcore import harness
    return harness


def _mirror():
    from agentcore import registry_mirror
    return registry_mirror


def _specs():
    from agentcore import agent_specs
    return agent_specs


def _tool_schema() -> List[dict]:
    import agentcore
    path = os.path.join(os.path.dirname(os.path.abspath(agentcore.__file__)), "tool_schema.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _err(e: BaseException) -> str:
    return type(e).__name__


def _harness_name(name: str) -> str:
    return f"bank_{name}"


def _stage_kw(data: Any) -> dict:
    """Harness 이벤트 페이로드 → ctx.stage 키워드. 예약 키(type/step/plane/reqId/traceId)는 덮어쓰지 못하게 뺀다."""
    d = dict(data) if isinstance(data, dict) else {}
    for k in ("type", "step", "plane", "reqId", "traceId"):
        d.pop(k, None)
    return d


def _harness_arn(h: Optional[dict]) -> Optional[str]:
    if not h:
        return None
    return h.get("arn") or h.get("harnessArn")


def _harness_summary(h: Optional[dict]) -> Optional[dict]:
    """Harness 상세 → 전송용 요약. executionRoleArn 등 화이트리스트 밖 키는 절대 포함하지 않는다."""
    if not h:
        return None
    out = {k: h[k] for k in HARNESS_KEYS if k in h}
    if "arn" not in out and out.get("harnessArn"):
        out["arn"] = out["harnessArn"]
    sp = out.get("systemPrompt")
    if isinstance(sp, list):
        out["systemPrompt"] = "".join(str(p.get("text", "")) for p in sp if isinstance(p, dict))
    return json.loads(json.dumps(out, default=str))


def _version_num(v: str) -> int:
    m = re.match(r"^v(\d+)$", str(v or ""))
    return int(m.group(1)) if m else 0


def _skill_names() -> List[str]:
    return sorted({r["name"] for r in registry_api.list_approved("SKILL")})


def _scenario_of(rec: dict, spec: Optional[dict]) -> str:
    payload = rec.get("payload") or {}
    if payload.get("scenario"):
        return str(payload["scenario"])
    if spec:
        return str(spec.get("scenario", "custom"))
    for t in rec.get("tags") or []:
        if str(t).upper() in SCENARIOS:
            return str(t).upper()
    return "custom"


def _find_agent_record(name: str, version: Optional[str] = None) -> Optional[dict]:
    """버전 미지정이면 APPROVED 최신 → 그 외 최신 순으로 고른다."""
    if version:
        rec = registry_api.get_record(name, version)
        return rec if rec and rec.get("recordType") == "AGENT" else None
    recs = [registry_api.strip(r) for r in registry_api.get_store().versions(name) if r.get("recordType") == "AGENT"]
    if not recs:
        return None
    recs.sort(key=lambda r: (r.get("status") == "APPROVED", _version_num(r.get("recordVersion"))), reverse=True)
    return recs[0]


def _resolve_harness_arn(rec: dict) -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    """Resolve bank_<name> and verify any stored ARN; return the live configuration."""
    payload = rec.get("payload") or {}
    try:
        h = _harness().find_harness(_harness_name(rec["name"]))
    except Exception as e:  # noqa: BLE001
        return None, None, _err(e)
    arn = _harness_arn(h)
    if payload.get("harnessArn") and payload["harnessArn"] != arn:
        return None, h, "Harness configuration differs from the approved record"
    return arn, h, None


# ---------- 카탈로그 ----------
def agents_catalog(ctx: Ctx, body: dict) -> None:
    specs = _specs()
    records = registry_api.list_records({"type": "AGENT"})

    # Harness 목록은 한 번만 읽어 인덱스를 만들고, 존재하는 것만 find_harness 로 상세를 가져온다 (없는 건 호출 0회)
    harness_index: Optional[Dict[str, dict]] = None
    harness_err: Optional[str] = None
    try:
        harness_index = {}
        for h in _harness().list_platform_harnesses():
            # find_harness 와 같은 매칭 규칙: harnessName 일치 또는 harnessId 가 "<name>-" 로 시작
            if h.get("harnessName"):
                harness_index[str(h["harnessName"])] = h
            hid = str(h.get("harnessId") or "")
            if "-" in hid:
                harness_index.setdefault(hid.rsplit("-", 1)[0], h)
    except Exception as e:  # noqa: BLE001
        harness_index, harness_err = None, _err(e)

    mirror_err: Optional[str] = None
    agents = []
    for rec in records:
        name, version = rec["name"], rec.get("recordVersion", "v1")
        payload = rec.get("payload") or {}
        spec = specs.spec_by_name(name)
        row: Dict[str, Any] = {
            "name": name, "version": version,
            "title": payload.get("title") or (spec or {}).get("title") or name,
            "description": rec.get("description", ""),
            "status": rec.get("status"), "allowedTargets": rec.get("allowedTargets", []),
            "agentcoreStatus": None,
            "harnessArn": payload.get("harnessArn"), "harnessStatus": "unknown",
            "model": payload.get("model") or (spec or {}).get("model"),
            "allowedTools": payload.get("allowedTools") or (spec or {}).get("allowedTools") or [],
            "skills": payload.get("skills") or [],
            "memory": bool(payload.get("memory", (spec or {}).get("memory", False))),
            "scenario": _scenario_of(rec, spec),
            "runtime": payload.get("runtime") or ("AgentCore Harness" if spec else payload.get("entry") or "—"),
            "createdBy": payload.get("createdBy") or rec.get("updatedBy"),
            "updatedAt": rec.get("updatedAt"), "subtype": rec.get("subtype") or "",
        }
        # Harness 상태
        if harness_index is not None:
            hname = _harness_name(name)
            if hname in harness_index:
                try:
                    h = _harness().find_harness(hname)
                    row["harnessStatus"] = (h or {}).get("status") or "unknown"
                    row["harnessArn"] = row["harnessArn"] or _harness_arn(h)
                except Exception as e:  # noqa: BLE001
                    row["harnessStatus"], row["harnessError"] = "unknown", _err(e)
            else:
                row["harnessStatus"] = "none"
        else:
            row["harnessError"] = harness_err
        # AgentCore Registry 상태 (도달 불가면 null — 첫 실패 후엔 재시도하지 않는다)
        if mirror_err is None:
            try:
                m = _mirror().find_record(name, version)
                row["agentcoreStatus"] = (m or {}).get("status")
            except Exception as e:  # noqa: BLE001
                mirror_err = _err(e)
        agents.append(row)

    tools = [{"name": t["name"], "description": t.get("description", "")} for t in _tool_schema()]
    skills = [{"name": r["name"], "version": r.get("recordVersion"), "status": r.get("status")}
              for r in registry_api.list_approved("SKILL")]
    log_event("agents.catalog", ctx.trace_id, agents=len(agents), tools=len(tools), skills=len(skills),
              harnessListed=harness_index is not None, agentcoreRegistry=mirror_err is None)
    ctx.post({"type": "agents_catalog", "agents": agents, "tools": tools, "skills": skills, "models": MODELS,
              "modelOptions": model_catalog.options(),
              "gateway": {"arn": os.environ.get("GATEWAY_ARN", ""), "url": os.environ.get("GATEWAY_URL", "")},
              "commonRules": specs.COMMON_RULES, "defaultModel": specs.DEFAULT_MODEL,
              "harnessError": harness_err, "agentcoreRegistryError": mirror_err,
              "registryBackend": registry_api.backend()})


# ---------- 생성 ----------
def _validate_create(body: dict) -> Tuple[Optional[dict], Optional[str]]:
    name = str(body.get("name", "") or "").strip()
    if not NAME_RE.match(name):
        return None, "이름은 영문 소문자로 시작하는 snake_case 3~41자여야 합니다 (^[a-z][a-z0-9_]{2,40}$)."
    title = str(body.get("title", "") or "").strip()[:120] or name
    description = str(body.get("description", "") or "").strip()[:2000]
    model = str(body.get("model", "") or "").strip() or _specs().DEFAULT_MODEL
    if model not in MODELS:
        return None, f"지원하지 않는 모델: {model} (가능: {', '.join(MODELS)})"
    system_prompt = str(body.get("systemPrompt", "") or "").strip()
    if not system_prompt:
        return None, "시스템 프롬프트가 비어 있습니다."
    if len(system_prompt) > MAX_PROMPT:
        return None, f"시스템 프롬프트는 {MAX_PROMPT}자 이하여야 합니다."
    if body.get("memory"):
        return None, "장기 Memory는 별도 개인정보 검토 후 사용할 수 있습니다."
    tools_in = body.get("allowedTools") or []
    skills_in = body.get("skills") or []
    if not isinstance(tools_in, list) or not isinstance(skills_in, list):
        return None, "allowedTools / skills 는 문자열 배열이어야 합니다."
    if not tools_in:
        return None, "허용할 Gateway 도구를 한 개 이상 선택하세요."
    tools_in = [str(t) for t in tools_in]
    skills_in = [str(s) for s in skills_in]
    known_tools = {t["name"] for t in _tool_schema()}
    bad_tools = [t for t in tools_in if t not in known_tools]
    if bad_tools:
        return None, f"알 수 없는 도구: {', '.join(bad_tools)} (Gateway tool_schema 에 없음)"
    known_skills = set(_skill_names())
    bad_skills = [s for s in skills_in if s not in known_skills]
    if bad_skills:
        return None, f"승인된 Registry SKILL이 아닙니다: {', '.join(bad_skills)}"
    from agentcore import skill_binding
    try:
        selected_skills = list(dict.fromkeys(skills_in))
        bindings = body.get("skillBindings")
        if bindings is None:
            bindings = skill_binding.capture(selected_skills)
        skill_binding.resolve(bindings, selected_skills)
    except (RegistryError, OSError, UnicodeError, ValueError, TypeError) as e:
        return None, f"SKILL 승인 자료를 확인하지 못했습니다: {type(e).__name__}"
    return {"name": name, "title": title, "description": description or title, "model": model,
            "systemPrompt": system_prompt, "allowedTools": list(dict.fromkeys(tools_in)),
            "skills": selected_skills, "skillBindings": bindings, "memory": bool(body.get("memory", False)),
            "scenario": "custom"}, None


def agent_create(ctx: Ctx, body: dict) -> None:
    # Bindings are server-derived; the browser cannot select a different source.
    spec, err = _validate_create({key: value for key, value in body.items() if key != "skillBindings"})
    if err or spec is None:
        ctx.post({"type": "agent_create", "ok": False, "code": 400, "error": err})
        return
    spec["createdBy"] = ctx.email
    name, version = spec["name"], "v1"
    if registry_api.get_record(name, version):
        ctx.post({"type": "agent_create", "ok": False, "code": 409,
                  "error": f"이미 존재하는 에이전트 이름입니다: {name} — Harness 이름(bank_{name})이 겹치므로 다른 이름을 사용하세요."})
        return

    payload = {"model": spec["model"],
               "allowedTools": spec["allowedTools"], "skills": spec["skills"], "memory": spec["memory"],
               "skillBindings": spec["skillBindings"],
               "runtime": RUNTIME, "systemPrompt": spec["systemPrompt"], "title": spec["title"],
               "scenario": "custom", "createdBy": ctx.email, "administration": "IAM_ONLY"}
    try:
        registry_api.create_record({"name": name, "recordVersion": version, "recordType": "AGENT", "subtype": "HARNESS",
                                    "description": spec["description"], "owner": str(ctx.email)[:80],
                                    "tags": ["custom", "harness", "builder"], "payload": payload}, actor=ctx.email, embed=False)
        rec, ev = registry_api.transition(name, version, "PENDING_APPROVAL", ctx.email, "빌더 생성 — 승인 대기")
    except RegistryError as e:
        ctx.post({"type": "agent_create", "ok": False, "code": getattr(e, "code", 400), "stage": "registry",
                  "error": str(e)[:300], "errorType": type(e).__name__})
        return
    log_event("agent.requested", ctx.trace_id, name=name, version=version, model=spec["model"],
              tools=len(spec["allowedTools"]), skills=len(spec["skills"]), memory=spec["memory"],
              promptLen=len(spec["systemPrompt"]), email=ctx.email)
    ctx.post({"type": "agent_create", "ok": True, "record": registry_api.public_record(rec),
              "harness": {"arn": None, "status": "PENDING_ADMIN", "reused": False},
              "agentcoreRegistry": {"status": "PENDING_ADMIN"}, "audit": ev,
              "message": "명세를 저장했습니다. 승인 요청 후 IAM 관리자가 실행 환경을 준비합니다."})


# ---------- 전이 ----------
def agent_transition(ctx: Ctx, body: dict) -> None:
    name = str(body.get("name", "") or "").strip()
    version = str(body.get("version", "") or body.get("recordVersion", "") or "v1").strip()
    to = str(body.get("to", "") or "").strip().upper()
    reason = str(body.get("reason", "") or "").strip()[:500]
    try:
        from agentcore.administration import request_transition
        result = request_transition(name, version, to, ctx.email, reason)
    except RegistryError as e:
        log_event("agent.transition_rejected", ctx.trace_id, name=name, version=version, to=to if to in STATUSES else "invalid",
                  code=getattr(e, "code", 400), errorType=type(e).__name__)
        ctx.post({"type": "agent_transition", "ok": False, "error": str(e)[:300], "code": getattr(e, "code", 400),
                  "errorType": type(e).__name__, "name": name, "version": version, "to": to})
        return
    ctx.post({"type": "agent_transition", "ok": True, **result,
              "auditTrail": registry_api.public_agent_audit(registry_api.audit_trail(name, version))})


# ---------- 조회 ----------
def agent_get(ctx: Ctx, body: dict) -> None:
    name = str(body.get("name", "") or "").strip()
    version = str(body.get("version", "") or body.get("recordVersion", "") or "").strip() or None
    rec = _find_agent_record(name, version)
    if rec is None:
        ctx.post({"type": "agent_get", "ok": False, "code": 404, "error": f"에이전트 없음: {name} {version or ''}".strip()})
        return
    harness_summary, harness_err = None, None
    try:
        harness_summary = _harness_summary(_harness().find_harness(_harness_name(name)))
    except Exception as e:  # noqa: BLE001
        harness_err = _err(e)
    ctx.post({"type": "agent_get", "ok": True, "record": registry_api.public_record(rec), "harness": harness_summary, "harnessError": harness_err,
              "audit": registry_api.public_agent_audit(registry_api.audit_trail(name, rec["recordVersion"]))})


# ---------- 호출 (스트리밍) ----------
def agent_invoke(ctx: Ctx, body: dict) -> None:
    name = str(body.get("name", "") or "").strip()
    version = str(body.get("version", "") or "").strip() or None
    message = str(body.get("message", "") or "").strip()[:MAX_MESSAGE]
    session_id = body.get("sessionId")
    session_id = str(session_id)[:128] if isinstance(session_id, str) and session_id.strip() else None
    if not ctx.user_sub:
        ctx.done("agent", error="인증된 사용자 식별자가 필요합니다. 다시 연결하세요.", code=401)
        return
    if not name or not message:
        ctx.done("agent", error="에이전트 이름과 메시지가 필요합니다.", name=name)
        return

    rec = _find_agent_record(name, version)
    if rec is None:
        ctx.done("agent", error=f"에이전트 없음: {name}", name=name, status=None)
        return
    status = rec.get("status")
    if status != "APPROVED":
        # ★ Consumer 게이트 — APPROVED 가 아니면 Harness 를 호출하는 코드 경로에 들어가지 않는다
        log_event("agent.invoke_refused", ctx.trace_id, name=name, version=rec.get("recordVersion"), status=status,
                  email=ctx.email)
        ctx.done("agent", error="승인되지 않은 에이전트는 호출할 수 없습니다 (Consumer 게이트)", status=status,
                 name=name, version=rec.get("recordVersion"), gate="consumer", runtime=RUNTIME)
        return

    import hashlib
    import uuid
    client_session_id = session_id or uuid.uuid4().hex
    session_id = hashlib.sha256(json.dumps(
        ["bank-agent-session-v1", ctx.user_sub, rec["name"], rec["recordVersion"], client_session_id],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    payload = rec.get("payload") or {}
    model_id = payload.get("model")
    from agentcore import invoke as _invoke
    kind = _invoke.kind_of(rec)
    desc = _invoke.describe(rec)
    runtime_label = desc.get("runtime") if desc.get("runtime") not in (None, "", "—") else RUNTIME
    runtime_badge = desc.get("badge")
    harness_arn = None
    if kind != "strands":
        # Harness 형(또는 런타임 미지정 레코드): 이름 규칙 bank_<name> 으로 Harness 를 찾는다
        harness_arn, _h, herr = _resolve_harness_arn(rec)
        if not harness_arn:
            ctx.done("agent", error=("이 에이전트에 연결된 Harness 가 없습니다" + (f" — {herr}" if herr else
                                     " (파이프라인형 에이전트는 각 시나리오 화면에서 실행)")),
                     status=status, name=name, version=rec.get("recordVersion"), runtime=payload.get("runtime"))
            return
        from agentcore import skill_binding
        from agentcore.administration import harness_settings
        try:
            # Legacy approved records receive the same gate; creation date or
            # absence of an administration marker is never an exemption.
            skill_binding.resolve(payload.get("skillBindings"), payload.get("skills", []))
            spec, error = _validate_create({**payload, "name": rec["name"], "description": rec.get("description", "")})
            if error or not spec or (_h or {}).get("status") not in {"READY", "ACTIVE"}:
                raise ValueError("Harness approval needs reconciliation")
            if harness_settings(_h) != harness_settings(_harness().build_config(spec)):
                raise ValueError("Harness configuration changed after approval")
        except (RegistryError, OSError, UnicodeError, ValueError, TypeError):
            ctx.done("agent", error="승인된 Harness·Skill 구성을 확인하지 못했습니다. IAM 관리자 검토가 필요합니다.", code=409)
            return
        kind = "harness"
        runtime_label = _invoke.RUNTIME_HARNESS
        runtime_badge = _invoke.BADGE_HARNESS
        if not payload.get("harnessArn"):
            rec = {**rec, "payload": {**payload, "harnessArn": harness_arn, "runtime": _invoke.RUNTIME_HARNESS}}

    ctx.stage("agent", "gate", ok=True, status=status, name=name, version=rec.get("recordVersion"),
              modelId=model_id, runtime=runtime_label, runtimeBadge=runtime_badge, runtimeKind=kind, harnessArn=harness_arn,
              runtimeArn=desc.get("runtimeArn"), plane="agentcore")

    usage: dict = {}
    stop_reason, out_sid = "", session_id
    tool_calls, text_len, errors = 0, 0, []
    boundary_events: list = []
    tools_missing: list = []
    blocked, pii_outbound = False, None
    started = time.time()
    runtime_kind = kind
    try:
        for kind_ev, data in _invoke.stream(rec, message, session_id):
            if kind_ev == "text":
                text_len += len(data)
                ctx.token("agent", data)
            elif kind_ev == "tool_start":
                tool_calls += 1
                ctx.stage("agent", "tool_start", plane="agentcore", **_stage_kw(data))
            elif kind_ev == "tool_input":
                ctx.stage("agent", "tool_input", plane="agentcore", **_stage_kw(data))
            elif kind_ev == "tool_result":
                ctx.stage("agent", "tool_result", plane="agentcore", **_stage_kw(data))
            elif kind_ev == "boundary":
                boundary_events.append(data if isinstance(data, dict) else {"raw": str(data)[:200]})
                ctx.stage("agent", "boundary", plane="boundary", **_stage_kw(data))
            elif kind_ev == "error":
                error_message = "에이전트 실행을 완료하지 못했습니다."
                errors.append(error_message)
                ctx.stage("agent", "error", message=error_message)
            elif kind_ev == "meta":
                usage = (data or {}).get("usage") or {}
                tools_missing = [str(name) for name in (data or {}).get("toolsMissing", [])]
                stop_reason = (data or {}).get("stopReason", "")
                out_sid = (data or {}).get("sessionId") or out_sid
        meta_model = None
        blocked = stop_reason == "gate_refused"
        pii_outbound = sum(int(event.get("piiRules", 0) or 0) for event in boundary_events) if boundary_events else None
        done_kw: Dict[str, Any] = {"usage": usage, "stopReason": stop_reason, "sessionId": client_session_id,
                                   "runtimeSessionId": session_id,
                                   "toolsMissing": tools_missing,
                                   "modelId": model_id, "runtime": runtime_label, "runtimeBadge": runtime_badge, "runtimeKind": runtime_kind, "name": name,
                                   "boundary": boundary_events[-1] if boundary_events else None,
                                   "boundaryCrossings": len(boundary_events),
                                   "version": rec.get("recordVersion"), "toolCalls": tool_calls, "errors": errors}
        if tools_missing:
            done_kw["code"] = 502
        if blocked:
            done_kw.update(blocked=True, blockedBy="gate")
        if errors and text_len == 0:
            done_kw["error"] = "에이전트 스트림 오류 — " + errors[-1][:200]
        ctx.done("agent", **done_kw)
    except Exception as e:  # noqa: BLE001
        errors.append(_err(e))
        from engine.gate import GateRefused
        if isinstance(e, GateRefused):
            blocked, pii_outbound, stop_reason = True, e.count, "gate_refused"
        ctx.done("agent", error=f"에이전트 호출 실패 — {_err(e)}", name=name, version=rec.get("recordVersion"),
                 modelId=model_id, runtime=runtime_label, sessionId=client_session_id,
                 runtimeSessionId=session_id, usage=usage, toolCalls=tool_calls,
                 blocked=blocked, stopReason=stop_reason)
    finally:
        tokens_in, tokens_out = int(usage.get("inputTokens", 0) or 0), int(usage.get("outputTokens", 0) or 0)
        elapsed = int((time.time() - started) * 1000)
        try:
            tracing.record_trace({"traceId": ctx.trace_id, "scenario": "AGENT", "email": ctx.email, "query": message,
                                  "tokensIn": tokens_in, "tokensOut": tokens_out, "modelId": model_id,
                                  "route": "strands" if runtime_kind == "strands" else "harness", "plane": "agentcore", "blocked": blocked,
                                  **({"piiOutbound": pii_outbound} if pii_outbound is not None else {}),
                                  "piiDetectors": (["rules(harness-input)" if runtime_kind == "harness" else "rules(gate)"]
                                                   if boundary_events or blocked else []), "agent": name,
                                  "toolCalls": tool_calls, "errors": len(errors), "cached": False,
                                  "elapsedMs": elapsed})
        except Exception as e:  # noqa: BLE001
            log_event("agent.trace_failed", ctx.trace_id, errorType=type(e).__name__)
        try:
            from common import costguard
            costguard.add_usage(tokens_in + tokens_out)
        except Exception:  # noqa: BLE001
            pass
        log_event("agent.invoke", ctx.trace_id, name=name, version=rec.get("recordVersion"), tokensIn=tokens_in,
                  tokensOut=tokens_out, toolCalls=tool_calls, errors=len(errors), stopReason=stop_reason,
                  textLen=text_len, ms=elapsed, email=ctx.email)


ROUTES = {
    "agents_catalog": agents_catalog, "agent_create": agent_create, "agent_invoke": agent_invoke,
    "agent_transition": agent_transition, "agent_get": agent_get,
}
