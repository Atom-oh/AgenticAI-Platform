"""Validate the production low-level transaction wire format without AWS calls."""
from types import SimpleNamespace

import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.session import get_session
from botocore.validate import validate_parameters

from registry.model import ConflictError
from registry.store import RegistryStore


def test_production_transaction_encodes_exact_source_and_atomic_audit():
    record = {"name": "synthetic", "recordVersion": "v1", "recordType": "CUSTOM", "subtype": "COMPONENT",
              "status": "PENDING_APPROVAL", "payload": {"rate": 3.25, "active": True, "tools": ["lookup"]},
              "description": "Synthetic", "owner": "staff", "tags": ["probe"], "stateRevision": 2}
    table = SimpleNamespace(name="synthetic-table", get_item=lambda **kwargs: {"Item": record})
    store = RegistryStore(table=table)
    writes = []
    shape = get_session().get_service_model("dynamodb").operation_model("TransactWriteItems").input_shape

    def transact(**request):
        validate_parameters(request, shape)
        writes.append(request)

    store._transaction_client = SimpleNamespace(transact_write_items=transact)
    updated, _ = store.transition("synthetic", "v1", "APPROVED", "iam-invoke:synthetic", expected_record=record)
    assert updated["status"] == "APPROVED" and len(writes) == 1
    update, audit = writes[0]["TransactItems"]
    decode = TypeDeserializer().deserialize
    values = {key: decode(value) for key, value in update["Update"]["ExpressionAttributeValues"].items()}
    assert record["payload"] in values.values() and record["tags"] in values.values()
    assert update["Update"]["TableName"] == audit["Put"]["TableName"] == table.name
    assert decode(audit["Put"]["Item"]["to"]) == "APPROVED"
    assert decode(audit["Put"]["Item"]["pk"]).startswith("audit#synthetic#v1")
    assert "ConditionExpression" in update["Update"] and "ConditionExpression" in audit["Put"]


@pytest.mark.parametrize("index", [0, 1])
def test_real_transaction_cancellation_maps_record_or_audit_conflict(index):
    store = RegistryStore(table=SimpleNamespace(name="synthetic-table"))
    reasons = [{"Code": "None"}, {"Code": "None"}]
    reasons[index] = {"Code": "ConditionalCheckFailed"}

    def fail(**request):
        raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                           "CancellationReasons": reasons}, "TransactWriteItems")

    store._transaction_client = SimpleNamespace(transact_write_items=fail)
    with pytest.raises(ConflictError):
        store.put_new({"name": "synthetic", "recordVersion": "v1", "status": "DRAFT"}, "staff")
