"""에이전트 호출 디스패치 — Registry 레코드의 payload.runtime 에 따라 AgentCore Runtime(Strands) 또는 Harness 로.

    from agentcore import invoke
    for kind, data in invoke.stream(record, message, session_id): ...   # harness.invoke_stream 과 같은 튜플
    invoke.describe(record) → {runtime, modelId, tier, badge, runtimeArn|harnessArn}

- payload.runtime == 'agentcore-runtime/strands' → runtime.invoke_stream(AGENTS_RUNTIME_ARN 또는 payload.runtimeArn, name, ...)
  (레코드 이름의 'bank_' 접두어는 컨테이너 명세 이름이 아니므로 제거한다)
- payload.runtime == 'AgentCore Harness' 또는 payload.harnessArn 존재 → harness.invoke_stream(payload.harnessArn, ...)
- 그 외 → ValueError (파이프라인형 에이전트는 각 시나리오 화면에서 실행된다)
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterator, Optional, Tuple

RUNTIME_STRANDS = "agentcore-runtime/strands"
RUNTIME_HARNESS = "AgentCore Harness"
BADGE_STRANDS = "AgentCore Runtime · Strands"
BADGE_HARNESS = "AgentCore Harness (설정형)"
TIER = "0/1"  # Tier 0/1 전용 — Tier 2(PII 추론) 경로는 AgentCore 를 태우지 않는다 (SPEC §11-4, §16)


def _payload(record: dict) -> dict:
    p = (record or {}).get("payload")
    return p if isinstance(p, dict) else {}


def agent_name(record: dict) -> str:
    """Registry 레코드 이름 → 컨테이너 명세 이름 ('bank_' 접두어 제거)."""
    name = str((record or {}).get("name") or "").strip()
    return name[5:] if name.startswith("bank_") else name


def runtime_arn(record: dict) -> str:
    return str(_payload(record).get("runtimeArn") or os.environ.get("AGENTS_RUNTIME_ARN", "") or "").strip()


def kind_of(record: dict) -> Optional[str]:
    """'strands' | 'harness' | None."""
    p = _payload(record)
    rt = str(p.get("runtime") or "").strip()
    if rt == RUNTIME_STRANDS:
        return "strands"
    if rt == RUNTIME_HARNESS or p.get("harnessArn"):
        return "harness"
    return None


def describe(record: dict) -> Dict[str, Any]:
    p = _payload(record)
    k = kind_of(record)
    out: Dict[str, Any] = {"runtime": p.get("runtime") or ("—" if k is None else k), "modelId": p.get("model"),
                           "tier": TIER, "badge": None, "sdk": p.get("sdk")}
    if k == "strands":
        out.update({"badge": BADGE_STRANDS, "runtimeArn": runtime_arn(record) or None, "sdk": p.get("sdk") or "Strands Agents"})
    elif k == "harness":
        out.update({"badge": BADGE_HARNESS, "harnessArn": p.get("harnessArn")})
    return out


def stream(record: dict, text: str, session_id: Optional[str] = None, model: Optional[str] = None) -> Iterator[Tuple[str, Any]]:
    k = kind_of(record)
    if k == "strands":
        arn = runtime_arn(record)
        if not arn:
            raise ValueError("AgentCore Runtime ARN 없음 — AGENTS_RUNTIME_ARN 또는 payload.runtimeArn 필요")
        from agentcore import runtime

        return runtime.invoke_stream(arn, agent_name(record), text, session_id=session_id, model=model)
    if k == "harness":
        arn = str(_payload(record).get("harnessArn") or "").strip()
        if not arn:
            raise ValueError("Harness ARN 없음 — payload.harnessArn 필요")
        from agentcore import harness

        return harness.invoke_stream(arn, text, session_id)
    raise ValueError(f"실행 런타임을 알 수 없는 에이전트: runtime={_payload(record).get('runtime')!r} "
                     f"(지원: {RUNTIME_STRANDS!r}, {RUNTIME_HARNESS!r})")
