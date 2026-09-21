import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_schema import node, edge
from test_ontology_sources import asset, context
from workspace import ontology_schema as schema
from workspace.ontology_sources import asset_reference
from workspace.ontology_store import Ontology, CURRENT
from workspace.collaboration import CollaborationError


def candidate(wb):
    image, control = asset(wb, "image"), asset(wb, "control")
    nodes = [node("image", "Foundation", project=wb.project["id"], subtype="icon", sourceRefs=[asset_reference(image)]),
             node("control", project=wb.project["id"], sourceRefs=[asset_reference(control)])]
    relation = edge("use", "control", "image", sourceRefs=[asset_reference(control)])
    return {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": [relation]}


def publish(wb, graph=None, **kwargs):
    graph = candidate(wb) if graph is None else graph
    return Ontology(context(wb)).publish_candidate("collection", graph, expected_generation=kwargs.get("generation"),
                                                 request_id=kwargs.get("request", "req-1"))


def test_workspace_ontology_is_the_only_new_write_authority(wb):
    data = candidate(wb)
    result = publish(wb, data)
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    assert current["generation"] == result["generation"]
    assert wb.storage.get(wb.owner, "wb_index", "current") is None
    view = Ontology(context(wb)).read()
    assert {n["title"] for n in view["nodes"]} == {"image", "control"}
    assert len(view["edges"]) == 1
    assert all(n["reviewState"] == "candidate" and n["provenance"] == "declared" for n in view["nodes"])
    again = publish(wb, data)
    assert again["generation"] == result["generation"]
    assert again["historical"] is False


def test_manual_coverage_cannot_claim_complete_runtime_dependencies(wb):
    value = candidate(wb)
    value["coverage"] = {"complete": True, "scope": "runtime-complete", "unknown": [], "truncated": False}
    result = publish(wb, value)
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["scope"] == "declared-project-partition"
    assert "unreviewed-design-mappings" in result["coverage"]["unknown"]


def test_declared_marker_cannot_alias_a_trusted_parser_publication(wb):
    value = candidate(wb)
    publish(wb, value)
    with pytest.raises(CollaborationError) as error:
        Ontology(context(wb)).publish_candidate("collection", value, expected_generation=None,
            request_id="req-1", _producer="parser-extracted", _completion_writes=lambda marker: pytest.fail("wrong producer replay"))
    assert error.value.code == "ontology-changed"


def test_read_page_does_not_inherit_the_atomic_write_authority_limit(wb):
    generation = None
    for part in range(2):
        nodes = []
        for number in range(50):
            identifier = f"asset-{part}-{number}"
            source = asset(wb, identifier)
            nodes.append(node(identifier, project=wb.project["id"], sourceRefs=[asset_reference(source)]))
        result = Ontology(context(wb)).publish_candidate(f"read-{part}", {
            "schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": []},
            expected_generation=generation, request_id=f"read-{part}")
        generation = result["generation"]
    assert len(Ontology(context(wb)).read(limit=100)["nodes"]) == 100


def test_publication_rechecks_source_versions_atomically(wb, monkeypatch):
    data = candidate(wb)
    original = wb.storage.put_blob_once
    changed = False
    def race(key, raw, mime):
        nonlocal changed
        result = original(key, raw, mime)
        if "index-nodes-" in key and not changed:
            changed = True
            row = wb.storage.get(wb.owner, "asset", "image")
            wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
        return result
    monkeypatch.setattr(wb.storage, "put_blob_once", race)
    with pytest.raises(CollaborationError):
        publish(wb, data)
    assert wb.storage.get(wb.owner, "ontology", CURRENT) is None


def test_failed_index_write_cannot_publish_a_partial_manifest(wb, monkeypatch):
    data = candidate(wb)
    original = wb.storage.put_blob_once
    def fail_index(key, raw, mime):
        if "index-adjacency-" in key:
            raise RuntimeError("synthetic storage failure")
        return original(key, raw, mime)
    monkeypatch.setattr(wb.storage, "put_blob_once", fail_index)
    with pytest.raises(RuntimeError):
        publish(wb, data)
    assert wb.storage.get(wb.owner, "ontology", CURRENT) is None


def test_revoked_or_archived_sources_disappear_without_restoring_old_graph_access(wb):
    publish(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    view = Ontology(context(wb)).read()
    assert [n["title"] for n in view["nodes"]] == ["control"]
    assert view["edges"] == []
    assert view["coverage"]["complete"] is False


def test_cursors_are_opaque_and_bound_to_actor_scope_and_generation(wb):
    publish(wb)
    first = Ontology(context(wb)).read(limit=1)
    assert first["cursor"].startswith("cursor-")
    second = Ontology(context(wb)).read(limit=1, cursor=first["cursor"])
    assert first["nodes"][0]["id"] != second["nodes"][0]["id"]
    with pytest.raises(CollaborationError):
        Ontology(context(wb, "bob")).read(limit=1, cursor=first["cursor"])
    row = wb.storage.get(wb.owner, "project", wb.project["id"])
    row["members"].pop("bob")
    wb.storage.put(wb.owner, "project", row, row["version"])
    with pytest.raises(CollaborationError):
        Ontology(context(wb)).read(limit=1, cursor=first["cursor"])


def test_forged_project_or_publication_scope_is_not_a_new_canonical_authority(wb):
    data = candidate(wb)
    wrong = copy.deepcopy(data)
    wrong["projectId"] = "other"
    with pytest.raises(CollaborationError):
        publish(wb, wrong)
    data["nodes"][0] = schema.seal({**data["nodes"][0], "scope": {"kind": "published", "publicationId": "fake"}})
    with pytest.raises(CollaborationError):
        publish(wb, data)


def test_index_tampering_is_not_accepted_as_an_empty_graph(wb):
    result = publish(wb)
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    bucket, digest = next(iter(current["indexes"]["nodes"].items()))
    key = wb.storage.key_for(wb.owner, "ontology", "index-nodes-" + bucket, digest + ".json")
    wb.storage.put_blob(key, b"{}", "application/json")
    with pytest.raises(CollaborationError, match="해시"):
        Ontology(context(wb)).read(list(result["identities"].values()))
