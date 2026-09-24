"""Read-only design view over an authorized procedure snapshot. No I/O."""
from __future__ import annotations

from dataclasses import dataclass, field

from workspace.ontology_ux import references

RENDERABLE = ("Atom", "Molecule", "Organism", "Pattern", "Component")
_BLOCKING = {"ambiguous-entry", "stale-endpoint-revisions", "unmapped-or-inaccessible", "too-many-screens", "truncated",
             "retired-or-rejected-mapping", "unapproved-dependency"}


@dataclass
class Knowledge:
    generation: str
    coverage: dict
    assets: dict = field(default_factory=dict)
    templates: dict = field(default_factory=dict)
    screens: dict = field(default_factory=dict)
    procedures: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    unresolved: list = field(default_factory=list)

    @property
    def complete(self):
        return not self.coverage.get("truncated") and not self.unresolved and not _BLOCKING & set(self.coverage.get("unknown", []))


def _live(node, include_candidates):
    if node.get("tombstone"):
        return False
    return node["reviewState"] == "approved" or include_candidates and node["reviewState"] in ("candidate", "reviewed")


def _add_unknown(k, reason):
    k.coverage["unknown"] = sorted(set(k.coverage.get("unknown", [])) | {reason})


def from_snapshot(snapshot, *, include_candidates=False):
    every = {n["id"]: n for n in snapshot.get("nodes", [])}
    nodes = {i: n for i, n in every.items() if _live(n, include_candidates)}
    dropped, retired = set(), set()
    for i, n in every.items():
        if i in nodes:
            continue
        if n.get("tombstone") or n["reviewState"] in ("rejected", "deprecated"):
            retired.add(i)                 # a rejected or retired mapping never silently disappears
        else:
            dropped.add(i)                 # every snapshot node is a dependency of the procedure (fail closed)
    for e in snapshot.get("edges", []):
        if e.get("tombstone") or e["reviewState"] in ("rejected", "deprecated"):
            continue
        src, dst = e["src"]["id"], e["dst"]["id"]
        if src in nodes and dst in every and dst not in nodes and dst not in retired:
            dropped.add(dst)               # candidate dependency of a live node
        if dst in nodes and src in every and src not in nodes and src not in retired \
                and e["type"] in ("PART_OF", "NEXT", "COMPOSES"):
            dropped.add(src)               # candidate member Screen of a live Procedure (review round 4, #5/N11)
    edges = [e for e in snapshot.get("edges", []) if not e.get("tombstone")
             and e["reviewState"] not in ("rejected", "deprecated")
             and e["src"]["id"] in nodes and e["dst"]["id"] in nodes
             and nodes[e["src"]["id"]]["revision"] == e["src"]["revision"]
             and nodes[e["dst"]["id"]]["revision"] == e["dst"]["revision"]]
    k = Knowledge(generation=str(snapshot.get("generation") or ""), coverage=dict(snapshot.get("coverage") or {}))
    k.coverage["unknown"] = list(k.coverage.get("unknown", []))
    if dropped:
        _add_unknown(k, "unapproved-dependency")
        k.coverage["unapproved"] = sorted(dropped)
    if retired:
        _add_unknown(k, "retired-or-rejected-mapping")
        k.coverage["retired"] = sorted(retired)
    entries = {}
    for n in nodes.values():
        props, kind = n.get("properties", {}), n["type"]
        ux = props.get("uxModel", {})
        for alias in n.get("aliases", []):
            if alias["namespace"] == "partition-local":
                k.aliases[alias["value"]] = n["id"]
        if kind in RENDERABLE:
            k.assets[n["id"]] = {"id": n["id"], "level": kind, "title": n["title"], "intent": ux.get("intent"),
                                 "code": ux.get("layers", {}).get("code"), "requiredStates": ux.get("requiredStates", []),
                                 "stateProps": ux.get("stateProps", {}),
                                 "conditions": ux.get("conditions", []), "bindings": ux.get("dataBindings", []),
                                 "composes": [], "reviewState": n["reviewState"]}
        elif kind == "PageTemplate":
            k.templates[n["id"]] = {"id": n["id"], "title": n["title"], "slots": ux.get("slots", {}),
                                    "code": ux.get("layers", {}).get("code"), "reviewState": n["reviewState"]}
        elif kind == "Screen":
            k.screens[n["id"]] = {"id": n["id"], "title": n["title"], "pageId": props.get("pageId", n["id"]),
                                  "templateId": None, "assets": [], "procedureId": None,
                                  "reviewState": n["reviewState"]}
        elif kind == "Procedure":
            k.procedures[n["id"]] = {"id": n["id"], "title": n["title"], "entry": None, "screens": [], "transitions": []}
            entries[n["id"]] = props.get("entryScreenId")
        elif kind == "PolicyRule":
            k.rules[n["id"]] = {"id": n["id"], "ruleId": props.get("ruleId", n["id"]),
                                "statement": props.get("statement", n["title"]), "required": bool(props.get("required")),
                                "severity": props.get("severity", "major"), "targets": [], "citation": props.get("citation"),
                                "appliesWhen": props.get("appliesWhen"), "reviewState": n["reviewState"],
                                "revision": n.get("revision"), "contentHash": n.get("contentHash")}
        elif kind == "Foundation" and props.get("token"):
            k.tokens[props.get("name", n["id"])] = props["token"]
        for ref in references(ux) if ux else []:
            if ref not in nodes:
                k.unresolved.append({"nodeId": n["id"], "reference": ref})
    by_type = lambda t: [e for e in edges if e["type"] == t]  # noqa: E731
    for e in by_type("PART_OF"):
        s, p = e["src"]["id"], e["dst"]["id"]
        if s in k.screens and p in k.procedures and k.screens[s]["procedureId"] in (None, p):
            k.screens[s]["procedureId"] = p
            if s not in k.procedures[p]["screens"]:
                k.procedures[p]["screens"].append(s)
    for e in by_type("COMPOSES"):
        s, d = e["src"]["id"], e["dst"]["id"]
        if s in k.screens and d in k.templates:
            k.screens[s]["templateId"] = d
        elif s in k.screens and d in k.assets:
            k.screens[s]["assets"].append(d)
        elif s in k.assets and d in k.assets:
            k.assets[s]["composes"].append(d)
    for e in by_type("GOVERNED_BY"):
        if e["dst"]["id"] in k.rules:
            k.rules[e["dst"]["id"]]["targets"].append(e["src"]["id"])
    for e in sorted(by_type("NEXT"), key=lambda x: x["id"]):
        s, d = e["src"]["id"], e["dst"]["id"]
        proc = k.screens.get(s, {}).get("procedureId")
        if proc and k.screens.get(d, {}).get("procedureId") == proc:
            props = e.get("properties", {})
            k.procedures[proc]["transitions"].append({"id": e["id"], "src": s, "dst": d,
                                                      "conditionId": props.get("conditionId"),
                                                      "condition": props.get("condition"),
                                                      "navigation": props.get("navigation", "forward"),
                                                      "retains": props.get("retains")})
    for pid, proc in k.procedures.items():
        proc["screens"] = _ordered(proc, k, entries.get(pid))
    return k


def _ordered(proc, k, declared):
    """Topological walk over forward edges from the entry; back/cancel edges are user actions (review round 11, AE2)."""
    members = proc["screens"]
    moves = [t for t in proc["transitions"] if t["navigation"] == "forward"]
    if declared is not None:
        entries = [declared] if declared in members else []
    else:
        incoming = {t["dst"] for t in moves}
        entries = [s for s in members if s not in incoming]
    if len(entries) != 1:
        _add_unknown(k, "ambiguous-entry")
    proc["entry"] = entries[0] if len(entries) == 1 else None
    order, queue = [], entries[:1]
    while queue:
        cursor = queue.pop(0)
        if cursor in order:
            continue
        order.append(cursor)
        outs = sorted((t for t in moves if t["src"] == cursor), key=lambda t: (t["condition"] is not None, t["id"]))
        queue += [t["dst"] for t in outs]
    return order + [s for s in members if s not in order]
