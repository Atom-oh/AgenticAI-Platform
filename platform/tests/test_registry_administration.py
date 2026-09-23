"""Generic approval requires IAM administration and authoritative source state."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api")]
os.environ["REGISTRY_EMBED"] = "0"

from common.ctx import Ctx
from handlers import registry as routes
from registry import api, administration
from registry.model import ValidationError
import admin_handler
from types import SimpleNamespace
ADMIN_CONTEXT = SimpleNamespace(aws_request_id="11111111-1111-4111-8111-111111111111")


@pytest.fixture(autouse=True)
def store():
    yield api.reset_for_tests()


def record(kind="CUSTOM", status="DRAFT"):
    return api.create_record({
        "name": "approval-probe", "recordVersion": "v1", "recordType": kind,
        "subtype": "COMPONENT" if kind == "CUSTOM" else None, "owner": "staff",
        "description": "Synthetic review", "tags": ["probe"], "payload": {"revision": 1},
    }, "staff", status=status, embed=False)


def context(actor="staff"):
    class Socket:
        def __init__(self):
            self.messages = []

        def post_to_connection(self, **kwargs):
            self.messages.append(json.loads(kwargs["Data"]))
    socket = Socket()
    return Ctx(apigw=socket, conn_id="synthetic", email=actor, rid="probe"), socket.messages


@pytest.mark.parametrize("kind", ["MCP", "SKILL", "CUSTOM"])
@pytest.mark.parametrize("target,status", [
    ("APPROVED", "PENDING_APPROVAL"), ("REJECTED", "PENDING_APPROVAL"), ("DEPRECATED", "APPROVED")])
def test_staff_cannot_apply_generic_decisions_or_spoof_admin(store, kind, target, status):
    item = record(kind, status)
    before = store.table().dump()
    for actor in ["staff", "another-staff"]:
        ctx, messages = context(actor)
        routes.registry_transition(ctx, {"name": item["name"], "version": "v1", "to": target,
                                         "reason": "Synthetic", "admin": True, "role": "admin"})
        assert messages[-1]["code"] == 403 and messages[-1]["ok"] is False
    assert store.table().dump() == before
    public = api.public_record(api.get_record(item["name"], "v1"))
    assert target not in public["allowedTargets"] and target in public["adminRequiredTargets"]


@pytest.mark.parametrize("kind", ["MCP", "SKILL", "CUSTOM"])
def test_iam_decision_uses_exact_inspected_record(kind):
    item = record(kind)
    ctx, messages = context()
    routes.registry_transition(ctx, {"name": item["name"], "version": "v1", "to": "PENDING_APPROVAL"})
    assert messages[-1]["ok"] is True
    inspected = admin_handler.handler({"op": "inspect_registry_record", "name": item["name"], "version": "v1"}, ADMIN_CONTEXT)
    result = admin_handler.handler({"op": "transition_registry_record", "name": item["name"],
                                    "version": "v1", "to": "APPROVED", "expectedHash": inspected["expectedHash"]}, ADMIN_CONTEXT)
    assert result["ok"] and result["record"]["status"] == "APPROVED"
    assert result["audit"]["actor"] == "iam-invoke:" + ADMIN_CONTEXT.aws_request_id
    assert [r["name"] for r in api.list_approved()] == [item["name"]]


@pytest.mark.parametrize("timing", ["after_inspection", "before_commit"])
def test_iam_decision_rejects_changed_content_without_approval(store, monkeypatch, timing):
    item = record(status="PENDING_APPROVAL")
    inspected = administration.inspect(item["name"], "v1")
    write = store._write_with_audit

    def change():
        store.rewrite(item["name"], "v1", {"payload": {"revision": 2}, "tags": ["changed"]}, "another-admin")

    if timing == "after_inspection":
        change()
    else:
        def concurrent(*args, **kwargs):
            change()
            return write(*args, **kwargs)
        monkeypatch.setattr(store, "_write_with_audit", concurrent)
    result = admin_handler.handler({"op": "transition_registry_record", "name": item["name"],
                                    "version": "v1", "to": "APPROVED", "expectedHash": inspected["expectedHash"]}, ADMIN_CONTEXT)
    assert result["ok"] is False and result["code"] == 409
    assert store.get(item["name"], "v1")["status"] == "PENDING_APPROVAL"
    assert not any(event["to"] == "APPROVED" for event in store.audit(item["name"], "v1"))


def test_generic_admin_cannot_bypass_agent_administration():
    item = record("AGENT", "PENDING_APPROVAL")
    with pytest.raises(ValidationError):
        administration.inspect(item["name"], "v1")
    result = admin_handler.handler({"op": "transition_registry_record", "name": item["name"], "version": "v1",
                                    "to": "APPROVED", "expectedHash": administration.fingerprint(item)}, ADMIN_CONTEXT)
    assert result["ok"] is False
    assert api.get_record(item["name"], "v1")["status"] == "PENDING_APPROVAL"


def test_admin_audit_uses_lambda_context_not_claimed_operator():
    item = record(status="PENDING_APPROVAL")
    reviewed = administration.inspect(item["name"], "v1")
    event = {"op": "transition_registry_record", "name": item["name"], "version": "v1", "to": "APPROVED",
             "expectedHash": reviewed["expectedHash"], "actor": "forged-operator", "callerArn": "forged-arn"}
    rejected = admin_handler.handler(event, None)
    assert rejected["ok"] is False
    result = admin_handler.handler(event, ADMIN_CONTEXT)
    assert result["ok"] and result["audit"]["actor"] == "iam-invoke:" + ADMIN_CONTEXT.aws_request_id


@pytest.mark.parametrize("change", ["deprecate", "remove"])
def test_approved_consumer_never_returns_stale_index_images(store, monkeypatch, change):
    item = record(status="APPROVED")
    stale = dict(item)
    if change == "deprecate":
        api.transition(item["name"], "v1", "DEPRECATED", "iam-admin", "Synthetic retirement")
    else:
        store.table().delete_item(Key={"pk": "rec#" + item["name"], "sk": "v1"})
    monkeypatch.setattr(store, "by_status", lambda status: [stale])
    assert api.list_approved() == []
    ctx, messages = context()
    routes.registry_consumer(ctx, {})
    assert messages[-1]["records"] == []
