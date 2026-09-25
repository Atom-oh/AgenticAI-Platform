import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_analysis import collection
from test_ontology_sources import context
from ontology_runtime.authorization import active_job
from ontology_runtime.capability import AuthorizationDenied
from ontology_runtime.dispatch import RuntimeAnalyzer
from ontology_runtime.admission import classify
from workspace.collaboration import CollaborationError
from workspace.ontology_jobs import submit
from workspace.ontology_sources import asset_reference


def admitted(wb):
    wb.api.ontology_analyzer_ready = True
    wb.api.ontology_analyzer = RuntimeAnalyzer(
        "arn:aws:lambda:ap-northeast-2:180294183052:function:synthetic-authority:1", "a" * 64)
    files = collection(wb)
    for file in files:
        source = wb.storage.get(wb.owner, "asset", file["assetId"])
        classify(context(wb), {"requestId": file["assetId"], "sourceRef": asset_reference(source),
            "classification": "synthetic", "reason": "Synthetic test fixture"})
    queued = submit(context(wb), {"requestId": "runtime", "name": "runtime", "files": files})
    wb.storage.claim_job(wb.owner, queued["job"]["id"])
    return queued["artifact"]


@pytest.mark.parametrize("mutation", ["cancel", "artifact", "membership", "actor", "expired", "title"])
def test_runtime_boundary_checks_the_original_job_and_audience(wb, mutation):
    artifact = admitted(wb)
    if mutation == "cancel":
        job = wb.storage.get(wb.owner, "job", artifact["jobId"])
        wb.storage.put(wb.owner, "job", {**job, "status": "cancelled"}, job["version"])
    elif mutation == "artifact":
        wb.storage.put(wb.owner, "wb_artifact", {**artifact, "status": "failed"}, artifact["version"])
    elif mutation in {"membership", "actor", "title"}:
        project = wb.storage.get(wb.owner, "project", wb.project["id"])
        if mutation == "membership":
            project["members"].pop("bob")
        elif mutation == "actor":
            project["members"]["alice"]["role"] = "designer"
        else:
            project["title"] = "Content-only update"
        wb.storage.put(wb.owner, "project", project, project["version"])
    deadline = wb.now // 1000 if mutation == "expired" else wb.now // 1000 + 60
    if mutation == "title":
        ctx, _, _, _, checks = active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"], deadline=deadline)
        assert ctx.claims["exp"] == deadline
        assert {row["kind"] for row in checks} == {"wb_artifact", "job"}
    else:
        with pytest.raises((AuthorizationDenied, CollaborationError)):
            active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"], deadline=deadline)


def test_runtime_job_cancellation_during_a_tool_transaction_cannot_commit(wb):
    artifact = admitted(wb)
    ctx, _, job, sources, checks = active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"])
    checks.extend(sources.verify(artifact["sourceRefs"]))
    def cancel():
        wb.storage.put(wb.owner, "job", {**job, "status": "cancelled"}, job["version"])
    wb.storage.table().before_transaction = cancel
    with pytest.raises(CollaborationError) as error:
        ctx.commit([ctx.write("ac_operation", {"id": "cancel-race", "ttl": wb.now // 1000 + 60})], checks)
    assert error.value.code == "conflict"
    assert wb.storage.get(wb.owner, "ac_operation", "cancel-race") is None


def test_runtime_retention_is_bounded_and_preserved(wb):
    now = wb.now // 1000
    for kind in ("ac_execution", "ac_operation"):
        row = wb.storage.put(wb.owner, kind, {"id": "retained", "ttl": now + 86400})
        assert row["ttl"] == now + 86400
        for expiry in (None, True, now, now + 32 * 86400):
            with pytest.raises(ValueError, match="retention"):
                wb.storage.put(wb.owner, kind, {"id": "invalid", "ttl": expiry})


def test_classification_binds_workbench_document_identity():
    from ontology_runtime.admission import identifier
    reference = {"sourceKind": "workbench-document", "sourceId": "source", "revision": "v1",
                 "sha256": "a" * 64, "audienceRevision": "1", "location": {"documentId": "first"},
                 "allowedRoles": ["owner"]}
    other = {**reference, "location": {"documentId": "second"}}
    assert identifier(reference) != identifier(other)
    assert identifier(reference) == identifier({**reference, "location": {**reference["location"], "page": 1}})
