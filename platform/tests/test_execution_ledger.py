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
from workspace.execution_ledger import receipt_hash, STAGE_SERVICES, OPERATIONS as OPS  # noqa: E402
from workspace import execution_ledger as _ledger_module  # noqa: E402

OWNER = "project:p1"
import hashlib as _hashlib  # noqa: E402
import json as _json_module  # noqa: E402
XFER_DATA = bytes(range(256)) * 2048 + b"tail"            # the admitted derivative served by the xfer fixture
ADM = [{"decisionId": "adm-1", "revision": "1", "artifactHash": _hashlib.sha256(XFER_DATA).hexdigest()}]


def admitted_sources(storage):
    """The admitted derivative, its admission-decision record, and resolver/prior-authority adapters.

    Both adapters return the transactional authority predicate of the record that grants access; withdrawing
    the decision (status != "ready") or revoking a run round makes them refuse.
    """
    key = storage.key_for(OWNER, "asset", "adm-src", "derivative.bin")
    try:
        storage.put_blob_once(key, XFER_DATA, "application/octet-stream")
    except Exception:
        pass
    if storage.get(OWNER, "asset", "adm-src") is None:
        storage.put(OWNER, "asset", {"id": "adm-src", "status": "ready", "decisionId": "adm-1"})

    def resolver(owner, admission):
        record = storage.get(OWNER, "asset", "adm-src")
        if admission.get("decisionId") != "adm-1" or not record or record.get("status") != "ready":
            return None
        return {"key": key, "sha256": _hashlib.sha256(XFER_DATA).hexdigest(),
                "check": {"owner": OWNER, "kind": "asset", "id": "adm-src", "version": record["version"]}}

    def prior_authority(owner, job, prior):
        if prior.get("sourceId") == "revoked-round":
            return None
        record = storage.get(OWNER, "run", prior["sourceId"])
        if record is None:
            record = storage.put(OWNER, "run", {"id": prior["sourceId"], "status": "ready"})
        if record.get("status") != "ready":
            return None
        return {"owner": OWNER, "kind": "run", "id": record["id"], "version": record["version"]}
    return resolver, prior_authority


def offline_ledger(storage, **kwargs):
    resolver, prior_authority = admitted_sources(storage)
    return Ledger.offline(storage, verifier=TestKeyVerifier(), input_resolver=resolver,
                          prior_authority=prior_authority, **kwargs)


@pytest.fixture
def env():
    now = [1_800_000_000_000]
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test", clock=lambda: now[0])
    storage.put(OWNER, "project", {"id": "p1", "status": "active", "members": {
        a: {"role": "designer"} for a in ("designer-1", "alice", "bob", "carol")}})
    return storage, offline_ledger(storage), now


def bump_authority(storage, project_id):
    project = storage.get(OWNER, "project", project_id)
    members = dict(project["members"]); members.pop("carol", None)
    storage.put(OWNER, "project", {**project, "members": members}, project["version"])


def make_manifest(storage, admissions=ADM, **extra):
    """The immutable input manifest: exactly the admitted decisions plus frozen source obligations."""
    body = _json_module.dumps({"admissions": admissions, **extra}, sort_keys=True).encode()
    digest = _hashlib.sha256(body).hexdigest()
    key = storage.key_for(OWNER, "run", "manifests", f"{digest}.json")
    try:
        storage.put_blob_once(key, body, "application/json")
    except Exception:
        pass
    return {"ref": key, "hash": digest}


def admit(ledger, key="req-1", **over):
    project = ledger.storage.get(OWNER, "project", "p1")
    extra = over.pop("manifest_extra", {})
    if "manifest" not in over:
        over["manifest"] = make_manifest(ledger.storage, over.get("admissions", ADM), **extra)
    args = dict(request_key=key, actor="designer-1", project_id="p1", operation="design.generate",
                model="model-a", manifest=None, admissions=ADM,
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
    fenced, bound = source_fences(storage)
    job = admit(ledger, key="k88", completion_scope={"nonSourceOperations": 12, "sourceChecks": 88, "sourceBindings": 50},
                manifest_extra={"sourceChecks": fenced, "sourceBindings": bound})
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
    second = chained(job, "generate", "n2", previous=receipt_hash(first),
                     inputs=[{"key": out["key"], "sha256": out["sha256"], "size": out["size"]}], status="ok",
                     service=service_call(ledger, job, "generate", "model"))
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


# === Task 6: per-call intent, budget and unknown outcomes (RUN-03) =================================

def test_lost_model_response_becomes_recovery_required_and_is_not_replayed(env):
    storage, ledger, now = env
    job = running(env)
    call = ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                                kind="model", min_remaining_ms=60_000)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    swept = ledger.reconciler().sweep(OWNER, job["id"])
    assert swept["status"] == "recovery_required" and swept["unknownOutcome"]
    with pytest.raises(LedgerError):
        ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=False)
    retried = ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=True)
    assert retried["status"] == "queued" and retried["fence"] > swept["fence"]
    assert [c["status"] for c in retried["calls"]] == ["unknown"]


def test_budget_and_deadline_reservation(env):
    _, ledger, now = env
    job = running(env)
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                             kind="model", min_remaining_ms=PROFILE_DEFAULT["deadlineMs"] + 1)
    assert error.value.code == "deadline-budget"


def test_watchdog_cannot_complete_and_recovery_window_is_bounded(env):
    _, ledger, now = env
    job = running(env)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    now[0] += PROFILE_DEFAULT["recoveryWindowMs"] + 1
    assert ledger.reconciler().sweep(OWNER, job["id"])["status"] in {"failed", "expired"}


def test_call_budget_is_exhausted_at_the_operation_ceiling(env):
    storage, ledger, _ = env
    job = admit(ledger, operation="design.release")         # 10 calls, no model stage
    job = ledger.dispatcher().allocate(OWNER, job["id"])
    job = ledger.tool().claim(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="compile",
                             kind="model", min_remaining_ms=0)
    assert error.value.code == "model-not-allowed"
    for _ in range(10):
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="browser",
                             kind="browser", min_remaining_ms=0)
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="browser",
                             kind="browser", min_remaining_ms=0)
    assert error.value.code == "budget-exhausted"


def test_token_budget_is_reserved_and_released_by_the_outcome(env):
    _, ledger, _ = env
    job = running(env)
    call = ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                                kind="model", min_remaining_ms=0, max_tokens=300_000)
    assert call["job"]["budget"]["tokensReserved"] == 300_000
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                             kind="model", min_remaining_ms=0, max_tokens=200_000)
    assert error.value.code == "token-budget"
    done = ledger.tool().outcome(OWNER, job["id"], job["attempt"]["id"], job["fence"], call["callId"],
                                 status="completed", usage={"inputTokens": 1000, "outputTokens": 500})
    assert done["budget"]["tokensReserved"] == 0 and done["budget"]["tokensUsed"] == 1500
    again = ledger.tool().outcome(OWNER, job["id"], job["attempt"]["id"], job["fence"], call["callId"],
                                  status="completed", usage={"inputTokens": 1000, "outputTokens": 500})
    assert again["version"] == done["version"]
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(OWNER, job["id"], job["attempt"]["id"], job["fence"], call["callId"],
                              status="failed", usage=None)
    assert error.value.code == "operation-changed"
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(OWNER, job["id"], job["attempt"]["id"], job["fence"], call["callId"],
                              status="completed", usage={"inputTokens": 1, "outputTokens": 1, "prompt": "x"})
    assert error.value.code in {"operation-changed", "call-invalid"}


def test_retried_intent_with_the_same_operation_id_reserves_once(env):
    _, ledger, _ = env
    job = running(env)
    operation = op_id()
    args = dict(stage="generate", kind="model", min_remaining_ms=0, max_tokens=1000, operation_id=operation)
    first = ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], **args)
    second = ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], **args)
    assert first["callId"] == second["callId"]
    assert second["job"]["budget"]["calls"] == 1 and second["job"]["budget"]["tokensReserved"] == 1000
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"],
                             **{**args, "max_tokens": 2000})
    assert error.value.code == "operation-changed"


def test_production_cost_gate_fails_closed_when_costguard_is_unconfigured(env, monkeypatch):
    from workspace.execution_ledger import CostGuardGate
    storage, _, _ = env
    monkeypatch.delenv("CACHE_TABLE", raising=False)
    ledger = offline_ledger(storage, cost_gate=CostGuardGate())
    job = running((storage, ledger, env[2]))
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                             kind="model", min_remaining_ms=0)
    assert error.value.code == "daily-budget-unavailable"
    assert storage.get(OWNER, "job", job["id"])["budget"]["calls"] == 0


def test_daily_budget_exhaustion_refuses_the_model_call(env, monkeypatch):
    import types
    from workspace.execution_ledger import CostGuardGate
    storage, _, _ = env
    fake = types.SimpleNamespace(_tbl=object(), budget_ok=lambda: False, add_usage=lambda tokens: tokens)
    monkeypatch.setenv("CACHE_TABLE", "cache-test")
    monkeypatch.setitem(sys.modules, "common.costguard", fake)
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(costguard=fake))
    ledger = offline_ledger(storage, cost_gate=CostGuardGate())
    job = running((storage, ledger, env[2]))
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                             kind="model", min_remaining_ms=0)
    assert error.value.code == "daily-budget"


def test_deadline_expiry_releases_quota(env):
    storage, ledger, now = env
    job = admit(ledger)
    now[0] = job["deadlineAt"]
    assert ledger.reconciler().sweep(OWNER, job["id"])["status"] == "expired"
    assert quota(storage, job) == ([], [])


def test_recovery_window_failure_releases_quota_and_keeps_unknown_outcome(env):
    storage, ledger, now = env
    job = running(env)
    ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                         kind="model", min_remaining_ms=0)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    now[0] += PROFILE_DEFAULT["recoveryWindowMs"] + 1
    failed = ledger.reconciler().sweep(OWNER, job["id"])
    assert failed["status"] == "failed" and failed["unknownOutcome"] is True
    assert quota(storage, job) == ([], [])


def test_retry_reacquires_quota_and_proceeds_to_running_within_the_deadline(env):
    storage, ledger, now = env
    job = running(env)
    ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                         kind="model", min_remaining_ms=0)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    now[0] += PROFILE_DEFAULT["recoveryWindowMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])                      # failed, quota released
    admit(ledger, key="other", actor="alice")
    admit(ledger, key="other2", actor="bob")                         # the project is now full
    with pytest.raises(LedgerError) as error:
        ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=True)
    assert error.value.code == "concurrency-project"
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"
    for row in storage.list(OWNER, "job"):
        if row["id"] != job["id"]:
            ledger.api().cancel(OWNER, row["id"], actor=row["actor"])
    retried = ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=True)
    assert quota(storage, job) == ([job["id"]], [job["id"]])
    allocated = ledger.dispatcher().allocate(OWNER, retried["id"])
    claimed = ledger.tool().claim(OWNER, retried["id"], allocated["attempt"]["id"], allocated["fence"])
    assert claimed["status"] == "running" and len(claimed["attempts"]) == 1


@pytest.mark.parametrize("late", ["deadline", "authorization"])
def test_retry_after_deadline_or_authorization_expiry_is_refused(env, late):
    storage, ledger, now = env
    job = running(env) if late == "deadline" else None
    if late == "authorization":
        job = admit(ledger, authorization_expires_at=now[0] + 200_000)
        job = ledger.dispatcher().allocate(OWNER, job["id"])
        job = ledger.tool().claim(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    swept = ledger.reconciler().sweep(OWNER, job["id"])
    assert swept["status"] == "recovery_required"
    now[0] = job["deadlineAt"] if late == "deadline" else job["authorizationExpiresAt"]
    with pytest.raises(LedgerError) as error:
        ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=True)
    assert error.value.code == "retry-expired"
    assert storage.get(OWNER, "job", job["id"])["version"] == swept["version"]
    assert quota(storage, job) == ([job["id"]], [job["id"]])


def test_superseding_admission_records_the_old_job(env):
    storage, ledger, now = env
    job = admit(ledger)
    ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    fresh = admit(ledger, key="req-2", supersedes=job["id"])
    assert fresh["supersedes"] == job["id"]


def test_scheduled_sweep_discovers_lost_leases_without_supplied_ids(env):
    storage, ledger, now = env
    job = running(env)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().run_due()
    assert storage.get(OWNER, "job", job["id"])["status"] == "recovery_required"
    now[0] += PROFILE_DEFAULT["recoveryWindowMs"] + 1
    ledger.reconciler().run_due()
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"
    from workspace.storage import DUE_OWNER
    assert all(row["status"] == "done" for row in storage.list(DUE_OWNER, "exec_due"))


def test_scheduled_sweep_of_a_live_heartbeating_job_reschedules_it(env):
    storage, ledger, now = env
    job = running(env)
    now[0] += 60_000
    ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    now[0] += 40_000                     # the original lease time passed, the heartbeat extended it
    ledger.reconciler().run_due()
    current = storage.get(OWNER, "job", job["id"])
    assert current["status"] == "running"
    from workspace.storage import DUE_OWNER
    pending = [row for row in storage.list(DUE_OWNER, "exec_due") if row["status"] == "pending"]
    assert [row["id"] for row in pending] == [current["dueId"]]
    assert pending[0]["dueAt"] == current["attempt"]["leaseExpiresAt"]


def test_job_record_stays_bounded_under_heartbeats_and_recorded_operations(env):
    storage, ledger, _ = env
    job = running(env)
    for _ in range(11_000):
        ledger.tool().heartbeat(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    calls = [ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate",
                                  kind="model", min_remaining_ms=0, max_tokens=1000, operation_id=op_id())["callId"]
             for _ in range(120)]
    context = put_output(storage, job, "context", "context.json", b"context")
    r = chained(job, "context", "nonce-context", previous=None, outputs=[context])
    job = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r, operation_id=op_id())
    previous = receipt_hash(r)
    for index in range(29):
        ledger.tool().outcome(*ids(job), calls[index], status="completed")
        r = chained(job, "generate", f"nonce-{index}", previous=previous, inputs=[entry_of(context)],
                    service={"kind": "model", "sessionId": job["attempt"]["sessionId"], "taskId": calls[index]})
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r, operation_id=op_id())
        previous = receipt_hash(r)
    stored = storage.get(OWNER, "job", job["id"])
    assert len(stored["ops"]) == 150
    import json
    assert len(json.dumps(stored).encode()) < 200_000


# === Task 7: admission-bound transfer receipts (RUN-05) ==============================================

import base64  # noqa: E402
import json as _json  # noqa: E402

CHUNK = 256 * 1024


@pytest.fixture
def xfer():
    """A ledger with an admitted-input resolver; the fake S3 stores real bytes."""
    now = [1_800_000_000_000]
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test", clock=lambda: now[0])
    storage.put(OWNER, "project", {"id": "p1", "status": "active", "members": {
        a: {"role": "designer"} for a in ("designer-1", "alice", "bob", "carol")}})
    data = XFER_DATA                                      # 512 KiB + 4 bytes -> 3 chunks
    return storage, offline_ledger(storage), now, data


def run_job(ledger, **over):
    job = admit(ledger, **over)
    job = ledger.dispatcher().allocate(OWNER, job["id"])
    return ledger.tool().claim(OWNER, job["id"], job["attempt"]["id"], job["fence"])


def ids(job):
    return OWNER, job["id"], job["attempt"]["id"], job["fence"]


def read_all(ledger, job, handle):
    chunks = []
    for index in range(handle["chunks"]):
        chunk = ledger.tool().read_chunk(*ids(job), handle["handleId"], index)
        raw = base64.b64decode(chunk["data"])
        assert hashlib.sha256(raw).hexdigest() == chunk["sha256"]
        chunks.append(raw)
    return b"".join(chunks)


def test_admitted_input_read_completes_with_server_computed_hashes(xfer):
    storage, ledger, _, data = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    assert handle["total"] == len(data) and handle["chunks"] == 3
    assert handle["sha256"] == hashlib.sha256(data).hexdigest()
    assert read_all(ledger, job, handle) == data
    assert storage.get(OWNER, "job", job["id"])["transferUsage"] == {"chunks": 3, "bytes": len(data)}


def test_input_handle_for_a_decision_outside_the_admissions_is_refused(xfer):
    _, ledger, _, _ = xfer
    job = run_job(ledger)
    with pytest.raises(LedgerError) as error:
        ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-9", stage="context")
    assert error.value.code == "transfer-invalid"


def open_out(ledger, job, data, name="page.tsx", stage="generate"):
    return ledger.tool().open_output(*ids(job), operation_id=op_id(), stage=stage, name=name, total=len(data),
                                     sha256=hashlib.sha256(data).hexdigest())


def b64(raw):
    return base64.b64encode(raw).decode()


def test_output_is_assembled_server_side_and_recorded(xfer):
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    data = b"x" * (CHUNK + 10)
    handle = open_out(ledger, job, data)
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data[:CHUNK]))
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 1, b64(data[CHUNK:]))
    closed = ledger.tool().close_output(*ids(job), handle["handleId"], operation_id=op_id())
    [output] = closed["transfers"]
    assert output["key"] == storage.key_for(OWNER, "job", job["id"], f"out/{job['attempt']['id']}/generate/page.tsx")
    assert storage.get_blob(output["key"]) == data and output["sha256"] == hashlib.sha256(data).hexdigest()
    assert not any(k[1].endswith(("part0000", "part0001")) for k in storage.s3().objects)


def test_out_of_order_output_chunk_is_refused(xfer):
    _, ledger, _, _ = xfer
    job = run_job(ledger)
    data = b"y" * (CHUNK + 1)
    handle = open_out(ledger, job, data)
    with pytest.raises(LedgerError) as error:
        ledger.tool().write_chunk(*ids(job), handle["handleId"], 1, b64(data[CHUNK:]))
    assert error.value.code == "transfer-invalid"


def test_output_whose_server_hash_differs_from_the_declared_hash_is_refused(xfer):
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_output(*ids(job), operation_id=op_id(), stage="generate", name="a.txt", total=4,
                                       sha256=hashlib.sha256(b"good").hexdigest())
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(b"evil"))
    with pytest.raises(LedgerError) as error:
        ledger.tool().close_output(*ids(job), handle["handleId"], operation_id=op_id())
    assert error.value.code == "transfer-invalid"
    assert storage.get(OWNER, "job", job["id"])["transfers"] == []


@pytest.mark.parametrize("name", ["a/b.txt", "../x", "", "A.txt", "x" * 101])
def test_output_names_are_constrained(xfer, name):
    _, ledger, _, _ = xfer
    job = run_job(ledger)
    with pytest.raises(LedgerError) as error:
        open_out(ledger, job, b"z", name=name)
    assert error.value.code == "transfer-invalid"


def test_chunk_after_cancel_is_fenced_and_the_handle_is_cleaned_up(xfer):
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    data = b"q" * (CHUNK + 5)
    handle = open_out(ledger, job, data)
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data[:CHUNK]))
    cancelled = ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    assert cancelled["cleanup"] == [handle["handleId"]]
    with pytest.raises(LedgerError) as error:
        ledger.tool().write_chunk(*ids(job), handle["handleId"], 1, b64(data[CHUNK:]))
    assert error.value.code == "stale-attempt"
    ledger.reconciler().run_due()
    assert storage.get(OWNER, "job", job["id"])["cleanup"] == []
    assert not any(".part" in k[1] for k in storage.s3().objects)


def test_transfer_chunk_budget_is_bounded(xfer):
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    from workspace.execution_ledger import _seed_for_tests
    stored = storage.get(OWNER, "job", job["id"])
    _seed_for_tests(storage, OWNER, "job", {**stored, "transferUsage": {"chunks": 511, "bytes": 0}}, stored["version"])
    handle = open_out(ledger, job, b"ab")
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(b"ab"))       # the 512th chunk
    with pytest.raises(LedgerError) as error:           # the 513th chunk: refused when declared
        open_out(ledger, job, b"cd", name="b.txt")
    assert error.value.code == "transfer-invalid"
    with pytest.raises(LedgerError) as error:           # and when written
        ledger.tool().write_chunk(*ids(job), handle["handleId"], 1, b64(b"cd"))
    assert error.value.code == "transfer-invalid"
    with pytest.raises(LedgerError) as error:
        open_out(ledger, job, b"e" * (CHUNK * 600), name="huge.bin")
    assert error.value.code == "transfer-invalid"


def test_retried_write_chunk_is_idempotent(xfer):
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    data = b"r" * 10
    handle = open_out(ledger, job, data)
    operation = op_id()
    first = ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data), operation_id=operation)
    again = ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data), operation_id=operation)
    assert again["version"] == first["version"] + 1          # only the durable retry count is written
    assert again["handles"][handle["handleId"]]["parts"] == first["handles"][handle["handleId"]]["parts"]
    assert again["handles"][handle["handleId"]]["retries"] == {"0": 1}
    assert again["transferUsage"]["chunks"] == first["transferUsage"]["chunks"]
    with pytest.raises(LedgerError) as error:
        ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(b"s" * 10))
    assert error.value.code == "transfer-invalid"


def test_finish_requires_every_output_handle_closed(xfer):
    _, ledger, _, _ = xfer
    job = run_job(ledger)
    open_out(ledger, job, b"open")
    stored = ledger.api().read(OWNER, job["id"])
    assert any(h["status"] == "open" for h in stored["handles"].values())


class FakeRuntime:
    """Holds only the invocation envelope and the Gateway tool facade: no storage, no S3 client."""

    def __init__(self, tool, envelope):
        self.tool, self.envelope = tool, envelope

    def __getattr__(self, name):
        if name in ("storage", "s3"):
            raise AttributeError("direct S3 access is denied to the Runtime")
        raise AttributeError(name)

    def read(self, handle):
        e = self.envelope
        return b"".join(base64.b64decode(self.tool.read_chunk(e["owner"], e["jobId"], e["attemptId"], e["fence"],
                                                              handle["handleId"], index)["data"])
                        for index in range(handle["chunks"]))


def test_design_release_chain_retrieves_manifest_and_approved_source_through_the_gateway(xfer):
    storage, ledger, _, _ = xfer
    source = b"export default function Page(){return null}\n" * 100
    source_key = storage.key_for(OWNER, "run", "run-1", "rounds/2/source.zip")
    storage.put_blob_once(source_key, source, "application/zip")
    prior = {"sourceKind": "run-round", "sourceId": "run-1", "revision": "2",
             "sha256": hashlib.sha256(source).hexdigest(), "key": source_key}
    manifest = _json.dumps({"admissions": ADM, "priors": [prior]}).encode()
    manifest_key = storage.key_for(OWNER, "run", "run-1", "release-manifest.json")
    storage.put_blob_once(manifest_key, manifest, "application/json")
    job = run_job(ledger, operation="design.release",
                  manifest={"ref": manifest_key, "hash": hashlib.sha256(manifest).hexdigest()})
    envelope = {"owner": OWNER, "jobId": job["id"], "attemptId": job["attempt"]["id"], "fence": job["fence"],
                "manifest": job["manifest"]}
    runtime = FakeRuntime(ledger.tool(), envelope)
    with pytest.raises(AttributeError):
        runtime.storage
    opened = runtime.tool.open_manifest(OWNER, job["id"], job["attempt"]["id"], job["fence"], operation_id=op_id())
    assert hashlib.sha256(runtime.read(opened)).hexdigest() == envelope["manifest"]["hash"]
    listed = _json.loads(runtime.read(opened))["priors"][0]
    handle = runtime.tool.open_prior(OWNER, job["id"], job["attempt"]["id"], job["fence"],
                                     operation_id=op_id(), ref=listed["key"])
    assert runtime.read(handle) == source


def test_prior_not_listed_or_revoked_is_refused(xfer):
    storage, ledger, _, _ = xfer
    data = b"prior"
    key = storage.key_for(OWNER, "run", "run-2", "source.zip")
    storage.put_blob_once(key, data, "application/zip")
    prior = {"sourceKind": "run-round", "sourceId": "revoked-round", "revision": "1",
             "sha256": hashlib.sha256(data).hexdigest(), "key": key}
    manifest = _json.dumps({"admissions": ADM, "priors": [prior]}).encode()
    manifest_key = storage.key_for(OWNER, "run", "run-2", "manifest.json")
    storage.put_blob_once(manifest_key, manifest, "application/json")
    job = run_job(ledger, operation="design.release",
                  manifest={"ref": manifest_key, "hash": hashlib.sha256(manifest).hexdigest()})
    with pytest.raises(LedgerError) as error:
        ledger.tool().open_prior(*ids(job), operation_id=op_id(), ref=key)
    assert error.value.code == "transfer-invalid"
    with pytest.raises(LedgerError) as error:
        ledger.tool().open_prior(*ids(job), operation_id=op_id(), ref=storage.key_for(OWNER, "run", "x", "y"))
    assert error.value.code == "transfer-invalid"


def test_production_ledger_has_no_input_resolver():
    from workspace.execution_ledger import Ledger as L
    assert "input_resolver" not in L.production.__code__.co_varnames


# === Task 8: coupled completion and reconciliation (RUN-02, RUN-04) ==================================

from test_workspace_storage import TransactionFailure  # noqa: E402
from botocore.exceptions import ClientError, ReadTimeoutError  # noqa: E402

GOOD = {"context": ("ok", {}), "generate": ("ok", {}),
        "compile": ("ok", {"sourceHash": "5" * 64, "bundleHash": _hashlib.sha256(b"compile-bytes").hexdigest()}),
        "browser": ("ok", {"visualDiff": 0.0, "passed": True, "functionalStatus": "pass",
                           "accessibility": {"status": "pass", "violations": []}}),
        "analyze": ("ok", {"coverage": {"files": 1}}),
        "verify": ("ok", {"verdict": "pass", "approvable": True, "issues": [], "reviewer": "deterministic",
                          "passed": True, "compositionHashes": ["h1"], "judgeEvidence": {"h1": "ev-1"}})}


APPROVED_SHOT = b"approved-screenshot-png"


def approved_screenshot(storage):
    key = storage.key_for(OWNER, "run", "run-r", "approved/screenshot.png")
    try:
        storage.put_blob_once(key, APPROVED_SHOT, "image/png")
    except Exception:
        pass
    return {"key": key, "sha256": hashlib.sha256(APPROVED_SHOT).hexdigest()}


def release_manifest(storage, screenshot=True):
    approved = {"sourceHash": "5" * 64, "bundleHash": _hashlib.sha256(b"compile-bytes").hexdigest()}
    if screenshot:
        approved["screenshot"] = approved_screenshot(storage)
    body = _json.dumps({"admissions": ADM, "approved": approved, "priors": []}).encode()
    key = storage.key_for(OWNER, "run", "run-r", "release-manifest.json" if screenshot else "release-noshot.json")
    try:
        storage.put_blob_once(key, body, "application/json")
    except Exception:
        pass
    return {"ref": key, "hash": hashlib.sha256(body).hexdigest()}


def chain(env, operation="design.generate", results=None, skip=(), key="req-1", manifest=None, comparison=True,
          manifest_extra=None, extra_inputs=None):
    """Drive a complete verified receipt chain; returns (job, finish result)."""
    storage, ledger = env[0], env[1]
    over = {"manifest": manifest or release_manifest(storage)} if operation == "design.release" else {}
    if manifest_extra:
        over["manifest_extra"] = manifest_extra
    job = run_job(ledger, operation=operation, key=key, **over)
    results = {**GOOD, **(results or {})}
    previous, hashes, files = None, [], []
    stages = [stage for stage in __import__("workspace.execution_ledger", fromlist=["OPERATIONS"]).OPERATIONS[operation]
              if stage not in skip]
    for index, stage in enumerate(stages):
        outputs = [put_output(storage, job, stage, f"{stage}.out", f"{stage}-bytes".encode())]
        if stage == "compile":
            outputs[0]["role"] = "bundle"
        files.append({"key": outputs[0]["key"], "sha256": outputs[0]["sha256"]})
        if index == len(stages) - 1:
            manifest = _json.dumps({"files": files}).encode()
            outputs.append(put_output(storage, job, stage, "result-manifest.json", manifest))
        status, result = results[stage]
        kind = STAGE_SERVICES[stage][0]
        service = {} if kind == "runtime" else {"service": service_call(ledger, job, stage, kind)}
        inputs = [*required_inputs(job, stage), *((extra_inputs or {}).get(stage, []))]
        if operation == "design.release" and stage == "browser" and comparison and job.get("releaseBaseline"):
            baseline = job["releaseBaseline"]          # the Browser compares against the approved screenshot
            inputs = [*inputs, {**baseline, "size": len(APPROVED_SHOT)}]
            result = {**result, "comparison": {"key": baseline["key"], "sha256": baseline["sha256"]}}
        r = chained(job, stage, f"n-{stage}", previous=previous, outputs=outputs, status=status, result=result,
                    inputs=inputs, **service)
        job = ledger.tool().stage(*ids(job), r)
        previous = receipt_hash(r)
        hashes.append(previous)
    manifest_out = job["stages"][-1]["outputs"][-1]
    return job, {"manifestRef": manifest_out["key"], "manifestHash": manifest_out["sha256"], "receipts": hashes}


def required_inputs(job, stage):
    """The inputs a stage must consume: the latest predecessor's outputs (compile: its bundle)."""
    predecessor = getattr(_ledger_module, "STAGE_INPUTS", {}).get(job["operation"], {}).get(stage)
    rows = [row for row in job["stages"] if row["stage"] == predecessor and row["attemptId"] == job["attempt"]["id"]]
    if not rows:
        return []
    outputs = rows[-1]["outputs"]
    if predecessor == "compile":
        outputs = [entry for entry in outputs if entry.get("role") == "bundle"]
    return [entry_of(entry) for entry in outputs]


def finish(ledger, job, status="succeeded", result=None, **kwargs):
    return ledger.tool().finish(*ids(job), status=status, result=result, **kwargs)


RULES = [
    ("design.extract", {}, "succeeded", True),
    ("design.extract", {"verify": ("ok", {"issues": ["missing citation"]})}, "needs_changes", True),
    ("design.extract", {"verify": ("ok", {"issues": ["missing citation"]})}, "succeeded", False),
    ("design.generate", {}, "succeeded", True),
    ("design.generate", {"verify": ("ok", {"verdict": "fail", "approvable": False})}, "needs_changes", True),
    ("design.generate", {"verify": ("ok", {"verdict": "fail", "approvable": False})}, "succeeded", False),
    ("design.edit", {"verify": ("ok", {"verdict": "pass", "approvable": False})}, "succeeded", False),
    ("design.compose", {}, "succeeded", True),
    ("design.compose", {"verify": ("ok", {"reviewer": "deterministic", "passed": True, "compositionHashes": ["h1", "h2"],
                                          "judgeEvidence": {"h1": "ev-1"}})}, "needs_changes", True),
    ("design.compose", {"verify": ("ok", {"reviewer": "llm", "passed": True, "compositionHashes": [],
                                          "judgeEvidence": {}})}, "succeeded", False),
    ("intake.transcribe", {}, "succeeded", True),
    ("intake.transcribe", {"verify": ("ok", {"issues": ["region outside image"]})}, "needs_changes", True),
    ("design.release", {}, "succeeded", True),
    ("design.release", {"browser": ("ok", {"visualDiff": 0.03})}, "succeeded", False),
    ("design.release", {"compile": ("ok", {"sourceHash": "0" * 64,
                                           "bundleHash": _hashlib.sha256(b"compile-bytes").hexdigest()})},
     "succeeded", False),
    ("design.release", {}, "needs_changes", False),
]


@pytest.mark.parametrize("operation,results,status,accepted", RULES)
def test_terminal_rules_are_operation_specific(xfer, operation, results, status, accepted):
    storage, ledger, now, _ = xfer
    job, result = chain(xfer, operation, results)
    if accepted:
        done = finish(ledger, job, status, result)
        assert done["status"] == status and done["result"]["manifestHash"] == result["manifestHash"]
        assert quota(storage, job) == ([], [])
    else:
        with pytest.raises(LedgerError) as error:
            finish(ledger, job, status, result)
        assert error.value.code == "status-inconsistent"
        assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def test_a_signed_failed_receipt_cannot_finish_succeeded(xfer):
    _, ledger, _, _ = xfer
    job, result = chain(xfer, results={"browser": ("failed", {"visualDiff": 1.0})})
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "status-inconsistent"


def test_compose_refuses_a_model_intent(xfer):
    _, ledger, _, _ = xfer
    job = run_job(ledger, operation="design.compose")
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), stage="verify", kind="model", min_remaining_ms=0)
    assert error.value.code == "model-not-allowed"


def test_finishing_without_the_browser_stage_is_incomplete(xfer):
    _, ledger, _, _ = xfer
    job, result = chain(xfer, skip=("browser", "verify"))     # verify cannot be staged without browser evidence
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "stages-incomplete"


def test_receipt_list_must_equal_the_recorded_chain(xfer):
    _, ledger, _, _ = xfer
    job, result = chain(xfer)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", {**result, "receipts": list(reversed(result["receipts"]))})
    assert error.value.code == "receipt-invalid"


def test_substituted_result_manifest_is_refused(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    fake = _json.dumps({"files": [{"key": storage.key_for(OWNER, "asset", "x", "y"), "sha256": "0" * 64}]}).encode()
    forged = put_output(storage, job, "verify", "forged.json", fake)    # under the prefix but not a chain output
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", {**result, "manifestRef": forged["key"], "manifestHash": forged["sha256"]})
    assert error.value.code == "receipt-invalid"


def test_manifest_listing_a_non_chain_object_is_refused(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer, "design.extract", skip=("verify",))
    stray = put_output(storage, job, "verify", "stray.bin", b"stray")
    body = _json.dumps({"files": [{"key": stray["key"], "sha256": stray["sha256"]}]}).encode()
    outputs = [put_output(storage, job, "verify", "verify.out", b"verify"),
               put_output(storage, job, "verify", "result-manifest.json", body)]
    r = chained(job, "verify", "n-verify", previous=result["receipts"][-1], outputs=outputs, status="ok",
                result=GOOD["verify"][1], inputs=required_inputs(job, "verify"))
    job = ledger.tool().stage(*ids(job), r)
    hashes = [*result["receipts"], receipt_hash(r)]
    manifest = job["stages"][-1]["outputs"][-1]
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", {"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                          "receipts": hashes})
    assert error.value.code == "receipt-invalid"


def test_open_output_handles_block_finish(xfer):
    _, ledger, _, _ = xfer
    job, result = chain(xfer)
    open_out(ledger, job, b"unfinished", name="late.txt", stage="verify")
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "transfers-incomplete"


def staged_publication(storage, job):
    """Stands in for publish_candidate(_stage=True, _origin="execution") output (see report: staging mode deferred)."""
    def stage_completion(prepared):
        assert prepared["job"]["status"] == "succeeded"
        return {"writes": [
            {"owner": OWNER, "kind": "ontology", "item": {"id": "project-current", "generation": "g-1"},
             "expected_version": None},
            {"owner": OWNER, "kind": "ontology", "item": {"id": "request-" + job["id"][5:], "generation": "g-1"},
             "expected_version": None},
            {"owner": OWNER, "kind": "wb_artifact", "item": {"id": "art-x", "jobId": job["id"],
                                                             "executionId": job["id"], "status": "completed"},
             "expected_version": None}],
            "checks": [{"owner": OWNER, "kind": "asset", "id": "src-1", "version": None}]}
    return stage_completion


def test_combined_finish_commits_ontology_marker_artifact_and_job_in_one_transaction(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    before = len(storage.table().transactions)
    done = finish(ledger, job, "succeeded", result, stage_completion=staged_publication(storage, job),
                  operation_id=op_id())
    assert len(storage.table().transactions) == before + 2      # the durable submission count, then the completion
    kinds = [entry["Put"]["Item"]["sk"].split("#")[0] for entry in storage.table().transactions[-1]["TransactItems"]
             if "Put" in entry]
    assert {"job", "ontology", "wb_artifact", "exec_quota"} <= set(kinds)
    assert done["status"] == "succeeded"
    assert storage.get(OWNER, "ontology", "project-current")["generation"] == "g-1"


def test_stale_completion_write_commits_nothing(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    artifact = storage.put(OWNER, "wb_artifact", {"id": "art-s", "status": "processing"})
    stale = lambda prepared: {"writes": [{"owner": OWNER, "kind": "wb_artifact",
                                          "item": {**artifact, "status": "completed"}, "expected_version": 99}],
                              "checks": []}
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, stage_completion=stale)
    assert error.value.code == "conflict"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"
    assert storage.get(OWNER, "wb_artifact", "art-s")["status"] == "processing"


def test_completion_scope_overflow_writes_nothing(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    before = len(storage.table().transactions)
    wide = lambda prepared: {
        "writes": [{"owner": OWNER, "kind": "wb_artifact", "item": {"id": f"w{i}"}, "expected_version": None}
                   for i in range(2)],
        "checks": [{"owner": OWNER, "kind": "asset", "id": f"c{i}", "version": None} for i in range(99)]}
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, stage_completion=wide)
    assert error.value.code == "execution-completion-scope"
    assert len(storage.table().transactions) == before


def test_retried_completed_finish_returns_the_terminal_job(xfer):
    _, ledger, _, _ = xfer
    job, result = chain(xfer)
    operation = op_id()
    done = finish(ledger, job, "succeeded", result, operation_id=operation)
    again = finish(ledger, job, "succeeded", result, operation_id=operation)
    assert again["status"] == "succeeded" and again["version"] == done["version"]
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "needs_changes", result, operation_id=operation)
    assert error.value.code == "operation-changed"
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=op_id())
    assert error.value.code in {"terminal", "stale-attempt"}


def test_superseded_attempt_finish_is_stale_and_kept_as_a_late_diagnostic(xfer):
    storage, ledger, now, _ = xfer
    job, result = chain(xfer)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    retried = ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=True)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "stale-attempt"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "queued" and stored["result"] is None
    assert stored["attempts"][-1]["id"] == job["attempt"]["id"]
    assert stored["attempts"][-1]["late"][0]["receipts"] == result["receipts"]
    assert retried["fence"] == stored["fence"]


def test_reconcile_completes_a_recovery_required_attempt_from_verified_receipts(xfer):
    storage, ledger, now, _ = xfer
    job, result = chain(xfer, skip=("verify",))
    out = put_output(storage, job, "verify", "verify.out", b"verify")
    files = [{"key": row["outputs"][0]["key"], "sha256": row["outputs"][0]["sha256"]} for row in job["stages"]]
    manifest = put_output(storage, job, "verify", "result-manifest.json", _json.dumps({"files": files}).encode())
    last = chained(job, "verify", "n-verify", previous=result["receipts"][-1], outputs=[out, manifest],
                   status="ok", result=GOOD["verify"][1], inputs=required_inputs(job, "verify"))
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    done = ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[last], status="succeeded",
                                         result={"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                                 "receipts": [*result["receipts"], receipt_hash(last)]})
    assert done["status"] == "succeeded"


def test_reconcile_after_the_deadline_is_refused(xfer):
    storage, ledger, now, _ = xfer
    job, result = chain(xfer)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    now[0] = job["deadlineAt"]
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[], status="succeeded",
                                      result=result)
    assert error.value.code == "deadline"


def test_single_attempt_transport_configuration():
    assert Storage(table_name="t", single_attempt=True)._client_config().retries["total_max_attempts"] == 1
    assert Storage(table_name="t")._client_config().retries["total_max_attempts"] == 3


class FlakyTable(FakeTable):
    def __init__(self, mode, failures=1):
        super().__init__()
        self.mode, self.failures, self.wire, self.hook = mode, failures, 0, None
        self.meta.client.transact_write_items = self.flaky

    def flaky(self, **kwargs):
        statuses = ("succeeded", "failed") if self.mode == "contention-settlement" else ("succeeded",)
        finishing = any("Put" in entry and entry["Put"]["Item"]["sk"].startswith("job#")
                        and entry["Put"]["Item"].get("status") in statuses for entry in kwargs["TransactItems"])
        if not finishing:
            return self.transact_write_items(**kwargs)
        self.wire += 1
        if self.failures:
            self.failures -= 1
            if self.hook is not None:           # a concurrent writer acting while this submission is in flight
                hook, self.hook = self.hook, None
                hook(self.wire)
            if self.mode == "timeout-before":
                raise ReadTimeoutError(endpoint_url="https://dynamodb.invalid")
            if self.mode == "timeout-after":
                self.transact_write_items(**kwargs)
                raise ReadTimeoutError(endpoint_url="https://dynamodb.invalid")
            if self.mode in ("contention", "contention-settlement"):
                error = TransactionFailure.__new__(TransactionFailure)
                ClientError.__init__(error, {"Error": {"Code": "TransactionCanceledException"},
                                             "CancellationReasons": [{"Code": "TransactionConflict"}]
                                             * len(kwargs["TransactItems"])}, "TransactWriteItems")
                raise error
        return self.transact_write_items(**kwargs)


def flaky_env(mode, failures=1):
    now = [1_800_000_000_000]
    storage = Storage(table=FlakyTable(mode, failures), s3=FakeS3(), bucket="private-test", clock=lambda: now[0])
    storage.put(OWNER, "project", {"id": "p1", "status": "active", "members": {"designer-1": {"role": "designer"}}})
    return storage, offline_ledger(storage), now


def test_unknown_transport_outcome_is_one_wire_attempt_then_recovery(monkeypatch):
    env = flaky_env("timeout-before")
    storage, ledger, _ = env
    job, result = chain(env)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=op_id())
    assert error.value.code == "unknown-outcome"
    assert storage.table().wire == 1
    assert storage.get(OWNER, "job", job["id"])["status"] == "recovery_required"


def test_unknown_transport_outcome_that_committed_is_reconciled_from_durable_state():
    env = flaky_env("timeout-after")
    storage, ledger, _ = env
    job, result = chain(env)
    done = finish(ledger, job, "succeeded", result, operation_id=op_id())
    assert done["status"] == "succeeded" and storage.table().wire == 1


def test_proven_contention_retries_with_fresh_authority_checks(monkeypatch):
    env = flaky_env("contention", failures=5)
    storage, ledger, _ = env
    job, result = chain(env)
    calls = []
    original = ledger._authority
    monkeypatch.setattr(ledger, "_authority", lambda *a: calls.append(1) or original(*a))
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=op_id())
    assert error.value.code == "completion-contention"
    assert storage.table().wire == 3 and len(calls) == 3
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "failed" and stored["error"] == {"code": "completion-contention"}
    assert stored["fence"] == job["fence"] + 1 and quota(storage, job) == ([], [])


def test_exhausted_contention_refuses_further_submissions_for_the_attempt():
    """Finding 8: after two retries the fenced completion-contention failure is durable."""
    env = flaky_env("contention", failures=6)
    storage, ledger, _ = env
    job, result = chain(env)
    operation = op_id()
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=operation)
    assert error.value.code == "completion-contention"
    for again in (operation, op_id()):
        with pytest.raises(LedgerError) as error:
            finish(ledger, job, "succeeded", result, operation_id=again)
        assert error.value.code in ("terminal", "stale-attempt")
    assert storage.table().wire == 3
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"


def test_exhausted_contention_preserves_a_concurrent_cancellation():
    env = flaky_env("contention", failures=3)
    storage, ledger, _ = env
    job, result = chain(env)
    table = storage.table()
    table.hook = lambda wire: None
    original = table.flaky

    def cancel_on_last(**kwargs):
        if table.wire == 2:                  # the third (last) submission is in flight
            table.hook = lambda wire: ledger.api().cancel(OWNER, job["id"], actor="designer-1")
        return original(**kwargs)
    table.meta.client.transact_write_items = cancel_on_last
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=op_id())
    assert error.value.code in ("completion-contention", "terminal")
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "cancelled" and stored["error"]["code"] == "cancelled"


@pytest.mark.parametrize("version", [True, "1", 2, None, "missing"])
def test_malformed_discriminators_are_rejected_by_every_entry_point(env, version):
    from workspace.execution_ledger import _seed_for_tests
    storage, ledger, _ = env
    record = {"id": "exec-" + "9" * 32, "task": "agentcore-execution", "status": "running", "fence": 1,
              "attempt": {"id": "att-x", "fence": 1}}
    if version != "missing":
        record["executionSchemaVersion"] = version
    seeded = _seed_for_tests(storage, OWNER, "job", record)
    calls = [
        lambda: ledger.dispatcher().allocate(OWNER, seeded["id"]),
        lambda: ledger.tool().claim(OWNER, seeded["id"], "att-x", 1),
        lambda: ledger.tool().stage(OWNER, seeded["id"], "att-x", 1, {}),
        lambda: ledger.tool().finish(OWNER, seeded["id"], "att-x", 1, status="succeeded", result={}),
        lambda: ledger.reconciler().sweep(OWNER, seeded["id"]),
        lambda: ledger.reconciler().reconcile(OWNER, seeded["id"], "att-x", receipts=[], status="succeeded", result={}),
        lambda: ledger.api().retry(OWNER, seeded["id"], actor="designer-1", acknowledge_unknown_outcome=True),
    ]
    for call in calls:
        with pytest.raises(LedgerError) as error:
            call()
        assert error.value.code == "not-an-execution"
    assert storage.get(OWNER, "job", seeded["id"])["version"] == seeded["version"]


def test_every_facade_method_is_implemented(env):
    ledger = env[1]
    for facade, names in ((ledger.api(), ("admit", "cancel", "retry", "read")),
                          (ledger.dispatcher(), ("allocate",)),
                          (ledger.tool(), ("claim", "heartbeat", "intent", "outcome", "stage", "finish", "fail",
                                           "open_manifest", "open_prior", "open_input", "read_chunk",
                                           "open_output", "write_chunk", "close_output")),
                          (ledger.reconciler(), ("sweep", "reconcile", "settle", "resolve_orphan", "run_due"))):
        assert all(callable(getattr(facade, name, None)) for name in names)


def test_production_requires_a_registered_verifier_and_single_attempt_storage(monkeypatch):
    from workspace import execution_ledger as module

    class ReviewedVerifier:
        def verify(self, receipt):
            return False

        def revision(self):
            return 7
    monkeypatch.setattr(module, "_REGISTERED", {ReviewedVerifier})
    with pytest.raises(PermissionError):
        Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b"), verifier=ReviewedVerifier())
    ledger = Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b", single_attempt=True),
                               verifier=ReviewedVerifier())
    assert isinstance(ledger.cost_gate, module.CostGuardGate) and ledger.input_resolver is None


# === PR #27 review round 1 regressions ===============================================================

def test_offline_verifier_is_rejected_by_production_outside_pytest_and_registration_is_restricted():
    """Finding 4: a process without pytest cannot register or use the offline test verifier."""
    import subprocess
    script = (
        "import sys\n"
        "sys.path[:0] = [%r, %r]\n"
        "import execution_fakes\n"
        "from workspace import execution_ledger as m\n"
        "from workspace.storage import Storage\n"
        "outcomes = []\n"
        "try:\n"
        "    m.register_verifier(execution_fakes.TestKeyVerifier); outcomes.append('registered')\n"
        "except PermissionError: outcomes.append('register-refused')\n"
        "try:\n"
        "    v = execution_fakes.TestKeyVerifier(); outcomes.append('constructed')\n"
        "except PermissionError: outcomes.append('construct-refused'); v = object.__new__(execution_fakes.TestKeyVerifier)\n"
        "m._REGISTERED.add(execution_fakes.TestKeyVerifier)\n"
        "try:\n"
        "    m.Ledger.production(Storage(single_attempt=True), verifier=v); outcomes.append('production')\n"
        "except PermissionError: outcomes.append('production-refused')\n"
        "print(','.join(outcomes))\n") % (str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent))
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "register-refused,construct-refused,production-refused", done.stderr


def test_production_rejects_offline_verifier_types_even_when_registered(monkeypatch):
    from workspace import execution_ledger as module
    monkeypatch.setattr(module, "_REGISTERED", {TestKeyVerifier})
    with pytest.raises(PermissionError):
        Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b", single_attempt=True),
                          verifier=TestKeyVerifier())

    class Signing:                       # anything able to sign receipts is not a verify-only production verifier
        def sign(self, body): return "x"
        def verify(self, receipt): return True
    monkeypatch.setattr(module, "_REGISTERED", {Signing})
    with pytest.raises(PermissionError):
        Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b", single_attempt=True),
                          verifier=Signing())
    with pytest.raises(PermissionError):
        module.register_verifier(type("Other", (), {"verify": lambda self, r: True}))


def test_input_transfer_is_bound_to_the_frozen_admission_artifact(xfer):
    """Finding 2: the resolved artifact must equal admission.artifactHash; the handle keeps decisionId/revision."""
    storage, ledger, _, data = xfer
    other = b"not the admitted derivative"
    key = storage.key_for(OWNER, "asset", "adm-src", "other.bin")
    storage.put_blob_once(key, other, "application/octet-stream")
    ledger.input_resolver = lambda owner, admission: {"key": key, "sha256": hashlib.sha256(other).hexdigest()}
    job = run_job(ledger)
    with pytest.raises(LedgerError) as error:
        ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    assert error.value.code == "transfer-invalid"
    assert storage.get(OWNER, "job", job["id"])["handles"] == {}


def test_input_handle_records_the_admission_decision_and_revision(xfer):
    storage, ledger, _, data = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    stored = storage.get(OWNER, "job", job["id"])["handles"][handle["handleId"]]
    assert stored["decisionId"] == "adm-1" and stored["revision"] == "1"
    assert stored["sha256"] == ADM[0]["artifactHash"] == hashlib.sha256(data).hexdigest()


def revoke_requester(storage, actor="designer-1"):
    project = storage.get(OWNER, "project", "p1")
    members = dict(project["members"]); members.pop(actor)
    storage.put(OWNER, "project", {**project, "members": members}, project["version"])


def last_transaction_checks(storage):
    return [entry["ConditionCheck"]["Key"]["sk"] for entry in storage.table().transactions[-1]["TransactItems"]
            if "ConditionCheck" in entry]


def test_revocation_stops_chunk_reads_including_retries(xfer):
    """Finding 1: read_chunk revalidates the requester's current authority and fences its mutation."""
    storage, ledger, _, data = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    ledger.tool().read_chunk(*ids(job), handle["handleId"], 0)
    assert any(sk.startswith("project#") for sk in last_transaction_checks(storage))
    revoke_requester(storage)
    for index in (0, 1):                   # a retry of an already-read chunk and a new chunk
        with pytest.raises(LedgerError) as error:
            ledger.tool().read_chunk(*ids(job), handle["handleId"], index)
        assert error.value.code in ("authority-changed", "stale-attempt")
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "failed" and stored["error"]["code"] == "authority-changed"
    assert stored["transferUsage"]["chunks"] == 1


def test_revoked_admission_stops_chunk_reads(xfer):
    storage, ledger, _, data = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    ledger.input_resolver = lambda owner, admission: None          # the admission decision was withdrawn
    with pytest.raises(LedgerError) as error:
        ledger.tool().read_chunk(*ids(job), handle["handleId"], 0)
    assert error.value.code == "authority-changed"
    with pytest.raises(LedgerError) as error:
        ledger.tool().read_chunk(*ids(job), handle["handleId"], 0)
    assert error.value.code == "stale-attempt"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["transferUsage"]["chunks"] == 0 and stored["status"] == "failed"


def test_revocation_stops_model_call_intents(env):
    storage, ledger, _ = env
    job = running(env)
    ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate", kind="model",
                         min_remaining_ms=0)
    assert any(sk.startswith("project#") for sk in last_transaction_checks(storage))
    revoke_requester(storage)
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate", kind="model",
                             min_remaining_ms=0)
    assert error.value.code == "authority-changed"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "failed" and stored["budget"]["calls"] == 1


@pytest.mark.parametrize("victim", ["listed-output", "result-manifest"])
def test_finish_rechecks_every_result_object_before_publishing(xfer, victim):
    """Finding 5: a deleted stage output referenced by the result manifest blocks completion."""
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    key = job["stages"][1]["outputs"][0]["key"] if victim == "listed-output" else result["manifestRef"]
    storage.s3().delete_object(Bucket=storage.bucket, Key=key)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "receipt-invalid"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "running" and stored["result"] is None


def test_deadline_passing_during_completion_staging_is_refused_at_submission(xfer):
    """Finding 7: final temporal checks run inside the transaction attempt (before_attempt)."""
    storage, ledger, now, _ = xfer
    job, result = chain(xfer)

    def slow(prepared):
        now[0] = job["deadlineAt"] + 1
        return {"writes": [], "checks": []}
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, stage_completion=slow)
    assert error.value.code == "deadline"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def recovering(xfer):
    storage, ledger, now, _ = xfer
    job, result = chain(xfer)
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    swept = ledger.reconciler().sweep(OWNER, job["id"])
    assert swept["status"] == "recovery_required"
    return job, result, swept


def test_reconcile_after_the_recovery_window_is_refused_before_the_deadline(xfer):
    storage, ledger, now, _ = xfer
    job, result, swept = recovering(xfer)
    now[0] = swept["recoveryAt"] + PROFILE_DEFAULT["recoveryWindowMs"] + 1
    assert now[0] < job["deadlineAt"]
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[], status="succeeded",
                                      result=result)
    assert error.value.code == "recovery-window"
    assert storage.get(OWNER, "job", job["id"])["status"] == "recovery_required"


def test_recovery_window_passing_during_reconcile_staging_is_refused(xfer):
    storage, ledger, now, _ = xfer
    job, result, swept = recovering(xfer)

    def slow(prepared):
        now[0] = swept["recoveryAt"] + PROFILE_DEFAULT["recoveryWindowMs"]
        return {"writes": [], "checks": []}
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[], status="succeeded",
                                      result=result, stage_completion=slow)
    assert error.value.code == "recovery-window"
    assert storage.get(OWNER, "job", job["id"])["status"] == "recovery_required"


def test_concurrent_heartbeat_does_not_defeat_unknown_outcome_recovery():
    """Finding 9: the recovery entry uses the latest compatible version of the same attempt."""
    env = flaky_env("timeout-before")
    storage, ledger, _ = env
    job, result = chain(env)
    storage.table().hook = lambda wire: ledger.tool().heartbeat(*ids(job))
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=op_id())
    assert error.value.code == "unknown-outcome"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "recovery_required" and stored["unknownOutcome"] is True
    assert stored["attempt"]["id"] == job["attempt"]["id"] and stored["attempt"]["heartbeatAt"] is not None


def test_source_analysis_completion_is_unavailable_until_its_staging_adapter_exists(xfer):
    """Finding 6: no source.analyze completion without ontology/artifact publication."""
    storage, ledger, now, _ = xfer
    job, result = chain(xfer, "source.analyze")
    for status in ("succeeded", "needs_changes"):
        with pytest.raises(LedgerError) as error:
            finish(ledger, job, status, result)
        assert error.value.code == "completion-unavailable"
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    ledger.reconciler().sweep(OWNER, job["id"])
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[], status="succeeded",
                                      result=result)
    assert error.value.code == "completion-unavailable"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "recovery_required" and stored["result"] is None


WIDE_SCOPE = {"nonSourceOperations": 12, "sourceChecks": 88, "sourceBindings": 50}


def source_fences(storage, checks=88, bindings=50):
    records = []
    for i in range(checks):
        record = storage.get(OWNER, "asset", f"src-{i}") or storage.put(OWNER, "asset", {"id": f"src-{i}",
                                                                                        "status": "ready"})
        records.append(record)
    fenced = [{"owner": OWNER, "kind": "asset", "id": row["id"], "version": row["version"]} for row in records]
    bound = [{"sourceKind": "asset", "sourceId": f"src-{i}", "revision": "1", "audience": "project"}
             for i in range(bindings)]
    return fenced, bound


def chain_with_scope(xfer, scope):
    """chain() for a job admitted with a frozen completion scope whose manifest lists the exact obligations."""
    import functools
    fenced, bound = source_fences(xfer[0], scope["sourceChecks"], scope["sourceBindings"])
    original = globals()["admit"]
    globals()["admit"] = functools.partial(original, completion_scope=scope,
                                           manifest_extra={"sourceChecks": fenced, "sourceBindings": bound})
    try:
        return chain(xfer, key="req-scoped")
    finally:
        globals()["admit"] = original


NEVER_ADMITTED = {"sourceKind": "asset", "sourceId": "never-admitted", "revision": "1", "audience": "project"}


@pytest.mark.parametrize("supplied", ["none", "short-bindings", "unrelated-binding", "unrelated-checks"])
def test_frozen_source_obligations_require_exact_correspondence(xfer, supplied):
    """Review 2 finding 5: completion must correspond exactly to the frozen decisions, bindings and predicates."""
    storage, ledger, _, _ = xfer
    job, result = chain_with_scope(xfer, WIDE_SCOPE)
    fenced, bound = source_fences(storage)
    unrelated = [storage.put(OWNER, "asset", {"id": f"other-{i}", "status": "ready"}) for i in range(88)]
    unrelated = [{"owner": OWNER, "kind": "asset", "id": row["id"], "version": row["version"]} for row in unrelated]
    staged = {"none": None,
              "short-bindings": {"writes": [], "checks": [], "sourceBindings": bound[:-1]},
              "unrelated-binding": {"writes": [], "checks": fenced, "sourceChecks": fenced,
                                    "sourceBindings": [*bound[:-1], NEVER_ADMITTED]},
              "unrelated-checks": {"writes": [], "checks": unrelated, "sourceChecks": unrelated,
                                   "sourceBindings": bound}}[supplied]
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result,
               stage_completion=None if staged is None else (lambda prepared: staged))
    assert error.value.code == "completion-obligations"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def test_a_frozen_source_changed_after_admission_stops_completion(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain_with_scope(xfer, WIDE_SCOPE)
    fenced, bound = source_fences(storage)
    record = storage.get(OWNER, "asset", "src-7")
    storage.put(OWNER, "asset", {**record, "status": "revised"}, record["version"])
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, stage_completion=lambda prepared: {"sourceBindings": bound})
    assert error.value.code == "authority-changed"
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"


@pytest.mark.parametrize("case", ["other-decision", "missing-blob", "hash-mismatch", "scope-count", "stale-check"])
def test_admission_validates_the_immutable_input_manifest(env, case):
    storage, ledger, _ = env
    fenced, bound = source_fences(storage, 2, 1)
    extra, scope = {}, {"nonSourceOperations": 12, "sourceChecks": 2, "sourceBindings": 1}
    admissions = ADM
    if case == "other-decision":
        manifest = make_manifest(storage, [{**ADM[0], "decisionId": "adm-9"}], sourceChecks=fenced,
                                 sourceBindings=bound)
    elif case == "missing-blob":
        manifest = {"ref": storage.key_for(OWNER, "run", "manifests", "absent.json"), "hash": "c" * 64}
    elif case == "hash-mismatch":
        manifest = {**make_manifest(storage, ADM, sourceChecks=fenced, sourceBindings=bound), "hash": "d" * 64}
    elif case == "scope-count":
        manifest = make_manifest(storage, ADM, sourceChecks=fenced[:1], sourceBindings=bound)
    else:
        manifest = make_manifest(storage, ADM, sourceChecks=[{**fenced[0], "version": 99}, fenced[1]],
                                 sourceBindings=bound)
    before = len(storage.table().transactions)
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="k-" + case, manifest=manifest, admissions=admissions, completion_scope=scope)
    assert error.value.code == ("authority-changed" if case == "stale-check" else "manifest-invalid")
    assert len(storage.table().transactions) == before


def test_complete_frozen_source_fences_commit_with_the_terminal_job(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain_with_scope(xfer, WIDE_SCOPE)
    fenced, bound = source_fences(storage)
    done = finish(ledger, job, "succeeded", result, stage_completion=lambda prepared: {
        "writes": [], "checks": fenced[:3], "sourceChecks": fenced, "sourceBindings": list(reversed(bound))})
    assert done["status"] == "succeeded"
    assert len([e for e in storage.table().transactions[-1]["TransactItems"] if "ConditionCheck" in e]) == 90   # project + admission + 88 source


def test_non_source_operations_beyond_the_frozen_scope_are_refused(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)                      # default scope: 10 non-source operations
    extra = lambda prepared: {"writes": [{"owner": OWNER, "kind": "wb_artifact", "item": {"id": f"w{i}"},
                                          "expected_version": None} for i in range(6)], "checks": []}
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, stage_completion=extra)
    assert error.value.code == "execution-completion-scope"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def service_call(ledger, job, stage, kind, *, status="completed", session="svc-session-1"):
    """A recorded call intent plus outcome: the observed service task a receipt must reference."""
    call = ledger.tool().intent(*ids(job), stage=stage, kind=kind, min_remaining_ms=0)["callId"]
    if status is not None:
        ledger.tool().outcome(*ids(job), call, status=status,
                              **({} if kind == "model" else {"service_session_id": session}))
    if kind == "model":
        return {"kind": "model", "sessionId": job["attempt"]["sessionId"], "taskId": call}
    pinned = getattr(_ledger_module, "TOOL_PROFILES", {}).get(kind)
    return {"kind": kind, "sessionId": session, "taskId": call, "exitCode": 0,
            **({"profile": pinned} if pinned else {})}


SERVICE_CASES = ["missing", "wrong-kind", "unrelated-task", "unrelated-session", "nonzero-exit", "other-stage-call",
                 "call-not-completed", "runtime-foreign-session", "reused-task", "model-exit-code"]


@pytest.mark.parametrize("case", SERVICE_CASES)
def test_receipts_must_carry_stage_specific_service_bindings(env, case):
    """Finding 3: a signed receipt whose service evidence contradicts the stage, call or outcome is rejected."""
    storage, ledger, _ = env
    job, previous = staged_through(env, "browser")
    service = service_call(ledger, job, "browser", "browser")
    stage, bundle = "browser", required_inputs(job, "browser")
    if case == "missing":
        service = None
    if case == "wrong-kind":
        service = {**service, "kind": "model"}
    if case == "unrelated-task":
        service = {**service, "taskId": "task-unrelated"}
    if case == "unrelated-session":
        service = {**service, "sessionId": "svc-other"}
    if case == "nonzero-exit":
        service = {**service, "exitCode": 17}
    if case == "other-stage-call":
        service = service_call(ledger, job, "compile", "interpreter")
        service = {**service, "kind": "browser"}
    if case == "call-not-completed":
        service = service_call(ledger, job, "browser", "browser", status=None)
    if case == "runtime-foreign-session":
        stage, service = "verify", {"kind": "runtime", "sessionId": "rt-" + "1" * 40}
    if case == "reused-task":
        first = chained(job, "browser", "n0", previous=previous, status="ok", service=service, inputs=bundle)
        job = ledger.tool().stage(*ids(job), first)
        previous = receipt_hash(first)
    if case == "model-exit-code":
        stage, service = "generate", {**service_call(ledger, job, "generate", "model"), "exitCode": 0}
        bundle = required_inputs(job, "generate")
    if case == "runtime-foreign-session":
        job = ledger.tool().stage(*ids(job), chained(job, "browser", "n0", previous=previous, status="ok",
                                                    service=service_call(ledger, job, "browser", "browser"),
                                                    inputs=bundle,
                                                    outputs=[put_output(storage, job, "browser", "s.png", b"s")]))
        previous, bundle = job["stages"][-1]["receiptHash"], required_inputs(job, "verify")
    extra = {"status": "ok", "inputs": bundle} if service is None else {"status": "ok", "service": service,
                                                                         "inputs": bundle}
    body = chained(job, stage, "n1", previous=previous, **extra)
    if service is None:
        body = resign({k: v for k, v in body.items() if k != "service"})
    before = storage.get(OWNER, "job", job["id"])["version"]
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(*ids(job), body)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["version"] == before


def test_a_failed_browser_receipt_may_report_a_nonzero_exit(env):
    storage, ledger, _ = env
    job, previous = staged_through(env, "browser")
    service = {**service_call(ledger, job, "browser", "browser", status="failed"), "exitCode": 17}
    job = ledger.tool().stage(*ids(job), chained(job, "browser", "n1", previous=previous, status="failed",
                                                 service=service, inputs=required_inputs(job, "browser")))
    assert job["stages"][-1]["status"] == "failed" and job["stages"][-1]["service"]["taskId"] == service["taskId"]


def test_the_reviewed_contradictory_browser_receipt_is_rejected(env):
    storage, ledger, _ = env
    job, previous = staged_through(env, "browser")
    forged = chained(job, "browser", "n1", previous=previous, status="ok", inputs=required_inputs(job, "browser"),
                     service={"kind": "model", "sessionId": "sess-unrelated", "taskId": "task-unrelated", "exitCode": 17})
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(*ids(job), forged)
    assert error.value.code == "receipt-invalid"


# === PR #27 review round 2 regressions ===============================================================

def test_chunk_operation_ids_are_bound_to_handle_index_and_hash(xfer):
    """Review 2 finding 10: reusing a chunk operation ID for a different submission is an operation conflict."""
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    data = b"a" * (CHUNK + 3)
    handle = open_out(ledger, job, data)
    operation = op_id()
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data[:CHUNK]), operation_id=operation)
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data[:CHUNK]), operation_id=operation)
    with pytest.raises(LedgerError) as error:
        ledger.tool().write_chunk(*ids(job), handle["handleId"], 1, b64(data[CHUNK:]), operation_id=operation)
    assert error.value.code == "operation-changed"
    stored = storage.get(OWNER, "job", job["id"])["handles"][handle["handleId"]]
    assert len(stored["parts"]) == 1
    source = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    read_op = op_id()
    ledger.tool().read_chunk(*ids(job), source["handleId"], 0, operation_id=read_op)
    with pytest.raises(LedgerError) as error:
        ledger.tool().read_chunk(*ids(job), source["handleId"], 1, operation_id=read_op)
    assert error.value.code == "operation-changed"


class ClockAdvancingGate:
    """A daily cost gate whose probe takes time: the clock moves between the checks and the submission."""

    def __init__(self, now, step):
        self.now, self.step = now, step

    def check(self):
        self.now[0] += self.step

    def record(self, tokens):
        pass


@pytest.mark.parametrize("case", ["lease", "deadline"])
def test_intent_rechecks_lease_and_deadline_immediately_before_submission(env, case):
    """Review 2 finding 7: the protected-operation guard runs inside the transaction attempt."""
    storage, _, now = env
    job = running(env)
    if case == "lease":
        now[0] = job["attempt"]["leaseExpiresAt"] - 1
        step, expected = 2, "stale-attempt"
    else:
        from workspace.execution_ledger import _seed_for_tests
        stored = storage.get(OWNER, "job", job["id"])
        job = _seed_for_tests(storage, OWNER, "job", {**stored, "attempt": {**stored["attempt"],
                              "leaseExpiresAt": stored["deadlineAt"]}}, stored["version"])
        now[0] = job["deadlineAt"] - 65_000
        step, expected = 70_000, "deadline"
    ledger = offline_ledger(storage, cost_gate=ClockAdvancingGate(now, step))
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0)
    assert error.value.code == expected
    assert storage.get(OWNER, "job", job["id"])["calls"] == []


def withdraw_admission(storage):
    record = storage.get(OWNER, "asset", "adm-src")
    storage.put(OWNER, "asset", {**record, "status": "withdrawn"}, record["version"])


def test_admission_withdrawal_stops_intents(xfer):
    """Review 2 finding 1: every consumed admission is revalidated by the protected-operation guard."""
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    withdraw_admission(storage)
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0)
    assert error.value.code == "authority-changed"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["calls"] == [] and stored["status"] == "failed"


def test_admission_withdrawal_stops_completion(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    withdraw_admission(storage)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "authority-changed"
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"


def test_admission_authority_is_a_transaction_predicate(xfer):
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0)
    assert "asset#adm-src" in last_transaction_checks(storage)
    # A withdrawal racing the submission aborts it.
    table = storage.table()
    table.before_transaction = lambda: withdraw_admission(storage)
    with pytest.raises(LedgerError):
        ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0)
    assert len(storage.get(OWNER, "job", job["id"])["calls"]) == 1


def test_unresolved_model_calls_block_successful_completion(xfer):
    """Review 2 finding 6: a possibly billed call without an outcome keeps the attempt open for recovery."""
    storage, ledger, now, _ = xfer
    job, result = chain(xfer)
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0)["callId"]
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "calls-unresolved"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "running" and stored["calls"][-1]["status"] == "intent"
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    swept = ledger.reconciler().sweep(OWNER, job["id"])
    assert swept["status"] == "recovery_required" and swept["unknownOutcome"] is True
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[], status="succeeded",
                                      result=result)
    assert error.value.code == "calls-unresolved"
    assert call


@pytest.mark.parametrize("entry", [
    "key-only", "null-hash", "short-hash", "non-string-key", "not-a-dict"])
def test_malformed_result_manifest_entries_are_refused(xfer, entry):
    """Review 2 finding 4: every manifest entry needs a valid key and SHA-256 that is a verified chain output."""
    storage, ledger, _, _ = xfer
    job, result = chain(xfer, "design.extract", skip=("verify",))
    missing = storage.key_for(OWNER, "job", job["id"], f"out/{job['attempt']['id']}/verify/missing.bin")
    listed = {"key-only": {"key": missing}, "null-hash": {"key": missing, "sha256": None},
              "short-hash": {"key": missing, "sha256": "ab"}, "non-string-key": {"key": 7, "sha256": "0" * 64},
              "not-a-dict": "missing.bin"}[entry]
    body = _json.dumps({"files": [listed]}).encode()
    outputs = [put_output(storage, job, "verify", "verify.out", b"verify"),
               put_output(storage, job, "verify", "result-manifest.json", body)]
    r = chained(job, "verify", "n-verify", previous=result["receipts"][-1], outputs=outputs, status="ok",
                result=GOOD["verify"][1], inputs=required_inputs(job, "verify"))
    job = ledger.tool().stage(*ids(job), r)
    manifest = job["stages"][-1]["outputs"][-1]
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", {"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                          "receipts": [*result["receipts"], receipt_hash(r)]})
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


class CountingVerifier(TestKeyVerifier):
    """The current verifier at completion: it may have rotated keys and now reject every receipt."""
    __test__ = False

    def __init__(self, accept):
        super().__init__()
        self.accept, self.calls = accept, 0

    def verify(self, receipt):
        self.calls += 1
        return self.accept and super().verify(receipt)


def test_completion_reverifies_the_retained_signed_chain_with_the_current_verifier(xfer):
    """Review 2 finding 3: signed receipts are retained immutably and re-verified at finish."""
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    refs = [row["receiptRef"] for row in storage.get(OWNER, "job", job["id"])["stages"]]
    assert all(storage.get_blob(ref) for ref in refs)
    ledger.verifier = CountingVerifier(accept=False)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "receipt-invalid" and ledger.verifier.calls >= 1
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"
    ledger.verifier = CountingVerifier(accept=True)
    assert finish(ledger, job, "succeeded", result)["status"] == "succeeded"
    assert ledger.verifier.calls == 2 * len(refs)      # at re-read and again by the submission key guard


def test_completion_refuses_a_missing_retained_receipt(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    ref = storage.get(OWNER, "job", job["id"])["stages"][0]["receiptRef"]
    storage.s3().delete_object(Bucket=storage.bucket, Key=ref)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "receipt-invalid"


def entry_of(output):
    return {"key": output["key"], "sha256": output["sha256"], "size": output["size"]}


def staged_through(xfer, upto, operation="design.generate"):
    """Stage the operation's receipts before `upto` with their required inputs; returns (job, previous)."""
    job, result = chain(xfer, operation, skip=tuple(OPS[operation][OPS[operation].index(upto):]))
    return job, result["receipts"][-1]


def test_browser_receipt_must_verify_the_exact_compiled_bundle(xfer):
    """Review 2 finding 2: stage inputs follow the operation's evidence graph; Browser names the compile bundle."""
    storage, ledger, _, _ = xfer
    job, previous = staged_through(xfer, "browser")
    context_out = next(row for row in job["stages"] if row["stage"] == "context")["outputs"][0]
    service = service_call(ledger, job, "browser", "browser")
    for inputs in ([entry_of(context_out)], []):
        forged = chained(job, "browser", "n-b", previous=previous, status="ok", service=service, inputs=inputs,
                         outputs=[put_output(storage, job, "browser", "shot.png", b"png")])
        with pytest.raises(LedgerError) as error:
            ledger.tool().stage(*ids(job), forged)
        assert error.value.code == "receipt-invalid"


@pytest.mark.parametrize("profile", [None, "chromium-unpinned/9"])
def test_browser_and_compiler_receipts_bind_the_pinned_tool_profile(xfer, profile):
    storage, ledger, _, _ = xfer
    job, previous = staged_through(xfer, "browser")
    bundle = next(row for row in job["stages"] if row["stage"] == "compile")["outputs"][0]
    service = {k: v for k, v in service_call(ledger, job, "browser", "browser").items() if k != "profile"}
    if profile:
        service["profile"] = profile
    forged = chained(job, "browser", "n-b", previous=previous, status="ok", service=service,
                     inputs=[entry_of(bundle)], outputs=[put_output(storage, job, "browser", "shot.png", b"png")])
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(*ids(job), forged)
    assert error.value.code == "receipt-invalid"


def test_service_case_controls_are_accepted(env):
    """Positive control for the service-binding cases: the correctly bound receipts stage."""
    storage, ledger, _ = env
    job, previous = staged_through(env, "browser")
    shot = put_output(storage, job, "browser", "s.png", b"s")
    job = ledger.tool().stage(*ids(job), chained(job, "browser", "n0", previous=previous, status="ok",
                                                 service=service_call(ledger, job, "browser", "browser"),
                                                 inputs=required_inputs(job, "browser"), outputs=[shot]))
    job = ledger.tool().stage(*ids(job), chained(job, "verify", "n1", previous=job["stages"][-1]["receiptHash"],
                                                 status="ok", inputs=required_inputs(job, "verify")))
    assert [row["stage"] for row in job["stages"]][-2:] == ["browser", "verify"]


def test_failed_contention_settlement_cannot_permit_further_completion_submissions():
    """Review 2 finding 8: the attempt's completion submissions are counted durably before each submission."""
    env = flaky_env("contention-settlement", failures=6)     # 3 completion + 3 settlement submissions contend
    storage, ledger, _ = env
    job, result = chain(env)
    operation = op_id()
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, operation_id=operation)
    assert error.value.code == "completion-contention"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["attempt"]["completionSubmissions"] == 3
    for again in (operation, op_id()):
        with pytest.raises(LedgerError) as error:
            finish(ledger, job, "succeeded", result, operation_id=again)
        assert error.value.code in ("completion-contention", "terminal", "stale-attempt")
    completions = [t for t in storage.table().transactions
                   if any("Put" in e and e["Put"]["Item"].get("status") == "succeeded" for e in t["TransactItems"])]
    assert completions == [] and storage.get(OWNER, "job", job["id"])["status"] == "failed"


# === PR #27 review round 3 regressions ===============================================================

@pytest.mark.parametrize("race", ["withdrawal", "lease-expiry"])
def test_retried_chunk_read_runs_the_final_authority_and_temporal_fence(xfer, race):
    """Review 3 finding 2: a retried chunk is fenced like a first read, without charging usage twice."""
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    operation = op_id()
    ledger.tool().read_chunk(*ids(job), handle["handleId"], 0, operation_id=operation)
    again = ledger.tool().read_chunk(*ids(job), handle["handleId"], 0, operation_id=operation)
    assert again["index"] == 0
    assert storage.get(OWNER, "job", job["id"])["transferUsage"]["chunks"] == 1
    original = storage.get_blob

    def racing(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key == handle_key:
            if race == "withdrawal":
                withdraw_admission(storage)
            else:
                now[0] = storage.get(OWNER, "job", job["id"])["attempt"]["leaseExpiresAt"]
        return data
    handle_key = storage.get(OWNER, "job", job["id"])["handles"][handle["handleId"]]["key"]
    storage.get_blob = racing
    try:
        with pytest.raises(LedgerError) as error:
            ledger.tool().read_chunk(*ids(job), handle["handleId"], 0, operation_id=operation)
    finally:
        storage.get_blob = original
    assert error.value.code in ("conflict", "stale-attempt")
    assert storage.get(OWNER, "job", job["id"])["transferUsage"]["chunks"] == 1


def test_a_completed_model_call_without_usage_keeps_its_conservative_reservation(env):
    """Review 3 finding 4: missing usage is charged at the full reservation and marked estimated."""
    storage, ledger, _ = env
    job = running(env)
    budget = PROFILE_DEFAULT["tokenBudget"]
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                max_tokens=budget)["callId"]
    done = ledger.tool().outcome(*ids(job), call, status="completed")
    assert done["budget"]["tokensUsed"] == budget and done["budget"]["tokensReserved"] == 0
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["calls"][-1]["usageEstimated"] is True
    assert ledger.cost_gate.recorded == [budget]
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0, max_tokens=budget)
    assert error.value.code == "token-budget"


@pytest.mark.parametrize("transition", ["cancel", "fail", "revocation"])
def test_terminal_transitions_keep_outstanding_call_obligations(xfer, transition):
    """Review 3 finding 3: a terminal job with an unresolved call stays uncertain and due for settlement."""
    from workspace.storage import DUE_OWNER
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0, max_tokens=1000)
    if transition == "cancel":
        ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    elif transition == "fail":
        ledger.tool().fail(*ids(job), code="runtime-crash")
    else:
        withdraw_admission(storage)
        with pytest.raises(LedgerError):
            ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0)
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] in ("cancelled", "failed")
    assert stored["unknownOutcome"] is True and stored["dueId"] is not None
    due = storage.get(DUE_OWNER, "exec_due", stored["dueId"])
    assert due["status"] == "pending" and due["dueAt"] == stored["settlementDueAt"]
    ledger.reconciler().sweep(OWNER, job["id"])                    # before the bound: nothing is revived
    assert storage.get(OWNER, "job", job["id"])["calls"][-1]["status"] == "intent"
    now[0] = stored["settlementDueAt"]
    ledger.reconciler().run_due()
    settled = storage.get(OWNER, "job", job["id"])
    assert settled["status"] == stored["status"] and settled["calls"][-1]["status"] == "unknown"
    assert settled["budget"]["tokensReserved"] == 0 and settled["budget"]["tokensUsed"] == 1000
    assert settled["dueId"] is None and settled["unknownOutcome"] is True


def observation(job, call_id, *, status="completed", usage=None, nonce="obs-1", **over):
    body = {"schemaVersion": 1, "type": "call-outcome", "executionId": job["id"], "attemptId": job["attempt"]["id"],
            "fence": job["attempt"]["fence"], "callId": call_id, "stage": "generate", "kind": "model",
            "status": status, "service": {"kind": "model", "sessionId": job["attempt"]["sessionId"]},
            "nonce": nonce, "keyId": TestKeyVerifier.key_id, **({"usage": usage} if usage else {}), **over}
    return {**body, "signature": TestKeyVerifier().sign(body)}


def lost_model_call(xfer):
    """design.extract: context staged, a model call started, then the Runtime lost its lease before outcome."""
    storage, ledger, now, _ = xfer
    job, previous = staged_through(xfer, "generate", operation="design.extract")
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                max_tokens=5000)["callId"]
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    assert ledger.reconciler().sweep(OWNER, job["id"])["status"] == "recovery_required"
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(*ids(job), call, status="completed", usage={"inputTokens": 10, "outputTokens": 20})
    assert error.value.code == "stale-attempt"
    return storage.get(OWNER, "job", job["id"]), call, previous


def test_reconciler_settles_a_recovered_call_observation_then_reconciles(xfer):
    """Review 3 finding 5: an IAM-only settlement records a verified recovered outcome before completion."""
    storage, ledger, _, _ = xfer
    job, call, previous = lost_model_call(xfer)
    assert not hasattr(ledger.tool(), "settle") and not hasattr(ledger.api(), "settle")
    usage = {"inputTokens": 10, "outputTokens": 20}
    settled = ledger.reconciler().settle(OWNER, job["id"], call, observation(job, call, usage=usage))
    row = next(c for c in settled["calls"] if c["callId"] == call)
    assert row["status"] == "completed" and row["usage"] == usage and row["settledBy"] == "reconciler"
    assert settled["budget"]["tokensUsed"] == 30 and settled["budget"]["tokensReserved"] == 0
    gen_out = put_output(storage, job, "generate", "generate.out", b"generate")
    generate = chained(job, "generate", "n-gen", previous=previous, status="ok", outputs=[gen_out],
                       inputs=required_inputs(job, "generate"),
                       service={"kind": "model", "sessionId": job["attempt"]["sessionId"], "taskId": call})
    staged = {**job, "stages": [*job["stages"], {"stage": "generate", "outputs": [gen_out],
                                                 "attemptId": job["attempt"]["id"]}]}
    out = put_output(storage, job, "verify", "verify.out", b"verify")
    files = [{"key": gen_out["key"], "sha256": gen_out["sha256"]}]
    manifest = put_output(storage, job, "verify", "result-manifest.json", _json.dumps({"files": files}).encode())
    verify = chained(job, "verify", "n-ver", previous=receipt_hash(generate), status="ok", outputs=[out, manifest],
                     inputs=required_inputs(staged, "verify"), result=GOOD["verify"][1])
    hashes = [row["receiptHash"] for row in job["stages"]] + [receipt_hash(generate), receipt_hash(verify)]
    done = ledger.reconciler().reconcile(OWNER, job["id"], job["attempt"]["id"], receipts=[generate, verify],
                                         status="succeeded", result={"manifestRef": manifest["key"],
                                                                     "manifestHash": manifest["sha256"],
                                                                     "receipts": hashes})
    assert done["status"] == "succeeded"


@pytest.mark.parametrize("case", ["forged", "wrong-session", "other-call", "replayed-nonce", "wrong-attempt"])
def test_settlement_rejects_unverified_or_mismatched_observations(xfer, case):
    storage, ledger, _, _ = xfer
    job, call, _ = lost_model_call(xfer)
    obs = observation(job, call, usage={"inputTokens": 1, "outputTokens": 1})
    if case == "forged":
        obs = {**obs, "signature": "0" * 64}
    if case == "wrong-session":
        obs = resign({**obs, "service": {"kind": "model", "sessionId": "rt-" + "9" * 40}})
    if case == "other-call":
        obs = resign({**obs, "callId": "call-" + "0" * 32})
    if case == "wrong-attempt":
        obs = resign({**obs, "attemptId": "att-" + "0" * 32})
    if case == "replayed-nonce":
        obs = resign({**obs, "nonce": job["stages"][0]["nonce"]})
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().settle(OWNER, job["id"], call, obs)
    assert error.value.code in ("receipt-invalid", "call-invalid")
    assert next(c for c in storage.get(OWNER, "job", job["id"])["calls"] if c["callId"] == call)["status"] == "intent"


@pytest.mark.parametrize("manifest_choice", ["new", "old"])
def test_recompilation_invalidates_downstream_verification(xfer, manifest_choice):
    """Review 3 finding 1: completion needs one complete current chain; a newer compile makes browser/verify stale."""
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    rebuilt = {**put_output(storage, job, "compile", "bundle-2.js", b"recompiled"), "role": "bundle"}
    files = [{"key": row["outputs"][0]["key"], "sha256": row["outputs"][0]["sha256"]} for row in job["stages"]
             if row["stage"] != "compile"] + [{"key": rebuilt["key"], "sha256": rebuilt["sha256"]}]
    manifest = put_output(storage, job, "compile", "result-manifest-2.json", _json.dumps({"files": files}).encode())
    again = chained(job, "compile", "n-recompile", previous=result["receipts"][-1], status="ok",
                    outputs=[rebuilt, manifest], inputs=required_inputs(job, "compile"),
                    result=GOOD["compile"][1], service=service_call(ledger, job, "compile", "interpreter"))
    job = ledger.tool().stage(*ids(job), again)
    hashes = [*result["receipts"], receipt_hash(again)]
    final = ({"manifestRef": manifest["key"], "manifestHash": manifest["sha256"], "receipts": hashes}
             if manifest_choice == "new" else {**result, "receipts": hashes})
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", final)
    assert error.value.code == "evidence-stale"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def test_reverification_of_the_new_bundle_completes(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    previous, hashes = result["receipts"][-1], list(result["receipts"])
    for stage in ("compile", "browser", "verify"):
        outputs = [put_output(storage, job, stage, f"{stage}-2.out", f"{stage}-2".encode())]
        if stage == "compile":
            outputs[0]["role"] = "bundle"
        if stage == "verify":
            current = {row["stage"]: row for row in job["stages"]}
            files = [{"key": current[name]["outputs"][0]["key"], "sha256": current[name]["outputs"][0]["sha256"]}
                     for name in ("context", "generate", "compile", "browser")] + [
                {"key": outputs[0]["key"], "sha256": outputs[0]["sha256"]}]
            outputs.append(put_output(storage, job, stage, "result-manifest-2.json",
                                      _json.dumps({"files": files}).encode()))
        kind = STAGE_SERVICES[stage][0]
        service = {} if kind == "runtime" else {"service": service_call(ledger, job, stage, kind)}
        r = chained(job, stage, f"n2-{stage}", previous=previous, status="ok", outputs=outputs,
                    inputs=required_inputs(job, stage), result=GOOD[stage][1], **service)
        job = ledger.tool().stage(*ids(job), r)
        previous = receipt_hash(r)
        hashes.append(previous)
    manifest = job["stages"][-1]["outputs"][-1]
    done = finish(ledger, job, "succeeded", {"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                             "receipts": hashes})
    assert done["status"] == "succeeded"


# === PR #27 review round 4 regressions ===============================================================

def uploaded(ledger, job, data, *, stage, name):
    """Upload bytes through the real transfer facade; returns the closed output entry."""
    handle = open_out(ledger, job, data, name=name, stage=stage)
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data), operation_id=op_id())
    closed = ledger.tool().close_output(*ids(job), handle["handleId"], operation_id=op_id())
    output = next(row for row in closed["transfers"] if row["handleId"] == handle["handleId"])
    return closed, {"key": output["key"], "sha256": output["sha256"], "size": output["size"]}


def final_verify(xfer, job, previous, extra_outputs, files, nonce="n-verify-4"):
    storage, ledger = xfer[0], xfer[1]
    verify_out = put_output(storage, job, "verify", "verify-4.out", b"verify-4")
    manifest = put_output(storage, job, "verify", "result-manifest-4.json", _json.dumps({"files": files}).encode())
    r = chained(job, "verify", nonce, previous=previous, status="ok", result=GOOD["verify"][1],
                outputs=[verify_out, *extra_outputs, manifest], inputs=required_inputs(job, "verify"))
    return r, manifest


@pytest.mark.parametrize("stage,role", [("verify", "bundle"), ("browser", "bundle"), ("verify", "source"),
                                        ("compile", "source"), ("verify", "deliverable")])
def test_output_roles_are_restricted_per_stage(xfer, stage, role):
    """Review 4 finding 2: only compile emits a bundle and only generate a source; roles are a closed set."""
    storage, ledger, _, _ = xfer
    job, previous = staged_through(xfer, stage)
    out = {**put_output(storage, job, stage, "replacement.js", b"replacement"), "role": role}
    kind = STAGE_SERVICES[stage][0]
    service = {} if kind == "runtime" else {"service": service_call(ledger, job, stage, kind)}
    r = chained(job, stage, "n-role", previous=previous, status="ok", result=GOOD[stage][1], outputs=[out],
                inputs=required_inputs(job, stage), **service)
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(*ids(job), r)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["stages"] == job["stages"]


def test_completion_refuses_a_deliverable_bundle_that_was_not_compiled_and_browser_verified(xfer):
    """Review 4 finding 2 (reviewed scenario): replacement bytes uploaded after verification are not the bundle."""
    storage, ledger, _, _ = xfer
    job, previous = staged_through(xfer, "verify")
    job, replacement = uploaded(ledger, job, b"unverified replacement bundle", stage="verify", name="bundle.js")
    files = [{"key": replacement["key"], "sha256": replacement["sha256"]}]        # only the replacement
    r, manifest = final_verify(xfer, job, previous, [{**replacement, "role": "artifact"}], files)
    job = ledger.tool().stage(*ids(job), r)
    hashes = [row["receiptHash"] for row in job["stages"]]
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", {"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                          "receipts": hashes})
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def test_manifest_roles_must_match_the_recorded_output_role(xfer):
    storage, ledger, _, _ = xfer
    job, previous = staged_through(xfer, "verify")
    bundle = next(row for row in job["stages"] if row["stage"] == "compile")["outputs"][0]
    context_out = next(row for row in job["stages"] if row["stage"] == "context")["outputs"][0]
    files = [{"key": bundle["key"], "sha256": bundle["sha256"]},
             {"key": context_out["key"], "sha256": context_out["sha256"], "role": "bundle"}]
    r, manifest = final_verify(xfer, job, previous, [], files)
    job = ledger.tool().stage(*ids(job), r)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", {"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                          "receipts": [row["receiptHash"] for row in job["stages"]]})
    assert error.value.code == "receipt-invalid"


def test_completion_binds_the_deliverable_bundle_and_source_to_their_evidence(xfer):
    storage, ledger, _, _ = xfer
    job, previous = staged_through(xfer, "generate")
    source = {**put_output(storage, job, "generate", "page.tsx", b"export default () => null"), "role": "source"}
    job = ledger.tool().stage(*ids(job), chained(job, "generate", "n-g4", previous=previous, status="ok",
                                                 outputs=[source], inputs=required_inputs(job, "generate"),
                                                 service=service_call(ledger, job, "generate", "model")))
    rows = {}
    for stage in ("compile", "browser"):
        out = put_output(storage, job, stage, f"{stage}-4.out", f"{stage}-4".encode())
        if stage == "compile":
            out["role"] = "bundle"
        r = chained(job, stage, f"n-{stage}-4", previous=job["stages"][-1]["receiptHash"], status="ok",
                    outputs=[out], inputs=required_inputs(job, stage), result=GOOD[stage][1],
                    service=service_call(ledger, job, stage, STAGE_SERVICES[stage][0]))
        job = ledger.tool().stage(*ids(job), r)
        rows[stage] = (receipt_hash(r), out)
    bundle = rows["compile"][1]
    files = [{"key": bundle["key"], "sha256": bundle["sha256"], "role": "bundle"},
             {"key": source["key"], "sha256": source["sha256"], "role": "source"}]
    r, manifest = final_verify(xfer, job, job["stages"][-1]["receiptHash"], [], files)
    job = ledger.tool().stage(*ids(job), r)
    done = finish(ledger, job, "succeeded", {"manifestRef": manifest["key"], "manifestHash": manifest["sha256"],
                                             "receipts": [row["receiptHash"] for row in job["stages"]]})
    assert done["status"] == "succeeded"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["deliverables"]["bundle"] == {"key": bundle["key"], "sha256": bundle["sha256"],
                                                "compileReceipt": rows["compile"][0],
                                                "browserReceipt": rows["browser"][0]}
    assert stored["deliverables"]["sources"] == [{"key": source["key"], "sha256": source["sha256"],
                                                  "generateReceipt": job["stages"][1]["receiptHash"],
                                                  "compileReceipt": rows["compile"][0]}]


def replay_args(operation):
    return dict(stage="generate", kind="model", min_remaining_ms=0, max_tokens=1000, operation_id=operation)


@pytest.mark.parametrize("change", ["withdrawal", "revocation", "lease-loss", "deadline"])
def test_intent_replay_revalidates_authority_lease_and_time(xfer, change):
    """Review 4 finding 3: a replayed intent never bypasses the protected-operation guard."""
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    operation = op_id()
    first = ledger.tool().intent(*ids(job), **replay_args(operation))
    if change == "withdrawal":
        withdraw_admission(storage)
    elif change == "revocation":
        revoke_requester(storage)
    elif change == "lease-loss":
        now[0] += PROFILE_DEFAULT["leaseMs"] + 1
        assert ledger.reconciler().sweep(OWNER, job["id"])["status"] == "recovery_required"
    else:
        now[0] = storage.get(OWNER, "job", job["id"])["deadlineAt"]
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), **replay_args(operation))
    assert error.value.code == ("authority-changed" if change in ("withdrawal", "revocation") else "stale-attempt")
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == {"withdrawal": "failed", "revocation": "failed", "lease-loss": "recovery_required",
                                "deadline": "running"}[change]
    assert [call["callId"] for call in stored["calls"]] == [first["callId"]]
    assert stored["budget"]["calls"] == 1 and stored["budget"]["tokensReserved"] in (0, 1000)


def test_intent_replay_is_fenced_and_never_authorizes_another_invocation(xfer):
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    operation = op_id()
    first = ledger.tool().intent(*ids(job), **replay_args(operation))
    assert first.get("replayed") is not True
    before = len(storage.table().transactions)
    again = ledger.tool().intent(*ids(job), **replay_args(operation))
    assert again["callId"] == first["callId"] and again["replayed"] is True and again["callStatus"] == "intent"
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["budget"]["calls"] == 1 and stored["budget"]["tokensReserved"] == 1000
    [fence] = storage.table().transactions[before:]
    assert all("ConditionCheck" in entry for entry in fence["TransactItems"])     # check-only, nothing reserved
    # The final guard runs at submission: a lease that lapses while the replay is checked is refused.
    lease = stored["attempt"]["leaseExpiresAt"]
    original = ledger.cost_gate.check

    def lapse():
        now[0] = lease
        return original()
    ledger.cost_gate.check = lapse
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), **replay_args(operation))
    assert error.value.code == "stale-attempt"


class CrashingGate:
    """An offline cost gate whose usage write fails (a crash or an unavailable costguard) a given number of times."""

    def __init__(self, failures=1):
        self.failures, self.recorded, self.charges = failures, [], {}

    def check(self):
        return None

    def record(self, tokens, charge_id=None):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("synthetic crash after the outcome commit")
        if charge_id in self.charges:
            return
        self.charges[charge_id] = tokens
        self.recorded.append(tokens)


def crashed_outcome(xfer, failures=1):
    storage, _, now, _ = xfer
    ledger = offline_ledger(storage, cost_gate=CrashingGate(failures))
    job = run_job(ledger)
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                max_tokens=1000)["callId"]
    usage = {"inputTokens": 100, "outputTokens": 200}
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(*ids(job), call, status="completed", usage=usage)
    assert error.value.code == "accounting-pending"                  # surfaced, never swallowed
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["calls"][-1]["status"] == "completed"
    [obligation] = stored["accounting"]                              # persisted with the outcome
    assert obligation["tokens"] == 300 and obligation["callId"] == call
    return ledger, job, call, usage


def test_a_crash_after_the_outcome_commit_keeps_the_accounting_obligation_for_a_retry(xfer):
    """Review 4 finding 5: the identical retried outcome settles the retained obligation exactly once."""
    storage = xfer[0]
    ledger, job, call, usage = crashed_outcome(xfer)
    again = ledger.tool().outcome(*ids(job), call, status="completed", usage=usage)
    assert again["accounting"] == [] and ledger.cost_gate.recorded == [300]
    ledger.tool().outcome(*ids(job), call, status="completed", usage=usage)
    assert ledger.cost_gate.recorded == [300] and storage.get(OWNER, "job", job["id"])["accounting"] == []


def test_a_retained_obligation_survives_cancellation_and_is_settled_by_the_reconciler(xfer):
    from workspace.storage import DUE_OWNER
    storage, _, now, _ = xfer
    ledger, job, call, _ = crashed_outcome(xfer)
    ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["status"] == "cancelled" and len(stored["accounting"]) == 1
    due = storage.get(DUE_OWNER, "exec_due", stored["dueId"])
    assert due["status"] == "pending"
    now[0] = due["dueAt"]
    ledger.reconciler().run_due()
    settled = storage.get(OWNER, "job", job["id"])
    assert settled["accounting"] == [] and ledger.cost_gate.recorded == [300]
    assert settled["status"] == "cancelled" and settled["dueId"] is None


def test_an_obligation_recorded_before_its_removal_crashed_is_not_charged_twice(xfer):
    storage = xfer[0]
    ledger, job, call, usage = crashed_outcome(xfer)
    original = ledger._commit
    state = {"crash": 3}                  # every bounded removal attempt fails after the charge was recorded

    def crash_removal(owner, before, after, **kwargs):
        if state["crash"] and after.get("accounting") == [] and before.get("accounting"):
            state["crash"] -= 1
            raise LedgerError("conflict")
        return original(owner, before, after, **kwargs)
    ledger._commit = crash_removal
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(*ids(job), call, status="completed", usage=usage)
    assert error.value.code == "accounting-pending"
    assert len(storage.get(OWNER, "job", job["id"])["accounting"]) == 1
    ledger._commit = original
    ledger.reconciler().sweep(OWNER, job["id"])
    assert storage.get(OWNER, "job", job["id"])["accounting"] == [] and ledger.cost_gate.recorded == [300]


def test_production_cost_gate_records_each_charge_once_and_surfaces_failures(monkeypatch):
    import types
    from workspace.execution_ledger import CostGuardGate

    class Canceled(Exception):
        def __init__(self, code):
            super().__init__(code)
            self.response = {"CancellationReasons": [{"Code": code}, {"Code": "None"}]}

    class Client:
        exceptions = types.SimpleNamespace(TransactionCanceledException=Canceled)

        def __init__(self):
            self.markers, self.tokens, self.down = set(), 0, False

        def transact_write_items(self, TransactItems):
            if self.down:
                raise OSError("costguard unreachable")
            marker = TransactItems[0]["Put"]["Item"]["pk"]
            if marker in self.markers:
                raise Canceled("ConditionalCheckFailed")
            self.markers.add(marker)
            self.tokens += TransactItems[1]["Update"]["ExpressionAttributeValues"][":t"]

    client = Client()
    table = types.SimpleNamespace(name="cache-test", meta=types.SimpleNamespace(client=client))
    fake = types.SimpleNamespace(_tbl=table, budget_ok=lambda: True, _today=lambda: "2027-01-15")
    monkeypatch.setenv("CACHE_TABLE", "cache-test")
    monkeypatch.setitem(sys.modules, "common.costguard", fake)
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(costguard=fake))
    gate = CostGuardGate()
    gate.record(300, charge_id="chg-1")
    gate.record(300, charge_id="chg-1")
    assert client.tokens == 300
    client.down = True
    with pytest.raises(LedgerError) as error:
        gate.record(5, charge_id="chg-2")
    assert error.value.code == "daily-budget-unavailable"


@pytest.mark.parametrize("then", ["cancel", "complete-replacement"])
def test_explicit_retry_charges_the_superseded_unknown_call_independently(xfer, then):
    """Review 4 finding 4: a retried attempt's unresolved call is conservatively charged when marked unknown."""
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                max_tokens=1000)["callId"]
    now[0] += PROFILE_DEFAULT["leaseMs"] + 1
    assert ledger.reconciler().sweep(OWNER, job["id"])["status"] == "recovery_required"
    retried = ledger.api().retry(OWNER, job["id"], actor="designer-1", acknowledge_unknown_outcome=True)
    row = next(c for c in retried["calls"] if c["callId"] == call)
    assert row["status"] == "unknown" and row["usageEstimated"] is True
    assert retried["budget"]["tokensReserved"] == 0 and retried["budget"]["tokensUsed"] == 1000
    assert ledger.cost_gate.recorded == [1000] and retried["accounting"] == []
    if then == "cancel":
        ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    else:
        job = ledger.dispatcher().allocate(OWNER, job["id"])
        job = ledger.tool().claim(*ids(job))
        other = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                     max_tokens=500)["callId"]
        ledger.tool().outcome(*ids(job), other, status="completed", usage={"inputTokens": 1, "outputTokens": 2})
    stored = storage.get(OWNER, "job", job["id"])
    assert stored["budget"]["tokensReserved"] == 0
    assert stored["budget"]["tokensUsed"] == (1000 if then == "cancel" else 1003)
    assert ledger.cost_gate.recorded == ([1000] if then == "cancel" else [1000, 3])


@pytest.mark.parametrize("change", ["verifier-rejects", "key-revision"])
def test_key_revocation_during_completion_staging_is_refused_at_submission(xfer, change):
    """Review 4 finding 6: completion is bound to the verified key-registry revision and rechecked at submission."""
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)

    def revoke_while_staging(context):
        if change == "verifier-rejects":
            ledger.verifier.verify = lambda receipt: False
        else:
            ledger.verifier.epoch += 1
        return {"writes": [], "checks": [], "sourceBindings": []}
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result, stage_completion=revoke_while_staging)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def test_completion_records_the_verified_key_revision(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer)
    done = finish(ledger, job, "succeeded", result)
    assert done["status"] == "succeeded"
    assert storage.get(OWNER, "job", job["id"])["verifiedKeyRevision"] == ledger.verifier.revision()


def test_production_requires_a_key_registry_revision(monkeypatch):
    from workspace import execution_ledger as module

    class NoRevision:
        def verify(self, receipt):
            return False
    monkeypatch.setattr(module, "_REGISTERED", {NoRevision})
    with pytest.raises(PermissionError):
        Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b", single_attempt=True),
                          verifier=NoRevision())


@pytest.mark.parametrize("state", ["recovery_required", "terminal"])
def test_settlement_enforces_its_recovery_bound_at_submission(xfer, state):
    """Review 4 finding 7: the bound is rechecked by before_attempt, after the observation is verified."""
    storage, ledger, now, _ = xfer
    job, call, _ = lost_model_call(xfer)
    if state == "terminal":
        ledger.api().cancel(OWNER, job["id"], actor="designer-1")
        job = storage.get(OWNER, "job", job["id"])
        bound = job["settlementDueAt"]
    else:
        bound = min(job["recoveryAt"] + PROFILE_DEFAULT["recoveryWindowMs"], job["deadlineAt"])
    obs = observation(job, call, usage={"inputTokens": 1, "outputTokens": 1})
    verify = ledger.verifier.verify

    def slow(value):
        now[0] = bound
        return verify(value)
    ledger.verifier.verify = slow
    with pytest.raises(LedgerError) as error:
        ledger.reconciler().settle(OWNER, job["id"], call, obs)
    assert error.value.code == "recovery-window"
    assert next(c for c in storage.get(OWNER, "job", job["id"])["calls"] if c["callId"] == call)["status"] == "intent"

def test_negative_output_chunk_indexes_are_refused(xfer):
    """Review 4 finding 8: index -1 is not a retry of the last written part."""
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    data = b"y" * 10
    handle = open_out(ledger, job, data)
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data))
    for index in (-1, -2):
        with pytest.raises(LedgerError) as error:
            ledger.tool().write_chunk(*ids(job), handle["handleId"], index, b64(data))
        assert error.value.code == "transfer-invalid"


# === PR #27 review round 5 regressions ===============================================================

def test_production_daily_charge_serializes_native_values_through_the_real_resource_client(monkeypatch):
    """Review 5 finding 1: costguard._tbl is a boto3 resource; the charge must be marshaled exactly once."""
    import json
    import types
    import boto3
    from botocore.stub import Stubber
    from workspace.execution_ledger import CostGuardGate
    table = boto3.resource("dynamodb", region_name="us-east-1", aws_access_key_id="test-only",
                           aws_secret_access_key="test-only").Table("cache-test")
    client = table.meta.client
    captured = []
    client.meta.events.register_first(
        "before-call.*.*", lambda params, model, **kwargs: captured.append(json.loads(params["body"]))
        if model.name == "TransactWriteItems" else None)
    fake = types.SimpleNamespace(_tbl=table, budget_ok=lambda: True, _today=lambda: "2027-01-15")
    monkeypatch.setenv("CACHE_TABLE", "cache-test")
    monkeypatch.setitem(sys.modules, "common.costguard", fake)
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(costguard=fake))
    with Stubber(client) as stub:
        stub.add_response("transact_write_items", {})
        stub.add_client_error("transact_write_items", "TransactionCanceledException",
                              response_meta={}, modeled_fields={"CancellationReasons": [
                                  {"Code": "ConditionalCheckFailed"}, {"Code": "None"}]})
        CostGuardGate().record(300, charge_id="chg-1")
        CostGuardGate().record(300, charge_id="chg-1")          # already recorded: idempotent, no error
        stub.assert_no_pending_responses()
    put, update = captured[0]["TransactItems"][0]["Put"], captured[0]["TransactItems"][1]["Update"]
    assert put["Item"]["pk"] == {"S": "usage-charge#chg-1"} and put["Item"]["tokens"] == {"N": "300"}
    assert update["Key"] == {"pk": {"S": "usage#2027-01-15"}}
    assert update["ExpressionAttributeValues"][":t"] == {"N": "300"}
    assert set(update["ExpressionAttributeValues"][":ttl"]) == {"N"}


@pytest.mark.parametrize("replacement", ["same-size", "other-size"])
def test_input_reads_are_pinned_to_the_validated_object_identity(xfer, replacement):
    """Review 5 finding 2: bytes replaced under an old admission are refused before any chunk is returned."""
    storage, ledger, _, data = xfer
    job = run_job(ledger)
    handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    stored = storage.get(OWNER, "job", job["id"])["handles"][handle["handleId"]]
    assert stored["etag"] and stored["total"] == len(data)
    first = ledger.tool().read_chunk(*ids(job), handle["handleId"], 0, operation_id=op_id())
    other = bytes(reversed(data)) if replacement == "same-size" else data + b"-appended"
    storage.put_blob(stored["key"], other, "application/octet-stream")
    for index in (0, 1):
        with pytest.raises(LedgerError) as error:
            ledger.tool().read_chunk(*ids(job), handle["handleId"], index, operation_id=op_id())
        assert error.value.code == "transfer-invalid"
    assert first["index"] == 0


@pytest.mark.parametrize("change", ["lease-expiry", "key-rotation"])
def test_final_submission_checks_run_after_the_final_signature_verification(xfer, change):
    """Review 5 finding 3: key revision is rechecked after re-verification and time is checked last."""
    storage, ledger, now, _ = xfer
    job, result = chain(xfer)
    refs = len(storage.get(OWNER, "job", job["id"])["stages"])
    verify, calls = ledger.verifier.verify, [0]

    def slow(receipt):
        calls[0] += 1
        if calls[0] == 2 * refs:                  # the last verification of the submission-time key guard
            if change == "lease-expiry":
                now[0] = storage.get(OWNER, "job", job["id"])["attempt"]["leaseExpiresAt"]
            else:
                ledger.verifier.epoch += 1
        return verify(receipt)
    ledger.verifier.verify = slow
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == ("stale-attempt" if change == "lease-expiry" else "receipt-invalid")
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


@pytest.mark.parametrize("case", ["no-approved-screenshot", "bundle-only-browser", "other-comparison"])
def test_release_completion_requires_the_frozen_approved_screenshot_comparison(xfer, case):
    """Review 5 finding 4: release success binds the Browser comparison to the approved screenshot frozen at
    admission."""
    storage, ledger, _, _ = xfer
    if case == "no-approved-screenshot":
        job, result = chain(xfer, "design.release", manifest=release_manifest(storage, screenshot=False))
        assert job["releaseBaseline"] is None
    elif case == "bundle-only-browser":
        job, result = chain(xfer, "design.release", comparison=False)
    else:
        job, result = chain(xfer, "design.release",
                            results={"browser": ("ok", {"visualDiff": 0.0, "comparison": {
                                "key": storage.key_for(OWNER, "run", "run-r", "other.png"), "sha256": "0" * 64}})},
                            comparison=False)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "status-inconsistent"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


def test_release_admission_freezes_the_approved_screenshot(xfer):
    storage, ledger, _, _ = xfer
    job, result = chain(xfer, "design.release")
    assert job["releaseBaseline"] == approved_screenshot(storage)
    assert finish(ledger, job, "succeeded", result)["status"] == "succeeded"
    body = _json.dumps({"admissions": ADM, "approved": {"sourceHash": "5" * 64, "bundleHash": "6" * 64,
                                                        "screenshot": {**approved_screenshot(storage),
                                                                       "sha256": "0" * 64}}}).encode()
    key = storage.key_for(OWNER, "run", "run-r", "bad-shot-manifest.json")
    storage.put_blob_once(key, body, "application/json")
    with pytest.raises(LedgerError) as error:
        admit(ledger, key="req-bad-shot", actor="alice", operation="design.release",
              manifest={"ref": key, "hash": hashlib.sha256(body).hexdigest()})
    assert error.value.code == "manifest-invalid"


# === PR #27 review round 6 regressions ===============================================================

@pytest.mark.parametrize("case", ["null-hash-negative-size", "missing-hash", "string-size", "bool-size",
                                  "authorized-wrong-size", "unauthorized-key", "extra-field"])
def test_receipt_inputs_are_strictly_validated_and_authorized(xfer, case):
    """Review 6 finding 2: every input is a well-formed {key, sha256, size} of an authorized input with its metadata."""
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    manifest = {"key": job["manifest"]["ref"], "sha256": job["manifest"]["hash"],
                "size": len(storage.get_blob(job["manifest"]["ref"]))}
    entry = {"null-hash-negative-size": {"key": storage.key_for(OWNER, "asset", "nowhere", "x"), "sha256": None,
                                         "size": -100},
             "missing-hash": {"key": manifest["key"], "size": manifest["size"]},
             "string-size": {**manifest, "size": str(manifest["size"])},
             "bool-size": {**manifest, "size": True},
             "authorized-wrong-size": {**manifest, "size": manifest["size"] + 1},
             "unauthorized-key": {**manifest, "key": storage.key_for(OWNER, "asset", "other", "x")},
             "extra-field": {**manifest, "role": "artifact"}}[case]
    r = chained(job, "context", "n-in", inputs=[entry], status="ok")
    with pytest.raises(LedgerError) as error:
        ledger.tool().stage(*ids(job), r)
    assert error.value.code == "receipt-invalid"
    assert storage.get(OWNER, "job", job["id"])["stages"] == []
    ok = ledger.tool().stage(*ids(job), chained(job, "context", "n-in-ok", inputs=[manifest], status="ok"))
    assert ok["stages"][-1]["inputs"] == [{"key": manifest["key"], "sha256": manifest["sha256"]}]


@pytest.mark.parametrize("admissions", [
    [{"decisionId": "adm-1", "revision": "1"}],
    [{"decisionId": "adm-1", "revision": "1", "artifactHash": None}],
    [{"decisionId": "adm-1", "revision": 1, "artifactHash": "a" * 64}],
    [{"decisionId": "", "revision": "1", "artifactHash": "a" * 64}],
    [{**ADM[0], "extra": True}],
    [ADM[0], ADM[0]],
])
def test_admissions_are_strictly_shaped(env, admissions):
    with pytest.raises(LedgerError) as error:
        admit(env[1], admissions=admissions)
    assert error.value.code == "admission-invalid"


def screenshot_only_manifest(storage, **approved):
    body = _json.dumps({"admissions": ADM, "approved": {"screenshot": approved_screenshot(storage), **approved},
                        "priors": []}).encode()
    key = storage.key_for(OWNER, "run", "run-r", f"shot-only-{hashlib.sha256(body).hexdigest()[:8]}.json")
    storage.put_blob_once(key, body, "application/json")
    return {"ref": key, "hash": hashlib.sha256(body).hexdigest()}


BUNDLE_SHA = hashlib.sha256(b"compile-bytes").hexdigest()        # the bytes chain() stores as the compile bundle


@pytest.mark.parametrize("case", ["no-approved-hashes", "empty-observed", "null-approved", "non-hex",
                                  "observed-not-the-compiled-bundle"])
def test_release_success_requires_explicit_valid_approved_and_observed_hashes(xfer, case):
    """Review 6 finding 1: missing hashes never compare equal as None."""
    storage, ledger, _, _ = xfer
    results, approved = {}, {"sourceHash": "5" * 64, "bundleHash": BUNDLE_SHA}
    if case == "no-approved-hashes":
        approved, results = {}, {"compile": ("ok", {})}
    elif case == "empty-observed":
        results = {"compile": ("ok", {})}
    elif case == "null-approved":
        approved = {"sourceHash": None, "bundleHash": None}
        results = {"compile": ("ok", {"sourceHash": None, "bundleHash": None})}
    elif case == "non-hex":
        approved = {"sourceHash": "s" * 64, "bundleHash": "u" * 64}
        results = {"compile": ("ok", {"sourceHash": "s" * 64, "bundleHash": "u" * 64})}
    else:
        approved = {"sourceHash": "5" * 64, "bundleHash": "6" * 64}
        results = {"compile": ("ok", {"sourceHash": "5" * 64, "bundleHash": "6" * 64})}
    job, result = chain(xfer, "design.release", results=results,
                        manifest=screenshot_only_manifest(storage, **approved))
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "status-inconsistent"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


@pytest.mark.parametrize("outage", ["persists", "recovers"])
def test_pending_accounting_is_settled_before_another_paid_intent(xfer, monkeypatch, outage):
    """Review 6 finding 3: an uncharged obligation never lets a further model call past the daily cap."""
    import types
    from workspace.execution_ledger import CostGuardGate
    storage, _, _, _ = xfer

    class Client:
        exceptions = types.SimpleNamespace()

        def __init__(self):
            self.tokens, self.markers, self.down = 0, set(), False

        def transact_write_items(self, TransactItems):
            if self.down:
                raise OSError("synthetic accounting-write outage")
            marker = TransactItems[0]["Put"]["Item"]["pk"]
            if marker not in self.markers:
                self.markers.add(marker)
                self.tokens += TransactItems[1]["Update"]["ExpressionAttributeValues"][":t"]

    client = Client()
    table = types.SimpleNamespace(name="cache-test", meta=types.SimpleNamespace(client=client))
    fake = types.SimpleNamespace(_tbl=table, budget_ok=lambda: client.tokens < 300, _today=lambda: "2027-01-15")
    monkeypatch.setenv("CACHE_TABLE", "cache-test")
    monkeypatch.setitem(sys.modules, "common.costguard", fake)
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(costguard=fake))
    ledger = offline_ledger(storage, cost_gate=CostGuardGate())
    job = run_job(ledger)
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                max_tokens=1000)["callId"]
    client.down = True
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(*ids(job), call, status="completed", usage={"inputTokens": 100, "outputTokens": 200})
    assert error.value.code == "accounting-pending" and client.tokens == 0
    client.down = outage == "persists"
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0, max_tokens=10)
    assert error.value.code == ("accounting-pending" if outage == "persists" else "daily-budget")
    stored = storage.get(OWNER, "job", job["id"])
    assert len(stored["calls"]) == 1
    assert client.tokens == (0 if outage == "persists" else 300)
    assert len(stored["accounting"]) == (1 if outage == "persists" else 0)


@pytest.mark.parametrize("change", ["withdrawal", "lease-expiry"])
def test_pure_output_chunk_retry_runs_the_shared_guard(xfer, change):
    """Review 6 finding 4: an already-written chunk repeated with its operation ID is fenced like a first write."""
    storage, ledger, now, _ = xfer
    job = run_job(ledger)
    ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
    data = b"z" * 10
    handle = open_out(ledger, job, data)
    operation = op_id()
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data), operation_id=operation)
    before = len(storage.table().transactions)
    ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data), operation_id=operation)
    assert len(storage.table().transactions) == before + 1          # the retry submitted its fence
    if change == "withdrawal":
        withdraw_admission(storage)
    else:
        now[0] = storage.get(OWNER, "job", job["id"])["attempt"]["leaseExpiresAt"]
    with pytest.raises(LedgerError) as error:
        ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data), operation_id=operation)
    assert error.value.code == ("authority-changed" if change == "withdrawal" else "stale-attempt")


@pytest.mark.parametrize("direction", ["read", "write"])
def test_chunk_retries_are_durably_bounded_by_read_retries(xfer, direction):
    """Review 6 finding 5: at most readRetries (2) retries per chunk index, counted on the handle."""
    storage, ledger, _, _ = xfer
    job = run_job(ledger)
    if direction == "read":
        handle = ledger.tool().open_input(*ids(job), operation_id=op_id(), decision_id="adm-1", stage="context")
        attempt = lambda operation: ledger.tool().read_chunk(*ids(job), handle["handleId"], 0,
                                                             operation_id=operation)
    else:
        data = b"w" * 10
        handle = open_out(ledger, job, data)
        attempt = lambda operation: ledger.tool().write_chunk(*ids(job), handle["handleId"], 0, b64(data),
                                                              operation_id=operation)
    operation = op_id()
    attempt(operation)
    for _ in range(PROFILE_DEFAULT["readRetries"]):
        attempt(operation)
    for again in (operation, op_id()):
        with pytest.raises(LedgerError) as error:
            attempt(again)
        assert error.value.code == "retry-exhausted"
    stored = storage.get(OWNER, "job", job["id"])["handles"][handle["handleId"]]
    assert stored["retries"] == {"0": PROFILE_DEFAULT["readRetries"]}
    assert storage.get(OWNER, "job", job["id"])["transferUsage"]["chunks"] == 1


# === PR #27 review round 7 regressions (prepared while Bash was unavailable) =========================

def listed_prior(storage, source_id="run-p"):
    data = b"prior round source bytes"
    key = storage.key_for(OWNER, "run", source_id, "rounds/1/source.zip")
    try:
        storage.put_blob_once(key, data, "application/zip")
    except Exception:
        pass
    prior = {"sourceKind": "run-round", "sourceId": source_id, "revision": "1", "key": key,
             "sha256": hashlib.sha256(data).hexdigest()}
    return prior, {"key": key, "sha256": prior["sha256"], "size": len(data)}


def revoke_round(storage, source_id="run-p"):
    record = storage.get(OWNER, "run", source_id)
    storage.put(OWNER, "run", {**record, "status": "revoked"}, record["version"])


@pytest.mark.parametrize("when", ["before-staging", "before-finish"])
def test_consumed_priors_are_revalidated_without_an_open_handle(xfer, when):
    """Review 7 finding 1: a listed prior consumed by a receipt is re-authorized, and its grant is a predicate."""
    storage, ledger, _, _ = xfer
    prior, entry = listed_prior(storage)
    if when == "before-staging":
        storage.put(OWNER, "run", {"id": "run-p", "status": "ready"})
        revoke_round(storage)
        with pytest.raises(LedgerError) as error:
            chain(xfer, "design.extract", manifest_extra={"priors": [prior]}, extra_inputs={"context": [entry]})
        assert error.value.code == "authority-changed"
        return
    job, result = chain(xfer, "design.extract", manifest_extra={"priors": [prior]},
                        extra_inputs={"context": [entry]})
    predicates = [item["ConditionCheck"]["Key"]["sk"] for item in storage.table().transactions[-1]["TransactItems"]
                  if "ConditionCheck" in item]
    assert "run#run-p" in predicates                      # the grant fenced the last protected write
    revoke_round(storage)
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "authority-changed"
    assert storage.get(OWNER, "job", job["id"])["status"] == "failed"


def pending_costguard(monkeypatch, cap=300):
    import types

    class Client:
        exceptions = types.SimpleNamespace()

        def __init__(self):
            self.tokens, self.markers, self.down = 0, set(), False

        def transact_write_items(self, TransactItems):
            if self.down:
                raise OSError("synthetic accounting-write outage")
            marker = TransactItems[0]["Put"]["Item"]["pk"]
            if marker not in self.markers:
                self.markers.add(marker)
                self.tokens += TransactItems[1]["Update"]["ExpressionAttributeValues"][":t"]

    client = Client()
    table = types.SimpleNamespace(name="cache-test", meta=types.SimpleNamespace(client=client))
    fake = types.SimpleNamespace(_tbl=table, budget_ok=lambda: client.tokens < cap, usage_today=lambda: client.tokens,
                                 DAILY_TOKEN_CAP=cap, _today=lambda: "2027-01-15")
    monkeypatch.setenv("CACHE_TABLE", "cache-test")
    monkeypatch.setitem(sys.modules, "common.costguard", fake)
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(costguard=fake))
    return client


@pytest.mark.parametrize("then", ["cancel", "retry-replacement"])
def test_pending_charges_of_other_jobs_count_against_the_daily_budget(xfer, monkeypatch, then):
    """Review 7 finding 2: an unrecorded charge of a cancelled/replaced job still blocks further paid calls."""
    from workspace.execution_ledger import CostGuardGate, QUOTA_OWNER, PENDING_CHARGES_ID
    storage, _, now, _ = xfer
    client = pending_costguard(monkeypatch)
    ledger = offline_ledger(storage, cost_gate=CostGuardGate())
    job = run_job(ledger)
    call = ledger.tool().intent(*ids(job), stage="generate", kind="model", min_remaining_ms=0,
                                max_tokens=1000)["callId"]
    client.down = True
    with pytest.raises(LedgerError) as error:
        ledger.tool().outcome(*ids(job), call, status="completed", usage={"inputTokens": 100, "outputTokens": 200})
    assert error.value.code == "accounting-pending"
    registry = storage.get(QUOTA_OWNER, "exec_quota", PENDING_CHARGES_ID)
    assert [row["tokens"] for row in registry["charges"].values()] == [300]
    ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    client.down = False                                   # the counter is readable; the charge is still unrecorded
    other = run_job(ledger, key="req-other")
    with pytest.raises(LedgerError) as error:
        ledger.tool().intent(*ids(other), stage="generate", kind="model", min_remaining_ms=0, max_tokens=10)
    assert error.value.code == "daily-budget"
    assert storage.get(OWNER, "job", other["id"])["calls"] == []
    if then == "retry-replacement":
        # Once the reconciler records the retained charge, the registry empties and the counter enforces the cap.
        ledger.reconciler().sweep(OWNER, job["id"])
        assert client.tokens == 300
        assert storage.get(QUOTA_OWNER, "exec_quota", PENDING_CHARGES_ID)["charges"] == {}
        with pytest.raises(LedgerError) as error:
            ledger.tool().intent(*ids(other), stage="generate", kind="model", min_remaining_ms=0, max_tokens=10)
        assert error.value.code == "daily-budget"


@pytest.mark.parametrize("case", ["failed-behavior", "failed-functional", "failed-accessibility",
                                  "missing-evidence", "blocking-findings", "verify-failed"])
def test_release_success_requires_successful_behavior_and_accessibility_evidence(xfer, case):
    """Review 7 finding 3: contradictory or incomplete Browser/verify results never complete a release."""
    storage, ledger, _, _ = xfer
    browser = dict(GOOD["browser"][1])
    verify = dict(GOOD["verify"][1])
    if case == "failed-behavior":
        browser["passed"] = False
    elif case == "failed-functional":
        browser["functionalStatus"] = "fail"
    elif case == "failed-accessibility":
        browser["accessibility"] = {"status": "fail", "violations": [{"id": "color-contrast"}]}
    elif case == "missing-evidence":
        browser = {"visualDiff": 0.0}
    elif case == "blocking-findings":
        browser["blockingFindings"] = ["unresolved requirement"]
    else:
        verify.update(passed=False, verdict="fail")
    job, result = chain(xfer, "design.release", results={"browser": ("ok", browser), "verify": ("ok", verify)})
    with pytest.raises(LedgerError) as error:
        finish(ledger, job, "succeeded", result)
    assert error.value.code == "status-inconsistent"
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"
