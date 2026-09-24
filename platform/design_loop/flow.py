"""Flow model, independent expected graph and case traversal (engine plan, Task E8; U-01, U-02; Codex #9).

`build_flow` reads the ontology procedure. `expected` is derived only from the PRD conditions and the approved rules
(never from the flow's transitions), so a flow that omits a required branch cannot also redefine what is required.
Back and cancel edges are user actions, not forward moves: traversal and transition usage consider forward edges.
"""
from __future__ import annotations

import itertools

from workspace import ontology_ux
from workspace.ontology_schema import digest

ENDS = ("terminal", "dead-end", "loop", "ambiguous")
_END_CODE = {"dead-end": "dead-end", "loop": "loop", "ambiguous": "ambiguous-branch"}


def build_flow(k, procedure_id):
    proc = k.procedures.get(procedure_id)
    if proc is None:
        raise ValueError("Unknown procedure")
    members = list(proc["screens"])
    transitions = [{"id": t["id"], "src": t["src"], "dst": t["dst"], "when": t.get("condition"),
                    "navigation": t.get("navigation") or "forward"}
                   for t in proc["transitions"] if t["src"] in members and t["dst"] in members]
    forward = [t for t in transitions if t["navigation"] == "forward"]
    sources = {t["src"] for t in forward}
    return {"procedureId": procedure_id, "entry": proc.get("entry"), "screens": members, "transitions": transitions,
            "terminals": [s for s in members if s not in sources]}


def evaluate(when, case):
    return ontology_ux.evaluate(when, case)


def _condition_id(entry):
    return entry.get("conditionId") or entry.get("id")


def _contains(k, screen_id):
    """Assets a screen shows: its direct assets and their COMPOSES closure."""
    seen, stack = set(), list(k.screens.get(screen_id, {}).get("assets", []))
    while stack:
        a = stack.pop()
        if a in seen:
            continue
        seen.add(a)
        stack += k.assets.get(a, {}).get("composes", [])
    return seen


def expected(prd, k):
    conditions, requirements = [], [{"kind": "reach-terminal", "case": "*"}]
    elig = prd.get("eligibility") if isinstance(prd.get("eligibility"), dict) else None
    eligible = elig.get("conditionId") if elig else None
    if eligible:
        conditions.append(eligible)
        requirements.append({"kind": "terminal-state", "when": f"!cond:{eligible}", "state": "ineligible"})
    for p in prd.get("preferential") or []:
        cid = _condition_id(p)
        if cid not in conditions:
            conditions.append(cid)
        if p.get("evidence") == "input":
            term = f"cond:{cid}"
            assets = sorted(a for a, v in k.assets.items()
                            if any(c.get("when") == term and c.get("effect") == "include" for c in v.get("conditions", [])))
            requirements.append({"kind": "visit-when", "when": f"cond:{eligible} & {term}" if eligible else term,
                                 "eligibleWhen": f"cond:{eligible}" if eligible else None, "condition": cid,
                                 "asset": assets[0] if assets else None})
    for rid in sorted(k.rules):
        rule = k.rules[rid]
        if not rule["required"] or rule.get("reviewState", "approved") != "approved":
            continue
        if any(t in k.screens or t in k.assets for t in rule["targets"]):
            requirements.append({"kind": "rule-screen", "rule": rid, "when": rule.get("appliesWhen")})
    for n, notice in enumerate(prd.get("notices") or [], 1):
        requirements.append({"kind": "notice", "id": notice.get("id"), "path": f"product.notice.notice-{n}"})
    return {"conditions": conditions, "requirements": requirements}


def enumerate_cases(conditions, limit=64):
    names = list(dict.fromkeys(conditions))
    cases = []
    for values in itertools.product((True, False), repeat=len(names)):
        if len(cases) >= limit:
            return {"cases": cases, "truncated": True}
        cases.append(dict(zip(names, values)))
    return {"cases": cases, "truncated": False}


def traverse(flow, case, *, max_steps=50):
    moves = [t for t in flow["transitions"] if t.get("navigation", "forward") == "forward"]
    terminals, cursor = set(flow["terminals"]), flow.get("entry")
    if cursor is None:
        return {"path": [], "transitions": [], "end": "dead-end"}
    path, taken = [cursor], []
    for _ in range(max_steps):
        if cursor in terminals:
            return {"path": path, "transitions": taken, "end": "terminal"}
        try:
            enabled = [t for t in moves if t["src"] == cursor and evaluate(t["when"], case)]
        except ValueError:
            return {"path": path, "transitions": taken, "end": "ambiguous"}     # undecidable branch fails closed
        if not enabled:
            return {"path": path, "transitions": taken, "end": "dead-end"}
        if len(enabled) > 1:
            return {"path": path, "transitions": taken, "end": "ambiguous"}
        taken.append(enabled[0]["id"])
        cursor = enabled[0]["dst"]
        if cursor in path:
            return {"path": path + [cursor], "transitions": taken, "end": "loop"}
        path.append(cursor)
    return {"path": path, "transitions": taken, "end": "loop"}


def _holds(when, case):
    try:
        return evaluate(when, case)
    except ValueError:
        return None


def flow_issues(flow, expectation, k):
    issues, seen = [], set()

    def add(code, case=None, detail=None):
        item = {"code": code, **({"case": case} if case is not None else {}), "detail": detail}
        key = digest(item)
        if key not in seen:
            seen.add(key)
            issues.append(item)

    enumerated = enumerate_cases(expectation["conditions"])
    if enumerated["truncated"]:
        add("case-enumeration-truncated", detail={"conditions": len(expectation["conditions"])})
    visited, used = set(), set()
    for case in enumerated["cases"]:
        run = traverse(flow, case)
        visited.update(run["path"])
        used.update(run["transitions"])
        shown = set().union(*[_contains(k, s) | {s} for s in run["path"]]) if run["path"] else set()
        for req in expectation["requirements"]:
            kind = req["kind"]
            if kind == "reach-terminal":
                if run["end"] != "terminal":
                    add(_END_CODE[run["end"]], case, {"at": run["path"][-1] if run["path"] else None})
            elif kind == "terminal-state":
                applies = _holds(req["when"], case)
                if applies is None:
                    add("terminal-state-missing", case, {"when": req["when"], "reason": "unknown-condition"})
                elif applies and not _ends_in_state(run, req, k):
                    add("terminal-state-missing", case, {"state": req["state"]})
            elif kind == "visit-when":
                eligible = _holds(req.get("eligibleWhen"), case)
                wanted = case.get(req["condition"])
                if eligible is None or type(wanted) is not bool:
                    add("conditional-visit-missing", case, {"condition": req["condition"], "reason": "unknown-condition"})
                elif eligible:
                    present = req["asset"] is not None and req["asset"] in shown
                    if wanted and not present:
                        add("conditional-visit-missing", case, {"condition": req["condition"], "asset": req["asset"]})
                    elif not wanted and present:
                        add("conditional-visit-leak", case, {"condition": req["condition"], "asset": req["asset"]})
            elif kind == "rule-screen":
                applies = _holds(req["when"], case)
                targets = set(k.rules.get(req["rule"], {}).get("targets", []))
                if applies is None or applies and not targets & shown:
                    add("rule-screen-missing", case, {"rule": req["rule"]})
    for s in flow["screens"]:
        if s not in visited:
            add("unreachable-screen", detail={"screen": s})
    for t in flow["transitions"]:
        if t.get("navigation", "forward") == "forward" and t["id"] not in used:
            add("unused-transition", detail={"transition": t["id"]})
    return issues


def _ends_in_state(run, req, k):
    if run["end"] != "terminal" or not run["path"]:
        return False
    last = run["path"][-1]
    if any(req["state"] in k.assets.get(a, {}).get("requiredStates", []) for a in _contains(k, last)):
        return True
    shown = _contains(k, last) | {last}
    return any(r["required"] and r.get("reviewState", "approved") == "approved" and r.get("appliesWhen") == req["when"]
               and set(r["targets"]) & shown for r in k.rules.values())


def mermaid(flow, case=None):
    chosen = set(traverse(flow, case)["transitions"]) if case is not None else set()
    lines = ["flowchart TD"]
    for t in flow["transitions"]:
        if t["id"] in chosen:
            lines.append(f"  {t['src']} ==> {t['dst']}")
        elif t["when"]:
            label = t["when"].replace('"', "'")
            lines.append(f'  {t["src"]} -->|"{label}"| {t["dst"]}')
        elif t.get("navigation", "forward") != "forward":
            lines.append(f"  {t['src']} -.-> {t['dst']}")
        else:
            lines.append(f"  {t['src']} --> {t['dst']}")
    return "\n".join(lines) + "\n"
