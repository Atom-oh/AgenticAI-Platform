"""Content-free durable turn state for the managed Harness's opaque history."""
from contextlib import contextmanager
import uuid

from registry import api
from registry.store import _is_conditional_failure


class HarnessSessionUnavailable(RuntimeError):
    pass


@contextmanager
def turn(session_id):
    table = api.get_store().table()
    key = {"pk": "harness-session#" + session_id, "sk": "state"}
    previous = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if previous and previous.get("phase") != "COMPLETE":
        raise HarnessSessionUnavailable("Use a new conversation after an unfinished Harness turn")
    token = uuid.uuid4().hex
    condition = {"ConditionExpression": "attribute_not_exists(pk)"}
    if previous:
        condition = {"ConditionExpression": "#phase = :complete AND #token = :previous",
                     "ExpressionAttributeNames": {"#phase": "phase", "#token": "turnToken"},
                     "ExpressionAttributeValues": {":complete": "COMPLETE", ":previous": previous["turnToken"]}}
    try:
        table.put_item(Item={**key, "phase": "ACTIVE", "turnToken": token}, **condition)
    except Exception as error:
        if _is_conditional_failure(error):
            raise HarnessSessionUnavailable("Harness turn is already in progress; use a new conversation") from None
        raise
    state = {"complete": False}
    try:
        yield state
    finally:
        # A failed write leaves ACTIVE, which still prevents session reuse.
        table.update_item(Key=key, UpdateExpression="SET #phase = :phase",
            ConditionExpression="#token = :token",
            ExpressionAttributeNames={"#phase": "phase", "#token": "turnToken"},
            ExpressionAttributeValues={":phase": "COMPLETE" if state["complete"] else "FAILED", ":token": token})
