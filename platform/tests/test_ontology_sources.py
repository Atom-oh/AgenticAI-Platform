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


def test_restoring_membership_does_not_restore_an_inflight_authority_epoch(wb):
    row = asset(wb)
    reader = Sources(context(wb))
    reader.resolve(asset_reference(row))
    original = wb.storage.get(wb.owner, "project", wb.project["id"])
    changed = {**original, "members": {key: value for key, value in original["members"].items() if key != "bob"}}
    changed = wb.storage.put(wb.owner, "project", changed, original["version"])
    restored = wb.storage.put(wb.owner, "project", {**changed, "members": original["members"]}, changed["version"])
    assert restored["authorityRevision"] == original["authorityRevision"] + 2
    with pytest.raises(CollaborationError) as error:
        reader.recheck()
    assert error.value.code == "ontology-authority-changed"


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


def test_workbench_generation_is_checked_even_when_source_permissions_do_not_change(wb):
    indexed(wb)
    hit = call(wb, "GET", "knowledge")["items"][0]
    evidence = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    reference = workbench_reference(evidence)
    manifest = wb.storage.get(wb.owner, "wb_index", "current")
    manifest["sources"][evidence["sourceId"]]["generation"] = "b" * 64
    wb.storage.put(wb.owner, "wb_index", manifest, manifest["version"])
    with pytest.raises(CollaborationError) as error:
        Sources(context(wb)).resolve(reference)
    assert error.value.code == "ontology-source-stale"


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


def test_workbench_expiry_is_rechecked_without_a_record_mutation(wb):
    indexed(wb)
    hit = call(wb, "GET", "knowledge")["items"][0]
    evidence = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    ctx = context(wb)
    ctx.claims["exp"] += 30 * 86400
    reader = Sources(ctx)
    reader.resolve(workbench_reference(evidence))
    wb.now += 2 * 86400 * 1000
    wb.storage.clock = lambda: wb.now
    with pytest.raises(CollaborationError):
        reader.recheck()


def test_authority_budget_is_measured_in_condition_records(wb):
    reader = Sources(context(wb))
    for number in range(90):
        reader._remember("asset", {"id": f"asset-{number}", "version": 1})
    with pytest.raises(CollaborationError) as error:
        reader._remember("asset", {"id": "over-limit", "version": 1})
    assert error.value.status == 422


def test_source_locations_do_not_multiply_the_authority_budget(wb):
    reference = asset_reference(asset(wb))
    refs = [{**reference, "location": {"path": "App.tsx", "line": number + 1}} for number in range(100)]
    assert len(Sources(context(wb)).verify(refs)) == 1


def test_current_sources_enforce_the_bound_audience_for_project_wide_assets(wb):
    reference = {**asset_reference(asset(wb)), "allowedRoles": ["owner"]}
    with pytest.raises(CollaborationError) as error:
        Sources(context(wb, "bob")).resolve(reference)
    assert error.value.status == 403
    with pytest.raises(CollaborationError) as owner_error:
        Sources(context(wb)).resolve(reference)
    assert owner_error.value.code == "ontology-source-audience"


def test_explicit_project_check_is_reconciled_with_the_same_commit_fence(wb):
    ctx = context(wb)
    result = ctx.commit([ctx.write("wb_artifact", {"id": "fenced", "projectId": wb.project["id"], "kind": "test"})],
                       [ctx.check("project", ctx.scope["project"])])
    assert result[0]["id"] == "fenced"


def test_document_library_approval_audience_and_post_read_revocation(wb):
    from test_documents_library import upload, finalize, approve
    project = wb.project["id"]
    uploaded = upload(wb.api, data=b"Synthetic ontology policy.", project=project)
    finalized = finalize(wb.api, uploaded, project=project)
    status, approved = approve(wb.api, finalized, project=project)
    assert status == 200
    revision = approved["revision"]
    document = wb.storage.get(wb.owner, "document", uploaded["document"]["id"])
    reference = {"sourceKind": "document-revision", "sourceId": document["id"],
        "revision": revision["id"], "sha256": revision["sha256"],
        "audienceRevision": str(document["aclVersion"]), "allowedRoles": document["readRoles"]}
    reader = Sources(context(wb, "bob"))
    assert "Synthetic ontology policy." in reader.resolve(reference, text=True)["text"]
    assert reader.recheck()
    wb.storage.put(wb.owner, "document", {**document, "readRoles": ["owner"],
        "aclVersion": document["aclVersion"] + 1}, document["version"])
    with pytest.raises(CollaborationError):
        reader.recheck()
    with pytest.raises(CollaborationError):
        Sources(context(wb, "bob")).authorize(reference)
