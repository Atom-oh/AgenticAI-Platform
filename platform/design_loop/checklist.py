"""체크리스트 확정 — 기본 세트(자산) + 상품명세서 파생 항목.

항목 형식: {id, text, method: rule|llm, target: flow|screen, source: base|derived, rule?: {fn, args}, severity, set}
파생 규칙(결정론):
- 우대조건 evidence.type == input → '증빙 입력 스텝 존재'(flow/rule) + '조건·우대금리 고지'(screen/rule)
- 필수 고지 각각 → 고지 문구 존재(terms|confirm)
- 제휴사 각각 → 제휴사명 표기
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import validate_spec
from .prd import _rate_text


def _applies(cl: Dict[str, Any], spec: Dict[str, Any]) -> bool:
    a = cl.get("appliesTo") or {}
    if not a:
        return True
    if a.get("productType") and a["productType"] != spec.get("productType"):
        return False
    if a.get("category") and a["category"] != spec.get("category"):
        return False
    if a.get("partnerType"):
        if not any(p.get("type") == a["partnerType"] for p in spec.get("partners") or []):
            return False
    return True


def _bind(item: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """기본 세트 항목의 rule.args 에 '$spec.<field>' 참조가 있으면 명세서 값으로 치환한다."""
    rule = item.get("rule")
    if not isinstance(rule, dict):
        return item
    args = {}
    for k, v in (rule.get("args") or {}).items():
        if v == "$spec.partnerNames":
            args[k] = [p["name"] for p in spec.get("partners") or []]
        elif v == "$spec.productName":
            args[k] = [spec["productName"]]
        elif v == "$spec.baseRate":
            args[k] = [_rate_text(spec["baseRate"])]
        else:
            args[k] = v
    return {**item, "rule": {**rule, "args": args}}


def build_checklist(spec: Dict[str, Any], base_sets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    spec = validate_spec(spec)
    items: List[Dict[str, Any]] = []
    for cl in base_sets or []:
        if not _applies(cl, spec):
            continue
        for it in cl.get("items") or []:
            items.append({**_bind(it, spec), "source": "base", "set": cl.get("id"),
                          "severity": it.get("severity", "major")})
    for c in spec["preferentialConditions"]:
        if c["evidence"]["type"] == "input":
            items.append({"id": f"d-step-{c['id']}", "text": f"플로우에 '{c['condition']}' 증빙 입력 스텝이 존재한다",
                          "method": "rule", "target": "flow", "source": "derived", "set": "product-spec",
                          "rule": {"fn": "step_exists", "args": {"stepId": f"evidence-{c['id']}"}}, "severity": "critical"})
            label = c["evidence"].get("label") or f"{c['condition']} 증빙"
            items.append({"id": f"d-input-{c['id']}", "text": f"증빙 입력 화면에 '{label}' 입력 필드와 안내가 있다",
                          "method": "rule", "target": "screen", "source": "derived", "set": "product-spec",
                          "rule": {"fn": "text_present", "args": {"texts": [label], "steps": [f"evidence-{c['id']}"]}},
                          "severity": "major"})
        rate = _rate_text(c.get("rate"))
        texts = [c["condition"]] + ([rate] if rate else [])
        items.append({"id": f"d-rate-{c['id']}", "text": f"우대조건 '{c['condition']}'과 우대금리 {rate or '(미정)'}가 화면에 고지된다",
                      "method": "rule", "target": "screen", "source": "derived", "set": "product-spec",
                      "rule": {"fn": "text_present", "args": {"texts": texts, "steps": ["preferential", "confirm"], "any": True}},
                      "severity": "major"})
    for i, n in enumerate(spec["mandatoryNotices"], 1):
        items.append({"id": f"d-notice-{i}", "text": f"필수 고지 '{n}'가 약관 또는 확인 화면에 있다",
                      "method": "rule", "target": "screen", "source": "derived", "set": "product-spec",
                      "rule": {"fn": "text_present", "args": {"texts": [n], "steps": ["terms", "confirm"], "any": True}},
                      "severity": "critical"})
    for i, p in enumerate(spec["partners"], 1):
        items.append({"id": f"d-partner-{i}", "text": f"제휴사 '{p['name']}' 명칭이 상품 안내에 표기된다",
                      "method": "rule", "target": "screen", "source": "derived", "set": "product-spec",
                      "rule": {"fn": "text_present", "args": {"texts": [p["name"]], "steps": ["intro", "preferential"], "any": True}},
                      "severity": "minor"})
    return items
