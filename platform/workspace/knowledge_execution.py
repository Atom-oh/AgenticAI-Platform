"""Admitted guideline proposals through the sole B0 writer; no model approval."""
from __future__ import annotations

import hashlib
import json

from design_loop import guide_rules
from design_loop.references import References
from intake import admission, derivative, inspect
from workbench.service import fields, fail
from workspace import ontology_schema as schema
from workspace.design_api import inputs, _matches
from workspace.execution_ledger import LedgerError
from workspace.execution_source import encode, view_job, view_artifact
from workspace.execution_sources import context, unique
from workspace.ontology_store import Ontology
from workspace.ontology_ux import parse_when

OPERATION = "design.guidelines"
OPERATIONS = frozenset({OPERATION, "design.derive"})
KIND = "knowledge-stage"
ROLES = {"owner", "designer"}
READ_ROLES = ROLES | {"developer"}


def selected_context(ctx, decision_ids, node_ids):
    admitted = inputs(ctx, decision_ids)
    if (not isinstance(node_ids, list) or not 1 <= len(node_ids) <= 20
            or len(set(node_ids)) != len(node_ids)):
        fail(422, "knowledge-target-limit", "현재 자산을 1~20개 선택하세요.")
    for identifier in node_ids:
        schema._identifier(identifier)
    store = Ontology(ctx)
    snapshot = store.context(node_ids)
    if snapshot.get("coverage", {}).get("truncated"):
        fail(422, "knowledge-context-incomplete", "검증 가능한 자산 범위를 줄이세요.")
    nodes = {node["id"]: node for node in snapshot["nodes"]}
    if any(nodes[identifier]["reviewState"] != "approved" or nodes[identifier]["tombstone"]
           for identifier in node_ids):
        fail(409, "knowledge-target-unapproved", "정확한 버전이 승인된 자산을 선택하세요.")
    for ref in snapshot["sourceRefs"]:
        if ref["sourceKind"] != "package" and not any(_matches(ref, source) for source in admitted["sourceRefs"]):
            fail(409, "knowledge-source-admission-required", "자산의 모든 원본에 현재 반입 승인이 필요합니다.")
    terms = derivative.canonical_entries(admission._denylist(ctx.host))
    text = References(snapshot).encode(encode(snapshot).decode())
    inspected = inspect.inspect([{"page": 1, "text": text}], denylist=terms)
    checked = derivative.residual([{"page": 1, "text": text}], terms)
    if checked["identifiers"] or checked["pii"]:
        fail(409, "knowledge-context-sensitive", "자산 메타데이터의 비공개 식별자를 먼저 정리하세요.")
    current = store.current()
    if current["generation"] != snapshot["generation"]:
        fail(409, "ontology-changed", "조회 중 자산 기준이 변경되었습니다.")
    checks = unique([*admitted["checks"], *store.sources.recheck(), ctx.check("ontology", current)])
    return admitted, snapshot, checks, schema.digest(inspected)


def condition_ids(snapshot):
    expressions = []
    for node in snapshot["nodes"]:
        properties = node.get("properties", {})
        expressions += [row["when"] for row in properties.get("uxModel", {}).get("conditions", [])]
        if properties.get("appliesWhen"):
            expressions.append(properties["appliesWhen"])
    expressions += [edge["properties"]["when"] for edge in snapshot.get("edges", [])
                    if edge.get("properties", {}).get("when")]
    return sorted({identifier for expression in expressions for _, identifier in parse_when(expression)})


def verify_proposal(manifest, response):
    """Pure replay of extraction checks; never calls a provider."""
    if manifest["operation"] == "design.derive":
        from workspace.derive_proposals import verify_proposal as verify_derive
        return verify_derive(manifest, response)
    if not isinstance(response, str) or len(response.encode()) > 512 * 1024:
        raise LedgerError("knowledge-proposal-invalid")
    deps = {"normalize": lambda text: text, "generate": lambda *args: response}
    checked = guide_rules.extract_rules(manifest["pages"], deps, asset_ids=manifest["nodeIds"],
        model=manifest["model"], condition_ids=condition_ids(manifest["snapshot"]))
    if checked.get("blocked"):
        raise LedgerError("knowledge-proposal-invalid")
    # A mixed valid/invalid proposal stays inspectable, never a publishable success.
    if not checked["rules"] and not checked["rejected"]:
        checked["rejected"].append({"reason": "no-rules"})
    revisions = {node["id"]: node["revision"] for node in manifest["snapshot"]["nodes"]}
    refs = {source["sourceId"]: source for source in manifest["sourceRefs"]}
    graph = guide_rules.rule_graph(checked["rules"], project_id=manifest["projectId"],
                                  source_ref_for=lambda citation: refs[citation["sourceId"]], revisions=revisions)
    return {"rules": checked["rules"], "rejected": [*checked["rejected"], *graph["rejected"]],
            "graph": {"schemaVersion": 1, "projectId": manifest["projectId"],
                      "nodes": graph["nodes"], "edges": graph["edges"]},
            "approvalEffect": False}


def context_for(ctx, value):
    if value.get("operation") == "design.derive":
        from workspace.derive_proposals import selected_context as derive_context
        return derive_context(ctx, value["decisionIds"], value["nodeIds"], value["analysisId"])
    return selected_context(ctx, value.get("decisionIds"), value.get("nodeIds"))


def submit(ctx, body, ledger, configuration, enqueue, *, operation=OPERATION):
    if operation not in OPERATIONS:
        raise LedgerError("profile-invalid")
    fields(body, {"requestId", "name", "decisionIds", "nodeIds", "model", "expectedGeneration"}
           | ({"analysisId"} if operation == "design.derive" else set()))
    ctx.fresh(ROLES)
    settings = configuration.get("design") or {}
    model = body.get("model")
    if ctx.project_id not in settings.get("projects", []) or model not in settings.get("models", []):
        raise LedgerError("knowledge-execution-unavailable")
    name = schema._identifier(body.get("name"))
    identifier = ctx.identity("wb_artifact", body.get("requestId"))
    old = ctx.storage.get(ctx.owner, "wb_artifact", identifier)
    request_hash = schema.digest(body if operation == OPERATION else {**body, "operation": operation})
    if old:
        if old.get("kind") != KIND or old.get("requestHash") != request_hash or old.get("createdBy") != ctx.actor:
            raise LedgerError("request-changed")
        authorized(ctx, old)
        job = ledger.api().read(ctx.owner, old["jobId"])
        if job["status"] == "queued":
            enqueue(ctx.owner, job["id"])
        return {"artifact": view_artifact(old), "job": view_job(job)}
    admitted, snapshot, checks, inspection = context_for(ctx, {**body, "operation": operation})
    if snapshot["generation"] != body.get("expectedGeneration"):
        raise LedgerError("ontology-changed")
    manifest = {"schemaVersion": 1, "operation": operation, "projectId": ctx.project_id,
        "snapshot": snapshot, "pages": admitted["pages"], "inspection": inspection, "model": model,
        "nodeIds": body["nodeIds"], "decisionIds": body["decisionIds"], "sourceRefs": admitted["sourceRefs"],
        "tool": configuration["interpreter"], "admissions": admitted["admissions"], "sourceChecks": checks,
        "sourceBindings": [{"sourceKind": row["sourceKind"], "sourceId": row["sourceId"],
                           "revision": row["revision"], "audience": row["audienceRevision"]}
                          for row in admitted["sourceRefs"]]}
    if operation == "design.derive":
        manifest.update(analysisId=body["analysisId"], derivation=admitted["derivation"])
    raw = encode(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    key = ctx.storage.key_for(ctx.owner, "wb_artifact", identifier, "input-" + digest + ".json")
    ctx.storage.put_blob_once(key, raw, "application/json")
    project = ctx.scope["project"]
    job = ledger.api().admit(ctx.owner, request_key=body["requestId"], actor=ctx.actor, project_id=ctx.project_id,
        operation=operation, model=model, manifest={"ref": key, "hash": digest}, admissions=admitted["admissions"],
        backend_config_revision=configuration["revision"], authorization_expires_at=admitted["expiresAt"],
        project_authority={"authorityRevision": project["authorityRevision"], "membershipDigest": schema.digest(project["members"])},
        completion_scope={"sourceChecks": len(checks), "sourceBindings": len(manifest["sourceBindings"]), "nonSourceOperations": 20},
        artifact={"id": identifier, "kind": KIND, "name": name, "expectedGeneration": snapshot["generation"],
                  "sourceRefs": admitted["sourceRefs"], "requestHash": request_hash})
    enqueue(ctx.owner, job["id"])
    return {"artifact": view_artifact(ctx.get("wb_artifact", identifier)), "job": view_job(job)}


def manifest_for(ctx, artifact):
    if artifact.get("kind") != KIND:
        fail(404, "not-found", "지식화 후보를 찾을 수 없습니다.")
    job = ctx.storage.get(ctx.owner, "job", artifact["jobId"])
    if job and (job.get("operation") not in OPERATIONS or job.get("artifact", {}).get("id") != artifact["id"]):
        fail(404, "not-found", "지식화 후보를 찾을 수 없습니다.")
    pointer = artifact.get("input") or (job or {}).get("manifest")
    if not pointer:
        fail(404, "not-found", "지식화 입력 근거를 찾을 수 없습니다.")
    for binding in artifact.get("admissions", (job or {}).get("admissions", [])):
        decision, _, _ = admission.authorized(ctx.host, ctx.scope, binding["decisionId"], claims=ctx.claims)
        if str(decision["revision"]) != binding["revision"] or decision["artifact"]["sha256"] != binding["artifactHash"]:
            fail(404, "not-found", "지식화 입력 근거를 확인하지 못했습니다.")
    raw = ctx.storage.get_blob(pointer["ref"])
    if hashlib.sha256(raw).hexdigest() != pointer["hash"]:
        fail(409, "knowledge-evidence-changed", "지식화 입력 근거가 변경되었습니다.")
    manifest = json.loads(raw)
    if manifest.get("operation") not in OPERATIONS or job and manifest["operation"] != job["operation"]:
        fail(409, "knowledge-evidence-changed", "지식화 실행 종류가 변경되었습니다.")
    return manifest, job


def authorized(ctx, artifact, *, require_current=True):
    ctx.fresh(READ_ROLES)
    manifest, job = manifest_for(ctx, artifact)
    if require_current:
        admitted, snapshot, checks, _ = context_for(ctx, manifest)
    else:
        from workspace.ontology_sources import Sources
        if manifest["operation"] == "design.derive":
            from workspace.derive_proposals import source_inputs
            admitted = source_inputs(ctx, manifest["decisionIds"], manifest["analysisId"])
        else:
            admitted = inputs(ctx, manifest["decisionIds"])
        reader = Sources(ctx)
        reader.verify(manifest["snapshot"]["sourceRefs"])
        fences, deadlines = reader.recheck_deadlines()
        checks = unique([*admitted["checks"], *fences])
        admitted["expiresAt"] = min([admitted["expiresAt"], *[bound for bound, _ in deadlines]])
        snapshot = manifest["snapshot"]
    if admitted["admissions"] != manifest["admissions"] or snapshot != manifest["snapshot"]:
        fail(409, "knowledge-source-changed", "지식화 원본 또는 자산 기준이 변경되었습니다.")
    if manifest["operation"] == "design.derive" and (
            admitted["analysisEvidence"] != manifest["derivation"]["analysisEvidence"]
            or require_current and admitted["derivation"] != manifest["derivation"]):
        fail(409, "knowledge-source-changed", "역도출의 분석·사용 화면 근거가 변경되었습니다.")
    return manifest, unique([*checks, ctx.check("wb_artifact", artifact),
                            *([ctx.check("job", job)] if job else [])]), admitted["expiresAt"]


class KnowledgePublication:
    """Only composes ledger-owned artifact completion; no canonical approval."""
    def __init__(self, host, configuration):
        self.host, self.configuration = host, configuration

    def __call__(self, prepared):
        job = prepared["job"]
        ctx = context(self.host, job)
        artifact = ctx.get("wb_artifact", job["artifact"]["id"])
        if job["operation"] not in OPERATIONS or job["backendConfigRevision"] != self.configuration["revision"]:
            raise LedgerError("profile-invalid")
        manifest, checks, expiry = authorized(ctx, artifact)
        if manifest["tool"] != self.configuration["interpreter"]:
            raise LedgerError("profile-invalid")
        rows = {row["stage"]: row for row in job["stages"] if row["attemptId"] == job["attempt"]["id"]}
        proposal_ref = rows["generate"]["result"].get("proposal")
        result_ref = rows["verify"]["result"].get("knowledgeResult")
        if proposal_ref not in rows["generate"]["outputs"] or result_ref not in rows["verify"]["outputs"]:
            raise LedgerError("receipt-invalid")
        from workspace.design_api import read_object
        response = read_object(ctx, proposal_ref)["response"]
        expected = verify_proposal(manifest, response)
        result = read_object(ctx, result_ref)
        if result != expected or (job["status"] == "succeeded") != (not expected["rejected"]):
            raise LedgerError("receipt-invalid")
        checks = [check for check in checks if check["kind"] != "job"]  # B0's conditional job Put owns that fence.
        row = {**artifact, "status": "completed", "result": result_ref,
               "input": job["manifest"],
               "admissions": job["admissions"], "receiptHashes": job["result"]["receipts"],
               "proposalStatus": "review" if not expected["rejected"] else "needs-changes",
               "backendConfigRevision": job["backendConfigRevision"], "profileHash": job["profile"]["hash"]}
        return {"writes": [ctx.write("wb_artifact", row, artifact["version"])], "checks": checks,
                "expiresAt": expiry, "sourceChecks": job["obligations"]["sourceChecks"],
                "sourceBindings": job["obligations"]["sourceBindings"]}
