"""AgentCore Registry integration; platform records remain the approval authority.

AGENT descriptors expose metadata only. Typed MCP/SKILL descriptors retain their
inspected type, and failed or oversized descriptors never become partial mirrors.
"""
from __future__ import annotations

import json
import hashlib
import os

import boto3

REGISTRY_REGION = os.environ.get("AGENTCORE_REGISTRY_REGION", "us-east-1")
REGISTRY_ID = os.environ.get("AGENTCORE_REGISTRY_ID", "b2hOSZL4eOhDXAyk")
_ctl = None


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
    if rt == "SKILL" and payload.get("skillMd"):
        return "AGENT_SKILLS", {"agentSkills": {"skillMd": {"inlineContent": _bounded(str(payload["skillMd"]))}}}
    if rt == "AGENT":
        body = {key: record.get(key) for key in ("name", "recordVersion", "recordType", "subtype", "description")}
    else:
        body = {k: v for k, v in record.items() if k not in ("embedding",)}
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
        return (current.get("name") == _legacy_name(name) and original.get("name") == name
                and str(original.get("recordVersion")) == version)
    except (KeyError, TypeError, ValueError):
        return False


def find_record(name: str, version: str) -> dict | None:
    token = None
    legacy = []
    while True:
        kw = {"registryId": REGISTRY_ID, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = ctl().list_registry_records(**kw)
        for rec in r.get("registryRecords", []):
            if rec.get("name") == _mirror_name(name) and str(rec.get("recordVersion", "")) == str(version):
                return rec
            if rec.get("name") == _legacy_name(name) and str(rec.get("recordVersion", "")) == str(version):
                legacy.append(rec)
        token = r.get("nextToken")
        if not token:
            break
    for record in legacy:
        rid = record.get("recordId") or str(record.get("recordArn", "")).rsplit("/", 1)[-1]
        current = ctl().get_registry_record(registryId=REGISTRY_ID, recordId=rid)
        if _legacy_identity(current, name, str(version)):
            return current
    return None


def _mirror_name(name: str) -> str:
    return "p_" + hashlib.sha256(("platform-registry-name-v1:" + name).encode()).hexdigest()[:46]


def mirror(record: dict) -> dict:
    """레코드를 생성/갱신하고 상태를 맞춘다. 반환: {recordId, status, action}."""
    name, version = record["name"], str(record.get("recordVersion", "v1"))
    dtype, descriptors = _descriptor(record)
    existing = find_record(name, version)
    if existing is None:
        r = ctl().create_registry_record(registryId=REGISTRY_ID, name=_mirror_name(name),
                                         description=(record.get("description") or name)[:1000],
                                         descriptorType=dtype, descriptors=descriptors, recordVersion=version)
        # CreateRegistryRecord 는 recordArn 만 돌려준다 — ARN 마지막 세그먼트가 recordId
        rec_id = r.get("recordId") or str(r.get("recordArn", "")).rsplit("/", 1)[-1]
        action = "created"
    else:
        rec_id = existing.get("recordId") or str(existing.get("recordArn", "")).rsplit("/", 1)[-1]
        action = "exists"
        # Old CUSTOM mirrors can contain prompts. Refresh and verify their
        # descriptor before any approval/status synchronization.
        current = ctl().get_registry_record(registryId=REGISTRY_ID, recordId=rec_id)
        if (str(current.get("recordVersion")) != version or
                current.get("name") != _mirror_name(name) and not _legacy_identity(current, name, version)):
            raise RuntimeError("Agent mirror identity changed before synchronization")
        if (current.get("descriptorType") != dtype or current.get("descriptors") != descriptors
                or current.get("name") != _mirror_name(name)):
            ctl().update_registry_record(registryId=REGISTRY_ID, recordId=rec_id,
                name=_mirror_name(name), descriptorType=dtype, descriptors=descriptors,
                description=(record.get("description") or name)[:1000])
            current = ctl().get_registry_record(registryId=REGISTRY_ID, recordId=rec_id)
            if current.get("descriptorType") != dtype or current.get("descriptors") != descriptors:
                raise RuntimeError("Agent mirror metadata replacement was not confirmed")
            action = "updated"
        existing = {**existing, "status": current.get("status")}
    status = sync_status(rec_id, record.get("status", "DRAFT"), existing.get("status") if existing else "DRAFT",
                         reason=record.get("statusReason") or "platform registry sync")
    current = ctl().get_registry_record(registryId=REGISTRY_ID, recordId=rec_id)
    if (current.get("name") != _mirror_name(name) or str(current.get("recordVersion")) != version
            or current.get("descriptorType") != dtype or current.get("descriptors") != descriptors
            or current.get("status") != record.get("status", "DRAFT")):
        raise RuntimeError("Agent mirror completion was not confirmed")
    return {"recordId": rec_id, "status": status, "action": action, "descriptorType": dtype}


def sync_status(rec_id: str, target: str, current: str | None, reason: str = "") -> str:
    """플랫폼 상태 → AgentCore 상태. DRAFT→PENDING은 submit_for_approval, 그 외는 update_status."""
    if current == target:
        return target
    try:
        if target == "PENDING_APPROVAL":
            ctl().submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=rec_id)
        else:
            ctl().update_registry_record_status(registryId=REGISTRY_ID, recordId=rec_id, status=target,
                                                statusReason=(reason or "platform registry sync")[:250])
        return target
    except Exception as e:
        return f"{current or 'DRAFT'} (sync failed: {type(e).__name__})"
