# B0 — Execution Ledger and Legacy Writer Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the offline protocol foundation of `platform-execution/1`. It has two parts:
- `workspace/execution_ledger.py`, the single logical transition writer for `agentcore-execution` jobs
- guards that make every legacy job/artifact writer refuse to mutate new-schema records

**Architecture:**
- Workspace job store: new executions are `job` records with the immutable discriminator `task="agentcore-execution", executionSchemaVersion=1`.
- Single chokepoint: one writer check inside `Storage._prepare` rejects any non-ledger write to a reserved record. The only exemption is a module-private writer token held by `execution_ledger`.
- DB-level fence: a conditional `attribute_not_exists(executionSchemaVersion)` is added to legacy updates.
- Ledger responsibilities:
  - admission idempotency
  - fenced attempts with leases and heartbeats
  - per-call intent and budget reservation
  - receipt verification behind a registered-verifier interface
  - watchdog recovery
  - coupled completion through `Storage.put_many`

The B0 scope is offline only. No AWS resource is created, and nothing is wired to an API route or dispatcher.

**Tech Stack:** Python 3.12, pytest, and the existing `tests/test_workspace_storage.py` fakes, which are extended here.

**Roadmap:** [2026-09-24-design-poc-roadmap.md](2026-09-24-design-poc-roadmap.md), unit 1. This plan fixes Codex finding #1.

**Owning contract:** `platform/workspace/AGENTCORE_CONTRACT.md` `platform-execution/1`, which covers:
- L459-470: ledger, callers and roles
- L472-490: discriminator and legacy guards
- L509-537: invocation fields, profile and states
- L539-581: retry, recovery and verifiers

**Callers after C:**
- authenticated API: admit, cancel, retry
- IAM-only dispatcher: allocate
- ontology Lambda facade: claim, heartbeat, intent, stage, transfer, finish, fail
- IAM-only watchdog/reconciler: sweep, reconcile

In B0 they are test harness callers only.

**Acceptance cases:** RUN-01 to RUN-05, O portions (`platform/docs/ONTOLOGY_AGENTCORE_VALIDATION.md` L270-274). ROLL-02 defaults stay unchanged: nothing admits new-schema work in production.

**Exit:**
- RUN-01 to RUN-05 O-mode tests pass, importing the real legacy writers.
- A reviewed B-chain PR is merged.
- There is no cloud-readiness claim.
- A deployment-owner record of the deployed guard revision is required before any B1 shared-table probe. That step belongs to B1.

## Global Constraints

- Follow the roadmap global constraints.
- The ledger never imports boto3 clients beyond the existing `Storage` abstraction, and never imports a model client.
- A caller role is fixed when the trusted handler constructs its facade. No function takes a caller role from request, model or tool arguments.
- A malformed or unknown discriminator never falls back to legacy. The new path accepts only `executionSchemaVersion == 1` with `task == "agentcore-execution"` and otherwise rejects without mutation.
- A test verifier is constructible only in offline tests. Production construction rejects unregistered verifier types.
- Preserve Unit A's concurrent edits in `ontology_store.py`, `ontology_api.py` and `ontology_analysis.py`. This plan touches `ontology_jobs.py` for the guard only.
- From `platform/`, `python3 -m pytest tests/ -q` stays green after every task.

## Legacy writer inventory (baseline `e097cee`)

Every row gets a guard test in Task 3 that imports the real function.

| # | Writer | Location | Mutation |
|---|---|---|---|
| W1 | `Storage.claim_job` | `workspace/storage.py:303-310` | job queued→running |
| W2 | `Worker._update` | `workspace/worker.py:227-236` | Merge write used by W3 and the progress writers |
| W3 | `Worker.handle` completion/failure | `workspace/worker.py:283,296,320,324,328,332,335` | job completed/failed; linked release/gitexport/asset/run failed |
| W4 | Progress writers | `workspace/worker.py:357,528,606,644`, `git_service.py:154`, `releases.py:110`, `react_generation.py:112,138` | `progress` via `_update` |
| W5 | `_mark_failed` | `workbench/worker.py:55-100`; callers `workbench/worker.py:47,51`, `workbench/service.py:251` | `wb_artifact`/`wb_batch`/`wb_skill` failed |
| W6 | Analysis-read repair | `workspace/ontology_jobs.py:14-40` (callers `ontology_api.py:142,153`, `ontology_jobs.py:55`) | job failed, and `_mark_failed` with a synthesized job when the job is missing |
| W7 | Analysis completion | `workspace/ontology_jobs.py:159-176` | artifact and job completed, via `publish_candidate(_completion_writes=…)` |
| W8 | Stale-job expiry | `workspace/http.py:259-285` (callers `http.py:385,389`, `ontology_jobs.py:20`) | job failed `job-timeout` |
| W9 | Dispatch failure / retry | `workspace/http.py:511-541`, `544-564` | job failed `dispatch-failed` / requeued; `_JOB_TARGETS` target |
| W10 | Job creation | `workspace/http.py:566-576` `_new_job` | job created |
| W11 | Document jobs | `documents/jobs.py:9-53,63-78,81-122`; `documents/intake.py:219,232`; `documents/analysis.py:168,186,308,362`; `documents/api.py:154,178` | Document job/target status |
| W12 | Skill completion | `workbench/skills.py:402` | `wb_artifact` completed |

**Out of scope, with reason:** `api/handlers/studio.py:80` writes `studio.store.StudioStore` (`studio/store.py:59-70`). That is a separate table (`STUDIO_TABLE`) that never holds workspace jobs. Task 3 adds a test asserting the table names differ, so a future merge cannot silently bring it into scope.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/workspace/storage.py` (modify) | `ReservedRecord`, discriminator and linked-job checks in `_prepare`, DB fence condition, ledger writer token, kinds `exec_report`/`exec_request`/`exec_quota`, `claim_job` guard |
| `platform/workspace/execution_ledger.py` (create) | Discriminator constants, profiles, `Ledger` transitions, verifier registry, misroute reports |
| `platform/workspace/worker.py`, `workspace/http.py`, `workspace/ontology_jobs.py`, `workbench/worker.py`, `workbench/service.py`, `documents/jobs.py` (modify) | Catch `ReservedRecord` → report and return without mutation; `_new_job` rejects reserved tasks |
| `platform/tests/test_workspace_storage.py` (modify) | `FakeTable` transactional evaluator supports `AND`, `<>` and parenthesized builder output |
| `platform/tests/execution_fakes.py` (create) | Offline-only `TestKeyVerifier`, receipt builder |
| `platform/tests/test_execution_guards.py` (create) | RUN-01 legacy inventory guards (W1–W12) |
| `platform/tests/test_execution_ledger.py` (create) | RUN-01 to RUN-05 ledger behavior |
| `platform/workspace/AGENTCORE_CONTRACT.md` (modify) | Concrete record fields for `platform-execution/1` |
| `platform/docs/CONTRACTS.md` (modify) | Storage kind `exec_report`; reserved-job behavior of legacy routes |

---

### Task 1: FakeTable transactional conditions

**Files:**
- Modify: `platform/tests/test_workspace_storage.py:89-118` (`FakeTable.transact_write_items`)
- Test: same file

**Interfaces:** Produces `_eval(expression, names, values, current) -> bool` inside the fake. It supports `attribute_not_exists(#x)`, `attribute_exists(#x)`, `#x = :v`, `#x <> :v`, `AND`, `OR` and balanced parentheses, which are the forms `ConditionExpressionBuilder` emits.

- [ ] **Step 1: Write the failing test**

```python
def test_fake_transaction_evaluates_compound_builder_conditions(storage):
    from boto3.dynamodb.conditions import Attr
    from workspace.storage import Conflict
    storage.put("alice", "job", {"id": "j1", "task": "run"})
    table = storage.table()
    cond = Attr("version").eq(1) & Attr("executionSchemaVersion").not_exists()
    from boto3.dynamodb.conditions import ConditionExpressionBuilder
    built = ConditionExpressionBuilder().build_expression(cond)
    item = dict(table.items[next(iter(table.items))], version=2)
    put = {"TableName": table.name, "Item": item, "ConditionExpression": built.condition_expression,
           "ExpressionAttributeNames": built.attribute_name_placeholders,
           "ExpressionAttributeValues": built.attribute_value_placeholders}
    table.transact_write_items(TransactItems=[{"Put": put}])          # passes
    reserved = dict(item, executionSchemaVersion=1, version=3)
    table.items[next(iter(table.items))] = reserved
    put["Item"] = dict(reserved, version=4)
    put["ExpressionAttributeValues"] = {":v0": 3}
    with pytest.raises(TransactionFailure):
        table.transact_write_items(TransactItems=[{"Put": put}])
```

- [ ] **Step 2: Run to verify failure**

Run: `cd platform && python3 -m pytest tests/test_workspace_storage.py -q -k compound_builder`
Expected: FAIL with `ValueError` from `expression.split(" = ")`.

- [ ] **Step 3: Implement the evaluator**

```python
import re as _re

_TOKEN = _re.compile(r"\s*(\(|\)|AND\b|OR\b|attribute_not_exists\(#\w+\)|attribute_exists\(#\w+\)|#\w+\s*(?:=|<>)\s*:\w+)")


def _eval(expression, names, values, current):
    tokens, pos = [], 0
    while pos < len(expression):
        match = _TOKEN.match(expression, pos)
        if not match:
            raise AssertionError(f"Unsupported fake transaction condition: {expression}")
        tokens.append(match.group(1).strip())
        pos = match.end()

    def atom(tok):
        if tok.startswith("attribute_not_exists("):
            return names[tok[21:-1]] not in current
        if tok.startswith("attribute_exists("):
            return names[tok[17:-1]] in current
        op = "<>" if "<>" in tok else "="
        name, value = (part.strip() for part in tok.split(op))
        equal = current.get(names[name]) == values[value]
        return equal if op == "=" else not equal

    def parse_or(i):
        left, i = parse_and(i)
        while i < len(tokens) and tokens[i] == "OR":
            right, i = parse_and(i + 1)
            left = left or right
        return left, i

    def parse_and(i):
        left, i = parse_term(i)
        while i < len(tokens) and tokens[i] == "AND":
            right, i = parse_term(i + 1)
            left = left and right
        return left, i

    def parse_term(i):
        if tokens[i] == "(":
            value, i = parse_or(i + 1)
            assert tokens[i] == ")"
            return value, i + 1
        return atom(tokens[i]), i + 1

    result, end = parse_or(0)
    assert end == len(tokens)
    return result
```

Then replace the `if expression.startswith(...)` / `else` block with:

```python
                matches = _eval(expression, names, entry.get("ExpressionAttributeValues", {}), current)
```

Also update `condition_matches` so that `put_item` handles `<>`:

```python
    if op == "<>":
        return item.get(name) != values[1]
```

- [ ] **Step 4: Run the storage and transaction suites**

Run: `python3 -m pytest tests/test_workspace_storage.py tests/test_workspace_transaction_retry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add platform/tests/test_workspace_storage.py
git commit -m "test(storage): evaluate compound transactional conditions in the fake table"
```

### Task 2: Reserved-record chokepoint in `Storage`

**Files:**
- Modify: `platform/workspace/storage.py:20-24` (KINDS += `exec_report`), `:125-161` (`_prepare`), `:163-170` (`put`), `:172-246` (`put_many`), `:303-310` (`claim_job`)
- Test: `platform/tests/test_execution_guards.py` (create; storage-level cases)

**Interfaces:**
- `class ReservedRecord(Conflict)` carries `.kind`, `.id` and `.reason`. It subclasses `Conflict`, so existing `except Conflict` paths never write when they catch it.
- `RESERVED_TASKS = frozenset({"agentcore-execution"})`
- `def is_reserved(record: dict) -> bool` is true when any of these holds:
  - `record.get("task") in RESERVED_TASKS`
  - `"executionSchemaVersion" in record`
  - `"executionId" in record`
- `_LEDGER_WRITER = object()`, module-private. It is exported only to `execution_ledger` through `storage._ledger_writer()`. That function asserts the caller's module name is `workspace.execution_ledger`, by `sys._getframe(1).f_globals["__name__"]`. This is a code-review boundary; production IAM separation is B2.
- `put(owner, kind, item, expected_version=None, *, _writer=None)` and `put_many(writes, checks=None, *, retry_conflicts=True, before_attempt=None, _writer=None)`.
- `_prepare` raises `ReservedRecord` before any I/O when `_writer is not _LEDGER_WRITER` and any of these holds:
  - `is_reserved(item)`
  - the pre-read `previous` exists and `is_reserved(previous)`
- The non-ledger update condition is `Attr("version").eq(v) & Attr("executionSchemaVersion").not_exists() & Attr("executionId").not_exists()`.
- On a `ReservedRecord`, `put`/`put_many` write a metadata-only report before re-raising. `_report(owner, kind, id, reason)` writes an `exec_report` record with id `schema.identity("exec-report", kind, id, reason)`. Its fields are `{kind, recordId, reason, count, firstAt, lastAt}`: no payload and no source text. It uses optimistic merge with at most 3 attempts, and failures are swallowed so the refusal still propagates.
- `claim_job` returns `None` for a reserved job and records a report. Its CAS path is otherwise unchanged.
- **Human-authorized writer (review round 3, F1).** Human approval, release creation and Git export legitimately update runs, releases and gitexports that are linked to execution jobs, through `_run_approve → _approval_put → put_many` (`http.py:783`) and `git_service.create_export` (`git_service.py:116`). `storage._human_writer()` returns a second module-private token, `_HUMAN_WRITER`. It checks the caller module name ∈ `{"workspace.http", "workspace.releases", "workspace.git_service", "workspace.design_api"}`.
  - The token is accepted **only** for kinds `run`, `release`, `gitexport` and `design`, and **never** for `job`, `wb_*` or `doc*` kinds. That keeps legacy repair excluded.
  - The human token bypasses the marker checks **only** for those four kinds, and only for writes that do not move `status` into `failed`, `needs_changes` or `completed`. Execution outcomes remain ledger-only. The token's DB condition is plain version CAS.
  - The approval and export helpers pass `_writer=storage._human_writer()`.
  - Tests:
    - `_run_approve` on a design-linked run succeeds.
    - `_mark_failed` on the same run is still refused.
    - A human write that sets `status: "failed"` on a linked run is refused.
    - Obtaining the token from another module raises `PermissionError`.
- **Linked-job fence (review round 2, N2).** For a non-ledger write to any linked kind (`wb_artifact`, `wb_batch`, `wb_skill`, `docrevision`, `docanalysis`, `release`, `gitexport`, `run`, `asset`), `_prepare` reads the linkage from the **stored** record, falling back to `item.jobId` when there is no stored record. It then reads the **stored** job named by that linkage. The write raises `ReservedRecord(kind, id, "linked-reserved-job")` when any of the following holds:
  - the stored job is reserved
  - the job is missing and its id has the new-ledger prefix `exec-`
  - the stored `jobId` differs from `item.jobId`, which is a mismatched linkage

  A caller-supplied job dict is never trusted: `_mark_failed` passes one, and the guard ignores it. An unmarked artifact linked to a reserved job is therefore refused, and the refusal is reported for the IAM-only reconciler.
  - A **missing** linked job, with any id format, is **unknown linkage**. Legacy code does not fail or complete that artifact. The write is refused with `ReservedRecord(kind, id, "unknown-linkage")` and reported for the IAM-only reconciler, per the contract's "mismatched/unknown linkage is reported … legacy code does not fail or complete that artifact" (`AGENTCORE_CONTRACT.md:489-490`; review round 6, N2).
  - This **changes** the existing legacy orphan-repair behavior (`ontology_jobs.py:34-40`, `workbench/worker.py:89`). The existing test `tests/test_ontology_analysis.py` "orphaned artifact" (~L492) is updated to expect no mutation plus an `exec_report`.
  - The reconciler (B2 S7) resolves such orphans: a legacy-format job id with no execution record is failed by the reconciler through the ledger writer, with reason `orphan-legacy-job`. The PR states this behavior change.
  - **Report delivery** (review round 37, BE1). `_report` also writes an `exec_due` entry of kind `report`, with `{targetOwner: owner, ref: {kind, id}}`, in the same `put`, so the reconciler discovers it without known ids.
  - The reconciler operation `resolve_orphan(targetOwner, kind, id)` **rechecks** before acting. It fails the artifact only when all of these still hold:
    - the stored artifact is unmarked
    - its linked job is still missing
    - the id is not an `exec-` id
    - no execution record references it
  - It then writes through the ledger writer, in one `put_many`, with the artifact's version CAS, the `done` tombstone of the entry, **and** a transactional absence check `{owner, kind: "job", id: jobId, version: None}` (review round 38, BF2). This is the same pattern `documents/jobs.py:51` uses and `storage.py:221-223` supports. Every other record that established orphanhood is included as a version check too. A job recreated just before submission (for example by `_new_job`, `http.py:565`) makes the transaction fail, and nothing changes. Otherwise it only marks the entry done.
  - Test: recreate the job between the reconciler's reads and its submission (through `FakeTable.before_transaction`) → `Conflict`, with the artifact still processing, the new job queued and the due entry still pending.
  - Test: `_mark_failed` on an orphan → a report and a due entry → an ordinary scheduler event with no supplied ids → the artifact is failed with `orphan-legacy-job`. An artifact whose job reappeared is left untouched.
  - New-ledger artifacts are always marked, so they are refused by the item check before linkage is considered.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_execution_guards.py
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


def seed(storage, record, kind="job"):
    from workspace.execution_ledger import _seed_for_tests
    return _seed_for_tests(storage, "project:p1", kind, record)


def reports(storage):
    return storage.list("project:p1", "exec_report")


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


def test_ledger_writer_token_is_not_obtainable_outside_the_ledger():
    from workspace import storage as module
    with pytest.raises(PermissionError):
        module._ledger_writer()
```

The `test_db_fence…` test mutates a raw fake item and deliberately keeps the same version, which is only possible in the fake. It shows that the DB condition, not only the pre-read, rejects the write.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_execution_guards.py -q`
Expected: FAIL with `ImportError: ReservedRecord`.

- [ ] **Step 3: Implement in `storage.py`**

```python
# after class Conflict
class ReservedRecord(Conflict):
    """A legacy writer attempted to mutate a new-execution record (platform-execution/1)."""

    def __init__(self, kind, identifier, reason):
        super().__init__("The resource belongs to a separately governed execution")
        self.kind, self.id, self.reason = kind, identifier, reason


RESERVED_TASKS = frozenset({"agentcore-execution"})
_LEDGER_WRITER = object()


def is_reserved(record):
    return isinstance(record, dict) and (record.get("task") in RESERVED_TASKS
                                         or "executionSchemaVersion" in record or "executionId" in record)


def _ledger_writer():
    import sys
    if sys._getframe(1).f_globals.get("__name__") != "workspace.execution_ledger":
        raise PermissionError("Only the execution ledger may write execution records")
    return _LEDGER_WRITER
```

Add `"exec_report"`, `"exec_request"` and `"exec_quota"` to `KINDS`.

In `_prepare`, add the keyword-only parameter `writer=None`, and add this block after `previous` is read:

```python
        ledger = writer is _LEDGER_WRITER
        # Human approval/export writes are evaluated BEFORE the reserved-record refusal (review round 4, F1):
        # a design-linked run/release may itself carry execution markers written by the ledger completion.
        human = (writer is _HUMAN_WRITER and kind in {"run", "release", "gitexport", "design"}
                 and not (previous and previous.get("status") != item.get("status")
                          and item.get("status") in {"failed", "needs_changes", "completed"}))
        if not ledger and not human and (is_reserved(item) or is_reserved(previous)):
            raise ReservedRecord(kind, identifier, "reserved-item" if is_reserved(item) else "reserved-record")
        if not ledger and not human and kind in LINKED_KINDS:
            stored_link = (previous or {}).get("jobId")
            link = stored_link if stored_link is not None else item.get("jobId")
            if stored_link is not None and item.get("jobId") not in (None, stored_link):
                raise ReservedRecord(kind, identifier, "linkage-mismatch")
            if link is not None:
                linked = self.get(owner, "job", link)
                if is_reserved(linked):
                    raise ReservedRecord(kind, identifier, "linked-reserved-job")
                # Unknown linkage is refused only for REPAIR of an existing linked record (review round 7, AA1):
                # the stored record already names this job, the job is not proposed in the same transaction,
                # and the write changes status. Creation and first linkage (asset finalize, document finalize,
                # workbench queueing) are unaffected.
                if (linked is None and stored_link == link and link not in proposed_jobs
                        and previous.get("status") != item.get("status")):
                    raise ReservedRecord(kind, identifier, "unknown-linkage")
```

with the module constant:

```python
LINKED_KINDS = frozenset({"wb_artifact", "wb_batch", "wb_skill", "docrevision", "docanalysis",
                          "release", "gitexport", "run", "asset"})
```

`proposed_jobs` is the set of job ids written in the same `put_many` call. `put_many` computes it before preparing each write and passes it to `_prepare`; for `put` it is empty. The extra job read occurs only for linked kinds that carry a `jobId`.
- Tests for creation and first linkage, run through the real paths:
  - asset finalization writes the linkage before creating its job (`http.py:607-613`)
  - document finalization creates target and job together (`documents/api.py:176-178`)
  - workbench queueing
  - each succeeds unchanged
- The repair negatives stay refused. The job's reservation state is immutable, because the discriminator never changes after creation, so a read before the conditional write is sufficient for this fence. The DB-level condition covers the record itself.

Replace the condition line with:

```python
        if expected_version:
            condition = Attr("version").eq(expected_version)
            if not ledger and not human:
                condition = condition & Attr("executionSchemaVersion").not_exists() & Attr("executionId").not_exists()
        else:
            condition = Attr("pk").not_exists()
```

In `put`, add `*, _writer=None`, pass it through, and report on refusal:

```python
    def put(self, owner, kind, item, expected_version=None, *, _writer=None):
        try:
            data, keys, condition = self._prepare(owner, kind, item, expected_version, self.clock(), writer=_writer)
        except ReservedRecord as refused:
            self._report(owner, refused)
            raise
        ...
```

In `put_many`, do the same for each write's `_prepare` call (report, then re-raise). A DB-level condition failure on a non-ledger write still raises the existing `Conflict`. The next read by that caller sees the reserved record and raises `ReservedRecord`, which reports it.

```python
    def _report(self, owner, refused):
        from workspace import ontology_schema as schema
        identifier = schema.identity("exec-report", refused.kind, str(refused.id), refused.reason)
        for _ in range(3):
            try:
                current = self.get(owner, "exec_report", identifier)
                now = self.clock()
                record = {"id": identifier, "kind": refused.kind, "recordId": str(refused.id)[:128],
                          "reason": refused.reason, "count": (current or {}).get("count", 0) + 1,
                          "firstAt": (current or {}).get("firstAt", now), "lastAt": now}
                self.put(owner, "exec_report", record, current["version"] if current else None)
                return
            except Exception:  # noqa: BLE001 — reporting must never mask the refusal
                continue
```

`claim_job`:

```python
    def claim_job(self, owner, id):
        job = self.get(owner, "job", id)
        if not job or job.get("status") != "queued":
            return None
        try:
            return self.put(owner, "job", {**job, "status": "running"}, expected_version=job["version"])
        except Conflict:
            return None
```

This body is unchanged. `put` raises `ReservedRecord`, which is a `Conflict`, after reporting.

- [ ] **Step 4: Add `_seed_for_tests` to a new `execution_ledger.py` stub**

```python
"""Single logical transition writer for agentcore-execution jobs (AGENTCORE_CONTRACT platform-execution/1)."""
from __future__ import annotations

from workspace import storage as _storage

TASK, SCHEMA_VERSION = "agentcore-execution", 1
_WRITER = _storage._ledger_writer()


def _seed_for_tests(storage, owner, kind, record):
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise PermissionError("test seeding is offline-only")
    return storage.put(owner, kind, record, _writer=_WRITER)
```

`_WRITER` is obtained at import time. `_ledger_writer` inspects the importing frame, whose module name is `workspace.execution_ledger`.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS. No legacy record carries the new fields, so existing tests are unaffected.

- [ ] **Step 6: Commit**

```bash
git add platform/workspace/storage.py platform/workspace/execution_ledger.py platform/tests/test_execution_guards.py
git commit -m "feat(execution): refuse legacy mutation of reserved execution records at the storage chokepoint"
```

### Task 3: Guard every legacy writer (RUN-01 inventory)

**Files:**
- Modify:
  - `platform/workspace/http.py:566-576` (`_new_job` rejects reserved tasks)
  - `platform/workspace/worker.py:246-336` (reserved claim → `duplicate-or-unavailable`, no write)
  - `platform/workbench/worker.py:55-100` (`_mark_failed`: a marked artifact is refused)
  - `platform/workspace/ontology_jobs.py:14-40` (repair)
  - `platform/documents/jobs.py`
- Test: `platform/tests/test_execution_guards.py` (append)

**Interfaces:**
- Every legacy writer lets `ReservedRecord` propagate to its existing `except Conflict`/`except Exception` branches. The chokepoint has already reported it.
- The explicit changes:
  - `WorkspaceAPI._new_job` raises `HTTPError(400, "reserved-task", …)` for `task in RESERVED_TASKS`.
  - `_mark_failed` returns early without a write when `is_reserved(current)` or when the passed job `is_reserved`. A missing job never makes a marked artifact legacy.
  - `ontology_jobs.reconcile` returns the artifact unchanged when the artifact is reserved.

- [ ] **Step 1: Write the failing inventory tests**

Each test seeds a reserved job, a marked artifact, or both through `_seed_for_tests`. It calls the **real** legacy function and asserts all of the following:
1. no field of the reserved records changed (the version is equal)
2. an `exec_report` exists
3. the legacy function returns its normal "unavailable/failed" result without raising to the caller

```python
from test_workspace_http import FakeLambda, FakeRules  # noqa: E402
from workspace.http import WorkspaceAPI  # noqa: E402
from workspace.worker import Worker  # noqa: E402

MARKED = {"id": "art-1", "projectId": "p1", "status": "processing", "jobId": "exec-1",
          "executionSchemaVersion": 1, "executionId": "exec-1"}


def api_for(storage):
    return WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker", rules=FakeRules)


def unchanged(storage, kind, record):
    assert storage.get("project:p1", kind, record["id"])["version"] == record["version"]


def test_w1_w3_worker_handle_never_runs_or_fails_a_reserved_job(storage):
    job = seed(storage, dict(NEW))
    worker = Worker(storage=storage)
    assert worker.handle({"owner": "project:p1", "jobId": "exec-1"})["status"] == "duplicate-or-unavailable"
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


def test_w10_new_job_rejects_reserved_task(storage):
    from workspace.http import HTTPError
    with pytest.raises(HTTPError) as error:
        api_for(storage)._new_job("project:p1", "x1", "agentcore-execution", {}, "h" * 64)
    assert error.value.code == "reserved-task"


def test_w5_mark_failed_refuses_marked_artifact_even_without_a_job(storage):
    from workbench.worker import _mark_failed
    art = seed(storage, MARKED, kind="wb_artifact")
    _mark_failed(Worker(storage=storage), "project:p1",
                 {"id": "exec-1", "input": {"operation": "ontology-analyze", "artifactId": "art-1"}}, "job-timeout")
    unchanged(storage, "wb_artifact", art)


def test_w6_analysis_read_repair_leaves_marked_artifact_for_the_reconciler(storage, wb_context):
    from workspace import ontology_jobs
    art = seed(storage, {**MARKED, "jobInput": {"authorizationExpiresAt": 0}}, kind="wb_artifact")
    assert ontology_jobs.reconcile(wb_context(storage), art)["version"] == art["version"]
    unchanged(storage, "wb_artifact", art)
    assert reports(storage)
```

`wb_context` is a small fixture that builds the `ctx` object `ontology_jobs.reconcile` needs (`storage`, `owner`, `host`, `get`). It is built from `tests/test_workbench_core.py:21` `wb` and `tests/test_ontology_analysis.py` `context(wb)`; reuse those helpers directly.

Add further tests using the same pattern:
- W2/W4: `Worker._update` on a reserved job raises `Conflict` and changes nothing.
- W7: `ontology_jobs` completion with a reserved job input raises `Conflict` inside `publish_candidate` and publishes nothing. Check that the ontology generation is unchanged.
- W11: `documents.jobs.reconcile`, `expire_raw_job` and `fail_work` on a reserved job.
- W12: the `workbench/skills.py:402` completion against a marked artifact.
- Studio table separation: assert `studio.store.StudioStore` table env name != workspace `TABLE`, and that `api/handlers/studio.py` imports no `workspace.storage`.

If `FakeLambda` has no failure switch, add a `fail` attribute to `tests/test_workspace_http.py:23` `FakeLambda.invoke` that raises when set.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_execution_guards.py -q`
Expected: several FAIL.
- W10 fails because `_new_job` accepts the reserved task.
- W5/W6 fail because `_mark_failed` swallows the refusal but returns before reporting. If the storage chokepoint already covers a test, it passes; keep it as a regression test.

- [ ] **Step 3: Implement the explicit guards**

`workspace/http.py` `_new_job` (L566):

```python
        from workspace.storage import RESERVED_TASKS
        if task in RESERVED_TASKS:
            raise HTTPError(400, "reserved-task", "이 작업 유형은 별도 실행 경로에서만 생성됩니다.")
```

`workspace/worker.py` `handle`: no change is needed, because `claim_job` returns `None` for reserved jobs. Add a comment above `claim_job` pointing to the contract:

```python
        # platform-execution/1: reserved execution jobs are never claimed here (storage chokepoint).
```

`workbench/worker.py` `_mark_failed`, at the top after the input check:

```python
    from workspace.storage import is_reserved
    if is_reserved(job):
        return
```

And after `current = worker.storage.get(...)`:

```python
        if is_reserved(current):
            worker.storage._report(owner, _refusal(kind, identifier))
            return
```

`_refusal` is defined as `lambda kind, identifier: ReservedRecord(kind, identifier, "marked-artifact")`.

`workspace/ontology_jobs.py` `reconcile`, first line of the body:

```python
    from workspace.storage import is_reserved, ReservedRecord
    if is_reserved(artifact):
        ctx.storage._report(ctx.owner, ReservedRecord("wb_artifact", artifact["id"], "marked-artifact"))
        return artifact
```

`documents/jobs.py` (review round 6, Z3). `reconcile(host, scope, kind, target, …)` receives the **target**, and its local `job` is loaded later (`jobs.py:9-19`). The guard therefore works in two places:
- first: `if is_reserved(target): report(target, "marked-artifact"); return target`
- after `job = library.storage.get(...)`: `if is_reserved(job) or (job is None and target.get("executionId")): report(...); return target`

`expire_raw_job(host, scope, job)` and `fail_work(worker, owner, job, message)` receive the job, so they check `is_reserved(job)` first. Tests call each real function for both an ordinary document job, whose behavior is unchanged, and a reserved target or job.

- [ ] **Step 4: Retire the obsolete cloud-factory comment**

In `workspace/ontology_jobs.py:119-123`, replace the three comment lines with:

```python
    # Unit A admits only this exact offline analyzer. AgentCore source analysis is a
    # new-ledger Runtime execution (AGENTCORE_CONTRACT platform-execution/1), never an
    # analyzer injected here.
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add platform/workspace/http.py platform/workspace/worker.py platform/workbench/worker.py platform/workspace/ontology_jobs.py platform/documents/jobs.py platform/tests/test_execution_guards.py platform/tests/test_workspace_http.py
git commit -m "feat(execution): guard the full legacy writer inventory against reserved execution records"
```

### Task 4: Profiles, admission and cancellation

**Files:**
- Modify: `platform/workspace/execution_ledger.py`
- Create: `platform/tests/test_execution_ledger.py`

**Interfaces:**

`PROFILE_DEFAULT`:

```python
PROFILE_DEFAULT = {"id": "agentcore-default", "revision": "1", "deadlineMs": 14 * 60_000,
                   "heartbeatMs": 30_000, "leaseMs": 90_000, "recoveryWindowMs": 300_000,
                   "readRetries": 2, "completionRetries": 2, "maxAttempts": 3,
                   # "maxCalls" is not a global cap: budget.maxCalls = maxCallsByOperation[operation] (review round 8, Z2)
                   # per-operation model/service call ceilings (review round 6, Z2); admission preflight
                   # rejects a request whose worst-case plan exceeds its ceiling with "call-budget"
                   "maxCallsByOperation": {"design.extract": 12, "design.generate": 120, "design.edit": 40,
                                           "design.compose": 20, "design.release": 10, "source.analyze": 10,
                                           "intake.transcribe": 6}}
```

`profile_hash(profile) = schema.digest(profile)`. `OPERATIONS` is a closed map from operation to required stages. `design.compose` and `design.release` have no `generate` stage, and the Lambda `intent` refuses `kind: "model"` for them:

```python
OPERATIONS = {"design.extract": ("context", "generate", "verify"),
              "design.generate": ("context", "generate", "compile", "browser", "verify"),
              "design.edit": ("context", "generate", "compile", "browser", "verify"),
              "design.compose": ("context", "compile", "browser", "verify"),
              "design.release": ("context", "compile", "browser", "verify"),   # no model stage
              "intake.transcribe": ("context", "generate", "verify"),
              "source.analyze": ("context", "analyze")}
```

`class Ledger` is constructed only through classmethods:
- `Ledger.production(storage, *, verifier)` requires `type(verifier) in _REGISTERED`, a registry populated only by B1's KMS verifier module.
- `Ledger.offline(storage, *, verifier)` requires `"pytest" in sys.modules` and `type(verifier).__module__ == "execution_fakes"`.

Facades are role-bound views: `api()`, `dispatcher()`, `tool()`, `reconciler()`. Each returns an object exposing only that role's methods, so a caller role is never an argument.

`api().admit(owner, *, request_key, actor, project_id, operation, model, manifest, admissions, backend_config_revision, authorization_expires_at, profile=PROFILE_DEFAULT) -> dict`:
- `manifest` is `{ref, hash}`: the immutable input manifest, already written as a blob by the caller.
- `admissions` is `[{decisionId, revision, artifactHash}]`.
- **Identity (review round 2, N3).**
  - The job id is random: `"exec-" + secrets.token_hex(16)`, which is 128 bits.
  - Idempotency uses a separate **request marker** record, kind `exec_request`, with id `schema.identity("exec-req", owner, actor, request_key)`. The id is actor-scoped, so two actors never share a key. The marker holds `{jobId, inputHash}`.
  - The marker and the job are created in one `put_many`, with a `version: None` check on the marker.
- Idempotency:
  - Same actor and `request_key` with the same `inputHash` returns the existing job.
  - Same actor and key with a different hash → `LedgerError("request-changed")`.
  - A different actor with the same key creates its own job.
- **Authority binding.** `admit` takes the verified claims (`actor`, and `authorization_expires_at` equal to the JWT `exp` × 1000) and a `project_authority` value, `{authorityRevision, membershipDigest}`. These come from the current project record (`storage.py:142-147` maintains `authorityRevision`). The API reads them through `Collaboration`; they are never taken from the request body. They are stored on the job as `authority`.
  - Every later transition (`allocate`, `claim`, `stage`, `finish`, `reconcile`, `retry`) re-reads the project record and compares `authorityRevision` and the actor's current role. A mismatch → `LedgerError("authority-changed")` and a fenced `failed` transition, so removing and re-adding a member cannot resurrect authorization.
- `inputHash = schema.digest({actor, operation, model, manifest, admissions, backendConfigRevision, profileHash, authority})`.
- `deadlineAt = min(now + profile.deadlineMs, authorization_expires_at)`. `authorization_expires_at` must be ≤ the verified token expiry; the API never computes `now + constant`.
- An empty `admissions` list → `LedgerError("admission-required")`.
- **Completion-scope preflight (review round 5, Y5; `ONTOLOGY_CONTRACT.md:31-40`).**
  - `admit` takes `completion_scope = {nonSourceOperations: int, sourceChecks: int, sourceBindings: int}`. The caller's planner computes it before any paid work: `design_completion.plan_scope(operation, manifest)` counts the terminal job, marker, quota and op entries, artifact/run/round writes, the ontology manifest and request marker, and every source check from the manifest.
  - `admit` rejects the request, **unchanged**, with `LedgerError("execution-completion-scope", limits=...)` when either of these holds:
    - `sourceChecks > min(90, 100 - max(10, nonSourceOperations))`
    - `sourceBindings > 50`
  - The accepted scope is frozen in the job. `finish` rechecks the actual count, and overflow returns `execution-completion-scope` with no transaction.
  - Test: 12 non-source operations with 88 source checks are admitted, while 89 are rejected at admission with zero dispatcher, Runtime or model calls. The fakes count them.
- Every later transition's `_check_authority` also adds the project record version as a `put_many` check, so the authority comparison and the write are atomic.
- **Due-work index** (review round 35, BC1). Each transition that changes a lease, the deadline or the terminal state writes a new due-time-ordered `exec_due` entry, and marks the previous one `done`, in the same `put_many` (B2 S7, review round 36, BD1/BD2). The entries sit in partition `execution:due` with the target partition in `targetOwner`, and removal is a tombstone rather than a delete. That lets the scheduled reconciler (B2 S7) discover work without supplied ids. The storage kind `exec_due` is added.
- **Concurrency and spend (N7; `AGENTCORE_CONTRACT.md:712-716`).**
  - An `exec_quota` record per actor (`quota-actor-<hash>`) and per project (`quota-project-<id>`) holds `active: [jobId]`.
  - `admit` adds the job to both lists in the same transaction, with version CAS. More than 1 active job per actor → `LedgerError("concurrency-actor")`. More than 2 per project → `LedgerError("concurrency-project")`.
  - Every terminal transition removes the job from both lists, again in the same transaction. `retry`, which requeues a `recovery_required`/`failed`/`expired` job, **re-acquires** both quota slots in its own transaction, under the same limits (review round 5, N7). A retry that would exceed a limit → `concurrency-actor`/`concurrency-project`, and the job stays terminal. This includes `_cancel`, `_fail`, `sweep`→`expired`/`failed`, and `finish`. Each has a test asserting the quota is released (review round 3, N7).
  - The profile adds `tokenBudget` (default 400 000 input+output tokens per execution) and `cleanupReserveMs` (default 60 000).
  - `intent` reserves `max_tokens` against the remaining `tokenBudget` and `min_remaining_ms + cleanupReserveMs` against the deadline. `outcome` records the actual usage and releases the difference.
  - The daily cost gate reuses `common.costguard.budget_ok()` and `add_usage()`, the same module the legacy worker uses (`workspace/worker.py:169-176`). The Lambda tool facade calls it inside `intent`; `budget_ok() is False` → `LedgerError("daily-budget")`. `costguard` returns success when its table is not configured (`api/common/costguard.py:41-58`), so the ledger first requires `costguard` to be configured: the table env var is set and a probe read succeeds. If it is not → `LedgerError("daily-budget-unavailable")`, which is fail closed. The Runtime has no DynamoDB access, so it cannot bypass the gate.
  - B2 S7 adds the service-spend alarm, and C7 requires it before cohort admission.

`api().cancel(owner, job_id, *, actor)`:
- queued/dispatched/running/recovery_required → `cancelled`.
- The `fence` is incremented so that later attempt writes fail.
- A terminal job → `LedgerError("terminal")`.

`LedgerError(code)` is a `ValueError` subclass with a `.code` attribute. The API maps it to HTTP 409/400 in C.

Job record fields, written with `_writer=_WRITER`:

```python
{"id", "task": TASK, "executionSchemaVersion": 1, "requestKey", "inputHash", "actor", "projectId", "operation",
 "model", "backend": "agentcore", "backendConfigRevision", "profile": {"id", "revision", "hash"},
 "manifest": {"ref", "hash"}, "admissions": [...], "status", "fence": 0, "attempt": None, "attempts": [],
 "calls": [], "stages": [], "transfers": [], "budget": {"calls": 0, "maxCalls": ...}, "deadlineAt",
 "authorizationExpiresAt", "result": None, "error": None, "unknownOutcome": False}
```

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_execution_ledger.py
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


def test_cancel_fences_the_job(env):
    _, ledger, _ = env
    job = admit(ledger)
    cancelled = ledger.api().cancel(OWNER, job["id"], actor="designer-1")
    assert cancelled["status"] == "cancelled" and cancelled["fence"] == job["fence"] + 1
```

  Also add a test in which records with `executionSchemaVersion` equal to `True`, `"1"`, `2`, `None` or missing (with `task="agentcore-execution"`), seeded through `_seed_for_tests`, are rejected by **every** new-ledger entry point (`allocate`, `claim`, `stage`, `finish`, `sweep`, `reconcile`, `retry`) with `not-an-execution`, with no mutation. The version is unchanged (review round 16, AJ2).

- [ ] **Step 2: Create `tests/execution_fakes.py`**

```python
"""Offline-only execution fakes. Not packaged into any Lambda or Runtime image."""
import hashlib, hmac, json

_KEY = b"offline-test-key-not-a-secret"


class TestKeyVerifier:
    key_id = "offline-test"

    def sign(self, body):
        return hmac.new(_KEY, json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()

    def verify(self, receipt):
        body = {k: v for k, v in receipt.items() if k != "signature"}
        return receipt.get("keyId") == self.key_id and hmac.compare_digest(receipt.get("signature", ""), self.sign(body))


def receipt(verifier, *, job, stage, nonce, objects=(), extra=None):
    body = {"schemaVersion": 1, "executionId": job["id"], "attemptId": job["attempt"]["id"],
            "fence": job["fence"], "sessionId": job["attempt"]["sessionId"], "stage": stage, "nonce": nonce,
            "profileHash": job["profile"]["hash"], "admissions": job["admissions"],
            "objects": list(objects), "keyId": verifier.key_id, **(extra or {})}
    return {**body, "signature": verifier.sign(body)}
```

- [ ] **Step 3: Implement admission, cancellation, facades and verifier registry in `execution_ledger.py`**

```python
import copy, secrets, sys
from workspace import ontology_schema as schema
from workspace.storage import Conflict

TERMINAL = frozenset({"succeeded", "needs_changes", "failed", "cancelled", "expired"})


def supported_execution(job):
    """The only accepted discriminator (review round 16, AJ2): bool is an int subclass, so compare the exact type."""
    version = (job or {}).get("executionSchemaVersion")
    return bool(job) and job.get("task") == TASK and type(version) is int and version == SCHEMA_VERSION
_REGISTERED: set[type] = set()


class LedgerError(ValueError):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def register_verifier(cls):
    """Called only by the reviewed KMS verifier module (B1)."""
    _REGISTERED.add(cls)
    return cls


def profile_hash(profile):
    return schema.digest(profile)


class Ledger:
    def __init__(self, storage, verifier, _token):
        if _token is not _CONSTRUCT:
            raise PermissionError("use Ledger.production or Ledger.offline")
        self.storage, self.verifier = storage, verifier

    @classmethod
    def production(cls, storage, *, verifier):
        if type(verifier) not in _REGISTERED:
            raise PermissionError("unregistered receipt verifier")
        return cls(storage, verifier, _CONSTRUCT)

    @classmethod
    def offline(cls, storage, *, verifier):
        if "pytest" not in sys.modules or type(verifier).__module__ != "execution_fakes":
            raise PermissionError("offline ledger requires the offline test verifier")
        return cls(storage, verifier, _CONSTRUCT)

    def api(self): return _Facade(self, ("admit", "cancel", "retry", "read"))
    def dispatcher(self): return _Facade(self, ("allocate",))
    def tool(self): return _Facade(self, ("claim", "heartbeat", "intent", "outcome", "stage", "transfer", "finish", "fail"))
    def reconciler(self): return _Facade(self, ("sweep", "reconcile"))

    # --- storage helpers ---------------------------------------------------
    def _get(self, owner, job_id):
        job = self.storage.get(owner, "job", job_id)
        if not supported_execution(job):
            raise LedgerError("not-an-execution")
        return job

    def _put(self, owner, job, expected, *, extra_writes=(), checks=None):
        writes = [{"owner": owner, "kind": "job", "item": job, "expected_version": expected}, *extra_writes]
        try:
            saved = self.storage.put_many(writes, checks or [], retry_conflicts=False, _writer=_WRITER)
        except Conflict as error:
            raise LedgerError("conflict") from error
        return saved[0]

    # --- API role ----------------------------------------------------------
    def _admit(self, owner, *, request_key, actor, project_id, operation, model, manifest, admissions,
               backend_config_revision, authorization_expires_at, project_authority, profile=None):
        profile = copy.deepcopy(profile or PROFILE_DEFAULT)
        if operation not in OPERATIONS:
            raise LedgerError("unknown-operation")
        if not admissions:
            raise LedgerError("admission-required")
        now = self.storage.clock()
        ref = {"id": profile["id"], "revision": profile["revision"], "hash": profile_hash(profile)}
        input_hash = schema.digest({"actor": actor, "operation": operation, "model": model, "manifest": manifest,
                                    "admissions": admissions, "backendConfigRevision": backend_config_revision,
                                    "profileHash": ref["hash"], "authority": project_authority})
        marker_id = schema.identity("exec-req", owner, actor, request_key)
        marker = self.storage.get(owner, "exec_request", marker_id)
        if marker:
            if marker["inputHash"] != input_hash:          # includes authority: a changed epoch is a changed request
                raise LedgerError("request-changed")
            existing = self._get(owner, marker["jobId"])
            self._check_authority(owner, existing)          # a replayed admit revalidates current authority
            return existing
        job_id = "exec-" + secrets.token_hex(16)
        quotas = self._quota_writes(owner, actor, project_id, add=job_id)   # raises concurrency-actor/-project
        job = {"id": job_id, "task": TASK, "executionSchemaVersion": SCHEMA_VERSION, "requestKey": request_key,
               "inputHash": input_hash, "actor": actor, "projectId": project_id, "operation": operation,
               "model": model, "backend": "agentcore", "backendConfigRevision": backend_config_revision,
               "profile": ref, "profileBody": profile, "manifest": manifest, "admissions": admissions,
               "status": "queued", "fence": 0, "attempt": None, "attempts": [], "calls": [], "stages": [],
               "transfers": [], "budget": {"calls": 0, "maxCalls": profile["maxCallsByOperation"][operation]},
               "deadlineAt": min(now + profile["deadlineMs"], authorization_expires_at),
               "authorizationExpiresAt": authorization_expires_at, "result": None, "error": None,
               "unknownOutcome": False, "authority": project_authority, "ops": {}}
        marker_write = {"owner": owner, "kind": "exec_request",
                        "item": {"id": marker_id, "jobId": job_id, "inputHash": input_hash}, "expected_version": None}
        project = self.storage.get(owner, "project", project_id)
        if (not project or project.get("authorityRevision") != project_authority["authorityRevision"]):
            raise LedgerError("authority-changed")
        # The project record's version is a transactional fence: a concurrent membership change aborts admission.
        return self._put(owner, job, None, extra_writes=[marker_write, *quotas],
                         checks=[{"owner": owner, "kind": "project", "id": project_id, "version": project["version"]}])

    def _cancel(self, owner, job_id, *, actor):
        job = self._get(owner, job_id)
        if job["status"] in TERMINAL:
            raise LedgerError("terminal")
        quotas = self._quota_writes(owner, job["actor"], job["projectId"], remove=job_id)   # review round 4, N7
        return self._put(owner, {**job, "status": "cancelled", "fence": job["fence"] + 1,
                                 "error": {"code": "cancelled", "actor": actor}}, job["version"], extra_writes=quotas)


_CONSTRUCT = object()


class _Facade:
    def __init__(self, ledger, names):
        for name in names:
            setattr(self, name, getattr(ledger, "_" + name))
```

The `_admit` signature takes `profile=None`, and the job stores both `profileBody`, which is bounded, and the `profile` ref.

- [ ] **Step 4: Run** `python3 -m pytest tests/test_execution_ledger.py -q` → PASS.
- [ ] **Step 5: Commit**

```bash
git add platform/workspace/execution_ledger.py platform/tests/test_execution_ledger.py platform/tests/execution_fakes.py
git commit -m "feat(execution): admission, cancellation and role-bound ledger facades"
```

### Common mutation contract for Tasks 5–8 (review round 2, N4 and N5)

**Operation idempotency (`AGENTCORE_CONTRACT.md:241-245`)**

Every mutating `tool()`, `dispatcher()` and `reconciler()` method takes `operation_id`, a caller-generated id of at least 128 bits, and derives `digest = schema.digest({method, args without operation_id})`.

The job holds `ops: {operation_id: {"digest", "version", "status", "value"?}}`. The bound is `maxCallsByOperation[operation] + 80`, which covers the ledger's own stage, finish and handle operations (review round 7, Z2). Only `intent` is an op entry per model call. `outcome` is keyed by `callId` on the call record, where the same outcome is idempotent and a different one → `operation-changed`, so 120 calls consume 120 op entries, not 240. The entries are compact (see F5). `value` is an optional scalar result of at most 256 bytes. Examples are `intent`'s `callId` and `open_output`'s `handleId`. A retry returns it together with the projection, so a retried `intent` yields the **same** `callId` (review round 4, N4). This is a **compact** reference: the job version and status produced by that operation, **never** the job or result body, so there is no recursive embedding. A retry with the same id and digest returns the current job projection. That projection is the job minus `ops`, `calls[*]` and older `attempts`, bounded to 64 KiB, taken at version ≥ the recorded version. `ops` entries older than the current attempt are pruned at `allocate`. Writing a new entry when the map is full → `LedgerError("operation-budget")`.
- `heartbeat`, `read_chunk` and `write_chunk` are **not** recorded in `ops`. Heartbeats are idempotent by nature. Chunk operations are idempotent by `(handleId, index, chunkHash)` stored on the handle. They count against the transfer budget of 512 chunks and 128 MiB, which is separate from the 60-call model-visible tool budget (review round 3, N6).
- A test runs 11 000 heartbeats plus 150 recorded operations and asserts the job record stays below 200 000 bytes.

| Situation | Behavior |
|---|---|
| Same id, same digest | Returns the stored `result` without repeating effects: no second budget reservation, no nonce check. This applies even after the job becomes terminal, when the id was the completing `finish`. |
| Same id, different digest | `LedgerError("operation-changed")` |
| New id on a terminal job | `LedgerError("terminal")` |

The op entry and its effect are written in the **same** conditional put, so a crash can never leave one without the other.

**Receipt schema v1**

Every receipt is signed by the observer key (B2 S3). Unknown fields are rejected.

```python
{"schemaVersion": 1, "executionId", "attemptId", "fence", "sessionId", "stage", "operationId", "nonce",
 "profileHash", "admissions": [...],                      # exactly the job's admissions
 "inputs": [{"key", "sha256", "size"}],                  # consumed objects (admitted inputs or earlier outputs)
 "outputs": [{"key", "sha256", "size", "role"}],         # objects this stage produced
 "service": {"kind": "runtime"|"interpreter"|"browser"|"model", "sessionId", "taskId"?, "exitCode"?},
 "iat": <ms>, "exp": <ms>,                               # signed timing; exp ≤ job deadline (review round 6, Y7)
 "status": "ok"|"failed"|"incomplete", "result": {...},  # stage-specific, bounded 8 KiB
 "previous": <sha256 of the previous receipt or null>, "keyId", "signature"}
```

**Checks at `stage`**, beyond the Task 5 bindings:
- `previous` equals the hash of the last recorded receipt, so the chain is linear per attempt.
- Every `inputs[*].key` is one of these (review round 8, AA3):
  - an admitted input artifact of the job
  - the job manifest (`job.manifest.ref`)
  - a prior listed in the manifest's `priors`
  - an `outputs[*]` of an earlier receipt in the chain
- Every `outputs[*]` object exists, with `storage.blob_info(key)` giving the declared `sha256` and `size`. These are server-computed: the Lambda re-hashes them, not the Runtime.
- Every output key lies under `key_for(owner, "job", job_id, f"out/{attemptId}/…")`.

**Terminal status rules at `finish`: operation-specific (review round 3, F4)**

`TERMINAL_RULES[operation]` is a closed table, versioned with the profile:

| Operation | `succeeded` requires | `needs_changes` when |
|---|---|---|
| `source.analyze` | The `analyze` receipt is `ok`, with `result.coverage` present. There is no verify stage | Never; analysis either succeeds or fails |
| `design.extract` | The `verify` receipt is `ok` with `result.issues == []`, the deterministic citation check | The `verify` result has issues |
| `design.generate`, `design.edit` | The `verify` receipt has `result.verdict == "pass"` and `approvable` | The verdict is `fail`/`blocked`, or not approvable |
| `design.compose` | The `compile` and `browser` receipts are `ok`. The `verify` receipt comes from the **deterministic** reviewer (composition, coverage, tester) **plus** the reused LLM-judge evidence of the generate/edit rounds whose composition hashes equal the composed ones. No model call; `intent kind=model` is refused | Any deterministic failure, or judge evidence missing for a composed composition hash |
| `intake.transcribe` | The `verify` receipt is `ok`: transcription schema valid, region within image. Human validation follows separately | The verify result has issues |
| `design.release` | The `compile` receipt `sourceHash`/`bundleHash` equal the approved ones, and the `browser` receipt passes with the visual diff ≤ 0.02 against the approved screenshot. No reviewer | Never; a mismatch → `failed` |

Any other combination → `LedgerError("status-inconsistent")`. Each row has a positive and a negative test.

The result manifest (`result.manifestRef`) must itself be a chain output, with a matching hash. `finish` re-reads its bytes and checks that every file and object it lists is a chain output. A substituted manifest → `receipt-invalid`.

**Tests added to Tasks 5 and 8**
- A correctly signed receipt whose `status` is `failed`, used in an attempt to finish `succeeded` → `status-inconsistent`.
- A receipt whose output object is missing from storage → `receipt-invalid`.
- A receipt whose output hash differs from the stored bytes → `receipt-invalid`.
- A substituted result manifest → `receipt-invalid`.
- A broken `previous` chain → `receipt-invalid`.
- Retrying `intent` with the same `operation_id` reserves budget once.
- Retrying a successful `stage` with the same `operation_id` returns the same result, without a nonce replay error.
- Retrying a completed `finish` with the same `operation_id` returns the terminal job.
- Reusing an `operation_id` with a changed payload → `operation-changed`.

### Task 5: Attempts, leases, heartbeats and stage receipts (RUN-01, RUN-02, RUN-04)

**Files:** Modify `execution_ledger.py`; append tests.

**Interfaces:**

`dispatcher().allocate(owner, job_id) -> dict`:
- queued → dispatched.
- Sets `attempt = {id: "att-"+hex(16), fence: fence+1, sessionId: "rt-" + 40 hex chars, leaseExpiresAt: now+leaseMs, heartbeatAt: None, startedAt: now}`. The session id is at least 33 characters, as the Runtime requires.
- Increments `fence`. Any status other than queued → `LedgerError("not-queued")`.
- `len(attempts) >= maxAttempts` → `failed/attempts-exhausted`.

`tool().claim(owner, job_id, attempt_id, fence) -> dict`: dispatched → running. It uses `_current(job, attempt_id, fence)`, which raises `LedgerError("stale-attempt")` unless all of these hold:
- the attempt id and fence match
- the status is in {dispatched, running}
- `now < deadlineAt`
- `now < attempt.leaseExpiresAt`

`tool().heartbeat(owner, job_id, attempt_id, fence)` extends `leaseExpiresAt = min(now + leaseMs, deadlineAt)`.

`tool().stage(owner, job_id, attempt_id, fence, receipt) -> dict`: appends `{stage, receiptHash, nonce, objects}` after all of these checks pass:
- `verifier.verify(receipt)`
- the receipt's executionId, attemptId, fence, sessionId and profileHash equal the current job's
- `receipt.admissions == job.admissions`, i.e. the exact consumed decisions
- the nonce has not been seen before on this job
- the stage belongs to `OPERATIONS[job.operation]`

Any mismatch raises `LedgerError("receipt-invalid")` without writing.

`tool().fail(owner, job_id, attempt_id, fence, *, code)` → failed.

- [ ] **Step 1: Write the failing tests**

```python
def running(env):
    storage, ledger, now = env
    job = admit(ledger)
    job = ledger.dispatcher().allocate(OWNER, job["id"])
    job = ledger.tool().claim(OWNER, job["id"], job["attempt"]["id"], job["fence"])
    return job


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


def test_replayed_nonce_is_rejected(env):
    _, ledger, _ = env
    job = running(env)
    r = receipt(TestKeyVerifier(), job=job, stage="context", nonce="n1")
    job = ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r)
    with pytest.raises(LedgerError):
        ledger.tool().stage(OWNER, job["id"], job["attempt"]["id"], job["fence"], r)
```

In the tamper cases, the tampered fields are changed after signing, so the signature also fails. Two tests make the binding check independently necessary: build a receipt signed over the tampered body, using `TestKeyVerifier().sign` over the modified body, for the `admissions` case and the `attemptId` case. Verify that these still fail.

- [ ] **Step 2: Fail** → **Step 3: Implement** `_allocate`, `_claim`, `_heartbeat`, `_stage`, `_fail`, `_current`. Each method re-reads the job, validates it, and writes with `expected_version=job["version"]` through `_put`.
- [ ] **Step 4: Pass** → **Step 5: Commit** `git commit -m "feat(execution): fenced attempts, leases and verified stage receipts"`

### Task 6: Per-call intent, budget and unknown outcomes (RUN-03)

**Interfaces:**

`tool().intent(owner, job_id, attempt_id, fence, *, stage, kind: "model"|"interpreter"|"browser", min_remaining_ms) -> {"callId", "job"}`:
- Rejects with `budget-exhausted` if `budget.calls >= maxCalls`.
- Rejects with `deadline-budget` if `deadlineAt - now < min_remaining_ms`.
- Appends the call `{callId, stage, kind, status: "intent", at}` and increments `budget.calls`.

`tool().outcome(owner, job_id, attempt_id, fence, call_id, *, status: "completed"|"failed", usage=None)`:
- `usage` holds metadata only: `{inputTokens, outputTokens}`.

`reconciler().sweep(owner, job_id) -> dict`:
- running/dispatched with a lost lease, or any call still `intent` past its lease → `recovery_required`, `unknownOutcome = any intent`.
- `now >= deadlineAt` → `expired`.
- In `recovery_required` longer than `recoveryWindowMs` → `failed` with `unknownOutcome` metadata.
- The watchdog never sets succeeded/needs_changes.

`api().retry(owner, job_id, *, actor, acknowledge_unknown_outcome: bool)`:
- Only from `recovery_required` or `failed`/`expired` with `unknownOutcome`, **and only while `now < deadlineAt` and `now < authorizationExpiresAt`** (review round 31, AY2).
  - After the deadline or the JWT expiry → `LedgerError("retry-expired")`, with the job's terminal state and quota unchanged. The caller must instead submit a **new** admission with a new `request_key`, freshly verified authorization and the explicit `acknowledge_unknown_outcome`. The new job records `supersedes: <old job id>`.
  - Tests: a retry after the deadline, and a retry after authorization expiry, are both refused with state and quota unchanged. A retry within the deadline proceeds through `allocate` and `claim` to `running`.
- `actor` must be the original requester or a current project owner; C supplies the current role check.
- It requires `acknowledge_unknown_outcome=True` when `unknownOutcome`.
- It resets to `queued` with `fence+1` and the previous attempt appended to `attempts`. It never replays calls: calls with status `intent` are marked `unknown`.

- [ ] **Step 1: Tests**

```python
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
```

- [ ] **Step 2–4:** Fail → implement `_intent`, `_outcome`, `_sweep`, `_retry` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): per-call intent, budget reservation and bounded recovery"`

### Task 7: Transfer receipts (RUN-05)

**Interfaces:**

Handles are ledger records inside the job: `handles: {handleId: {...}}`. There are two directions (review round 2, N6).

**Manifest and prior-artifact handles (review round 7, AA3)**

The Runtime has no S3 access. It receives only `manifest: {ref, hash}` in the invocation, so the ledger provides:
- `tool().open_manifest(owner, job_id, attempt_id, fence, *, operation_id) -> {handleId, total, sha256}` serves the exact job manifest blob `job.manifest.ref`. The Lambda re-hashes it against `job.manifest.hash`.
- `tool().open_prior(owner, job_id, attempt_id, fence, *, operation_id, ref)` serves a prior artifact that the manifest lists under `priors`, for example an approved round's `source.zip` for `design.release` or a base composition for `design.edit`.
  - Each prior is `{sourceKind: "run-round"|"release", sourceId, revision, sha256, key}`.
  - The Lambda checks that `ref` is listed in the manifest, re-hashes the blob, and resolves **current** authority through the B0-sharing `run-round` adapter, including upstream lineage.
- Both use the same chunked `read_chunk` transport.

Test: a complete `design.release` chain driven only by the invocation envelope and Gateway calls, with direct S3 access denied to the fake Runtime, retrieves its manifest and approved source.

**Input handles**

`tool().open_input(owner, job_id, attempt_id, fence, *, operation_id, decision_id, stage) -> {handleId, total, sha256, chunks}` requires `(decision_id, …) ∈ job.admissions`. The Lambda then serves chunks of the admitted derivative blob (B0 intake `pages_for` / blob key), 256 KiB each, through `read_chunk(handleId, index)`. The Lambda computes every chunk hash from the stored bytes. Nothing claimed by the Runtime is recorded.

**Output handles**

`tool().open_output(owner, job_id, attempt_id, fence, *, operation_id, stage, name, total, sha256)`:
- `name` matches `[a-z0-9][a-z0-9._-]{0,99}`.
- The handle is bound to `(executionId, attemptId, stage)`, and its storage key is fixed by the server: `key_for(owner, "job", job_id, f"out/{attemptId}/{stage}/{name}")`.

`write_chunk(handleId, index, bytes)`:
- The chunk bytes travel base64 in the tool call and are ≤ 256 KiB.
- The Lambda writes each chunk to `…/{name}.part{index:04d}` and computes its hash.
- Indexes must be contiguous from 0.

`close_output(handleId)`:
- The Lambda concatenates the parts **server-side**, computes the SHA-256 and requires it to equal the declared `sha256` and `total`.
- It writes the final object with `put_blob_once` and deletes the parts.
- It records `{key, sha256, size}` as an attempt output.

**Limits and fencing**
- Per job: at most 512 chunks and 128 MiB of binary transfer, plus 4 MiB of model-visible text (`AGENTCORE_CONTRACT.md:660-670`).
- A handle expires at the attempt lease or the deadline.
- Cancellation, revocation or supersession fences further chunks and records the handle in `cleanup`, which the reconciler deletes.
- Violations → `transfer-invalid`.

- [ ] **Step 1: Tests** (the fake S3 stores real bytes):
  - an input read that completes
  - an out-of-order output chunk
  - an output whose final server hash differs from the declared hash
  - an output name with `/`
  - an input handle for a decision not in `admissions`
  - a chunk after cancel
  - exceeding 512 chunks
  - a retried `write_chunk` with the same `operation_id` → idempotent
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): admission-bound transfer receipts"`

### Task 8: Coupled completion and reconciliation (RUN-02, RUN-04, ONT-07 staging rule)

**Files:** Modify `platform/workspace/execution_ledger.py`, `platform/workspace/ontology_store.py` (`publish_candidate` `_stage` mode, co-reviewed by the ontology maintainer) and `platform/workspace/ONTOLOGY_CONTRACT.md`. Append tests.

**Interfaces:**

**Single-attempt transport (review round 8, AB1).** `Storage._config()` gives the workspace client `total_max_attempts=3` (`storage.py:92-94`). A timed-out transaction could therefore be resubmitted by the SDK without the facade's fresh authority checks.
- `Storage(..., single_attempt=True)` builds its client with `retries={"total_max_attempts": 1}`. The ledger, the tool facade and the dispatcher/reconciler construct their `Storage` this way.
- The only retries are the facade's explicit ones: at most 2 proven-contention retries, each after fresh actor, source, deadline and attempt checks.
- An unknown transport outcome, such as a read timeout, first reconciles durable state: the job version, the marker and the op entry. Otherwise it enters `recovery_required`, and it is never resubmitted blindly.
- Test: a botocore `Stubber`/fake transport that times out on the first `TransactWriteItems` → exactly **one** wire attempt, followed by reconciliation. A proven `TransactionConflict` → at most 3 wire attempts, each preceded by an authority check (spy counts).

**Composition with ontology publication (review round 2, N1)**

`Ontology.publish_candidate` commits through `ctx.commit` (`ontology_store.py:376-377`). B0 therefore adds a **staging mode** to it: `publish_candidate(..., _stage=True)`. In staging mode it runs every existing check and index computation, then returns `{"writes": [...], "checks": [...], "result": {...}}` instead of committing, where the writes are the ontology manifest, the request marker and any `_completion_writes`. `authorize_publication`'s origin set (`ontology_store.py:207-208`, currently `{"manual", "workbench-import"}`) gains `"execution"` and `"design-approval"`, and the **`design-usage-` name prefix is reserved** there (review round 15, AI2):
- `manual`, `workbench-import` and `execution` origins writing a `design-usage-*` name → `403 ontology-managed-partition`, even for the owner.
- `design-approval` may write **only** `design-usage-<designId>`, where `designId` is taken from the authorized design record, never from the request.
- The prior partition's `createdBy` check (`ontology_store.py:216-218`) does not apply to that server-managed partition.
- Tests:
  - a direct `POST /ontology/partitions {name: "design-usage-x"}` by a developer or the owner → 403
  - a designer's approval → the usage partition is written, even when another designer approved earlier

Staging is allowed only with `_origin="execution"` (coordinated by `Ledger._finish`) or `_origin="design-approval"`. The second is coordinated by the human design-approval transaction in C4, restricted to producer `verified-build` and to the design's own `design-usage-<designId>` partition (review round 14, AH1). The staged path also accepts the execution producers `model-inferred` and `verified-build`, besides `parser-extracted`, which the manual path keeps refusing. This is an ontology-contract change, owned by the ontology maintainer and co-reviewed.

The single trusted coordinator is `execution_ledger.Ledger._finish`:
1. It prepares its own terminal-job, quota and op-entry writes and checks, **without committing**, through `prepare_finish(...)`.
2. It asks the caller for the staged ontology and artifact writes.
3. It submits everything in **one** `storage.put_many(..., retry_conflicts=False, _writer=_WRITER)`.

Reserved-record and linked-job guards do not block the combined write, because the ledger writer token authorizes it. Any predicate failure writes nothing. There is no second commit: `publish_candidate` never commits in staging mode.

`tool().finish(owner, job_id, attempt_id, fence, *, operation_id, status: "succeeded"|"needs_changes", result: {manifestRef, manifestHash, receipts: [receiptHash]}, stage_completion=None)`:
- `stage_completion` is a callable. It receives the prepared ledger writes, so it can reference them, and returns `{writes, checks}` from `publish_candidate(_stage=True)` and the artifact writers.
- It requires:
  - a current attempt
  - every stage in `OPERATIONS[operation]` present in `job.stages` with verified receipts
  - `result.receipts` equal to the recorded receipt hashes, in order
  - all transfers complete
- It commits the prepared ledger writes plus the `stage_completion` writes and checks in **one** `put_many` transaction with `retry_conflicts=False`.
- On proven contention, it retries at most `completionRetries` times with fresh checks.
- `len(writes) + len(checks) > 100` → `LedgerError("execution-completion-scope")` with no write.
- There is no terminal-only write when the staged completion writes fail.

`reconciler().reconcile(owner, job_id, attempt_id, *, receipts)`:
- Allowed only when the attempt is still current, `now < deadlineAt` and the status is `recovery_required`.
- It verifies receipts exactly as `stage` does, then delegates to the same completion path.

- [ ] **Step 1: Tests**
  - `publish_candidate(_stage=True, _origin="execution")` returns writes and checks, and the ontology generation is unchanged.
  - `_stage=True` with `_origin="manual"` → `ValueError`.
  - A combined finish commits the ontology manifest, the request marker, the artifact and the terminal job in one transaction. Assert this through `FakeTable.transactions` length 1.
  - Finishing without the `browser` stage → `stages-incomplete`.
  - `completion_writes` containing a stale record version → `LedgerError("conflict")`, with the job still `running` and no artifact written.
  - 99 checks plus 2 writes → `execution-completion-scope`.
  - A superseded attempt (`retry` after `recovery_required`) calling `finish` with the old fence → `stale-attempt`, and the late receipt stays as a non-current diagnostic in `attempts[-1].late` without changing the result.
  - Reconcile after the deadline → `LedgerError("deadline")`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): single-transaction completion and bounded reconciliation"`

### Task 9: Owning contract and docs

**Files:**
- Modify: `platform/workspace/AGENTCORE_CONTRACT.md` `platform-execution/1`
- Modify: `platform/docs/CONTRACTS.md` (storage kinds, reserved-task behavior)
- Modify: `platform/docs/ARCHITECTURE.md` B0 row (baseline → "offline protocol implemented; not deployed")

- [ ] **Step 1:** Add a "Record fields (v1)" subsection under `platform-execution/1`. List:
  - the job fields from Task 4
  - the attempt fields
  - call, stage, transfer
  - the facade/method → caller-role table
  - error codes: `request-changed`, `admission-required`, `not-queued`, `stale-attempt`, `receipt-invalid`, `budget-exhausted`, `deadline-budget`, `transfer-invalid`, `stages-incomplete`, `execution-completion-scope`, `conflict`, `terminal`, `deadline`
  - the `exec_report` metadata record

  Label the transports as planned: dispatcher, Lambda facade and IAM.
- [ ] **Step 2:** In `CONTRACTS.md`, add `exec_report` to the storage kinds. State that `POST` routes creating jobs reject reserved tasks with `400 reserved-task`.
- [ ] **Step 3:** Run `cd .. && NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build` (only if the guidebook links these files) and `git diff --check`.
- [ ] **Step 4: Commit** `git commit -m "docs(execution): record platform-execution/1 v1 fields, roles and errors"`

## Verification before PR

```bash
cd platform
python3 -m pytest tests/test_execution_guards.py tests/test_execution_ledger.py -q
python3 -m pytest tests/ -q
git diff --check
```

Record the RUN-01 to RUN-05 O-mode results in the private dated assessment copied from `ONTOLOGY_AGENTCORE_VALIDATION.md`. L remains NOT_RUN. Open the PR as "B0: execution ledger and legacy writer guards (offline)". The PR body states: no deployment, no production executor, and defaults unchanged.
