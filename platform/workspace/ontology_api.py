"""Project-authorized canonical ontology API, shared by workbench consumers."""
from __future__ import annotations

from workbench.service import Service, fields, fail, public, limit
from workspace import ontology_schema as schema
from workspace.ontology_store import Ontology


def _impact_views(ontology, current, body, raw_seeds):
    from workspace.ontology_impact import analyze
    seeds, history, history_truncated = list(raw_seeds), [], False
    restricted = False
    if body.get("oldSource"):
        restricted = not ontology._visible([body["oldSource"]], historical=True)
        seeds.extend(ontology.source_nodes(body["oldSource"], for_impact=True, include_history=False))
        history, history_truncated = ontology.source_history(body["oldSource"])
    views = ([(ontology, sorted(set(seeds)), None)] if seeds or not history else []) + history
    results, snapshots, used_nodes, used_edges = [], [], 0, 0
    truncated = history_truncated
    request_hash = schema.digest({**body, "nodeIds": sorted(set(raw_seeds))})
    for reader, selected, snapshot_hash in views:
        if used_nodes >= 500 or used_edges >= 1000 or sum(len(value["items"]) for value in results) >= 50:
            truncated = True
            break
        generation = reader.current()["generation"]
        graph = (reader.closure(selected[:20], direction="dependents", max_nodes=500 - used_nodes,
                               max_edges=1000 - used_edges, historical=True) if selected else
                 {"schemaVersion": 1, "projectId": ontology.ctx.project_id, "nodes": [], "edges": [], "impactSeeds": [],
                  "coverage": {"complete": False, "truncated": False, "unknown": ["unmapped-or-inaccessible-seed"],
                               "scope": "authorized-manifest-snapshot"}})
        used_nodes += reader._last_inspection[0]
        used_edges += reader._last_inspection[1]
        impact_seeds = graph.pop("impactSeeds")
        if len(selected) > 20:
            graph["coverage"]["truncated"] = True
            graph["coverage"]["unknown"].append("seed-limit")
        if restricted:
            graph["coverage"]["unknown"].append("restricted-source-boundary")
        if snapshot_hash:
            graph["coverage"]["unknown"].append("historical-source-revisions")
        graph["coverage"]["complete"] = False
        change = {"id": body.get("changeId"), "kind": body.get("kind"), "baseGeneration": generation,
                  "nodeIds": impact_seeds, "requestHash": request_hash}
        for key in ("oldSource", "newSource"):
            if body.get(key):
                change[key] = body[key]
        value = analyze({key: item for key, item in graph.items() if key != "generation"}, change,
                        generation=generation, can_read=lambda refs: reader._visible(refs, historical=True))
        for item in value["items"]:
            item["snapshotGeneration"] = generation
        results.append(value)
        snapshots.append({"generation": generation, "manifestHash": snapshot_hash, "historical": bool(snapshot_hash)})
    # At least one empty/current view is processed for an empty selection.
    result = results[0]
    items = [item for value in results for item in value["items"]]
    unknown = {reason for value in results for reason in value["coverage"]["unknown"]}
    truncated = truncated or len(items) > 50 or any(value["coverage"]["truncated"] for value in results)
    if truncated:
        unknown.add("truncated")
    if history:
        unknown.add("historical-source-snapshots")
    result.update(generation=current["generation"], items=items[:50], sourceSnapshots=snapshots,
                  coverage={"complete": False, "truncated": truncated, "unknown": sorted(unknown),
                            "scope": "authorized-current-and-historical-snapshots"})
    result["change"] = {"kind": body["kind"], "requestHash": request_hash,
                        "selectionHash": schema.digest(sorted(set(raw_seeds))),
                        "oldSourceHash": schema.digest(body["oldSource"]) if body.get("oldSource") else None,
                        "newSourceHash": schema.digest(body["newSource"]) if body.get("newSource") else None}
    result.pop("hash", None)
    result["hash"] = schema.digest(result)
    ontology._recheck(current)
    return result


def route(host, scope, claims, method, parts, body, query):
    ctx = Service(host, scope, claims)
    if parts == ["schema"] and method == "GET":
        return 200, {"schemaVersion": 1, "levels": list(schema.LEVELS), "nodeTypes": sorted(schema.NODE_TYPES),
                     "edgeTypes": sorted(schema.EDGE_TYPES), "sourceKinds": sorted(schema.SOURCE_KINDS),
                     "backend": "workspace-project-ontology",
                     "analyzerConfigured": bool(getattr(host, "ontology_analyzer_ready", False))}
    ontology = Ontology(ctx)
    if parts == ["import-workbench"] and method == "POST":
        from workspace.ontology_workbench import import_legacy
        return 201, import_legacy(ctx, body)
    if not parts and method == "GET":
        return 200, ontology.read(limit=limit(query), cursor=query.get("cursor"))
    if len(parts) == 2 and parts[0] == "nodes" and method == "GET":
        result = ontology.read([parts[1]])
        if not result["nodes"]:
            fail(404, "not-found", "읽을 수 있는 온톨로지 노드가 없습니다.")
        return 200, {"node": result["nodes"][0], "generation": result["generation"]}
    if len(parts) == 3 and parts[0] == "nodes" and parts[2] == "review" and method == "POST":
        fields(body, {"requestId", "expectedGeneration", "revision", "decision", "reason"})
        return 200, public(ontology.review_node(parts[1], expected_generation=body.get("expectedGeneration"),
            revision=body.get("revision"), decision=body.get("decision"), reason=body.get("reason"),
            request_id=body.get("requestId")))
    if parts == ["context"] and method == "POST":
        fields(body, {"nodeIds"})
        return 200, ontology.context(body.get("nodeIds"))
    if parts == ["partitions"] and method == "POST":
        fields(body, {"requestId", "name", "graph", "expectedGeneration"})
        return 201, ontology.publish_candidate(body.get("name"), body.get("graph"),
            expected_generation=body.get("expectedGeneration"), request_id=body.get("requestId"))
    if parts == ["impact"] and method == "POST":
        fields(body, {"changeId", "kind", "expectedGeneration", "oldSource", "nodeIds", "newSource"})
        current = ontology.current()
        if not current or current["generation"] != body.get("expectedGeneration"):
            fail(409, "ontology-changed", "영향 분석 기준이 변경되었습니다.")
        if body.get("newSource"):
            ontology.sources.verify([body["newSource"]], recheck=False)
        raw_seeds = body.get("nodeIds", [])
        if not isinstance(raw_seeds, list) or len(raw_seeds) > 20:
            fail(400, "ontology-selection", "시작 노드 목록이 올바르지 않습니다.")
        for seed in raw_seeds:
            schema._identifier(seed)
            node = ontology._node(current, seed)
            if not node or not ontology._visible_node(current, node, historical=True):
                fail(404, "not-found", "읽을 수 있는 영향 분석 시작 노드가 없습니다.")
        return 200, _impact_views(ontology, current, body, raw_seeds)
    if parts == ["analyses"] and method == "POST":
        from workspace.ontology_jobs import submit
        return 202, public(submit(ctx, body))
    if parts == ["analyses"] and method == "GET":
        from workspace.collaboration import CollaborationError
        from workspace.ontology_jobs import reconcile
        from workspace.ontology_sources import Sources
        page = ctx.page("wb_artifact", query)
        sources, items = Sources(ctx, max_records=2500), []
        for artifact in page["items"]:
            if artifact.get("kind") != "ontology-analysis":
                continue
            try:
                sources.verify(artifact["sourceRefs"], recheck=False)
            except CollaborationError as error:
                if error.status in {403, 404, 409}:
                    continue
                raise
            artifact = reconcile(ctx, artifact)
            items.append({key: artifact[key] for key in
                          ("id", "name", "status", "jobId", "requestId", "createdBy", "createdAt", "updatedAt")
                          if key in artifact})
        sources.recheck()
        return 200, {"items": items, "cursor": page.get("cursor")}
    if len(parts) == 2 and parts[0] == "analyses" and method == "GET":
        from workspace.ontology_jobs import reconcile
        artifact = ctx.get("wb_artifact", parts[1])
        if artifact.get("kind") != "ontology-analysis":
            fail(404, "not-found", "분석 작업이 없습니다.")
        artifact = reconcile(ctx, artifact)
        from workspace.ontology_sources import Sources
        sources = Sources(ctx)
        sources.verify(artifact["sourceRefs"])
        response = {"artifact": public(artifact)}
        if artifact.get("analysisKey"):
            response["analysis"] = ctx.read_json(artifact["analysisKey"], artifact["analysisHash"], 4_000_000)
        sources.recheck()
        if ctx.get("wb_artifact", parts[1])["version"] != artifact["version"]:
            fail(409, "ontology-analysis-changed", "조회 중 분석 기록이 변경되었습니다.")
        return 200, response
    fail(404, "not-found", "온톨로지 경로를 찾을 수 없습니다.")
