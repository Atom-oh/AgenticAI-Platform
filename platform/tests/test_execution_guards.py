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
