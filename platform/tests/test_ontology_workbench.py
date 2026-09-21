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
    different = {**relation, "sourceRef": dict(original["nodes"][-1]["sourceRef"])}
    original["edges"].append(different)
    original["edges"].append({**relation, "rel": "NEXT"})
    monkeypatch.setattr(knowledge, "legacy_graph", lambda ctx: original)
    imported = import_legacy(context(wb), {"requestId": "parallel", "expectedGeneration": None})
    graph = Ontology(context(wb)).read()
    assert len([e for e in graph["edges"] if e["type"] == "USES"]) == 1
    assert "unmapped-legacy-edge-shape" in imported["coverage"]["unknown"]


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
