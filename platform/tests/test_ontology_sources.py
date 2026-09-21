import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb, indexed, call
from workbench.service import Service
from workspace.collaboration import CollaborationError
from workspace.ontology_sources import Sources, asset_reference, workbench_reference, guideline_reference


def context(wb, actor="alice"):
    return Service(wb.api, wb.collab.resolve_scope(actor, wb.project["id"]),
                   {"sub": actor, "exp": wb.now // 1000 + 3600})


def asset(wb, identifier="code", content=b"export const x=1;"):
    key = wb.storage.key_for(wb.owner, "asset", identifier, "original.ts")
    wb.storage.put_blob_once(key, content, "text/plain")
    return wb.storage.put(wb.owner, "asset", {
        "id": identifier, "projectId": wb.project["id"], "name": identifier + ".ts",
        "uploadStatus": "stored", "parseStatus": "complete", "originalKey": key,
        "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "importRevision": 1})


def test_source_reader_preserves_exact_bytes_and_rechecks_mutation(wb):
    row = asset(wb, content="\ufeffexport const x=1;".encode())
    reader = Sources(context(wb))
    value = reader.resolve(asset_reference(row), text=True)
    assert hashlib.sha256(value["text"].encode()).hexdigest() == row["sha256"]
    assert reader.recheck()
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    with pytest.raises(CollaborationError):
        reader.recheck()
    with pytest.raises(CollaborationError):
        Sources(context(wb)).resolve(asset_reference(row))


def test_source_blob_tampering_and_forged_revision_fail(wb):
    row = asset(wb)
    ref = asset_reference(row)
    with pytest.raises(CollaborationError):
        Sources(context(wb)).resolve({**ref, "revision": "2"})
    wb.storage.put_blob(row["originalKey"], b"tampered source", "text/plain")
    with pytest.raises(CollaborationError, match="변경"):
        Sources(context(wb)).resolve(ref)


def test_membership_revocation_blocks_existing_source_reader(wb):
    row = asset(wb)
    reader = Sources(context(wb, "bob"))
    reader.resolve(asset_reference(row))
    project = wb.storage.get(wb.owner, "project", wb.project["id"])
    project["members"].pop("bob")
    wb.storage.put(wb.owner, "project", project, project["version"])
    with pytest.raises(CollaborationError):
        reader.resolve(asset_reference(row), text=True)


def test_role_or_project_epoch_change_during_read_cannot_refresh_away_the_fence(wb):
    row = asset(wb)
    reader = Sources(context(wb))
    reader.resolve(asset_reference(row), text=True)
    project = wb.storage.get(wb.owner, "project", wb.project["id"])
    project["members"]["alice"]["role"] = "designer"
    wb.storage.put(wb.owner, "project", project, project["version"])
    with pytest.raises(CollaborationError, match="권한"):
        reader.recheck()


def test_workbench_source_keeps_its_existing_acl_and_generation_authority(wb):
    indexed(wb)
    hit = next(item for item in call(wb, "GET", "knowledge")["items"] if item["title"] == "Synthetic withdrawal guidance")
    evidence = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    reference = workbench_reference(evidence)
    result = Sources(context(wb)).resolve(reference, text=True)
    assert result["text"] == "withdrawal limit confirmation notice"
    src = wb.storage.get(wb.owner, "wb_source", evidence["sourceId"])
    wb.storage.put(wb.owner, "wb_source", {**src, "permissionVersion": src["permissionVersion"] + 1}, src["version"])
    with pytest.raises(CollaborationError):
        Sources(context(wb)).resolve(reference)


def test_only_current_published_product_guidance_is_a_current_source(wb):
    product = wb.collab.handle("POST", ["products"], {
        "requestId": "p", "title": "Synthetic product", "description": "Synthetic guidance",
        "conditions": [], "steps": [], "notices": []}, {}, "alice", wb.project["id"])[1]["product"]
    published = wb.collab.handle("POST", ["products", product["id"], "publish"],
                                {"version": product["version"]}, {}, "alice", wb.project["id"])[1]
    reference = guideline_reference(published["guideline"])
    assert "Synthetic product" in Sources(context(wb)).resolve(reference, text=True)["text"]
    changed = wb.storage.get(wb.owner, "product", product["id"])
    wb.storage.put(wb.owner, "product", {**changed, "publishedGuidelineId": "other"}, changed["version"])
    with pytest.raises(CollaborationError):
        Sources(context(wb)).resolve(reference)
