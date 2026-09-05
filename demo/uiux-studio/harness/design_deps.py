"""design_loop deps for the sister UI/UX studio harness (strands BedrockModel).

The platform is the source of truth for the shared engine (platform/design_loop); this file only
provides the model-call callbacks the engine needs. Sister demo uses synthetic assets; no PII gate here.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

_JSON = re.compile(r"\{.*\}", re.S)
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
GEN_MAX_TOKENS = int(os.environ.get("DESIGN_GEN_MAX_TOKENS", "32000"))
JUDGE_MAX_TOKENS = int(os.environ.get("DESIGN_JUDGE_MAX_TOKENS", "500"))


def _model(model_id: str, max_tokens: int):
    from strands.models import BedrockModel
    return BedrockModel(model_id=model_id, region_name=REGION, streaming=True, max_tokens=max_tokens)


def make_deps(model_id: str) -> Dict[str, Any]:
    from strands import Agent

    acc = {"inputTokens": 0, "outputTokens": 0, "calls": 0}

    def _run(system: str, user: str, max_tokens: int) -> str:
        agent = Agent(model=_model(model_id, max_tokens), system_prompt=system, callback_handler=None)
        res = agent(user)
        try:
            u = res.metrics.accumulated_usage
            acc["inputTokens"] += int(u.get("inputTokens", 0) or 0)
            acc["outputTokens"] += int(u.get("outputTokens", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        acc["calls"] += 1
        return str(res)

    def generate(system: str, user: str, on_token) -> str:
        text = _run(system, user, GEN_MAX_TOKENS)
        if on_token:
            on_token(text[:40])
        return text

    def llm_judge(item: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, str]:
        sys_p = ("당신은 은행 UX 디자인 검수자다. 체크리스트 항목 하나를 플로우 텍스트에 대해 판정한다. "
                 "verdict 는 pass|fail|incomplete 중 하나, evidence 는 한국어 한두 문장. "
                 "출력은 JSON 하나만: {\"verdict\": \"pass|fail|incomplete\", \"evidence\": \"...\"}")
        prd = context.get("prd") or {}
        steps = ", ".join(s.get("id", "") for s in prd.get("steps") or [])
        user_p = ("### 항목\n[" + str(item.get("id")) + "] " + str(item.get("text")) + "\n\n### PRD 스텝\n" + steps +
                  "\n\n### 플로우 텍스트\n" + str(context.get("flowText", ""))[:6000])
        text = _run(sys_p, user_p, JUDGE_MAX_TOKENS)
        m = _JSON.search(text)
        if not m:
            return {"verdict": "incomplete", "evidence": "판정 JSON 없음"}
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return {"verdict": "incomplete", "evidence": "판정 JSON 오류"}
        v = str(obj.get("verdict", "")).lower()
        return {"verdict": v if v in ("pass", "fail", "incomplete") else "incomplete", "evidence": str(obj.get("evidence", ""))[:600]}

    return {"generate": generate, "llm_judge": llm_judge, "usage": lambda: dict(acc)}


def load_seed():
    """번들된 합성 자산 로드 — design_seed/{product_specs,sm_model,checklists}.json."""
    from pathlib import Path
    for base in (Path(__file__).resolve().parent.parent, Path("/app")):
        d = base / "design_seed"
        if (d / "product_specs.json").is_file():
            j = lambda n: json.loads((d / n).read_text(encoding="utf-8"))  # noqa: E731
            return j("product_specs.json"), j("sm_model.json"), j("checklists.json")
    return [], {}, []
