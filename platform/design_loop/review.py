"""리뷰 — 체크리스트 항목별 판정. rule → rules.py(결정론), llm → 호출자 콜백(없으면 미판정).

llm_judge(item, context) -> {"verdict": pass|fail|incomplete, "evidence": str}
context = {"flowText": 스텝별 가시 텍스트 요약, "prd": prd}
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .rules import run_rule, visible_text

Judge = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, str]]


def flow_text(flow: Dict[str, Any], limit_per_step: int = 700) -> str:
    parts = []
    for s in flow.get("steps") or []:
        parts.append(f"[{s.get('id')}] {s.get('title')}: {visible_text(s.get('html', ''))[:limit_per_step]}")
    return "\n".join(parts)


def review(flow: Dict[str, Any], prd: Dict[str, Any], items: List[Dict[str, Any]], llm_judge: Optional[Judge] = None,
           attempt: int = 1) -> Dict[str, Any]:
    ctx = {"flowText": flow_text(flow), "prd": prd}
    results: List[dict] = []
    for it in items:
        if it.get("method") == "rule":
            r = run_rule(flow, prd, it.get("rule") or {})
        elif llm_judge is not None:
            try:
                r = llm_judge(it, ctx)
                if r.get("verdict") not in ("pass", "fail", "incomplete"):
                    r = {"verdict": "incomplete", "evidence": f"판정 형식 오류: {str(r)[:120]}"}
            except Exception as e:  # noqa: BLE001
                r = {"verdict": "incomplete", "evidence": f"LLM 판정 실패: {type(e).__name__}"}
        else:
            r = {"verdict": "incomplete", "evidence": "LLM 판정기 미연결"}
        results.append({"id": it.get("id"), "text": it.get("text"), "method": it.get("method"), "target": it.get("target"),
                        "source": it.get("source", "base"), "set": it.get("set"), "severity": it.get("severity", "major"),
                        "verdict": r["verdict"], "evidence": str(r.get("evidence", ""))[:600], "attempt": attempt})
    score = {"pass": sum(1 for r in results if r["verdict"] == "pass"),
             "fail": sum(1 for r in results if r["verdict"] == "fail"),
             "incomplete": sum(1 for r in results if r["verdict"] == "incomplete"), "total": len(results)}
    return {"items": results, "score": score, "attempt": attempt,
            "failures": [r for r in results if r["verdict"] == "fail"]}
