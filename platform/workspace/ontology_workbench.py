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


def graph(ctx, target_id=None):
    store = Ontology(ctx)
    if target_id:
        value = store.closure([target_id], direction="both", max_nodes=100)
    else:
        value = store.read(limit=100)
    # The compatibility fields preserve source refs and original canonical type.
    # Consumers can migrate incrementally without becoming a second authority.
    nodes = [{**node, "label": LABELS.get(node["type"], node["type"]),
              "canonicalType": node["type"], "version": str(node["revision"]),
              "sourceRef": node["sourceRefs"][0]} for node in value["nodes"]]
    edges = [{**edge, "src": edge["src"]["id"], "dst": edge["dst"]["id"],
              "rel": RELATIONS.get(edge["type"], edge["type"]), "canonicalRelation": edge["type"],
              "sourceRef": edge["sourceRefs"][0]} for edge in value["edges"]]
    return {"nodes": nodes, "edges": edges, "generation": value["generation"], "coverage": value["coverage"],
            "backend": "workspace-project-ontology", "cursor": value.get("cursor")}


def import_legacy(ctx, body):
    fields(body, {"requestId", "expectedGeneration"})
    ctx.fresh({"owner"})
    from workbench import knowledge
    baseline = ctx.storage.get(ctx.owner, "wb_index", "current")
    old = knowledge.legacy_graph(ctx)
    if baseline and baseline.get("generation") != old.get("generation"):
        from workbench.service import fail
        fail(409, "legacy-source-changed", "가져오는 동안 기존 지식 그래프가 변경되었습니다.")
    nodes, edges, ids, missing = [], [], set(), set()
    types = {"Flow": "Procedure", "Guideline": "Document", "Rule": "PolicyRule", "Role": "Team", "Icon": "Foundation"}
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
        nodes.append(schema.seal(row))
        ids.add(row["id"])
    relations = {"DEPENDS_ON": "USES", "CONFORMS_TO": "GOVERNED_BY", "REQUIRES": "USES"}
    node_types = {node["id"]: node["type"] for node in nodes}
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
        edges.append(schema.seal({"id": schema.identity("legacy-edge", source, kind, target), "type": kind,
            "src": {"id": source, "revision": 1}, "dst": {"id": target, "revision": 1},
            "sourceRefs": [workbench_reference(old_edge["sourceRef"])], "provenance": "declared",
            "reviewState": "candidate", "tombstone": False}))
    value = {"schemaVersion": 1, "projectId": ctx.project_id, "nodes": nodes, "edges": edges,
             "coverage": {"complete": False, "scope": "explicit-workbench-migration",
                          "truncated": old["coverage"].get("truncated", False),
                          "unknown": sorted(missing | {"legacy-mappings-require-review"})}}
    return Ontology(ctx).publish_candidate("legacy-workbench", value,
        expected_generation=body.get("expectedGeneration"), request_id=body.get("requestId"),
        additional_checks=[ctx.check("wb_index", baseline)] if baseline else [])
