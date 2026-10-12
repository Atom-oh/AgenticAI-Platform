"""Project design records and exact human decisions; execution completion belongs to B0."""
from __future__ import annotations

import copy
import hashlib
import json

from intake import admission, derivative, inspect
from workbench.service import Service, fields, fail, check_source_deadlines
from workspace.collaboration import CollaborationError
from workspace import ontology_schema as schema, storage as storage_module
from workspace.execution_source import encode
from workspace.execution_sources import unique
from workspace.ontology_sources import Sources
from workspace.ontology_store import Ontology
from workspace.storage import Conflict

HUMAN = storage_module._human_writer()
ROLES = {"create": {"owner", "planner", "designer"}, "prd": {"owner", "planner"},
         "flow": {"owner", "designer"}, "generate": {"owner", "designer"},
         "approve": {"owner", "designer"}, "accept": {"owner", "developer"}}


def _object(ctx, identifier, name, value):
    raw = encode(value)
    digest = hashlib.sha256(raw).hexdigest()
    key = ctx.storage.key_for(ctx.owner, "design", identifier, name + "/" + digest + ".json")
    ctx.storage.put_blob_once(key, raw, "application/json")
    return {"key": key, "sha256": digest, "size": len(raw)}


def read_object(ctx, ref):
    if not isinstance(ref, dict) or not ctx.storage.owns_key(ctx.owner, ref.get("key")):
        fail(404, "not-found", "설계 자료를 확인할 수 없습니다.")
    raw = ctx.storage.get_blob(ref["key"])
    if hashlib.sha256(raw).hexdigest() != ref.get("sha256") or len(raw) != ref.get("size"):
        fail(409, "design-evidence-changed", "설계 근거 파일이 변경되었습니다.")
    return json.loads(raw)


def _matches(left, right):
    return all(left.get(key) == right.get(key) for key in ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision"))


def inputs(ctx, decision_ids):
    if (not isinstance(decision_ids, list) or not 1 <= len(decision_ids) <= 20
            or len(set(decision_ids)) != len(decision_ids)):
        fail(422, "design-input-limit", "승인된 입력 자료를 1~20개 선택하세요.")
    pages, refs, decisions, checks, bounds = [], [], [], [], []
    for identifier in decision_ids:
        decision, data, authority = admission.authorized(ctx.host, ctx.scope, identifier, claims=ctx.claims)
        if decision["artifact"]["kind"] not in {"document-pages", "diagram-transcription"}:
            fail(422, "design-input-kind", "설계 원본에는 반입 승인된 문서나 도해가 필요합니다.")
        value = json.loads(data)
        if decision["artifact"]["kind"] == "diagram-transcription":
            value = [{"page": 1, "text": value["text"]}]
        if not isinstance(value, list):
            fail(409, "design-input-invalid", "승인된 문서 형식을 확인하지 못했습니다.")
        for page in value:
            pages.append({"admissionId": decision["id"], "derivativeHash": decision["artifact"]["sha256"],
                          "sourceRef": decision["source"], "page": page["page"], "text": page["text"]})
        fences, deadlines = authority.collect_deadlines()
        checks.extend([*authority.checks, *fences]);bounds.extend(expiry for expiry, _ in deadlines)
        refs.append(decision["source"])
        decisions.append({"decisionId": decision["id"], "revision": str(decision["revision"]),
                          "artifactHash": decision["artifact"]["sha256"]})
    if len(encode(pages)) > 2 * 1024 * 1024:
        fail(422, "design-input-limit", "문서 범위를 줄여 새 설계를 시작하세요.")
    expiry = min([ctx.authorization()["authorizationExpiresAt"], *bounds])
    if ctx.storage.clock() >= expiry:
        fail(401, "authorization-expired", "자료 또는 인증의 유효 기간이 만료되었습니다.")
    return {"pages": pages, "sourceRefs": refs, "admissions": decisions,
            "checks": unique(checks), "expiresAt": expiry}


def snapshot(ctx, procedure_id, admitted):
    ontology = Ontology(ctx)
    graph = ontology.procedure_snapshot(schema._identifier(procedure_id))
    from workspace.design_stages import knowledge
    knowledge(graph)
    references = [ref for row in [*graph["nodes"], *graph["edges"]] for ref in row["sourceRefs"]]
    for ref in references:
        if ref["sourceKind"] != "package" and not any(_matches(ref, source) for source in admitted["sourceRefs"]):
            fail(409, "design-source-admission-required", "절차의 모든 원본에 현재 반입 승인이 필요합니다.")
    # The projection can include human-authored metadata beyond the original pages.
    # Independently inspect that exact derived context before it can enter Runtime.
    entries = derivative.canonical_entries(admission._denylist(ctx.host))
    raw = encode(graph).decode()
    from design_loop.references import References
    inspected = References(graph).encode(raw)
    receipt = inspect.inspect([{"page": 1, "text": inspected}], denylist=entries)
    residual = derivative.residual([{"page": 1, "text": inspected}], entries)
    if residual["identifiers"] or residual["pii"]:
        fail(409, "design-context-sensitive", "절차 메타데이터의 비공개 식별자를 먼저 정리하세요.")
    checks = unique([*admitted["checks"], *ontology.sources.recheck()])
    current = ontology.current()
    if current["generation"] != graph["generation"]:
        fail(409, "ontology-changed", "조회 중 절차가 변경되었습니다.")
    checks = unique([*checks, ctx.check("ontology", current)])
    return graph, checks, {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "inspection": schema.digest(receipt)}


def _commit(ctx, value, version, checks, expiry):
    project = ctx.scope["project"]
    checks = unique([*checks, ctx.check("project", project)])
    def guard():
        check_source_deadlines(ctx.storage, checks, ctx.claims, expiry)
    try:
        return ctx.storage.put_many([ctx.write("design", value, version)], checks, retry_conflicts=False,
                                    before_attempt=guard, _writer=HUMAN)[0]
    except Conflict:
        fail(409, "design-changed", "설계 또는 원본이 변경되었습니다. 다시 조회하세요.")


def create(ctx, body):
    fields(body, {"requestId", "title", "procedureId", "decisionIds", "model"})
    ctx.fresh(ROLES["create"])
    from engine.model_catalog import resolve
    model = resolve(body.get("model"))
    identifier = ctx.identity("design", body.get("requestId"))
    title = schema._text(body.get("title"), 160)
    old = ctx.storage.get(ctx.owner, "design", identifier)
    if old:
        current_inputs(ctx, old)
        if old["requestHash"] != schema.digest(body):
            fail(409, "request-changed", "같은 요청 ID의 설계 내용이 다릅니다.")
        return public(old)
    admitted = inputs(ctx, body.get("decisionIds"))
    graph, checks, inspection = snapshot(ctx, body.get("procedureId"), admitted)
    value = {"id": identifier, "projectId": ctx.project_id, "schemaVersion": 1, "executionSchemaVersion": 1,
             "revision": 1, "title": title, "procedureId": body["procedureId"], "model": model,
             "decisionIds": body["decisionIds"], "admissions": admitted["admissions"],
             "sourceRefs": admitted["sourceRefs"], "generation": graph["generation"],
             "input": _object(ctx, identifier, "input", {"snapshot": graph, "pages": admitted["pages"], "inspection": inspection}),
             "status": "draft", "createdBy": ctx.actor, "requestId": body["requestId"],
             "requestHash": schema.digest(body), "prd": None, "flow": None, "approvals": {}, "artifacts": [],
             "activeJobId": None, "activeArtifactId": None, "variants": []}
    return public(_commit(ctx, value, None, checks, admitted["expiresAt"]))


def current_inputs(ctx, design):
    ctx.fresh()
    admitted = inputs(ctx, design["decisionIds"])
    if admitted["admissions"] != design["admissions"]:
        fail(409, "design-source-changed", "반입 승인 자료가 변경되었습니다.")
    graph, checks, inspection = snapshot(ctx, design["procedureId"], admitted)
    stored = read_object(ctx, design["input"])
    if (graph != stored["snapshot"] or graph["generation"] != design["generation"]
            or admitted["pages"] != stored["pages"] or inspection != stored["inspection"]):
        fail(409, "design-source-changed", "절차 또는 문서가 바뀌어 설계 확인이 필요합니다.")
    observe(ctx, checks, admitted["expiresAt"])
    return admitted, checks, stored


def decide(ctx, identifier, action, body):
    fields(body, {"version", "hash", "prd"})
    ctx.fresh(ROLES[action])
    design = ctx.get("design", identifier)
    if design["version"] != body.get("version") or design["activeJobId"]:
        fail(409, "design-changed", "실행이 끝난 현재 설계 버전을 확인하세요.")
    if design["status"] in {"approved", "released"}:
        fail(409, "design-state", "승인된 설계는 새 설계로 수정하세요.")
    admitted, checks, stored = current_inputs(ctx, design)
    from workspace.design_stages import prepare_flow
    from design_loop.prd_extract import validate_prd
    from workspace.component_catalog import read_catalog
    value = copy.deepcopy(design)
    if action == "prd":
        proposed = body.get("prd") if "prd" in body else read_object(ctx, design["prd"])["prd"] if design["prd"] else None
        result = validate_prd(admitted["pages"], proposed)
        if result["issues"] or schema.digest(result["prd"]) != body.get("hash"):
            fail(409, "design-prd-invalid", "원문 인용과 수치가 확인된 PRD만 확정할 수 있습니다.")
        value["prd"] = _object(ctx, identifier, "prd", result)
        prepared = prepare_flow(stored["snapshot"], design["procedureId"], result["prd"], catalog_hash=read_catalog()["hash"], design_id=identifier,
            registry_state=read_object(ctx, design["flow"])["registry"] if design["flow"] else None, document_version=design["revision"] + 1)
        value["flow"] = _object(ctx, identifier, "flow", prepared)
        value.update(status="flow-review", approvals={}, variants=[])
    elif action == "flow":
        if not design["flow"] or not design["approvals"].get("prd"):
            fail(409, "design-prd-required", "먼저 PRD를 확정하세요.")
        prepared = read_object(ctx, design["flow"])
        if prepared["issues"] or design["flow"]["sha256"] != body.get("hash"):
            fail(409, "design-flow-invalid", "누락과 충돌을 해결한 흐름만 승인할 수 있습니다.")
        value.update(status="ready")
    else:
        fail(400, "design-action", "지원되지 않는 설계 결정입니다.")
    value["revision"] += 1
    value["approvals"][action] = {"actor": ctx.actor, "at": ctx.storage.clock(), "hash": body["hash"],
                                    "sourceRevision": design["revision"]}
    return public(_commit(ctx, value, design["version"], checks, admitted["expiresAt"]))


def public(design):
    value = {key: copy.deepcopy(design[key]) for key in ["id", "projectId", "revision", "version", "title", "procedureId",
        "model", "status", "createdBy", "createdAt", "updatedAt", "activeJobId", "activeArtifactId", "approvals", "releases", "frontendAcceptance", "error", "unknownOutcome"] if key in design}
    value["variants"] = [{k: copy.deepcopy(row[k]) for k in ("artifactId", "jobId", "variant", "sourceHash", "bundleHash",
                            "passed", "verification", "designRevision", "runId") if k in row} for row in design["variants"]]
    return value


def _route(ctx, method, parts, body, query):
    host, scope, claims = ctx.host, ctx.scope, ctx.claims
    if parts == ["config"] and method == "GET":
        import os
        from ontology_runtime.execution_entrypoints import configuration
        settings = configuration().get("design", {}) if os.environ.get("SOURCE_EXECUTION_CONFIGURATION") else {}
        return 200, {"enabled": ctx.project_id in settings.get("projects", []), "models": settings.get("models", []),
                     "backend": "agentcore", "gitConfigured": bool(getattr(host, "git_connections", lambda: {})())}
    if parts == ["inputs"] and method == "GET":
        return 200, input_options(ctx, query)
    if parts == ["admissions"] and method == "POST":
        fields(body, {"documentId"})
        from documents.library import Library
        library = Library(host, ctx.scope)
        document = library.document(body.get("documentId"))
        revision = library.revision(document, document.get("approvedRevisionId"), approved=True)
        ref = {"sourceKind": "document-revision", "sourceId": document["id"], "revision": revision["id"],
               "sha256": revision["sha256"], "audienceRevision": str(document["aclVersion"])}
        decision = admission.request(host, ctx.scope, ref, data_class="internal-non-sensitive", claims=claims)
        return 202, {"decision": {k: decision[k] for k in ("id", "revision", "status", "blocking") if k in decision}}
    if parts == ["instructions"] and method == "POST":
        fields(body, {"text"})
        from intake.prompts import admit_prompt_text
        decision = admit_prompt_text(host, ctx.scope, body.get("text"), purpose="edit-instruction", claims=claims)
        return 202, {"decision": {k: decision[k] for k in ("id", "revision", "status", "blocking") if k in decision}}
    if len(parts) == 2 and method == "POST" and parts[1] in {"extract", "generate", "edit", "compose", "release"}:
        from ontology_runtime.execution_entrypoints import dependencies
        from workspace.design_execution import submit
        import os
        from botocore.config import Config
        dep_host, config, ledger, _, _, session, _ = dependencies()
        stage_ctx = Service(dep_host, ctx.scope, claims)
        queue_url = os.environ.get("SOURCE_EXECUTION_QUEUE_URL")
        if not queue_url:
            fail(503, "design-execution-unavailable", "설계 실행 환경이 아직 활성화되지 않았습니다.")
        queue = session.client("sqs", config=Config(retries={"total_max_attempts": 1}))
        def enqueue(owner, job_id):
            queue.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"owner": owner, "jobId": job_id}))
        return 202, submit(stage_ctx, parts[0], parts[1], body, ledger, config, enqueue)
    if len(parts) == 2 and method == "POST" and parts[1] == "approve":
        return 200, {"design": approve_variant(ctx, parts[0], body)}
    if len(parts) == 2 and method == "POST" and parts[1] == "cancel":
        from ontology_runtime.execution_entrypoints import dependencies
        _, _, ledger, _, _, _, _ = dependencies()
        design = ctx.get("design", parts[0])
        if design.get("activeJobId"):
            ledger.api().cancel(ctx.owner, design["activeJobId"], actor=ctx.actor)
        return 200, {"design": public(ctx.get("design", parts[0]))}
    if len(parts) == 3 and parts[1] == "releases" and method == "GET":
        return 200, release_view(ctx, parts[0], parts[2], query)
    if len(parts) == 4 and parts[1] == "releases" and parts[3] == "accept" and method == "POST":
        return 200, {"design": accept_frontend(ctx, parts[0], parts[2], body)}
    if len(parts) == 3 and parts[1] == "artifacts" and method == "GET":
        return 200, artifact_view(ctx, parts[0], parts[2], query)
    if not parts and method == "POST":
        return 201, {"design": create(ctx, body)}
    if not parts and method == "GET":
        page = page_for(ctx, "design", query)
        items = []
        for value in page["items"]:
            try:
                probe = Service(ctx.host, ctx.scope, ctx.claims)
                current_inputs(probe, value)
                items.append(public(value))
            except (admission.AdmissionError, CollaborationError, ValueError):
                continue
        return 200, {"designs": items, "cursor": page.get("cursor")}
    if len(parts) == 1 and method == "GET":
        value = ctx.get("design", parts[0]);current_inputs(ctx, value)
        return 200, {"design": public(value), "prd": read_object(ctx, value["prd"]) if value["prd"] else None,
                     "flow": read_object(ctx, value["flow"]) if value["flow"] else None,
                     "flowHash": value["flow"]["sha256"] if value["flow"] else None,
                     "failure": compile_failure(ctx, value)}
    if len(parts) == 2 and method == "POST" and parts[1] in {"confirm-prd", "approve-flow"}:
        return 200, {"design": decide(ctx, parts[0], "prd" if parts[1] == "confirm-prd" else "flow", body)}
    fail(404, "not-found", "설계 경로를 찾을 수 없습니다.")


def compile_failure(ctx, design):
    """Private diagnostics for a failed attempt, never a bundle or approval receipt."""
    from workspace.execution_sources import ExecutionSources
    if (ctx.scope['role'] not in {'owner', 'designer', 'developer'} or design.get('status') != 'interrupted'
            or not design.get('lastExecutionId')):
        return None
    job = ctx.get('job', design['lastExecutionId'])
    descriptor = job.get('artifact') or {}
    if (job.get('status') != 'failed' or (job.get('error') or {}).get('code') != 'design-compile-failed'
            or descriptor.get('designId') != design['id'] or descriptor.get('designRevision') != design['revision']):
        return None
    checks = [ctx.check('job', job), ctx.check('design', design)]
    expiry = ctx.authorization()['authorizationExpiresAt']
    resolver = ExecutionSources(ctx.host)
    if not job.get('admissions'):
        fail(404, 'not-found', 'Resource not found')
    for binding in job['admissions']:
        resolved = resolver(ctx.owner, binding, job={'actor': ctx.actor, 'projectId': ctx.project_id,
                                                   'authorizationExpiresAt': expiry})
        checks.extend([resolved['check'], *resolved.get('checks', [])])
        expiry = min(expiry, resolved['expiresAt'])
    observe(ctx, checks, expiry)
    attempt = job['attempt']['id']
    def output(stage, name):
        key = ctx.storage.key_for(ctx.owner, 'job', job['id'], f'out/{attempt}/{stage}/{name}')
        rows = [row for row in job['transfers'] if row['attemptId'] == attempt and row['key'] == key and row['stage'] == stage]
        if len(rows) != 1 or rows[0]['size'] > 2 * 1024 * 1024:
            fail(404, 'not-found', 'Resource not found')
        return read_object(ctx, rows[0])
    build = output('compile', 'build.json')['build']
    source_stage = 'context' if job['operation'] == 'design.compose' else 'generate'
    source = output(source_stage, 'sources.json') if job['operation'] != 'design.release' else {'files': {}}
    if build.get('ok') is not False:
        fail(404, 'not-found', 'Resource not found')
    return {'stage': 'compile', 'status': 'failed', 'diagnostics': build.get('diagnostics', []),
            'gates': build.get('gates', {}), 'files': source['files'], 'approved': False}


def variant_authority(ctx, design, artifact_id):
    """Every consumed input remains in the delivery lineage, including later edit instructions."""
    from workspace.execution_sources import ExecutionSources
    variant = next((v for v in design["variants"] if v["artifactId"] == artifact_id), None)
    if not variant:
        fail(404, "not-found", "시안을 확인할 수 없습니다.")
    from workspace.ontology_sources import Sources
    run = ctx.get("run", variant["runId"])
    rows = [row for row in run.get("rounds", []) if row.get("number") == 1]
    if (run.get("designId") != design["id"] or len(rows) != 1
            or rows[0].get("sourceHash") != variant["sourceHash"] or rows[0].get("bundleHash") != variant["bundleHash"]):
        fail(404, "not-found", "Resource not found")
    Sources(ctx)._round_permission(run, rows[0])
    artifact = ctx.get("wb_artifact", artifact_id)
    if artifact.get("designId") != design["id"] or artifact.get("jobId") != variant["jobId"]:
        fail(404, "not-found", "시안을 확인할 수 없습니다.")
    admitted = artifact.get("admissions")
    if not admitted:
        fail(404, "not-found", "시안의 원본 권한을 확인할 수 없습니다.")
    resolver, checks = ExecutionSources(ctx.host), []
    expiry = ctx.authorization()["authorizationExpiresAt"]
    for binding in admitted:
        resolved = resolver(ctx.owner, binding, job={"actor": ctx.actor, "projectId": ctx.project_id,
                                                   "authorizationExpiresAt": expiry})
        checks.extend([resolved["check"], *resolved.get("checks", [])]);expiry = min(expiry, resolved["expiresAt"])
    checks.extend([ctx.check("wb_artifact", artifact), ctx.check("design", design), ctx.check("run", run)])
    check_source_deadlines(ctx.storage, checks, ctx.claims, expiry)
    observe(ctx, checks, expiry)
    return variant, artifact, unique(checks), expiry


def approve_variant(ctx, identifier, body):
    fields(body, {"version", "artifactId", "sourceHash", "bundleHash"})
    ctx.fresh(ROLES["approve"])
    design = ctx.get("design", identifier)
    if design["version"] != body.get("version") or design.get("activeJobId"):
        fail(409, "design-changed", "현재 시안과 실행 상태를 다시 확인하세요.")
    admitted, checks, _ = current_inputs(ctx, design)
    variant, artifact, more, expiry = variant_authority(ctx, design, body.get("artifactId"))
    if (not variant["passed"] or any(variant[key] != body.get(key) for key in ("sourceHash", "bundleHash"))):
        fail(409, "design-approval-required", "검증된 소스와 화면의 정확한 해시를 승인하세요.")
    run = ctx.get("run", variant["runId"])
    approval = {"actor": ctx.actor, "at": ctx.storage.clock(), "round": 1,
        "sourceHash": variant["sourceHash"], "bundleHash": variant["bundleHash"], "catalogHash": run["catalogHash"],
        "contractHash": run["contractHash"], "artifactId": variant["artifactId"]}
    changed = {**design, "status": "approved", "approvals": {**design["approvals"], "ux": approval}}
    predicates = unique([*checks, *more, ctx.check("project", ctx.scope["project"])])
    writes = {("design", design["id"]): design["version"], ("run", run["id"]): run["version"]}
    for check in predicates:
        identity = (check["kind"], check["id"])
        if identity in writes and check["version"] != writes[identity]:
            fail(409, "design-changed", "승인 대상의 버전이 변경되었습니다.")
    # The same condition is enforced by each conditional Put; different versions never merge.
    predicates = [check for check in predicates if (check["kind"], check["id"]) not in writes]
    bound = min(expiry, admitted["expiresAt"])
    ctx.storage.put_many([ctx.write("design", changed, design["version"]),
        ctx.write("run", {**run, "approval": approval}, run["version"])], predicates, retry_conflicts=False,
        before_attempt=lambda: check_source_deadlines(ctx.storage, predicates, ctx.claims, bound), _writer=HUMAN)
    return public(ctx.get("design", identifier))


def input_options(ctx, query):
    from workspace.collaboration import CollaborationError
    options, observed, deadlines = [], [], []
    page = page_for(ctx, "adm_decision", query)
    for row in page["items"]:
        if row.get("status") != "admitted" or row.get("artifact", {}).get("kind") not in {"document-pages", "diagram-transcription", "prompt-text"}:
            continue
        try:
            value, _, authority = admission.authorized(ctx.host, ctx.scope, row["id"], claims=ctx.claims)
            fences, bounds = authority.collect_deadlines()
            observed.extend([*authority.checks, *fences]);deadlines.extend(bound for bound, _ in bounds)
            options.append({"id": row["id"], "kind": value["artifact"]["kind"], "revision": value["revision"],
                            "sourceId": value["source"]["sourceId"], "pageCount": value["artifact"].get("pages", 1)})
        except (admission.AdmissionError, CollaborationError):
            continue
    checks = unique([*observed, ctx.check("project", ctx.scope["project"])])
    expiry = min([ctx.authorization()["authorizationExpiresAt"], *deadlines])
    ctx.storage.put_many([], checks, retry_conflicts=False,
        before_attempt=lambda: check_source_deadlines(ctx.storage, checks, ctx.claims, expiry), _writer=HUMAN)
    return {"inputs": options, "cursor": page.get("cursor")}


def artifact_view(ctx, identifier, artifact_id, query):
    design = ctx.get("design", identifier)
    admitted, original_checks, _ = current_inputs(ctx, design)
    variant, artifact, checks, expiry = variant_authority(ctx, design, artifact_id)
    result = read_object(ctx, artifact["result"])
    kind = query.get("kind", "view")
    if kind == "view":
        build = read_object(ctx, result["build"])["build"]
        from workspace.design_stages import knowledge, unpacked
        from design_loop.convention import Registry
        from design_loop.composition import walk
        snapshot_ = read_object(ctx, design["input"])["snapshot"]
        k = knowledge(snapshot_)
        registry = Registry(k, state=result["generated"]["registry"])
        targets = []
        for (screen, state), composition in unpacked(result["generated"]["screens"]).items():
            ids = registry.test_ids(screen, composition)["byNode"]
            for _, node, _ in walk(composition):
                targets.append({"screenId": screen, "state": state, "targetId": node["id"], "testId": ids[node["id"]],
                                "label": k.screens[screen]["title"] + " · " + k.assets[node["asset"]]["title"]})
        value = {"previewHtml": build["previewHtml"], "verification": result["verification"], "targets": targets,
                 "screens": result["generated"]["screens"], "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"]}
    elif kind in {"source", "bundle", "screenshot"}:
        import base64
        ref = result[kind]
        data = ctx.storage.get_blob(ref["key"])
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            fail(409, "design-evidence-changed", "검증한 파일이 변경되었습니다.")
        try:
            offset = int(query.get("offset", 0))
        except (TypeError, ValueError):
            fail(400, "design-range", "파일 범위를 확인하세요.")
        if not 0 <= offset < len(data):
            fail(400, "design-range", "파일 범위를 확인하세요.")
        chunk = data[offset:offset + 256 * 1024]
        value = {"data": base64.b64encode(chunk).decode(), "offset": offset, "total": len(data), "sha256": ref["sha256"],
                 "chunkHash": hashlib.sha256(chunk).hexdigest(), "complete": offset + len(chunk) == len(data)}
    else:
        fail(400, "design-artifact-kind", "지원하지 않는 결과 파일입니다.")
    all_checks = unique([*checks, *original_checks, ctx.check("project", ctx.scope["project"])])
    bound = min(expiry, admitted["expiresAt"])
    ctx.storage.put_many([], all_checks, retry_conflicts=False,
        before_attempt=lambda: check_source_deadlines(ctx.storage, all_checks, ctx.claims, bound), _writer=HUMAN)
    return value


def observe(ctx, checks, expiry):
    ctx._design_checks = unique([*getattr(ctx, "_design_checks", []), *checks])
    ctx._design_expiry = min(getattr(ctx, "_design_expiry", expiry), expiry)


def page_for(ctx, kind, query):
    import secrets
    try:
        limit = int(query.get("limit", 5))
    except (TypeError, ValueError, OverflowError):
        fail(400, "design-page-limit", "한 번에 1~10개 항목을 조회하세요.")
    if not 1 <= limit <= 10:
        fail(400, "design-page-limit", "한 번에 1~10개 항목을 조회하세요.")
    fingerprint = schema.digest([ctx.actor, ctx.scope["role"], ctx.scope["project"]["authorityRevision"], kind, limit])
    token = query.get("cursor")
    cursor = None
    if token:
        row = ctx.storage.get(ctx.owner, "ontology_cursor", token)
        if not row or row.get("fingerprint") != fingerprint or row.get("expiresAt", 0) <= ctx.storage.clock():
            fail(409, "design-cursor-stale", "목록 범위 또는 권한이 변경되었습니다.")
        cursor = row["cursor"]
        observe(ctx, [ctx.check("ontology_cursor", row)], row["expiresAt"])
    page = ctx.storage.list_page(ctx.owner, kind, limit=limit, cursor=cursor)
    if page.get("cursor"):
        identifier = "design-page-" + secrets.token_hex(20)
        ctx.storage.put(ctx.owner, "ontology_cursor", {"id": identifier, "projectId": ctx.project_id,
            "fingerprint": fingerprint, "cursor": page["cursor"], "expiresAt": ctx.storage.clock() + 300000})
        page["cursor"] = identifier
    return page


def safe_summary(ctx, record):
    from workspace.collaboration import CollaborationError
    admitted, checks, _ = current_inputs(ctx, record)
    value = copy.deepcopy(record);value["variants"] = []
    for variant in record["variants"]:
        try:
            variant_authority(ctx, record, variant["artifactId"])
            value["variants"].append(variant)
        except (admission.AdmissionError, CollaborationError):
            continue
    visible = {v["artifactId"] for v in value["variants"]}
    if value.get("approvals", {}).get("ux", {}).get("artifactId") not in visible:
        value["approvals"].pop("ux", None)
    observe(ctx, [*checks, ctx.check("design", record)], admitted["expiresAt"])
    return public(value)


def _response(host, scope, claims, method, parts, body, query):
    ctx = Service(host, scope, claims)
    status, result = _route(ctx, method, parts, body, query)
    # Re-collect after writes; the final response must use the saved record, not the pre-write version.
    if method != "GET":
        ctx._design_checks = []
        ctx._design_expiry = ctx.authorization()["authorizationExpiresAt"]
    elif not hasattr(ctx, "_design_checks"):
        ctx._design_checks = []
        ctx._design_expiry = ctx.authorization()["authorizationExpiresAt"]
    if isinstance(result.get("design"), dict):
        record = ctx.get("design", result["design"]["id"])
        if record["version"] != result["design"]["version"]:
            fail(409, "design-changed", "응답을 준비하는 중 설계가 변경되었습니다.")
        result["design"] = safe_summary(ctx, record)
    if isinstance(result.get("designs"), list):
        visible = []
        for value in result["designs"]:
            # Failed rows must not poison the retained response observations.
            probe = Service(host, scope, claims)
            try:
                summary = safe_summary(probe, probe.get("design", value["id"]))
            except (admission.AdmissionError, CollaborationError, ValueError):
                continue
            observe(ctx, probe._design_checks, probe._design_expiry)
            visible.append(summary)
        result["designs"] = visible
    if len(parts) == 3 and parts[1] == "artifacts":
        record = ctx.get("design", parts[0]);current_inputs(ctx, record);variant_authority(ctx, record, parts[2])
    if len(parts) >= 3 and parts[1] == "releases":
        release_authority(ctx, parts[0], parts[2])
    ctx.fresh()
    checks = unique([*ctx._design_checks, ctx.check("project", ctx.scope["project"])])
    if len(checks) > 100:
        fail(422, "design-response-scope", "조회 범위를 줄여 다시 요청하세요.")
    ctx.storage.put_many([], checks, retry_conflicts=False, _writer=HUMAN,
        before_attempt=lambda: check_source_deadlines(ctx.storage, checks, ctx.claims, ctx._design_expiry))
    return status, result


def release_authority(ctx, identifier, release_id):
    from workspace.releases import approved_artifacts, approval_hash
    design = ctx.get("design", identifier)
    admitted, checks, _ = current_inputs(ctx, design)
    record = next((item for item in design.get("releases", []) if item["id"] == release_id), None)
    if not record:
        fail(404, "not-found", "전달 자료를 확인할 수 없습니다.")
    _, _, more, expiry = variant_authority(ctx, design, record["artifactId"])
    release = ctx.get("release", release_id)
    run = ctx.get("run", release["runId"])
    if release["status"] != "ready" or release.get("approvalHash") != approval_hash(run.get("approval")):
        fail(409, "design-release-stale", "전달 자료의 승인 상태가 변경되었습니다.")
    approved_artifacts(ctx.storage, ctx.owner, run, release["round"], release["approvalHash"])
    checks = unique([*checks, *more, ctx.check("release", release), ctx.check("run", run), ctx.check("design", design)])
    observe(ctx, checks, min(expiry, admitted["expiresAt"]))
    return design, release, checks, min(expiry, admitted["expiresAt"])


def release_view(ctx, identifier, release_id, query):
    import base64
    _, release, _, _ = release_authority(ctx, identifier, release_id)
    kind = query.get("kind", "view")
    if kind == "view":
        value = {k: release[k] for k in ("id", "status", "sourceHash", "bundleHash", "approvalHash", "version",
                 "rebuiltAt", "verification", "frontendAcceptance") if k in release}
        export_id = (release.get("git") or {}).get("exportId")
        if export_id:
            exported = ctx.get("gitexport", export_id)
            if exported.get("releaseId") != release_id or exported.get("sourceHash") != release["sourceHash"]:
                fail(404, "not-found", "Resource not found")
            value["git"] = {key: exported[key] for key in ("status", "branch", "baseSha", "commitSha", "commitUrl", "filesUrl",
                "sourceHash", "connectionId", "jobId", "criteriaCurrent", "criteriaStatus", "error") if key in exported}
            observe(ctx, [ctx.check("gitexport", exported)], ctx.authorization()["authorizationExpiresAt"])
        return {"release": value}
    names = {"handoff": ("handoffKey", "handoffHash"), "source": ("sourceKey", "sourceArchiveSha256"),
             "bundle": ("distKey", "distArchiveSha256")}
    if kind not in names:
        fail(400, "design-artifact-kind", "지원하지 않는 전달 파일입니다.")
    key, digest = names[kind]
    raw = ctx.storage.get_blob(release[key])
    if hashlib.sha256(raw).hexdigest() != release[digest]:
        fail(409, "design-evidence-changed", "검증된 전달 파일이 변경되었습니다.")
    try:
        offset = int(query.get("offset", 0))
    except (TypeError, ValueError):
        fail(400, "design-range", "파일 범위를 확인하세요.")
    if not 0 <= offset < len(raw):
        fail(400, "design-range", "파일 범위를 확인하세요.")
    data = raw[offset:offset + 256 * 1024]
    return {"data": base64.b64encode(data).decode(), "offset": offset, "total": len(raw),
            "sha256": release[digest], "chunkHash": hashlib.sha256(data).hexdigest(), "complete": offset + len(data) == len(raw)}


def accept_frontend(ctx, identifier, release_id, body):
    fields(body, {"version", "sourceHash", "bundleHash", "note"})
    ctx.fresh(ROLES["accept"])
    design, release, checks, expiry = release_authority(ctx, identifier, release_id)
    if release["version"] != body.get("version") or any(release[k] != body.get(k) for k in ("sourceHash", "bundleHash")):
        fail(409, "design-release-stale", "검토한 전달 자료의 버전과 해시를 확인하세요.")
    acceptance = {"actor": ctx.actor, "at": ctx.storage.clock(), "sourceHash": release["sourceHash"],
        "bundleHash": release["bundleHash"], "approvalHash": release["approvalHash"],
        "note": schema._text(body.get("note", "플랫폼 패키지 검토 완료"), 1000), "customerIntegration": "not-verified"}
    predicates = [check for check in checks if (check["kind"], check["id"]) not in {("design", design["id"]), ("release", release["id"])}]
    predicates = unique([*predicates, ctx.check("project", ctx.scope["project"])])
    ctx.storage.put_many([ctx.write("release", {**release, "frontendAcceptance": acceptance}, release["version"]),
        ctx.write("design", {**design, "frontendAcceptance": {"releaseId": release_id, **acceptance}}, design["version"])],
        predicates, retry_conflicts=False, before_attempt=lambda: check_source_deadlines(ctx.storage, predicates, ctx.claims, expiry), _writer=HUMAN)
    return public(ctx.get("design", identifier))


def criteria_authority(ctx, value):
    """Resolve a design contract/run against its current canonical and human authority."""
    from workspace.rules import contract_hash
    design = ctx.get("design", value["designId"])
    admitted, checks, _ = current_inputs(ctx, design)
    if not design.get("flow") or not design.get("approvals", {}).get("flow"):
        fail(404, "not-found", "Resource not found")
    prepared = read_object(ctx, design["flow"])
    expected = prepared["contractHash"]
    contract = value.get("contract", value)
    if (value.get("projectId") != ctx.project_id or contract_hash(contract) != expected
            or value.get("contractHash", expected) != expected
            or design["approvals"]["flow"]["hash"] != design["flow"]["sha256"]):
        fail(404, "not-found", "Resource not found")
    from workspace.component_catalog import read_catalog
    if prepared["contract"]["catalogHash"] != read_catalog()["hash"]:
        fail(404, "not-found", "Resource not found")
    checks = unique([*checks, ctx.check("design", design)])
    observe(ctx, checks, admitted["expiresAt"])
    return checks, admitted["expiresAt"]


def route(host, scope, claims, method, parts, body, query):
    try:
        return _response(host, scope, claims, method, parts, body, query)
    except (admission.AdmissionError, CollaborationError) as error:
        if method == "GET" and parts and parts[0] not in {"config", "inputs"} and error.status in {404, 409}:
            fail(404, "not-found", "Resource not found")
        raise
    except ValueError:
        if method == "GET" and parts and parts[0] not in {"config", "inputs"}:
            fail(404, "not-found", "Resource not found")
        raise
