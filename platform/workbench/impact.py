"""Deterministic reverse dependency traversal and source-bound role worklists."""
from __future__ import annotations

from collections import deque

from workbench import knowledge
from workbench.service import ROLES, _hash, _id, _version, fail, fields, text

ROLE_FOR_LABEL = {"Product": "planner", "Flow": "planner", "Guideline": "planner", "Rule": "planner",
                  "Component": "designer", "Icon": "designer", "Screen": "designer",
                  "API": "developer", "Test": "developer", "Skill": "developer", "Document": "planner",
                  "Foundation": "designer", "Pattern": "designer", "Asset": "designer",
                  "CodeFile": "developer", "CodeSymbol": "developer"}
MAX_IMPACT = 50


def create_change(ctx, body):
    fields(body, {"requestId", "title", "targetId", "changeType", "before", "after", "reason", "productId"})
    ctx.fresh({"owner", "planner"})
    if body.get("changeType") not in {"update", "add", "remove", "deprecate", "policy",
                                     "condition", "flow", "guide", "component", "icon", "token", "skill"}:
        fail(400, "invalid-change", "변경 종류가 올바르지 않습니다.")
    product_id = body.get("productId")
    if product_id:
        ctx.get("product", product_id)
    values = {"title": text(body.get("title"), "change title", 300),
              "targetId": _id(body.get("targetId")), "changeType": body["changeType"],
              "before": text(body.get("before", ""), "before", 8000, True),
              "after": text(body.get("after", ""), "after", 8000, True),
              "reason": text(body.get("reason"), "reason", 2000), "productId": product_id, "status": "draft"}
    return ctx.create("wb_change", body, values)


def traversal(graph, target_id):
    nodes = {node["id"]: node for node in graph["nodes"]}
    reverse = {}
    for edge in graph["edges"]:
        from workspace.ontology_schema import DEPENDENCIES
        dependency = (edge["canonicalRelation"] in DEPENDENCIES if "canonicalRelation" in edge
                      else edge["rel"] in knowledge.DEPENDENCIES)
        if dependency and not edge.get("tombstone") and edge.get("reviewState") not in {"rejected", "deprecated"}:
            reverse.setdefault(edge["dst"], []).append(edge)
    pending = deque([(target_id, [target_id], [])])
    visited, items, references = set(), [], {}
    while pending and len(items) < MAX_IMPACT:
        identifier, path, witness_edges = pending.popleft()
        if identifier in visited:
            continue
        visited.add(identifier)
        node = nodes.get(identifier)
        if node is not None and node["label"] == "Role":
            continue
        refs = [ref for entry in ([node] if node else []) + witness_edges
                for ref in (entry.get("sourceRefs") or [entry["sourceRef"]])]
        for ref in refs:
            references[_hash(ref)] = ref
        confidence = "unknown" if not node else "confirmed" if all(
            entry.get("provenance") == "connector-extracted" for entry in [node, *witness_edges]) else "candidate"
        role = node.get("role") or ROLE_FOR_LABEL.get(node["label"], "planner") if node else "planner"
        items.append({"targetId": identifier, "title": node["title"] if node else "매핑되지 않은 대상",
                      "role": role, "reason": "변경 대상" if len(path) == 1 else "의존 관계를 통해 영향 가능",
                      "witnessPath": path, "witnessEdges": witness_edges, "sourceRefs": refs,
                      "sourceRevision": node["sourceRef"]["revision"] if node else None,
                      "confidence": confidence})
        parallel = {}
        for edge in sorted(reverse.get(identifier, []), key=lambda e: (e["src"], e["rel"])):
            parallel.setdefault(edge["src"], []).append(edge)
        for source, supporting in parallel.items():
            if source not in visited:
                pending.append((source, path + [source], witness_edges + supporting))
    return {"items": items, "generation": graph["generation"], "sourceRefs": list(references.values()),
            "coverage": {**graph["coverage"], "complete": False, "truncated": bool(pending) or graph["coverage"].get("truncated", False),
                         "unknown": sorted(set(graph["coverage"].get("unknown", []) + ["unmapped-dependencies"]))}}


def analyze(ctx, identifier, body):
    fields(body, {"version"})
    ctx.fresh({"owner", "planner"})
    change = ctx.get("wb_change", identifier)
    if _version(body.get("version")) != change["version"]:
        fail(409, "conflict", "변경 요청 버전이 바뀌었습니다.")
    graph = knowledge.graph(ctx, change["targetId"]) if getattr(ctx.host, "ontology_mode", "legacy") == "canonical" else knowledge.graph(ctx)
    impact = traversal(graph, change["targetId"])
    canonical = getattr(ctx.host, "ontology_mode", "legacy") == "canonical"
    authority = "canonical" if canonical else "legacy"
    checks = knowledge.verify_refs(ctx, impact["sourceRefs"], authority=authority)
    # Pin the same manifest even when the graph has no source evidence.
    manifest_kind, manifest_id = ("ontology", "project-current") if canonical else ("wb_index", "current")
    manifest = ctx.storage.get(ctx.owner, manifest_kind, manifest_id)
    if (manifest or {}).get("generation") != impact["generation"]:
        fail(409, "stale-impact", "분석 중 그래프가 변경되었습니다.")
    if manifest:
        checks.append(ctx.check(manifest_kind, manifest))
    # Reserve one change write and the project fence. Keep every inspected
    # authority check; disclose reduced task coverage instead of exceeding the
    # atomic transaction or dropping evidence to make it fit.
    unique_checks = {(check["owner"], check["kind"], check["id"]) for check in checks}
    capacity = 98 - len(unique_checks)
    if capacity < 1:
        fail(422, "impact-scope-limit", "영향 분석 근거 범위를 나누세요.")
    if len(impact["items"]) > capacity:
        impact["items"] = impact["items"][:capacity]
        impact["coverage"]["truncated"] = True
        impact["coverage"]["unknown"] = sorted(set(impact["coverage"]["unknown"]) | {"atomic-task-limit"})
    digest = _hash({"changeId": identifier, "change": {k: change[k] for k in
                    ("targetId", "changeType", "before", "after", "reason")}, "impact": impact})
    impact["impactHash"] = digest
    key, sha = ctx.put_json("wb_change", identifier, digest + "/impact.json", impact)
    tasks, writes = [], []
    for item in impact["items"]:
        task_id = "task-" + _hash([identifier, digest, item["targetId"], item["role"]])[:40]
        previous = ctx.storage.get(ctx.owner, "wb_task", task_id)
        if previous:
            tasks.append(previous)
            continue
        task = {"id": task_id, "projectId": ctx.project_id, "createdBy": ctx.actor,
                "changeId": identifier, "impactHash": digest, "targetId": item["targetId"],
                "title": item["title"], "role": item["role"], "status": "open",
                "assigneeSub": None, "evidenceRefs": [], "sourceRefs": item["sourceRefs"],
                "generation": impact["generation"], "confidence": item["confidence"]}
        task["graphAuthority"] = authority
        writes.append(ctx.write("wb_task", task))
        tasks.append(task)
    updated = {**change, "status": "analyzed", "impactHash": digest, "impactKey": key, "impactSha": sha,
               "generation": impact["generation"], "sourceRefs": impact["sourceRefs"],
               "taskIds": [task["id"] for task in tasks], "graphAuthority": "canonical" if canonical else "legacy"}
    saved = ctx.commit([ctx.write("wb_change", updated, change["version"]), *writes], checks)
    tasks_by_id = {task["id"]: task for task in saved[1:]}
    return {"change": saved[0], "impact": impact, "tasks": [tasks_by_id.get(t["id"], t) for t in tasks]}


def read_impact(ctx, identifier):
    ctx.fresh()
    change = ctx.get("wb_change", identifier)
    if not change.get("impactKey"):
        fail(409, "analysis-required", "영향 분석을 먼저 실행하세요.")
    knowledge.verify_refs(ctx, change["sourceRefs"], authority=change.get("graphAuthority", "legacy"))
    manifest_kind, manifest_id = ("ontology", "project-current") if change.get("graphAuthority") == "canonical" else ("wb_index", "current")
    manifest = ctx.storage.get(ctx.owner, manifest_kind, manifest_id) or {}
    if manifest.get("generation") != change.get("generation"):
        fail(409, "stale-impact", "그래프가 변경되어 재분석이 필요합니다.")
    result = ctx.read_json(change["impactKey"], change["impactSha"])
    ctx.fresh()
    current = ctx.get("wb_change", identifier)
    knowledge.verify_refs(ctx, current["sourceRefs"], authority=current.get("graphAuthority", "legacy"))
    active = ctx.storage.get(ctx.owner, manifest_kind, manifest_id) or {}
    if (current["version"] != change["version"]
            or current.get("impactHash") != change.get("impactHash")
            or current.get("impactSha") != change.get("impactSha")
            or active.get("generation") != change.get("generation")
            or active.get("version") != manifest.get("version")):
        fail(409, "stale-impact", "조회 중 영향 분석 또는 게시 버전이 변경되었습니다.")
    return result


def update_task(ctx, identifier, body):
    fields(body, {"version", "status", "assigneeSub", "evidenceRefs"})
    ctx.fresh()
    task = ctx.get("wb_task", identifier)
    ctx.fresh({"owner", task["role"]})
    authority = task.get("graphAuthority", "legacy")
    knowledge.authorize_refs(ctx, task.get("sourceRefs", []), authority=authority)
    if _version(body.get("version")) != task["version"]:
        fail(409, "conflict", "작업 버전이 변경되었습니다.")
    status = body.get("status")
    if status not in {"open", "in-progress", "blocked", "done"}:
        fail(400, "invalid-task", "작업 상태가 올바르지 않습니다.")
    assignee = body.get("assigneeSub", task.get("assigneeSub"))
    if assignee is not None:
        member = ctx.scope["project"]["members"].get(assignee)
        if not member or member["role"] not in {"owner", task["role"]}:
            fail(400, "invalid-assignee", "해당 역할의 현재 프로젝트 구성원만 배정할 수 있습니다.")
    refs = body.get("evidenceRefs", task.get("evidenceRefs", []))
    checks = knowledge.verify_refs(ctx, refs, authority=authority)
    if status == "done":
        change = ctx.get("wb_change", task["changeId"])
        impact = read_impact(ctx, task["changeId"])
        if change["impactHash"] != task["impactHash"] or impact["impactHash"] != task["impactHash"]:
            fail(409, "stale-task", "현재 영향 분석에 해당하지 않는 작업입니다.")
        if not refs or not any(ref in task["sourceRefs"] for ref in refs):
            fail(422, "evidence-required", "이 작업의 현재 소스 근거가 필요합니다.")
        checks.extend(knowledge.verify_refs(ctx, task["sourceRefs"], authority=authority))
        checks.append(ctx.check("wb_change", change))
        kind, manifest_id = ("ontology", "project-current") if authority == "canonical" else ("wb_index", "current")
        manifest = ctx.storage.get(ctx.owner, kind, manifest_id)
        if manifest:
            checks.append(ctx.check(kind, manifest))
    updated = {**task, "status": status, "assigneeSub": assignee, "evidenceRefs": refs,
               "completedBy": ctx.actor if status == "done" else None,
               "completedAt": ctx.storage.clock() if status == "done" else None}
    return ctx.commit([ctx.write("wb_task", updated, task["version"])], checks)[0]
