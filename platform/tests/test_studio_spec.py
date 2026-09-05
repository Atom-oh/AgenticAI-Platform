# platform/tests/test_studio_spec.py
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.store import LocalGraphStore  # noqa: E402
from studio import spec  # noqa: E402

STORE = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


def _by_id(s, item_id):
    return next(i for i in s["items"] if i["id"] == item_id)


def test_football_spec_requires_condition_step():
    s = spec.build_spec(STORE, "PRD-DEP-001", "ux-flow")
    assert s["hasPreferential"] is True and s["category"] == "수신"
    flow = _by_id(s, "FLOW-COND")
    assert flow["required"] and flow["check"] == "dom" and "축구클럽" in flow["expect"]["stepContainsAny"][0]
    assert [st["name"] for st in s["steps"]][3] == "축구클럽 인증"
    order = _by_id(s, "STEP-ORDER")
    assert order["expect"]["stepOrder"] == [st["name"] for st in s["steps"]]
    assert not any(i["id"] == "FLOW-NOCOND" for i in s["items"])


def test_basic_spec_forbids_extra_step():
    s = spec.build_spec(STORE, "PRD-DEP-002", "ux-flow")
    assert s["hasPreferential"] is False
    assert _by_id(s, "FLOW-NOCOND")["expect"] == {"maxSteps": 6}
    assert not any(i["id"] == "FLOW-COND" for i in s["items"])
    assert not any(i["id"].startswith("CND-") and i["category"] == "조건형" for i in s["items"])


def test_conditions_terms_policies_become_items():
    s = spec.build_spec(STORE, "PRD-DEP-001", "design")
    cond_items = [i for i in s["items"] if i["id"].startswith("COND-")]
    assert len(cond_items) == 5 and all(i["check"] == "llm" for i in cond_items)
    assert any("축구클럽" in i["text"] for i in cond_items)
    pol = _by_id(s, "POL-POL-DEP-001")
    assert pol["required"] and pol["weight"] == 3 and pol["check"] == "dom" and pol["expect"] == {"control": "checkbox"}
    terms = [i for i in s["items"] if i["id"].startswith("TERM-")]
    assert any(i["expect"] == {"contains": "적금 우대금리"} for i in terms) and all(i["check"] == "text" for i in terms)
    # design(단일 화면) 에서는 STEP-ORDER 대신 내비 항목
    assert not any(i["id"] == "STEP-ORDER" for i in s["items"])
    assert any(i["id"] == "NAV-FLOW" for i in s["items"])


def test_templates_and_conditional_addon():
    s = spec.build_spec(STORE, "PRD-DEP-001")
    ids = {i["id"] for i in s["items"]}
    assert "CM-01" in ids and "DEP-01" in ids and "CD-01" in ids
    s2 = spec.build_spec(STORE, "PRD-DEP-002")
    ids2 = {i["id"] for i in s2["items"]}
    assert "CD-01" not in ids2 and "DEP-01" in ids2
    assert all(len({i["id"] for i in x["items"]}) == len(x["items"]) for x in (s, s2)), "item id 중복"


def test_unknown_product_raises():
    with pytest.raises(spec.SpecError):
        spec.build_spec(STORE, "PRD-NOPE")


def test_list_products_marks_preferential():
    rows = spec.list_products(STORE)
    fb = next(r for r in rows if r["code"] == "PRD-DEP-001")
    assert fb["hasPreferential"] and fb["conditionCount"] == 5 and fb["stepCount"] == 7
    assert rows[0]["code"] == "PRD-DEP-001", "히어로 상품이 먼저"
