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
