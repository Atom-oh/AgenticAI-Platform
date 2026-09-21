"""Revision-bound reverse dependency paths without crossing unreadable nodes."""
from __future__ import annotations

from collections import deque

from workspace import ontology_schema as schema

LIMITS = {"nodes": 500, "edges": 1000, "hops": 12, "items": 50}
STRUCTURAL = schema.DEPENDENCIES - {"GOVERNED_BY"}
CHANGE_EDGES = {
    "asset": STRUCTURAL, "token": STRUCTURAL, "design": STRUCTURAL, "code": STRUCTURAL,
    "kit": STRUCTURAL, "rule": schema.DEPENDENCIES, "product-condition": schema.DEPENDENCIES,
    "procedure": STRUCTURAL, "permission": schema.DEPENDENCIES,
}


def _same_source(left, right):
    return all(left.get(key) == right.get(key) for key in ("sourceKind", "sourceId", "revision", "sha256"))


def analyze(graph, change, *, generation, can_read):
    schema.validate_graph(graph, diagnostic=True)
    schema._hash(generation)
    schema._fields(change, {"id", "kind", "baseGeneration"}, {"oldSource", "nodeIds", "newSource"})
    schema._identifier(change["id"])
    if change["baseGeneration"] != generation:
        raise ValueError("Impact must use the selected manifest generation")
    if change["kind"] not in CHANGE_EDGES or not callable(can_read):
        raise ValueError("Impact requires a known change kind and authorization filter")
    seeds = change.get("nodeIds", [])
    if not isinstance(seeds, list) or len(seeds) > 20:
        raise ValueError("Invalid impact seed list")
    for seed in seeds:
        schema._identifier(seed)
    old = schema.source_ref(change["oldSource"]) if change.get("oldSource") else None
    if change.get("newSource"):
        schema.source_ref(change["newSource"])
    if not seeds and old is None:
        raise ValueError("An exact source revision or canonical node seed is required")
    nodes = {node["id"]: node for node in graph["nodes"]}
    visible = {identifier for identifier, node in nodes.items()
               if node["reviewState"] != "rejected" and can_read(node["sourceRefs"])}
    if old:
        seeds = [*seeds, *(node["id"] for node in nodes.values()
                          if any(_same_source(ref, old) for ref in node["sourceRefs"]))]
        seeds.extend(edge["src"]["id"] for edge in graph["edges"]
                     if edge["type"] in CHANGE_EDGES[change["kind"]]
                     and any(_same_source(ref, old) for ref in edge["sourceRefs"])
                     and can_read(edge["sourceRefs"]))
    missing_seeds = bool(set(seeds) - visible)
    seeds = sorted(set(seed for seed in seeds if seed in visible))
    reverse = {}
    hidden_boundary = False
    for edge in graph["edges"]:
        if edge["reviewState"] == "rejected" or edge["type"] not in CHANGE_EDGES[change["kind"]]:
            continue
        reverse.setdefault(edge["dst"]["id"], []).append(edge)
    for edges in reverse.values():
        edges.sort(key=lambda edge: (edge["src"]["id"], edge["type"], edge["id"]))
    pending = deque((seed, [seed], []) for seed in seeds)
    seen, seen_edges, items = set(), set(), []
    truncated = False
    while pending:
        identifier, path, witnesses = pending.popleft()
        if identifier in seen:
            continue
        if len(seen) >= LIMITS["nodes"] or len(items) >= LIMITS["items"]:
            truncated = True
            break
        seen.add(identifier)
        node = nodes[identifier]
        if node["type"] == "Team":
            continue
        evidence = [node, *witnesses, *(nodes[prior] for prior in path[:-1])]
        stale = any(edge["tombstone"] or any(nodes[edge[end]["id"]]["revision"] != edge[end]["revision"] for end in ("src", "dst"))
                    for edge in witnesses)
        candidate = stale or "restricted-source-boundary" in graph.get("coverage", {}).get("unknown", []) or any(
            item["tombstone"] or item["reviewState"] != "approved"
            or item["provenance"] == "model-inferred" for item in evidence)
        method = "candidate" if candidate else "approved-declared" if any(
            item["provenance"] == "declared" for item in evidence) else "observed-structural"
        refs = {schema.digest(ref): ref for item in evidence for ref in item["sourceRefs"]}
        items.append({"nodeId": identifier, "revision": node["revision"], "title": node["title"],
                      "type": node["type"], "evidenceKind": method,
                      "action": "investigate" if candidate else "revalidate",
                      "witnessPath": path, "witnessEdges": [edge["id"] for edge in witnesses],
                      "staleWitness": stale,
                      "sourceRefs": list(refs.values())})
        if len(witnesses) >= LIMITS["hops"]:
            if reverse.get(identifier):
                truncated = True
            continue
        for edge in reverse.get(identifier, []):
            source = edge["src"]["id"]
            if source not in visible or not can_read(edge["sourceRefs"]):
                hidden_boundary = True
                continue
            if edge["id"] not in seen_edges and len(seen_edges) >= LIMITS["edges"]:
                truncated = True
                break
            seen_edges.add(edge["id"])
            if source not in seen:
                pending.append((source, [*path, source], [*witnesses, edge]))
    unknown = set(graph.get("coverage", {}).get("unknown", []))
    if hidden_boundary:
        unknown.add("restricted-or-unmapped")
    if not seeds or missing_seeds:
        unknown.add("unmapped-or-inaccessible-seed")
    if any(item["evidenceKind"] == "candidate" for item in items):
        unknown.add("unreviewed-dependencies")
    if truncated or graph.get("coverage", {}).get("truncated"):
        unknown.add("truncated")
    # A bounded project snapshot cannot certify dependencies outside its scope.
    unknown.add("outside-snapshot-not-certified")
    result = {"schemaVersion": 1, "changeId": change["id"], "generation": generation,
              "items": items, "coverage": {"complete": False, "truncated": truncated or graph.get("coverage", {}).get("truncated", False),
                                         "unknown": sorted(unknown), "scope": "authorized-manifest-snapshot"}}
    result["hash"] = schema.digest(result)
    return result
