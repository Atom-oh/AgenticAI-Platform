"""Exact-source lifecycle decisions called only by the IAM Admin Lambda."""
import hashlib
import json
import re
import uuid

from registry import api
from registry.model import ConflictError, NotFoundError, ValidationError

DECISIONS = frozenset({"APPROVED", "REJECTED", "DEPRECATED"})


def invocation_actor(context):
    """Use trusted Lambda execution identity, never a caller-supplied IAM ARN."""
    request_id = getattr(context, "aws_request_id", "")
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A trusted Lambda invocation reference is required") from None
    return "iam-invoke:" + request_id


def fingerprint(record):
    fields = ("name", "recordVersion", "recordType", "subtype", "status", "stateRevision",
              "payload", "description", "owner", "tags")
    value = {key: record.get(key) for key in fields}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def inspect(name, version):
    record = api.get_record(name, version)
    if not record:
        raise NotFoundError("Registry record not found")
    if record.get("recordType") not in {"MCP", "SKILL", "CUSTOM"} or api.is_internal(record):
        raise ValidationError("Agent records require the Agent administration request path")
    return {"record": record, "expectedHash": fingerprint(record)}


def transition(name, version, target, expected_hash, reason="", *, actor_ref):
    if target not in DECISIONS:
        raise ValidationError("Unsupported Registry administration decision")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValidationError("An exact reviewed Registry hash is required")
    current = inspect(name, version)
    if current["expectedHash"] != expected_hash:
        raise ConflictError("Registry content changed after inspection")
    if not isinstance(actor_ref, str) or not re.fullmatch(r"iam-invoke:[0-9a-f-]{36}", actor_ref):
        raise ValidationError("A trusted administrative invocation reference is required")
    if current["record"]["status"] == target:
        record, audit = current["record"], None
    else:
        record, audit = api.transition(name, version, target, actor=actor_ref, reason=reason,
                                       expected_record=current["record"])
    from agentcore import registry_mirror
    try:
        mirrored = registry_mirror.mirror(record)
    except Exception as error:
        mirrored = {"status": "SYNC_PENDING", "errorType": type(error).__name__}
    return {"record": record, "audit": audit, "applied": True,
            "completed": mirrored.get("status") == target, "agentcoreRegistry": mirrored}
