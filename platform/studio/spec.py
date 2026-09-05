# platform/studio/spec.py
"""DesignSpec — 온톨로지(Product·Condition·Procedure·ScreenMeta·PolicyRule·UXTerm·Pattern)에서 검수 체크리스트를 만든다.

GraphStore 인터페이스만 사용한다(Local/Neptune 공통). 항목(Item)의 check 종류:
  llm  — 리뷰어 모델이 판정 (evidence·fix 반환)
  text — 문구 존재 (결정적)
  dom  — 구조 검사 (결정적, expect 에 규칙)
"""
from __future__ import annotations

import json
from pathlib import Path

BRAND_HEX = ["#008485", "#00615f", "#e90061", "#17332f"]
PREFERENTIAL_TYPES = {"우대"}
WEIGHT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
CATEGORY_TEMPLATE = {"수신": "deposit", "여신": "loan", "카드": "card", "외환": "fx"}
CHECKLIST_DIR = Path(__file__).resolve().parent / "checklists"
# ruleType → (문구, check, expect)
RULE_ITEM = {
    "고지의무": ("고지 문구가 화면에 존재한다", "llm", None),
    "동의": ("동의 컨트롤(체크박스 등)이 존재한다", "dom", {"control": "checkbox"}),
    "입력검증": ("입력 필드에 형식 힌트·검증 안내가 있다", "llm", None),
    "표기": ("명칭·표기가 규칙대로 통일되어 있다", "llm", None),
    "접근성": ("터치 타깃 44px 이상, 본문 13px 이상이다", "llm", None),
    "보안": ("세션·인증 관련 안내가 있다", "llm", None),
}
MAX_TERMS = 12
MAX_PATTERNS = 6


class SpecError(Exception):
    """상품을 찾지 못했거나 판매 화면이 없다."""


def _load_template(name: str) -> list[dict]:
    p = CHECKLIST_DIR / f"{name}.json"
    if not p.is_file():
        return []
    return [dict(i, expect=i.get("expect"), source=None) for i in json.loads(p.read_text(encoding="utf-8"))["items"]]


def _item(iid: str, category: str, text: str, *, required: bool, weight: int, check: str,
          expect: dict | None = None, source: dict | None = None) -> dict:
    return {"id": iid, "category": category, "text": text, "required": required, "weight": weight,
            "check": check, "expect": expect, "source": source}


def _src(node) -> dict:
    return {"nodeId": node.id, "label": node.label}


def _cond_name(c) -> str:
    p = c.props
    return p.get("name") or f"{p.get('type', '조건')} {p.get('operator', '')} {p.get('value', '')}{p.get('unit', '')}".strip()


def _ordered_screens(store, product) -> list:
    """SOLD_VIA 화면을 ScreenMeta prev/next 사슬로 정렬한다. 사슬이 없으면 엣지 순서."""
    screens = [n for _, n in store.neighbors(product.id, rel="SOLD_VIA")]
    if not screens:
        return []
    meta = {}
    for s in screens:
        for _, sm in store.neighbors(s.id, rel="DESCRIBES", direction="in"):
            meta[s.id] = sm
    ids = {s.id for s in screens}
    by_id = {s.id: s for s in screens}
    heads = [s for s in screens if s.id in meta and not [p for p in json.loads(meta[s.id].props.get("prevScreens", "[]")) if p in ids]]
    if len(heads) != 1:
        return screens
    chain, cur, seen = [], heads[0], set()
    while cur and cur.id not in seen:
        chain.append(cur)
        seen.add(cur.id)
        nxt = [n for n in json.loads(meta[cur.id].props.get("nextScreens", "[]")) if n in ids] if cur.id in meta else []
        cur = by_id.get(nxt[0]) if nxt else None
    return chain if len(chain) == len(screens) else screens


def build_spec(store, product_code: str, output_type: str = "design") -> dict:
    product = store.get_node(product_code)
    if not product or product.label != "Product":
        raise SpecError(f"상품을 찾을 수 없습니다: {product_code}")
    conds = [n for _, n in store.neighbors(product.id, rel="HAS_CONDITION")]
    screens = _ordered_screens(store, product)
    if not screens:
        raise SpecError(f"판매 화면(SOLD_VIA)이 없는 상품입니다: {product_code}")
    pref = [c for c in conds if c.props.get("type") in PREFERENTIAL_TYPES]
    has_pref = bool(pref)
    category = product.props.get("category", "")

    steps = []
    for s in screens:
        sm = next((m for _, m in store.neighbors(s.id, rel="DESCRIBES", direction="in")), None)
        steps.append({"screenId": s.id, "name": s.props.get("name", s.id),
                      "entryCondition": sm.props.get("entryCondition", "") if sm else ""})
    step_names = [st["name"] for st in steps]

    items: list[dict] = []
    # 1) 조건 → 명세반영
    for c in conds:
        items.append(_item(f"COND-{c.id}", "명세반영", f"조건 '{_cond_name(c)}'이(가) 화면에 표기된다",
                           required=c.props.get("type") in ("금리", "우대", "한도"), weight=3 if c.props.get("type") in ("금리", "우대") else 2,
                           check="llm", source=_src(c)))
    # 2) 흐름
    if has_pref:
        kws = []
        for c in pref:
            nm = _cond_name(c)
            kws += [nm] + [w for w in nm.replace("(", " ").replace(")", " ").split() if len(w) >= 2][:3]
        items.append(_item("FLOW-COND", "UX흐름", "우대 조건 입력·인증 스텝이 가입 흐름에 존재한다 (조건이 있으면 페이지가 하나 더 생겨야 한다)",
                           required=True, weight=3, check="dom", expect={"stepContainsAny": kws}, source=_src(pref[0])))
    else:
        items.append(_item("FLOW-NOCOND", "UX흐름", f"우대 조건이 없는 상품이므로 절차({len(steps)}단계) 밖의 추가 스텝이 없다",
                           required=True, weight=2, check="dom", expect={"maxSteps": len(steps)}, source=_src(product)))
    if output_type == "ux-flow":
        items.append(_item("STEP-ORDER", "UX흐름", "프레임 순서가 절차 단계 순서와 일치한다: " + " → ".join(step_names),
                           required=True, weight=3, check="dom", expect={"stepOrder": step_names}, source=_src(screens[0])))
        items.append(_item("STEP-MIN", "UX흐름", f"프레임이 {len(steps)}개 이상이다", required=True, weight=2, check="dom",
                           expect={"minSteps": len(steps)}, source=_src(screens[0])))
    else:
        items.append(_item("NAV-FLOW", "UX흐름", "이전/다음 화면으로의 이동(뒤로가기·다음 버튼)이 절차 순서와 맞는다: " + " → ".join(step_names),
                           required=False, weight=2, check="llm", source=_src(screens[0])))
    # 3) 정책
    seen_rules = set()
    for s in screens:
        for _, r in store.neighbors(s.id, rel="CONSTRAINS", direction="in"):
            if r.id in seen_rules or r.props.get("status", "ACTIVE") != "ACTIVE":
                continue
            seen_rules.add(r.id)
            text, check, expect = RULE_ITEM.get(r.props.get("ruleType", ""), ("정책 규칙을 준수한다", "llm", None))
            sev = r.props.get("severity", "MEDIUM")
            items.append(_item(f"POL-{r.id}", "정책", f"[{r.props.get('ruleType', '')}] {r.props.get('title', r.id)} — {text}",
                               required=sev == "HIGH", weight=WEIGHT.get(sev, 2), check=check, expect=expect, source=_src(r)))
    # 4) 용어
    terms, seen_terms = [], set()
    for s in screens:
        for _, t in store.neighbors(s.id, rel="USED_IN", direction="in"):
            term = t.props.get("term", "")
            if term and term not in seen_terms and len(terms) < MAX_TERMS:
                seen_terms.add(term)
                terms.append(term)
                items.append(_item(f"TERM-{t.id}", "용어", f"표준 용어 '{term}'을(를) 사용한다", required=False, weight=1,
                                   check="text", expect={"contains": term}, source=_src(t)))
    # 5) 패턴 (패턴 하나가 여러 화면에 걸려도 항목은 1건)
    seen_pat: set = set()
    for s in screens:
        for _, p in store.neighbors(s.id, rel="FOLLOWS"):
            if p.props.get("status") == "APPROVED" and p.id not in seen_pat and len(seen_pat) < MAX_PATTERNS:
                seen_pat.add(p.id)
                items.append(_item(f"PAT-{p.id}", "패턴", f"승인 패턴 '{p.props.get('name', p.id)}' 구조가 화면 '{s.props.get('name')}'에 나타난다",
                                   required=False, weight=1, check="llm", source=_src(p)))
    # 6) 템플릿
    items += _load_template("common")
    items += _load_template(CATEGORY_TEMPLATE.get(category, ""))
    if has_pref:
        items += _load_template("conditional")
    for it in items:
        if it["id"] == "CM-03":
            it["expect"] = {"brandHex": BRAND_HEX}

    return {"productCode": product.id, "productName": product.props.get("name", product.id), "category": category,
            "hasPreferential": has_pref, "outputType": output_type,
            "conditions": [{"id": c.id, "type": c.props.get("type", ""), "name": _cond_name(c)} for c in conds],
            "steps": steps, "terms": terms, "brandHex": BRAND_HEX, "items": items}


def list_products(store) -> list[dict]:
    """조건이 있고 판매 화면이 있는 상품만. 히어로(PRD-DEP-*) 먼저, 나머지는 조건 수 내림차순."""
    rows = []
    for p in store.find_by_label("Product"):
        conds = [n for _, n in store.neighbors(p.id, rel="HAS_CONDITION")]
        screens = [n for _, n in store.neighbors(p.id, rel="SOLD_VIA")]
        if not conds or not screens:
            continue
        rows.append({"code": p.id, "name": p.props.get("name", p.id), "category": p.props.get("category", ""),
                     "conditionCount": len(conds), "stepCount": len(screens),
                     "hasPreferential": any(c.props.get("type") in PREFERENTIAL_TYPES for c in conds)})
    rows.sort(key=lambda r: (0 if r["code"].startswith("PRD-DEP-") else 1, -r["conditionCount"], r["code"]))
    return rows[:40]
