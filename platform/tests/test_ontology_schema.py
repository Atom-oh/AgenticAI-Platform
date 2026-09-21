import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import ontology_schema as schema


def ref(identifier="asset-1"):
    return {"sourceKind": "asset", "sourceId": identifier, "revision": "1",
            "sha256": hashlib.sha256(identifier.encode()).hexdigest(), "audienceRevision": "1"}


def node(identifier, kind="Atom", project="project-1", **extra):
    return schema.seal({"id": identifier, "type": kind, "scope": {"kind": "project", "projectId": project},
                        "title": identifier, "revision": 1, "sourceRefs": [ref(identifier)],
                        "provenance": "declared", "reviewState": "candidate", "tombstone": False, **extra})


def edge(identifier, src, dst, kind="USES", **extra):
    return schema.seal({"id": identifier, "type": kind, "src": {"id": src, "revision": 1},
                        "dst": {"id": dst, "revision": 1}, "sourceRefs": [ref(src)],
                        "provenance": "declared", "reviewState": "candidate", "tombstone": False, **extra})


def graph(nodes, edges):
    return {"schemaVersion": 1, "projectId": "project-1", "nodes": nodes, "edges": edges}


def test_hashes_are_order_independent_and_do_not_coerce_floating_values():
    assert schema.digest({"b": "한글", "a": [2, True]}) == schema.digest({"a": [2, True], "b": "한글"})
    assert schema.identity("asset", "a", "b") != schema.identity("asset", "ab")
    for value in [float("nan"), 1.0, 2**53, {"bad": b"bytes"}, "\ud800"]:
        with pytest.raises((ValueError, UnicodeError)):
            schema.canonical(value)


def test_icon_to_atom_to_screen_and_code_references_are_representable():
    nodes = [node("icon", "Foundation", subtype="icon"), node("button"),
             node("screen", "Screen"), node("code", "CodeFile", properties={"path": "src/App.tsx", "language": "tsx"})]
    edges = [edge("icon-use", "button", "icon"), edge("screen-use", "screen", "button", "COMPOSES"),
             edge("implementation", "code", "screen", "IMPLEMENTS")]
    result = schema.validate_graph(graph(nodes, edges))
    assert len(result["nodes"]) == 4
    assert result["coverage"]["complete"] is False


def test_external_and_diagnostic_patterns_do_not_require_unrelated_usage_nodes():
    pattern = node("pattern", "Pattern", reviewState="approved", properties={"usageIds": ["screen-a", "screen-b"]})
    with pytest.raises(ValueError, match="usages"):
        schema.validate_graph(graph([pattern], []))
    assert schema.validate_graph(graph([node("consumer")], [edge("use", "consumer", "pattern")]),
                                 external_nodes=[pattern])
    assert schema.validate_graph(graph([pattern], []), diagnostic=True)


def test_visual_composition_cannot_reverse_levels_or_hide_cycles_as_nesting():
    nodes = [node("atom"), node("molecule", "Molecule"), node("other", "Molecule"),
             node("icon", "Foundation", subtype="icon")]
    for bad in [edge("up", "atom", "molecule", "COMPOSES"),
                edge("same", "molecule", "other", "COMPOSES"),
                edge("wrong-kind", "atom", "icon", "COMPOSES")]:
        with pytest.raises(ValueError, match="Composition"):
            schema.validate_graph(graph(nodes, [bad]))
    assert schema.validate_graph(graph(nodes, [edge("icon-use", "atom", "icon")]))


def test_private_cross_project_nodes_and_wrong_endpoint_revisions_fail():
    with pytest.raises(ValueError, match="another project"):
        schema.validate_graph(graph([node("other", project="project-2")], []))
    stale = edge("stale", "a", "b")
    stale["dst"]["revision"] = 2
    with pytest.raises(ValueError, match="endpoint revision"):
        schema.validate_graph(graph([node("a"), node("b")], [schema.seal(stale)]))


def test_node_and_edge_content_tampering_cannot_keep_original_hashes():
    original = node("a")
    changed = copy.deepcopy(original)
    changed["title"] = "tampered"
    with pytest.raises(ValueError, match="hash"):
        schema.validate_node(changed)
    relation = edge("a-b", "a", "b")
    relation["type"] = "COMPOSES"
    with pytest.raises(ValueError, match="hash"):
        schema.validate_edge(relation)


def test_closed_types_properties_and_exact_source_references_are_required():
    for changed in [
        node("a", "Unknown"), node("a", properties={"grantAdmin": True}),
        node("a", "CodeFile"), node("a", sourceRefs=[]),
        node("a", sourceRefs=[ref(), ref()]), node("a", "Foundation", subtype="unknown"),
    ]:
        with pytest.raises(ValueError):
            schema.validate_node(changed)
    with pytest.raises(ValueError):
        schema.source_ref({**ref(), "location": {"path": "../secret.ts"}})


def test_patterns_need_two_reviewed_screen_usages_but_candidates_do_not():
    candidate = node("pattern", "Pattern", properties={"usageIds": ["screen-a"]})
    screens = [node("screen-a", "Screen", reviewState="reviewed"), node("screen-b", "Screen", reviewState="reviewed")]
    assert schema.validate_graph(graph([candidate, *screens], []))
    approved = schema.seal({**candidate, "reviewState": "approved"})
    with pytest.raises(ValueError, match="usages"):
        schema.validate_graph(graph([approved, *screens], []))
    approved = schema.seal({**approved, "properties": {"usageIds": ["screen-a", "screen-b"]}})
    assert schema.validate_graph(graph([approved, *screens], []))


def test_non_dependency_edges_have_typed_endpoints_and_incomplete_coverage_is_honest():
    nodes = [node("a"), node("b"), node("team", "Team", properties={"teamId": "team-1"})]
    assert schema.validate_graph(graph(nodes, [edge("owner", "a", "team", "OWNED_BY")]))
    for relation in [edge("owner", "a", "b", "OWNED_BY"), edge("policy", "a", "b", "GOVERNED_BY"),
                     edge("flow", "a", "b", "NEXT")]:
        with pytest.raises(ValueError):
            schema.validate_graph(graph(nodes, [relation]))
    with pytest.raises(ValueError, match="Incomplete"):
        schema.validate_graph({**graph(nodes, []), "coverage": {
            "scope": "partial", "complete": True, "truncated": True, "unknown": []}})
