"""design_flow_agent 용 deps — 컨테이너 경계(BoundaryGateHook)를 지나는 생성·판정.

모델 호출은 strands 라이브러리 안에서 일어난다 (이 파일에는 bedrock-runtime 클라이언트·converse 호출이 없다 — §12.1 게이트 가드 통과).
경계: 프롬프트를 scan_rules 로 먼저 검사(식별자 히트 → 거부)하고, BoundaryGateHook 이 모델 호출 직전 메시지를 다시 스캔한다.
design_loop 는 이 deps 의 generate/llm_judge/usage 만 쓴다.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

_JSON = re.compile(r"\{.*\}", re.S)
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "").strip()
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "").strip() or "DRAFT"
GEN_MAX_TOKENS = int(os.environ.get("DESIGN_GEN_MAX_TOKENS", "32000"))
JUDGE_MAX_TOKENS = int(os.environ.get("DESIGN_JUDGE_MAX_TOKENS", "500"))


def _model(model_id: str, max_tokens: int):
    from strands.models import BedrockModel
    kw: Dict[str, Any] = {"model_id": model_id, "region_name": REGION, "streaming": True, "max_tokens": max_tokens}
    if os.environ.get("GEN_TEMPERATURE"):
        kw["temperature"] = float(os.environ["GEN_TEMPERATURE"])
    if GUARDRAIL_ID:
        kw.update({"guardrail_id": GUARDRAIL_ID, "guardrail_version": GUARDRAIL_VERSION, "guardrail_trace": "enabled"})
    return BedrockModel(**kw)


def make_deps(model_id: str) -> Dict[str, Any]:
    from strands import Agent
    from boundary_gate import BoundaryGateHook, scan_rules

    acc = {"inputTokens": 0, "outputTokens": 0, "calls": 0}

    def _acc(res: Any) -> None:
        try:
            u = res.metrics.accumulated_usage
            acc["inputTokens"] += int(u.get("inputTokens", 0) or 0)
            acc["outputTokens"] += int(u.get("outputTokens", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        acc["calls"] += 1

    def _run(system: str, user: str, max_tokens: int) -> str:
        hits = scan_rules(system + "\n" + user)
        if hits:  # 경계: 식별자가 프롬프트에 있으면 모델 호출 없이 거부
            raise RuntimeError("boundary refused (identifier rule hit): " + ",".join(sorted({h["type"] for h in hits})))
        agent = Agent(model=_model(model_id, max_tokens), system_prompt=system, hooks=[BoundaryGateHook()],
                      callback_handler=None)
        res = agent(user)
        _acc(res)
        return str(res)

    def generate(system: str, user: str, on_token) -> str:
        text = _run(system, user, GEN_MAX_TOKENS)
        if on_token:
            on_token(text[:40])
        return text

    def llm_judge(item: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, str]:
        sys_p = ("당신은 은행 UX 디자인 검수자다. 체크리스트 항목 하나를 플로우 텍스트(스텝별 가시 문구)에 대해 판정한다. "
                 "verdict 는 pass|fail|incomplete 중 하나, evidence 는 한국어 한두 문장. "
                 "출력은 JSON 하나만: {\"verdict\": \"pass|fail|incomplete\", \"evidence\": \"...\"}")
        prd = context.get("prd") or {}
        steps = ", ".join(s.get("id", "") for s in prd.get("steps") or [])
        user_p = ("### 항목\n[" + str(item.get("id")) + "] " + str(item.get("text")) + " (대상: " + str(item.get("target")) +
                  ")\n\n### PRD 스텝 순서\n" + steps + "\n\n### 플로우 텍스트\n" + str(context.get("flowText", ""))[:6000])
        text = _run(sys_p, user_p, JUDGE_MAX_TOKENS)
        m = _JSON.search(text)
        if not m:
            return {"verdict": "incomplete", "evidence": f"판정 JSON 없음: {text[:120]}"}
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return {"verdict": "incomplete", "evidence": f"판정 JSON 오류: {text[:120]}"}
        v = str(obj.get("verdict", "")).lower()
        return {"verdict": v if v in ("pass", "fail", "incomplete") else "incomplete", "evidence": str(obj.get("evidence", ""))[:600]}

    return {"generate": generate, "llm_judge": llm_judge, "usage": lambda: dict(acc)}
