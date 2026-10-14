"""Exact-source human review of guideline candidates; never implicit approval."""
from __future__ import annotations

import copy
from intake.admission import AdmissionError

from workbench.service import Service, fields, fail, check_source_deadlines
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.storage import Conflict
from workspace.knowledge_execution import KIND, ROLES, READ_ROLES, authorized
from workspace.execution_sources import unique
from workspace.execution_source import view_artifact
from workspace.ontology_store import Ontology
from workspace.design_api import read_object, page_for


def view(ctx, artifact):
    manifest, checks, expiry = authorized(ctx, artifact, require_current=False)
    value = {"artifact": view_artifact(artifact), "model": manifest["model"],
             "nodeIds": manifest["nodeIds"], "approvalEffect": False}
    if artifact.get("result"):
        value.update(result=read_object(ctx, artifact["result"]), resultHash=artifact["result"]["sha256"],
                     version=artifact["version"], generation=manifest["snapshot"]["generation"])
    return value, checks, expiry


def fence(ctx, checks, expiry):
    ctx.fresh()
    try:
        ctx.storage.put_many([], unique([*checks, ctx.check("project", ctx.scope["project"])]), retry_conflicts=False,
            before_attempt=lambda: check_source_deadlines(ctx.storage, checks, ctx.claims, expiry))
    except Conflict:
        fail(404, "not-found", "지식화 근거 또는 권한이 변경되었습니다.")


def publish(ctx, identifier, body):
    fields(body, {"requestId", "version", "resultHash", "expectedGeneration"})
    ctx.fresh(ROLES)
    artifact = ctx.get("wb_artifact", identifier)
    visible, checks, expiry = view(ctx, artifact)
    if artifact.get("proposalStatus") != "review" or artifact["version"] != body.get("version"):
        fail(409, "knowledge-review-required", "검증을 통과한 정확한 후보 버전을 확인하세요.")
    if body.get("resultHash") != visible.get("resultHash"):
        fail(409, "knowledge-evidence-changed", "검토한 지식화 근거가 변경되었습니다.")
    result = visible["result"]
    if result["rejected"] or not result["graph"]["nodes"]:
        fail(409, "knowledge-review-required", "누락·거절된 후보를 수정한 뒤 다시 검토하세요.")
    identity = ctx.identity("knowledge_decision", body.get("requestId"))
    request_hash = schema.digest({"artifactId": identifier, **body})
    old = ctx.storage.get(ctx.owner, "knowledge_decision", identity)
    if old:
        if old["requestHash"] != request_hash:
            fail(409, "request-changed", "같은 요청 ID의 검토 내용이 달라졌습니다.")
        fence(ctx, checks, expiry)
        return {"publication": old["publication"],
                "decision": {key: old[key] for key in ("id", "artifactId", "actor", "resultHash", "at")},
                "approvalEffect": False}
    _, current_checks, current_expiry = authorized(ctx, artifact)
    checks = unique([*checks, *current_checks])
    expiry = min(expiry, current_expiry)
    decision = {"id": identity, "projectId": ctx.project_id, "artifactId": identifier, "actor": ctx.actor,
                "resultHash": body["resultHash"], "requestHash": request_hash, "at": ctx.storage.clock()}
    graph = copy.deepcopy(result["graph"])
    # Exact extraction provenance remains in each PolicyRule's properties.
    store = Ontology(ctx)
    check_source_deadlines(ctx.storage, checks, ctx.claims, expiry)
    published = store.publish_candidate("guide-" + identifier, graph, expected_generation=body.get("expectedGeneration"),
        request_id=identity, additional_checks=checks, _stage=True)
    decision["publication"] = published["result"]
    fences, deadlines = store.sources.recheck_deadlines()
    expiry = min([expiry, *[bound for bound, _ in deadlines]])
    try:
        writes = [*published["writes"], ctx.write("knowledge_decision", decision)]
        predicates = {(row["owner"], row["kind"], row["item"]["id"]): row.get("expected_version") for row in writes}
        all_checks = unique([*checks, *published["checks"], *fences])
        retained = []
        for check in all_checks:
            key = check["owner"], check["kind"], check["id"]
            if key in predicates:
                if predicates[key] != check["version"]:
                    fail(409, "knowledge-source-changed", "동일한 원본의 다른 버전이 관찰되었습니다.")
            else:
                retained.append(check)
        ctx.storage.put_many(writes, retained, retry_conflicts=False,
            before_attempt=lambda: check_source_deadlines(ctx.storage, checks, ctx.claims, expiry))
    except Conflict:
        fail(409, "knowledge-source-changed", "게시 전에 원본·자산·권한이 변경되었습니다.")
    return {"publication": published["result"], "decision": {key: decision[key]
             for key in ("id", "artifactId", "actor", "resultHash", "at")}, "approvalEffect": False}


def route(host, scope, claims, method, parts, body, query):
    ctx = Service(host, scope, claims)
    ctx.fresh(READ_ROLES)
    if parts == ["guidelines"] and method == "POST":
        from ontology_runtime.execution_entrypoints import knowledge_operation
        return 202, knowledge_operation(ctx, body)
    if not parts and method == "GET":
        page = page_for(ctx, "wb_artifact", query, selection="knowledge")
        items, checks, expiry = [], [], ctx.authorization()["authorizationExpiresAt"]
        for artifact in page["items"]:
            if artifact.get("kind") != KIND:
                continue
            probe = Service(host, scope, claims)
            try:
                value, observed, bound = view(probe, artifact)
            except (AdmissionError, CollaborationError, ValueError):
                continue
            items.append(value)
            checks = unique([*checks, *observed])
            expiry = min(expiry, bound)
        fence(ctx, checks, expiry)
        return 200, {"items": items, "cursor": page.get("cursor")}
    if len(parts) == 1 and method == "GET":
        try:
            value, checks, expiry = view(ctx, ctx.get("wb_artifact", parts[0]))
            fence(ctx, checks, expiry)
            return 200, value
        except (AdmissionError, CollaborationError, ValueError):
            fail(404, "not-found", "지식화 후보를 찾을 수 없습니다.")
    if len(parts) == 2 and parts[1] == "publish" and method == "POST":
        return 201, publish(ctx, parts[0], body)
    fail(404, "not-found", "지식화 경로를 찾을 수 없습니다.")
