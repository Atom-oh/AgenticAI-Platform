"""입력 자산·산출물의 형태 검증 (dict 기반 — Registry payload 를 그대로 받는다).

ProductSpec  {productName, productType, category, eligibility, term, baseRate, preferentialConditions[], mandatoryNotices[],
              terminationRules, partners[]}
  preferentialConditions[i] = {id, condition, rate, evidence: {type: input|auto|none, label?, format?}}
  partners[i] = {name, type}
SmModel      {id, molecules[], organisms[], templates[]}
  organisms[i] = {id, title, composition[], rules[], required[], relations[]}
  templates[i] = {id, title, steps[] {id, title, organisms[]}}
Checklist    {id, title, appliesTo{productType?, category?, partnerType?}, items[] {id, text, method: rule|llm, target: flow|screen,
              rule?: {fn, args}, severity}}
FlowDraft    {steps[] {id, title, html, organisms[]}, transitions[] {from, to, trigger, when?}}
"""
from __future__ import annotations

from typing import Any, Dict, List


class SpecError(ValueError):
    """명세 필드 부족 — code 'spec-incomplete', missing 에 필드명."""

    def __init__(self, missing: List[str]):
        super().__init__("상품명세서 필드 부족: " + ", ".join(missing))
        self.missing = missing


SPEC_REQUIRED = ("productName", "productType", "category", "baseRate")


def validate_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise SpecError(list(SPEC_REQUIRED))
    missing = [k for k in SPEC_REQUIRED if spec.get(k) in (None, "", [])]
    if missing:
        raise SpecError(missing)
    out = dict(spec)
    conds = []
    for i, c in enumerate(spec.get("preferentialConditions") or []):
        if not isinstance(c, dict) or not c.get("condition"):
            continue
        ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {"type": "none"}
        conds.append({"id": str(c.get("id") or f"pc{i + 1}"), "condition": str(c["condition"]),
                      "rate": c.get("rate"), "evidence": {"type": str(ev.get("type") or "none"),
                                                          "label": ev.get("label"), "format": ev.get("format")}})
    out["preferentialConditions"] = conds
    out["mandatoryNotices"] = [str(n) for n in (spec.get("mandatoryNotices") or []) if str(n).strip()]
    out["partners"] = [{"name": str(p.get("name")), "type": str(p.get("type") or "")}
                       for p in (spec.get("partners") or []) if isinstance(p, dict) and p.get("name")]
    return out


def validate_sm(sm: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(sm, dict) or not sm.get("templates"):
        raise ValueError("UX SM 모델에 templates 가 없습니다")
    return sm


def organism_index(sm: Dict[str, Any]) -> Dict[str, dict]:
    return {str(o.get("id")): o for o in (sm.get("organisms") or []) if isinstance(o, dict) and o.get("id")}


def pick_template(sm: Dict[str, Any], spec: Dict[str, Any]) -> dict:
    """상품유형/카테고리에 맞는 템플릿 — appliesTo 가 없으면 첫 템플릿."""
    for t in sm.get("templates") or []:
        a = t.get("appliesTo") or {}
        if a and all(spec.get(k) == v for k, v in a.items()):
            return t
    for t in sm.get("templates") or []:
        if not t.get("appliesTo"):
            return t
    return (sm.get("templates") or [{}])[0]


def step_by_id(flow: Dict[str, Any], step_id: str) -> dict | None:
    for s in flow.get("steps") or []:
        if str(s.get("id")) == step_id:
            return s
    return None
