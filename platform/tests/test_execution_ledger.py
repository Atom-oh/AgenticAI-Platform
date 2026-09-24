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


# === Task 5: attempts, leases, heartbeats and stage receipts (RUN-01, RUN-02, RUN-04) ================

import hashlib  # noqa: E402
import secrets  # noqa: E402


def running(env):
    storage, ledger, now = env
    job = admit(ledger)
    job = ledger.dispatcher().allocate(OWNER, job["id"])
    job = ledger.tool().claim(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    return job


def op_id():
    return secrets.token_hex(16)


def test_one_current_attempt_and_superseded_fence_rejected(env):
    storage, ledger, now = env
    job = running(env)
    old = dict(job["attempt"])
    ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    with pytest.raises(LedgerError) as error:
        ledger.tool().heartbeat(OWNER, job["id"], old["id"], old["fence"])
    assert error.value.code == "stale-attempt"


def test_double_allocate_cannot_create_second_attempt(env):
    _, ledger, _ = env
    job = admit(ledger)
    ledger.dispatcher().allocate(OWNER, job["id"])
    with pytest.raises(LedgerError):
        ledger.dispatcher().allocate(OWNER, job["id"])


def test_lease_loss_blocks_stage_writes(env):
    storage, ledger, now = env
    job = running(env)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"],
                            receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1"))
    assert error.value.code == "stale-attempt"


@pytest.mark.parametrize("tamper", [
    lambda r: {**r, "attemptId": "att-other"},
    lambda r: {**r, "admissions": [{"decisionId": "adm-2", "revision": "1", "artifactHash": "a" * 64}]},
    lambda r: {**r, "profileHash": "c" * 64},
    lambda r: {**r, "signature": "0" * 64},
    lambda r: {**r, "stage": "deploy"},
])
def test_invalid_receipts_are_rejected_without_writes(env, tamper):
    storage, ledger, _ = env
    job = running(env)
    bad = tamper(receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1"))
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], bad)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["version"] == job["version"]


def resign(body):
    body = {k: v for k, v in body.items() if k != "signature"}
    return {**body, "signature": TestKeyVerifier().sign(body)}


@pytest.mark.parametrize("field,value", [
    ("admissions", [{"decisionId": "adm-2", "revision": "1", "artifactHash": "a" * 64}]),
    ("attemptId", "att-other"),
    ("fence", 99), ("sessionId", "rt-" + "0" * 40), ("executionId", "exec-" + "0" * 32),
])
def test_binding_checks_are_independent_of_the_signature(env, field, value):
    storage, ledger, _ = env
    job = running(env)
    forged = resign({**receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1"), field: value})
    assert TestKeyVerifier().verify(forged)
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], forged)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["version"] == job["version"]


def test_replayed_nonce_is_rejected(env):
    _, ledger, _ = env
    job = running(env)
    r = receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1")
    job = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r)
    with pytest.raises(LedgerError):
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r)


def put_output(storage, job, stage, name, data):
    key = storage.key_for(OWNER, "job", job["id"], f"out/{job['attempt']['id']}/{stage}/{name}")
    storage.put_blob_once(key, data, "application/octet-stream")
    return {"key": key, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "role": "artifact"}


def chained(job, stage, nonce, previous=None, **extra):
    return receipt(TestKeyVerifier(), job=job, stage=stage, nonce=nonce, extra={"previous": previous, **extra})


def test_stage_records_a_verified_chain_with_server_checked_outputs(env):
    storage, ledger, _ = env
    job = running(env)
    out = put_output(storage, job, "context", "context.json", b'{"ok":true}')
    first = chained(job, "context", "n1", outputs=[out], status="ok", result={"summary": "x"})
    job = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], first)
    assert job["stages"][-1]["stage"] == "context" and job["stages"][-1]["outputs"] == [out]
    second = chained(job, "generate", "n2", previous=schema.digest(first),
                     inputs=[{"key": out["key"], "sha256": out["sha256"], "size": out["size"]}], status="ok")
    job = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], second)
    assert [row["stage"] for row in job["stages"]] == ["context", "generate"]


@pytest.mark.parametrize("case", ["missing-output", "hash-mismatch", "outside-prefix", "broken-chain",
                                  "unknown-input", "unknown-field", "expired-receipt"])
def test_receipt_evidence_failures_are_rejected(env, case):
    storage, ledger, now = env
    job = running(env)
    out = put_output(storage, job, "context", "c.json", b"context")
    extra = {"outputs": [out]}
    if case == "missing-output":
        extra["outputs"] = [{**out, "key": out["key"].replace("c.json", "absent.json")}]
    if case == "hash-mismatch":
        extra["outputs"] = [{**out, "sha256": "f" * 64}]
    if case == "outside-prefix":
        foreign = storage.key_for(OWNER, "asset", "x", "c.json")
        storage.put_blob_once(foreign, b"context", "application/octet-stream")
        extra["outputs"] = [{**out, "key": foreign}]
    if case == "broken-chain":
        extra["previous"] = "e" * 64
    if case == "unknown-input":
        extra["inputs"] = [{"key": storage.key_for(OWNER, "asset", "y", "z"), "sha256": "1" * 64, "size": 1}]
    if case == "unknown-field":
        extra["model"] = "unexpected"
    if case == "expired-receipt":
        extra.update(iat=now[0] - 10, exp=now[0] - 1)
    bad = receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1", extra=extra)
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], bad)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["version"] == job["version"]


def test_retried_stage_with_the_same_operation_id_returns_the_same_result(env):
    storage, ledger, _ = env
    job = running(env)
    r = receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1")
    operation = op_id()
    first = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r, operation_id=operation)
    again = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r, operation_id=operation)
    assert again["stages"] == first["stages"] and again["version"] == first["version"]
    other = receipt(TestKeyVerifier(), job=job, stage="context", nonce="n2")
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], other, operation_id=operation)
    assert error.value.code == "operation-changed"


def test_heartbeat_extends_the_lease_but_never_past_the_deadline(env):
    storage, ledger, now = env
    job = running(env)
    now[0] += 60_000
    beat = ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    assert beat["attempt"]["leaseExpiresAt"] == now[0] + PROFILE_DEFAULT["leaseMs"]
    assert beat["attempt"]["heartbeatAt"] == now[0]
    while now[0] + 60_000 < job["deadlineAt"] - 30_000:
        now[0] += 60_000
        beat = ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    now[0] = job["deadlineAt"] - 30_000
    beat = ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    assert beat["attempt"]["leaseExpiresAt"] == job["deadlineAt"]
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1          # lease lost: a late heartbeat is stale
    with pytest.raises(LedgerError) as error:
        ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    assert error.value.code == "stale-attempt"


def test_claim_requires_a_dispatched_attempt_and_is_single(env):
    _, ledger, _ = env
    job = running(env)
    with pytest.raises(LedgerError) as error:
        ledger.tool().claim(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    assert error.value.code == "already-claimed"


def test_fail_releases_quota_and_fences(env):
    storage, ledger, _ = env
    job = running(env)
    failed = ledger.tool().fail(OWNER, job["id"], job["attempt"]["id"], job["fence"], code="runtime-error")
    assert failed["status"] == "failed" and failed["error"]["code"] == "runtime-error"
    assert quota(storage, job) == ([], [])
    with pytest.raises(LedgerError):
        ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])


def test_allocate_exhausts_attempts(env):
    storage, ledger, _ = env
    job = admit(ledger)
    from workspace.execution_ledger import _seed_for_tests
    stored = storage.get(OWNER, "job", job["id"])
    _seed_for_tests(storage, OWNER, "job", {**stored, "attempts": [{"id": "a"}, {"id": "b"}, {"id": "c"}]},
                    stored["version"])
    with pytest.raises(LedgerError) as error:
        ledger.dispatcher().allocate(OWNER, job["id"])
    assert error.value.code == "attempts-exhausted"
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"
    assert quota(storage, job) == ([], [])


def test_attempt_and_session_identifiers(env):
    _, ledger, _ = env
    job = ledger.dispatcher().allocate(OWNER, admit(ledger)["id"])
    assert job["status"] == "dispatched" and job["fence"] == 1 and job["attempt"]["fence"] == 1
    assert len(job["attempt"]["sessionId"]) >= 33 and job["attempt"]["id"].startswith("att-")
