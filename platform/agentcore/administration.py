"""User requests stay local; only the IAM Admin Lambda applies AgentCore changes."""
import hashlib
import json
import re
import time

from registry import api
from registry.model import ConflictError, NotFoundError, ValidationError, check_transition

HARNESS_FIELDS = ("executionRoleArn", "model", "systemPrompt", "allowedTools", "tools", "skills",
                  "maxIterations", "maxTokens", "timeoutSeconds", "memory", "environment",
                  "environmentArtifact", "environmentVariables", "authorizerConfiguration", "truncation", "hooks")
HARNESS_METADATA = frozenset({"harnessId", "harnessName", "arn", "harnessArn", "status", "harnessVersion",
                              "createdAt", "updatedAt", "statusReason", "tags", "clientToken"})


def harness_settings(value):
    if not isinstance(value, dict) or set(value) - set(HARNESS_FIELDS) - HARNESS_METADATA:
        raise ValidationError("Unreviewed Harness configuration fields require IAM review")
    settings = {key: value.get(key) for key in HARNESS_FIELDS}
    environment = settings.get("environment")
    if isinstance(environment, dict) and isinstance(environment.get("agentCoreRuntimeEnvironment"), dict):
        config = environment["agentCoreRuntimeEnvironment"]
        settings["environment"] = {**environment, "agentCoreRuntimeEnvironment": {
            key: value for key, value in config.items() if key not in {"agentRuntimeArn", "agentRuntimeName", "agentRuntimeId"}}}
    settings["environmentVariables"] = settings["environmentVariables"] or {}
    settings["hooks"] = settings["hooks"] or []
    return settings


def fingerprint(record):
    value = {key: record.get(key) for key in ("name", "recordVersion", "recordType", "subtype", "status", "stateRevision", "payload", "description")}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def harness_fingerprint(value):
    return hashlib.sha256(json.dumps({"harnessId": value.get("harnessId"), **harness_settings(value)},
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
    saved = api.get_record(key, "v1", include_internal=True)
    if saved and (saved.get("subtype") != "AGENT_ADMIN_REQUEST" or saved.get("payload") != request):
        raise ConflictError("The administrative request identifier is already occupied")
    if not saved:
        saved = api.create_record({"name": key, "recordVersion": "v1", "recordType": "CUSTOM",
            "subtype": "AGENT_ADMIN_REQUEST", "description": "IAM administrator agent transition request",
            "owner": actor[:80], "tags": ["agent-admin-request"], "payload": request}, actor=actor, embed=False)
    return {"record": api.public_record(record), "request": {"name": saved["name"], "version": "v1", "status": "PENDING_ADMIN"},
            "agentcoreRegistry": {"status": "PENDING_ADMIN"}, "message": "관리자 처리 요청을 기록했습니다."}


def apply_request(name, version="v1", *, reconcile_hash=None, actor_ref="admin"):
    """Called only by admin_handler; this function is not a WebSocket route."""
    from agentcore import harness, registry_mirror
    request = api.get_record(name, version, include_internal=True)
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
        return {"record": record, "request": request, "replayed": True, "applied": True, "completed": True}
    applied = (record["status"] == body["to"] and record.get("stateRevision") == body["expectedRevision"] + 1
               and fingerprint({**record, "status": None, "stateRevision": None}) == body.get("specHash"))
    if fingerprint(record) != body["expectedHash"] and not applied:
        raise ConflictError("The agent changed after this request; submit a new request")
    if not applied:
        check_transition(record["status"], body["to"], body["reason"])
    if body["to"] == "APPROVED":
        if record.get("payload", {}).get("runtime") != "AgentCore Harness":
            raise ValidationError("Built-in Runtime approvals use the IAM seed_agents operation")
        if "skillBindings" not in record["payload"]:
            raise ValidationError("SKILL approval bindings are missing; submit a new agent specification")
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
            if existing.get("status") not in {"READY", "ACTIVE"}:
                raise ConflictError("Existing Harness is not ready for approval")
            differs = harness_settings(existing) != harness_settings(expected)
            if differs:
                if not reconcile_hash:
                    raise ConflictError("Existing Harness differs; use reconcile_agent_request with its reviewed configuration hash")
                if harness_fingerprint(existing) != reconcile_hash:
                    raise ConflictError("Harness changed after the reconciliation inspection")
                if any(row["status"] == "APPROVED" for row in api.get_store().versions(record["name"])):
                    raise ConflictError("Deprecate approved consumers before changing their Harness")
                if fingerprint(api.get_record(record["name"], record["recordVersion"])) != body["expectedHash"]:
                    raise ConflictError("Request source changed before reconciliation")
                if any(existing.get(key) not in (None, {}, []) for key in
                       ("environmentArtifact", "authorizerConfiguration", "hooks")):
                    raise ConflictError("Remove unexpected Harness extensions through IAM before reconciliation")
                parameters = harness.update_parameters(expected)
                harness.ctl().update_harness(harnessId=existing["harnessId"],
                    clientToken=hashlib.sha256((name + reconcile_hash).encode()).hexdigest(), **parameters)
                for _ in range(30):
                    current = harness.ctl().get_harness(harnessId=existing["harnessId"])["harness"]
                    if current.get("status") in {"READY", "ACTIVE"}:
                        if harness_settings(current) != harness_settings(expected):
                            raise ConflictError("Reconciled Harness does not match the requested specification")
                        break
                    if current.get("status") in {"FAILED", "DELETE_FAILED"}:
                        raise RuntimeError("Harness reconciliation failed")
                    time.sleep(3)
                else:
                    raise RuntimeError("Harness reconciliation did not finish")
        else:
            created = harness.ensure_harness(spec)
            if created.get("status") not in {"READY", "ACTIVE"} or harness_settings(created) != harness_settings(expected):
                raise ConflictError("Provisioned Harness is not ready with the requested configuration")
    if body["to"] == "APPROVED":
        from agentcore.skill_binding import resolve
        resolve(record["payload"].get("skillBindings", []), record["payload"].get("skills", []))
    updated, audit = ((record, None) if applied else
        api.transition(record["name"], record["recordVersion"], body["to"], actor_ref, body["reason"], expected_record=record))
    # User prompts, identities and private request reasons are not discovery metadata.
    try:
        mirrored = registry_mirror.mirror({
            "name": updated["name"], "recordVersion": updated["recordVersion"], "recordType": "AGENT",
            "status": updated["status"], "description": "Bank Tier 0/1 agent metadata",
        })
    except Exception as e:
        mirrored = {"status": "SYNC_PENDING", "errorType": type(e).__name__}
    if mirrored.get("status") != updated["status"]:
        # Local approval is already committed; a retry finishes the metadata mirror.
        return {"record": updated, "request": request, "audit": audit,
                "agentcoreRegistry": mirrored, "applied": True, "completed": False}
    if request["status"] == "DRAFT":
        api.transition(name, version, "PENDING_APPROVAL", actor_ref, "IAM administration accepted request")
    saved, _ = api.transition(name, version, "APPROVED", actor_ref, "Agent transition applied")
    return {"record": updated, "request": saved, "audit": audit, "agentcoreRegistry": mirrored,
            "applied": True, "completed": True}
