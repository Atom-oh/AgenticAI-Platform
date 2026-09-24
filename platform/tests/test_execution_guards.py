# platform/tests/test_execution_guards.py
"""RUN-01: the storage chokepoint and every legacy writer refuse reserved execution records."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspace_storage import FakeS3, FakeTable  # noqa: E402
from workspace.storage import Conflict, ReservedRecord, Storage  # noqa: E402

NEW = {"id": "exec-1", "task": "agentcore-execution", "executionSchemaVersion": 1, "status": "queued"}


@pytest.fixture
def storage():
    return Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test")


def seed(storage, record, kind="job", owner="project:p1", expected_version=None):
    from workspace.execution_ledger import _seed_for_tests
    return _seed_for_tests(storage, owner, kind, record, expected_version)


def reports(storage, owner="project:p1"):
    return storage.list(owner, "exec_report")


def due_entries(storage):
    from workspace.storage import DUE_OWNER
    return storage.list(DUE_OWNER, "exec_due")


def test_non_ledger_write_of_reserved_item_is_refused(storage):
    with pytest.raises(ReservedRecord):
        storage.put("project:p1", "job", dict(NEW))
    assert storage.get("project:p1", "job", "exec-1") is None


@pytest.mark.parametrize("variant", [
    {"task": "agentcore-execution"},
    {"task": "run", "executionSchemaVersion": 1},
    {"task": "run", "executionSchemaVersion": "x"},
    {"task": "run", "executionSchemaVersion": None},
    {"task": "run", "executionId": "exec-9"},
])
def test_malformed_or_partial_discriminators_are_never_legacy(storage, variant):
    saved = seed(storage, {"id": "j", "status": "queued", **variant})
    with pytest.raises(ReservedRecord):
        storage.put("project:p1", "job", {**saved, "status": "failed"}, saved["version"])
    assert storage.claim_job("project:p1", "j") is None
    assert storage.get("project:p1", "job", "j")["status"] == "queued"
    assert reports(storage)


def test_reserved_refusal_is_a_conflict_and_writes_nothing_in_transactions(storage):
    saved = seed(storage, dict(NEW))
    other = storage.put("project:p1", "run", {"id": "r1"})
    with pytest.raises(Conflict):
        storage.put_many([
            {"owner": "project:p1", "kind": "run", "item": {**other, "status": "failed"}, "expected_version": 1},
            {"owner": "project:p1", "kind": "job", "item": {**saved, "status": "failed"}, "expected_version": saved["version"]}])
    assert storage.get("project:p1", "run", "r1")["status"] == "queued"


def test_db_fence_rejects_a_reserved_record_that_appears_after_the_read(storage):
    legacy = storage.put("project:p1", "job", {"id": "j2", "task": "run"})
    table = storage.table()

    def swap():
        key = next(k for k in table.items if k[1] == "job#j2")
        table.items[key] = {**table.items[key], "executionSchemaVersion": 1}
    table.before_transaction = swap
    with pytest.raises(Conflict):
        storage.put_many([{"owner": "project:p1", "kind": "job",
                           "item": {**legacy, "status": "failed"}, "expected_version": 1}])


@pytest.mark.parametrize("kind", ["wb_artifact", "run", "release", "docanalysis"])
def test_unmarked_artifact_linked_to_a_reserved_job_is_refused(storage, kind):
    seed(storage, dict(NEW))
    artifact = seed(storage, {"id": "a1", "jobId": "exec-1", "status": "processing"}, kind=kind)   # created by the ledger
    with pytest.raises(ReservedRecord):
        storage.put("project:p1", kind, {**artifact, "status": "failed"}, artifact["version"])
    assert storage.get("project:p1", kind, "a1")["status"] == "processing"


def test_missing_new_ledger_job_does_not_make_an_artifact_legacy(storage):
    artifact = seed(storage, {"id": "a2", "jobId": "exec-gone", "status": "processing"}, kind="wb_artifact")
    with pytest.raises(ReservedRecord):
        storage.put("project:p1", "wb_artifact", {**artifact, "status": "failed"}, artifact["version"])


def test_stale_supplied_job_dict_is_ignored_by_the_guard(storage):
    from workbench.worker import _mark_failed
    from workspace.worker import Worker
    seed(storage, dict(NEW))
    art = seed(storage, {"id": "a3", "projectId": "p1", "jobId": "exec-1", "status": "processing"}, kind="wb_artifact")
    _mark_failed(Worker(storage=storage), "project:p1",
                 {"id": "exec-1", "task": "workbench", "input": {"operation": "ontology-analyze", "artifactId": "a3"}},
                 "job-timeout")
    assert storage.get("project:p1", "wb_artifact", "a3")["version"] == art["version"]


def test_stale_supplied_job_dict_is_ignored_even_with_a_current_project(storage):
    """Same as above, but the project exists, so the refusal comes from the storage linkage read."""
    from workbench.worker import _mark_failed
    from workspace.worker import Worker
    storage.put("project:p1", "project", {"id": "p1", "status": "active", "members": {"alice": {"role": "owner"}}})
    seed(storage, dict(NEW))
    art = seed(storage, {"id": "a3", "projectId": "p1", "jobId": "exec-1", "status": "processing"}, kind="wb_artifact")
    _mark_failed(Worker(storage=storage), "project:p1",
                 {"id": "exec-1", "task": "workbench", "input": {"operation": "ontology-analyze", "artifactId": "a3"}},
                 "job-timeout")
    assert storage.get("project:p1", "wb_artifact", "a3")["version"] == art["version"]
    assert any(row["reason"] == "linked-reserved-job" for row in reports(storage))


def test_linkage_mismatch_is_refused(storage):
    storage.put("project:p1", "job", {"id": "legacy-job", "task": "run"})
    artifact = storage.put("project:p1", "run", {"id": "r2", "jobId": "legacy-job"})
    with pytest.raises(ReservedRecord) as error:
        storage.put("project:p1", "run", {**artifact, "jobId": "exec-1"}, artifact["version"])
    assert error.value.reason == "linkage-mismatch"


def test_ledger_writer_token_is_not_obtainable_outside_the_ledger():
    from workspace import storage as module
    with pytest.raises(PermissionError):
        module._ledger_writer()


def test_human_writer_token_is_not_obtainable_outside_the_human_paths():
    from workspace import storage as module
    with pytest.raises(PermissionError):
        module._human_writer()


def test_report_is_metadata_only_counted_and_delivered_through_a_due_entry(storage):
    saved = seed(storage, dict(NEW, secret="payload text"))
    for _ in range(2):
        with pytest.raises(ReservedRecord):
            storage.put("project:p1", "job", {**saved, "status": "failed"}, saved["version"])
    [report] = reports(storage)
    assert report["count"] == 2 and report["recordId"] == "exec-1" and report["kind"] == "job"
    assert "payload text" not in str(report)
    assert set(report) - {"id", "version", "createdAt", "updatedAt", "status"} == {
        "kind", "recordId", "reason", "count", "firstAt", "lastAt", "dueId"}
    [due] = due_entries(storage)
    assert due["status"] == "pending" and due["type"] == "report"
    assert due["targetOwner"] == "project:p1" and due["ref"] == {"kind": "job", "id": "exec-1"}


def test_report_failure_never_masks_the_refusal(storage, monkeypatch):
    saved = seed(storage, dict(NEW))
    monkeypatch.setattr(storage, "put_many", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(ReservedRecord):
        storage.put("project:p1", "job", {**saved, "status": "failed"}, saved["version"])


# --- creation and first linkage are unaffected (review round 7, AA1) ---------------------------

def test_first_linkage_and_creation_with_the_job_succeed(storage):
    asset = storage.put("project:p1", "asset", {"id": "as1", "uploadStatus": "uploading"})
    linked = storage.put("project:p1", "asset", {**asset, "jobId": "finalize-as1", "status": "processing"},
                         asset["version"])
    assert linked["jobId"] == "finalize-as1"
    revision = storage.put("project:p1", "docrevision", {"id": "rev1", "status": "uploaded"})
    saved = storage.put_many([
        {"owner": "project:p1", "kind": "docrevision",
         "item": {**revision, "status": "processing", "jobId": "doc-job"}, "expected_version": revision["version"]},
        {"owner": "project:p1", "kind": "job", "item": {"id": "doc-job", "task": "document-finalize"},
         "expected_version": None}])
    assert saved[0]["status"] == "processing"


def test_unknown_linkage_repair_is_refused_and_reported(storage):
    artifact = storage.put("project:p1", "wb_artifact", {"id": "orphan", "projectId": "p1", "jobId": "legacy-gone",
                                                          "status": "processing"})
    with pytest.raises(ReservedRecord) as error:
        storage.put("project:p1", "wb_artifact", {**artifact, "status": "failed"}, artifact["version"])
    assert error.value.reason == "unknown-linkage"
    assert storage.get("project:p1", "wb_artifact", "orphan")["status"] == "processing"
    assert [row["reason"] for row in reports(storage)] == ["unknown-linkage"]
    # A metadata-only update that keeps the status is not a repair.
    storage.put("project:p1", "wb_artifact", {**artifact, "note": "x"}, artifact["version"])


# --- human-authorized writer (review round 3, F1) ------------------------------------------------

def _linked_run(api):
    from test_workspace_http import _tested_run
    run, request = _tested_run(api)
    seed(api.storage, {**run, "executionId": "exec-1"}, kind="run", owner="alice", expected_version=run["version"])
    return api.storage.get("alice", "run", run["id"]), request


@pytest.fixture
def http_api():
    from test_workspace_http import FakeLambda, FakeRules
    from workspace.http import WorkspaceAPI
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test")
    return WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker", rules=FakeRules)


def test_human_run_approval_of_a_design_linked_run_succeeds(http_api):
    from test_workspace_http import call
    run, request = _linked_run(http_api)
    status, payload, _ = call(http_api, "POST", f"/runs/{run['id']}/approve", request)
    assert status == 200, payload
    assert payload["run"]["approval"]["artifactSha256"] == request["artifactSha256"]
    assert payload["run"]["executionId"] == "exec-1"


def test_legacy_repair_of_the_same_linked_run_is_still_refused(http_api):
    from workspace.worker import Worker
    run, _ = _linked_run(http_api)
    with pytest.raises(ReservedRecord):
        Worker(storage=http_api.storage)._update("alice", "run", run["id"], status="failed", error="x")
    assert http_api.storage.get("alice", "run", run["id"])["version"] == run["version"]


def test_human_writer_cannot_move_a_linked_run_into_an_outcome_status(http_api):
    from workspace import storage as module
    run, _ = _linked_run(http_api)
    for status in ("failed", "needs_changes"):
        with pytest.raises(ReservedRecord):
            http_api.storage.put_many([{"owner": "alice", "kind": "run", "item": {**run, "status": status},
                                        "expected_version": run["version"]}], _writer=module._HUMAN_WRITER)
    job = seed(http_api.storage, dict(NEW), owner="alice")
    with pytest.raises(ReservedRecord):    # never for job kinds
        http_api.storage.put("alice", "job", {**job, "progress": 5}, job["version"], _writer=module._HUMAN_WRITER)
    assert http_api.storage.get("alice", "run", run["id"])["version"] == run["version"]


# --- orphan resolution by the IAM-only reconciler (review rounds 37-38, BE1/BF2) -----------------

def _orphan(storage):
    from workbench.worker import _mark_failed
    from workspace.worker import Worker
    storage.put("project:p1", "project", {"id": "p1", "status": "active", "members": {"alice": {"role": "owner"}}})
    art = storage.put("project:p1", "wb_artifact", {"id": "orph", "projectId": "p1", "jobId": "ontology-job-x",
                                                    "status": "processing"})
    _mark_failed(Worker(storage=storage), "project:p1",
                 {"id": "ontology-job-x", "input": {"operation": "ontology-analyze", "artifactId": "orph"}},
                 "dispatch-interrupted")
    assert storage.get("project:p1", "wb_artifact", "orph")["version"] == art["version"]
    return art


def test_orphan_is_reported_and_failed_by_an_ordinary_scheduler_event(storage):
    from workspace.execution_ledger import _run_due
    _orphan(storage)
    assert [row["reason"] for row in reports(storage)] == ["unknown-linkage"]
    [due] = due_entries(storage)
    assert due["status"] == "pending"
    _run_due(storage)       # no supplied ids
    failed = storage.get("project:p1", "wb_artifact", "orph")
    assert failed["status"] == "failed" and failed["errorCode"] == "orphan-legacy-job"
    assert [row["status"] for row in due_entries(storage)] == ["done"]


def test_orphan_whose_job_reappeared_is_left_untouched(storage):
    from workspace.execution_ledger import _run_due
    art = _orphan(storage)
    storage.put("project:p1", "job", {"id": "ontology-job-x", "task": "workbench"})
    _run_due(storage)
    assert storage.get("project:p1", "wb_artifact", "orph")["version"] == art["version"]
    assert [row["status"] for row in due_entries(storage)] == ["done"]


def test_orphan_resolution_aborts_when_the_job_is_recreated_before_submission(storage):
    from workspace.execution_ledger import _resolve_orphan
    art = _orphan(storage)
    [due] = due_entries(storage)
    table = storage.table()

    def recreate():
        storage.put("project:p1", "job", {"id": "ontology-job-x", "task": "workbench"})
    table.before_transaction = recreate
    with pytest.raises(Conflict):
        _resolve_orphan(storage, due)
    assert storage.get("project:p1", "wb_artifact", "orph")["status"] == "processing"
    assert storage.get("project:p1", "wb_artifact", "orph")["version"] == art["version"]
    assert storage.get("project:p1", "job", "ontology-job-x")["status"] == "queued"
    assert [row["status"] for row in due_entries(storage)] == ["pending"]


# === Task 3: RUN-01 legacy writer inventory (W1-W12), each driving the REAL legacy function ===========

from test_workspace_http import FakeLambda, FakeRules  # noqa: E402
from test_workbench_core import wb  # noqa: E402,F401  (fixture)
from workspace.http import WorkspaceAPI  # noqa: E402
from workspace.worker import Worker  # noqa: E402

MARKED = {"id": "art-1", "projectId": "p1", "status": "processing", "jobId": "exec-1",
          "executionSchemaVersion": 1, "executionId": "exec-1"}


def api_for(storage):
    return WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker", rules=FakeRules)


def unchanged(storage, kind, record, owner="project:p1"):
    assert storage.get(owner, kind, record["id"])["version"] == record["version"]


def test_w1_w3_worker_handle_never_runs_or_fails_a_reserved_job(storage):
    job = seed(storage, dict(NEW))
    worker = Worker(storage=storage)
    assert worker.handle({"owner": "project:p1", "jobId": "exec-1"})["status"] == "duplicate-or-unavailable"
    unchanged(storage, "job", job)
    assert reports(storage)


def test_w2_w4_worker_update_and_progress_writers_refuse_a_reserved_job(storage):
    job = seed(storage, {**NEW, "status": "running"})
    worker = Worker(storage=storage)
    with pytest.raises(Conflict):
        worker._update("project:p1", "job", "exec-1", progress={"stage": "x", "percent": 10})
    with pytest.raises(Conflict):
        worker._update("project:p1", "job", "exec-1", status="completed", progress=100)
    unchanged(storage, "job", job)


def test_w8_stale_expiry_skips_reserved_jobs(storage):
    job = seed(storage, {**NEW, "status": "running"})
    api = api_for(storage)
    storage.clock = lambda: job["updatedAt"] + 17 * 60 * 1000
    assert api._expire_job("project:p1", job)["status"] == "running"
    unchanged(storage, "job", job)
    assert reports(storage)


def test_w9_dispatch_failure_and_retry_skip_reserved_jobs(storage):
    job = seed(storage, dict(NEW))
    api = api_for(storage)
    api.lambda_client.fail = True       # FakeLambda raising on invoke
    with pytest.raises(Exception):
        api._invoke("project:p1", job)
    unchanged(storage, "job", job)
    failed = seed(storage, {**NEW, "id": "exec-2", "status": "failed", "errorCode": "dispatch-failed"})
    assert api._retry_dispatch("project:p1", failed)["status"] == "failed"
    unchanged(storage, "job", failed)
    assert reports(storage)


def test_w9_dispatch_failure_does_not_fail_a_reserved_target(storage):
    job = storage.put("project:p1", "job", {"id": "finalize-x", "task": "finalize", "input": {"assetId": "x"}})
    target = seed(storage, {"id": "x", "status": "processing", "uploadStatus": "processing",
                            "executionId": "exec-1"}, kind="asset")
    api = api_for(storage)
    api.lambda_client.fail = True
    with pytest.raises(Exception):
        api._invoke("project:p1", job)
    unchanged(storage, "asset", target)
    unchanged(storage, "job", job)


def test_w10_new_job_rejects_reserved_task(storage):
    from workspace.http import HTTPError
    with pytest.raises(HTTPError) as error:
        api_for(storage)._new_job("project:p1", "x1", "agentcore-execution", {}, "h" * 64)
    assert error.value.code == "reserved-task"
    assert storage.get("project:p1", "job", "x1") is None


def test_w5_mark_failed_refuses_marked_artifact_even_without_a_job(storage):
    from workbench.worker import _mark_failed
    art = seed(storage, MARKED, kind="wb_artifact")
    _mark_failed(Worker(storage=storage), "project:p1",
                 {"id": "exec-1", "input": {"operation": "ontology-analyze", "artifactId": "art-1"}}, "job-timeout")
    unchanged(storage, "wb_artifact", art)
    assert [row["reason"] for row in reports(storage)] == ["marked-artifact"]


def test_w5_mark_failed_returns_for_a_reserved_job_dict(storage):
    from workbench.worker import _mark_failed
    art = seed(storage, MARKED, kind="wb_artifact")
    _mark_failed(Worker(storage=storage), "project:p1",
                 {**NEW, "input": {"operation": "ontology-analyze", "artifactId": "art-1"}}, "job-timeout")
    unchanged(storage, "wb_artifact", art)


def test_w5_service_dispatch_failure_cannot_fail_a_marked_artifact(wb):
    from workbench.worker import _mark_failed
    art = seed(wb.storage, {**MARKED, "projectId": wb.project["id"]}, kind="wb_artifact", owner=wb.owner)
    _mark_failed(wb.api, wb.owner, {"id": "exec-1", "input": {"operation": "skill-execute", "artifactId": "art-1"}},
                 "dispatch-failed")
    unchanged(wb.storage, "wb_artifact", art, owner=wb.owner)


@pytest.fixture
def wb_context():
    from test_ontology_sources import context

    def build(target):
        return context(target)
    return build


def test_w6_analysis_read_repair_leaves_marked_artifact_for_the_reconciler(wb, wb_context):
    from workspace import ontology_jobs
    art = seed(wb.storage, {**MARKED, "projectId": wb.project["id"], "jobInput": {"authorizationExpiresAt": 0}},
               kind="wb_artifact", owner=wb.owner)
    assert ontology_jobs.reconcile(wb_context(wb), art)["version"] == art["version"]
    unchanged(wb.storage, "wb_artifact", art, owner=wb.owner)
    assert reports(wb.storage, wb.owner)


def test_w6_analysis_read_repair_of_a_reserved_job_changes_nothing(wb, wb_context):
    from workspace import ontology_jobs
    job = seed(wb.storage, {**NEW, "status": "queued"}, owner=wb.owner)
    art = seed(wb.storage, {"id": "art-2", "projectId": wb.project["id"], "status": "queued", "jobId": "exec-1",
                            "jobInput": {"authorizationExpiresAt": 0, "operation": "ontology-analyze",
                                         "artifactId": "art-2"}}, kind="wb_artifact", owner=wb.owner)
    ontology_jobs.reconcile(wb_context(wb), art)
    unchanged(wb.storage, "wb_artifact", art, owner=wb.owner)
    unchanged(wb.storage, "job", job, owner=wb.owner)
    assert reports(wb.storage, wb.owner)


def test_w7_analysis_completion_with_a_reserved_job_publishes_nothing(wb, wb_context):
    from test_ontology_analysis import collection
    from workbench.worker import process
    from workspace.ontology_analysis import local_analyze
    from workspace.ontology_jobs import submit
    from workspace.ontology_store import Ontology
    from types import SimpleNamespace
    wb.api.ontology_analyzer_ready = True
    queued = submit(wb_context(wb), {"requestId": "reserved", "name": "example", "files": collection(wb)})
    job = wb.storage.get(wb.owner, "job", queued["job"]["id"])
    job = seed(wb.storage, {**job, "executionId": "exec-1"}, owner=wb.owner, expected_version=job["version"])
    before = Ontology(wb_context(wb)).current()
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    with pytest.raises(Exception):
        process(worker, wb.owner, job)
    assert Ontology(wb_context(wb)).current() == before
    unchanged(wb.storage, "job", job, owner=wb.owner)
    artifact = wb.storage.get(wb.owner, "wb_artifact", queued["artifact"]["id"])
    assert artifact["status"] == "queued" and "generation" not in artifact
    assert reports(wb.storage, wb.owner)


# --- W11 document jobs ---------------------------------------------------------------------------

@pytest.fixture
def docs():
    from test_documents_library import api as build  # noqa: F401
    from test_documents_library import DocumentHost
    from graph.store import LocalGraphStore
    host = DocumentHost(storage=Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test"),
                        lambda_client=FakeLambda(), worker_fn="worker",
                        directory=lambda q: [{"sub": "alice", "displayName": "alice"}])
    host.graph_store = LocalGraphStore()
    return host


def _doc_upload(host):
    from test_documents_library import upload
    result = upload(host)
    scope = host.collaboration.resolve_scope("alice", None)
    revision = host.storage.get("alice", "docrevision", result["revision"]["id"])
    job = host.storage.get("alice", "job", result["job"]["id"])
    return scope, revision, job


def test_w11_document_reconcile_leaves_a_marked_target(docs):
    from documents.jobs import reconcile
    scope, revision, job = _doc_upload(docs)
    marked = seed(docs.storage, {**revision, "executionId": "exec-1"}, kind="docrevision", owner="alice",
                  expected_version=revision["version"])
    docs.storage.clock = lambda: job["updatedAt"] + 17 * 60_000
    assert reconcile(docs, scope, "docrevision", marked)["version"] == marked["version"]
    unchanged(docs.storage, "docrevision", marked, owner="alice")
    unchanged(docs.storage, "job", job, owner="alice")
    assert reports(docs.storage, "alice")


def test_w11_document_reconcile_leaves_a_target_of_a_reserved_job(docs):
    from documents.jobs import reconcile
    scope, revision, job = _doc_upload(docs)
    reserved = seed(docs.storage, {**job, "executionId": "exec-1"}, owner="alice", expected_version=job["version"])
    docs.storage.clock = lambda: reserved["updatedAt"] + 17 * 60_000
    assert reconcile(docs, scope, "docrevision", revision)["version"] == revision["version"]
    unchanged(docs.storage, "docrevision", revision, owner="alice")
    unchanged(docs.storage, "job", reserved, owner="alice")
    assert reports(docs.storage, "alice")


def test_w11_document_reconcile_of_an_ordinary_stale_job_is_unchanged(docs):
    from documents.jobs import reconcile
    scope, revision, job = _doc_upload(docs)
    docs.storage.clock = lambda: job["updatedAt"] + 17 * 60_000
    assert reconcile(docs, scope, "docrevision", revision)["status"] == "failed"
    assert docs.storage.get("alice", "job", job["id"])["errorCode"] == "job-timeout"


def test_w11_expire_raw_job_and_fail_work_skip_a_reserved_job(docs):
    from documents.jobs import expire_raw_job, fail_work
    scope, revision, job = _doc_upload(docs)
    reserved = seed(docs.storage, {**job, "executionId": "exec-1", "status": "running"}, owner="alice",
                    expected_version=job["version"])
    docs.storage.clock = lambda: reserved["updatedAt"] + 17 * 60_000
    assert expire_raw_job(docs, scope, reserved)["version"] == reserved["version"]
    assert fail_work(docs, "alice", reserved, "failed") is False
    unchanged(docs.storage, "job", reserved, owner="alice")
    unchanged(docs.storage, "docrevision", revision, owner="alice")
    assert reports(docs.storage, "alice")


def test_w11_fail_work_of_an_ordinary_running_job_is_unchanged(docs):
    from documents.jobs import fail_work
    _, revision, job = _doc_upload(docs)
    running = docs.storage.claim_job("alice", job["id"])
    assert fail_work(docs, "alice", running, "synthetic failure") is True
    assert docs.storage.get("alice", "docrevision", revision["id"])["status"] == "failed"


# --- W12 skill execution completion --------------------------------------------------------------

def test_w12_skill_execution_completion_refuses_a_marked_artifact(wb, wb_context, monkeypatch):
    from workbench import knowledge, skills
    from workbench.service import Service
    ctx = wb_context(wb)
    skill = wb.storage.put(wb.owner, "wb_skill", {"id": "skill-1", "projectId": wb.project["id"],
                                                  "status": "APPROVED"})
    key, digest = ctx.put_json("wb_artifact", "art-3", "input.json", {"input": {"q": "x"}})
    art = seed(wb.storage, {**MARKED, "id": "art-3", "projectId": wb.project["id"], "status": "queued",
                            "inputHash": digest, "inputKey": key, "skillVersion": 1, "sourceRefs": []},
               kind="wb_artifact", owner=wb.owner)
    monkeypatch.setattr(skills, "resolve_approved", lambda *a: {"skill": wb.storage.get(wb.owner, "wb_skill", "skill-1")})
    monkeypatch.setattr(knowledge, "verify_refs", lambda *a: [])
    monkeypatch.setattr(Service, "gate", lambda self, *a, **k: None)
    wb.api.workbench_executor = lambda **k: {"status": "ok", "answer": "a", "evidenceIds": []}
    with pytest.raises(Exception):
        skills.process_execution(ctx, {"artifactId": "art-3", "inputHash": digest, "skillVersion": 1,
                                       "skillId": skill["id"], "contentHash": "c"})
    unchanged(wb.storage, "wb_artifact", art, owner=wb.owner)
    assert reports(wb.storage, wb.owner)


# --- Out of scope: the studio store is a separate table -------------------------------------------

def test_studio_store_is_a_separate_table_and_handler_never_imports_workspace_storage(monkeypatch):
    from studio.store import StudioStore
    monkeypatch.setenv("STUDIO_TABLE", "studio-table")
    monkeypatch.setenv("WORKSPACE_TABLE", "workspace-table")
    assert StudioStore()._table_name != Storage().table_name
    source = (Path(__file__).resolve().parents[1] / "api" / "handlers" / "studio.py").read_text()
    assert "workspace.storage" not in source and "WORKSPACE_TABLE" not in source
    store_source = (Path(__file__).resolve().parents[1] / "studio" / "store.py").read_text()
    assert "WORKSPACE_TABLE" not in store_source


# --- PR #27 review 2: the linked-job exclusion is fenced in the artifact's own transaction -----------

def test_linked_job_marked_after_the_guard_read_aborts_a_worker_update(storage):
    """Finding 9: a real Worker._update whose linked job gains a reserved discriminator after the guard read."""
    from workspace.worker import Worker
    job = storage.put("alice", "job", {"id": "legacy-1", "task": "generate", "status": "running"})
    run = storage.put("alice", "run", {"id": "run-1", "jobId": "legacy-1", "status": "processing"})
    original, injected = storage.get, []

    def racing_get(owner, kind, identifier):
        value = original(owner, kind, identifier)
        if kind == "job" and identifier == "legacy-1" and not injected:
            injected.append(True)          # the ledger marks the job between the guard read and the write
            seed(storage, {**original(owner, kind, identifier), "executionId": "exec-" + "1" * 32},
                 owner="alice", expected_version=job["version"])
        return value
    storage.get = racing_get
    with pytest.raises((ReservedRecord, Conflict, ValueError)):
        Worker(storage=storage)._update("alice", "run", run["id"], status="completed")
    storage.get = original
    assert injected and storage.get("alice", "run", "run-1")["status"] == "processing"


def test_linked_job_fence_is_a_transaction_predicate(storage):
    job = storage.put("alice", "job", {"id": "legacy-2", "task": "generate", "status": "running"})
    run = storage.put("alice", "run", {"id": "run-2", "jobId": "legacy-2", "status": "processing"})
    storage.put("alice", "run", {**run, "status": "completed"}, run["version"])
    [check] = [entry["ConditionCheck"] for entry in storage.table().transactions[-1]["TransactItems"]
               if "ConditionCheck" in entry]
    assert check["Key"]["sk"] == "job#legacy-2"
    assert check["ExpressionAttributeValues"] == {":version": job["version"]}
