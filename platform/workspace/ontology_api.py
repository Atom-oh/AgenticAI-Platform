"""Project-authorized canonical ontology API, shared by workbench consumers."""
from __future__ import annotations

from workbench.service import Service, fields, fail, public, limit
from workspace import ontology_schema as schema
from workspace.ontology_store import Ontology


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
        from workspace.ontology_impact import analyze
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
        seeds = list(raw_seeds)
        restricted_source = False
        if body.get("oldSource"):
            restricted_source = not ontology._visible([body["oldSource"]], historical=True)
            seeds.extend(ontology.source_nodes(body["oldSource"], for_impact=True))
        seeds = sorted(set(seeds))
        graph = (ontology.closure(seeds[:20], direction="dependents", max_nodes=500, historical=True) if seeds else
                 {"schemaVersion": 1, "projectId": ctx.project_id, "nodes": [], "edges": [], "impactSeeds": [],
                  "coverage": {"complete": False, "truncated": False, "unknown": ["unmapped-or-inaccessible-seed"],
                               "scope": "authorized-manifest-snapshot"}})
        impact_seeds = graph.pop("impactSeeds")
        change = {"id": body.get("changeId"), "kind": body.get("kind"), "baseGeneration": current["generation"],
                  "nodeIds": impact_seeds, "requestHash": schema.digest({**body, "nodeIds": sorted(set(raw_seeds))})}
        if body.get("oldSource"):
            change["oldSource"] = body["oldSource"]
        if body.get("newSource"):
            change["newSource"] = body["newSource"]
        if len(seeds) > 20:
            graph["coverage"]["truncated"] = True
            graph["coverage"]["unknown"].append("seed-limit")
        if restricted_source:
            graph["coverage"]["unknown"].append("restricted-source-boundary")
        result = analyze({key: value for key, value in graph.items() if key != "generation"},
                         change, generation=current["generation"], can_read=lambda refs: ontology._visible(refs, historical=True))
        ontology._recheck(current)
        return 200, result
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
