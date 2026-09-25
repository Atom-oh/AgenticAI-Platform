"""Owner-scoped metadata and private blobs for the designer workspace.

Metadata creation and replacement are conditional; callers retain the version
returned by put(). S3 clients and DynamoDB resources are created lazily and may
be replaced by dependency-injected fakes without configuring AWS credentials.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import random
import re
import time
from decimal import Decimal

KINDS = frozenset({"asset", "contract", "job", "run", "project", "membership", "ontology_cursor",
                   "product", "guideline", "ontology", "comment", "batch", "release", "gitexport",
                   "wb_source", "wb_batch", "wb_index", "wb_change", "wb_task", "wb_skill",
                   "wb_artifact", "wb_pension", "wb_report", "wb_tool",
                   "document", "docrevision", "docbinding", "docaudit", "docanalysis", "docdecision",
                   "exec_report", "exec_request", "exec_quota", "exec_due"})
MAX_BLOB_BYTES = 50 * 1024 * 1024
MAX_RECORD_BYTES = 350_000
JOB_RETENTION_SECONDS = 30 * 24 * 60 * 60
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+\Z")


class Conflict(Exception):
    """A conditional write lost a race or would replace immutable bytes."""


class TransactionContention(Conflict):
    """A canceled transaction whose only failures were proven TransactionConflict contention (wrote nothing)."""


class ReservedRecord(Conflict):
    """A legacy writer attempted to mutate a new-execution record (platform-execution/1)."""

    def __init__(self, kind, identifier, reason):
        super().__init__("The resource belongs to a separately governed execution")
        self.kind, self.id, self.reason = kind, identifier, reason


RESERVED_TASKS = frozenset({"agentcore-execution"})
# Kinds whose records name a job through ``jobId``; a non-ledger write reads the stored linkage.
LINKED_KINDS = frozenset({"wb_artifact", "wb_batch", "wb_skill", "docrevision", "docanalysis",
                          "release", "gitexport", "run", "asset"})
# Human approval/export writes (review round 3, F1): never jobs, workbench or document kinds.
HUMAN_KINDS = frozenset({"run", "release", "gitexport", "design"})
_HUMAN_MODULES = frozenset({"workspace.http", "workspace.releases", "workspace.git_service", "workspace.design_api"})
_HUMAN_BLOCKED_STATUS = frozenset({"failed", "needs_changes", "completed"})
# Partition of the due-time-ordered work index read by the IAM-only reconciler.
DUE_OWNER = "execution:due"
EXECUTION_JOB_PREFIX = "exec-"
_LEDGER_WRITER = object()
_HUMAN_WRITER = object()


def is_reserved(record):
    return isinstance(record, dict) and (record.get("task") in RESERVED_TASKS
                                         or "executionSchemaVersion" in record or "executionId" in record)


def _ledger_writer():
    import sys
    if sys._getframe(1).f_globals.get("__name__") != "workspace.execution_ledger":
        raise PermissionError("Only the execution ledger may write execution records")
    return _LEDGER_WRITER


def _human_writer():
    """Token for human approval, release creation and Git export writes (code-review boundary)."""
    import sys
    if sys._getframe(1).f_globals.get("__name__") not in _HUMAN_MODULES:
        raise PermissionError("Only human approval and export paths may use the human writer")
    return _HUMAN_WRITER


def due_id(due_at, *parts):
    """Due-time-ordered exec_due identifier; the sort key order is the due order."""
    if type(due_at) is not int or due_at < 0:
        raise ValueError("Invalid due time")
    digest = hashlib.sha256(json.dumps(list(parts), sort_keys=True, default=str).encode()).hexdigest()[:32]
    return f"due-{due_at:015d}-{digest}"


def _owner_hash(owner: str) -> str:
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 256:
        raise ValueError("Invalid owner")
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _identity(kind: str, identifier: str) -> None:
    if kind not in KINDS or not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError("Invalid resource identifier")


def key_for(owner: str, kind: str, id: str, suffix: str) -> str:
    _identity(kind, id)
    if not isinstance(suffix, str) or not suffix or len(suffix) > 500:
        raise ValueError("Invalid blob suffix")
    if any(part in ("", ".", "..") or not _SEGMENT.fullmatch(part) for part in suffix.split("/")):
        raise ValueError("Invalid blob suffix")
    return f"workspace/{_owner_hash(owner)}/{kind}/{id}/{suffix}"


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _marshal(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite metadata number")
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _marshal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_marshal(item) for item in value]
    return value


def _record(item):
    return {key: _plain(value) for key, value in item.items() if key not in ("pk", "sk")}


class Storage:
    key_for = staticmethod(key_for)

    def __init__(self, table=None, s3=None, bucket=None, table_name=None, clock=None, single_attempt=False):
        self._table, self._s3 = table, s3
        # The execution ledger and its facades use exactly one wire attempt per request (review round 8, AB1):
        # an SDK retry of a timed-out transaction would bypass the facade's fresh authority checks.
        self.single_attempt = single_attempt
        self.bucket = bucket if bucket is not None else os.environ.get("WORKSPACE_BUCKET", "")
        self.table_name = table_name if table_name is not None else os.environ.get("WORKSPACE_TABLE", "")
        self.clock = clock or (lambda: int(time.time() * 1000))

    @staticmethod
    def _config():
        from botocore.config import Config
        return Config(connect_timeout=5, read_timeout=30, retries={"mode": "standard", "total_max_attempts": 3})

    def _client_config(self):
        from botocore.config import Config
        attempts = 1 if getattr(self, "single_attempt", False) else 3
        return Config(connect_timeout=5, read_timeout=30, retries={"mode": "standard", "total_max_attempts": attempts})

    def table(self):
        if self._table is None:
            if not self.table_name:
                raise RuntimeError("Workspace metadata storage is not configured")
            import boto3
            self._table = boto3.resource("dynamodb", config=self._client_config()).Table(self.table_name)
        return self._table

    def s3(self):
        if not self.bucket:
            raise RuntimeError("Workspace blob storage is not configured")
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3", config=self._client_config())
        return self._s3

    @staticmethod
    def owns_key(owner: str, key: str) -> bool:
        return isinstance(key, str) and key.startswith(f"workspace/{_owner_hash(owner)}/")

    @staticmethod
    def _key(owner, kind, identifier):
        _identity(kind, identifier)
        return {"pk": f"owner#{_owner_hash(owner)}", "sk": f"{kind}#{identifier}"}

    def get(self, owner: str, kind: str, id: str) -> dict | None:
        response = self.table().get_item(Key=self._key(owner, kind, id), ConsistentRead=True)
        return _record(response["Item"]) if response.get("Item") else None

    def _prepare(self, owner, kind, item, expected_version, now, *, writer=None, proposed_jobs=frozenset(),
                 fences=None):
        from boto3.dynamodb.conditions import Attr
        if not isinstance(item, dict):
            raise ValueError("Invalid metadata record")
        identifier = item.get("id")
        keys = self._key(owner, kind, identifier)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise ValueError("Invalid expected version")
        previous = None
        if expected_version is not None:
            previous = self.get(owner, kind, identifier)
            if not previous or previous["version"] != expected_version:
                raise Conflict("The resource has changed")
        ledger = writer is _LEDGER_WRITER
        # Human approval/export writes are evaluated BEFORE the reserved-record refusal (review round 4, F1):
        # a design-linked run/release may itself carry execution markers written by the ledger completion.
        human = (writer is _HUMAN_WRITER and kind in HUMAN_KINDS
                 and not (item.get("status") in _HUMAN_BLOCKED_STATUS
                          and (previous is None or previous.get("status") != item.get("status"))))
        if not ledger and not human:
            self._guard(owner, kind, identifier, item, previous, proposed_jobs, fences)
        data = {key: copy.deepcopy(value) for key, value in item.items()
                if key not in ("pk", "sk", "owner", "sub", "ttl")}
        data.update(id=identifier, version=expected_version + 1 if expected_version else 1,
                    createdAt=previous["createdAt"] if previous else now, updatedAt=now)
        if kind == "project":
            def authority(record):
                return ({actor: member["role"] for actor, member in record.get("members", {}).items()},
                        record.get("status"), record.get("archived", False))
            data["authorityRevision"] = ((previous.get("authorityRevision", 0) +
                int(authority(previous) != authority(data))) if previous else 1)
        if kind == "job":
            data["ttl"] = now // 1000 + JOB_RETENTION_SECONDS
        if kind == "ontology_cursor":
            expiry = data.get("expiresAt")
            if type(expiry) is not int or not now < expiry <= now + 300000:
                raise ValueError("An ontology cursor requires a bounded expiry")
            data["ttl"] = expiry // 1000
        data.setdefault("status", {"asset": "uploading", "contract": "draft", "job": "queued",
                                  "run": "queued", "product": "draft"}.get(kind, "active"))
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, default=str).encode()
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("Metadata exceeds the storage limit; put large evidence in blob storage")
        if expected_version:
            condition = Attr("version").eq(expected_version)
            if not ledger and not human:
                # DB-level fence: a reserved marker that appears after the pre-read still rejects the write.
                condition = condition & Attr("executionSchemaVersion").not_exists() & Attr("executionId").not_exists()
        else:
            condition = Attr("pk").not_exists()
        return data, keys, condition

    def _guard(self, owner, kind, identifier, item, previous, proposed_jobs, fences=None):
        """platform-execution/1 chokepoint: legacy writers never mutate reserved or reserved-linked records.

        Every stored linked job the guard reads is appended to ``fences`` as (owner, id, version-or-None); the
        write then carries a transactional check that the job is unchanged (or still absent), so a job that
        acquires a reserved discriminator after this read aborts the artifact mutation (RUN-01).
        """
        def read_job(candidate):
            linked = self.get(owner, "job", candidate)
            if fences is not None and (owner, candidate) not in proposed_jobs:
                fences.append((owner, candidate, linked["version"] if linked else None))
            return linked

        if is_reserved(item) or is_reserved(previous):
            raise ReservedRecord(kind, identifier, "reserved-item" if is_reserved(item) else "reserved-record")
        if kind not in LINKED_KINDS:
            return
        stored_link = (previous or {}).get("jobId")
        link = stored_link if stored_link is not None else item.get("jobId")
        if stored_link is not None and item.get("jobId") not in (None, stored_link):
            # Relinking between two legacy jobs (e.g. skill proposal -> validation) stays legacy.
            # Detaching from, or attaching to, an execution job is a mismatched linkage.
            for candidate in (stored_link, item["jobId"]):
                if (not isinstance(candidate, str) or not _ID.fullmatch(candidate)
                        or candidate.startswith(EXECUTION_JOB_PREFIX) or is_reserved(read_job(candidate))):
                    raise ReservedRecord(kind, identifier, "linkage-mismatch")
            return
        if link is None:
            return
        if not isinstance(link, str) or not _ID.fullmatch(link):
            raise ReservedRecord(kind, identifier, "unknown-linkage")
        # The stored job is authoritative; a caller-supplied job dict is never trusted.
        linked = read_job(link)
        if is_reserved(linked) or linked is None and link.startswith(EXECUTION_JOB_PREFIX):
            raise ReservedRecord(kind, identifier, "linked-reserved-job")
        # A missing legacy-format job (not an ``exec-`` id) on an unmarked record is legacy linkage, e.g. a job
        # deleted by its retention TTL (PR #27 review 4, #1): human review and legacy repair continue unchanged.
        # The absence is fenced above, so a reserved job created at that id aborts this write.

    def put(self, owner: str, kind: str, item: dict, expected_version: int | None = None, *, _writer=None) -> dict:
        fences = []
        try:
            data, keys, condition = self._prepare(owner, kind, item, expected_version, self.clock(), writer=_writer,
                                                  fences=fences)
        except ReservedRecord as refused:
            self._report(owner, refused)
            raise
        if fences:
            # A linked-job fence needs a second predicate: submit the write as a one-put transaction.
            return self.put_many([{"owner": owner, "kind": kind, "item": item, "expected_version": expected_version}],
                                 retry_conflicts=False, _writer=_writer)[0]
        table = self.table()
        try:
            table.put_item(Item={**_marshal(data), **keys}, ConditionExpression=condition)
        except table.meta.client.exceptions.ConditionalCheckFailedException as error:
            raise Conflict("The resource has changed") from error
        return _plain(data)

    def _report(self, owner, refused):
        """Metadata-only refusal report plus a due entry for the IAM-only reconciler. Never masks the refusal."""
        from workspace import ontology_schema as schema
        identifier = schema.identity("exec-report", refused.kind, str(refused.id), refused.reason)
        for _ in range(3):
            try:
                current = self.get(owner, "exec_report", identifier)
                now = self.clock()
                due = self.get(DUE_OWNER, "exec_due", current["dueId"]) if current and current.get("dueId") else None
                writes = []
                due_ref = (current or {}).get("dueId")
                if not due or due.get("status") != "pending":
                    due_ref = due_id(now, owner, identifier, (current or {}).get("count", 0))
                    writes.append({"owner": DUE_OWNER, "kind": "exec_due", "expected_version": None, "item": {
                        "id": due_ref, "type": "report", "dueAt": now, "status": "pending",
                        "targetOwner": owner, "ref": {"kind": refused.kind, "id": str(refused.id)[:128]},
                        "reason": refused.reason}})
                record = {"id": identifier, "kind": refused.kind, "recordId": str(refused.id)[:128],
                          "reason": refused.reason, "count": (current or {}).get("count", 0) + 1,
                          "firstAt": (current or {}).get("firstAt", now), "lastAt": now, "dueId": due_ref}
                writes.insert(0, {"owner": owner, "kind": "exec_report", "item": record,
                                  "expected_version": current["version"] if current else None})
                self.put_many(writes, retry_conflicts=False)
                return
            except Exception:  # noqa: BLE001 - reporting must never mask the refusal
                continue

    def put_many(self, writes: list[dict], checks: list[dict] | None = None, *, retry_conflicts=True, before_attempt=None,
                 _writer=None) -> list[dict]:
        """Atomically publish conditional metadata across owner partitions.

        The resource client marshals native values. Build each nested condition
        separately: its placeholders belong to that Put, not the outer request.
        A transaction failure must never fall back to individual writes.
        """
        from boto3.dynamodb.conditions import ConditionExpressionBuilder
        if before_attempt is not None and not callable(before_attempt):
            raise ValueError("A transaction guard must be callable")
        checks = [] if checks is None else checks
        if not isinstance(writes, list) or not isinstance(checks, list) or not 1 <= len(writes) + len(checks) <= 100:
            raise ValueError("A transaction requires between 1 and 100 writes")
        prepared, seen = [], set()
        now = self.clock()
        for write in writes:
            if not isinstance(write, dict) or set(write) - {"owner", "kind", "item", "expected_version"}:
                raise ValueError("Invalid transaction write")
        proposed_jobs = frozenset((write.get("owner"), write["item"].get("id")) for write in writes
                                  if write.get("kind") == "job" and isinstance(write.get("item"), dict))
        fences = []
        for write in writes:
            try:
                data, keys, condition = self._prepare(
                    write.get("owner"), write.get("kind"), write.get("item"), write.get("expected_version"), now,
                    writer=_writer, proposed_jobs=proposed_jobs, fences=fences)
            except ReservedRecord as refused:
                self._report(write.get("owner"), refused)
                raise
            identity = keys["pk"], keys["sk"]
            if identity in seen:
                raise ValueError("A transaction cannot write the same record twice")
            seen.add(identity)
            prepared.append((data, keys, condition))
        table = self.table()
        transactions = []
        for data, keys, condition in prepared:
            built = ConditionExpressionBuilder().build_expression(condition)
            put = {"TableName": table.name, "Item": {**_marshal(data), **keys},
                   "ConditionExpression": built.condition_expression,
                   "ExpressionAttributeNames": built.attribute_name_placeholders}
            if built.attribute_value_placeholders:
                put["ExpressionAttributeValues"] = _marshal(built.attribute_value_placeholders)
            transactions.append({"Put": put})
        for check in checks:
            if (not isinstance(check, dict) or set(check) != {"owner", "kind", "id", "version"}
                    or check["version"] is not None and (type(check["version"]) is not int or check["version"] < 1)):
                raise ValueError("Invalid transactional version check")
            keys = self._key(check["owner"], check["kind"], check["id"])
            identity = keys["pk"], keys["sk"]
            if identity in seen:
                raise ValueError("A transaction cannot operate on the same record twice")
            seen.add(identity)
            condition = {
                "TableName": table.name, "Key": keys, "ConditionExpression": "#version = :version",
                "ExpressionAttributeNames": {"#version": "version"},
                "ExpressionAttributeValues": {":version": check["version"]},
            }
            if check["version"] is None:
                condition.update(ConditionExpression="attribute_not_exists(#pk)", ExpressionAttributeNames={"#pk": "pk"})
                condition.pop("ExpressionAttributeValues")
            transactions.append({"ConditionCheck": condition})
        for fence_owner, job_id, version in fences:
            keys = self._key(fence_owner, "job", job_id)
            identity = keys["pk"], keys["sk"]
            if identity in seen:
                continue            # the job is written or checked by this transaction already
            seen.add(identity)
            if len(transactions) >= 100:
                raise ValueError("A transaction requires between 1 and 100 writes")
            condition = {"TableName": table.name, "Key": keys, "ConditionExpression": "#version = :version",
                         "ExpressionAttributeNames": {"#version": "version"},
                         "ExpressionAttributeValues": {":version": version}}
            if version is None:
                condition.update(ConditionExpression="attribute_not_exists(#pk)", ExpressionAttributeNames={"#pk": "pk"})
                condition.pop("ExpressionAttributeValues")
            transactions.append({"ConditionCheck": condition})
        for attempt in range(5):
            if before_attempt is not None:
                before_attempt()
            try:
                table.meta.client.transact_write_items(TransactItems=copy.deepcopy(transactions))
                break
            except table.meta.client.exceptions.TransactionCanceledException as error:
                reasons = error.response.get("CancellationReasons", [])
                codes = [reason.get("Code") if isinstance(reason, dict) else None
                         for reason in reasons] if isinstance(reasons, list) else []
                # A canceled transaction wrote nothing. Retry only proven
                # contention, with the same data, versions and authority checks.
                # Actual predicate failures must never be refreshed or bypassed.
                contention = (len(codes) == len(transactions) and "TransactionConflict" in codes
                              and all(code in ("None", "TransactionConflict") for code in codes))
                if contention and retry_conflicts and attempt < 4:
                    time.sleep(random.uniform(.025 * 2 ** attempt, .05 * 2 ** attempt))
                    continue
                if contention:
                    raise TransactionContention("The transaction lost a race and wrote nothing") from error
                if any(code in ("ConditionalCheckFailed", "TransactionConflict") for code in codes):
                    raise Conflict("The resource has changed") from error
                raise
        return [_plain(data) for data, _, _ in prepared]

    def list_page(self, owner: str, kind: str, limit: int = 100, cursor: str | None = None, *, prefix: str = "") -> dict:
        from boto3.dynamodb.conditions import Key
        if kind not in KINDS or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid list limit")
        if not isinstance(prefix, str) or prefix and not _ID.fullmatch(prefix):
            raise ValueError("Invalid list prefix")
        partition = f"owner#{_owner_hash(owner)}"
        sort_prefix = f"{kind}#{prefix}"
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(partition) & Key("sk").begins_with(sort_prefix),
            "ConsistentRead": True, "Limit": limit, "ScanIndexForward": True,
        }
        if cursor:
            try:
                if not isinstance(cursor, str) or len(cursor) > 1000:
                    raise ValueError()
                decoded = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
                if not isinstance(decoded, dict):
                    raise ValueError()
                if set(decoded) == {"key", "prefix"}:
                    if decoded["prefix"] != prefix or not isinstance(decoded["key"], dict):
                        raise ValueError()
                    last_key = decoded["key"]
                else:
                    # Existing unfiltered workspace cursors keep their contract.
                    if prefix:
                        raise ValueError()
                    last_key = decoded
                if (set(last_key) != {"pk", "sk"} or last_key["pk"] != partition
                        or not isinstance(last_key["sk"], str) or not last_key["sk"].startswith(sort_prefix)):
                    raise ValueError()
                _identity(kind, last_key["sk"].split("#", 1)[1])
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid list cursor") from error
            kwargs["ExclusiveStartKey"] = last_key
        response = self.table().query(**kwargs)
        page = {"items": [_record(item) for item in response.get("Items", [])]}
        if response.get("LastEvaluatedKey"):
            continuation = ({"key": response["LastEvaluatedKey"], "prefix": prefix}
                            if prefix else response["LastEvaluatedKey"])
            page["cursor"] = base64.urlsafe_b64encode(
                json.dumps(continuation, separators=(",", ":")).encode()).decode()
        return page

    def list(self, owner: str, kind: str, limit: int = 100) -> list[dict]:
        return self.list_page(owner, kind, limit)["items"]

    def cursor_after(self, owner, kind, identifier, *, prefix=""):
        if (not isinstance(identifier, str) or not isinstance(prefix, str)
                or prefix and not _ID.fullmatch(prefix) or not identifier.startswith(prefix)):
            raise ValueError("Invalid cursor prefix")
        key = self._key(owner, kind, identifier)
        value = {"key": key, "prefix": prefix} if prefix else key
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()

    def claim_job(self, owner: str, id: str) -> dict | None:
        # A reserved execution job raises ReservedRecord (a Conflict) in put, after it is reported.
        job = self.get(owner, "job", id)
        if not job or job.get("status") != "queued":
            return None
        try:
            return self.put(owner, "job", {**job, "status": "running"}, expected_version=job["version"])
        except Conflict:
            return None

    @staticmethod
    def _blob_key(key):
        if not isinstance(key, str) or not key.startswith("workspace/") or len(key) > 1024:
            raise ValueError("Invalid blob key")
        if any(part in ("", ".", "..") or not _SEGMENT.fullmatch(part) for part in key.split("/")):
            raise ValueError("Invalid blob key")

    def _write_blob(self, key, data, content_type, once):
        from botocore.exceptions import ClientError
        self._blob_key(key)
        if not isinstance(data, bytes) or len(data) > MAX_BLOB_BYTES:
            raise ValueError("Invalid blob size")
        if not isinstance(content_type, str) or not content_type or any(c in content_type for c in "\r\n"):
            raise ValueError("Invalid blob content type")
        digest = hashlib.sha256(data).digest()
        info = {"size": len(data), "contentType": content_type, "sha256": digest.hex()}
        kwargs = dict(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                      Metadata={"sha256": digest.hex()}, CacheControl="private, no-store",
                      ChecksumSHA256=base64.b64encode(digest).decode())
        # key_for puts the upload path after owner/kind/id. An identifier
        # literally named "parts" must not expire its retained original.
        segments = key.split("/", 4)
        if len(segments) == 5 and segments[4].startswith("parts/"):
            kwargs["Tagging"] = "workspace-temporary=true"
        if once:
            kwargs["IfNoneMatch"] = "*"
        try:
            self.s3().put_object(**kwargs)
        except ClientError as error:
            if not once or error.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                raise
            if self.blob_info(key) != info:
                raise Conflict("Immutable blob already contains different bytes") from error
        return info

    def put_blob(self, key, data, content_type):
        return self._write_blob(key, data, content_type, False)

    def put_blob_once(self, key, data, content_type):
        """Conditional object creation for upload parts and revision snapshots."""
        return self._write_blob(key, data, content_type, True)

    def blob_info(self, key):
        from botocore.exceptions import ClientError
        self._blob_key(key)
        try:
            response = self.s3().head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "NotFound", "404"):
                raise FileNotFoundError("Blob not found") from error
            raise
        return {"size": response["ContentLength"], "contentType": response.get("ContentType", "application/octet-stream"),
                "sha256": response.get("Metadata", {}).get("sha256", "")}

    def blob_identity(self, key):
        """Size, stored content hash and entity tag of the current object version (a rewrite changes the tag)."""
        from botocore.exceptions import ClientError
        self._blob_key(key)
        try:
            response = self.s3().head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "NotFound", "404"):
                raise FileNotFoundError("Blob not found") from error
            raise
        return {"size": response["ContentLength"], "sha256": response.get("Metadata", {}).get("sha256", ""),
                "etag": response.get("ETag", "")}

    def get_blob(self, key, offset=0, length=None, *, if_match=None) -> bytes:
        """Ranged read. With ``if_match`` the read is conditional on that entity tag (S3 IfMatch): a replaced
        object raises Conflict and returns no bytes."""
        from botocore.exceptions import ClientError
        self._blob_key(key)
        if if_match is not None and (not isinstance(if_match, str) or not if_match):
            raise ValueError("Invalid blob identity")
        if type(offset) is not int or offset < 0 or (length is not None and (type(length) is not int or length < 0)):
            raise ValueError("Invalid blob range")
        info = self.blob_info(key)
        if info["size"] > MAX_BLOB_BYTES:
            raise ValueError("Blob exceeds the read limit")
        if offset > info["size"] or (offset == info["size"] and info["size"] != 0):
            raise ValueError("Invalid blob range")
        count = min(info["size"] - offset, length) if length is not None else info["size"] - offset
        if count == 0:
            return b""
        conditional = {"IfMatch": if_match} if if_match is not None else {}
        try:
            response = self.s3().get_object(Bucket=self.bucket, Key=key, Range=f"bytes={offset}-{offset + count - 1}",
                                            **conditional)
        except ClientError as error:
            if if_match is not None and error.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                raise Conflict("The blob was replaced") from error
            raise
        with response["Body"] as body:
            data = body.read(count + 1)
        if len(data) != count:
            raise ValueError("Blob range length mismatch")
        return data
