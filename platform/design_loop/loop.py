"""유계 루프: prd → checklist → generate → review(+test) → [regenerate ×1 → review] → report.

deps:
  generate(system: str, user: str, on_token: Callable[[str], None]) -> str      (필수)
  llm_judge(item, context) -> {"verdict", "evidence"}                          (선택 — 없으면 llm 항목은 미판정)
  test_gate(flow) -> list[{"id","text","verdict","evidence"}]                   (선택 — 외부 게이트(axe 등) 결과를 항목으로 추가)
emit(event: dict): {"type": "stage", "step": …} | {"type": "token", "text": …}
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .checklist import build_checklist
from .generate import ParseError, build_prompts, parse_flow
from .models import SpecError
from .prd import derive_prd
from .review import review

MAX_REGENERATIONS = 1                # SPEC §12.11 — 재생성은 최대 1회
MAX_ATTEMPTS = MAX_REGENERATIONS + 1
PARSE_RETRIES = 1                    # 형식 위반 시 같은 시도 안에서 재요청 1회 (재생성 카운트에 포함하지 않음)


def _emit(emit: Optional[Callable[[dict], None]], ev: dict) -> None:
    if emit is not None:
        emit(ev)


def run(spec: Dict[str, Any], sm: Dict[str, Any], checklists: List[Dict[str, Any]], deps: Dict[str, Any],
        emit: Optional[Callable[[dict], None]] = None, assets: Optional[List[dict]] = None, prd: Optional[Dict[str, Any]] = None,
        output_type: str = "design") -> Dict[str, Any]:
    started = time.time()
    gen = deps["generate"]
    judge = deps.get("llm_judge")
    test_gate = deps.get("test_gate")

    # 1) PRD
    _emit(emit, {"type": "stage", "step": "prd", "status": "start"})
    try:
        prd = prd or derive_prd(spec, sm)
    except SpecError as e:
        _emit(emit, {"type": "stage", "step": "prd", "status": "error", "code": "spec-incomplete", "missing": e.missing})
        return {"error": str(e), "code": "spec-incomplete", "missing": e.missing}
    _emit(emit, {"type": "stage", "step": "prd", "status": "done", "steps": [s["id"] for s in prd["steps"]],
                 "branchSteps": prd.get("branchSteps", []), "transitions": len(prd.get("transitions") or [])})

    # 2) 체크리스트
    items = build_checklist(spec, checklists)
    _emit(emit, {"type": "stage", "step": "checklist", "status": "done", "total": len(items),
                 "base": sum(1 for i in items if i.get("source") == "base"),
                 "derived": sum(1 for i in items if i.get("source") == "derived"),
                 "sets": sorted({str(i.get("set")) for i in items})})

    flow: Optional[Dict[str, Any]] = None
    report: Optional[Dict[str, Any]] = None
    failures: List[dict] = []
    attempts = 0
    history: List[dict] = []
    usage_notes: List[dict] = []

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        if attempts > 1:
            _emit(emit, {"type": "stage", "step": "regenerate", "attempt": attempts, "limit": MAX_REGENERATIONS,
                         "reasons": [f"[{f['id']}] {f['text']}" for f in failures][:8]})
        system, user = build_prompts(prd, sm, spec, assets=assets, failures=failures or None, previous=flow, output_type=output_type)
        _emit(emit, {"type": "stage", "step": "generate", "attempt": attempts, "status": "start",
                     "promptChars": len(system) + len(user)})
        text = gen(system, user, lambda t: _emit(emit, {"type": "token", "text": t}))
        parsed = None
        for k in range(PARSE_RETRIES + 1):
            try:
                parsed = parse_flow(text)
                break
            except ParseError as e:
                if k >= PARSE_RETRIES:
                    _emit(emit, {"type": "stage", "step": "generate", "attempt": attempts, "status": "error", "message": str(e)})
                    return {"error": f"출력 형식 오류: {e}", "code": "parse-failed", "prd": prd, "checklist": items,
                            "attempts": attempts, "elapsedMs": int((time.time() - started) * 1000)}
                _emit(emit, {"type": "stage", "step": "generate", "attempt": attempts, "status": "reparse", "message": str(e)})
                text = gen(system, user + "\n\n★ 직전 출력이 형식을 위반했다: " + str(e) +
                           "\n반드시 <<<STEP id=\"…\" title=\"…\">>> … <<<END>>> 블록과 <<<FLOW>>> 블록만 출력한다.",
                           lambda t: _emit(emit, {"type": "token", "text": t}))
        flow = parsed
        _emit(emit, {"type": "stage", "step": "generate", "attempt": attempts, "status": "done",
                     "steps": [s["id"] for s in flow["steps"]], "htmlChars": sum(len(s["html"]) for s in flow["steps"])})

        # 리뷰(체크리스트) + 테스트(외부 게이트)
        _emit(emit, {"type": "stage", "step": "review", "attempt": attempts, "status": "start", "items": len(items)})
        report = review(flow, prd, items, llm_judge=judge, attempt=attempts)
        if test_gate is not None:
            _emit(emit, {"type": "stage", "step": "test", "attempt": attempts, "status": "start"})
            try:
                extra = test_gate(flow) or []
            except Exception as e:  # noqa: BLE001
                extra = [{"id": "gate-error", "text": "외부 게이트 실행", "verdict": "incomplete",
                          "evidence": f"{type(e).__name__}: {str(e)[:120]}"}]
            for x in extra:
                report["items"].append({"id": x.get("id"), "text": x.get("text"), "method": "gate", "target": "screen",
                                        "source": "gate", "set": "test-agent", "severity": x.get("severity", "major"),
                                        "verdict": x.get("verdict", "incomplete"), "evidence": str(x.get("evidence", ""))[:600],
                                        "attempt": attempts})
            report["score"] = {"pass": sum(1 for r in report["items"] if r["verdict"] == "pass"),
                               "fail": sum(1 for r in report["items"] if r["verdict"] == "fail"),
                               "incomplete": sum(1 for r in report["items"] if r["verdict"] == "incomplete"),
                               "total": len(report["items"])}
            report["failures"] = [r for r in report["items"] if r["verdict"] == "fail"]
            _emit(emit, {"type": "stage", "step": "test", "attempt": attempts, "status": "done", "added": len(extra)})
        failures = report["failures"]
        history.append({"attempt": attempts, "score": report["score"], "failed": [f["id"] for f in failures]})
        _emit(emit, {"type": "stage", "step": "review", "attempt": attempts, "status": "done", **report["score"],
                     "failed": [f["id"] for f in failures]})
        if not failures:
            break

    final = {
        "prd": prd, "checklist": items, "flow": flow,
        "report": {**report, "attempts": attempts, "regenerated": attempts > 1, "maxRegenerations": MAX_REGENERATIONS,
                   "history": history, "openItems": [f["id"] for f in failures]},
        "attempts": attempts, "regenerated": attempts > 1, "ok": not failures,
        "elapsedMs": int((time.time() - started) * 1000),
    }
    _emit(emit, {"type": "stage", "step": "report", "status": "done", "ok": final["ok"], "attempts": attempts,
                 "open": final["report"]["openItems"], **report["score"]})
    return final
