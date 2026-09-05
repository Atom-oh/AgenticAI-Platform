"""수신 히어로 상품 시드 (스튜디오 DesignSpec 시연용) — 기존 노드·엣지는 그대로, 새 블록만 검증."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.store import LocalGraphStore  # noqa: E402

STORE = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


def _names(pairs):
    return [n.props.get("name") for _, n in pairs]


def test_football_product_has_preferential_condition_and_extra_step():
    p = STORE.get_node("PRD-DEP-001")
    assert p and p.props["category"] == "수신" and "축구" in p.props["name"]
    conds = [n for _, n in STORE.neighbors("PRD-DEP-001", rel="HAS_CONDITION")]
    pref = [c for c in conds if c.props["type"] == "우대"]
    assert len(pref) >= 1 and any("축구클럽" in c.props["name"] for c in pref)
    screens = _names(STORE.neighbors("PRD-DEP-001", rel="SOLD_VIA"))
    assert "축구클럽 인증" in screens and len(screens) == 7


def test_basic_product_has_no_preferential_condition_and_no_extra_step():
    conds = [n for _, n in STORE.neighbors("PRD-DEP-002", rel="HAS_CONDITION")]
    assert conds and all(c.props["type"] != "우대" for c in conds)
    screens = _names(STORE.neighbors("PRD-DEP-002", rel="SOLD_VIA"))
    assert "축구클럽 인증" not in screens and len(screens) == 6


def test_procedure_chain_is_ordered_by_screenmeta():
    proc = STORE.get_node("PRC-030")
    steps = json.loads(proc.props["steps"])
    assert steps[0] == "상품안내" and steps[3] == "축구클럽 인증" and steps[-1] == "가입 완료"
    first = STORE.get_node("SCR-DEP-001")
    sm = [n for _, n in STORE.neighbors("SCR-DEP-001", rel="DESCRIBES", direction="in")]
    assert len(sm) == 1 and json.loads(sm[0].props["prevScreens"]) == [] \
        and json.loads(sm[0].props["nextScreens"]) == ["SCR-DEP-002"]
    assert first.props["name"] == "상품안내"


def test_policy_rules_and_terms_attached():
    pol = [n for _, n in STORE.neighbors("SCR-DEP-004", rel="CONSTRAINS", direction="in")]
    assert any(n.props["ruleType"] == "동의" and n.props["severity"] == "HIGH" for n in pol)
    derived = [n.id for _, n in STORE.neighbors("POL-DEP-001", rel="DERIVED_FROM")]
    assert derived == ["REG-CS-003"]
    terms = [n for _, n in STORE.neighbors("SCR-DEP-001", rel="USED_IN", direction="in")]
    assert any(n.props["term"] == "적금 우대금리" for n in terms)


def test_existing_nodes_untouched():
    """새 블록은 뒤에 덧붙는다 — 기존 첫 노드·마지막 UX 노드 id가 그대로."""
    assert STORE.get_node("REG-LN-001") and STORE.get_node("TRM-0199")
    assert len(STORE.find_by_label("Product")) == 122
