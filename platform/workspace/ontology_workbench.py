"""Workbench projection of the workspace canonical ontology; not another store."""
from __future__ import annotations

from workspace.ontology_store import Ontology
from workspace import ontology_schema as schema
from workspace.ontology_sources import workbench_reference
from workbench.service import fields


LABELS = {"Atom": "Component", "Molecule": "Component", "Organism": "Component",
          "PageTemplate": "Component", "Procedure": "Flow", "PolicyRule": "Rule", "Team": "Role"}
RELATIONS = {"COMPOSES": "DEPENDS_ON", "IMPORTS": "DEPENDS_ON", "GOVERNED_BY": "CONFORMS_TO",
             "OWNED_BY": "OWNS"}


def graph(ctx, target_id=None, *, historical=False):
    store = Ontology(ctx)
    if target_id:
        # This endpoint takes a raw node ID, not an exact old-source reference.
        # Opaque changed-source expansion belongs to /ontology/impact's
        # oldSource path; arbitrary IDs must not become connectivity probes.
        current = store.current()
        target = store._node(current or {}, target_id)
        if not target or not store._visible_node(current, target, historical=historical):
            from workbench.service import fail
            fail(404, "not-found", "읽을 수 있는 온톨로지 대상이 없습니다.")
        value = store.closure([target_id], direction="dependents" if historical else "both", max_nodes=100,
                              historical=historical)
        store._recheck(current)
    else:
        value = store.read(limit=100)
    # The compatibility fields preserve source refs and original canonical type.
    # Consumers can migrate incrementally without becoming a second authority.
    nodes = [{**{key: item for key, item in node.items() if key != "contentHash"},
              "canonical": node, "label": LABELS.get(node["type"], node["type"]),
              "canonicalType": node["type"], "version": str(node["revision"]),
              "sourceRef": node["sourceRefs"][0]} for node in value["nodes"]]
    edges = [{**{key: item for key, item in edge.items() if key != "contentHash"},
              "canonical": edge, "src": edge["src"]["id"], "dst": edge["dst"]["id"],
              "rel": RELATIONS.get(edge["type"], edge["type"]), "canonicalRelation": edge["type"],
              "sourceRef": edge["sourceRefs"][0]} for edge in value["edges"]]
    for edge in edges:
        if edge["canonicalRelation"] == "OWNED_BY":
            edge["src"], edge["dst"] = edge["dst"], edge["src"]
    result = {"nodes": nodes, "edges": edges, "generation": value["generation"], "coverage": value["coverage"],
              "backend": "workspace-project-ontology", "cursor": value.get("cursor"), "historical": historical}
    if historical:
        result["impactSeeds"] = value.get("impactSeeds", [])
    return result


def import_legacy(ctx, body):
    fields(body, {"requestId", "expectedGeneration", "nodeIds"})
    ctx.fresh({"owner"})
    from workbench import knowledge
    baseline = ctx.storage.get(ctx.owner, "wb_index", "current")
    if not baseline:
        from workbench.service import fail
        fail(409, "legacy-not-indexed", "기존 지식 자료의 수집·색인을 먼저 완료하세요.")
    selected = body.get("nodeIds")
    if selected is not None:
        if not isinstance(selected, list) or not 1 <= len(selected) <= 20:
            from workbench.service import fail
            fail(422, "legacy-import-scope", "가져올 기존 노드를 1~20개 선택하세요.")
        selected = sorted({schema._identifier(identifier) for identifier in selected})
    old = knowledge.legacy_graph(ctx, selected_ids=selected) if selected is not None else knowledge.legacy_graph(ctx)
    if (old["coverage"].get("truncated") or old["coverage"].get("conflictingNodeIds")
            or old["coverage"].get("incompleteSnapshot")):
        from workbench.service import fail
        fail(422, "legacy-import-incomplete", "일부만 조회된 기존 그래프는 교체할 수 없습니다. 원본 범위를 나누고 충돌을 해결하세요.")
    if baseline and baseline.get("generation") != old.get("generation"):
        from workbench.service import fail
        fail(409, "legacy-source-changed", "가져오는 동안 기존 지식 그래프가 변경되었습니다.")
    nodes, edges, ids, missing = [], {}, set(), set()
    types = {"Flow": "Procedure", "Guideline": "Document", "Rule": "PolicyRule", "Condition": "PolicyRule",
             "Role": "Team", "Icon": "Foundation"}
    property_ids = {"Product": "productId", "Document": "documentId", "PolicyRule": "ruleId",
                    "Team": "teamId", "API": "apiId", "Test": "testId", "Skill": "skillId"}
    for old_node in old["nodes"]:
        kind = types.get(old_node["label"], old_node["label"])
        if kind not in schema.NODE_TYPES:
            missing.add("unmapped-legacy-type")
            continue
        properties = {property_ids[kind]: old_node["id"]} if kind in property_ids else {}
        row = {"id": old_node["id"], "scope": {"kind": "project", "projectId": ctx.project_id},
               "type": kind, "title": old_node["title"], "revision": 1,
               "sourceRefs": [workbench_reference(old_node["sourceRef"])],
               "provenance": "declared", "reviewState": "candidate", "tombstone": False,
               "properties": properties, "aliases": [{"namespace": "workbench-v1", "value": old_node["id"]}]}
        if kind == "Foundation":
            row["subtype"] = "icon"
        try:
            node = schema.validate_node(schema.seal(row))
        except (ValueError, TypeError):
            missing.add("unmapped-legacy-node-shape")
            continue
        nodes.append(node)
        ids.add(row["id"])
    relations = {"DEPENDS_ON": "USES", "CONFORMS_TO": "GOVERNED_BY", "REQUIRES": "USES"}
    node_types = {node["id"]: node["type"] for node in nodes}
    node_by_id = {node["id"]: node for node in nodes}
    for old_edge in old["edges"]:
        if old_edge["src"] not in ids or old_edge["dst"] not in ids:
            missing.add("unmapped-legacy-endpoint")
            continue
        source, target = old_edge["src"], old_edge["dst"]
        kind = relations.get(old_edge["rel"], old_edge["rel"])
        if kind == "OWNS":
            source, target, kind = target, source, "OWNED_BY"
        if kind == "VALIDATES":
            kind = "REFERENCES"
        if kind not in schema.EDGE_TYPES or kind == "GOVERNED_BY" and node_types[target] != "PolicyRule":
            missing.add("unmapped-legacy-relation")
            continue
        row = {"id": schema.identity("legacy-edge", source, kind, target), "type": kind,
            "src": {"id": source, "revision": 1}, "dst": {"id": target, "revision": 1},
            "sourceRefs": [workbench_reference(old_edge["sourceRef"])], "provenance": "declared",
            "reviewState": "candidate", "tombstone": False}
        try:
            schema.validate_graph({"schemaVersion": 1, "projectId": ctx.project_id,
                "nodes": [node_by_id[key] for key in sorted({source, target})], "edges": [schema.seal(row)]})
        except (ValueError, TypeError):
            missing.add("unmapped-legacy-edge-shape")
            continue
        # Preserve parallel evidence without colliding identities or silently
        # dropping references at the per-edge schema limit.
        identity = row["id"]
        ref = row["sourceRefs"][0]
        group = edges.setdefault(identity, {**row, "sourceRefs": []})["sourceRefs"]
        if ref not in group:
            group.append(ref)
    projected_edges = []
    for identity, row in sorted(edges.items()):
        refs = row["sourceRefs"]
        refs.sort(key=schema.digest)
        for offset in range(0, len(refs), schema.MAX_REFS):
            projected_edges.append(schema.seal({**row, "id": schema.identity("legacy-evidence", identity, offset),
                "sourceRefs": refs[offset:offset + schema.MAX_REFS]}))
    value = {"schemaVersion": 1, "projectId": ctx.project_id, "nodes": nodes, "edges": projected_edges,
             "coverage": {"complete": False, "scope": "explicit-workbench-migration",
                          "truncated": old["coverage"].get("truncated", False),
                          "unknown": sorted(missing | set(old["coverage"].get("unknown", [])) | {"legacy-mappings-require-review"})}}
    name = schema.identity("legacy-scope", selected) if selected is not None else "legacy-workbench"
    return Ontology(ctx).publish_candidate(name, value,
        expected_generation=body.get("expectedGeneration"), request_id=body.get("requestId"),
        additional_checks=[ctx.check("wb_index", baseline)] if baseline else [],
        _origin="workbench-import", _replacement_complete=not missing)
