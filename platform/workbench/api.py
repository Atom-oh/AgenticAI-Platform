"""Authenticated workbench HTTP and bounded, stateless MCP JSON-RPC serving."""
from __future__ import annotations

import json

from workspace.collaboration import CollaborationError
from workbench import impact, knowledge, skills
from workbench.service import Service, TOOL_NAMES, _id, _version, fail, fields, public, text, tools


def _readable(ctx, record):
    try:
        authority = record.get("graphAuthority", "legacy")
        validate = knowledge.authorize_refs if authority == "canonical" else knowledge.verify_refs
        validate(ctx, record.get("sourceRefs", []), authority=authority)
        return True
    except CollaborationError as error:
        if error.status in (400, 403, 404, 409):
            return False
        raise


def _skill_metadata(ctx, record):
    knowledge.authorize_refs(ctx, record.get("sourceRefs", []))
    result = dict(record)
    try:
        knowledge.verify_refs(ctx, record.get("sourceRefs", []))
        result.update(sourceStatus="current", consumable=record["status"] == "APPROVED")
    except CollaborationError as error:
        if error.status != 409:
            raise
        result.update(sourceStatus="stale", consumable=False)
    return result


def _list(ctx, kind, query):
    page = ctx.page(kind, query)
    if kind == "wb_skill":
        readable = []
        for row in page["items"]:
            try:
                readable.append(_skill_metadata(ctx, row))
            except CollaborationError as error:
                if error.status not in {403, 404, 409}:
                    raise
        page["items"] = readable
    elif kind in {"wb_task", "wb_change"}:
        page["items"] = [row for row in page["items"] if _readable(ctx, row)]
    if kind == "wb_task":
        for key in ("role", "status"):
            if query.get(key):
                page["items"] = [row for row in page["items"] if row.get(key) == query[key]]
    if kind == "wb_batch":
        for batch in page["items"]:
            job = ctx.storage.get(ctx.owner, "job", batch.get("jobId", "missing"))
            if job and job["status"] == "failed" and batch["status"] != "completed":
                batch.update(status="failed", error="수집 작업이 실패했습니다. 작업 기록을 확인하세요.")
    return page


def _overview(ctx):
    stats, incomplete = {}, []
    for label, kind in (("sources", "wb_source"), ("batches", "wb_batch"), ("changes", "wb_change"),
                        ("tasks", "wb_task"), ("skills", "wb_skill"), ("reports", "wb_report")):
        rows, more = ctx.bounded_list(kind, 1000)
        if kind in {"wb_skill", "wb_task", "wb_change"}:
            rows = [row for row in rows if _readable(ctx, row)]
        if kind == "wb_report":
            from workbench.business import can_read_report
            rows = [row for row in rows if can_read_report(ctx.host, ctx.scope, ctx.claims, row)]
        stats[label] = len(rows)
        if more:
            incomplete.append(label)
    tasks = _list(ctx, "wb_task", {"limit": 10, "status": "open"})["items"]
    artifacts = ctx.page("wb_artifact", {"limit": 10})["items"]
    return {"actorId": ctx.actor, "role": ctx.scope["role"], "operator": ctx.operator,
            "capabilities": {"read": True, "author": True, "examples": ctx.scope["role"] in {"owner", "planner"},
                             "analyze": ctx.scope["role"] in {"owner", "planner"},
                             "approveSkill": ctx.scope["role"] in {"owner", "planner"},
                             "registerExternalSource": ctx.operator, "registerTools": ctx.operator},
            "stats": stats, "statsIncomplete": incomplete, "tasks": tasks, "recentArtifacts": artifacts,
            "readiness": {
                "knowledge": {"status": "available", "backend": knowledge.BACKEND},
                "model": {"status": "configured" if callable(getattr(ctx.host, "workbench_model", None))
                          and callable(getattr(ctx.host, "workbench_gate", None)) else "not-configured"},
                "behaviorEvaluation": {"status": "configured" if callable(getattr(ctx.host, "workbench_evaluator", None))
                                       and callable(getattr(ctx.host, "workbench_gate", None)) else "not-configured"},
                "externalSources": {"status": "configured-only", "liveConnectivityVerified": False},
                "mcp": {"status": "available", "transport": "authenticated-stateless-json-rpc"},
            }}


SCHEMAS = {
    "knowledge.search": ({"q": {"type": "string", "maxLength": 2000}, "kind": {"type": "string"},
                          "cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, []),
    "knowledge.read": ({"id": {"type": "string"}}, ["id"]),
    "dependencies.read": ({"targetId": {"type": "string"}}, []),
    "impact.read": ({"changeId": {"type": "string"}}, ["changeId"]),
    "skills.list": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, []),
    "skills.read": ({"id": {"type": "string"}, "version": {"type": "integer", "minimum": 1},
                     "contentHash": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}, ["id", "version", "contentHash"]),
}
DESCRIPTIONS = {"knowledge.search": "현재 권한과 소스 버전을 확인하여 사설 벡터 인덱스를 검색합니다.",
                "knowledge.read": "현재 읽을 수 있는 원문과 정확한 소스 근거를 읽습니다.",
                "dependencies.read": "근거가 있는 타입별 의존 관계를 읽습니다.",
                "impact.read": "현재 소스 버전에 해당하는 저장된 영향 분석을 읽습니다.",
                "skills.list": "현재 읽을 수 있는 승인된 Skill을 나열합니다.",
                "skills.read": "정확한 승인 버전·해시의 Skill 패키지를 읽습니다."}


def _rpc(ctx, body):
    identifier = body.get("id") if isinstance(body, dict) else None

    def error(code, message):
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}

    if (not isinstance(body, dict) or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("method"), str) or set(body) - {"jsonrpc", "id", "method", "params"}
            or identifier is not None and type(identifier) not in (int, str)):
        return 200, error(-32600, "Invalid Request")
    method, params = body["method"], body.get("params", {})
    if not isinstance(params, dict):
        return 200, error(-32602, "Invalid params")
    if method.startswith("notifications/") and "id" not in body:
        return 202, {}
    if "id" not in body:
        return 202, {}
    if method == "initialize":
        supported = {"2025-11-25", "2025-06-18", "2024-11-05"}
        protocol = params.get("protocolVersion")
        result = {"protocolVersion": protocol if isinstance(protocol, str) and protocol in supported else "2025-11-25",
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "private-project-workbench", "version": "1.0.0"},
                  "instructions": "Project-authorized, read-only tools. Local hashing/artifact indexes; no implied external service."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        if params:
            return 200, error(-32602, "Invalid params")
        result = {"tools": [{"name": name, "description": DESCRIPTIONS[name],
                            "inputSchema": {"type": "object", "properties": SCHEMAS[name][0],
                                            "required": SCHEMAS[name][1], "additionalProperties": False},
                            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                            "idempotentHint": True, "openWorldHint": False}}
                           for name in TOOL_NAMES]}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or name not in SCHEMAS or not isinstance(args, dict) or set(params) - {"name", "arguments"}:
            return 200, error(-32602, "Unsupported tool or arguments")
        properties, required = SCHEMAS[name]
        if set(args) - set(properties) or set(required) - set(args):
            return 200, error(-32602, "Unsupported tool arguments")
        for key, value in args.items():
            if properties[key]["type"] == "string" and not isinstance(value, str) or (
                    properties[key]["type"] == "integer" and type(value) is not int):
                return 200, error(-32602, "Invalid argument type")
        try:
            if name == "knowledge.search":
                payload = knowledge.search(ctx, args)
            elif name == "knowledge.read":
                payload = knowledge.read_document(ctx, args["id"])
            elif name == "dependencies.read":
                payload = knowledge.graph(ctx, args.get("targetId"))
            elif name == "impact.read":
                payload = impact.read_impact(ctx, args["changeId"])
            elif name == "skills.list":
                payload = _list(ctx, "wb_skill", args)
                payload["items"] = [x for x in payload["items"] if x.get("consumable")]
            else:
                payload = skills.resolve_approved(ctx, args["id"], args["version"], args["contentHash"])
            payload = public(payload)
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                      "structuredContent": payload, "isError": False}
        except CollaborationError as exc:
            if exc.status == 400:
                return 200, error(-32602, exc.message)
            result = {"content": [{"type": "text", "text": exc.message}], "isError": True}
    else:
        return 200, error(-32601, "Method not found")
    return 200, {"jsonrpc": "2.0", "id": identifier, "result": result}


def route(api, scope, claims, method, parts, body, query):
    if parts and parts[0] in {"pension", "reports"}:
        from workbench.business import route as business_route
        result = business_route(api, scope, claims, method, parts, body, query)
        if result is not None:
            return result
        fail(404, "not-found", "요청한 경로가 없습니다.")
    ctx = Service(api, scope, claims)
    if not isinstance(body, dict) or not isinstance(query, dict):
        fail(400, "invalid-input", "입력 형식이 올바르지 않습니다.")
    result = _route(ctx, method, parts, body, query)
    return result[0], public(result[1])


def _route(ctx, method, parts, body, query):
    if parts == ["overview"] and method == "GET":
        return 200, _overview(ctx)
    if parts == ["examples"] and method == "POST":
        from workbench.fixtures import create_example
        return 201, create_example(ctx, body)
    kinds = {"sources": "wb_source", "batches": "wb_batch", "changes": "wb_change",
             "tasks": "wb_task", "skills": "wb_skill", "tools": "wb_tool"}
    if len(parts) == 1 and method == "GET" and parts[0] in kinds:
        page = _list(ctx, kinds[parts[0]], query)
        if parts[0] == "tools":
            page.update(endpoint="/studio-api/workbench/mcp", operator=ctx.operator)
        return 200, page
    if parts == ["sources"] and method == "POST":
        return 201, {"source": knowledge.register_source(ctx, body)}
    if len(parts) == 2 and parts[0] == "sources" and method == "GET":
        return 200, {"source": ctx.get("wb_source", parts[1])}
    if len(parts) == 3 and parts[0] == "sources" and parts[2] == "batches" and method == "POST":
        return 202, knowledge.start_batch(ctx, parts[1], body)
    if len(parts) == 2 and parts[0] == "batches" and method == "GET":
        batch = ctx.get("wb_batch", parts[1])
        job = ctx.storage.get(ctx.owner, "job", batch["jobId"])
        if job and job["status"] == "failed" and batch["status"] != "completed":
            batch.update(status="failed", error="수집 작업이 실패했습니다.")
        return 200, {"batch": batch}
    if parts == ["knowledge"] and method == "GET":
        return 200, knowledge.search(ctx, query)
    if len(parts) == 2 and parts[0] == "knowledge" and method == "GET":
        return 200, knowledge.read_document(ctx, parts[1])
    if parts == ["dependencies"]:
        if method == "GET":
            return 200, knowledge.graph(ctx, query.get("targetId"))
        if method == "POST":
            return 201, knowledge.declare_graph(ctx, body)
    if parts == ["changes"] and method == "POST":
        return 201, {"change": impact.create_change(ctx, body)}
    if len(parts) == 2 and parts[0] == "changes" and method == "GET":
        change = ctx.get("wb_change", parts[1])
        authority = change.get("graphAuthority", "legacy")
        validate = knowledge.authorize_refs if authority == "canonical" else knowledge.verify_refs
        validate(ctx, change.get("sourceRefs", []), authority=authority)
        return 200, {"change": change}
    if len(parts) == 3 and parts[0] == "changes":
        if parts[2] == "analyze" and method == "POST":
            return 200, impact.analyze(ctx, parts[1], body)
        if parts[2] == "impact" and method == "GET":
            return 200, {"impact": impact.read_impact(ctx, parts[1])}
    if len(parts) == 2 and parts[0] == "tasks" and method == "PUT":
        return 200, {"task": impact.update_task(ctx, parts[1], body)}
    if parts == ["skills"] and method == "POST":
        return 201, {"skill": skills.create(ctx, body)}
    if parts == ["skills", "propose"] and method == "POST":
        return 202, skills.propose(ctx, body)
    if len(parts) == 2 and parts[0] == "skills":
        if method == "PUT":
            return 200, {"skill": skills.update(ctx, parts[1], body)}
        if method == "GET":
            skill = ctx.get("wb_skill", parts[1])
            return 200, {"skill": _skill_metadata(ctx, skill)}
    if len(parts) == 3 and parts[0] == "skills":
        identifier, operation = parts[1:]
        if operation == "package" and method == "GET":
            return 200, skills.package(ctx, identifier)
        if operation == "evaluation" and method == "GET":
            return 200, skills.evaluation(ctx, identifier)
        if method == "POST":
            if operation == "validate":
                result = skills.validate(ctx, identifier, body)
                return 202 if "job" in result else 200, result
            if operation == "approve":
                return 200, {"skill": skills.approve(ctx, identifier, body)}
            if operation == "deprecate":
                return 200, {"skill": skills.deprecate(ctx, identifier, body)}
            if operation == "execute":
                return 202, skills.execute(ctx, identifier, body)
    if len(parts) == 4 and parts[0] == "skills" and parts[2] == "executions" and method == "GET":
        return 200, skills.read_execution(ctx, parts[1], parts[3])
    if parts == ["tools"] and method == "POST":
        fields(body, {"requestId", "name", "description", "toolNames"})
        ctx.fresh(operator=True)
        return 201, {"tool": ctx.create("wb_tool", body, {
            "name": text(body.get("name"), "tool name", 120),
            "description": text(body.get("description"), "description", 2000),
            "toolNames": tools(body.get("toolNames")), "status": "registered",
            "execution": "supported-internal-read-only", "registeredBy": ctx.actor})}
    if parts == ["mcp"] and method == "POST":
        return _rpc(ctx, body)
    fail(404, "not-found", "요청한 경로가 없습니다.")
