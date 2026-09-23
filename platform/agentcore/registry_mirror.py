"""AgentCore Registry integration; platform records remain the approval authority.

AGENT descriptors expose metadata only. Typed MCP/SKILL descriptors retain their
inspected type, and failed or oversized descriptors never become partial mirrors.
"""
from __future__ import annotations

import json
import hashlib
import os
import time

import boto3

REGISTRY_REGION = os.environ.get("AGENTCORE_REGISTRY_REGION", "us-east-1")
REGISTRY_ID = os.environ.get("AGENTCORE_REGISTRY_ID", "b2hOSZL4eOhDXAyk")
_ctl = None


class MirrorUnsupported(ValueError):
    pass


def ctl():
    global _ctl
    if _ctl is None:
        _ctl = boto3.client("bedrock-agentcore-control", region_name=REGISTRY_REGION)
    return _ctl


def _descriptor(record: dict) -> tuple[str, dict]:
    """플랫폼 레코드 → (descriptorType, descriptors). SKILL은 agentSkills(skill.md), 나머지는 CUSTOM/MCP."""
    rt = record.get("recordType", "CUSTOM")
    payload = record.get("payload") or {}
    if rt == "MCP":
        tools = payload.get("tools") or []
        return "MCP", {"mcp": {"tools": {"protocolVersion": "2025-06-18",
                                        "inlineContent": _bounded(json.dumps({"tools": tools}, ensure_ascii=False))}}}
    if rt == "SKILL":
        if not isinstance(payload.get("skillMd"), str) or not payload["skillMd"].strip():
            raise MirrorUnsupported("Path-backed SKILL has no approved inline mirror descriptor")
        return "AGENT_SKILLS", {"agentSkills": {"skillMd": {"inlineContent": _bounded(payload["skillMd"])}}}
    if rt == "AGENT":
        body = {key: record.get(key) for key in ("name", "recordVersion", "recordType")}
        body["description"] = "Bank Tier 0/1 agent metadata"
    else:
        # Lifecycle clocks and status belong to the service envelope, not the
        # immutable source descriptor.
        body = {key: record.get(key) for key in
                ("name", "recordVersion", "recordType", "subtype", "description", "owner", "tags", "payload")}
    return "CUSTOM", {"custom": {"inlineContent": _bounded(json.dumps(body, ensure_ascii=False, default=str))}}


def _bounded(text):
    if len(text.encode("utf-8")) > 60000:
        raise ValueError("Registry descriptor exceeds its admitted size")
    return text


def _legacy_name(name):
    import re
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:48]


def _legacy_identity(current, name, version):
    try:
        original = json.loads(current["descriptors"]["custom"]["inlineContent"])
        return (current.get("name") in {_legacy_name(name), _old_hash_name(name)} and original.get("name") == name
                and str(original.get("recordVersion")) == version)
    except (KeyError, TypeError, ValueError):
        return False


def find_record(name: str, version: str) -> dict | None:
    token = None
    legacy = []
    matches = {}
    while True:
        kw = {"registryId": REGISTRY_ID, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = ctl().list_registry_records(**kw)
        for rec in r.get("registryRecords", []):
            if rec.get("name") == _mirror_name(name, version):
                matches[rec.get("recordId") or rec.get("recordArn")] = rec
            if rec.get("name") in {_legacy_name(name), _old_hash_name(name)}:
                legacy.append(rec)
        token = r.get("nextToken")
        if not token:
            break
    if len(matches) > 1 or None in matches:
        raise RuntimeError("Ambiguous Registry mirror identity")
    if matches:
        return next(iter(matches.values()))
    verified_legacy = []
    for record in legacy:
        rid = record.get("recordId") or str(record.get("recordArn", "")).rsplit("/", 1)[-1]
        current = ctl().get_registry_record(registryId=REGISTRY_ID, recordId=rid)
        if _legacy_identity(current, name, str(version)):
            verified_legacy.append(current)
    if len(verified_legacy) > 1:
        raise RuntimeError("Ambiguous legacy Registry mirror identity")
    return verified_legacy[0] if verified_legacy else None


def _old_hash_name(name):
    return "p_" + hashlib.sha256(("platform-registry-name-v1:" + name).encode()).hexdigest()[:46]


def _mirror_name(name: str, version: str) -> str:
    identity = json.dumps(["platform-registry-identity-v2", name, str(version)], separators=(",", ":"))
    return "p_" + hashlib.sha256(identity.encode()).hexdigest()[:46]


def _ready_record(record_id):
    """Create/UpdateRegistryRecord are asynchronous; never approve in-flight content."""
    for _ in range(60):
        current = ctl().get_registry_record(registryId=REGISTRY_ID, recordId=record_id)
        state = current.get("status")
        if state in {"DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"}:
            return current
        if state not in {"CREATING", "UPDATING"}:
            raise RuntimeError("Registry record provisioning failed")
        time.sleep(1)
    raise RuntimeError("Registry record provisioning did not finish")


def _manual_registry():
    config = ctl().get_registry(registryId=REGISTRY_ID)
    if config.get("status") != "READY" or config.get("approvalConfiguration", {}).get("autoApproval") is not False:
        raise MirrorUnsupported("Platform decisions require a ready Registry with manual approval")


def _update_descriptors(descriptors):
    """Registry PATCH wraps the union, variant, and MCP/Skill definition fields."""
    if len(descriptors) != 1:
        raise ValueError("Expected one typed Registry descriptor")
    kind, body = next(iter(descriptors.items()))
    if kind == "mcp":
        if set(body) - {"tools", "server"}:
            raise ValueError("Unsupported MCP update definition")
        body = {key: {"optionalValue": value} for key, value in body.items()}
    elif kind == "agentSkills":
        if set(body) - {"skillMd", "skillDefinition"}:
            raise ValueError("Unsupported Skill update definition")
        body = {key: {"optionalValue": value} for key, value in body.items()}
    elif kind != "custom":
        raise ValueError("Unsupported Registry update descriptor")
    return {"optionalValue": {kind: {"optionalValue": body}}}


def mirror(record: dict) -> dict:
    """Local immutable versions live in the name; service revisions are optimistic locks."""
    name, version = record["name"], str(record.get("recordVersion", "v1"))
    target = record.get("status", "DRAFT")
    wanted = _mirror_name(name, version)
    dtype, descriptors = _descriptor(record)
    description = ("Bank Tier 0/1 agent metadata" if record.get("recordType") == "AGENT"
                   else (record.get("description") or name)[:1000])
    _manual_registry()
    existing = find_record(name, version)
    current, rec_id = None, None
    if existing is not None:
        rec_id = existing.get("recordId") or str(existing.get("recordArn", "")).rsplit("/", 1)[-1]
        current = _ready_record(rec_id)
        legacy = current.get("name") != wanted
        if legacy and not _legacy_identity(current, name, version):
            raise RuntimeError("Registry mirror identity changed before synchronization")
        if current["status"] == "DEPRECATED" and target == "DEPRECATED":
            # The service forbids rewriting deprecated records. Verify inactivity
            # and retain the historical descriptor without claiming it was replaced.
            return {"recordId": rec_id, "status": "DEPRECATED", "action": "archived", "archived": True,
                    "metadataCurrent": current.get("descriptorType") == dtype and current.get("descriptors") == descriptors,
                    "descriptorType": current.get("descriptorType")}
        if legacy:
            if current["status"] != "DEPRECATED":
                sync_status(rec_id, "DEPRECATED", current["status"], "Replace legacy mirror with version-bound metadata")
                if _ready_record(rec_id)["status"] != "DEPRECATED":
                    raise RuntimeError("Legacy mirror retirement was not confirmed")
            if target == "DEPRECATED":
                return {"recordId": rec_id, "status": "DEPRECATED", "action": "archived", "archived": True,
                        "metadataCurrent": False, "descriptorType": current.get("descriptorType")}
            existing, current = None, None
        elif current["status"] == "DEPRECATED":
            raise MirrorUnsupported("A deprecated mirror requires a new immutable platform version")
    if existing is None:
        response = ctl().create_registry_record(registryId=REGISTRY_ID, name=wanted,
            description=description, descriptorType=dtype, descriptors=descriptors, recordVersion="1")
        # This mutable service revision is separate from the local vN identity.
        rec_id = response.get("recordId") or str(response.get("recordArn", "")).rsplit("/", 1)[-1]
        current = _ready_record(rec_id)
        action = "created"
    else:
        action = "exists"
        if current.get("descriptorType") != dtype or current.get("descriptors") != descriptors:
            if current["status"] == "APPROVED":
                # An edit otherwise leaves the old approved revision discoverable.
                sync_status(rec_id, "REJECTED", "APPROVED", "Descriptor revision requires platform review")
                current = _ready_record(rec_id)
                if current["status"] != "REJECTED":
                    raise RuntimeError("Old approved Registry revision was not hidden")
            response = ctl().update_registry_record(registryId=REGISTRY_ID, recordId=rec_id,
                recordVersion=current["recordVersion"], descriptorType=dtype,
                descriptors=_update_descriptors(descriptors),
                description={"optionalValue": description})
            current = _ready_record(rec_id)
            if (not isinstance(response, dict) or not response.get("recordVersion")
                    or current.get("recordVersion") != response["recordVersion"]):
                raise RuntimeError("Registry update revision was not confirmed")
            action = "updated"
    if (current.get("name") != wanted or not current.get("recordVersion")
            or current.get("descriptorType") != dtype or current.get("descriptors") != descriptors):
        raise RuntimeError("Registry descriptor was not confirmed before its status decision")
    service_revision = current["recordVersion"]
    status = sync_status(rec_id, target, current["status"], reason=record.get("statusReason") or "platform registry sync")
    current = _ready_record(rec_id)
    if (current.get("name") != wanted or current.get("recordVersion") != service_revision
            or current.get("descriptorType") != dtype or current.get("descriptors") != descriptors
            or current.get("status") != target):
        raise RuntimeError("Registry mirror completion was not confirmed")
    return {"recordId": rec_id, "status": status, "action": action, "descriptorType": dtype,
            "serviceVersion": service_revision, "metadataCurrent": True}


def sync_status(rec_id: str, target: str, current: str | None, reason: str = "") -> str:
    """플랫폼 상태 → AgentCore 상태. DRAFT→PENDING은 submit_for_approval, 그 외는 update_status."""
    if current == target:
        return target
    try:
        if current == "DRAFT" and target in {"APPROVED", "REJECTED"}:
            ctl().submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=rec_id)
            current = _ready_record(rec_id)["status"]
            if current != "PENDING_APPROVAL":
                raise RuntimeError("Registry did not retain the pending review state")
        if target == "PENDING_APPROVAL":
            ctl().submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=rec_id)
        else:
            ctl().update_registry_record_status(registryId=REGISTRY_ID, recordId=rec_id, status=target,
                                                statusReason=(reason or "platform registry sync")[:250])
        return target
    except Exception as e:
        return f"{current or 'DRAFT'} (sync failed: {type(e).__name__})"
