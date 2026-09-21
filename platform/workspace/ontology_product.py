"""Atomically project published product guidance into the canonical manifest."""
from __future__ import annotations

import copy
from types import SimpleNamespace

from workbench.service import Service
from workspace import ontology_schema as schema
from workspace.ontology_sources import guideline_reference
from workspace.ontology_store import Ontology, CURRENT


def prepare_product(collaboration, scope, product, guideline, legacy):
    """Return writes for the caller's existing product publication transaction.

    The caller already authorized publish and writes the new guideline/product
    records in that same transaction. The new source is not read before commit.
    """
    ctx = Service(SimpleNamespace(storage=collaboration.storage, collaboration=collaboration),
                  scope, {"sub": scope["actor"]})
    ctx.fresh({"owner", "planner"})
    store = Ontology(ctx)
    current = store.current()
    name = "product-" + product["id"]
    partition = schema.identity("partition", name)
    prior = store._part(current, partition) if current else None
    ref = guideline_reference(guideline)
    nodes, edges = [], []
    revisions = {n["id"]: n["revision"] for n in prior["graph"]["nodes"]} if prior else {}

    def node(label, local_id, title, properties, state="approved", aliases=()):
        identifier = schema.identity("node", partition, local_id)
        result = schema.seal({"id": identifier, "scope": {"kind": "project", "projectId": scope["project"]["id"]},
            "type": label, "title": title if len(title) <= 300 else title[:297] + "…", "revision": revisions.get(identifier, 0) + 1,
            "sourceRefs": [ref], "provenance": "declared", "reviewState": state,
            "tombstone": False, "properties": properties, "aliases": list(aliases)})
        nodes.append(result)
        return result

    def edge(source, target, kind):
        edges.append(schema.seal({"id": schema.identity("edge", partition, source["id"], target["id"], kind),
            "type": kind, "src": {"id": source["id"], "revision": source["revision"]},
            "dst": {"id": target["id"], "revision": target["revision"]}, "sourceRefs": [ref],
            "provenance": "declared", "reviewState": "approved", "tombstone": False}))

    root = node("Product", product["id"], product["title"], {
        "productId": product["id"], "guidelineId": guideline["id"]},
        aliases=[{"namespace": "workspace-product", "value": product["id"]}])
    for condition in product["conditions"]:
        rule = node("PolicyRule", "condition-" + condition["id"], condition["text"], {
            "ruleId": condition["id"], "required": True, "guidelineId": guideline["id"]},
            aliases=[{"namespace": "workspace-condition", "value": condition["id"]}])
        edge(root, rule, "GOVERNED_BY")
    for notice in product["notices"]:
        rule = node("PolicyRule", "notice-" + notice["id"], notice["title"], {
            "ruleId": notice["id"], "required": notice["required"],
            "guidelineId": guideline["id"]},
            aliases=[{"namespace": "workspace-policy", "value": notice["id"]}])
        edge(root, rule, "GOVERNED_BY")
    unknown = ["design-and-code-not-linked"]
    if product["steps"]:
        # Legacy Procedure nodes are business steps, not individual proven
        # screen transitions. Preserve their original projection separately.
        procedure = node("Procedure", "procedure", product["title"] + " · 업무 절차", {
            "procedureId": schema.identity("procedure", product["id"]),
            "description": "Published business sequence; read its exact guideline for step content. Screen mappings remain unresolved.",
            "legacyType": "published-business-step-sequence"}, state="candidate")
        edge(root, procedure, "USES")
        unknown.append("business-steps-not-linked-to-screens")
    graph = schema.validate_graph({"schemaVersion": 1, "projectId": ctx.project_id, "nodes": nodes, "edges": edges,
        "coverage": {"complete": False, "scope": "published-product-partition", "truncated": False, "unknown": unknown}})
    part = {"partitionId": partition, "name": name, "kind": "published-product",
            "createdBy": prior["createdBy"] if prior else scope["actor"], "updatedBy": scope["actor"],
            "guidelineId": guideline["id"], "legacyProjectionHash": legacy["hash"], "graph": graph}
    updated = copy.deepcopy(current or {"id": CURRENT, "schemaVersion": 1, "projectId": ctx.project_id,
                                       "partitions": {}, "indexes": {}})
    updated["partitions"][partition] = store._put_json(partition, part)
    store._replace_indexes(updated, current or {}, partition, prior["graph"] if prior else {"nodes": [], "edges": []}, graph)
    updated["generation"] = schema.digest({"partitions": updated["partitions"], "indexes": updated["indexes"]})
    return [collaboration._write(ctx.owner, "ontology", updated, current["version"] if current else None)], {
        "generation": updated["generation"], "partitionId": partition, "nodeId": root["id"]}
