"""The selected node, rather than its optional display code, anchors impact."""
import sys
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1])]
from test_document_analyses import api, catalog, result, run, source, start
from graph.store import Edge, NeptuneGraphStore, Node


@pytest.mark.parametrize("duplicate", [False, True], ids=["missing-code", "duplicate-code"])
def test_private_analysis_keeps_the_selected_regulation_identity(api, duplicate):
    if duplicate:
        api.graph_store.upsert_nodes([
            Node("REG-other", "Regulation", {"code": "SHARED", "title": "Other synthetic rule"}),
            Node("DOC-other", "Document", {"title": "Unrelated original"}),
        ])
        api.graph_store.upsert_edges([Edge("DOC-other", "REFERENCES", "REG-other")])
    catalog(api)
    api.graph_store.upsert_nodes([Node("REG-1", "Regulation", {
        "title": "Selected synthetic rule", **({"code": "SHARED"} if duplicate else {}),
    })])
    if duplicate:
        find = api.graph_store.find_by_label
        def other_first(label, **filters):
            matches = find(label, **filters)
            return sorted(matches, key=lambda node: node.id != "REG-other")
        api.graph_store.find_by_label = other_first
    source(api, "REG-1")
    created = start(api)
    done, _ = run(api, created)
    assert done["status"] == "completed"
    status, payload, _ = result(api, created)
    assert status == 200
    value = payload["result"]
    assert value["regulation"]["id"] == "REG-1"
    assert [row["id"] for row in value["candidates"]["documents"]] == ["DOC-1"]
    assert all(row["graphRef"] != "REG-other" for row in value["sources"])


def test_neptune_impact_queries_use_the_selected_id_not_its_shared_code():
    class CapturingNeptune(NeptuneGraphStore):
        def __init__(self):
            self.calls = []

        def _q(self, cypher, params=None):
            self.calls.append((cypher, params))
            if cypher == "MATCH (n {id: $id}) RETURN n LIMIT 1":
                assert params == {"id": "REG-selected"}
                return [{"n": {"~labels": ["Regulation"], "~properties": {
                    "id": "REG-selected", "code": "SHARED", "title": "Selected rule",
                }}}]
            assert cypher.startswith("MATCH (r:Regulation {id: $c})")
            assert params["c"] == params["c2"] == "REG-selected"
            assert "code:" not in cypher
            if "<-[:REFERENCES]-(n:Document)" in cypher:
                return [{"n": {"~labels": ["Document"], "~properties": {"id": "DOC-selected"}}}]
            return []

    graph = CapturingNeptune()
    value = graph.impact_of_regulation_id("REG-selected")
    assert value.regulation.id == "REG-selected"
    assert [node.id for node in value.documents] == ["DOC-selected"]
    assert len(graph.calls) > 10
