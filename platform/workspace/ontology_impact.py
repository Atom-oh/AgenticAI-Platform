"""Revision-bound reverse dependency paths without crossing unreadable nodes."""
from __future__ import annotations

from collections import deque
import json

from workspace import ontology_schema as schema
from workspace.ontology_sources import authority_identity

LIMITS = {"nodes": 500, "edges": 1000, "hops": 12, "items": 50}
STRUCTURAL = schema.DEPENDENCIES - {"GOVERNED_BY"}
CHANGE_EDGES = {
    "asset": STRUCTURAL, "token": STRUCTURAL, "design": STRUCTURAL, "code": STRUCTURAL,
    "kit": STRUCTURAL, "rule": schema.DEPENDENCIES, "product-condition": schema.DEPENDENCIES,
    "procedure": STRUCTURAL, "permission": schema.DEPENDENCIES,
}


def check_output_budget(parts, maximum=3_500_000):
    from workbench.service import fail
    total = 0
    for part in parts:
        try:
            total += len(schema.canonical(part))
        except (ValueError, UnicodeError):
            fail(422, "ontology-impact-scope", "영향 근거의 직렬화 한도를 초과했습니다. 조회 범위를 나누세요.")
        if total > maximum:
            fail(422, "ontology-impact-scope", "영향 근거의 응답 크기 한도를 초과했습니다. 조회 범위를 나누세요.")


def check_metadata_budget(records):
    from workbench.service import fail
    from workspace.storage import MAX_RECORD_BYTES
    for record in records:
        # Match Storage's JSON accounting and reserve generated metadata and
        # subsequent approval fields rather than relying on the HTTP budget.
        if len(json.dumps(record, ensure_ascii=False, allow_nan=False, default=str).encode()) > MAX_RECORD_BYTES - 8192:
            fail(422, "ontology-impact-scope", "영향 근거의 저장 크기 한도를 초과했습니다. 조회 범위를 나누세요.")


def _same_source(left, right):
    return authority_identity(left) == authority_identity(right)


def analyze(graph, change, *, generation, can_read):
    schema.validate_graph(graph, diagnostic=True)
    schema._hash(generation)
    schema._fields(change, {"id", "kind", "baseGeneration"}, {"oldSource", "nodeIds", "newSource", "requestHash"})
    if "requestHash" in change:
        schema._hash(change["requestHash"])
    schema._identifier(change["id"])
    if change["baseGeneration"] != generation:
        raise ValueError("Impact must use the selected manifest generation")
    if change["kind"] not in CHANGE_EDGES or not callable(can_read):
        raise ValueError("Impact requires a known change kind and authorization filter")
    seeds = change.get("nodeIds", [])
    if not isinstance(seeds, list) or len(seeds) > LIMITS["nodes"]:
        raise ValueError("Invalid impact seed list")
    for seed in seeds:
        schema._identifier(seed)
    old = schema.source_ref(change["oldSource"]) if change.get("oldSource") else None
    new = schema.source_ref(change["newSource"]) if change.get("newSource") else None
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
        evidence = [node, *witnesses, *(nodes[prior] for prior in path[:-1])]
        stale = ("historical-source-revisions" in graph.get("coverage", {}).get("unknown", []) or
                 any(edge["tombstone"] or any(nodes[edge[end]["id"]]["revision"] != edge[end]["revision"] for end in ("src", "dst"))
                     for edge in witnesses))
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
        if node["type"] == "Team":
            continue
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
    # Keep one display path, but retain every authorized dependency witness
    # within the inspected result subgraph, including convergence and cycles.
    included = {item["nodeId"] for item in items}
    outgoing = {}
    for edges in reverse.values():
        for edge in edges:
            if (edge["src"]["id"] in included and edge["dst"]["id"] in included
                    and edge["id"] in seen_edges):
                outgoing.setdefault(edge["src"]["id"], []).append(edge)
    for item in items:
        pending_proofs, proof_nodes, proof_edges = [item["nodeId"]], set(), {}
        while pending_proofs:
            identifier = pending_proofs.pop()
            if identifier in proof_nodes:
                continue
            proof_nodes.add(identifier)
            for edge in outgoing.get(identifier, []):
                proof_edges[edge["id"]] = edge
                pending_proofs.append(edge["dst"]["id"])
        evidence = [nodes[key] for key in sorted(proof_nodes)] + list(proof_edges.values())
        stale = ("historical-source-revisions" in graph.get("coverage", {}).get("unknown", [])
                 or any(entry["tombstone"] or entry["reviewState"] == "deprecated" for entry in evidence)
                 or any(nodes[edge[end]["id"]]["revision"] != edge[end]["revision"]
                        for edge in proof_edges.values() for end in ("src", "dst")))
        candidate = stale or "restricted-source-boundary" in graph.get("coverage", {}).get("unknown", []) or any(
            entry["reviewState"] != "approved" or entry["provenance"] == "model-inferred" for entry in evidence)
        item.update(
            witnessEdges=sorted(proof_edges),
            sourceRefs=list({schema.digest(ref): ref for entry in evidence for ref in entry["sourceRefs"]}.values()),
            staleWitness=stale, action="investigate" if candidate else "revalidate",
            evidenceKind="candidate" if candidate else "approved-declared" if any(
                entry["provenance"] == "declared" for entry in evidence) else "observed-structural")
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
    # Bind opaque historical input through its digest without disclosing its
    # metadata; the API separately verifies current authority for a new source.
    result = {"schemaVersion": 1, "changeId": change["id"], "generation": generation,
              "change": {"kind": change["kind"], "oldSourceHash": schema.digest(old) if old else None,
                         "newSourceHash": schema.digest(new) if new else None,
                         "selectionHash": schema.digest(sorted(set(change.get("nodeIds", [])))),
                         "requestHash": change.get("requestHash", schema.digest(change))},
              "items": items, "coverage": {"complete": False, "truncated": truncated or graph.get("coverage", {}).get("truncated", False),
                                         "unknown": sorted(unknown), "scope": "authorized-manifest-snapshot"}}
    check_output_budget(items)
    result["hash"] = schema.digest(result)
    return result
