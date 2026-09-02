"""SPEC v2 §5-4 커버리지 검증 + §5-1/§5-2 볼륨 검증 + §9 식별자 검증 + Semantic Layer 해석 검증.

실행: cd platform && python3 -m pytest tests/ -q  (오프라인 — seed/out 만 읽는다)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.store import LocalGraphStore  # noqa: E402
from semantic.loader import SemanticLayer  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent.parent / "seed" / "out"
HERO_REGS = ["REG-LN-001", "REG-LN-014", "REG-CS-003"]
HERO_COMPONENTS = ["CMP-Button-v2", "CMP-Button-v3", "CMP-Input-v3"]

# §5-1 여신 도메인 + §5-2 UX 자산 도메인 목표 건수 (정확히 일치해야 한다 — Registry 시드가 Component 80건을 읽는다)
LABEL_TARGETS = {
    "Regulation": 60, "RegulationAmendment": 25, "Product": 120, "Condition": 800,
    "Department": 20, "Document": 200, "Template": 12, "Customer": 500, "Account": 1200, "Merchant": 150,
    "Screen": 150, "Component": 80, "Pattern": 40, "Procedure": 30, "PolicyRule": 60, "UXTerm": 200,
    "ScreenMeta": 150,
}


def _store() -> LocalGraphStore:
    return LocalGraphStore.from_seed_dir(SEED_DIR)


def test_volume_targets():
    """§5-2 합계 목표: 노드 약 3,800 / 관계 약 11,000 (라벨별 건수는 정확 일치)."""
    s = _store().stats()
    by = s["by_label"]
    for label, n in LABEL_TARGETS.items():
        assert by.get(label) == n, f"{label}: {by.get(label)} != {n}"
    assert set(by) == set(LABEL_TARGETS), f"예상 밖 라벨: {set(by) ^ set(LABEL_TARGETS)}"
    assert s["nodes"] == sum(LABEL_TARGETS.values()) == 3797
    assert 10500 <= s["edges"] <= 11500, f"edges={s['edges']}"


def test_hero_regulation_coverage():
    """§5-4 첫 제약: 히어로 규정 3건이 products>=4, screens>=6, components>=8, departments>=3, documents>=5."""
    store = _store()
    for code in HERO_REGS:
        r = store.impact_of_regulation(code)
        c = r.counts()
        assert r.regulation is not None, code
        assert c["products"] >= 4, f"{code} products={c['products']}"
        assert c["screens"] >= 6, f"{code} screens={c['screens']}"
        assert c["components"] >= 8, f"{code} components={c['components']}"
        assert c["departments"] >= 3, f"{code} departments={c['departments']}"
        assert c["documents"] >= 5, f"{code} documents={c['documents']}"
        # v2: 규정 → 정책규칙 → 화면 경로가 실제로 존재한다 (두 도메인 연결점)
        assert c["policyRules"] >= 2, f"{code} policyRules={c['policyRules']}"
        assert any(e.rel == "CONSTRAINS" for e in r.path_edges), f"{code}: CONSTRAINS 경로 없음"
        assert r.path_edges, "순회 경로가 비어 있으면 시각화가 불가능하다"


def test_hero_component_coverage():
    """§5-4 두 번째 제약: 히어로 컴포넌트 3건이 screens>=12, patterns>=4, policyRules>=2."""
    store = _store()
    for cid in HERO_COMPONENTS:
        r = store.impact_of_component(cid)
        c = r.counts()
        assert r.component is not None and r.component.id == cid, cid
        assert c["screens"] >= 12, f"{cid} screens={c['screens']}"
        assert c["patterns"] >= 4, f"{cid} patterns={c['patterns']}"
        assert c["policyRules"] >= 2, f"{cid} policyRules={c['policyRules']}"
        assert r.path_edges and any(e.rel == "USES" for e in r.path_edges), cid


def test_component_version_chain():
    """S3 시연 전제: Button 버전 사슬과 승인 상태가 존재한다 (Registry 시드가 이 ID를 읽는다)."""
    store = _store()
    v2 = store.get_node("CMP-Button-v2")
    v3 = store.get_node("CMP-Button-v3")
    assert v2 and v3
    nexts = store.neighbors("CMP-Button-v2", "SUPERSEDED_BY")
    assert any(n.id == "CMP-Button-v3" for _, n in nexts)
    assert v3.props["approvalStatus"] == "APPROVED"
    assert [n.id for n in store.version_chain("CMP-Button-v2")] == ["CMP-Button-v1", "CMP-Button-v2", "CMP-Button-v3"]


def test_no_real_identifiers():
    """§9: 개인 식별자는 토큰 형태만 존재한다 (주민번호 형식 금지) — UX 자산 노드 포함 전체 파일."""
    ssn_like = re.compile(r"\d{6}-?\d{7}")
    with open(SEED_DIR / "nodes.jsonl", encoding="utf-8") as f:
        for line in f:
            assert not ssn_like.search(line), "주민번호 형식 데이터 발견"
            n = json.loads(line)
            if n["label"] == "Customer":
                assert n["props"]["customerId"].startswith("CUST-")
            if n["label"] == "Account":
                assert n["props"]["accountId"].startswith("ACCT-")


def test_semantic_layer_resolution():
    """§6: 한국어 별칭이 올바른 지표로 해석된다."""
    sl = SemanticLayer()
    m = sl.resolve("지난달 사용액")
    assert m and m.name == "전월실적"
    assert "date_trunc('month', now()) - interval '1 month'" in m.sql_template
    assert sl.resolve("LTV").name == "담보인정비율"
    assert sl.resolve("존재하지않는지표") is None
