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


class TransactionFailure(ClientError):
    def __init__(self, code="ConditionalCheckFailed"):
        super().__init__({"Error": {"Code": "TransactionCanceledException"},
                          "CancellationReasons": [{"Code": code}]}, "TransactWriteItems")


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
        self.transactions = []
        self.name = "workspace-test"
        self.before_transaction = None
        self.lock = threading.RLock()
        self.page_size = page_size
        self.meta = SimpleNamespace(client=SimpleNamespace(
            transact_write_items=self.transact_write_items,
            exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailure,
                                       TransactionCanceledException=TransactionFailure)))

    def transact_write_items(self, **kwargs):
        with self.lock:
            if self.before_transaction:
                callback, self.before_transaction = self.before_transaction, None
                callback()
            writes = [entry["Put"] for entry in kwargs["TransactItems"] if "Put" in entry]
            checks = [entry["ConditionCheck"] for entry in kwargs["TransactItems"] if "ConditionCheck" in entry]
            assert 1 <= len(writes) + len(checks) <= 100
            keys = [(entry["Item"]["pk"], entry["Item"]["sk"]) for entry in writes]
            check_keys = [(entry["Key"]["pk"], entry["Key"]["sk"]) for entry in checks]
            assert len(set(keys + check_keys)) == len(keys + check_keys)
            for entry, key in zip(writes + checks, keys + check_keys):
                assert entry["TableName"] == self.name
                expression = entry["ConditionExpression"]
                assert isinstance(expression, str)
                names = entry["ExpressionAttributeNames"]
                current = self.items.get(key, {})
                if expression.startswith("attribute_not_exists("):
                    field = names[expression[len("attribute_not_exists("):-1]]
                    matches = field not in current
                else:
                    name, value = expression.split(" = ")
                    matches = current.get(names[name]) == entry["ExpressionAttributeValues"][value]
                if not matches:
                    raise TransactionFailure()
            for entry, key in zip(writes, keys):
                self.items[key] = copy.deepcopy(entry["Item"])
                self.calls.append(copy.deepcopy(entry["Item"]))
            self.transactions.append(copy.deepcopy(kwargs))
            return {}

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


@pytest.mark.parametrize("kind", ["asset", "contract", "run", "batch", "release", "gitexport"])
def test_user_work_and_approvals_never_receive_ttl(storage, kind):
    approval = {"version": 3, "hash": "a" * 64, "actor": "owner", "at": 1_800_000_000_000}
    record = storage.put("owner", kind, {"id": "retained", "status": "approved", "approval": approval, "ttl": 1})
    assert "ttl" not in record
    assert record["approval"] == approval
    updated = storage.put("owner", kind, {**record, "ttl": 2}, record["version"])
    assert "ttl" not in updated and updated["approval"] == approval
    assert "ttl" not in storage.get("owner", kind, "retained")
    assert all("ttl" not in item for item in storage.table().items.values())


def test_transaction_prepares_cross_scope_records_with_same_cas_rules(storage):
    first = storage.put("alice", "asset", {"id": "a", "status": "stored"})
    storage.clock = lambda: first["createdAt"] + 60_000
    writes = [
        {"owner": "alice", "kind": "asset", "item": {**first, "name": "updated", "ttl": 1},
         "expected_version": 1},
        {"owner": "project:shared", "kind": "job", "item": {"id": "j", "owner": "spoofed",
          "value": 0.25}, "expected_version": None},
    ]
    original = copy.deepcopy(writes)
    result = storage.put_many(writes)
    assert writes == original
    assert [row["version"] for row in result] == [2, 1]
    assert result[0]["createdAt"] == first["createdAt"]
    assert result[0]["updatedAt"] == result[1]["createdAt"]
    assert "ttl" not in result[0] and "owner" not in result[1]
    assert result[1]["ttl"] == storage.clock() // 1000 + 30 * 24 * 60 * 60
    assert storage.get("alice", "asset", "a") == result[0]
    assert storage.get("project:shared", "job", "j") == result[1]
    assert storage.get("alice", "job", "j") is None
    assert len(storage.table().transactions) == 1


def test_transaction_failure_never_partially_publishes(storage):
    from workspace.storage import Conflict
    original = storage.put("alice", "asset", {"id": "a", "name": "original"})
    # Lose the CAS after put_many's reads, before the transaction commits.
    def race():
        storage.put("alice", "asset", {**original, "name": "winner"}, 1)
    storage.table().before_transaction = race
    with pytest.raises(Conflict):
        storage.put_many([
            {"owner": "project:shared", "kind": "asset", "item": {"id": "new"}, "expected_version": None},
            {"owner": "alice", "kind": "asset", "item": original, "expected_version": 1},
        ])
    assert storage.get("project:shared", "asset", "new") is None
    assert storage.get("alice", "asset", "a")["name"] == "winner"


def test_transaction_validation_and_duplicate_keys_never_write(storage):
    from workspace.storage import Conflict
    create = {"owner": "alice", "kind": "asset", "item": {"id": "new"}, "expected_version": None}
    invalid = [
        [], [create] * 101, [create, create],
        [create, {**create, "item": {"id": "other"}, "expected_version": True}],
        [create, {**create, "item": {"id": "other", "text": "x" * 350_000}}],
    ]
    for writes in invalid:
        with pytest.raises(ValueError):
            storage.put_many(writes)
        assert storage.list("alice", "asset") == []
    storage.put("alice", "asset", {"id": "taken"})
    with pytest.raises(Conflict):
        storage.put_many([create, {**create, "item": {"id": "taken"}}])
    assert storage.get("alice", "asset", "new") is None


def test_transaction_service_failure_has_no_sequential_fallback(storage):
    def unavailable(**kwargs):
        raise RuntimeError("Transaction service is unavailable")
    storage.table().meta.client.transact_write_items = unavailable
    with pytest.raises(RuntimeError):
        storage.put_many([{"owner": "alice", "kind": "asset", "item": {"id": "new"},
                           "expected_version": None}])
    assert storage.list("alice", "asset") == []
    assert storage.table().calls == []


def test_transaction_throttling_is_not_reported_as_version_conflict(storage):
    def throttled(**kwargs):
        raise TransactionFailure("ThrottlingError")
    storage.table().meta.client.transact_write_items = throttled
    with pytest.raises(TransactionFailure):
        storage.put_many([{"owner": "alice", "kind": "asset", "item": {"id": "new"},
                           "expected_version": None}])


def test_transaction_boto3_wire_conditions_are_nested_and_values_marshaled_once():
    """Capture the real SDK's serialized request, intercepted before any IO."""
    import json
    import boto3
    from botocore.stub import Stubber
    from workspace.storage import Storage

    resource = boto3.resource("dynamodb", region_name="us-east-1",
                              aws_access_key_id="test-only", aws_secret_access_key="test-only")
    table = resource.Table("workspace-test")
    client = table.meta.client
    captured = []
    client.meta.events.register_first(
        "before-call.*.*",
        lambda params, model, **kwargs: captured.append(json.loads(params["body"]))
        if model.name == "TransactWriteItems" else None)
    store = Storage(table=table, clock=lambda: 5000)
    with Stubber(client) as stub:
        stub.add_response("get_item", {"Item": {
            "id": {"S": "old"}, "version": {"N": "1"}, "createdAt": {"N": "100"}}})
        stub.add_response("transact_write_items", {})
        result = store.put_many([
            {"owner": "alice", "kind": "product", "item": {"id": "old", "price": 0.25},
             "expected_version": 1},
            {"owner": "project:shared", "kind": "guideline", "item": {"id": "new"},
             "expected_version": None},
        ])
        stub.assert_no_pending_responses()
    wire = captured[0]
    assert "ExpressionAttributeNames" not in wire and "ExpressionAttributeValues" not in wire
    updated, created = [entry["Put"] for entry in wire["TransactItems"]]
    assert updated["Item"]["version"] == {"N": "2"}
    assert updated["Item"]["createdAt"] == {"N": "100"}
    assert updated["Item"]["price"] == {"N": "0.25"}
    assert list(updated["ExpressionAttributeValues"].values()) == [{"N": "1"}]
    assert list(updated["ExpressionAttributeNames"].values()) == ["version"]
    assert created["ConditionExpression"].startswith("attribute_not_exists(")
    assert result[0]["price"] == 0.25 and result[0]["createdAt"] == 100


def test_release_lifecycle_records_support_owner_scoped_transactions_and_keys(storage):
    kinds = ("batch", "release", "gitexport")
    created = storage.put_many([
        {"owner": "project:shared", "kind": kind,
         "item": {"id": "same", "status": "queued", "ttl": 1}, "expected_version": None}
        for kind in kinds
    ])
    assert all(row["version"] == 1 and "ttl" not in row for row in created)
    for kind, record in zip(kinds, created):
        assert storage.get("project:shared", kind, "same") == record
        assert storage.list("project:shared", kind) == [record]
        assert storage.get("project:other", kind, "same") is None
        assert storage.owns_key("project:shared", storage.key_for("project:shared", kind, "same", "manifest.json"))
    updated = storage.put_many([
        {"owner": "project:shared", "kind": kind,
         "item": {**record, "status": "completed"}, "expected_version": 1}
        for kind, record in zip(kinds, created)
    ])
    assert all(row["version"] == 2 and row["status"] == "completed" and "ttl" not in row for row in updated)
