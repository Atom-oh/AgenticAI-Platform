"""Offline regression tests for evidence-based Portal detail diagrams."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ["GRAPH_BACKEND"] = "local"
os.environ["REGISTRY_EMBED"] = "0"
os.environ.pop("REGISTRY_TABLE", None)

from graph.store import Edge, LocalGraphStore, NeptuneGraphStore, Node  # noqa: E402
from handlers import portal  # noqa: E402


class Capture:
    trace_id = "portal-visual-test"
    email = "offline@example.test"

    def post(self, payload):
        self.payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


@pytest.fixture
def detail(monkeypatch):
    def call(nodes, edges=(), node_id=None, graph=None):
        graph = graph or LocalGraphStore()
        graph.upsert_nodes(nodes)
        graph.upsert_edges(list(edges))
        monkeypatch.setattr(portal, "_store", lambda: graph)
        ctx = Capture()
        portal.portal_detail(ctx, {"id": node_id or nodes[0].id})
        assert ctx.payload["ok"]
        return ctx.payload
    return call


def assert_diagram(visual, source):
    assert set(visual) == {"kind", "source", "title", "nodes", "edges", "note", "truncated"}
    assert visual["kind"] == "diagram" and visual["source"] == source
    assert isinstance(visual["title"], str) and isinstance(visual["note"], str)
    assert 0 < len(visual["nodes"]) <= 25 and len(visual["edges"]) <= 50
    ids = {n["id"] for n in visual["nodes"]}
    assert len(ids) == len(visual["nodes"])
    for node in visual["nodes"]:
        assert {"id", "label", "type"} <= set(node) <= {"id", "label", "type", "assetId", "missing"}
        assert all(isinstance(node[key], str) for key in ("id", "label", "type"))
    for edge in visual["edges"]:
        assert set(edge) == {"from", "to", "label", "kind"}
        assert edge["from"] in ids and edge["to"] in ids
        assert edge["kind"] in ("sequence", "reference")
    assert all(isinstance(v, int) and v >= 0 for v in visual["truncated"].values())


@pytest.mark.parametrize("serialized", [False, True])
def test_procedure_uses_all_ordered_steps_not_includes_order(detail, serialized):
    steps = [f"단계 {i}" for i in range(12)]
    procedure = Node("PRC-test", "Procedure", {"name": "가입 절차", "steps": json.dumps(steps) if serialized else steps})
    screens = [Node(f"SCR-{i}", "Screen", {"name": step}) for i, step in enumerate(steps)]
    edges = [Edge(procedure.id, "INCLUDES", s.id) for s in reversed(screens)]
    ev = detail([procedure, *screens], edges)
    visual = ev["visual"]
    assert_diagram(visual, "procedure-steps")
    assert [n["label"] for n in visual["nodes"]] == steps
    assert [n["assetId"] for n in visual["nodes"]] == [s.id for s in screens]
    ids = [n["id"] for n in visual["nodes"]]
    assert [(e["from"], e["to"], e["kind"]) for e in visual["edges"]] == [
        (a, b, "sequence") for a, b in zip(ids, ids[1:])
    ]
    assert visual["truncated"] == {"nodes": 0, "edges": 0}
    assert ev["props"]["steps"] == steps
    assert ev["neighbors"][0]["count"] == 12 and len(ev["neighbors"][0]["nodes"]) == 8


def test_procedure_preserves_repeated_steps_and_requires_unique_exact_included_name(detail):
    long_name = "긴" * 100
    steps = ["정확", "중복", "미등록", "정확", "공백", long_name[:80]]
    procedure = Node("PRC-test", "Procedure", {"steps": steps})
    screens = [
        Node("SCR-A", "Screen", {"name": "정확"}),
        Node("SCR-B", "Screen", {"name": "중복"}),
        Node("SCR-C", "Screen", {"name": "중복"}),
        Node("SCR-D", "Screen", {"name": "미등록"}),
        Node("SCR-E", "Screen", {"name": " 공백 "}),
        Node("SCR-F", "Screen", {"name": long_name}),
    ]
    included = [s for s in screens if s.id != "SCR-D"]
    edges = [Edge(procedure.id, "INCLUDES", s.id) for s in included]
    edges.append(Edge(procedure.id, "INCLUDES", "SCR-A"))
    visual = detail([procedure, *screens], edges)["visual"]
    assert_diagram(visual, "procedure-steps")
    assert [n.get("assetId") for n in visual["nodes"]] == ["SCR-A", None, None, "SCR-A", None, None]
    assert [bool(n.get("missing")) for n in visual["nodes"]] == [False, True, True, False, True, True]
    assert len(visual["edges"]) == 5


@pytest.mark.parametrize("steps", [None, "", "[]", "not-json", {}, {"first": "입력"}, 7, [None, {}], ["", " "]])
def test_procedure_without_ordered_steps_does_not_invent_flow(detail, steps):
    visual = detail(
        [Node("PRC-test", "Procedure", {"steps": steps}), Node("SCR-A", "Screen", {"name": "입력"})],
        [Edge("PRC-test", "INCLUDES", "SCR-A")],
    )["visual"]
    assert visual["kind"] == "empty"
    assert set(visual) == {"kind", "reason", "note"} and visual["reason"] and visual["note"]


def test_malformed_step_does_not_join_nonadjacent_steps(detail):
    visual = detail([Node("PRC-test", "Procedure", {"steps": ["입력", None, "검토"]})])["visual"]
    assert_diagram(visual, "procedure-steps")
    assert len(visual["nodes"]) == 3 and visual["nodes"][1]["missing"] is True
    first, middle, last = [n["id"] for n in visual["nodes"]]
    assert [(e["from"], e["to"]) for e in visual["edges"]] == [(first, middle), (middle, last)]
    assert "assetId" not in visual["nodes"][1]


def test_screen_navigation_uses_metadata_only_and_marks_unresolved_ids(detail):
    nodes = [
        Node("SCR-now", "Screen", {"name": "현재 화면"}),
        Node("SM-now", "ScreenMeta", {
            "prevScreens": '["SCR-before", "SCR-before"]',
            "nextScreens": ["SCR-next", "SCR-absent"],
            "entryCondition": "로그인 완료",
        }),
        Node("SCR-next", "Screen", {"name": "저장소에만 있는 화면"}),
        Node("PAT-other", "Pattern", {"name": "별도 패턴"}),
    ]
    visual = detail(nodes, [
        Edge("SM-now", "DESCRIBES", "SCR-now"), Edge("SCR-now", "FOLLOWS", "PAT-other"),
    ])["visual"]
    assert_diagram(visual, "screen-navigation")
    by_asset = {n["assetId"]: n for n in visual["nodes"]}
    assert set(by_asset) == {"SCR-now", "SCR-before", "SCR-next", "SCR-absent"}
    assert by_asset["SCR-now"]["label"] == "현재 화면"
    assert all(by_asset[n]["missing"] and by_asset[n]["label"] == n for n in ("SCR-before", "SCR-absent"))
    assert by_asset["SCR-next"]["label"] == "저장소에만 있는 화면"
    assert not by_asset["SCR-next"].get("missing")
    id_to_asset = {n["id"]: n["assetId"] for n in visual["nodes"]}
    assert {(id_to_asset[e["from"]], id_to_asset[e["to"]], e["kind"]) for e in visual["edges"]} == {
        ("SCR-before", "SCR-now", "sequence"), ("SCR-now", "SCR-next", "sequence"),
        ("SCR-now", "SCR-absent", "sequence"),
    }
    assert "미확인" in visual["note"]
    assert "로그인 완료" not in json.dumps(visual, ensure_ascii=False)


def test_screen_without_metadata_is_empty_and_empty_metadata_has_only_current_node(detail):
    screen = Node("SCR-now", "Screen", {"name": "현재"})
    assert detail([screen])["visual"]["kind"] == "empty"
    visual = detail([screen, Node("SM-now", "ScreenMeta", {"prevScreens": [], "nextScreens": []})],
                    [Edge("SM-now", "DESCRIBES", screen.id)])["visual"]
    assert_diagram(visual, "screen-navigation")
    assert len(visual["nodes"]) == 1 and visual["edges"] == []


def test_invalid_screen_refs_do_not_become_navigation_edges(detail):
    visual = detail(
        [Node("SCR-now", "Screen", {"name": "현재"}),
         Node("SM-now", "ScreenMeta", {"prevScreens": {"bad": "SCR-A"}, "nextScreens": [None, {}, "", "SCR-B"]})],
        [Edge("SM-now", "DESCRIBES", "SCR-now")],
    )["visual"]
    assert_diagram(visual, "screen-navigation")
    assert {n["assetId"] for n in visual["nodes"]} == {"SCR-now", "SCR-B"}
    assert len(visual["edges"]) == 1 and "형식" in visual["note"]


def test_screen_resolves_only_already_fetched_screen_names(detail):
    visual = detail(
        [Node("SCR-now", "Screen", {"name": "현재"}),
         Node("SCR-next", "Screen", {"name": "다음"}),
         Node("SM-now", "ScreenMeta", {"nextScreens": ["SCR-next"]})],
        [Edge("SM-now", "DESCRIBES", "SCR-now"), Edge("SCR-now", "RELATED_TO", "SCR-next")],
    )["visual"]
    assert_diagram(visual, "screen-navigation")
    assert {n["label"] for n in visual["nodes"]} == {"현재", "다음"}
    assert not any(n.get("missing") for n in visual["nodes"])
    assert len(visual["edges"]) == 1 and visual["edges"][0]["kind"] == "sequence"


def test_screen_reference_lookup_uses_one_bounded_neptune_query():
    class Lookup:
        name = "neptune"
        def __init__(self):
            self.calls = []
        def _q(self, query, params):
            self.calls.append((query, params))
            return [{"n": Node(ref, "Screen", {"name": f"화면 {ref}"})} for ref in params["ids"]]
        def _node(self, value):
            return value

    store = Lookup()
    meta = {"prevScreens": [f"SCR-{i}" for i in range(40)], "nextScreens": ["SCR-0", "SCR-extra"]}
    result = portal.screen_reference_nodes(store, "SCR-current", meta)
    assert len(result) == 24 and len(store.calls) == 1
    query, params = store.calls[0]
    assert "(n:Screen)" in query and "n.id IN $ids" in query and "LIMIT 24" in query
    assert params["ids"] == [f"SCR-{i}" for i in range(24)]


def test_screen_reference_lookup_rejects_non_screen_and_unrequested_results():
    class Lookup:
        name = "neptune"
        def _q(self, query, params):
            return [{"n": Node("SCR-ok", "Screen", {"name": "확인"})},
                    {"n": Node("SCR-other", "Screen", {"name": "범위 밖"})},
                    {"n": Node("SCR-wrong", "Customer", {"name": "잘못된 유형"})}]
        def _node(self, value):
            return value
    result = portal.screen_reference_nodes(Lookup(), "SCR-current", {"nextScreens": ["SCR-ok", "SCR-wrong"]})
    assert list(result) == ["SCR-ok"]


@pytest.mark.parametrize("label,source,relations", [
    ("Pattern", "pattern-composition", [
        ("ROOT", "COMPOSES", "CMP-A"), ("SCR-A", "FOLLOWS", "ROOT"), ("SCR-B", "FOLLOWS", "ROOT"),
    ]),
    ("PolicyRule", "policy-relations", [
        ("ROOT", "CONSTRAINS", "SCR-A"), ("ROOT", "CONSTRAINS", "SCR-B"), ("ROOT", "DERIVED_FROM", "REG-A"),
    ]),
])
def test_pattern_and_policy_show_only_actual_reference_edges(detail, label, source, relations):
    nodes = [
        Node("ROOT", label, {"name": "관계"}),
        Node("CMP-A", "Component", {"name": "버튼"}), Node("SCR-A", "Screen", {"name": "입력"}),
        Node("SCR-B", "Screen", {"name": "확인"}), Node("REG-A", "Regulation", {"title": "규정"}),
    ]
    edges = [Edge(*e) for e in relations]
    edges += [Edge("ROOT", "RELATED_TO", "REG-A"), Edge(*relations[0])]
    visual = detail(nodes, edges)["visual"]
    assert_diagram(visual, source)
    ids = {n["id"]: n["assetId"] for n in visual["nodes"]}
    assert len(visual["edges"]) == len(relations)
    assert {(ids[e["from"]], ids[e["to"]]) for e in visual["edges"]} == {(a, b) for a, _, b in relations}
    assert all(e["kind"] == "reference" for e in visual["edges"])
    assert not any(ids[e["from"]] == "SCR-A" and ids[e["to"]] == "SCR-B" for e in visual["edges"])


@pytest.mark.parametrize("label,wrong_rel", [("Pattern", "COMPOSES"), ("PolicyRule", "CONSTRAINS")])
def test_wrong_direction_or_target_type_cannot_fabricate_diagram(detail, label, wrong_rel):
    visual = detail(
        [Node("ROOT", label), Node("SCR-A", "Screen"), Node("OTHER", "Department")],
        [Edge("SCR-A", wrong_rel, "ROOT"), Edge("ROOT", wrong_rel, "OTHER")],
    )["visual"]
    assert visual["kind"] == "empty"


def test_component_legacy_metadata_is_explicitly_code_unlinked(detail, monkeypatch):
    record = {"recordType": "CUSTOM", "payload": {"module": "@atom/ui/button"}, "status": "APPROVED"}
    monkeypatch.setattr(portal, "_registry_view", lambda n: {"record": record, "available": True})
    ev = detail([Node("CMP-A", "Component", {"name": "Button", "approvalStatus": "APPROVED"})])
    assert ev["visual"]["kind"] == "empty"
    assert "코드" in ev["visual"]["reason"] and "@atom/ui" in ev["visual"]["note"]
    assert "@studio/approved-ui" not in json.dumps(ev, ensure_ascii=False)
    assert ev["registry"]["record"] == record
    assert ev["status"] == "APPROVED" and ev["publishTarget"] == {"recordType": "CUSTOM", "subtype": "COMPONENT"}


@pytest.mark.parametrize("label", ["UXTerm", "Department"])
def test_unsupported_assets_have_explicit_empty_visual(detail, label):
    assert detail([Node("ROOT", label, {"name": "자산"})])["visual"]["kind"] == "empty"


def test_large_procedure_is_bounded_and_reports_omitted_sequence(detail):
    visual = detail([Node("PRC-test", "Procedure", {"steps": [f"{i}:" + "긴" * 500 for i in range(100)]})])["visual"]
    assert_diagram(visual, "procedure-steps")
    assert len(visual["nodes"]) == 25 and len(visual["edges"]) == 24
    assert visual["truncated"] == {"nodes": 75, "edges": 75}
    assert all(len(n["label"]) <= 120 for n in visual["nodes"])
    assert "생략" in visual["note"]
    assert len(json.dumps(visual, ensure_ascii=False).encode()) < 24000


def test_navigation_limits_preserve_identity_for_long_ids_and_count_dropped_edges(detail):
    refs = ["화" * 300 + str(i) for i in range(100)]
    visual = detail(
        [Node("SCR-now", "Screen", {"name": "제목" * 500}),
         Node("SM-now", "ScreenMeta", {"prevScreens": refs, "nextScreens": refs})],
        [Edge("SM-now", "DESCRIBES", "SCR-now")],
    )["visual"]
    assert_diagram(visual, "screen-navigation")
    assert len(visual["nodes"]) == 25 and len(visual["edges"]) == 48
    assert visual["truncated"] == {"nodes": 76, "edges": 152}
    assert len(visual["title"]) <= 120
    assert all(len(n["id"]) <= 64 and len(n.get("assetId", "")) <= 64 for n in visual["nodes"])
    assert len(json.dumps(visual, ensure_ascii=False).encode()) < 24000


def test_large_pattern_truncates_actual_relations_not_neighbor_card_sample(detail):
    nodes = [Node("ROOT", "Pattern"), *[Node(f"CMP-{i}", "Component", {"name": f"구성 {i}"}) for i in range(80)]]
    visual = detail(nodes, [Edge("ROOT", "COMPOSES", n.id) for n in nodes[1:]])["visual"]
    assert_diagram(visual, "pattern-composition")
    assert len(visual["nodes"]) == 25 and len(visual["edges"]) == 24
    assert visual["truncated"] == {"nodes": 56, "edges": 56}


def test_navigation_caps_edges_with_self_references_and_deduplicates_repeated_refs(detail):
    refs = ["SCR-now", *[f"SCR-{i}" for i in range(30)]]
    visual = detail(
        [Node("SCR-now", "Screen", {"name": "현재"}),
         Node("SM-now", "ScreenMeta", {"prevScreens": refs * 2, "nextScreens": refs * 2})],
        [Edge("SM-now", "DESCRIBES", "SCR-now")],
    )["visual"]
    assert_diagram(visual, "screen-navigation")
    assert len(visual["nodes"]) == 25 and len(visual["edges"]) == 50
    assert visual["truncated"] == {"nodes": 6, "edges": 12}


class OfflineNeptune(NeptuneGraphStore):
    """Exercise production Neptune query construction with an offline transport."""

    def __init__(self, nodes, edges):
        super().__init__(endpoint="unused.invalid")
        self.nodes = {n.id: n for n in nodes}
        self.edges = edges
        self.queries = []

    @staticmethod
    def row(node):
        return {"~id": node.id, "~labels": [node.label], "~properties": {"id": node.id, **node.props}}

    def _q(self, query, params=None):
        self.queries.append((query, params))
        nid = params["id"]
        if "count(DISTINCT m)" in query:
            return []
        if "RETURN n LIMIT 1" in query:
            return [{"n": self.row(self.nodes[nid])}] if nid in self.nodes else []
        if "RETURN type(r) AS rel, b LIMIT 200" in query:
            incoming = "<-[r" in query
            edges = [e for e in self.edges if (e.dst if incoming else e.src) == nid]
            if "[r:SUPERSEDED_BY]" in query:
                edges = [e for e in edges if e.rel == "SUPERSEDED_BY"]
            return [{"rel": e.rel, "b": self.row(self.nodes[e.src if incoming else e.dst])} for e in edges[:200]]
        raise AssertionError(f"Unexpected graph query: {query}")


@pytest.mark.parametrize("count", [3, 30, 201])
def test_neptune_detail_reuses_bounded_neighbors_without_per_asset_queries(monkeypatch, count):
    screens = [Node(f"SCR-{i}", "Screen", {"name": f"단계 {i}"}) for i in range(count)]
    if count == 201:
        screens[-1].props["name"] = screens[0].props["name"]
    procedure = Node("PRC-test", "Procedure", {"steps": [s.props["name"] for s in screens]})
    graph = OfflineNeptune([procedure, *screens], [Edge(procedure.id, "INCLUDES", s.id) for s in screens])
    monkeypatch.setattr(portal, "_store", lambda: graph)
    ctx = Capture()
    portal.portal_detail(ctx, {"id": procedure.id})
    visual = ctx.payload["visual"]
    assert_diagram(visual, "procedure-steps")
    assert len(graph.queries) <= 8
    assert all(params["id"] == procedure.id for _, params in graph.queries)
    assert all("LIMIT" in query or "count(DISTINCT m)" in query for query, _ in graph.queries)
    if count == 201:
        assert all("assetId" not in n and n["missing"] for n in visual["nodes"])
        assert "조회" in visual["note"]
    else:
        assert [n["assetId"] for n in visual["nodes"]] == [s.id for s in screens[:25]]
