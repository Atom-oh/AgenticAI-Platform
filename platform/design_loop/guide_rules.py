"""Admitted guideline derivative pages -> cited PolicyRule candidates (engine plan, Task E6; O-02, O-04).

Inputs are B0-admitted derivative pages `{admissionId, derivativeHash, sourceRef, page, text}`, never
`guidelines.context_for` and never an original. The derivative text is what the model sees and what every quote
is checked against. Nothing here confers review authority: PolicyRule approval stays with planner/owner
(`ontology_store.review_node`), and the publishing producer is decided in C through the ledger completion path.
"""
from __future__ import annotations

import json
import re

from workspace import ontology_schema as schema
from workspace.ontology_ux import parse_when

from .model_call import call

PROMPT_VERSION = "guide-rules-1"
MAX_RULES, MAX_STATEMENT = 200, 500
SEVERITIES = frozenset({"critical", "major", "minor"})
_SYSTEM = (
    "You extract UX policy rules from admitted guideline pages. Reply with JSON only: "
    '{"rules": [{"statement": str, "required": bool, "severity": "critical|major|minor", '
    '"appliesTo": [asset id], "appliesWhen": optional "cond:<id> & !cond:<id>", '
    '"citation": {"sourceId": str, "page": int, "quote": str}}]}. '
    "Every quote must be copied verbatim from the cited page. Use only the listed asset ids and condition ids."
)


def _norm(text):
    return re.sub(r"\s+", " ", text).strip()


def cited_pages(pages):
    """The single `(sourceId, page)` key shape used by extraction and by citation verification (review round 8, AB4)."""
    out = {}
    for p in pages:
        page = p.get("page")
        if type(page) is not int or page < 1:
            raise ValueError("Admitted pages carry a positive (logical) page number")
        schema._hash(p.get("derivativeHash"))
        if not isinstance(p.get("text"), str) or not isinstance(p.get("admissionId"), str) or not p["admissionId"]:
            raise ValueError("Admitted derivative page shape")
        key = (p["sourceRef"]["sourceId"], page)
        if key in out:
            raise ValueError("Ambiguous admitted page key")
        out[key] = p
    return out


def _prompt(pages, asset_ids, condition_ids):
    body = [f"[page sourceId={p['sourceRef']['sourceId']} page={p['page']}]\n{_norm(p['text'])}" for p in pages]
    return "\n\n".join([f"Asset ids: {', '.join(asset_ids) or '(none)'}",
                        f"Condition ids: {', '.join(condition_ids) or '(none)'}", *body])


def _valid_fields(r):
    return (isinstance(r, dict) and isinstance(r.get("statement"), str) and r["statement"].strip()
            and len(r["statement"]) <= MAX_STATEMENT and type(r.get("required")) is bool
            and r.get("severity") in SEVERITIES and isinstance(r.get("appliesTo", []), list)
            and all(isinstance(a, str) for a in r.get("appliesTo", []))
            and isinstance(r.get("citation"), dict))


def extract_rules(pages, deps, *, asset_ids, model, prompt_version=PROMPT_VERSION, condition_ids=()):
    keyed = cited_pages(pages)
    allowed, conditions = list(asset_ids), set(condition_ids)
    text, blocked = call(deps, _SYSTEM, _prompt(pages, allowed, sorted(conditions)))
    if blocked:
        return {"rules": [], "rejected": [], "blocked": blocked}
    try:
        proposed = json.loads(text)["rules"]
        if not isinstance(proposed, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return {"rules": [], "rejected": [{"reason": "parse-failed"}]}
    rules, rejected, seen = [], [], set()
    for index, r in enumerate(proposed):
        if len(rules) >= MAX_RULES:
            rejected.append({"index": index, "reason": "rule-limit"})
            continue
        if not _valid_fields(r):
            rejected.append({"index": index, "reason": "bad-field"})
            continue
        cite = r["citation"]
        page = keyed.get((cite.get("sourceId"), cite.get("page")))
        quote = _norm(cite["quote"]) if isinstance(cite.get("quote"), str) else ""
        if page is None or not quote or quote not in _norm(page["text"]):
            rejected.append({"index": index, "reason": "quote-not-found"})
            continue
        when = r.get("appliesWhen")
        if when is not None:
            try:
                terms = parse_when(when)
            except ValueError:
                terms = None
            if terms is None or any(cid not in conditions for _, cid in terms):
                rejected.append({"index": index, "reason": "applies-when"})
                continue
        statement = _norm(r["statement"])
        rule_id = "rule-" + schema.digest([page["sourceRef"]["sourceId"], page["page"], statement])[:24]
        if rule_id in seen:
            rejected.append({"index": index, "reason": "duplicate-rule", "ruleId": rule_id})
            continue
        seen.add(rule_id)
        targets = []
        for target in r.get("appliesTo", []):
            if target in allowed and target not in targets:
                targets.append(target)
            elif target not in allowed:
                rejected.append({"reason": "unknown-target", "ruleId": rule_id, "target": target})
        out = {"ruleId": rule_id, "statement": statement, "required": r["required"], "severity": r["severity"],
               "appliesTo": targets,
               "citation": {"sourceKind": page["sourceRef"]["sourceKind"], "sourceId": page["sourceRef"]["sourceId"],
                            "page": page["page"], "quote": quote, "derivativeHash": page["derivativeHash"]},
               "extraction": {"method": "model", "model": model, "promptVersion": prompt_version,
                              "admissionId": page["admissionId"]}}
        if when is not None:
            out["appliesWhen"] = when
        rules.append(out)
    return {"rules": rules, "rejected": rejected}


def rule_graph(rules, *, project_id, source_ref_for, revisions):
    """PolicyRule candidates plus `asset GOVERNED_BY rule` edges; endpoint revisions come from the live snapshot."""
    nodes, edges, rejected = [], [], []
    for r in rules:
        ref = schema.source_ref(source_ref_for(r["citation"]))
        props = {"ruleId": r["ruleId"], "statement": r["statement"], "required": r["required"],
                 "severity": r["severity"], "guidelineId": r["citation"]["sourceId"],
                 "citation": {k: v for k, v in r["citation"].items() if k != "sourceId"},
                 "extraction": dict(r["extraction"])}
        if r.get("appliesWhen") is not None:
            props["appliesWhen"] = r["appliesWhen"]          # carried into the node (review round 4, N14)
        node = schema.seal({"id": r["ruleId"], "scope": {"kind": "project", "projectId": project_id},
                            "type": "PolicyRule", "title": r["statement"][:300], "revision": 1,
                            "sourceRefs": [ref], "provenance": "model-inferred", "reviewState": "candidate",
                            "tombstone": False, "properties": props})
        nodes.append(schema.validate_node(node))
        for asset in r["appliesTo"]:
            revision = revisions.get(asset)
            if type(revision) is not int or revision < 1:
                rejected.append({"reason": "unknown-target", "ruleId": r["ruleId"], "target": asset})
                continue
            edges.append(schema.validate_edge(schema.seal({
                "id": "gov-" + schema.digest([asset, r["ruleId"]])[:24], "type": "GOVERNED_BY",
                "src": {"id": asset, "revision": revision}, "dst": {"id": r["ruleId"], "revision": 1},
                "sourceRefs": [ref], "provenance": "model-inferred", "reviewState": "candidate", "tombstone": False})))
    return {"nodes": nodes, "edges": edges, "rejected": rejected}
