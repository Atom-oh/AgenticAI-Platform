"""Immutable discussion on exact design evidence, under its current source authority."""
from __future__ import annotations

import json

from workbench.service import fields, fail, check_source_deadlines
from workspace import ontology_schema as schema
from workspace.design_api import current_inputs, observe, page_for, read_object, variant_authority
from workspace.execution_sources import unique
from workspace.storage import Conflict


def authorize(ctx, design, anchor):
    admitted, checks, _ = current_inputs(ctx, design)
    if not isinstance(anchor, dict):
        fail(422, "design-comment-anchor", "의견을 연결할 설계 근거를 선택하세요.")
    kind = anchor.get("kind")
    allowed = {"kind", "id", "sha256"} if kind != "screen" else {
        "kind", "id", "artifactId", "sourceHash", "bundleHash"}
    fields(anchor, allowed)
    target = schema._identifier(anchor.get("id"))
    if kind == "prd":
        if target != "prd" or not design.get("prd") or anchor.get("sha256") != design["prd"]["sha256"]:
            fail(409, "design-comment-stale", "의견을 남길 PRD 버전을 다시 확인하세요.")
        read_object(ctx, design["prd"])
    elif kind in {"flow-node", "rule"}:
        if not design.get("flow") or anchor.get("sha256") != design["flow"]["sha256"]:
            fail(409, "design-comment-stale", "의견을 남길 흐름과 확인 기준을 다시 확인하세요.")
        prepared = read_object(ctx, design["flow"])
        ids = prepared["flow"]["screens"] if kind == "flow-node" else [
            item["id"] for item in (prepared.get("contract") or {}).get("rules", [])]
        if target not in ids:
            fail(404, "not-found", "설계 근거를 찾을 수 없습니다.")
    elif kind == "screen":
        variant, artifact, more, expiry = variant_authority(ctx, design, anchor.get("artifactId"))
        if any(anchor.get(key) != variant[key] for key in ("sourceHash", "bundleHash")):
            fail(409, "design-comment-stale", "의견을 남길 시안의 정확한 버전을 확인하세요.")
        generated = read_object(ctx, artifact["result"])["generated"]
        if target not in {item["screenId"] for item in generated["screens"]}:
            fail(404, "not-found", "시안의 화면을 찾을 수 없습니다.")
        checks = unique([*checks, *more])
        admitted["expiresAt"] = min(admitted["expiresAt"], expiry)
    else:
        fail(422, "design-comment-anchor", "지원되지 않는 설계 의견 위치입니다.")
    checks = unique([*checks, ctx.check("design", design)])
    observe(ctx, checks, admitted["expiresAt"])
    return dict(anchor), checks, admitted["expiresAt"]


def handle(ctx, design_id, method, body, query):
    ctx.fresh()
    design = ctx.get("design", design_id)
    if method == "GET":
        fields(query, {"anchor", "cursor", "limit"})
        encoded = query.get("anchor")
        if not isinstance(encoded, str) or len(encoded) > 2048:
            fail(422, "design-comment-anchor", "의견을 조회할 설계 근거를 선택하세요.")
        try:
            anchor = json.loads(encoded)
        except ValueError:
            fail(422, "design-comment-anchor", "의견 위치의 형식을 확인하세요.")
        anchor, _, _ = authorize(ctx, design, anchor)
        page = page_for(ctx, "comment", query, selection=[design_id, anchor])
        comments = [item for item in page["items"] if item.get("projectId") == ctx.project_id
                    and item.get("anchor") == {"designId": design_id, **anchor}]
        for item in comments:
            observe(ctx, [ctx.check("comment", item)], ctx._design_expiry)
        return 200, {"comments": [public(item) for item in comments], "cursor": page.get("cursor")}
    fields(body, {"requestId", "text", "anchor"})
    ctx.collaboration.require(ctx.scope, "discuss")
    anchor, checks, expiry = authorize(ctx, design, body.get("anchor"))
    text = schema._text(body.get("text"), 4000)
    request_hash = schema.digest([design_id, anchor, text])
    identifier = "c-design-" + ctx.identity("comment", body.get("requestId")).split("-", 1)[1]
    previous = ctx.storage.get(ctx.owner, "comment", identifier)
    if previous:
        if (previous.get("projectId") != ctx.project_id or previous.get("author") != ctx.actor
                or previous.get("requestHash") != request_hash):
            fail(409, "request-changed", "같은 요청 ID에 다른 의견이 지정되었습니다.")
        observe(ctx, [ctx.check("comment", previous)], expiry)
        return 200, {"comment": public(previous)}
    comment = {"id": identifier, "projectId": ctx.project_id, "author": ctx.actor, "text": text,
               "anchor": {"designId": design_id, **anchor}, "designRevision": design["revision"],
               "requestHash": request_hash, "status": "active"}
    checks = unique([*checks, ctx.check("project", ctx.scope["project"])])
    try:
        saved = ctx.storage.put_many([ctx.write("comment", comment)], checks, retry_conflicts=False,
            before_attempt=lambda: check_source_deadlines(ctx.storage, checks, ctx.claims, expiry))[0]
    except Conflict:
        fail(409, "design-comment-changed", "설계 근거나 의견이 변경되었습니다. 다시 조회하세요.")
    return 201, {"comment": public(saved)}


def public(comment):
    return {key: comment[key] for key in
            ("id", "author", "text", "anchor", "designRevision", "createdAt")}
