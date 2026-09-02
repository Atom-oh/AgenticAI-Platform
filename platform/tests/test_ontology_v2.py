"""온톨로지 v2 (SPEC v2 §5-2 · §5-3 · §5-5) — UX 자산 도메인 스키마 · GraphStore v2 계약 · GraphRAG Context.

오프라인: seed/out 만 읽는다. Neptune 구현은 _q 를 가짜로 바꿔 질의 형태(라벨 지정 MATCH)와 빈 결과 처리를 검증한다.
실행: cd platform && python3 -m pytest tests/test_ontology_v2.py -q
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.store import ComponentImpact, Edge, ImpactResult, LocalGraphStore, NeptuneGraphStore, Node  # noqa: E402

SEED_DIR = ROOT / "seed" / "out"
HERO_REGS = ["REG-LN-001", "REG-LN-014", "REG-CS-003"]
HERO_COMPONENTS = ["CMP-Button-v2", "CMP-Button-v3", "CMP-Input-v3"]

ID_FORMAT = {
    "Pattern": (r"^PAT-\d{3}$", "patternId"), "Procedure": (r"^PRC-\d{3}$", "procedureId"),
    "PolicyRule": (r"^POL-\d{3}$", "ruleId"), "UXTerm": (r"^TRM-\d{4}$", "termId"),
    "ScreenMeta": (r"^SM-\d{3}$", None), "Screen": (r"^SCR-\d{3}$", "screenId"),
}
REQUIRED_PROPS = {
    "Pattern": {"patternId", "name", "category", "status"},
    "Procedure": {"procedureId", "name", "steps", "status"},
    "PolicyRule": {"ruleId", "title", "ruleType", "severity", "status"},
    "UXTerm": {"termId", "term", "definition", "category"},
    "ScreenMeta": {"screenNo", "purpose", "entryCondition", "prevScreens", "nextScreens"},
    "Component": {"componentId", "name", "version", "approvalStatus", "propsSchema", "owner"},
}
# (rel) → (src 라벨, dst 라벨) — §5-3 관계 방향 계약
REL_SCHEMA = {
    "FOLLOWS": {("Screen", "Pattern"), ("Document", "Template")},
    "COMPOSES": {("Pattern", "Component")}, "INCLUDES": {("Procedure", "Screen")},
    "CONSTRAINS": {("PolicyRule", "Screen")},
    "DERIVED_FROM": {("Condition", "Regulation"), ("PolicyRule", "Regulation")},
    "DESCRIBES": {("ScreenMeta", "Screen")}, "USED_IN": {("UXTerm", "Screen")},
    "USES": {("Screen", "Component")}, "SOLD_VIA": {("Product", "Screen")},
    "SUPERSEDED_BY": {("Component", "Component")},
}


@pytest.fixture(scope="module")
def store() -> LocalGraphStore:
    return LocalGraphStore.from_seed_dir(SEED_DIR)


@pytest.fixture(scope="module")
def raw():
    nodes = {}
    edges = []
    with open(SEED_DIR / "nodes.jsonl", encoding="utf-8") as f:
        for line in f:
            n = json.loads(line)
            nodes[n["id"]] = n
    with open(SEED_DIR / "edges.jsonl", encoding="utf-8") as f:
        for line in f:
            edges.append(json.loads(line))
    return nodes, edges


# ---------------- 스키마 ----------------
def test_new_labels_ids_and_props(raw):
    nodes, _ = raw
    for label, (pat, key) in ID_FORMAT.items():
        group = [n for n in nodes.values() if n["label"] == label]
        assert group, label
        for n in group:
            assert re.match(pat, n["id"]), (label, n["id"])
            if key:
                assert n["props"][key] == n["id"], (label, n["id"])
    for label, req in REQUIRED_PROPS.items():
        for n in (n for n in nodes.values() if n["label"] == label):
            missing = req - set(n["props"])
            assert not missing, f"{n['id']} 누락 속성 {missing}"
            assert all(n["props"][k] not in ("", None) for k in req), n["id"]


def test_screen_meta_one_per_screen(raw):
    nodes, edges = raw
    screens = {i for i, n in nodes.items() if n["label"] == "Screen"}
    described = defaultdict(list)
    for e in edges:
        if e["rel"] == "DESCRIBES":
            described[e["dst"]].append(e["src"])
    assert set(described) == screens and all(len(v) == 1 for v in described.values())
    for sm in (n for n in nodes.values() if n["label"] == "ScreenMeta"):
        assert sm["props"]["screenNo"] in screens
        prev, nxt = json.loads(sm["props"]["prevScreens"]), json.loads(sm["props"]["nextScreens"])
        assert isinstance(prev, list) and isinstance(nxt, list)
        assert set(prev) <= screens and set(nxt) <= screens
    # prev/next 는 Procedure.steps 순서에서 나온다 — 전세대출 절차의 2번째 화면은 1번째를 prev 로 가진다
    sm1 = next(n for n in nodes.values() if n["label"] == "ScreenMeta" and n["props"]["screenNo"] == "SCR-001")
    assert "SCR-000" in json.loads(sm1["props"]["prevScreens"])


def test_relationship_directions_follow_spec(raw):
    nodes, edges = raw
    seen_rels = set()
    for e in edges:
        if e["rel"] in REL_SCHEMA:
            pair = (nodes[e["src"]]["label"], nodes[e["dst"]]["label"])
            assert pair in REL_SCHEMA[e["rel"]], (e, pair)
            seen_rels.add(e["rel"])
    assert seen_rels == set(REL_SCHEMA), f"미사용 관계: {set(REL_SCHEMA) - seen_rels}"
    # 중복 엣지 없음 (같은 src-rel-dst 두 번 금지 — Related 카운트가 부풀지 않도록)
    keys = [(e["src"], e["rel"], e["dst"]) for e in edges]
    assert len(keys) == len(set(keys))


def test_policy_rules_bridge_both_domains(raw):
    """PolicyRule 은 반드시 Regulation 에서 파생되고 화면을 제약한다 (두 도메인 연결점)."""
    nodes, edges = raw
    derived = defaultdict(set)
    constrains = defaultdict(set)
    for e in edges:
        if e["rel"] == "DERIVED_FROM" and nodes[e["src"]]["label"] == "PolicyRule":
            derived[e["src"]].add(e["dst"])
        if e["rel"] == "CONSTRAINS":
            constrains[e["src"]].add(e["dst"])
    pols = [i for i, n in nodes.items() if n["label"] == "PolicyRule"]
    assert all(derived[p] and constrains[p] for p in pols)
    for reg in HERO_REGS:
        assert sum(1 for p in pols if reg in derived[p]) >= 3, reg


def test_component_owner_and_hero_ids_intact(store):
    for cid in ["CMP-Button-v2", "CMP-Button-v3", "CMP-Input-v3", "CMP-Card-v1"]:
        n = store.get_node(cid)
        assert n and n.label == "Component" and n.props["owner"], cid
    assert store.get_node("CMP-Button-v2").props["owner"] == "UI플랫폼팀"
    assert store.get_node("CMP-Button-v2").props["approvalStatus"] == "APPROVED"
    assert store.get_node("CMP-Button-v1").props["approvalStatus"] == "DEPRECATED"
    owners = {store.get_node(i).props["owner"] for i in store._by_label["Component"]}
    assert len(owners) >= 2


# ---------------- GraphStore v2 계약 ----------------
def test_impact_of_regulation_v2_screens_union(store, raw):
    """§5-5: screens = SOLD_VIA 화면 ∪ PolicyRule 제약 화면, components 는 그 화면 전체의 USES."""
    nodes, edges = raw
    out = defaultdict(lambda: defaultdict(set))
    inn = defaultdict(lambda: defaultdict(set))
    for e in edges:
        out[e["src"]][e["rel"]].add(e["dst"]); inn[e["dst"]][e["rel"]].add(e["src"])
    for code in HERO_REGS:
        r = store.impact_of_regulation(code)
        conds = {c for c in inn[code]["DERIVED_FROM"] if nodes[c]["label"] == "Condition"}
        pols = {c for c in inn[code]["DERIVED_FROM"] if nodes[c]["label"] == "PolicyRule"}
        prods = {p for c in conds for p in inn[c]["HAS_CONDITION"]}
        scr = {s for p in prods for s in out[p]["SOLD_VIA"]} | {s for p in pols for s in out[p]["CONSTRAINS"]}
        comps = {c for s in scr for c in out[s]["USES"]}
        depts = {d for x in prods | scr for d in out[x]["OWNED_BY"]}
        assert {n.id for n in r.policy_rules} == pols
        assert {n.id for n in r.screens} == scr
        assert {n.id for n in r.components} == comps
        assert {n.id for n in r.departments} == depts
        assert {n.id for n in r.documents} == set(inn[code]["REFERENCES"])
        assert r.counts()["policyRules"] == len(pols) and "policyRules" in r.counts()
        rels = {e.rel for e in r.path_edges}
        assert {"DERIVED_FROM", "CONSTRAINS", "HAS_CONDITION", "SOLD_VIA", "USES", "OWNED_BY", "REFERENCES"} <= rels
        # 경로 엣지는 전부 실제 엣지다
        real = {(e["src"], e["rel"], e["dst"]) for e in edges}
        assert all((e.src, e.rel, e.dst) in real for e in r.path_edges)
    assert store.impact_of_regulation("REG-NOPE").regulation is None


def test_impact_of_component_matches_manual_traversal(store, raw):
    nodes, edges = raw
    out = defaultdict(lambda: defaultdict(set))
    inn = defaultdict(lambda: defaultdict(set))
    for e in edges:
        out[e["src"]][e["rel"]].add(e["dst"]); inn[e["dst"]][e["rel"]].add(e["src"])
    for cid in HERO_COMPONENTS + ["CMP-Table-v2", "CMP-GEN-40"]:
        r = store.impact_of_component(cid)
        scr = set(inn[cid]["USES"])
        pats = {p for s in scr for p in out[s]["FOLLOWS"]} | set(inn[cid]["COMPOSES"])
        pols = {p for s in scr for p in inn[s]["CONSTRAINS"]}
        prods = {p for s in scr for p in inn[s]["SOLD_VIA"]}
        depts = {d for x in scr | prods for d in out[x]["OWNED_BY"]}
        procs = {p for s in scr for p in inn[s]["INCLUDES"]}
        assert isinstance(r, ComponentImpact) and r.component.id == cid
        assert {n.id for n in r.screens} == scr
        assert {n.id for n in r.patterns} == pats
        assert {n.id for n in r.policy_rules} == pols
        assert {n.id for n in r.products} == prods
        assert {n.id for n in r.departments} == depts
        assert {n.id for n in r.procedures} == procs
        assert set(r.counts()) == {"screens", "patterns", "policyRules", "products", "departments", "procedures"}
        real = {(e["src"], e["rel"], e["dst"]) for e in edges}
        assert all((e.src, e.rel, e.dst) in real for e in r.path_edges)
    empty = store.impact_of_component("CMP-NOPE")
    assert empty.component is None and empty.counts()["screens"] == 0 and empty.path_edges == []
    # 컴포넌트가 아닌 노드 ID 는 빈 결과
    assert store.impact_of_component("SCR-000").component is None


def test_related_counts_is_graph_derived(store, raw):
    """§8-2/§12.8: Related 카운트는 양방향 이웃을 라벨별로 센 값이다 — 원본 엣지와 대조."""
    nodes, edges = raw
    for nid in ["CMP-Button-v2", "SCR-000", "REG-LN-001", "PAT-000", "POL-000", "D-LNP"]:
        expected = defaultdict(set)
        for e in edges:
            if e["src"] == nid:
                expected[nodes[e["dst"]]["label"]].add(e["dst"])
            if e["dst"] == nid:
                expected[nodes[e["src"]]["label"]].add(e["src"])
        assert store.related_counts(nid) == {k: len(v) for k, v in expected.items()}, nid
    rc = store.related_counts("CMP-Button-v2")
    assert rc["Screen"] >= 12 and rc["Pattern"] >= 4 and rc["Component"] == 2  # v1(in) + v3(out)
    assert store.related_counts("NOPE") == {}


def test_version_chain_ordering_and_edges(store):
    for start in ("CMP-Button-v1", "CMP-Button-v2", "CMP-Button-v3"):
        assert [n.id for n in store.version_chain(start)] == ["CMP-Button-v1", "CMP-Button-v2", "CMP-Button-v3"]
    assert [n.id for n in store.version_chain("CMP-Card-v2")] == ["CMP-Card-v1", "CMP-Card-v2"]
    assert [n.id for n in store.version_chain("CMP-Badge-v1")] == ["CMP-Badge-v1"]
    assert store.version_chain("NOPE") == []
    # 순환이 있어도 끝난다 (§12.11)
    s = LocalGraphStore()
    s.upsert_nodes([Node("A", "Component"), Node("B", "Component")])
    s.upsert_edges([Edge("A", "SUPERSEDED_BY", "B"), Edge("B", "SUPERSEDED_BY", "A")])
    assert [n.id for n in s.version_chain("A")] == ["B", "A"] or [n.id for n in s.version_chain("A")] == ["A", "B"]


# ---------------- Neptune 구현: 질의 형태 (오프라인, _q 가짜) ----------------
def _assert_labeled(cy: str) -> None:
    """모든 노드 패턴은 라벨을 지정하거나(무라벨 MATCH 는 전수 스캔), 같은 질의 안에서 라벨과 함께 먼저 바인딩된 변수여야 한다."""
    labeled = set(re.findall(r"\((\w+):\w+", cy))
    for m in re.finditer(r"\((\w+)(?:\s*\{[^}]*\})?\)", cy):
        assert m.group(1) in labeled, f"무라벨 MATCH: {m.group(0)} in {cy}"


class _FakeNeptune(NeptuneGraphStore):
    def __init__(self, rows_for=None):
        super().__init__(endpoint="fake.local")
        self.queries = []
        self.rows_for = rows_for or (lambda cy, params: [])

    def _q(self, cypher, params=None):
        self.queries.append((cypher, params or {}))
        return self.rows_for(cypher, params or {})


def _nep_node(nid, label, **props):
    return {"~id": nid, "~labels": [label], "~properties": {"id": nid, **props}}


def test_neptune_component_impact_queries_are_labeled_and_bounded():
    def rows(cy, params):
        if cy.startswith("MATCH (n:Component)"):
            return [{"n": _nep_node("CMP-Button-v2", "Component", componentId="CMP-Button-v2", name="Button")}]
        if "RETURN DISTINCT s AS n" in cy:
            return [{"n": _nep_node("SCR-000", "Screen", screenId="SCR-000")}]
        if "RETURN DISTINCT s.id AS s, $nid AS d" in cy:
            return [{"s": "SCR-000", "d": "CMP-Button-v2"}]
        return []
    nep = _FakeNeptune(rows)
    r = nep.impact_of_component("CMP-Button-v2")
    assert r.component.id == "CMP-Button-v2" and [n.id for n in r.screens] == ["SCR-000"]
    assert r.path_edges == [Edge("SCR-000", "USES", "CMP-Button-v2")]
    assert r.counts()["screens"] == 1
    for cy, params in nep.queries:
        assert "LIMIT" in cy, cy
        _assert_labeled(cy)
        assert "componentId" in params or "cid" in params
    # 없는 컴포넌트 → 빈 결과, 추가 질의 없음
    nep2 = _FakeNeptune()
    assert nep2.impact_of_component("CMP-NOPE").component is None and len(nep2.queries) == 1


def test_neptune_regulation_impact_v2_includes_policy_path():
    def rows(cy, params):
        if cy.startswith("MATCH (n:Regulation)"):
            return [{"n": _nep_node("REG-LN-001", "Regulation", code="REG-LN-001", title="t")}]
        if "(n:PolicyRule) RETURN" in cy:
            return [{"n": _nep_node("POL-000", "PolicyRule", ruleId="POL-000")}]
        if "(pol:PolicyRule) RETURN DISTINCT pol.id AS s, $c2 AS d" in cy:
            return [{"s": "POL-000", "d": "REG-LN-001"}]
        if "-[:CONSTRAINS]->(s:Screen) RETURN DISTINCT pol.id AS s, s.id AS d" in cy:
            return [{"s": "POL-000", "d": "SCR-003"}]
        if "-[:CONSTRAINS]->(s:Screen) RETURN DISTINCT s AS n" in cy:
            return [{"n": _nep_node("SCR-003", "Screen", screenId="SCR-003")}]
        return []
    nep = _FakeNeptune(rows)
    r = nep.impact_of_regulation("REG-LN-001")
    assert isinstance(r, ImpactResult) and r.counts()["policyRules"] == 1
    assert [n.id for n in r.screens] == ["SCR-003"]
    rels = {(e.src, e.rel, e.dst) for e in r.path_edges}
    assert ("POL-000", "DERIVED_FROM", "REG-LN-001") in rels and ("POL-000", "CONSTRAINS", "SCR-003") in rels
    for cy, _ in nep.queries:
        assert "LIMIT" in cy and ":Regulation" in cy, cy
        _assert_labeled(cy)


def test_neptune_related_counts_and_version_chain_offline():
    def rows(cy, params):
        if cy.startswith("MATCH (n {id: $id})"):
            nid = params["id"]
            return [{"n": _nep_node(nid, "Component", componentId=nid)}] if nid.startswith("CMP-") else []
        if "head(labels(m))" in cy:
            return [{"label": "Screen", "c": 21}, {"label": "Pattern", "c": 11}]
        if "-[r:SUPERSEDED_BY]->(b)" in cy:
            return [{"rel": "SUPERSEDED_BY", "b": _nep_node("CMP-X-v2", "Component")}] if params["id"] == "CMP-X-v1" else []
        if "<-[r:SUPERSEDED_BY]-(b)" in cy:
            return []
        return []
    nep = _FakeNeptune(rows)
    assert nep.related_counts("CMP-X-v1") == {"Pattern": 11, "Screen": 21}
    assert nep.related_counts("NOPE") == {}
    assert [n.id for n in nep.version_chain("CMP-X-v1")] == ["CMP-X-v1", "CMP-X-v2"]
    assert any(":Component {id: $id}" in cy for cy, _ in nep.queries)


# ---------------- GraphRAG Context (Bedrock 호출 없음) ----------------
@pytest.fixture
def graphrag_offline(monkeypatch):
    """engine.bedrock 을 스텁으로 바꾼 채 graphrag 를 새로 import 한다 (Bedrock 호출 시 즉시 실패).

    다른 테스트 모듈이 실제 engine.bedrock 을 이미 import 했더라도 패키지 속성·sys.modules 를 함께 바꿔
    graphrag 가 스텁을 물도록 하고, 끝나면 graphrag 캐시를 비워 다음 import 가 새로 로드되게 한다."""
    import importlib
    import types
    import engine

    def _no_call(*a, **k):
        raise AssertionError("오프라인 테스트에서 Bedrock 호출 금지")
    stub = types.ModuleType("engine.bedrock")
    stub.generate = stub.embed = stub.generate_stream = _no_call
    monkeypatch.setitem(sys.modules, "engine.bedrock", stub)
    monkeypatch.setattr(engine, "bedrock", stub, raising=False)
    sys.modules.pop("engine.graphrag", None)
    if hasattr(engine, "graphrag"):
        delattr(engine, "graphrag")
    graphrag = importlib.import_module("engine.graphrag")
    assert graphrag.bedrock is stub
    yield graphrag
    sys.modules.pop("engine.graphrag", None)
    if hasattr(engine, "graphrag"):
        delattr(engine, "graphrag")


def test_graphrag_context_and_graph_include_policy_rules(store, graphrag_offline):
    graphrag = graphrag_offline
    r = store.impact_of_regulation("REG-LN-001")
    ctx = graphrag._assemble_context(r)
    assert f"[정책 규칙 {len(r.policy_rules)}건]" in ctx
    assert "POL-000 · 전세대출 담보 인정 범위 고지 문구 필수 · 고지의무" in ctx
    assert "(정책규칙 제약)" in ctx and "[영향 컴포넌트" in ctx
    g = graphrag._graph_payload(r)
    labels = {n["label"] for n in g["nodes"]}
    assert "PolicyRule" in labels and {"Regulation", "Product", "Screen", "Component", "Department", "Document"} <= labels
    rels = {e["rel"] for e in g["edges"]}
    assert {"CONSTRAINS", "DERIVED_FROM", "SOLD_VIA", "USES"} <= rels
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in g["edges"]) and len(g["edges"]) <= 400
    assert len({(e["src"], e["rel"], e["dst"]) for e in g["edges"]}) == len(g["edges"])
    # 뼈대(정책규칙→규정, 정책규칙→화면)가 400건 상한 안에 살아남는다 — 히어로 3건 모두
    for code in HERO_REGS:
        gg = graphrag._graph_payload(store.impact_of_regulation(code))
        assert any(e["rel"] == "CONSTRAINS" for e in gg["edges"]), code
        assert gg["nodes"][0]["label"] == "Regulation" and gg["nodes"][1]["label"] == "PolicyRule"
    assert "컴포넌트" in graphrag.GENERATE_SYSTEM and "정책규칙" in graphrag.GENERATE_SYSTEM
    assert graphrag.ID_PATTERN.findall("POL-000 PAT-001 CMP-Button-v2 D-LNP REG-LN-001") == \
        ["POL-000", "PAT-001", "CMP-Button-v2", "D-LNP", "REG-LN-001"]
    assert "POL-000" in graphrag._valid_ids(r) and "SCR-003" in graphrag._valid_ids(r)


# ---------------- 표기 규칙 (SPEC v2 §12.13) — 이 슬라이스가 소유한 파일 ----------------
def test_owned_files_use_v2_terminology():
    banned = ["온" + "프렘", "On-" + "Premises", "Two-" + "Plane", "In-" + "Region", "서울을 " + "벗어나지 않"]
    for rel in ["seed/generate.py", "seed/corpus.py", "schema/ontology.cypher", "graph/store.py",
                "engine/graphrag.py", "cli.py", "tests/test_coverage.py", "tests/test_ontology_v2.py"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for b in banned:
            assert b not in text, f"{rel}: 금지 표현 '{b}'"
