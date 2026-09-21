import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import ontology_schema as schema
from workspace.ontology_impact import analyze
from test_ontology_schema import node, edge, graph, ref


def fixture():
    nodes = [node("icon", "Foundation", subtype="icon"), node("button"),
             node("screen", "Screen"), node("procedure", "Procedure"),
             node("product", "Product", properties={"productId": "p"}),
             node("code", "CodeFile", properties={"path": "src/App.tsx", "language": "tsx"}),
             node("owner", "Team", properties={"teamId": "design"})]
    edges = [edge("a", "button", "icon"), edge("b", "screen", "button", "COMPOSES"),
             edge("c", "procedure", "screen"), edge("d", "product", "procedure"),
             edge("e", "code", "screen", "IMPLEMENTS"), edge("f", "code", "owner", "OWNED_BY")]
    return graph(nodes, edges)


def run(value, **kwargs):
    generation = schema.digest(value)
    change = {"id": "change-1", "kind": "asset", "baseGeneration": generation, "oldSource": ref("icon")}
    return analyze(value, change, generation=generation, can_read=kwargs.get("can_read", lambda refs: True))


def test_source_revision_seed_reaches_component_screen_procedure_product_and_code():
    result = run(fixture())
    items = {item["nodeId"]: item for item in result["items"]}
    assert set(items) == {"icon", "button", "screen", "procedure", "product", "code"}
    assert items["code"]["witnessPath"] == ["icon", "button", "screen", "code"]
    assert items["product"]["witnessEdges"] == ["a", "b", "c", "d"]
    assert all(item["action"] == "investigate" for item in items.values())
    assert result["coverage"]["complete"] is False


def test_traversal_stops_before_unreadable_nodes_and_does_not_leak_hidden_path_shape():
    value = fixture()
    result = run(value, can_read=lambda refs: all(r["sourceId"] != "screen" for r in refs))
    assert [item["nodeId"] for item in result["items"]] == ["icon", "button"]
    assert "restricted-or-unmapped" in result["coverage"]["unknown"]
    assert "screen" not in schema.canonical(result).decode()
    assert "product" not in schema.canonical(result).decode()


def test_approved_structural_and_declared_evidence_are_distinguished():
    value = fixture()
    value["nodes"] = [schema.seal({**n, "reviewState": "approved", "provenance": "parser-extracted"}) for n in value["nodes"]]
    value["edges"] = [schema.seal({**e, "reviewState": "approved", "provenance": "parser-extracted"}) for e in value["edges"]]
    assert all(item["evidenceKind"] == "observed-structural" for item in run(value)["items"])
    value["edges"][0] = schema.seal({**value["edges"][0], "provenance": "declared"})
    assert next(item for item in run(value)["items"] if item["nodeId"] == "code")["evidenceKind"] == "approved-declared"


def test_next_and_ownership_are_not_undocumented_dependency_paths():
    value = fixture()
    value["nodes"].append(node("unrelated", "Screen"))
    value["edges"].append(edge("next", "unrelated", "screen", "NEXT"))
    assert "unrelated" not in {item["nodeId"] for item in run(value)["items"]}


def test_generation_mismatch_and_missing_authorization_are_rejected():
    value = fixture()
    change = {"id": "change-1", "kind": "asset", "baseGeneration": "a" * 64, "nodeIds": ["icon"]}
    with pytest.raises(ValueError, match="generation"):
        analyze(value, change, generation="b" * 64, can_read=lambda refs: True)
    with pytest.raises(ValueError, match="authorization"):
        analyze(value, change, generation="a" * 64, can_read=None)


def test_source_attached_only_to_an_edge_seeds_the_dependent():
    value = fixture()
    value["edges"][0] = schema.seal({**value["edges"][0], "sourceRefs": [ref("mapping-document")]})
    generation = schema.digest(value)
    result = analyze(value, {"id": "edge-change", "kind": "asset", "baseGeneration": generation,
                            "oldSource": ref("mapping-document")}, generation=generation, can_read=lambda refs: True)
    assert "button" in {item["nodeId"] for item in result["items"]}
    assert "code" in {item["nodeId"] for item in result["items"]}


def test_stale_endpoint_remains_a_potential_witness_in_diagnostic_impact():
    value = fixture()
    value["nodes"][0] = schema.seal({**value["nodes"][0], "revision": 2})
    result = run(value)
    code = next(item for item in result["items"] if item["nodeId"] == "code")
    assert code["staleWitness"] is True
    assert code["evidenceKind"] == "candidate"
