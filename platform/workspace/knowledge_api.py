"""Exact-source human review of guideline candidates; never implicit approval."""
from __future__ import annotations

import copy
from intake.admission import AdmissionError

from workbench.service import Service, fields, fail, check_source_deadlines
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.execution_ledger import LedgerError
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


def predicates(checks):
    try:
        return unique(checks)
    except ValueError:
        fail(409, "knowledge-source-changed", "동일한 원본의 다른 버전이 관찰되었습니다.")


def fence(ctx, checks, expiry):
    ctx.fresh()
    checks = predicates([*checks, ctx.check("project", ctx.scope["project"])])
    if len(checks) > 100:
        fail(422, "atomic-scope-limit", "자료 범위를 줄여 다시 조회하세요.")
    try:
        ctx.storage.put_many([], checks, retry_conflicts=False,
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
    checks = predicates([*checks, *current_checks])
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
        write_versions = {(row["owner"], row["kind"], row["item"]["id"]): row.get("expected_version") for row in writes}
        # _stage bypasses Service.commit; retain its current project fence here.
        all_checks = predicates([*checks, *published["checks"], *fences,
                                 ctx.check("project", ctx.scope["project"])])
        retained = []
        for check in all_checks:
            key = check["owner"], check["kind"], check["id"]
            if key in write_versions:
                if write_versions[key] != check["version"]:
                    fail(409, "knowledge-source-changed", "동일한 원본의 다른 버전이 관찰되었습니다.")
            else:
                retained.append(check)
        if len(writes) + len(retained) > 100:
            fail(422, "atomic-scope-limit", "원자적 저장 한도를 초과했습니다. 후보와 근거 범위를 나누세요.")
        ctx.storage.put_many(writes, retained, retry_conflicts=False,
            before_attempt=lambda: check_source_deadlines(ctx.storage, all_checks, ctx.claims, expiry))
    except Conflict:
        fail(409, "knowledge-source-changed", "게시 전에 원본·자산·권한이 변경되었습니다.")
    return {"publication": published["result"], "decision": {key: decision[key]
             for key in ("id", "artifactId", "actor", "resultHash", "at")}, "approvalEffect": False}


def route(host, scope, claims, method, parts, body, query):
    ctx = Service(host, scope, claims)
    ctx.fresh(READ_ROLES)
    if parts == ["guidelines"] and method == "POST":
        from ontology_runtime.execution_entrypoints import knowledge_operation
        try:
            return 202, knowledge_operation(ctx, body)
        except LedgerError as error:
            fail(403 if error.code == "forbidden" else
                 503 if error.code == "knowledge-execution-unavailable" else 409,
                 error.code, "지식화의 현재 승인 조건과 실행 상태를 확인하세요.")
        except AdmissionError as error:
            fail(error.status, error.code, "자료의 현재 반입 승인이 필요합니다.")
    if not parts and method == "GET":
        page = page_for(ctx, "wb_artifact", query, selection="knowledge")
        items, checks = [], list(getattr(ctx, "_design_checks", []))
        expiry = min(ctx.authorization()["authorizationExpiresAt"],
                     getattr(ctx, "_design_expiry", ctx.scope["authorizationExpiresAt"]))
        for artifact in page["items"]:
            if artifact.get("kind") != KIND:
                continue
            probe = Service(host, scope, claims)
            try:
                value, observed, bound = view(probe, artifact)
            except (AdmissionError, CollaborationError, ValueError):
                continue
            items.append(value)
            checks = predicates([*checks, *observed])
            expiry = min(expiry, bound)
        fence(ctx, checks, expiry)
        return 200, {"items": items, "cursor": page.get("cursor")}
    if len(parts) == 1 and method == "GET":
        try:
            value, checks, expiry = view(ctx, ctx.get("wb_artifact", parts[0]))
            fence(ctx, checks, expiry)
            return 200, value
        except CollaborationError as error:
            if error.status not in {403, 404, 409}:
                raise
            # Source visibility stays opaque; current actor denial stays explicit.
            ctx.fresh()
            fail(404, "not-found", "지식화 후보를 찾을 수 없습니다.")
        except AdmissionError as error:
            if error.status == 401:
                fail(error.status, error.code, "인증이 만료되었습니다.")
            fail(404, "not-found", "지식화 후보를 찾을 수 없습니다.")
        except ValueError:
            fail(404, "not-found", "지식화 후보를 찾을 수 없습니다.")
    if len(parts) == 2 and parts[1] == "publish" and method == "POST":
        return 201, publish(ctx, parts[0], body)
    fail(404, "not-found", "지식화 경로를 찾을 수 없습니다.")
