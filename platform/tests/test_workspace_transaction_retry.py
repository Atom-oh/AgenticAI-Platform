"""Transient transaction contention must not weaken version/authority fences."""
import copy

import pytest

from test_workspace_storage import FakeS3, FakeTable, TransactionFailure
from workspace.storage import Conflict, Storage


class ContendedTable(FakeTable):
    def __init__(self, failures):
        super().__init__()
        self.failures = list(failures)
        self.attempts = []

    def transact_write_items(self, **kwargs):
        self.attempts.append(copy.deepcopy(kwargs))
        if self.failures:
            reasons = self.failures.pop(0)
            error = TransactionFailure()
            error.response["CancellationReasons"] = reasons
            raise error
        return super().transact_write_items(**kwargs)


def setup_storage(failures):
    table = ContendedTable(failures)
    storage = Storage(table=table, s3=FakeS3(), bucket="private", clock=lambda: 1000)
    authority = storage.put("team", "membership", {"id": "member", "role": "planner"})
    checks = [{"owner": "team", "kind": "membership", "id": "member", "version": authority["version"]}]
    writes = [
        {"owner": "team", "kind": "docanalysis", "item": {"id": "analysis", "status": "needs_review"}},
        {"owner": "team", "kind": "job", "item": {"id": "job", "status": "completed"}},
    ]
    return storage, table, authority, writes, checks


def test_transient_contention_publishes_once_with_identical_conditions(monkeypatch):
    reasons = [{"Code": "None"}, {"Code": "None"}, {"Code": "TransactionConflict"}]
    storage, table, _, writes, checks = setup_storage([reasons, reasons])
    pauses = []

    def pause(delay):
        pauses.append(delay)
        assert storage.get("team", "docanalysis", "analysis") is None
        assert storage.get("team", "job", "job") is None

    monkeypatch.setattr("workspace.storage.time.sleep", pause)
    result = storage.put_many(writes, checks=checks)
    assert [row["status"] for row in result] == ["needs_review", "completed"]
    assert storage.get("team", "docanalysis", "analysis")["version"] == 1
    assert storage.get("team", "job", "job")["version"] == 1
    assert len(table.transactions) == 1
    assert len(table.attempts) == 3 and len(pauses) == 2
    assert table.attempts[0] == table.attempts[1] == table.attempts[2]
    assert all(0 <= delay <= .4 for delay in pauses)


def test_authority_check_only_can_recover_without_writing_records(monkeypatch):
    storage, table, _, _, checks = setup_storage([[{"Code": "TransactionConflict"}]])
    before = copy.deepcopy(table.items)
    monkeypatch.setattr("workspace.storage.time.sleep", lambda _: None)
    assert storage.put_many([], checks=checks) == []
    assert len(table.attempts) == 2
    assert table.items == before


def test_sdk_parameter_mutation_cannot_change_retry_authority(monkeypatch):
    reasons = [{"Code": "None"}, {"Code": "None"}, {"Code": "TransactionConflict"}]
    storage, table, _, writes, checks = setup_storage([reasons])
    send = table.meta.client.transact_write_items

    def mutate_failed_request(**kwargs):
        try:
            return send(**kwargs)
        except TransactionFailure:
            kwargs["TransactItems"][-1]["ConditionCheck"]["ExpressionAttributeValues"][":version"] = 999
            raise

    table.meta.client.transact_write_items = mutate_failed_request
    monkeypatch.setattr("workspace.storage.time.sleep", lambda _: None)
    storage.put_many(writes, checks=checks)
    assert storage.get("team", "docanalysis", "analysis")["status"] == "needs_review"
    assert len(table.attempts) == 2
    assert table.attempts[0] == table.attempts[1]


def test_revocation_between_attempts_still_prevents_all_publication(monkeypatch):
    reasons = [{"Code": "None"}, {"Code": "None"}, {"Code": "TransactionConflict"}]
    storage, table, authority, writes, checks = setup_storage([reasons])

    def revoke(_):
        storage.put("team", "membership", {**authority, "role": "revoked"}, authority["version"])

    monkeypatch.setattr("workspace.storage.time.sleep", revoke)
    with pytest.raises(Conflict):
        storage.put_many(writes, checks=checks)
    assert len(table.attempts) == 2
    assert storage.get("team", "membership", "member")["role"] == "revoked"
    assert storage.get("team", "docanalysis", "analysis") is None
    assert storage.get("team", "job", "job") is None
    assert table.transactions == []


@pytest.mark.parametrize("reasons", [
    [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}, {"Code": "TransactionConflict"}],
    [{"Code": "ValidationError"}, {"Code": "None"}, {"Code": "TransactionConflict"}],
    [{"Code": "ProvisionedThroughputExceeded"}, {"Code": "None"}, {"Code": "None"}],
    [{"Code": "TransactionConflict"}],  # incomplete cancellation evidence
    [{"Code": "TransactionConflict"}, {}, {"Code": "None"}],
    [{"Code": "TransactionConflict"}, {"Code": None}, {"Code": "None"}],
    [],
])
def test_conditional_unknown_or_incomplete_failures_are_not_retried(monkeypatch, reasons):
    storage, table, _, writes, checks = setup_storage([reasons])
    pauses = []
    monkeypatch.setattr("workspace.storage.time.sleep", pauses.append)
    with pytest.raises((Conflict, TransactionFailure)):
        storage.put_many(writes, checks=checks)
    assert len(table.attempts) == 1 and pauses == []
    assert storage.get("team", "docanalysis", "analysis") is None
    assert storage.get("team", "job", "job") is None
    assert table.transactions == []


def test_persistent_contention_has_bounded_wait_and_no_partial_write(monkeypatch):
    reasons = [{"Code": "TransactionConflict"}, {"Code": "None"}, {"Code": "None"}]
    storage, table, _, writes, checks = setup_storage([reasons] * 100)
    pauses = []
    monkeypatch.setattr("workspace.storage.time.sleep", pauses.append)
    with pytest.raises(Conflict):
        storage.put_many(writes, checks=checks)
    assert 1 < len(table.attempts) <= 5
    assert len(pauses) == len(table.attempts) - 1
    assert sum(pauses) <= .75
    assert all(item == table.attempts[0] for item in table.attempts)
    assert storage.get("team", "docanalysis", "analysis") is None
    assert storage.get("team", "job", "job") is None


def test_uncontended_transaction_does_not_pause_or_repeat(monkeypatch):
    storage, table, _, writes, checks = setup_storage([])
    pauses = []
    monkeypatch.setattr("workspace.storage.time.sleep", pauses.append)
    result = storage.put_many(writes, checks=checks)
    assert len(result) == 2 and len(table.attempts) == 1
    assert pauses == []
