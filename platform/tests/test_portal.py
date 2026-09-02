"""UX Asset Portal (SPEC v2 §8-2 · §7 · §12.8) — 카테고리 매핑 · Related 카운트(그래프 순회와 일치) · 영향 분석 ·
Publish(DRAFT 레코드) · Sync · §7 MCP 레코드 시드 · Neptune 배치 질의 형태 · 용어 규칙.

오프라인: seed/out(LocalGraphStore) + 인메모리 Registry 페이크, REGISTRY_EMBED=0. 숫자는 전부 원본 엣지/스토어에서 다시 계산한다.
실행: cd platform && python3 -m pytest tests/test_portal.py -q
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ.pop("REGISTRY_TABLE", None)
os.environ["REGISTRY_EMBED"] = "0"
os.environ["GRAPH_BACKEND"] = "local"

from common.ctx import Ctx  # noqa: E402
from graph.store import LocalGraphStore, NeptuneGraphStore  # noqa: E402
from handlers import core as core_mod  # noqa: E402
from handlers import portal  # noqa: E402
from registry import api as registry_api  # noqa: E402
from registry import seed as seedmod  # noqa: E402

SEED_DIR = ROOT / "seed" / "out"
ACTOR = "demo@atomai.click"
HERO = "CMP-Button-v2"


class _FakeApigw:
    def __init__(self) -> None:
        self.posted = []

    def post_to_connection(self, ConnectionId, Data):
        self.posted.append(json.loads(Data.decode()))


def _ctx():
    gw = _FakeApigw()
    return Ctx(apigw=gw, conn_id="c1", email=ACTOR, rid="r1"), gw


def _call(fn, body: dict) -> dict:
    ctx, gw = _ctx()
    fn(ctx, body)
    assert len(gw.posted) == 1
    return gw.posted[-1]


@pytest.fixture(scope="module")
def store() -> LocalGraphStore:
    s = LocalGraphStore.from_seed_dir(SEED_DIR)
    core_mod._store = s  # handlers.core.lazy_store() 가 이 스토어를 돌려준다
    yield s
    core_mod._store = None


@pytest.fixture(scope="module")
def raw():
    nodes, edges = {}, []
    with open(SEED_DIR / "nodes.jsonl", encoding="utf-8") as f:
        for line in f:
            n = json.loads(line)
            nodes[n["id"]] = n
    with open(SEED_DIR / "edges.jsonl", encoding="utf-8") as f:
        for line in f:
            edges.append(json.loads(line))
    return nodes, edges


@pytest.fixture(autouse=True)
def fresh_registry():
    yield registry_api.reset_for_tests()


# ---------------- 라우트 · 카테고리 매핑 ----------------
def test_routes():
    assert set(portal.ROUTES) == {"portal_list", "portal_detail", "portal_impact", "portal_publish", "portal_sync",
                                  "portal_registry_map"}
    assert set(portal.CATEGORIES) == {"Foundation", "Components", "Patterns", "Screens", "Procedures", "Policies", "UXWriting"}


@pytest.mark.parametrize("category,label,flt", [
    ("Foundation", "UXTerm", {"category": "공통"}), ("Components", "Component", {}), ("Patterns", "Pattern", {}),
    ("Screens", "Screen", {}), ("Procedures", "Procedure", {}), ("Policies", "PolicyRule", {}), ("UXWriting", "UXTerm", {}),
])
def test_category_maps_to_label(store, category, label, flt):
    ev = _call(portal.portal_list, {"category": category})
    assert ev["ok"] and ev["type"] == "portal_list" and ev["label"] == label and ev["computedBy"] == "graph-traversal"
    expected = sorted(n.id for n in store.find_by_label(label, **flt))
    assert [c["id"] for c in ev["cards"]] == expected and ev["count"] == len(expected) > 0
    assert all(c["label"] == label for c in ev["cards"])
    assert ev["categoryCounts"][category] == len(expected)
    assert ev["backend"] == "local"
    if category == "Foundation":
        assert "미구현" in ev["note"] and all(c["termCategory"] == "공통" for c in ev["cards"])


def test_unknown_category_rejected(store):
    ev = _call(portal.portal_list, {"category": "Tokens"})
    assert ev["ok"] is False and ev["code"] == 400


def test_card_shape_and_status_mapping(store):
    for cat in ("Components", "Policies", "Screens", "Patterns"):
        for c in _call(portal.portal_list, {"category": cat})["cards"]:
            assert set(c) >= {"id", "name", "status", "rawStatus", "version", "owner", "related", "computedBy", "brief"}
            assert c["status"] in ("APPROVED", "DRAFT", "DEPRECATED")
            n = store.get_node(c["id"])
            raw = n.props.get("approvalStatus", n.props.get("status"))
            assert c["rawStatus"] == raw
            assert c["status"] == portal.map_status(raw)
    # 매핑 규칙 — 대기/미정 상태는 DRAFT, ACTIVE/LIVE 는 APPROVED, RETIRED 는 DEPRECATED
    assert portal.map_status("PENDING_APPROVAL") == "DRAFT" and portal.map_status(None) == "DRAFT"
    assert portal.map_status("ACTIVE") == portal.map_status("LIVE") == "APPROVED"
    assert portal.map_status("RETIRED") == portal.map_status("DEPRECATED") == "DEPRECATED"
    comps = {c["id"]: c for c in _call(portal.portal_list, {"category": "Components"})["cards"]}
    assert comps[HERO]["status"] == "APPROVED" and comps[HERO]["version"] == "2.0.0" and comps[HERO]["owner"] == "UI플랫폼팀"
    assert comps["CMP-Button-v1"]["status"] == "DEPRECATED"
    pending = [c for c in comps.values() if c["rawStatus"] == "PENDING_APPROVAL"]
    assert pending and all(c["status"] == "DRAFT" for c in pending)


# ---------------- Related 카운트 = 실제 이웃 수 (§12.8) ----------------
def test_related_counts_equal_actual_neighbors(store, raw):
    nodes, edges = raw
    for cat in ("Components", "Patterns", "Policies", "Foundation"):
        for c in _call(portal.portal_list, {"category": cat})["cards"]:
            assert c["related"] == store.related_counts(c["id"]), c["id"]
            expected = defaultdict(set)
            for e in edges:
                if e["src"] == c["id"]:
                    expected[nodes[e["dst"]]["label"]].add(e["dst"])
                if e["dst"] == c["id"]:
                    expected[nodes[e["src"]]["label"]].add(e["src"])
            assert c["related"] == {k: len(v) for k, v in expected.items()}, c["id"]
            assert c["computedBy"] == "graph-traversal"
    # 히어로 컴포넌트는 §5-4 커버리지를 카드에서도 보여야 한다
    hero = next(c for c in _call(portal.portal_list, {"category": "Components"})["cards"] if c["id"] == HERO)
    assert hero["related"]["Screen"] >= 12 and hero["related"]["Pattern"] >= 4


def test_screens_join_screen_meta_and_owner(store):
    ev = _call(portal.portal_list, {"category": "Screens"})
    for c in ev["cards"]:
        metas = [m for _, m in store.neighbors(c["id"], "DESCRIBES", direction="in")]
        assert len(metas) == 1 and c["meta"]["metaId"] == metas[0].id
        assert c["meta"]["purpose"] == metas[0].props["purpose"]
        assert isinstance(c["meta"]["prevScreens"], list) and isinstance(c["meta"]["nextScreens"], list)
        depts = [d for _, d in store.neighbors(c["id"], "OWNED_BY")]
        assert c["owner"] == depts[0].props["name"]
        assert c["status"] == "APPROVED" and c["rawStatus"] == "LIVE"


# ---------------- 상세 ----------------
def test_detail_component_with_registry(store):
    seedmod.seed(ACTOR)
    ev = _call(portal.portal_detail, {"id": HERO})
    assert ev["ok"] and ev["id"] == HERO and ev["category"] == "Components"
    assert ev["related"] == store.related_counts(HERO)
    assert [c["id"] for c in ev["versionChain"]] == [n.id for n in store.version_chain(HERO)] == \
        ["CMP-Button-v1", "CMP-Button-v2", "CMP-Button-v3"]
    assert [c["current"] for c in ev["versionChain"]] == [False, True, False]
    assert ev["props"]["propsSchema"]["variant"] == ["primary", "ghost"]  # JSON 문자열 → 객체
    assert ev["impactSupported"] is True and ev["publishable"] is True
    assert ev["publishTarget"] == {"recordType": "CUSTOM", "subtype": "COMPONENT"}
    reg = ev["registry"]
    assert reg["available"] and reg["tier"] == "Tier 0/1 전용" and reg["name"] == "Button" and reg["recordVersion"] == "v2"
    assert reg["record"]["status"] == "APPROVED" and reg["record"]["recordType"] == "CUSTOM"
    assert ev["mapping"]["recordType"] == "SKILL" and ev["mapping"]["deviation"] is True  # §7 과의 차이를 숨기지 않는다
    rels = {(x["rel"], x["direction"]) for x in ev["neighbors"]}
    assert ("USES", "in") in rels and ("SUPERSEDED_BY", "out") in rels
    for grp in ev["neighbors"]:
        assert grp["count"] == len(store.neighbors(HERO, grp["rel"], direction=grp["direction"]))
        assert len(grp["nodes"]) <= 8


def test_detail_screen_meta_and_missing(store):
    ev = _call(portal.portal_detail, {"id": "SCR-001"})
    assert ev["ok"] and ev["meta"]["purpose"] and "SCR-000" in ev["meta"]["prevScreens"]
    assert ev["owner"] and ev["registry"] is None and ev["impactSupported"] is False and ev["publishable"] is True
    ev2 = _call(portal.portal_detail, {"id": "TRM-0000"})
    assert ev2["ok"] and ev2["category"] == "UXWriting" and ev2["publishable"] is False and ev2["impactSupported"] is False
    assert _call(portal.portal_detail, {"id": "NOPE-1"})["code"] == 404
    assert _call(portal.portal_detail, {})["ok"] is False


# ---------------- 영향 분석 ----------------
def test_impact_component_matches_store(store):
    ev = _call(portal.portal_impact, {"id": HERO})
    r = store.impact_of_component(HERO)
    assert ev["ok"] and ev["label"] == "Component" and ev["traversal"] == "GraphStore.impact_of_component"
    for k in ("screens", "patterns", "policyRules", "products", "departments", "procedures"):
        assert ev["counts"][k] == r.counts()[k] == len(ev[k])
    assert ev["counts"]["screens"] >= 12 and ev["counts"]["patterns"] >= 4 and ev["counts"]["policyRules"] >= 2
    assert {s["id"] for s in ev["screens"]} == {n.id for n in r.screens}
    assert all(s.get("channel") for s in ev["screens"])
    g = ev["graph"]
    ids = {n["id"] for n in g["nodes"]}
    assert g["nodes"][0]["id"] == HERO and {s.id for s in r.screens} <= ids
    assert all(e["src"] in ids and e["dst"] in ids for e in g["edges"])
    real = {(e.src, e.rel, e.dst) for e in r.path_edges}
    assert all((e["src"], e["rel"], e["dst"]) in real for e in g["edges"])
    assert len({(e["src"], e["rel"], e["dst"]) for e in g["edges"]}) == len(g["edges"]) <= 400
    assert len(g["nodes"]) <= 110 and g["truncated"]["nodes"] == max(len({n.id for n in [r.component] + r.screens + r.patterns
                                                                            + r.policy_rules + r.departments + r.products
                                                                            + r.procedures}) - 110, 0)
    assert g["edges"][0]["rel"] == "USES"  # 뼈대(화면→컴포넌트)가 먼저


def test_impact_pattern_and_policy_manual_traversal(store, raw):
    nodes, edges = raw
    out = defaultdict(lambda: defaultdict(set))
    inn = defaultdict(lambda: defaultdict(set))
    for e in edges:
        out[e["src"]][e["rel"]].add(e["dst"]); inn[e["dst"]][e["rel"]].add(e["src"])
    for pid in ("PAT-001", "PAT-005"):
        ev = _call(portal.portal_impact, {"id": pid})
        scr = set(inn[pid]["FOLLOWS"])
        assert ev["ok"] and {s["id"] for s in ev["screens"]} == scr and ev["counts"]["screens"] == len(scr)
        assert {c["id"] for c in ev["components"]} == set(out[pid]["COMPOSES"]) | {c for s in scr for c in out[s]["USES"]}
        assert {p["id"] for p in ev["policyRules"]} == {p for s in scr for p in inn[s]["CONSTRAINS"]}
        assert {d["id"] for d in ev["departments"]} == {d for s in scr for d in out[s]["OWNED_BY"]}
        assert {p["id"] for p in ev["procedures"]} == {p for s in scr for p in inn[s]["INCLUDES"]}
        real = {(e["src"], e["rel"], e["dst"]) for e in edges}
        assert all((e["src"], e["rel"], e["dst"]) in real for e in ev["graph"]["edges"])
    for pol in ("POL-000", "POL-007"):
        ev = _call(portal.portal_impact, {"id": pol})
        scr = set(out[pol]["CONSTRAINS"])
        assert ev["ok"] and {s["id"] for s in ev["screens"]} == scr
        assert {c["id"] for c in ev["components"]} == {c for s in scr for c in out[s]["USES"]}
        assert {p["id"] for p in ev["patterns"]} == {p for s in scr for p in out[s]["FOLLOWS"]}
        assert {r["id"] for r in ev["regulations"]} == set(out[pol]["DERIVED_FROM"])
    # 지원하지 않는 라벨 · 없는 ID
    ev = _call(portal.portal_impact, {"id": "SCR-000"})
    assert ev["ok"] is False and ev["code"] == 400
    assert _call(portal.portal_impact, {"id": "NOPE"})["code"] == 404


# ---------------- Publish (DRAFT) · Sync ----------------
def test_publish_pattern_and_screen_create_draft_records(store):
    ev = _call(portal.portal_publish, {"id": "PAT-001"})
    assert ev["ok"] and ev["action"] == "created" and ev["tier"] == "Tier 0/1 전용"
    rec = ev["record"]
    assert rec["name"] == "PAT-001" and rec["recordVersion"] == "v1" and rec["status"] == "DRAFT"
    assert rec["recordType"] == "CUSTOM" and rec["subtype"] == "PATTERN" and rec["updatedBy"] == ACTOR
    assert rec["payload"]["related"] == store.related_counts("PAT-001")
    assert set(rec["payload"]["composes"]) == {c.id for _, c in store.neighbors("PAT-001", "COMPOSES")}
    assert registry_api.get_record("PAT-001", "v1")["status"] == "DRAFT"
    assert registry_api.audit_trail("PAT-001", "v1")[0]["transition"] == "create"
    # Consumer API 에는 보이지 않는다 (DRAFT)
    assert not [r for r in registry_api.list_approved("CUSTOM", "PATTERN")]
    # 멱등: 두 번째 발행은 기존 레코드를 돌려주고 새로 만들지 않는다
    ev2 = _call(portal.portal_publish, {"id": "PAT-001"})
    assert ev2["ok"] and ev2["action"] == "existing" and ev2["record"]["createdAt"] == rec["createdAt"]
    assert len(registry_api.list_records({"q": "PAT-001"})) == 1
    # 화면 스펙
    ev3 = _call(portal.portal_publish, {"id": "SCR-001"})
    r3 = ev3["record"]
    assert ev3["action"] == "created" and r3["recordType"] == "CUSTOM" and r3["subtype"] == "SCREEN_SPEC" and r3["status"] == "DRAFT"
    assert r3["payload"]["spec"]["purpose"] and "SCR-000" in r3["payload"]["spec"]["prevScreens"]
    assert set(r3["payload"]["uses"]) == {c.id for _, c in store.neighbors("SCR-001", "USES")}
    assert r3["owner"] == [d for _, d in store.neighbors("SCR-001", "OWNED_BY")][0].props["name"]


def test_publish_component_uses_existing_registry_record_or_creates_draft(store):
    # 레지스트리가 비어 있으면 CUSTOM/COMPONENT DRAFT 로 생성 (시드와 같은 payload 형태)
    ev = _call(portal.portal_publish, {"id": "CMP-Card-v1"})
    rec = ev["record"]
    assert ev["ok"] and ev["action"] == "created" and rec["name"] == "Card" and rec["recordVersion"] == "v1"
    assert rec["recordType"] == "CUSTOM" and rec["subtype"] == "COMPONENT" and rec["status"] == "DRAFT"
    assert rec["payload"]["module"] == "@atom/ui/card" and rec["payload"]["componentId"] == "CMP-Card-v1"
    assert rec["payload"]["propsSchema"]["type"] == "object" and rec["owner"] == store.get_node("CMP-Card-v1").props["owner"]
    # 기준선이 있으면 기존 레코드(APPROVED)를 그대로 — 덮어쓰지 않는다
    registry_api.reset_for_tests()
    seedmod.seed(ACTOR)
    ev = _call(portal.portal_publish, {"id": HERO})
    assert ev["ok"] and ev["action"] == "existing" and ev["record"]["status"] == "APPROVED"
    assert ev["record"]["payload"]["supersededBy"] == "v3" and "덮어쓰지" in ev["note"]
    assert ev["mapping"]["deviation"] is True


def test_publish_unsupported_label_is_labeled_unimplemented(store):
    for nid in ("PRC-000", "POL-000", "TRM-0000"):
        ev = _call(portal.portal_publish, {"id": nid})
        assert ev["ok"] is False and ev["code"] == 400 and "미구현" in ev["error"], nid
    assert registry_api.counts()["total"] == 0
    assert _call(portal.portal_publish, {"id": "NOPE"})["code"] == 404


def test_sync_recomputes_related(store):
    seedmod.seed(ACTOR)
    ev = _call(portal.portal_sync, {"id": HERO})
    assert ev["ok"] and ev["related"] == store.related_counts(HERO) and ev["syncLabel"] == "그래프 재순회"
    assert ev["computedBy"] == "graph-traversal" and ev["registry"]["record"]["status"] == "APPROVED"
    assert "미구현" in ev["note"]
    ev2 = _call(portal.portal_sync, {"id": "PAT-001"})
    assert ev2["ok"] and ev2["registry"] is None and ev2["related"] == store.related_counts("PAT-001")
    assert _call(portal.portal_sync, {"id": "NOPE"})["code"] == 404


# ---------------- §7 MCP 레코드 · 매핑표 ----------------
def test_mcp_server_seed_is_separate_and_idempotent():
    seedmod.seed(ACTOR)
    assert registry_api.counts()["byType"]["MCP"] == 1  # 기준선 계약 유지 (tests/test_registry.py)
    r1 = seedmod.seed_mcp_servers(ACTOR)
    assert r1["created"] == 3 and r1["skipped"] == 0 and r1["total"] == 3
    r2 = seedmod.seed_mcp_servers(ACTOR)
    assert r2["created"] == 0 and r2["skipped"] == 3
    assert registry_api.counts()["byType"]["MCP"] == 4
    g = registry_api.get_record("registry_gitlab_mcp", "v1")
    assert g["status"] == "APPROVED" and g["recordType"] == "MCP" and g["payload"]["deployed"] is False
    assert "미배포" in g["description"] and "VPC 내 EKS" in g["payload"]["origin"]
    for name in ("figma_mcp", "drawio_mcp"):
        r = registry_api.get_record(name, "v1")
        assert r["status"] == "APPROVED" and r["payload"]["origin"] == "외부 SaaS" and r["payload"]["connected"] is False
        assert "Tier 0/1 전용" in r["description"] and r["payload"]["tier"] == "Tier 0/1 전용"
    # Consumer API 에 MCP 4건 모두 (APPROVED)
    assert {r["name"] for r in registry_api.list_approved("MCP")} >= set(seedmod.MCP_SERVER_NAMES)
    # 기준선 seed(reset) 는 MCP 서버 레코드를 건드리지 않는다
    res = seedmod.seed(ACTOR, reset=True)
    assert res["total"] == res["updated"] and registry_api.get_record("figma_mcp", "v1")["status"] == "APPROVED"


def test_registry_map_bootstraps_and_reports_truthfully(store):
    ev = _call(portal.portal_registry_map, {})
    assert ev["ok"] and ev["bootstrapped"]["created"] > 0 and ev["mcpSeed"]["created"] == 3 and ev["tier"] == "Tier 0/1 전용"
    rows = {r["key"]: r for r in ev["rows"]}
    assert set(rows) == {r["key"] for r in seedmod.ASSET_RECORD_TYPES}
    comp = rows["component_contract"]
    assert comp["recordType"] == "SKILL" and comp["deviation"] is True and comp["current"] == "CUSTOM / COMPONENT"
    assert {(r["name"], r["status"]) for r in comp["records"]} == {("Button", "APPROVED"), ("Button", "PENDING_APPROVAL")}
    assert rows["registry_gitlab_mcp"]["records"][0]["payloadFlags"]["deployed"] is False
    assert all(r["found"] and r["status"] == "APPROVED" for r in rows["design_mcp"]["records"])
    assert all(r["found"] for r in rows["skills"]["records"] + rows["screen_agent"]["records"])
    assert rows["pattern"]["records"] == [] and rows["screen_spec"]["records"] == []
    _call(portal.portal_publish, {"id": "PAT-001"})
    ev2 = _call(portal.portal_registry_map, {})
    assert ev2["bootstrapped"] is None and ev2["mcpSeed"]["created"] == 0
    assert [r["name"] for r in {r["key"]: r for r in ev2["rows"]}["pattern"]["records"]] == ["PAT-001"]


# ---------------- Neptune 배치 질의 형태 (오프라인 · _q 가짜) ----------------
class _FakeNeptune(NeptuneGraphStore):
    def __init__(self, rows_for):
        super().__init__(endpoint="fake.local")
        self.queries = []
        self.rows_for = rows_for

    def _q(self, cypher, params=None):
        self.queries.append((cypher, params or {}))
        return self.rows_for(cypher, params or {})


def _nep(nid, label, **props):
    return {"~id": nid, "~labels": [label], "~properties": {"id": nid, **props}}


def test_neptune_bulk_queries_are_labeled_batched_and_bounded():
    def rows(cy, params):
        if "head(labels(m))" in cy:
            return [{"id": "CMP-A", "label": "Screen", "c": 21}, {"id": "CMP-A", "label": "Pattern", "c": 4},
                    {"id": "CMP-B", "label": "Screen", "c": 3}]
        if "-[:OWNED_BY]->(m:Department)" in cy:
            return [{"id": "SCR-1", "m": _nep("D-1", "Department", name="여신기획부")}]
        return []
    nep = _FakeNeptune(rows)
    ids = [f"CMP-{i}" for i in range(450)]
    rc = portal.bulk_related_counts(nep, "Component", ids)
    assert rc["CMP-A"] == {"Pattern": 4, "Screen": 21} and rc["CMP-B"] == {"Screen": 3} and rc["CMP-7"] == {}
    assert len(nep.queries) == 3  # 200 건 단위 배치 — 카드 수만큼 질의하지 않는다
    for cy, params in nep.queries:
        assert cy.startswith("MATCH (a:Component)") and "LIMIT" in cy and "IN $ids" in cy and len(params["ids"]) <= 200
    nep.queries.clear()
    nb = portal.bulk_neighbors(nep, "Screen", ["SCR-1", "SCR-2"], "OWNED_BY", "out", "Department")
    assert [n.id for n in nb["SCR-1"]] == ["D-1"] and "SCR-2" not in nb
    assert len(nep.queries) == 1 and "(a:Screen)-[:OWNED_BY]->(m:Department)" in nep.queries[0][0] and "LIMIT" in nep.queries[0][0]


def test_bulk_helpers_local_path_equals_store(store):
    ids = [n.id for n in store.find_by_label("Pattern")][:10]
    assert portal.bulk_related_counts(store, "Pattern", ids) == {i: store.related_counts(i) for i in ids}
    nb = portal.bulk_neighbors(store, "Pattern", ids, "COMPOSES", "out", "Component")
    for i in ids:
        assert [n.id for n in nb[i]] == [c.id for _, c in store.neighbors(i, "COMPOSES") if c.label == "Component"]
    assert portal.bulk_related_counts(store, "Pattern", []) == {} and portal.bulk_neighbors(store, "Pattern", [], "X", "out", "Y") == {}


# ---------------- 로그 · 용어 규칙 ----------------
def test_publish_log_has_no_description_text(store, capsys):
    _call(portal.portal_publish, {"id": "PAT-001"})
    out = capsys.readouterr().out
    assert "portal.publish" in out and ACTOR not in out
    name = store.get_node("PAT-001").props["name"]
    assert name not in out and "패턴 스펙" not in out


def test_owned_files_use_v2_terminology():
    banned = ["온" + "프렘", "On-" + "Premises", "Two-" + "Plane", "In-" + "Region", "서울을 " + "벗어나지 않"]
    for rel in ["api/handlers/portal.py", "web/src/views/Portal.tsx", "registry/seed.py", "tests/test_portal.py"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for b in banned:
            assert b not in text, f"{rel}: 금지 표현 '{b}'"


def test_list_can_skip_category_counts(store):
    ev = _call(portal.portal_list, {"category": "Patterns", "withCounts": False})
    assert ev["ok"] and ev["categoryCounts"] is None and ev["count"] == len(store.find_by_label("Pattern"))
    ev2 = _call(portal.portal_list, {"category": "Patterns"})
    assert ev2["categoryCounts"]["Patterns"] == ev["count"] and ev2["categoryCounts"]["Foundation"] == \
        len(store.find_by_label("UXTerm", category="공통"))
