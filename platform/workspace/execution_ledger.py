"""Single logical transition writer for agentcore-execution jobs (AGENTCORE_CONTRACT platform-execution/1)."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import secrets
import sys

from workspace import ontology_schema as schema
from workspace import storage as _storage
from workspace.storage import DUE_OWNER, EXECUTION_JOB_PREFIX, LINKED_KINDS, Conflict, due_id, is_reserved

TASK, SCHEMA_VERSION = "agentcore-execution", 1
_WRITER = _storage._ledger_writer()

PROFILE_DEFAULT = {"id": "agentcore-default", "revision": "1", "deadlineMs": 14 * 60_000,
                   "heartbeatMs": 30_000, "leaseMs": 90_000, "recoveryWindowMs": 300_000,
                   "readRetries": 2, "completionRetries": 2, "maxAttempts": 3,
                   # "maxCalls" is not a global cap: budget.maxCalls = maxCallsByOperation[operation] (review round 8, Z2)
                   # per-operation model/service call ceilings (review round 6, Z2); admission preflight
                   # rejects a request whose worst-case plan exceeds its ceiling with "call-budget"
                   "maxCallsByOperation": {"design.extract": 12, "design.generate": 120, "design.edit": 40,
                                           "design.compose": 20, "design.release": 10, "source.analyze": 10,
                                           "intake.transcribe": 6},
                   # Spend and cleanup reservations (N7; AGENTCORE_CONTRACT.md:712-716).
                   "tokenBudget": 400_000, "cleanupReserveMs": 60_000}

OPERATIONS = {"design.extract": ("context", "generate", "verify"),
              "design.generate": ("context", "generate", "compile", "browser", "verify"),
              "design.edit": ("context", "generate", "compile", "browser", "verify"),
              "design.compose": ("context", "compile", "browser", "verify"),
              "design.release": ("context", "compile", "browser", "verify"),   # no model stage
              "intake.transcribe": ("context", "generate", "verify"),
              "source.analyze": ("context", "analyze")}

TERMINAL = frozenset({"succeeded", "needs_changes", "failed", "cancelled", "expired"})
ACTIVE = frozenset({"queued", "dispatched", "running", "recovery_required"})
QUOTA_OWNER = "execution:quota"
MAX_ACTOR_ACTIVE, MAX_PROJECT_ACTIVE = 1, 2
MAX_SOURCE_CHECKS, MAX_SOURCE_BINDINGS, TRANSACTION_LIMIT = 90, 50, 100
DEFAULT_COMPLETION_SCOPE = {"nonSourceOperations": 10, "sourceChecks": 0, "sourceBindings": 0}
CHUNK_BYTES, MAX_TRANSFER_CHUNKS, MAX_TRANSFER_BYTES = 256 * 1024, 512, 128 * 1024 * 1024
_OUTPUT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}\Z")
_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{22,128}\Z")
_REGISTERED: set[type] = set()


def supported_execution(job):
    """The only accepted discriminator (review round 16, AJ2): bool is an int subclass, so compare the exact type."""
    version = (job or {}).get("executionSchemaVersion")
    return bool(job) and job.get("task") == TASK and type(version) is int and version == SCHEMA_VERSION


class LedgerError(ValueError):
    def __init__(self, code, message="", **details):
        super().__init__(message or code)
        self.code = code
        self.details = details


def register_verifier(cls):
    """Called only by the reviewed KMS verifier module (B1)."""
    _REGISTERED.add(cls)
    return cls


def profile_hash(profile):
    return schema.digest(profile)


def receipt_hash(value):
    """SHA-256 of the canonical JSON form; receipts may carry bounded floats (e.g. visualDiff)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _actor_quota_id(actor):
    return "quota-actor-" + hashlib.sha256(str(actor).encode()).hexdigest()[:48]


def _project_quota_id(project_id):
    return "quota-project-" + str(project_id)

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


# --- daily cost gate (reuses common.costguard, like the legacy worker) --------------------------------

class CostGuardGate:
    """Fail closed: an unconfigured or unreadable costguard is not an unlimited budget."""

    @staticmethod
    def _module():
        import os
        try:
            from common import costguard
        except Exception as error:  # noqa: BLE001 - absent module means no enforceable gate
            raise LedgerError("daily-budget-unavailable") from error
        if not os.environ.get("CACHE_TABLE") or getattr(costguard, "_tbl", None) is None:
            raise LedgerError("daily-budget-unavailable")
        return costguard

    def check(self):
        costguard = self._module()
        try:
            ok = costguard.budget_ok()          # also the probe read
        except Exception as error:  # noqa: BLE001
            raise LedgerError("daily-budget-unavailable") from error
        if ok is not True:
            raise LedgerError("daily-budget")

    def record(self, tokens):
        try:
            self._module().add_usage(tokens)
        except Exception:  # noqa: BLE001 - usage accounting never rewrites the ledger outcome
            pass


class _OfflineCostGate:
    def __init__(self):
        if "pytest" not in sys.modules:
            raise PermissionError("the unlimited cost gate is offline-only")
        self.recorded = []

    def check(self):
        return None

    def record(self, tokens):
        self.recorded.append(tokens)


# --- the ledger -------------------------------------------------------------------------------------

_CONSTRUCT = object()


class _Facade:
    def __init__(self, ledger, names):
        for name in names:
            method = getattr(ledger, "_" + name, None)
            if method is not None:
                setattr(self, name, method)


class Ledger:
    def __init__(self, storage, verifier, _token, *, offline=False, cost_gate=None, input_resolver=None,
                 prior_authority=None):
        if _token is not _CONSTRUCT:
            raise PermissionError("use Ledger.production or Ledger.offline")
        self.storage, self.verifier, self.offline = storage, verifier, offline
        self.cost_gate = cost_gate or CostGuardGate()
        # B0 intake (pages_for / admitted derivative) and the B0-sharing run-round adapter are separate units;
        # until they are wired, production refuses input and prior transfers (fail closed).
        self.input_resolver, self.prior_authority = input_resolver, prior_authority

    @classmethod
    def production(cls, storage, *, verifier):
        if type(verifier) not in _REGISTERED:
            raise PermissionError("unregistered receipt verifier")
        if getattr(storage, "single_attempt", False) is not True:
            raise PermissionError("the ledger requires a single-attempt storage transport")
        return cls(storage, verifier, _CONSTRUCT, cost_gate=CostGuardGate())

    @classmethod
    def offline(cls, storage, *, verifier, cost_gate=None, input_resolver=None, prior_authority=None):
        if "pytest" not in sys.modules or type(verifier).__module__ != "execution_fakes":
            raise PermissionError("offline ledger requires the offline test verifier")
        return cls(storage, verifier, _CONSTRUCT, offline=True, cost_gate=cost_gate or _OfflineCostGate(),
                   input_resolver=input_resolver, prior_authority=prior_authority)

    def api(self): return _Facade(self, ("admit", "cancel", "retry", "read"))
    def dispatcher(self): return _Facade(self, ("allocate",))
    def tool(self): return _Facade(self, ("claim", "heartbeat", "intent", "outcome", "stage", "finish", "fail",
                                          "open_manifest", "open_prior", "open_input", "read_chunk",
                                          "open_output", "write_chunk", "close_output"))
    def reconciler(self): return _Facade(self, ("sweep", "reconcile", "resolve_orphan", "run_due"))

    # --- storage helpers ---------------------------------------------------
    def _get(self, owner, job_id):
        try:
            job = self.storage.get(owner, "job", job_id)
        except ValueError:
            job = None
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

    def _quota_writes(self, owner, actor, project_id, *, add=None, remove=None):
        """Per-actor (global) and per-project active lists, written in the caller's transaction."""
        writes = []
        for partition, identifier, limit, code in (
                (QUOTA_OWNER, _actor_quota_id(actor), MAX_ACTOR_ACTIVE, "concurrency-actor"),
                (owner, _project_quota_id(project_id), MAX_PROJECT_ACTIVE, "concurrency-project")):
            current = self.storage.get(partition, "exec_quota", identifier)
            active = list((current or {}).get("active", []))
            if add is not None and add not in active:
                if len(active) >= limit:
                    raise LedgerError(code)
                active.append(add)
            if remove is not None and remove in active:
                active.remove(remove)
            if current is None and not active:
                continue
            if current is not None and active == current.get("active", []):
                continue
            writes.append({"owner": partition, "kind": "exec_quota",
                           "item": {**(current or {"id": identifier}), "active": active},
                           "expected_version": current["version"] if current else None})
        return writes

    # --- due-work index (review rounds 35-36, BC1/BD1/BD2) -------------------
    def _due_at(self, job):
        status = job["status"]
        if status in TERMINAL:
            return job["clock"] if job.get("cleanup") else None
        if status == "queued":
            return job["deadlineAt"]
        if status == "recovery_required":
            return min(job["recoveryAt"] + job["profileBody"]["recoveryWindowMs"], job["deadlineAt"])
        attempt = job.get("attempt") or {}
        return min(attempt.get("leaseExpiresAt", job["deadlineAt"]), job["deadlineAt"])

    def _due_writes(self, owner, before, job, *, force=False):
        """Tombstone the previous entry and write the next one in the same transaction."""
        due_at = self._due_at(job)
        previous_id = (before or {}).get("dueId")
        if not force and previous_id and before and self._due_at(before) == due_at and due_at is not None:
            return []
        writes = []
        if previous_id:
            previous = self.storage.get(DUE_OWNER, "exec_due", previous_id)
            if previous and previous.get("status") == "pending":
                writes.append({"owner": DUE_OWNER, "kind": "exec_due", "item": {**previous, "status": "done"},
                               "expected_version": previous["version"]})
        job["dueId"] = None
        if due_at is not None:
            job["dueId"] = due_id(due_at, owner, job["id"], job.get("fence"), secrets.token_hex(8))
            writes.append({"owner": DUE_OWNER, "kind": "exec_due", "expected_version": None, "item": {
                "id": job["dueId"], "type": "job", "dueAt": due_at, "status": "pending",
                "targetOwner": owner, "ref": {"kind": "job", "id": job["id"]}}})
        return writes

    # --- authority binding -------------------------------------------------
    def _authority(self, owner, job):
        """Returns the project version check, or None when current authority differs from the admitted one."""
        project = self.storage.get(owner, "project", job["projectId"])
        members = (project or {}).get("members", {}) if isinstance((project or {}).get("members"), dict) else {}
        member = members.get(job["actor"])
        if (not project or project.get("status") != "active" or not isinstance(member, dict)
                or project.get("authorityRevision") != job["authority"]["authorityRevision"]
                or schema.digest(members) != job["authority"]["membershipDigest"]
                or member.get("role") != job.get("actorRole")):
            return None
        return {"owner": owner, "kind": "project", "id": job["projectId"], "version": project["version"]}

    def _check_authority(self, owner, job):
        check = self._authority(owner, job)
        if check is None:
            if job["status"] not in TERMINAL:
                self._terminal(owner, job, "failed", error={"code": "authority-changed"}, bump_fence=True)
            raise LedgerError("authority-changed")
        return check

    # --- transitions ---------------------------------------------------------
    def _commit(self, owner, before, job, *, extra_writes=(), checks=(), op=None, value=None, reindex=True):
        """One conditional put: job CAS, op entry, due index, quota and caller writes."""
        job, writes = self._prepare(owner, before, job, extra_writes=extra_writes, op=op, value=value,
                                    reindex=reindex)
        return self._put(owner, job, before["version"], extra_writes=writes, checks=list(checks))

    def _prepare(self, owner, before, job, *, extra_writes=(), op=None, value=None, reindex=True):
        """Prepared, uncommitted ledger writes (prepare_finish): the job record plus its index writes."""
        job = copy.deepcopy(job)
        job["clock"] = self.storage.clock()
        if op is not None:
            operation_id, digest = op
            ops = dict(job.get("ops") or {})
            if operation_id not in ops and len(ops) >= job["budget"]["maxCalls"] + 80:
                raise LedgerError("operation-budget")
            entry = {"digest": digest, "version": before["version"] + 1, "status": job["status"]}
            if value is not None:
                entry["value"] = value
            ops[operation_id] = entry
            job["ops"] = ops
        writes = list(extra_writes)
        if reindex:
            writes = [*self._due_writes(owner, before, job), *writes]
        return job, writes

    def _terminal(self, owner, job, status, *, error=None, bump_fence=False, checks=(), op=None, extra=None):
        if job["status"] in TERMINAL:
            raise LedgerError("terminal")
        after = {**job, **(extra or {}), "status": status, "error": error if error is not None else job.get("error")}
        if bump_fence:
            after["fence"] = job["fence"] + 1
        after["cleanup"] = self._cleanup_for(after)
        quotas = self._quota_writes(owner, job["actor"], job["projectId"], remove=job["id"])
        return self._commit(owner, job, after, extra_writes=quotas, checks=checks, op=op)

    def _cleanup_for(self, job):
        cleanup = list(job.get("cleanup") or [])
        for handle_id, handle in (job.get("handles") or {}).items():
            if handle.get("direction") == "out" and handle.get("status") == "open" and handle_id not in cleanup:
                cleanup.append(handle_id)
        return cleanup

    def _projection(self, job):
        view = {key: copy.deepcopy(value) for key, value in job.items() if key not in ("ops", "calls", "attempts")}
        view["attempts"] = copy.deepcopy(job.get("attempts", [])[-1:])
        encoded = json.dumps(view, ensure_ascii=False, default=str).encode()
        if len(encoded) > 64 * 1024:
            view = {key: view[key] for key in ("id", "status", "fence", "attempt", "version", "result", "error")
                    if key in view}
        return view

    # --- operation idempotency (review round 2, N4) --------------------------
    def _op(self, job, method, operation_id, args):
        """Returns (op, replay). A replay returns the recorded outcome without repeating effects."""
        if operation_id is None:
            if not self.offline:
                raise LedgerError("operation-id-required")
            return None, None
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise LedgerError("operation-id-invalid")
        digest = receipt_hash({"method": method, "args": args})
        entry = (job.get("ops") or {}).get(operation_id)
        if entry is not None:
            if entry["digest"] != digest:
                raise LedgerError("operation-changed")
            return (operation_id, digest), {"job": self._projection(job), "value": entry.get("value")}
        if job["status"] in TERMINAL:
            raise LedgerError("terminal")
        return (operation_id, digest), None

    # --- API role ----------------------------------------------------------
    def _admit(self, owner, *, request_key, actor, project_id, operation, model, manifest, admissions,
               backend_config_revision, authorization_expires_at, project_authority, profile=None,
               completion_scope=None, supersedes=None):
        profile = copy.deepcopy(profile or PROFILE_DEFAULT)
        if operation not in OPERATIONS:
            raise LedgerError("unknown-operation")
        if not admissions:
            raise LedgerError("admission-required")
        if (not isinstance(manifest, dict) or set(manifest) != {"ref", "hash"}
                or not isinstance(manifest["hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["hash"])):
            raise LedgerError("manifest-invalid")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
            raise LedgerError("request-key-invalid")
        if type(authorization_expires_at) is not int:
            raise LedgerError("authorization-expired")
        scope = copy.deepcopy(completion_scope or DEFAULT_COMPLETION_SCOPE)
        if (not isinstance(scope, dict) or set(scope) != set(DEFAULT_COMPLETION_SCOPE)
                or any(type(value) is not int or value < 0 for value in scope.values())):
            raise LedgerError("execution-completion-scope")
        limits = {"sourceChecks": min(MAX_SOURCE_CHECKS, TRANSACTION_LIMIT - max(10, scope["nonSourceOperations"])),
                  "sourceBindings": MAX_SOURCE_BINDINGS}
        if scope["sourceChecks"] > limits["sourceChecks"] or scope["sourceBindings"] > limits["sourceBindings"]:
            raise LedgerError("execution-completion-scope", limits=limits)
        now = self.storage.clock()
        if authorization_expires_at <= now:
            raise LedgerError("authorization-expired")
        ref = {"id": profile["id"], "revision": profile["revision"], "hash": profile_hash(profile)}
        input_hash = schema.digest({"actor": actor, "operation": operation, "model": model, "manifest": manifest,
                                    "admissions": admissions, "backendConfigRevision": backend_config_revision,
                                    "profileHash": ref["hash"], "authority": project_authority,
                                    "completionScope": scope, "supersedes": supersedes})
        marker_id = schema.identity("exec-req", owner, actor, request_key)
        marker = self.storage.get(owner, "exec_request", marker_id)
        if marker:
            if marker["inputHash"] != input_hash:          # includes authority: a changed epoch is a changed request
                raise LedgerError("request-changed")
            existing = self._get(owner, marker["jobId"])
            self._check_authority(owner, existing)          # a replayed admit revalidates current authority
            return existing
        project = self.storage.get(owner, "project", project_id)
        members = (project or {}).get("members") if isinstance((project or {}).get("members"), dict) else {}
        member = members.get(actor)
        if (not project or owner != "project:" + str(project_id) or project.get("status") != "active"
                or not isinstance(member, dict) or not isinstance(project_authority, dict)
                or project.get("authorityRevision") != project_authority.get("authorityRevision")
                or schema.digest(members) != project_authority.get("membershipDigest")):
            raise LedgerError("authority-changed")
        job_id = "exec-" + secrets.token_hex(16)
        quotas = self._quota_writes(owner, actor, project_id, add=job_id)   # raises concurrency-actor/-project
        job = {"id": job_id, "task": TASK, "executionSchemaVersion": SCHEMA_VERSION, "requestKey": request_key,
               "inputHash": input_hash, "actor": actor, "actorRole": member.get("role"), "projectId": project_id,
               "operation": operation, "model": model, "backend": "agentcore",
               "backendConfigRevision": backend_config_revision,
               "profile": ref, "profileBody": profile, "manifest": manifest, "admissions": admissions,
               "status": "queued", "fence": 0, "attempt": None, "attempts": [], "calls": [], "stages": [],
               "nonces": [], "transfers": [], "handles": {}, "cleanup": [],
               "budget": {"calls": 0, "maxCalls": profile["maxCallsByOperation"][operation],
                          "tokensReserved": 0, "tokensUsed": 0, "tokenBudget": profile["tokenBudget"]},
               "transferUsage": {"chunks": 0, "bytes": 0},
               "deadlineAt": min(now + profile["deadlineMs"], authorization_expires_at),
               "authorizationExpiresAt": authorization_expires_at, "result": None, "error": None,
               "unknownOutcome": False, "authority": project_authority, "completionScope": scope,
               "supersedes": supersedes, "ops": {}, "clock": now, "dueId": None}
        marker_write = {"owner": owner, "kind": "exec_request",
                        "item": {"id": marker_id, "jobId": job_id, "inputHash": input_hash}, "expected_version": None}
        due = self._due_writes(owner, None, job)
        # The project record's version is a transactional fence: a concurrent membership change aborts admission.
        return self._put(owner, job, None, extra_writes=[marker_write, *quotas, *due],
                         checks=[{"owner": owner, "kind": "project", "id": project_id, "version": project["version"]}])

    def _may_act(self, owner, job, actor):
        if actor == job["actor"]:
            return True
        project = self.storage.get(owner, "project", job["projectId"]) or {}
        member = (project.get("members") or {}).get(actor) if isinstance(project.get("members"), dict) else None
        return isinstance(member, dict) and member.get("role") == "owner"

    def _cancel(self, owner, job_id, *, actor):
        job = self._get(owner, job_id)
        if job["status"] in TERMINAL:
            raise LedgerError("terminal")
        if not self._may_act(owner, job, actor):
            raise LedgerError("forbidden")
        return self._terminal(owner, job, "cancelled", error={"code": "cancelled", "actor": actor}, bump_fence=True)

    def _read(self, owner, job_id):
        return self._projection(self._get(owner, job_id))

    # --- dispatcher role -----------------------------------------------------
    def _allocate(self, owner, job_id, *, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "allocate", operation_id, {"jobId": job_id})
        if replay:
            return replay["job"]
        if job["status"] != "queued":
            raise LedgerError("not-queued")
        check = self._check_authority(owner, job)
        now = self.storage.clock()
        if now >= job["deadlineAt"]:
            self._terminal(owner, job, "expired", error={"code": "deadline"}, checks=[check])
            raise LedgerError("deadline")
        if len(job["attempts"]) >= job["profileBody"]["maxAttempts"]:
            self._terminal(owner, job, "failed", error={"code": "attempts-exhausted"}, checks=[check])
            raise LedgerError("attempts-exhausted")
        fence = job["fence"] + 1
        attempt = {"id": "att-" + secrets.token_hex(16), "fence": fence, "sessionId": "rt-" + secrets.token_hex(20),
                   "leaseExpiresAt": min(now + job["profileBody"]["leaseMs"], job["deadlineAt"]),
                   "heartbeatAt": None, "startedAt": now}
        after = {**job, "status": "dispatched", "fence": fence, "attempt": attempt, "ops": {}}
        return self._commit(owner, job, after, checks=[check], op=op)

    # --- tool role: attempts, leases and receipts ------------------------------
    def _current(self, job, attempt_id, fence, *, statuses=("dispatched", "running"), lease=True):
        attempt = job.get("attempt") or {}
        now = self.storage.clock()
        if (not attempt or attempt.get("id") != attempt_id or type(fence) is not int
                or fence != job["fence"] or attempt.get("fence") != fence or job["status"] not in statuses
                or not now < job["deadlineAt"] or lease and not now < attempt.get("leaseExpiresAt", 0)):
            raise LedgerError("stale-attempt")
        return attempt

    def _claim(self, owner, job_id, attempt_id, fence, *, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "claim", operation_id, {"jobId": job_id, "attemptId": attempt_id, "fence": fence})
        if replay:
            return replay["job"]
        self._current(job, attempt_id, fence)
        if job["status"] != "dispatched":
            raise LedgerError("already-claimed")
        check = self._check_authority(owner, job)
        return self._commit(owner, job, {**job, "status": "running"}, checks=[check], op=op)

    def _heartbeat(self, owner, job_id, attempt_id, fence):
        """Not an op entry: idempotent by nature. Extending a lease never re-indexes (a due sweep rechecks)."""
        job = self._get(owner, job_id)
        attempt = self._current(job, attempt_id, fence)
        now = self.storage.clock()
        extended = {**attempt, "heartbeatAt": now,
                    "leaseExpiresAt": min(now + job["profileBody"]["leaseMs"], job["deadlineAt"])}
        return self._commit(owner, job, {**job, "attempt": extended}, reindex=False)

    def _fail(self, owner, job_id, attempt_id, fence, *, code, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "fail", operation_id, {"jobId": job_id, "attemptId": attempt_id,
                                                          "fence": fence, "code": code})
        if replay:
            return replay["job"]
        self._current(job, attempt_id, fence)
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", code):
            raise LedgerError("code-invalid")
        return self._terminal(owner, job, "failed", error={"code": code}, bump_fence=True, op=op)

    def _allowed_inputs(self, owner, job):
        allowed = {job["manifest"]["ref"]: job["manifest"]["hash"]}
        for row in job.get("stages", []):
            for output in row.get("outputs", []):
                allowed[output["key"]] = output["sha256"]
        for handle in (job.get("handles") or {}).values():
            if handle.get("direction") == "in" and handle.get("attemptId") == (job.get("attempt") or {}).get("id"):
                allowed[handle["key"]] = handle["sha256"]
        return allowed

    def _manifest_priors(self, owner, job):
        ref = job["manifest"]["ref"]
        try:
            if not self.storage.owns_key(owner, ref):
                return []
            data = self.storage.get_blob(ref)
        except (ValueError, FileNotFoundError, RuntimeError):
            return []
        if hashlib.sha256(data).hexdigest() != job["manifest"]["hash"]:
            return []
        try:
            priors = json.loads(data).get("priors", [])
        except (ValueError, AttributeError):
            return []
        return [prior for prior in priors if isinstance(prior, dict)] if isinstance(priors, list) else []

    def _verify_object(self, owner, entry, fields):
        if not isinstance(entry, dict) or set(entry) - set(fields) or {"key", "sha256", "size"} - set(entry):
            return False
        try:
            info = self.storage.blob_info(entry["key"])
            if info["sha256"] != entry["sha256"] or info["size"] != entry["size"]:
                return False
            data = self.storage.get_blob(entry["key"])       # server re-hash of the stored bytes
        except (ValueError, FileNotFoundError, RuntimeError):
            return False
        return hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["size"]

    _RECEIPT_REQUIRED = frozenset({"schemaVersion", "executionId", "attemptId", "fence", "sessionId", "stage", "nonce",
                                   "profileHash", "admissions", "keyId", "signature"})
    _RECEIPT_OPTIONAL = frozenset({"operationId", "objects", "inputs", "outputs", "service", "iat", "exp",
                                   "status", "result", "previous"})

    def _verified_receipt(self, owner, job, receipt, *, attempt=None):
        """Receipt schema v1 checks; returns the stage entry or raises receipt-invalid without writing."""
        attempt = attempt or job["attempt"]
        now = self.storage.clock()
        try:
            valid = (isinstance(receipt, dict) and self._RECEIPT_REQUIRED <= set(receipt)
                     and not set(receipt) - self._RECEIPT_REQUIRED - self._RECEIPT_OPTIONAL
                     and self.verifier.verify(receipt) is True)
        except Exception:  # noqa: BLE001 - a verifier failure is an invalid receipt
            valid = False
        if not valid:
            raise LedgerError("receipt-invalid")
        stages = [row for row in job.get("stages", []) if row.get("attemptId") == attempt["id"]]
        previous = stages[-1]["receiptHash"] if stages else None
        nonce = receipt["nonce"]
        checks = [
            type(receipt["schemaVersion"]) is int and receipt["schemaVersion"] == 1,
            receipt["executionId"] == job["id"], receipt["attemptId"] == attempt["id"],
            type(receipt["fence"]) is int and receipt["fence"] == attempt["fence"] == job["fence"],
            receipt["sessionId"] == attempt["sessionId"], receipt["profileHash"] == job["profile"]["hash"],
            receipt["admissions"] == job["admissions"],
            receipt["stage"] in OPERATIONS[job["operation"]],
            isinstance(nonce, str) and 1 <= len(nonce) <= 128 and nonce not in job.get("nonces", []),
            receipt.get("previous") == previous,
            receipt.get("status", "ok") in ("ok", "failed", "incomplete"),
            len(stages) < 64,
        ]
        if "iat" in receipt or "exp" in receipt:
            iat, exp = receipt.get("iat"), receipt.get("exp")
            checks.append(type(iat) is int and type(exp) is int and iat <= now < exp <= job["deadlineAt"])
        if "result" in receipt:
            checks.append(isinstance(receipt["result"], dict)
                          and len(json.dumps(receipt["result"], ensure_ascii=False, default=str).encode()) <= 8192)
        if "service" in receipt:
            service = receipt["service"]
            checks.append(isinstance(service, dict)
                          and service.get("kind") in ("runtime", "interpreter", "browser", "model")
                          and not set(service) - {"kind", "sessionId", "taskId", "exitCode"})
        if not all(checks):
            raise LedgerError("receipt-invalid")
        allowed = self._allowed_inputs(owner, job)
        inputs = receipt.get("inputs", [])
        if not isinstance(inputs, list) or len(inputs) > 100:
            raise LedgerError("receipt-invalid")
        for entry in inputs:
            if not isinstance(entry, dict) or set(entry) != {"key", "sha256", "size"}:
                raise LedgerError("receipt-invalid")
            if allowed.get(entry["key"]) != entry["sha256"]:
                priors = {prior.get("key"): prior.get("sha256") for prior in self._manifest_priors(owner, job)}
                if priors.get(entry["key"]) != entry["sha256"]:
                    raise LedgerError("receipt-invalid")
        prefix = self.storage.key_for(owner, "job", job["id"], f"out/{attempt['id']}/x")[:-1]
        outputs = [*receipt.get("outputs", []), *receipt.get("objects", [])]
        if not isinstance(receipt.get("outputs", []), list) or not isinstance(receipt.get("objects", []), list) \
                or len(outputs) > 100:
            raise LedgerError("receipt-invalid")
        for entry in outputs:
            if (not isinstance(entry, dict) or not isinstance(entry.get("key"), str)
                    or not entry["key"].startswith(prefix)
                    or not self._verify_object(owner, entry, ("key", "sha256", "size", "role"))):
                raise LedgerError("receipt-invalid")
        return {"stage": receipt["stage"], "receiptHash": receipt_hash(receipt), "nonce": nonce,
                "attemptId": attempt["id"], "status": receipt.get("status", "ok"),
                "result": copy.deepcopy(receipt.get("result", {})),
                "outputs": [{key: entry[key] for key in ("key", "sha256", "size", "role") if key in entry}
                            for entry in outputs]}

    def _stage(self, owner, job_id, attempt_id, fence, receipt, *, operation_id=None):
        job = self._get(owner, job_id)
        operation_id = operation_id if operation_id is not None else (
            receipt.get("operationId") if isinstance(receipt, dict) else None)
        op, replay = self._op(job, "stage", operation_id, {"jobId": job_id, "attemptId": attempt_id, "fence": fence,
                                                           "receipt": receipt})
        if replay:
            return replay["job"]
        self._current(job, attempt_id, fence)
        if isinstance(receipt, dict) and "operationId" in receipt and receipt["operationId"] != operation_id:
            raise LedgerError("receipt-invalid")
        entry = self._verified_receipt(owner, job, receipt)
        check = self._check_authority(owner, job)
        after = {**job, "stages": [*job["stages"], entry], "nonces": [*job.get("nonces", []), entry["nonce"]]}
        return self._commit(owner, job, after, checks=[check], op=op, reindex=False)

    # --- tool role: per-call intent and outcome (RUN-03) -----------------------
    def _intent(self, owner, job_id, attempt_id, fence, *, stage, kind, min_remaining_ms, max_tokens=None,
                operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "intent", operation_id, {
            "jobId": job_id, "attemptId": attempt_id, "fence": fence, "stage": stage, "kind": kind,
            "minRemainingMs": min_remaining_ms, "maxTokens": max_tokens})
        if replay:
            return {"callId": replay["value"], "job": replay["job"]}
        attempt = self._current(job, attempt_id, fence, statuses=("running",))
        stages = OPERATIONS[job["operation"]]
        if kind not in ("model", "interpreter", "browser") or stage not in stages:
            raise LedgerError("call-invalid")
        if kind == "model" and "generate" not in stages:
            raise LedgerError("model-not-allowed")
        if type(min_remaining_ms) is not int or min_remaining_ms < 0:
            raise LedgerError("call-invalid")
        budget, profile, now = dict(job["budget"]), job["profileBody"], self.storage.clock()
        if budget["calls"] >= budget["maxCalls"]:
            raise LedgerError("budget-exhausted")
        if job["deadlineAt"] - now < min_remaining_ms + profile["cleanupReserveMs"]:
            raise LedgerError("deadline-budget")
        reserve = 0
        if kind == "model":
            reserve = 8192 if max_tokens is None else max_tokens
            if type(reserve) is not int or not 0 < reserve <= budget["tokenBudget"]:
                raise LedgerError("call-invalid")
            if budget["tokensUsed"] + budget["tokensReserved"] + reserve > budget["tokenBudget"]:
                raise LedgerError("token-budget")
            self.cost_gate.check()
        call = {"callId": "call-" + secrets.token_hex(16), "stage": stage, "kind": kind, "status": "intent",
                "at": now, "attemptId": attempt["id"], "reserved": reserve}
        budget.update(calls=budget["calls"] + 1, tokensReserved=budget["tokensReserved"] + reserve)
        after = {**job, "calls": [*job["calls"], call], "budget": budget}
        saved = self._commit(owner, job, after, op=op, value=call["callId"], reindex=False)
        return {"callId": call["callId"], "job": saved}

    def _outcome(self, owner, job_id, attempt_id, fence, call_id, *, status, usage=None):
        """Keyed by callId on the call record: the same outcome is idempotent, a different one is refused."""
        job = self._get(owner, job_id)
        attempt = self._current(job, attempt_id, fence, statuses=("running",))
        index = next((i for i, call in enumerate(job["calls"]) if call.get("callId") == call_id), None)
        if index is None or job["calls"][index].get("attemptId") != attempt["id"]:
            raise LedgerError("call-invalid")
        call = job["calls"][index]
        recorded = {"status": status, "usage": usage}
        if call["status"] != "intent":
            if {"status": call["status"], "usage": call.get("usage")} == recorded:
                return self._projection(job)
            raise LedgerError("operation-changed")
        if status not in ("completed", "failed"):
            raise LedgerError("call-invalid")
        if usage is not None and (not isinstance(usage, dict) or set(usage) != {"inputTokens", "outputTokens"}
                                  or any(type(v) is not int or v < 0 for v in usage.values())):
            raise LedgerError("call-invalid")
        actual = sum(usage.values()) if usage else 0
        budget = dict(job["budget"])
        budget.update(tokensReserved=budget["tokensReserved"] - call.get("reserved", 0),
                      tokensUsed=budget["tokensUsed"] + actual)
        calls = list(job["calls"])
        calls[index] = {**call, "status": status, "usage": usage, "doneAt": self.storage.clock()}
        saved = self._commit(owner, job, {**job, "calls": calls, "budget": budget}, reindex=False)
        if call["kind"] == "model" and actual:
            self.cost_gate.record(actual)
        return saved

    # --- tool role: transfers (RUN-05; review rounds 2/7, N6/AA3) ----------------
    def _open_read(self, owner, job, op, *, source, key, sha256, stage=None):
        """Server-side handle over stored bytes: the Lambda hashes them, nothing claimed by the Runtime counts."""
        try:
            if not isinstance(key, str) or not self.storage.owns_key(owner, key):
                raise ValueError("foreign key")
            data = self.storage.get_blob(key)
        except (ValueError, FileNotFoundError, RuntimeError) as error:
            raise LedgerError("transfer-invalid") from error
        if hashlib.sha256(data).hexdigest() != sha256:
            raise LedgerError("transfer-invalid")
        handle_id = "hdl-" + secrets.token_hex(16)
        chunks = max(1, -(-len(data) // CHUNK_BYTES))
        handle = {"direction": "in", "source": source, "key": key, "sha256": sha256, "total": len(data),
                  "chunks": chunks, "attemptId": job["attempt"]["id"], "stage": stage, "status": "open", "read": []}
        saved = self._commit(owner, job, {**job, "handles": {**job["handles"], handle_id: handle}}, op=op,
                             value=handle_id, reindex=False)
        return {"handleId": handle_id, "total": len(data), "sha256": sha256, "chunks": chunks, "job": saved}

    def _replayed_handle(self, replay):
        handle = replay["job"]["handles"].get(replay["value"], {})
        return {"handleId": replay["value"], "total": handle.get("total"), "sha256": handle.get("sha256"),
                "chunks": handle.get("chunks"), "job": replay["job"]}

    def _open_manifest(self, owner, job_id, attempt_id, fence, *, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "open_manifest", operation_id, {"jobId": job_id, "attemptId": attempt_id,
                                                                   "fence": fence})
        if replay:
            return self._replayed_handle(replay)
        self._current(job, attempt_id, fence, statuses=("running",))
        return self._open_read(owner, job, op, source="manifest", key=job["manifest"]["ref"],
                               sha256=job["manifest"]["hash"])

    def _open_prior(self, owner, job_id, attempt_id, fence, *, ref, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "open_prior", operation_id, {"jobId": job_id, "attemptId": attempt_id,
                                                                "fence": fence, "ref": ref})
        if replay:
            return self._replayed_handle(replay)
        self._current(job, attempt_id, fence, statuses=("running",))
        prior = next((row for row in self._manifest_priors(owner, job) if row.get("key") == ref), None)
        if (prior is None or prior.get("sourceKind") not in ("run-round", "release")
                or not callable(self.prior_authority)):
            raise LedgerError("transfer-invalid")
        try:
            current = self.prior_authority(owner, job, copy.deepcopy(prior)) is True
        except Exception:  # noqa: BLE001 - an authority failure is a refusal
            current = False
        if not current:
            raise LedgerError("transfer-invalid")
        return self._open_read(owner, job, op, source="prior", key=prior["key"], sha256=prior.get("sha256"))

    def _open_input(self, owner, job_id, attempt_id, fence, *, decision_id, stage, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "open_input", operation_id, {"jobId": job_id, "attemptId": attempt_id,
                                                                "fence": fence, "decisionId": decision_id,
                                                                "stage": stage})
        if replay:
            return self._replayed_handle(replay)
        self._current(job, attempt_id, fence, statuses=("running",))
        admission = next((row for row in job["admissions"] if row.get("decisionId") == decision_id), None)
        if admission is None or stage not in OPERATIONS[job["operation"]] or not callable(self.input_resolver):
            raise LedgerError("transfer-invalid")
        try:
            blob = self.input_resolver(owner, copy.deepcopy(admission))
        except Exception:  # noqa: BLE001
            blob = None
        if not isinstance(blob, dict) or not isinstance(blob.get("key"), str):
            raise LedgerError("transfer-invalid")
        return self._open_read(owner, job, op, source="input", key=blob["key"], sha256=blob.get("sha256"),
                               stage=stage)

    def _transfer_budget(self, job, chunks, size):
        usage = dict(job.get("transferUsage") or {"chunks": 0, "bytes": 0})
        if usage["chunks"] + chunks > MAX_TRANSFER_CHUNKS or usage["bytes"] + size > MAX_TRANSFER_BYTES:
            raise LedgerError("transfer-invalid")
        usage.update(chunks=usage["chunks"] + chunks, bytes=usage["bytes"] + size)
        return usage

    def _handle(self, job, handle_id, direction):
        handle = (job.get("handles") or {}).get(handle_id) if isinstance(handle_id, str) else None
        if (not handle or handle.get("direction") != direction or handle.get("status") != "open"
                or handle.get("attemptId") != (job.get("attempt") or {}).get("id")):
            raise LedgerError("transfer-invalid")
        return handle

    def _read_chunk(self, owner, job_id, attempt_id, fence, handle_id, index):
        job = self._get(owner, job_id)
        self._current(job, attempt_id, fence, statuses=("running",))
        handle = self._handle(job, handle_id, "in")
        if type(index) is not int or not 0 <= index < handle["chunks"]:
            raise LedgerError("transfer-invalid")
        offset = index * CHUNK_BYTES
        try:
            data = self.storage.get_blob(handle["key"], offset, min(CHUNK_BYTES, handle["total"] - offset))
        except (ValueError, FileNotFoundError) as error:
            raise LedgerError("transfer-invalid") from error
        digest = hashlib.sha256(data).hexdigest()
        if index not in handle["read"]:
            usage = self._transfer_budget(job, 1, len(data))
            handles = {**job["handles"], handle_id: {**handle, "read": [*handle["read"], index]}}
            self._commit(owner, job, {**job, "handles": handles, "transferUsage": usage}, reindex=False)
        return {"index": index, "data": base64.b64encode(data).decode(), "sha256": digest}

    def _open_output(self, owner, job_id, attempt_id, fence, *, stage, name, total, sha256, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "open_output", operation_id, {
            "jobId": job_id, "attemptId": attempt_id, "fence": fence, "stage": stage, "name": name,
            "total": total, "sha256": sha256})
        if replay:
            return self._replayed_handle(replay)
        attempt = self._current(job, attempt_id, fence, statuses=("running",))
        if (not isinstance(name, str) or not _OUTPUT_NAME.fullmatch(name) or ".." in name
                or stage not in OPERATIONS[job["operation"]] or type(total) is not int or not 0 < total
                or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)):
            raise LedgerError("transfer-invalid")
        chunks = -(-total // CHUNK_BYTES)
        self._transfer_budget(job, chunks, total)          # the declared size must fit the remaining budget
        key = self.storage.key_for(owner, "job", job_id, f"out/{attempt['id']}/{stage}/{name}")
        if any(h.get("key") == key and h.get("direction") == "out" for h in job["handles"].values()):
            raise LedgerError("transfer-invalid")
        handle_id = "hdl-" + secrets.token_hex(16)
        handle = {"direction": "out", "key": key, "stage": stage, "name": name, "total": total, "sha256": sha256,
                  "chunks": chunks, "attemptId": attempt["id"], "status": "open", "parts": [], "partKeys": []}
        saved = self._commit(owner, job, {**job, "handles": {**job["handles"], handle_id: handle}}, op=op,
                             value=handle_id, reindex=False)
        return {"handleId": handle_id, "key": key, "total": total, "sha256": sha256, "chunks": chunks, "job": saved}

    def _write_chunk(self, owner, job_id, attempt_id, fence, handle_id, index, data, *, operation_id=None):
        """Idempotent by (handleId, index, chunkHash); not an op entry (review round 3, N6)."""
        job = self._get(owner, job_id)
        self._current(job, attempt_id, fence, statuses=("running",))
        handle = self._handle(job, handle_id, "out")
        try:
            raw = base64.b64decode(data, validate=True) if isinstance(data, str) else None
        except ValueError:
            raw = None
        if raw is None or not 0 < len(raw) <= CHUNK_BYTES or type(index) is not int:
            raise LedgerError("transfer-invalid")
        digest = hashlib.sha256(raw).hexdigest()
        if index < len(handle["parts"]):
            if handle["parts"][index] == digest:
                return self._projection(job)
            raise LedgerError("transfer-invalid")
        if index != len(handle["parts"]) or index >= handle["chunks"]:
            raise LedgerError("transfer-invalid")
        usage = self._transfer_budget(job, 1, len(raw))
        # Parts live under the job's temporary "parts/" prefix, which the bucket lifecycle expires.
        part_key = self.storage.key_for(owner, "job", job_id,
                                        f"parts/out/{handle['attemptId']}/{handle['stage']}/{handle['name']}.part{index:04d}")
        try:
            self.storage.put_blob_once(part_key, raw, "application/octet-stream")
        except Conflict as error:
            raise LedgerError("transfer-invalid") from error
        handles = {**job["handles"], handle_id: {**handle, "parts": [*handle["parts"], digest],
                                                 "partKeys": [*handle["partKeys"], part_key]}}
        return self._commit(owner, job, {**job, "handles": handles, "transferUsage": usage}, reindex=False)

    def _close_output(self, owner, job_id, attempt_id, fence, handle_id, *, operation_id=None):
        job = self._get(owner, job_id)
        op, replay = self._op(job, "close_output", operation_id, {"jobId": job_id, "attemptId": attempt_id,
                                                                  "fence": fence, "handleId": handle_id})
        if replay:
            return replay["job"]
        self._current(job, attempt_id, fence, statuses=("running",))
        handle = self._handle(job, handle_id, "out")
        try:
            data = b"".join(self.storage.get_blob(key) for key in handle["partKeys"])
        except (ValueError, FileNotFoundError) as error:
            raise LedgerError("transfer-invalid") from error
        if (len(handle["parts"]) != handle["chunks"] or len(data) != handle["total"]
                or hashlib.sha256(data).hexdigest() != handle["sha256"]):
            raise LedgerError("transfer-invalid")
        try:
            info = self.storage.put_blob_once(handle["key"], data, "application/octet-stream")
        except Conflict as error:
            raise LedgerError("transfer-invalid") from error
        output = {"handleId": handle_id, "key": handle["key"], "sha256": info["sha256"], "size": info["size"],
                  "stage": handle["stage"], "attemptId": handle["attemptId"]}
        handles = {**job["handles"], handle_id: {**handle, "status": "closed"}}
        saved = self._commit(owner, job, {**job, "handles": handles, "transfers": [*job["transfers"], output]},
                             op=op, reindex=False)
        for key in handle["partKeys"]:
            try:
                self.storage.s3().delete_object(Bucket=self.storage.bucket, Key=key)
            except Exception:  # noqa: BLE001 - the lifecycle rule expires the temporary parts
                pass
        return saved

    # --- reconciler role: watchdog sweep -------------------------------------
    def _sweep(self, owner, job_id, *, operation_id=None):
        """The watchdog never sets succeeded/needs_changes."""
        job = self._get(owner, job_id)
        now, profile = self.storage.clock(), job["profileBody"]
        if job["status"] in TERMINAL:
            return self._cleanup(owner, job)
        unknown = any(call.get("status") == "intent" for call in job["calls"])
        if now >= job["deadlineAt"]:
            return self._terminal(owner, job, "expired", error={"code": "deadline"}, bump_fence=True,
                                  extra={"unknownOutcome": job["unknownOutcome"] or unknown})
        if job["status"] == "recovery_required":
            if now >= job["recoveryAt"] + profile["recoveryWindowMs"]:
                return self._terminal(owner, job, "failed", error={"code": "recovery-window"}, bump_fence=True,
                                      extra={"unknownOutcome": job["unknownOutcome"] or unknown})
        elif job["status"] in ("dispatched", "running"):
            attempt = job["attempt"] or {}
            hung = any(call.get("status") == "intent" and call["at"] + profile["leaseMs"] <= now
                       for call in job["calls"])
            if now >= attempt.get("leaseExpiresAt", 0) or hung:
                return self._commit(owner, job, {**job, "status": "recovery_required", "recoveryAt": now,
                                                 "unknownOutcome": job["unknownOutcome"] or unknown})
        due = self.storage.get(DUE_OWNER, "exec_due", job["dueId"]) if job.get("dueId") else None
        if due is None or due.get("status") != "pending" or due["dueAt"] <= now:
            job_after = dict(job)
            writes = self._due_writes(owner, job, job_after, force=True)
            return self._put(owner, {**job_after, "clock": now}, job["version"], extra_writes=writes)
        return job

    def _cleanup(self, owner, job):
        """Delete fenced output parts of a terminal job, then drop its due entry."""
        if not job.get("cleanup"):
            return job
        for handle_id in job["cleanup"]:
            handle = (job.get("handles") or {}).get(handle_id) or {}
            for index in range(len(handle.get("partKeys", []))):
                try:
                    self.storage.s3().delete_object(Bucket=self.storage.bucket, Key=handle["partKeys"][index])
                except Exception:  # noqa: BLE001 - a later sweep retries
                    return job
        return self._commit(owner, job, {**job, "cleanup": []})

    # --- API role: retry --------------------------------------------------------
    def _retry(self, owner, job_id, *, actor, acknowledge_unknown_outcome):
        job = self._get(owner, job_id)
        retryable = (job["status"] == "recovery_required"
                     or job["status"] in ("failed", "expired") and job.get("unknownOutcome"))
        if not retryable:
            raise LedgerError("not-retryable")
        now = self.storage.clock()
        if now >= job["deadlineAt"] or now >= job["authorizationExpiresAt"]:
            raise LedgerError("retry-expired")
        if not self._may_act(owner, job, actor):
            raise LedgerError("forbidden")
        if job.get("unknownOutcome") and acknowledge_unknown_outcome is not True:
            raise LedgerError("acknowledge-required")
        check = self._check_authority(owner, job)
        quotas = (self._quota_writes(owner, job["actor"], job["projectId"], add=job["id"])
                  if job["status"] in TERMINAL else [])
        previous = job.get("attempt")
        archived = list(job["attempts"])
        if previous:
            archived.append({**previous, "stages": [row["receiptHash"] for row in job["stages"]
                                                    if row.get("attemptId") == previous["id"]], "late": []})
        calls = [{**call, "status": "unknown"} if call.get("status") == "intent" else call for call in job["calls"]]
        after = {**job, "status": "queued", "fence": job["fence"] + 1, "attempt": None, "attempts": archived,
                 "stages": [], "calls": calls, "error": None, "recoveryAt": None,
                 "retriedBy": {"actor": actor, "at": now, "acknowledgedUnknownOutcome": bool(job.get("unknownOutcome"))}}
        after["cleanup"] = self._cleanup_for(job)
        return self._commit(owner, job, after, extra_writes=quotas, checks=[check])

    # --- tool role: coupled completion (RUN-02, RUN-04) -------------------------
    def _terminal_status(self, job, stages):
        """TERMINAL_RULES[operation]: a closed, profile-versioned table (review round 3, F4)."""
        last = {}
        for row in stages:
            last[row["stage"]] = row
        required = OPERATIONS[job["operation"]]
        if any(last[name]["status"] != "ok" for name in required):
            return None
        result = {name: last[name].get("result") or {} for name in required}
        operation = job["operation"]
        if operation == "source.analyze":
            return "succeeded" if "coverage" in result["analyze"] else None
        if operation in ("design.extract", "intake.transcribe"):
            issues = result["verify"].get("issues")
            if issues == []:
                return "succeeded"
            return "needs_changes" if isinstance(issues, list) and issues else None
        if operation in ("design.generate", "design.edit"):
            verify = result["verify"]
            if verify.get("verdict") == "pass" and verify.get("approvable") is True:
                return "succeeded"
            if verify.get("verdict") in ("fail", "blocked") or verify.get("approvable") is False:
                return "needs_changes"
            return None
        if operation == "design.compose":
            verify = result["verify"]
            if verify.get("reviewer") != "deterministic" or any(call.get("kind") == "model" for call in job["calls"]):
                return None
            hashes, evidence = verify.get("compositionHashes"), verify.get("judgeEvidence")
            if not isinstance(hashes, list) or not isinstance(evidence, dict) or not isinstance(verify.get("passed"), bool):
                return None
            covered = all(isinstance(evidence.get(h), str) and evidence[h] for h in hashes)
            return "succeeded" if verify["passed"] and covered and hashes else "needs_changes"
        if operation == "design.release":
            approved = self._manifest_document(job).get("approved") or {}
            diff = result["browser"].get("visualDiff")
            if (approved and result["compile"].get("sourceHash") == approved.get("sourceHash")
                    and result["compile"].get("bundleHash") == approved.get("bundleHash")
                    and isinstance(diff, (int, float)) and not isinstance(diff, bool) and diff <= 0.02):
                return "succeeded"
            return None
        return None

    def _manifest_document(self, job):
        ref = job["manifest"]["ref"]
        try:
            data = self.storage.get_blob(ref)
        except (ValueError, FileNotFoundError, RuntimeError):
            return {}
        if hashlib.sha256(data).hexdigest() != job["manifest"]["hash"]:
            return {}
        try:
            document = json.loads(data)
        except ValueError:
            return {}
        return document if isinstance(document, dict) else {}

    def _check_result_manifest(self, job, stages, result):
        outputs = {entry["key"]: entry["sha256"] for row in stages for entry in row.get("outputs", [])}
        ref, digest = result.get("manifestRef"), result.get("manifestHash")
        if not isinstance(ref, str) or outputs.get(ref) != digest:
            raise LedgerError("receipt-invalid")
        try:
            data = self.storage.get_blob(ref)
            document = json.loads(data)
        except (ValueError, FileNotFoundError, RuntimeError) as error:
            raise LedgerError("receipt-invalid") from error
        if hashlib.sha256(data).hexdigest() != digest or not isinstance(document, dict):
            raise LedgerError("receipt-invalid")
        listed = [*document.get("files", []), *document.get("objects", [])]
        if not isinstance(document.get("files", []), list) or not isinstance(document.get("objects", []), list):
            raise LedgerError("receipt-invalid")
        for entry in listed:
            if not isinstance(entry, dict) or outputs.get(entry.get("key")) != entry.get("sha256"):
                raise LedgerError("receipt-invalid")

    def _complete(self, owner, before, job, *, status, result, op, stage_completion):
        """Checks, prepares and submits the terminal transition plus staged writes in ONE transaction."""
        if status not in ("succeeded", "needs_changes"):
            raise LedgerError("status-inconsistent")
        if not isinstance(result, dict) or set(result) != {"manifestRef", "manifestHash", "receipts"}:
            raise LedgerError("receipt-invalid")
        attempt = job["attempt"]
        stages = [row for row in job["stages"] if row.get("attemptId") == attempt["id"]]
        if set(OPERATIONS[job["operation"]]) - {row["stage"] for row in stages}:
            raise LedgerError("stages-incomplete")
        if result["receipts"] != [row["receiptHash"] for row in stages]:
            raise LedgerError("receipt-invalid")
        if any(h.get("direction") == "out" and h.get("status") == "open" and h.get("attemptId") == attempt["id"]
               for h in job["handles"].values()):
            raise LedgerError("transfers-incomplete")
        if self._terminal_status(job, stages) != status:
            raise LedgerError("status-inconsistent")
        self._check_result_manifest(job, stages, result)
        check = self._check_authority(owner, before)
        after = {**job, "status": status, "result": copy.deepcopy(result), "completedAt": self.storage.clock()}
        after["cleanup"] = self._cleanup_for(after)
        quotas = self._quota_writes(owner, job["actor"], job["projectId"], remove=job["id"])
        prepared_job, ledger_writes = self._prepare(owner, before, after, extra_writes=quotas, op=op)
        writes = [{"owner": owner, "kind": "job", "item": prepared_job, "expected_version": before["version"]},
                  *ledger_writes]
        checks = [check]
        if stage_completion is not None:
            staged = stage_completion({"job": copy.deepcopy(prepared_job), "writes": copy.deepcopy(writes),
                                       "checks": copy.deepcopy(checks)})
            if not isinstance(staged, dict) or set(staged) - {"writes", "checks"}:
                raise LedgerError("completion-invalid")
            writes.extend(staged.get("writes", []))
            checks.extend(staged.get("checks", []))
        if len(writes) + len(checks) > TRANSACTION_LIMIT:
            raise LedgerError("execution-completion-scope", limits={"transaction": TRANSACTION_LIMIT})
        try:
            return self.storage.put_many(writes, checks, retry_conflicts=False, _writer=_WRITER)[0]
        except _storage.TransactionContention:
            raise
        except Conflict as error:
            raise LedgerError("conflict") from error
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - an unknown transport outcome is never resubmitted blindly
            from botocore.exceptions import BotoCoreError
            if not isinstance(error, BotoCoreError):
                raise LedgerError("unavailable") from error
            return self._unknown_outcome(owner, before, op)

    def _unknown_outcome(self, owner, before, op):
        current = self.storage.get(owner, "job", before["id"])
        if (current and current["version"] == before["version"] + 1 and current["status"] in TERMINAL
                and (op is None or op[0] in (current.get("ops") or {}))):
            return current
        if current and current["version"] == before["version"] and current["status"] in ("dispatched", "running"):
            try:
                self._commit(owner, current, {**current, "status": "recovery_required",
                                              "recoveryAt": self.storage.clock(), "unknownOutcome": True})
            except LedgerError:
                pass
        raise LedgerError("unknown-outcome")

    def _late(self, owner, job, attempt_id, result):
        """A superseded attempt's completion is kept only as a non-current diagnostic."""
        index = next((i for i, row in enumerate(job["attempts"]) if row.get("id") == attempt_id), None)
        if index is None:
            return
        receipts = result.get("receipts", []) if isinstance(result, dict) else []
        attempts = copy.deepcopy(job["attempts"])
        late = attempts[index].setdefault("late", [])
        if len(late) >= 5:
            return
        late.append({"at": self.storage.clock(), "receipts": [r for r in receipts if isinstance(r, str)][:32]})
        try:
            self._commit(owner, job, {**job, "attempts": attempts}, reindex=False)
        except LedgerError:
            pass

    def _finish(self, owner, job_id, attempt_id, fence, *, status, result, operation_id=None, stage_completion=None):
        args = {"jobId": job_id, "attemptId": attempt_id, "fence": fence, "status": status, "result": result}
        for attempt_number in range(PROFILE_DEFAULT["completionRetries"] + 1):
            job = self._get(owner, job_id)
            op, replay = self._op(job, "finish", operation_id, args)
            if replay:
                return replay["job"]
            try:
                self._current(job, attempt_id, fence)
            except LedgerError:
                self._late(owner, job, attempt_id, result)
                raise
            try:
                return self._complete(owner, job, job, status=status, result=result, op=op,
                                      stage_completion=stage_completion)
            except _storage.TransactionContention as error:
                # Proven contention wrote nothing: retry with fresh actor, deadline and attempt checks.
                if attempt_number >= job["profileBody"]["completionRetries"]:
                    raise LedgerError("conflict") from error
        raise LedgerError("conflict")

    # --- reconciler role -----------------------------------------------------
    def _resolve_orphan(self, due):
        return _resolve_orphan(self.storage, due)

    def _run_due(self, *, limit=100):
        """Scheduled pass: orphan reports and due execution jobs, discovered without supplied ids."""
        now, handled, cursor = self.storage.clock(), 0, None
        while handled < limit:
            page = self.storage.list_page(DUE_OWNER, "exec_due", 100, cursor)
            for due in page["items"]:
                if due.get("dueAt", now + 1) > now:
                    return handled
                if due.get("status") != "pending":
                    continue
                try:
                    if due.get("type") == "report":
                        _resolve_orphan(self.storage, due)
                    elif due.get("type") == "job":
                        self._run_job_due(due)
                except (Conflict, LedgerError):
                    continue
                handled += 1
                if handled >= limit:
                    return handled
            cursor = page.get("cursor")
            if not cursor:
                return handled
        return handled

    def _run_job_due(self, due):
        owner, job_id = due["targetOwner"], (due.get("ref") or {}).get("id")
        try:
            job = self._get(owner, job_id)
        except LedgerError:
            job = None
        if job is None or job.get("dueId") != due["id"]:
            self.storage.put_many([{"owner": DUE_OWNER, "kind": "exec_due", "item": {**due, "status": "done"},
                                    "expected_version": due["version"]}], retry_conflicts=False, _writer=_WRITER)
            return None
        return self._sweep(owner, job_id)

    def _reconcile(self, owner, job_id, attempt_id, *, receipts, status, result, operation_id=None,
                   stage_completion=None):
        """Complete a recovery_required attempt from receipts verified exactly as stage verifies them."""
        args = {"jobId": job_id, "attemptId": attempt_id, "receipts": receipts, "status": status, "result": result}
        for attempt_number in range(PROFILE_DEFAULT["completionRetries"] + 1):
            job = self._get(owner, job_id)
            op, replay = self._op(job, "reconcile", operation_id, args)
            if replay:
                return replay["job"]
            if self.storage.clock() >= job["deadlineAt"]:
                raise LedgerError("deadline")
            attempt = job.get("attempt") or {}
            if (job["status"] != "recovery_required" or attempt.get("id") != attempt_id
                    or attempt.get("fence") != job["fence"]):
                raise LedgerError("stale-attempt")
            if not isinstance(receipts, list) or len(receipts) > 16:
                raise LedgerError("receipt-invalid")
            staged = copy.deepcopy(job)
            for receipt in receipts:
                entry = self._verified_receipt(owner, staged, receipt, attempt=attempt)
                staged["stages"].append(entry)
                staged["nonces"].append(entry["nonce"])
            try:
                return self._complete(owner, job, staged, status=status, result=result, op=op,
                                      stage_completion=stage_completion)
            except _storage.TransactionContention as error:
                if attempt_number >= job["profileBody"]["completionRetries"]:
                    raise LedgerError("conflict") from error
        raise LedgerError("conflict")
