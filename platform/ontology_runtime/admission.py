"""Exact source classification is independent of project read permission."""
from __future__ import annotations

from intake import admission as intake_admission
from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources, authority_identity
from workspace.collaboration import CollaborationError
from ontology_runtime import inspection

POLICY = "design-nonsensitive-v1"
CLASSES = frozenset({"synthetic", "public", "internal-non-sensitive"})


def identifier(reference):
    ref = schema.source_ref(reference)
    return schema.identity("admission", authority_identity(ref))


def classify(ctx, body):
    fields(body, {"requestId", "sourceRef", "classification", "reason"})
    ctx.fresh({"owner"})
    request_id = schema._identifier(body.get("requestId"))
    reference = schema.source_ref(body.get("sourceRef"))
    if body.get("classification") not in CLASSES:
        fail(400, "admission-classification", "합성·공개·검토된 비민감 내부 원본만 AgentCore에 사용할 수 있습니다.")
    reason = schema._text(body.get("reason"), 2000)
    reader = Sources(ctx)
    checks = reader.verify([reference])
    inspected = inspection.inspect_source(ctx, reference)
    identity = identifier(reference)
    current = ctx.storage.get(ctx.owner, "ac_admission", identity)
    if current and current.get("requestId") == request_id:
        if current.get("requestHash") != schema.digest(body) or current.get("inspection") != inspected:
            fail(409, "admission-request-changed", "같은 분류 요청 ID의 원본 또는 검사 기준이 변경되었습니다.")
        reader.recheck()
        return current
    record = {"id": identity, "projectId": ctx.project_id, "sourceRef": reference,
        "classification": body["classification"], "policy": POLICY, "status": "approved",
        "reviewedBy": ctx.actor, "reviewedAt": ctx.storage.clock(), "reason": reason, "inspection": inspected,
        "requestId": request_id, "requestHash": schema.digest(body)}
    reader.recheck()
    return ctx.commit([ctx.write("ac_admission", record, current["version"] if current else None)], checks)[0]


def require(ctx, reference):
    try:
        row = ctx.get("ac_admission", identifier(reference))
    except CollaborationError as error:
        if error.status != 404:
            raise
        fail(403, "agentcore-input-unclassified", "AgentCore에 사용할 원본의 비민감 분류 검토가 필요합니다.")
    receipt = row.get("inspection", {})
    source = ctx.get("asset", reference["sourceId"]) if reference["sourceKind"] == "asset" else {}
    if (receipt.get("policy") != inspection.POLICY or receipt.get("profileHash") != inspection.profile()
            or receipt.get("sourceHash") != reference["sha256"]
            or receipt.get("descriptorHash") != schema.digest([reference, source.get("name")])
            or receipt.get("detectedIdentifiers") != 0):
        fail(403, "agentcore-inspection-stale", "현재 원본에 대한 비공개 경계 검사를 다시 실행해야 합니다.")
    if (row.get("policy") != POLICY or row.get("status") != "approved"
            or row.get("classification") not in CLASSES
            or identifier(row["sourceRef"]) != identifier(reference)):
        fail(403, "agentcore-input-unclassified", "AgentCore에 사용할 원본의 비민감 분류 검토가 필요합니다.")
    # The owner's self-reported classification is not authority by itself
    # (source-admission/1, AUTH-08): every transfer must still be backed by a
    # currently active, IAM-administered policy and, per data class, a
    # registered provenance (synthetic/public) or a current reviewer grant
    # (internal-non-sensitive) for the actor who recorded the classification.
    # Missing or revoked authority fails closed on every call, not only once.
    data_class = row["classification"]
    try:
        policy = intake_admission.current_policy(ctx.storage, ctx.project_id)
    except intake_admission.AdmissionError:
        fail(403, "agentcore-admission-authority-missing",
             "AgentCore 전송을 승인하는 관리형 정책이 없습니다.")
    if data_class not in policy["dataClasses"]:
        fail(403, "agentcore-admission-authority-missing",
             "현재 정책이 이 분류의 AgentCore 전송을 허용하지 않습니다.")
    if data_class in ("synthetic", "public"):
        provenance = intake_admission.find_provenance(ctx.storage, policy, reference, ctx.project_id, data_class)
        if provenance is None:
            fail(403, "agentcore-admission-authority-missing",
                 "등록된 출처 증명이 없어 AgentCore 전송을 승인할 수 없습니다.")
    else:
        grant = intake_admission.find_grant(ctx.storage, row["reviewedBy"], policy, ctx.project_id)
        if grant is None:
            fail(403, "agentcore-admission-authority-missing",
                 "검토자 권한이 없어 AgentCore 전송을 승인할 수 없습니다.")
    return ctx.check("ac_admission", row)
