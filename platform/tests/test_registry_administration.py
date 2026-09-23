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
def store(monkeypatch):
    from agentcore import registry_mirror
    monkeypatch.setattr(registry_mirror, "mirror", lambda record: {"status": record["status"], "recordId": "synthetic"})
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
@pytest.mark.parametrize("target", ["APPROVED", "REJECTED", "DEPRECATED"])
def test_iam_decision_uses_exact_inspected_record(kind, target):
    item = record(kind, "APPROVED" if target == "DEPRECATED" else "DRAFT")
    ctx, messages = context()
    if target != "DEPRECATED":
        routes.registry_transition(ctx, {"name": item["name"], "version": "v1", "to": "PENDING_APPROVAL"})
        assert messages[-1]["ok"] is True
    inspected = admin_handler.handler({"op": "inspect_registry_record", "name": item["name"], "version": "v1"}, ADMIN_CONTEXT)
    event = {"op": "transition_registry_record", "name": item["name"], "version": "v1", "to": target,
             "expectedHash": inspected["expectedHash"], "reason": "Synthetic decision"}
    stale = admin_handler.handler({**event, "expectedHash": "0" * 64}, ADMIN_CONTEXT)
    assert stale["ok"] is False and stale["code"] == 409
    if target in {"REJECTED", "DEPRECATED"}:
        missing = admin_handler.handler({**event, "reason": ""}, ADMIN_CONTEXT)
        assert missing["ok"] is False
    result = admin_handler.handler(event, ADMIN_CONTEXT)
    assert result["ok"] and result["record"]["status"] == target
    assert result["audit"]["actor"] == "iam-invoke:" + ADMIN_CONTEXT.aws_request_id
    assert [r["name"] for r in api.list_approved()] == ([item["name"]] if target == "APPROVED" else [])


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


def test_generic_mirror_failure_is_partial_and_retry_uses_fresh_inspection(monkeypatch):
    from agentcore import registry_mirror
    item = record(status="PENDING_APPROVAL")
    inspected = administration.inspect(item["name"], "v1")
    monkeypatch.setattr(registry_mirror, "mirror", lambda record: (_ for _ in ()).throw(RuntimeError("failed")))
    result = administration.transition(item["name"], "v1", "APPROVED", inspected["expectedHash"],
                                       actor_ref="iam-invoke:" + ADMIN_CONTEXT.aws_request_id)
    assert result["applied"] and not result["completed"] and result["record"]["status"] == "APPROVED"
    before = api.audit_trail(item["name"], "v1")
    monkeypatch.setattr(registry_mirror, "mirror", lambda record: {"status": record["status"], "recordId": "synthetic"})
    inspected = administration.inspect(item["name"], "v1")
    retried = administration.transition(item["name"], "v1", "APPROVED", inspected["expectedHash"],
                                        actor_ref="iam-invoke:" + ADMIN_CONTEXT.aws_request_id)
    assert retried["completed"] and retried["audit"] is None
    assert api.audit_trail(item["name"], "v1") == before


@pytest.mark.parametrize("target,status", [("APPROVED", "PENDING_APPROVAL"),
                                         ("REJECTED", "PENDING_APPROVAL"), ("DEPRECATED", "APPROVED")])
def test_iam_decision_and_audit_rollback_together(store, monkeypatch, target, status):
    item = record(status=status)
    reviewed = administration.inspect(item["name"], "v1")
    before = store.table().dump()
    put = store.table().put_item
    def fail_audit(**kwargs):
        if kwargs["Item"]["pk"].startswith("audit#"):
            raise RuntimeError("synthetic audit failure")
        return put(**kwargs)
    monkeypatch.setattr(store.table(), "put_item", fail_audit)
    result = admin_handler.handler({"op": "transition_registry_record", "name": item["name"], "version": "v1",
                                    "to": target, "reason": "Synthetic", "expectedHash": reviewed["expectedHash"]}, ADMIN_CONTEXT)
    assert result["ok"] is False and store.table().dump() == before


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


def test_generic_mirror_serializes_overlapping_decisions(store, monkeypatch):
    from agentcore import registry_mirror
    from registry.model import ConflictError
    item = record(status="PENDING_APPROVAL")
    def concurrent(current):
        reviewed = administration.inspect(item["name"], "v1")
        with pytest.raises(ConflictError, match="in progress"):
            administration.transition(item["name"], "v1", "DEPRECATED", reviewed["expectedHash"], "retire",
                                      actor_ref="iam-invoke:" + ADMIN_CONTEXT.aws_request_id)
        return {"status": current["status"]}
    monkeypatch.setattr(registry_mirror, "mirror", concurrent)
    result = administration.transition(item["name"], "v1", "APPROVED", administration.fingerprint(item),
                                      actor_ref="iam-invoke:" + ADMIN_CONTEXT.aws_request_id)
    assert result["completed"] and not result["sourceChanged"]
    assert all(not row["pk"].startswith("admin-sync#") for row in store.table().dump())


def test_generic_mirror_never_reports_stale_state_complete(store, monkeypatch):
    from agentcore import registry_mirror
    item = record(status="PENDING_APPROVAL")
    def direct_admin_write(current):
        store.force_status(current["name"], "v1", "DEPRECATED", "admin", "Synthetic maintenance")
        return {"status": current["status"]}
    monkeypatch.setattr(registry_mirror, "mirror", direct_admin_write)
    result = administration.transition(item["name"], "v1", "APPROVED", administration.fingerprint(item),
                                      actor_ref="iam-invoke:" + ADMIN_CONTEXT.aws_request_id)
    assert result["applied"] and not result["completed"] and result["sourceChanged"]
    assert result["record"]["status"] == "DEPRECATED"


def test_expired_admin_lease_can_be_recovered_and_is_not_discoverable(store):
    store.table().put_item(Item={"pk": "admin-sync#probe", "sk": "v1", "owner": "expired", "expires": 0})
    with store.administration_lease("probe", "v1"):
        assert store.all_records() == []
        assert store.table().get_item(Key={"pk": "admin-sync#probe", "sk": "v1"})["Item"]["owner"] != "expired"
