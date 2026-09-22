"""User requests stay local; only the IAM Admin Lambda applies AgentCore changes."""
import hashlib
import json
import re
import time

from registry import api
from registry.model import ConflictError, NotFoundError, ValidationError, check_transition


def fingerprint(record):
    value = {key: record.get(key) for key in ("name", "recordVersion", "recordType", "subtype", "status", "stateRevision", "payload", "description")}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def harness_fingerprint(value):
    fields = ("harnessId", "executionRoleArn", "model", "systemPrompt", "allowedTools", "tools", "skills")
    return hashlib.sha256(json.dumps({key: value.get(key) for key in fields},
        sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def request_transition(name, version, target, actor, reason):
    record = api.get_record(name, version)
    if not record or record.get("recordType") != "AGENT":
        raise NotFoundError("Agent record not found")
    check_transition(record["status"], target, reason)
    request = {"operation": "agent-transition", "name": name, "version": version, "to": target,
               "reason": reason, "expectedHash": fingerprint(record),
               "specHash": fingerprint({**record, "status": None, "stateRevision": None}),
               "expectedRevision": record.get("stateRevision", 0), "actor": actor}
    key = "agent-request-" + hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:48]
    saved = api.get_record(key, "v1")
    if not saved:
        saved = api.create_record({"name": key, "recordVersion": "v1", "recordType": "CUSTOM",
            "subtype": "AGENT_ADMIN_REQUEST", "description": "IAM administrator agent transition request",
            "owner": actor[:80], "tags": ["agent-admin-request"], "payload": request}, actor=actor, embed=False)
    return {"record": record, "request": {"name": saved["name"], "version": "v1", "status": "PENDING_ADMIN"},
            "agentcoreRegistry": {"status": "PENDING_ADMIN"}, "message": "관리자 처리 요청을 기록했습니다."}


def apply_request(name, version="v1", *, reconcile_hash=None):
    """Called only by admin_handler; this function is not a WebSocket route."""
    from agentcore import harness, registry_mirror
    request = api.get_record(name, version)
    if not request or request.get("recordType") != "CUSTOM" or request.get("subtype") != "AGENT_ADMIN_REQUEST":
        raise NotFoundError("Agent administration request not found")
    body = request["payload"]
    if (not isinstance(body, dict)
            or set(body) != {"operation", "name", "version", "to", "reason", "expectedHash", "specHash", "expectedRevision", "actor"}
            or body["operation"] != "agent-transition"
            or type(body["expectedRevision"]) is not int or body["expectedRevision"] < 0
            or any(not isinstance(body[key], str) or not re.fullmatch(r"[a-f0-9]{64}", body[key])
                   for key in ("expectedHash", "specHash"))):
        raise ValidationError("Invalid agent administration request")
    record = api.get_record(body["name"], body["version"])
    if not record or record.get("recordType") != "AGENT":
        raise NotFoundError("Requested agent not found")
    if request["status"] == "APPROVED":
        return {"record": record, "request": request, "replayed": True}
    applied = (record["status"] == body["to"] and record.get("stateRevision") == body["expectedRevision"] + 1
               and fingerprint({**record, "status": None, "stateRevision": None}) == body.get("specHash"))
    if fingerprint(record) != body["expectedHash"] and not applied:
        raise ConflictError("The agent changed after this request; submit a new request")
    if not applied:
        check_transition(record["status"], body["to"], body["reason"])
    if body["to"] == "APPROVED":
        if record.get("payload", {}).get("runtime") != "AgentCore Harness":
            raise ValidationError("Built-in Runtime approvals use the IAM seed_agents operation")
        from handlers.agents import _validate_create
        spec, error = _validate_create({**record["payload"], "name": record["name"],
            "description": record["description"], "title": record["payload"].get("title")})
        if error or not spec:
            raise ValidationError(error or "Invalid approved agent specification")
        if spec.get("memory"):
            raise ValidationError("Long-term Harness memory requires separate privacy and extraction review")
        expected = harness.build_config(spec)
        existing = harness.find_harness(expected["harnessName"])
        if existing:
            differs = any(existing.get(field) != expected[field] for field in
                          ("executionRoleArn", "model", "systemPrompt", "allowedTools", "tools", "skills"))
            if differs:
                if not reconcile_hash:
                    raise ConflictError("Existing Harness differs; use reconcile_agent_request with its reviewed configuration hash")
                if harness_fingerprint(existing) != reconcile_hash:
                    raise ConflictError("Harness changed after the reconciliation inspection")
                if any(row["status"] == "APPROVED" for row in api.get_store().versions(record["name"])):
                    raise ConflictError("Deprecate approved consumers before changing their Harness")
                if fingerprint(api.get_record(record["name"], record["recordVersion"])) != body["expectedHash"]:
                    raise ConflictError("Request source changed before reconciliation")
                parameters = {key: value for key, value in expected.items() if key not in {"harnessName", "tags"}}
                harness.ctl().update_harness(harnessId=existing["harnessId"],
                    clientToken=hashlib.sha256((name + reconcile_hash).encode()).hexdigest(), **parameters)
                for _ in range(30):
                    current = harness.ctl().get_harness(harnessId=existing["harnessId"])["harness"]
                    if current.get("status") in {"READY", "ACTIVE"}:
                        if any(current.get(field) != expected[field] for field in
                               ("executionRoleArn", "model", "systemPrompt", "allowedTools", "tools", "skills")):
                            raise ConflictError("Reconciled Harness does not match the requested specification")
                        break
                    if current.get("status") in {"FAILED", "DELETE_FAILED"}:
                        raise RuntimeError("Harness reconciliation failed")
                    time.sleep(3)
                else:
                    raise RuntimeError("Harness reconciliation did not finish")
        else:
            harness.ensure_harness(spec)
    updated, audit = ((record, None) if applied else
        api.transition(record["name"], record["recordVersion"], body["to"], "admin", body["reason"], expected_record=record))
    # User prompts, identities and private request reasons are not discovery metadata.
    mirrored = registry_mirror.mirror({
        "name": updated["name"], "recordVersion": updated["recordVersion"], "recordType": "AGENT",
        "status": updated["status"], "description": "Bank Tier 0/1 agent metadata",
    })
    if mirrored.get("status") != updated["status"]:
        raise RuntimeError("Agent Registry synchronization is incomplete")
    if request["status"] == "DRAFT":
        api.transition(name, version, "PENDING_APPROVAL", "admin", "IAM administration accepted request")
    saved, _ = api.transition(name, version, "APPROVED", "admin", "Agent transition applied")
    return {"record": updated, "request": saved, "audit": audit, "agentcoreRegistry": mirrored}
