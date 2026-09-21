import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb, indexed, call
from test_ontology_sources import context
from workspace.ontology_workbench import import_legacy
from workspace.ontology_store import Ontology
from workspace.collaboration import CollaborationError


def test_explicit_migration_preserves_legacy_graph_and_project_source_authority(wb):
    indexed(wb)
    before = wb.storage.get(wb.owner, "wb_index", "current")
    imported = import_legacy(context(wb), {"requestId": "import", "expectedGeneration": None})
    assert imported["generation"]
    assert wb.storage.get(wb.owner, "wb_index", "current") == before
    wb.api.ontology_mode = "canonical"
    result = call(wb, "GET", "dependencies")
    assert result["backend"] == "workspace-project-ontology"
    assert result["nodes"]
    assert all(node["canonicalType"] for node in result["nodes"])
    with pytest.raises(CollaborationError, match="검토 API"):
        call(wb, "POST", "dependencies", {})


def test_canonical_workbench_impacts_keep_the_same_manifest_and_all_source_refs(wb):
    indexed(wb)
    import_legacy(context(wb), {"requestId": "import", "expectedGeneration": None})
    wb.api.ontology_mode = "canonical"
    graph = Ontology(context(wb)).read()
    target = next(node["id"] for node in graph["nodes"] if node["title"] == "Withdrawal")
    change = call(wb, "POST", "changes", {"requestId": "change", "title": "Synthetic change",
        "targetId": target, "changeType": "update", "before": "before", "after": "after", "reason": "Test"})["change"]
    result = call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    assert result["change"]["graphAuthority"] == "canonical"
    assert result["impact"]["generation"] == graph["generation"]
    assert all(ref["sourceKind"] == "workbench-document" for ref in result["impact"]["sourceRefs"])
    loaded = call(wb, "GET", f"changes/{change['id']}/impact")["impact"]
    assert loaded["generation"] == graph["generation"]


def test_nonterminal_task_update_fences_revoked_canonical_source_records(wb):
    from test_ontology_store import candidate
    published = Ontology(context(wb)).publish_candidate("task-race", candidate(wb),
        expected_generation=None, request_id="task-race")
    wb.api.ontology_mode = "canonical"
    change = call(wb, "POST", "changes", {"requestId": "task-race-change", "title": "Source race",
        "targetId": published["identities"]["image"], "changeType": "update",
        "before": "", "after": "", "reason": "Synthetic authorization race"})["change"]
    result = call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    task = result["tasks"][0]

    def revoke():
        source = wb.storage.get(wb.owner, "asset", "image")
        wb.storage.put(wb.owner, "asset", {**source, "accessRevoked": True}, source["version"])

    wb.storage.table().before_transaction = revoke
    with pytest.raises(CollaborationError) as error:
        call(wb, "PUT", f"tasks/{task['id']}", {"version": task["version"], "status": "in-progress"})
    assert error.value.code == "conflict"
    assert wb.storage.get(wb.owner, "wb_task", task["id"])["status"] == "open"


def test_archived_readable_source_keeps_canonical_change_detail_available(wb):
    from test_ontology_store import candidate
    data = candidate(wb)
    published = Ontology(context(wb)).publish_candidate("changes", data, expected_generation=None, request_id="changes")
    wb.api.ontology_mode = "canonical"
    change = call(wb, "POST", "changes", {"requestId": "historical-detail", "title": "Synthetic image change",
        "targetId": published["identities"]["image"], "changeType": "update",
        "before": "", "after": "", "reason": "Synthetic"})["change"]
    analyzed = call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    source = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**source, "archived": True}, source["version"])
    assert call(wb, "GET", f"changes/{change['id']}")["change"]["id"] == change["id"]
    assert call(wb, "GET", f"changes/{change['id']}/impact")["impact"]["impactHash"] == analyzed["impact"]["impactHash"]
    report = call(wb, "POST", "reports", {"requestId": "historical-report", "type": "change-impact",
                                       "changeId": change["id"]})["report"]
    assert call(wb, "GET", f"reports/{report['id']}/document")["markdown"]
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"reports/{report['id']}/approve",
             {"version": report["version"], "contentHash": report["contentHash"]})
    current = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**current, "accessRevoked": True}, current["version"])
    with pytest.raises(CollaborationError):
        call(wb, "GET", f"reports/{report['id']}/document")


def test_non_owner_cannot_migrate_or_overwrite_canonical_authority(wb):
    indexed(wb)
    with pytest.raises(CollaborationError):
        import_legacy(context(wb, "bob"), {"requestId": "import", "expectedGeneration": None})


def test_legacy_refs_cannot_select_a_different_authority(wb):
    from workbench import knowledge
    from test_ontology_sources import asset
    from workspace.ontology_sources import asset_reference
    reference = asset_reference(asset(wb))
    with pytest.raises(CollaborationError) as error:
        knowledge.verify_refs(context(wb), [reference])
    assert error.value.code == "invalid-evidence"
    assert knowledge.verify_refs(context(wb), [reference], authority="canonical")


def test_import_preserves_parallel_evidence_and_marks_invalid_next(wb, monkeypatch):
    from workbench import knowledge
    indexed(wb)
    original = knowledge.legacy_graph(context(wb))
    relation = next(e for e in original["edges"] if e["rel"] == "DEPENDS_ON")
    original["edges"].append({**relation, "rel": "REQUIRES"})
    unrelated = next(hit for hit in call(wb, "GET", "knowledge")["items"] if hit["title"] == "Synthetic pension investment")
    different = {**relation, "sourceRef": call(wb, "GET", "knowledge/" + unrelated["id"])["evidence"]}
    original["edges"].append(different)
    original["edges"].append({**relation, "rel": "NEXT"})
    monkeypatch.setattr(knowledge, "legacy_graph", lambda ctx: original)
    imported = import_legacy(context(wb), {"requestId": "parallel", "expectedGeneration": None})
    graph = Ontology(context(wb)).read()
    assert len([e for e in graph["edges"] if e["type"] == "USES"]) == 1
    assert len(next(e for e in graph["edges"] if e["type"] == "USES")["sourceRefs"]) == 2
    assert "unmapped-legacy-edge-shape" in imported["coverage"]["unknown"]


def test_stale_declared_graph_cannot_tombstone_a_previous_import(wb):
    from workbench import knowledge
    from test_workbench_core import queue, run, documents
    src, _ = indexed(wb)
    identifier = next(row["id"] for row in call(wb, "GET", "knowledge")["items"] if row["title"] == "Synthetic withdrawal guidance")
    reference = call(wb, "GET", "knowledge/" + identifier)["evidence"]
    knowledge.declare_graph(context(wb), {"requestId": "declared", "sourceRef": reference,
        "nodes": [{"id": "declared-screen", "label": "Screen", "title": "Declared screen", "version": "1"}], "edges": []})
    first = import_legacy(context(wb), {"requestId": "before-reindex", "expectedGeneration": None})
    updated = documents()
    updated[0]["revision"] = "r2"
    run(wb, queue(wb, src, updated, request="reindex"))
    assert knowledge.legacy_graph(context(wb))["coverage"]["incompleteSnapshot"]
    with pytest.raises(CollaborationError) as error:
        import_legacy(context(wb), {"requestId": "after-reindex", "expectedGeneration": first["generation"]})
    assert error.value.code == "legacy-import-incomplete"
    assert Ontology(context(wb)).current()["generation"] == first["generation"]


def test_canonical_impact_excludes_order_containment_and_provenance():
    from workbench.impact import traversal
    from test_ontology_schema import ref
    source = ref("test")
    nodes = [{"id": identifier, "label": "Screen", "title": identifier, "sourceRef": source,
              "sourceRefs": [source], "provenance": "declared"} for identifier in ["changed", "real", "next", "part", "derived"]]
    edges = [{"src": identifier, "dst": "changed", "rel": relation, "canonicalRelation": relation,
              "sourceRefs": [source], "provenance": "declared"}
             for identifier, relation in [("real", "USES"), ("next", "NEXT"), ("part", "PART_OF"), ("derived", "DERIVED_FROM")]]
    result = traversal({"nodes": nodes, "edges": edges, "generation": "g", "coverage": {}}, "changed")
    assert {item["targetId"] for item in result["items"]} == {"changed", "real"}


def test_missing_legacy_authority_cannot_publish_an_unfenced_empty_import(wb):
    with pytest.raises(CollaborationError) as error:
        import_legacy(context(wb), {"requestId": "empty", "expectedGeneration": None})
    assert error.value.code == "legacy-not-indexed"
    assert wb.storage.get(wb.owner, "ontology", "project-current") is None


def test_scoped_import_has_stable_separate_partitions_and_discloses_cross_scope_edges(wb):
    from workbench import knowledge
    indexed(wb)
    legacy = knowledge.legacy_graph(context(wb))
    relation = next(edge for edge in legacy["edges"] if edge["rel"] == "DEPENDS_ON")
    first = import_legacy(context(wb), {"requestId": "scope-one", "expectedGeneration": None,
                                       "nodeIds": [relation["src"]]})
    second = import_legacy(context(wb), {"requestId": "scope-two", "expectedGeneration": first["generation"],
                                        "nodeIds": [relation["dst"]]})
    assert first["partitionId"] != second["partitionId"]
    assert "outside-selected-legacy-scope" in first["coverage"]["unknown"]
    again = import_legacy(context(wb), {"requestId": "scope-again", "expectedGeneration": second["generation"],
                                       "nodeIds": [relation["src"]]})
    assert first["partitionId"] == again["partitionId"]
    assert len(Ontology(context(wb)).read()["nodes"]) == 2


@pytest.mark.parametrize("coverage", [{"truncated": True}, {"conflictingNodeIds": 1}])
def test_incomplete_legacy_snapshots_cannot_replace_published_mappings(wb, monkeypatch, coverage):
    from workbench import knowledge
    indexed(wb)
    first = import_legacy(context(wb), {"requestId": "whole", "expectedGeneration": None})
    old = knowledge.legacy_graph(context(wb))
    old["coverage"].update(coverage)
    monkeypatch.setattr(knowledge, "legacy_graph", lambda ctx: old)
    with pytest.raises(CollaborationError) as error:
        import_legacy(context(wb), {"requestId": "incomplete", "expectedGeneration": first["generation"]})
    assert error.value.code == "legacy-import-incomplete"
    assert Ontology(context(wb)).current()["generation"] == first["generation"]


def test_parallel_impact_evidence_and_prior_truncation_survive_projection():
    from workbench.impact import traversal
    from test_ontology_schema import ref
    nodes = [{"id": key, "label": "Screen", "title": key, "sourceRef": ref(key), "provenance": "declared"}
             for key in ["root", "dependent"]]
    edges = [{"src": "dependent", "dst": "root", "rel": "USES", "canonicalRelation": "USES",
              "sourceRefs": [ref(f"evidence-{i}") for i in range(start, start + 20)], "provenance": "declared"}
             for start in [0, 20]]
    result = traversal({"nodes": nodes, "edges": edges, "generation": "g",
                        "coverage": {"truncated": True}}, "root")
    dependent = next(item for item in result["items"] if item["targetId"] == "dependent")
    assert len(dependent["sourceRefs"]) == 42
    assert result["coverage"]["truncated"] is True


def test_converging_paths_keep_all_intermediate_and_edge_sources():
    from workbench.impact import traversal
    from test_ontology_schema import ref
    nodes = [{"id": key, "label": "Screen", "title": key, "sourceRef": ref(key), "provenance": "declared"}
             for key in ["root", "b", "c", "a"]]
    edges = [{"src": src, "dst": dst, "rel": "USES", "sourceRef": ref(evidence), "provenance": "declared"}
             for src, dst, evidence in [("b", "root", "b-root"), ("c", "root", "c-root"),
                                        ("a", "b", "a-b"), ("a", "c", "a-c")]]
    result = traversal({"nodes": nodes, "edges": edges, "generation": "g", "coverage": {}}, "root")
    item = next(item for item in result["items"] if item["targetId"] == "a")
    assert {ref["sourceId"] for ref in item["sourceRefs"]} == {"root", "a", "b", "c", "a-b", "a-c", "b-root", "c-root"}
    assert len(item["witnessEdges"]) == 4


def test_task_budget_retains_all_source_checks_and_discloses_reduced_scope(wb):
    from test_ontology_sources import asset
    from test_ontology_schema import node, edge
    from workspace.ontology_sources import asset_reference
    nodes, edges = [], []
    for number in range(50):
        name = f"item-{number}"
        reference = asset_reference(asset(wb, name))
        nodes.append(node(name, "Screen", project=wb.project["id"], sourceRefs=[reference]))
        if number:
            edges.append(edge(f"edge-{number}", name, "item-0", sourceRefs=[reference]))
    published = Ontology(context(wb)).publish_candidate("tasks", {
        "schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": edges},
        expected_generation=None, request_id="tasks")
    wb.api.ontology_mode = "canonical"
    change = call(wb, "POST", "changes", {"requestId": "bounded", "title": "Synthetic wide impact",
        "targetId": published["identities"]["item-0"], "changeType": "update",
        "before": "", "after": "", "reason": "Synthetic scope"})["change"]
    result = call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    assert len(result["tasks"]) == 47
    assert len(result["impact"]["sourceRefs"]) == 50
    assert result["impact"]["coverage"]["truncated"]
    assert "atomic-task-limit" in result["impact"]["coverage"]["unknown"]
