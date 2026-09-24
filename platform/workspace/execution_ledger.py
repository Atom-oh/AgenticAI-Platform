"""Single logical transition writer for agentcore-execution jobs (AGENTCORE_CONTRACT platform-execution/1)."""
from __future__ import annotations

import json

from workspace import storage as _storage
from workspace.storage import DUE_OWNER, EXECUTION_JOB_PREFIX, LINKED_KINDS, Conflict, is_reserved

TASK, SCHEMA_VERSION = "agentcore-execution", 1
_WRITER = _storage._ledger_writer()

# Legacy statuses the orphan resolver never changes: terminal, approved or not yet queued.
_SETTLED = frozenset({"completed", "failed", "cancelled", "FAILED", "DRAFT", "APPROVED", "DEPRECATED",
                      "ready", "committed", "approved", "needs_changes"})
_ORPHAN_MESSAGE = "작업 기록이 없어 종료했습니다. 새 요청으로 다시 실행하세요."


def _seed_for_tests(storage, owner, kind, record, expected_version=None):
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise PermissionError("test seeding is offline-only")
    return storage.put(owner, kind, record, expected_version, _writer=_WRITER)


# --- orphan resolution (IAM-only reconciler; review rounds 37-38, BE1/BF2) --------------------------

def _execution_references(storage, owner, identifier):
    """Fail closed: any execution job in the partition that mentions the record keeps it out of legacy repair."""
    cursor = None
    while True:
        page = storage.list_page(owner, "job", 100, cursor, prefix=EXECUTION_JOB_PREFIX)
        for job in page["items"]:
            if json.dumps(job, ensure_ascii=False, default=str).find(json.dumps(identifier)) >= 0:
                return True
        cursor = page.get("cursor")
        if not cursor:
            return False


def _orphan_failure(kind):
    update = {"status": "FAILED" if kind == "wb_skill" else "failed", "error": _ORPHAN_MESSAGE,
              "errorCode": "orphan-legacy-job"}
    if kind == "docrevision":
        update["parseStatus"] = "failed"
    if kind == "asset":
        update.update(uploadStatus="failed", parseStatus="failed")
    return update


def _resolve_orphan(storage, due):
    """Recheck a reported record and fail it only while it is still an unmarked legacy orphan.

    The artifact CAS, the due-entry tombstone and the linked job's absence are one transaction:
    a job recreated before submission aborts it and nothing changes.
    """
    owner, ref = due["targetOwner"], due.get("ref") or {}
    kind, identifier = ref.get("kind"), ref.get("id")
    done = {"owner": DUE_OWNER, "kind": "exec_due", "item": {**due, "status": "done"},
            "expected_version": due["version"]}
    artifact = storage.get(owner, kind, identifier) if kind in LINKED_KINDS else None
    job_id = (artifact or {}).get("jobId")
    orphan = bool(artifact) and not is_reserved(artifact) and artifact.get("status") not in _SETTLED
    orphan = orphan and isinstance(job_id, str) and bool(_storage._ID.fullmatch(job_id))
    orphan = orphan and not job_id.startswith(EXECUTION_JOB_PREFIX)
    orphan = orphan and storage.get(owner, "job", job_id) is None
    orphan = orphan and not _execution_references(storage, owner, identifier)
    if not orphan:
        storage.put_many([done], retry_conflicts=False, _writer=_WRITER)
        return None
    item = {**artifact, **_orphan_failure(kind)}
    return storage.put_many(
        [{"owner": owner, "kind": kind, "item": item, "expected_version": artifact["version"]}, done],
        [{"owner": owner, "kind": "job", "id": job_id, "version": None}],
        retry_conflicts=False, _writer=_WRITER)[0]


def _run_due(storage, *, limit=100):
    """One scheduled reconciler pass over due entries; no ids are supplied by any caller."""
    now, handled, cursor = storage.clock(), 0, None
    while handled < limit:
        page = storage.list_page(DUE_OWNER, "exec_due", 100, cursor)
        for due in page["items"]:
            if due.get("dueAt", now + 1) > now:
                return handled
            if due.get("status") != "pending" or due.get("type") != "report":
                continue
            try:
                _resolve_orphan(storage, due)
            except Conflict:
                continue        # the entry stays pending for the next pass
            handled += 1
            if handled >= limit:
                return handled
        cursor = page.get("cursor")
        if not cursor:
            return handled
    return handled
