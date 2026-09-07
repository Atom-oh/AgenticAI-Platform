"""PRD 도출 — 결정론 골격. 스텝 존재 여부는 규칙이 정하고, LLM 은 문구만 다듬는다(옵션, 이 버전은 미사용).

규칙:
- 템플릿(SM 모델)의 스텝이 기본 골격.
- 우대조건의 evidence.type == "input" 이면 그 조건의 '증빙 입력' 스텝을 `preferential` 스텝 뒤에 분기로 삽입한다.
- 스텝별 필수 요소(required) = 오가니즘 required ∪ 스텝 고정 요소(상품명·기본금리·필수 고지…).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import organism_index, pick_template, validate_sm, validate_spec

BRANCH_ANCHOR = "preferential"      # 증빙 스텝이 붙는 위치
NOTICE_STEPS = ("terms", "confirm")  # 필수 고지가 반드시 보여야 하는 스텝


def _rate_text(rate: Any) -> str:
    try:
        return f"{float(rate):g}%"
    except (TypeError, ValueError):
        return str(rate or "")


def derive_prd(spec: Dict[str, Any], sm: Dict[str, Any]) -> Dict[str, Any]:
    spec = validate_spec(spec)
    sm = validate_sm(sm)
    tpl = pick_template(sm, spec)
    orgs = organism_index(sm)
    steps: List[dict] = []
    for s in tpl.get("steps") or []:
        sid = str(s.get("id"))
        required: List[str] = []
        for oid in s.get("organisms") or []:
            required += [str(r) for r in (orgs.get(str(oid)) or {}).get("required", [])]
        if sid in ("intro", "confirm"):
            required.append(spec["productName"])
        if sid == "intro":
            required.append(_rate_text(spec["baseRate"]))
        if sid in NOTICE_STEPS:
            required += spec["mandatoryNotices"]
        if sid == BRANCH_ANCHOR:
            required += [c["condition"] for c in spec["preferentialConditions"]]
        steps.append({"id": sid, "title": str(s.get("title") or sid), "organisms": list(s.get("organisms") or []),
                      "required": _dedupe(required)})

    # 우대조건 증빙 입력 스텝 (분기)
    anchor = next((i for i, s in enumerate(steps) if s["id"] == BRANCH_ANCHOR), len(steps) - 2)
    inserted: List[dict] = []
    for c in spec["preferentialConditions"]:
        if c["evidence"]["type"] != "input":
            continue
        label = c["evidence"].get("label") or f"{c['condition']} 증빙"
        inserted.append({"id": f"evidence-{c['id']}", "title": f"{c['condition']} 증빙 입력",
                         "organisms": ["evidence-input"],
                         "required": _dedupe([label] + [str(r) for r in (orgs.get("evidence-input") or {}).get("required", [])]),
                         "branch": {"when": f"우대조건 '{c['condition']}' 선택", "after": BRANCH_ANCHOR,
                                    "conditionId": c["id"]}})
    steps = steps[:anchor + 1] + inserted + steps[anchor + 1:]

    transitions = _transitions(steps, inserted)
    return {"productSpecId": spec.get("id"), "smModelId": sm.get("id"), "templateId": tpl.get("id"),
            "title": f"{spec['productName']} 가입 프로세스", "steps": steps, "transitions": transitions,
            "branchSteps": [s["id"] for s in inserted],
            "derivedFrom": {"productSpec": spec.get("id"), "smModel": sm.get("id")}}


def _transitions(steps: List[dict], inserted: List[dict]) -> List[dict]:
    ids = [s["id"] for s in steps]
    branch_ids = {s["id"] for s in inserted}
    out: List[dict] = []
    main = [i for i in ids if i not in branch_ids]
    for a, b in zip(main, main[1:]):
        out.append({"from": a, "to": b, "trigger": "가입 신청" if b == "complete" else "다음"})
    after_anchor = main[main.index(BRANCH_ANCHOR) + 1] if BRANCH_ANCHOR in main and main.index(BRANCH_ANCHOR) + 1 < len(main) else None
    for s in inserted:
        out.append({"from": s["branch"]["after"], "to": s["id"], "trigger": "다음", "when": s["branch"]["when"]})
        if after_anchor:
            out.append({"from": s["id"], "to": after_anchor, "trigger": "다음"})
    return out


def _dedupe(xs: List[str]) -> List[str]:
    seen, out = set(), []
    for x in xs:
        x = str(x).strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
