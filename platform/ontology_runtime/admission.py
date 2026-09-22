"""Exact source classification is independent of project read permission."""
from __future__ import annotations

from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources, authority_identity
from workspace.collaboration import CollaborationError

POLICY = "design-nonsensitive-v1"
CLASSES = frozenset({"synthetic", "public", "internal-non-sensitive"})


def identifier(reference):
    ref = schema.source_ref(reference)
    return schema.identity("admission", authority_identity(ref))


def classify(ctx, body):
    fields(body, {"requestId", "sourceRef", "classification", "reason"})
    ctx.fresh({"owner"})
    reference = schema.source_ref(body.get("sourceRef"))
    if body.get("classification") not in CLASSES:
        fail(400, "admission-classification", "합성·공개·검토된 비민감 내부 원본만 AgentCore에 사용할 수 있습니다.")
    reason = schema._text(body.get("reason"), 2000)
    reader = Sources(ctx)
    checks = reader.verify([reference])
    identity = identifier(reference)
    current = ctx.storage.get(ctx.owner, "ac_admission", identity)
    record = {"id": identity, "projectId": ctx.project_id, "sourceRef": reference,
        "classification": body["classification"], "policy": POLICY, "status": "approved",
        "reviewedBy": ctx.actor, "reviewedAt": ctx.storage.clock(), "reason": reason}
    reader.recheck()
    return ctx.commit([ctx.write("ac_admission", record, current["version"] if current else None)], checks)[0]


def require(ctx, reference):
    try:
        row = ctx.get("ac_admission", identifier(reference))
    except CollaborationError as error:
        if error.status != 404:
            raise
        fail(403, "agentcore-input-unclassified", "AgentCore에 사용할 원본의 비민감 분류 검토가 필요합니다.")
    if (row.get("policy") != POLICY or row.get("status") != "approved"
            or row.get("classification") not in CLASSES
            or identifier(row["sourceRef"]) != identifier(reference)):
        fail(403, "agentcore-input-unclassified", "AgentCore에 사용할 원본의 비민감 분류 검토가 필요합니다.")
    return ctx.check("ac_admission", row)
