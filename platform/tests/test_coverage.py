"""§4.3 커버리지 검증 + §4.1 볼륨 검증 + Semantic Layer 해석 검증.

실행: cd platform && python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.store import LocalGraphStore  # noqa: E402
from semantic.loader import SemanticLayer  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent.parent / "seed" / "out"
HERO_REGS = ["REG-LN-001", "REG-LN-014", "REG-CS-003"]


def _store() -> LocalGraphStore:
    return LocalGraphStore.from_seed_dir(SEED_DIR)


def test_volume_targets():
    """§4.1 노드 규모: 합계 약 3,300 / 관계 약 9,000."""
    s = _store().stats()
    by = s["by_label"]
    assert by["Regulation"] == 60
    assert by["RegulationAmendment"] == 25
    assert by["Product"] == 120
    assert by["Condition"] == 800
    assert by["Screen"] == 150
    assert by["Component"] == 80
    assert by["Department"] == 20
    assert by["Document"] == 200
    assert by["Template"] == 12
    assert by["Customer"] == 500
    assert by["Account"] == 1200
    assert by["Merchant"] == 150
    assert s["nodes"] >= 3300
    assert s["edges"] >= 8000, f"edges={s['edges']}"


def test_hero_coverage():
    """§4.3: 히어로 규정 3건이 요구 건수를 반환해야 한다."""
    store = _store()
    for code in HERO_REGS:
        r = store.impact_of_regulation(code)
        c = r.counts()
        assert r.regulation is not None, code
        assert c["products"] >= 4, f"{code} products={c['products']}"
        assert c["screens"] >= 6, f"{code} screens={c['screens']}"
        assert c["departments"] >= 3, f"{code} departments={c['departments']}"
        assert c["documents"] >= 5, f"{code} documents={c['documents']}"
        assert r.path_edges, "순회 경로가 비어 있으면 시각화가 불가능하다"


def test_component_version_chain():
    """S3 시연 전제: Button 버전 체인과 승인 상태가 존재한다."""
    store = _store()
    v2 = store.get_node("CMP-Button-v2")
    v3 = store.get_node("CMP-Button-v3")
    assert v2 and v3
    nexts = store.neighbors("CMP-Button-v2", "SUPERSEDED_BY")
    assert any(n.id == "CMP-Button-v3" for _, n in nexts)
    assert v3.props["approvalStatus"] == "APPROVED"


def test_no_real_identifiers():
    """§8: 개인 식별자는 토큰 형태만 존재한다 (주민번호 형식 금지)."""
    import json
    import re
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
    """§4.4: 한국어 별칭이 올바른 지표로 해석된다."""
    sl = SemanticLayer()
    m = sl.resolve("지난달 사용액")
    assert m and m.name == "전월실적"
    assert "date_trunc('month', now()) - interval '1 month'" in m.sql_template
    assert sl.resolve("LTV").name == "담보인정비율"
    assert sl.resolve("존재하지않는지표") is None
