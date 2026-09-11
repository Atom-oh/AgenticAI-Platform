"""Storage integration tests with conditional DynamoDB and private S3 fakes."""
from __future__ import annotations

import copy
import hashlib
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ConditionalFailure(Exception):
    pass


def condition_matches(condition, item):
    expression = condition.get_expression()
    op, values = expression["operator"], expression["values"]
    if op == "AND":
        return all(condition_matches(value, item) for value in values)
    if op == "OR":
        return any(condition_matches(value, item) for value in values)
    name = values[0].name
    if op == "attribute_not_exists":
        return name not in item
    if op == "attribute_exists":
        return name in item
    if op == "=":
        return item.get(name) == values[1]
    if op == "begins_with":
        return str(item.get(name, "")).startswith(values[1])
    raise AssertionError(f"Unsupported fake condition: {op}")


class FakeTable:
    def __init__(self, page_size=100):
        self.items, self.calls = {}, []
        self.lock = threading.RLock()
        self.page_size = page_size
        self.meta = SimpleNamespace(client=SimpleNamespace(
            exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailure)))

    def get_item(self, **kwargs):
        assert kwargs.get("ConsistentRead") is True
        with self.lock:
            item = self.items.get(tuple(kwargs["Key"][key] for key in ("pk", "sk")))
            return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, **kwargs):
        with self.lock:
            item = kwargs["Item"]
            key = item["pk"], item["sk"]
            assert "ConditionExpression" in kwargs
            if not condition_matches(kwargs["ConditionExpression"], self.items.get(key, {})):
                raise ConditionalFailure()
            self.items[key] = copy.deepcopy(item)
            self.calls.append(copy.deepcopy(item))
            return {}

    def query(self, **kwargs):
        assert kwargs.get("ConsistentRead") is True
        with self.lock:
            rows = [copy.deepcopy(item) for item in self.items.values()
                    if condition_matches(kwargs["KeyConditionExpression"], item)]
            rows.sort(key=lambda item: item["sk"], reverse=not kwargs.get("ScanIndexForward", True))
            start = kwargs.get("ExclusiveStartKey")
            if start:
                position = next(i for i, row in enumerate(rows) if row["sk"] == start["sk"])
                rows = rows[position + 1:]
            limit = min(kwargs.get("Limit", 100), self.page_size)
            response = {"Items": rows[:limit]}
            if len(rows) > limit:
                response["LastEvaluatedKey"] = {key: rows[limit - 1][key] for key in ("pk", "sk")}
            return response


class FakeS3:
    class Missing(ClientError):
        def __init__(self):
            super().__init__({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    def __init__(self):
        self.objects, self.puts, self.reads, self.bodies = {}, [], [], []
        self.exceptions = SimpleNamespace(NoSuchKey=self.Missing)
        self.lock = threading.RLock()

    def put_object(self, **kwargs):
        assert "ACL" not in kwargs
        key = kwargs["Bucket"], kwargs["Key"]
        with self.lock:
            if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.objects[key] = {
                "data": bytes(kwargs["Body"]), "ContentType": kwargs["ContentType"],
                "Metadata": copy.deepcopy(kwargs.get("Metadata", {})),
            }
            self.puts.append(copy.deepcopy(kwargs))
        return {}

    def head_object(self, **kwargs):
        with self.lock:
            item = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
            if item is None:
                raise self.Missing()
            return {"ContentLength": len(item["data"]), "ContentType": item["ContentType"],
                    "Metadata": copy.deepcopy(item["Metadata"])}

    def get_object(self, **kwargs):
        with self.lock:
            item = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
            if item is None:
                raise self.Missing()
            data = item["data"]
            if "Range" in kwargs:
                begin, end = kwargs["Range"].removeprefix("bytes=").split("-")
                data = data[int(begin):int(end) + 1]
            body = io.BytesIO(data)
            self.bodies.append(body)
            self.reads.append(kwargs)
            return {"Body": body}


@pytest.fixture
def storage():
    from workspace.storage import Storage
    return Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test")


def test_owner_isolation_and_conditional_versioning(storage):
    from workspace.storage import Conflict
    first = storage.put("owner-a", "asset", {"id": "same", "name": "one", "status": "uploading"})
    assert first["version"] == 1 and first["createdAt"] == first["updatedAt"]
    assert storage.get("owner-b", "asset", "same") is None
    assert storage.list("owner-b", "asset") == []
    with pytest.raises(Conflict):
        storage.put("owner-a", "asset", {"id": "same", "name": "overwrite"})
    second = storage.put("owner-a", "asset", {**first, "name": "two"}, expected_version=1)
    assert second["version"] == 2 and second["createdAt"] == first["createdAt"]
    with pytest.raises(Conflict):
        storage.put("owner-a", "asset", first, expected_version=1)
    assert storage.get("owner-a", "asset", "same")["name"] == "two"
    assert all("owner-a" not in record["pk"] for record in storage.table().items.values())


def test_claim_job_has_only_one_winner(storage):
    storage.put("owner", "job", {"id": "job", "task": "finalize", "status": "queued"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: storage.claim_job("owner", "job"), range(16)))
    winners = [result for result in results if result]
    assert len(winners) == 1
    assert winners[0]["status"] == "running" and winners[0]["version"] == 2
    assert storage.claim_job("foreign", "job") is None


def test_private_keys_ranges_and_body_cleanup(storage):
    from workspace.storage import key_for
    data = b"<html>private original</html>"
    key = key_for("private-sub", "asset", "a1", "original.html")
    assert "private-sub" not in key
    assert key != key_for("another-sub", "asset", "a1", "original.html")
    assert storage.key_for("private-sub", "asset", "a1", "original.html") == key
    storage.put_blob(key, data, "text/html")
    assert storage.blob_info(key) == {
        "size": len(data), "contentType": "text/html", "sha256": hashlib.sha256(data).hexdigest()}
    assert storage.get_blob(key, offset=2, length=5) == data[2:7]
    assert storage.s3().reads[-1]["Range"] == "bytes=2-6"
    assert all(body.closed for body in storage.s3().bodies)
    for offset in (-1, True, len(data) + 1):
        with pytest.raises(ValueError):
            storage.get_blob(key, offset=offset, length=1)


def test_immutable_parts_cannot_be_overwritten(storage):
    from workspace.storage import Conflict
    key = storage.key_for("owner", "asset", "a", "parts/0")
    storage.put_blob_once(key, b"abc", "application/octet-stream")
    storage.put_blob_once(key, b"abc", "application/octet-stream")
    with pytest.raises(Conflict):
        storage.put_blob_once(key, b"xyz", "application/octet-stream")
    assert storage.get_blob(key) == b"abc"
    assert len(storage.s3().puts) == 1


def test_owner_bound_list_continuation():
    from workspace.storage import Storage
    storage = Storage(table=FakeTable(page_size=2), s3=FakeS3(), bucket="private-test")
    for index in range(5):
        storage.put("alice", "asset", {"id": f"a{index}", "status": "stored"})
        storage.put("bob", "asset", {"id": f"a{index}", "status": "stored"})
    cursor, ids = None, []
    for _ in range(4):
        page = storage.list_page("alice", "asset", limit=2, cursor=cursor)
        ids.extend(row["id"] for row in page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
        with pytest.raises(ValueError):
            storage.list_page("bob", "asset", limit=2, cursor=cursor)
    assert ids == ["a0", "a1", "a2", "a3", "a4"]


def test_keys_and_versions_reject_unsafe_inputs(storage):
    from workspace.storage import key_for
    for suffix in ("../secret", "/absolute", "parts/../secret", "x?token=y", "x\\y"):
        with pytest.raises(ValueError):
            key_for("owner", "asset", "a", suffix)
    for identifier in ("../a", "a/b", "", "a#b"):
        with pytest.raises(ValueError):
            storage.get("owner", "asset", identifier)
    for version in (True, 0, -1, 1.5):
        with pytest.raises(ValueError):
            storage.put("owner", "asset", {"id": "a"}, expected_version=version)


def test_float_metadata_is_marshaled_for_dynamodb(storage):
    from decimal import Decimal
    saved = storage.put("owner", "run", {"id": "r", "visualTolerance": 0.15, "status": "queued"})
    assert saved["visualTolerance"] == 0.15
    assert isinstance(next(iter(storage.table().items.values()))["visualTolerance"], Decimal)


@pytest.mark.parametrize("method", ["put_blob", "put_blob_once"])
def test_only_upload_parts_receive_the_temporary_lifecycle_tag(storage, method):
    write = getattr(storage, method)
    part_key = storage.key_for("owner", "asset", "asset1", "parts/0-hash")
    write(part_key, b"part", "application/octet-stream")
    assert storage.s3().puts[-1]["Tagging"] == "workspace-temporary=true"
    for kind, identifier, suffix in [
        ("asset", "asset1", "original"),
        ("asset", "asset1", "analysis.json"),
        ("asset", "parts", "original"),
        ("run", "run1", "r1.html"),
        ("contract", "contract1", "revisions/1.json"),
    ]:
        write(storage.key_for("owner", kind, identifier, suffix), b"retained", "application/octet-stream")
        assert "Tagging" not in storage.s3().puts[-1]


@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed"])
def test_only_operational_jobs_expire_thirty_days_after_last_write(storage, status):
    now = [1_800_000_000_999]
    storage.clock = lambda: now[0]
    job = storage.put("owner", "job", {"id": "operational", "status": status, "ttl": 1})
    assert job["ttl"] == now[0] // 1000 + 30 * 24 * 60 * 60
    assert type(job["ttl"]) is int
    now[0] += 60_000
    updated = storage.put("owner", "job", {**job, "progress": 50}, job["version"])
    assert updated["ttl"] == now[0] // 1000 + 30 * 24 * 60 * 60
    assert updated["ttl"] == job["ttl"] + 60
    assert storage.get("owner", "job", job["id"])["ttl"] == updated["ttl"]


@pytest.mark.parametrize("kind", ["asset", "contract", "run"])
def test_user_work_and_approvals_never_receive_ttl(storage, kind):
    approval = {"version": 3, "hash": "a" * 64, "actor": "owner", "at": 1_800_000_000_000}
    record = storage.put("owner", kind, {"id": "retained", "status": "approved", "approval": approval, "ttl": 1})
    assert "ttl" not in record
    assert record["approval"] == approval
    updated = storage.put("owner", kind, {**record, "ttl": 2}, record["version"])
    assert "ttl" not in updated and updated["approval"] == approval
    assert "ttl" not in storage.get("owner", kind, "retained")
    assert all("ttl" not in item for item in storage.table().items.values())
