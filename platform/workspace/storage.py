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
import re
import time
from decimal import Decimal

KINDS = frozenset({"asset", "contract", "job", "run"})
MAX_BLOB_BYTES = 50 * 1024 * 1024
MAX_RECORD_BYTES = 350_000
JOB_RETENTION_SECONDS = 30 * 24 * 60 * 60
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+\Z")


class Conflict(Exception):
    """A conditional write lost a race or would replace immutable bytes."""


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

    def __init__(self, table=None, s3=None, bucket=None, table_name=None, clock=None):
        self._table, self._s3 = table, s3
        self.bucket = bucket if bucket is not None else os.environ.get("WORKSPACE_BUCKET", "")
        self.table_name = table_name if table_name is not None else os.environ.get("WORKSPACE_TABLE", "")
        self.clock = clock or (lambda: int(time.time() * 1000))

    @staticmethod
    def _config():
        from botocore.config import Config
        return Config(connect_timeout=5, read_timeout=30, retries={"mode": "standard", "total_max_attempts": 3})

    def table(self):
        if self._table is None:
            if not self.table_name:
                raise RuntimeError("Workspace metadata storage is not configured")
            import boto3
            self._table = boto3.resource("dynamodb", config=self._config()).Table(self.table_name)
        return self._table

    def s3(self):
        if not self.bucket:
            raise RuntimeError("Workspace blob storage is not configured")
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3", config=self._config())
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

    def put(self, owner: str, kind: str, item: dict, expected_version: int | None = None) -> dict:
        from boto3.dynamodb.conditions import Attr
        identifier = item.get("id")
        keys = self._key(owner, kind, identifier)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise ValueError("Invalid expected version")
        previous = None
        if expected_version is not None:
            previous = self.get(owner, kind, identifier)
            if not previous or previous["version"] != expected_version:
                raise Conflict("The resource has changed")
        now = self.clock()
        data = {key: copy.deepcopy(value) for key, value in item.items()
                if key not in ("pk", "sk", "owner", "sub", "ttl")}
        data.update(id=identifier, version=expected_version + 1 if expected_version else 1,
                    createdAt=previous["createdAt"] if previous else now, updatedAt=now)
        if kind == "job":
            data["ttl"] = now // 1000 + JOB_RETENTION_SECONDS
        data.setdefault("status", {"asset": "uploading", "contract": "draft", "job": "queued", "run": "queued"}[kind])
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, default=str).encode()
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("Metadata exceeds the storage limit; put large evidence in blob storage")
        condition = Attr("version").eq(expected_version) if expected_version else Attr("pk").not_exists()
        table = self.table()
        try:
            table.put_item(Item={**_marshal(data), **keys}, ConditionExpression=condition)
        except table.meta.client.exceptions.ConditionalCheckFailedException as error:
            raise Conflict("The resource has changed") from error
        return _plain(data)

    def list_page(self, owner: str, kind: str, limit: int = 100, cursor: str | None = None) -> dict:
        from boto3.dynamodb.conditions import Key
        if kind not in KINDS or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid list limit")
        partition = f"owner#{_owner_hash(owner)}"
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(partition) & Key("sk").begins_with(f"{kind}#"),
            "ConsistentRead": True, "Limit": limit, "ScanIndexForward": True,
        }
        if cursor:
            try:
                if not isinstance(cursor, str) or len(cursor) > 1000:
                    raise ValueError()
                last_key = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
                if (set(last_key) != {"pk", "sk"} or last_key["pk"] != partition
                        or not isinstance(last_key["sk"], str) or not last_key["sk"].startswith(f"{kind}#")):
                    raise ValueError()
                _identity(kind, last_key["sk"].split("#", 1)[1])
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid list cursor") from error
            kwargs["ExclusiveStartKey"] = last_key
        response = self.table().query(**kwargs)
        page = {"items": [_record(item) for item in response.get("Items", [])]}
        if response.get("LastEvaluatedKey"):
            page["cursor"] = base64.urlsafe_b64encode(
                json.dumps(response["LastEvaluatedKey"], separators=(",", ":")).encode()).decode()
        return page

    def list(self, owner: str, kind: str, limit: int = 100) -> list[dict]:
        return self.list_page(owner, kind, limit)["items"]

    def claim_job(self, owner: str, id: str) -> dict | None:
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

    def get_blob(self, key, offset=0, length=None) -> bytes:
        self._blob_key(key)
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
        response = self.s3().get_object(Bucket=self.bucket, Key=key, Range=f"bytes={offset}-{offset + count - 1}")
        with response["Body"] as body:
            data = body.read(count + 1)
        if len(data) != count:
            raise ValueError("Blob range length mismatch")
        return data
