# platform/tests/test_execution_ledger.py
"""RUN-01..RUN-05 (O mode): the platform-execution/1 ledger, offline."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspace_storage import FakeS3, FakeTable  # noqa: E402
from execution_fakes import TestKeyVerifier, receipt  # noqa: E402
from workspace.execution_ledger import Ledger, LedgerError, PROFILE_DEFAULT  # noqa: E402
from workspace.storage import Storage  # noqa: E402
from workspace import ontology_schema as schema  # noqa: E402

OWNER = "project:p1"
ADM = [{"decisionId": "adm-1", "revision": "1", "artifactHash": "a" * 64}]


@pytest.fixture
def env():
    now = [1_800_000_000_000]
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test", clock=lambda: now[0])
    storage.put(OWNER, "project", {"id": "p1", "status": "active", "members": {
        a: {"role": "designer"} for a in ("designer-1", "alice", "bob", "carol")}})
    return storage, Ledger.offline(storage, verifier=TestKeyVerifier()), now


def bump_authority(storage, project_id):
    project = storage.get(OWNER, "project", project_id)
    members = dict(project["members"]); members.pop("carol", None)
    storage.put(OWNER, "project", {**project, "members": members}, project["version"])


def admit(ledger, key="req-1", **over):
    project = ledger.storage.get(OWNER, "project", "p1")
    args = dict(request_key=key, actor="designer-1", project_id="p1", operation="design.generate",
                model="model-a", manifest={"ref": "m1", "hash": "b" * 64}, admissions=ADM,
                backend_config_revision="cfg-1", authorization_expires_at=1_800_000_000_000 + 3_600_000,
                project_authority={"authorityRevision": project["authorityRevision"],
                                   "membershipDigest": schema.digest(project["members"])})
    args.update(over)
    return ledger.api().admit(OWNER, **args)


def quota(storage, job):
    from workspace.execution_ledger import _actor_quota_id, QUOTA_OWNER
    actor = storage.get(QUOTA_OWNER, "exec_quota", _actor_quota_id(job["actor"]))
    project = storage.get(OWNER, "exec_quota", "quota-project-" + job["projectId"])
    return (actor or {}).get("active", []), (project or {}).get("active", [])


def test_admission_is_idempotent_and_hash_bound(env):
    _, ledger, _ = env
    first = admit(ledger)
    assert admit(ledger)["id"] == first["id"] and first["status"] == "queued"
    with pytest.raises(LedgerError) as error:
        admit(ledger, model="model-b")
    assert error.value.code == "request-changed"


def test_deadline_is_shortened_by_authorization_expiry(env):
    _, ledger, now = env
    job = admit(ledger, authorization_expires_at=now[0] + 60_000)
    assert job["deadlineAt"] == now[0] + 60_000


def test_admission_requires_consumed_admission_decisions(env):
    with pytest.raises(LedgerError) as error:
        admit(env[1], admissions=[])
    assert error.value.code == "admission-required"


def test_facades_expose_only_their_role(env):
    ledger = env[1]
    assert not hasattr(ledger.api(), "allocate") and not hasattr(ledger.tool(), "admit")
    assert not hasattr(ledger.reconciler(), "finish") and hasattr(ledger.reconciler(), "reconcile")


def test_request_keys_are_actor_scoped_and_ids_are_random(env):
    _, ledger, _ = env
    a = admit(ledger, key="same", actor="alice")
    b = admit(ledger, key="same", actor="bob")
    assert a["id"] != b["id"] and a["id"].startswith("exec-") and len(a["id"]) == 37


def test_member_remove_and_readd_invalidates_the_admitted_job(env):
    storage, ledger, _ = env
    job = admit(ledger)
    bump_authority(storage, "p1")          # helper: re-put the project record with a changed member set
    with pytest.raises(LedgerError) as error:
        ledger.dispatcher().allocate(OWNER, job["id"])
    assert error.value.code == "authority-changed"
    failed = storage.get(OWNER, "job", job["id"])
    assert failed["status"] == "failed" and failed["fence"] == job["fence"] + 1
    assert quota(storage, job) == ([], [])


def test_concurrency_limits(env):
    _, ledger, _ = env
    admit(ledger, key="k1", actor="alice")
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="k2", actor="alice")
    assert error.value.code == "concurrency-actor"
    admit(ledger, key="k3", actor="bob")
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="k4", actor="carol")
    assert error.value.code == "concurrency-project"


def test_production_rejects_test_verifier(env):
    with pytest.raises(PermissionError):
        Ledger.production(env[0], verifier=TestKeyVerifier())


def test_direct_construction_and_foreign_offline_verifiers_are_rejected(env):
    with pytest.raises(PermissionError):
        Ledger(env[0], TestKeyVerifier(), object())

    class Forged:
        def verify(self, receipt):
            return True
    with pytest.raises(PermissionError):
        Ledger.offline(env[0], verifier=Forged())


def test_cancel_fences_the_job(env):
    storage, ledger, _ = env
    job = admit(ledger)
    cancelled = ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    assert cancelled["status"] == "cancelled" and cancelled["fence"] == job["fence"] + 1
    assert quota(storage, job) == ([], [])
    with pytest.raises(LedgerError) as error:
        ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    assert error.value.code == "terminal"


def test_cancel_by_another_member_is_refused(env):
    _, ledger, _ = env
    job = admit(ledger)
    with pytest.raises(LedgerError) as error:
        ledger.api().cancel(OWNER, job["id"], actor="bob")
    assert error.value.code == "forbidden"


def test_job_record_carries_the_v1_fields(env):
    storage, ledger, _ = env
    job = admit(ledger)
    assert job["task"] == "agentcore-execution" and job["executionSchemaVersion"] == 1
    assert job["backend"] == "agentcore" and job["profile"]["hash"] == schema.digest(PROFILE_DEFAULT)
    assert job["budget"] == {"calls": 0, "maxCalls": 120, "tokensReserved": 0, "tokensUsed": 0,
                             "tokenBudget": PROFILE_DEFAULT["tokenBudget"]}
    assert job["authority"]["authorityRevision"] == storage.get(OWNER, "project", "p1")["authorityRevision"]
    assert quota(storage, job) == ([job["id"]], [job["id"]])


def test_admission_indexes_due_work_for_the_reconciler(env):
    from workspace.storage import DUE_OWNER
    storage, ledger, _ = env
    job = admit(ledger)
    [due] = storage.list(DUE_OWNER, "exec_due")
    assert due["id"] == job["dueId"] and due["status"] == "pending" and due["dueAt"] == job["deadlineAt"]
    assert due["targetOwner"] == OWNER and due["ref"] == {"kind": "job", "id": job["id"]}
    ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    assert [row["status"] for row in storage.list(DUE_OWNER, "exec_due")] == ["done"]


def test_admission_takes_authority_from_the_current_project(env):
    storage, ledger, _ = env
    stale = storage.get(OWNER, "project", "p1")
    bump_authority(storage, "p1")
    with pytest.raises(LedgerError) as error:
        admit(ledger, project_authority={"authorityRevision": stale["authorityRevision"],
                                         "membershipDigest": schema.digest(stale["members"])})
    assert error.value.code == "authority-changed"
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="k-outsider", actor="mallory")
    assert error.value.code == "authority-changed"


def test_completion_scope_preflight(env):
    storage, ledger, _ = env
    before = len(storage.table().transactions)
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="k89", completion_scope={"nonSourceOperations": 12, "sourceChecks": 89, "sourceBindings": 1})
    assert error.value.code == "execution-completion-scope"
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="k51", completion_scope={"nonSourceOperations": 12, "sourceChecks": 1, "sourceBindings": 51})
    assert error.value.code == "execution-completion-scope"
    assert len(storage.table().transactions) == before          # no write, no dispatch, no model call
    job = admit(ledger, key="k88", completion_scope={"nonSourceOperations": 12, "sourceChecks": 88, "sourceBindings": 50})
    assert job["completionScope"]["sourceChecks"] == 88


def test_unknown_operation_is_refused(env):
    with pytest.raises(LedgerError) as error:
        admit(env[1], operation="design.deploy")
    assert error.value.code == "unknown-operation"
