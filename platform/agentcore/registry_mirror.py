"""AgentCore Agent Registry 미러 — 플랫폼 Registry(DynamoDB, 감사·유일성)의 AGENT/MCP/SKILL 레코드를
AgentCore Registry(us-east-1, `AGENTCORE_REGISTRY_ID`)에 동기화한다. 승인 워크플로우는
submit_registry_record_for_approval / update_registry_record_status 로 그대로 반영된다.

에이전트 발견(discovery)의 정본은 AgentCore Registry, 거버넌스 원장(감사 이벤트·유일성·하이브리드 검색)은 플랫폼 Registry.
"""
from __future__ import annotations

import json
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
                                        "inlineContent": json.dumps({"tools": tools}, ensure_ascii=False)}}}
    if rt == "SKILL" and payload.get("skillMd"):
        return "AGENT_SKILLS", {"agentSkills": {"skillMd": {"inlineContent": str(payload["skillMd"])[:60000]}}}
    body = {k: v for k, v in record.items() if k not in ("embedding",)}
    return "CUSTOM", {"custom": {"inlineContent": json.dumps(body, ensure_ascii=False, default=str)[:60000]}}


def find_record(name: str, version: str) -> dict | None:
    token = None
    while True:
        kw = {"registryId": REGISTRY_ID, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = ctl().list_registry_records(**kw)
        for rec in r.get("registryRecords", []):
            if rec.get("name") == _mirror_name(name) and str(rec.get("recordVersion", "")) == str(version):
                return rec
        token = r.get("nextToken")
        if not token:
            return None


def _mirror_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:48]


def mirror(record: dict) -> dict:
    """레코드를 생성/갱신하고 상태를 맞춘다. 반환: {recordId, status, action}."""
    name, version = record["name"], str(record.get("recordVersion", "v1"))
    dtype, descriptors = _descriptor(record)
    existing = find_record(name, version)
    if existing is None:
        try:
            r = ctl().create_registry_record(registryId=REGISTRY_ID, name=_mirror_name(name),
                                             description=(record.get("description") or name)[:1000],
                                             descriptorType=dtype, descriptors=descriptors, recordVersion=version)
        except Exception as e:  # AGENT_SKILLS 미지원 등 — CUSTOM으로 재시도
            if dtype != "CUSTOM":
                dtype, descriptors = "CUSTOM", {"custom": {"inlineContent": json.dumps(
                    {k: v for k, v in record.items() if k != "embedding"}, ensure_ascii=False, default=str)[:60000]}}
                r = ctl().create_registry_record(registryId=REGISTRY_ID, name=_mirror_name(name),
                                                 description=(record.get("description") or name)[:1000],
                                                 descriptorType=dtype, descriptors=descriptors, recordVersion=version)
            else:
                raise e
        # CreateRegistryRecord 는 recordArn 만 돌려준다 — ARN 마지막 세그먼트가 recordId
        rec_id = r.get("recordId") or str(r.get("recordArn", "")).rsplit("/", 1)[-1]
        action = "created"
    else:
        rec_id = existing.get("recordId") or str(existing.get("recordArn", "")).rsplit("/", 1)[-1]
        action = "exists"
    status = sync_status(rec_id, record.get("status", "DRAFT"), existing.get("status") if existing else "DRAFT",
                         reason=record.get("statusReason") or "platform registry sync")
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
