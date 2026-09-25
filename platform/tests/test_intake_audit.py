"""Metadata-only admission and transfer audit export (Task I7; §7.2)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_intake_admission import env, guide, internal_admitted  # noqa: F401,E402
from intake_support import api  # noqa: F401,E402
from intake import admission, audit  # noqa: E402


def ledger_job(env, decision, *, at):
    """A job in the B0 ledger record shape (`execution_ledger` job fields; that unit writes it)."""
    ref = admission.admission_ref(decision)
    owner = f"project:{env.pid}"
    return env.api.storage.put(owner, "job", {
        "id": "exec-" + "1" * 32, "task": "execution", "executionSchemaVersion": 1, "actor": "alice",
        "projectId": env.pid, "operation": "design.prd", "model": "model-a", "status": "completed",
        "admissions": [{**ref, "revision": str(ref["revision"])}],
        "attempts": [], "attempt": {"id": "att-1"},
        "calls": [{"callId": "call-1", "attemptId": "att-1", "stage": "generate", "model": "model-a",
                   "status": "succeeded", "at": at, "prompt": "SECRET-PROMPT-TEXT"}],
        "transfers": [{"handleId": "h-1", "attemptId": "att-1", "decisionId": decision["id"],
                       "artifactHash": ref["artifactHash"], "stage": "context", "at": at,
                       "text": "SECRET-TRANSFER-TEXT"}],
        "result": {"text": "SECRET-RESULT"}})


def test_export_contains_ids_and_hashes_for_decisions_and_ledger_transfers(env):
    decision = internal_admitted(env)
    now = env.api.storage.clock()
    ledger_job(env, decision, at=now)
    exported = audit.export(env.api.storage, f"project:{env.pid}", since=now - 3_600_000, until=now + 3_600_000)
    [entry] = exported["decisions"]
    assert entry == {"decisionId": decision["id"], "revision": decision["revision"],
                     "dataClass": "internal-non-sensitive", "policy": decision["policy"],
                     "reviewer": {"actor": "bob", "grantId": "grant-1", "grantRevision": 1},
                     "derivativeHash": decision["derivation"]["derivativeHash"], "at": decision["updatedAt"]}
    transfers = exported["transfers"]
    assert {"executionId": "exec-" + "1" * 32, "attemptId": "att-1", "decisionId": decision["id"],
            "artifactHash": decision["derivation"]["derivativeHash"], "stage": "context", "model": "model-a",
            "at": now} in transfers
    assert {"executionId": "exec-" + "1" * 32, "attemptId": "att-1", "decisionId": decision["id"],
            "artifactHash": decision["derivation"]["derivativeHash"], "stage": "generate", "model": "model-a",
            "at": now} in transfers


def test_export_contains_no_page_text_prompts_or_deny_list_terms(env):
    decision = internal_admitted(env)
    now = env.api.storage.clock()
    ledger_job(env, decision, at=now)
    serialized = json.dumps(audit.export(env.api.storage, f"project:{env.pid}", since=0, until=now + 1),
                            ensure_ascii=False)
    for secret in ("정기예금 안내", "연 2.0% 금리", "고객사 A", env.term, "SECRET-PROMPT-TEXT",
                   "SECRET-TRANSFER-TEXT", "SECRET-RESULT"):
        assert secret not in serialized


def test_export_window_and_non_admitted_decisions(env):
    decision = internal_admitted(env)
    pending = admission.request(env.api, env.scope(), guide(env, text="Second synthetic guide.\n", request="second"),
                                data_class="internal-non-sensitive")
    assert pending["status"] == "pending-review"
    now = env.api.storage.clock()
    owner = f"project:{env.pid}"
    assert [e["decisionId"] for e in audit.export(env.api.storage, owner, since=0, until=now + 1)["decisions"]] == [
        decision["id"]]
    assert audit.export(env.api.storage, owner, since=0, until=1) == {"decisions": [], "transfers": []}
    with pytest.raises(ValueError):
        audit.export(env.api.storage, owner, since=10, until=5)
