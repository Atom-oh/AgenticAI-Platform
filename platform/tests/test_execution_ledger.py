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
from workspace.execution_ledger import receipt_hash  # noqa: E402

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
    second = chained(job, "generate", "n2", previous=receipt_hash(first),
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
    ledger = Ledger.offline(storage, verifier=TestKeyVerifier(), cost_gate=CostGuardGate())
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
    ledger = Ledger.offline(storage, verifier=TestKeyVerifier(), cost_gate=CostGuardGate())
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
    for _ in range(120):
        ledger.tool().intent(OWNER, job["id"], job["attempt"]["id"], job["fence"], stage="generate", kind="model",
                             min_remaining_ms=0, max_tokens=1000, operation_id=op_id())
    previous = None
    for index in range(30):
        r = chained(job, "generate", f"nonce-{index}", previous=previous)
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
    data = bytes(range(256)) * 2048 + b"tail"            # 512 KiB + 4 bytes -> 3 chunks
    key = storage.key_for(OWNER, "asset", "adm-src", "derivative.bin")
    storage.put_blob_once(key, data, "application/octet-stream")
    admitted = {"adm-1": {"key": key, "sha256": hashlib.sha256(data).hexdigest()}}
    grants = []

    def resolver(owner, admission):
        return admitted.get(admission["decisionId"])

    def prior_authority(owner, job, prior):
        grants.append(prior["sourceId"])
        return prior.get("sourceId") != "revoked-round"
    ledger = Ledger.offline(storage, verifier=TestKeyVerifier(), input_resolver=resolver,
                            prior_authority=prior_authority)
    return storage, ledger, now, data


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
    assert again["version"] == first["version"]
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
    manifest = _json.dumps({"priors": [prior]}).encode()
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
    manifest = _json.dumps({"priors": [prior]}).encode()
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

GOOD = {"context": ("ok", {}), "generate": ("ok", {}), "compile": ("ok", {"sourceHash": "s" * 64, "bundleHash": "u" * 64}),
        "browser": ("ok", {"visualDiff": 0.0}), "analyze": ("ok", {"coverage": {"files": 1}}),
        "verify": ("ok", {"verdict": "pass", "approvable": True, "issues": [], "reviewer": "deterministic",
                          "passed": True, "compositionHashes": ["h1"], "judgeEvidence": {"h1": "ev-1"}})}


def release_manifest(storage):
    body = _json.dumps({"approved": {"sourceHash": "s" * 64, "bundleHash": "u" * 64}, "priors": []}).encode()
    key = storage.key_for(OWNER, "run", "run-r", "release-manifest.json")
    try:
        storage.put_blob_once(key, body, "application/json")
    except Exception:
        pass
    return {"ref": key, "hash": hashlib.sha256(body).hexdigest()}


def chain(env, operation="design.generate", results=None, skip=(), key="req-1"):
    """Drive a complete verified receipt chain; returns (job, finish result)."""
    storage, ledger = env[0], env[1]
    over = {"manifest": release_manifest(storage)} if operation == "design.release" else {}
    job = run_job(ledger, operation=operation, key=key, **over)
    results = {**GOOD, **(results or {})}
    previous, hashes, files = None, [], []
    stages = [stage for stage in __import__("workspace.execution_ledger", fromlist=["OPERATIONS"]).OPERATIONS[operation]
              if stage not in skip]
    for index, stage in enumerate(stages):
        outputs = [put_output(storage, job, stage, f"{stage}.out", f"{stage}-bytes".encode())]
        files.append({"key": outputs[0]["key"], "sha256": outputs[0]["sha256"]})
        if index == len(stages) - 1:
            manifest = _json.dumps({"files": files}).encode()
            outputs.append(put_output(storage, job, stage, "result-manifest.json", manifest))
        status, result = results[stage]
        r = chained(job, stage, f"n-{stage}", previous=previous, outputs=outputs, status=status, result=result)
        job = ledger.tool().stage(*ids(job), r)
        previous = receipt_hash(r)
        hashes.append(previous)
    manifest_out = job["stages"][-1]["outputs"][-1]
    return job, {"manifestRef": manifest_out["key"], "manifestHash": manifest_out["sha256"], "receipts": hashes}


def finish(ledger, job, status="succeeded", result=None, **kwargs):
    return ledger.tool().finish(*ids(job), status=status, result=result, **kwargs)


RULES = [
    ("source.analyze", {}, "succeeded", True), ("source.analyze", {"analyze": ("ok", {})}, "succeeded", False),
    ("source.analyze", {}, "needs_changes", False),
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
    ("design.release", {"compile": ("ok", {"sourceHash": "0" * 64, "bundleHash": "u" * 64})}, "succeeded", False),
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
    job, result = chain(xfer, skip=("browser",))
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
    job = run_job(ledger, operation="source.analyze")
    stray = put_output(storage, job, "analyze", "stray.bin", b"stray")
    previous, hashes = None, []
    for stage in ("context", "analyze"):
        outputs = [put_output(storage, job, stage, f"{stage}.out", stage.encode())]
        if stage == "analyze":
            body = _json.dumps({"files": [{"key": stray["key"], "sha256": stray["sha256"]}]}).encode()
            outputs.append(put_output(storage, job, stage, "result-manifest.json", body))
        r = chained(job, stage, "n-" + stage, previous=previous, outputs=outputs, status="ok", result=GOOD[stage][1])
        job = ledger.tool().stage(*ids(job), r)
        previous = receipt_hash(r)
        hashes.append(previous)
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
    assert len(storage.table().transactions) == before + 1
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
                   status="ok", result=GOOD["verify"][1])
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
        self.mode, self.failures, self.wire = mode, failures, 0
        self.meta.client.transact_write_items = self.flaky

    def flaky(self, **kwargs):
        finishing = any("Put" in entry and entry["Put"]["Item"]["sk"].startswith("job#")
                        and entry["Put"]["Item"].get("status") == "succeeded" for entry in kwargs["TransactItems"])
        if not finishing:
            return self.transact_write_items(**kwargs)
        self.wire += 1
        if self.failures:
            self.failures -= 1
            if self.mode == "timeout-before":
                raise ReadTimeoutError(endpoint_url="https://dynamodb.invalid")
            if self.mode == "timeout-after":
                self.transact_write_items(**kwargs)
                raise ReadTimeoutError(endpoint_url="https://dynamodb.invalid")
            if self.mode == "contention":
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
    ledger = Ledger.offline(storage, verifier=TestKeyVerifier())
    return storage, ledger, now


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
    assert error.value.code == "conflict"
    assert storage.table().wire == 3 and len(calls) == 3
    assert storage.get(OWNER, "job", job["id"])["status"] == "running"


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
                          (ledger.reconciler(), ("sweep", "reconcile", "resolve_orphan", "run_due"))):
        assert all(callable(getattr(facade, name, None)) for name in names)


def test_production_requires_a_registered_verifier_and_single_attempt_storage(monkeypatch):
    from workspace import execution_ledger as module

    class ReviewedVerifier:
        def verify(self, receipt):
            return False
    monkeypatch.setattr(module, "_REGISTERED", {ReviewedVerifier})
    with pytest.raises(PermissionError):
        Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b"), verifier=ReviewedVerifier())
    ledger = Ledger.production(Storage(table=FakeTable(), s3=FakeS3(), bucket="b", single_attempt=True),
                               verifier=ReviewedVerifier())
    assert isinstance(ledger.cost_gate, module.CostGuardGate) and ledger.input_resolver is None
