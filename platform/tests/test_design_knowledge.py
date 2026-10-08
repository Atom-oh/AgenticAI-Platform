# platform/tests/test_design_knowledge.py
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge, sealed_graph
from workspace import ontology_schema as schema
from design_loop.knowledge import from_snapshot


def test_seed_is_contract_valid():
    g = sealed_graph()
    schema.validate_graph({k: g[k] for k in ("schemaVersion", "projectId", "nodes", "edges")})


def test_membership_is_built_before_transitions_regardless_of_edge_order():
    g = sealed_graph()
    g["edges"] = sorted(g["edges"], key=lambda e: e["type"] != "NEXT")   # NEXT edges first
    k = from_snapshot(g)
    assert k.procedures["savings-signup"]["screens"][:3] == ["intro", "eligibility", "terms"]
    assert len(k.procedures["savings-signup"]["transitions"]) == 9


def test_template_is_resolved_through_composes_not_implements():
    k = knowledge()
    assert k.screens["amount"]["templateId"] == "single-task"
    assert "amount-field" in k.screens["amount"]["assets"]


def test_truncation_and_unresolved_references_make_knowledge_incomplete():
    g = sealed_graph(); g["coverage"]["truncated"] = True
    assert not from_snapshot(g).complete
    g = sealed_graph()
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "branch-select"]
    g["edges"] = [e for e in g["edges"] if "branch-select" not in (e["src"]["id"], e["dst"]["id"])]
    k = from_snapshot(g)
    assert not k.complete and {"nodeId": "auto-transfer", "reference": "branch-select"} in k.unresolved


def test_candidate_dependency_of_an_approved_node_blocks_completeness():
    g = sealed_graph()
    g["nodes"] = [schema.seal({**n, "reviewState": "candidate"}) if n["id"] == "consent-row" else n for n in g["nodes"]]
    k = from_snapshot(g)
    assert not k.complete and "consent-row" in k.coverage["unapproved"]


def test_candidate_member_screen_of_an_approved_procedure_blocks_completeness():
    g = sealed_graph()
    g["nodes"] = [schema.seal({**n, "reviewState": "candidate"}) if n["id"] == "confirm" else n for n in g["nodes"]]
    k = from_snapshot(g)
    assert not k.complete and "confirm" in k.coverage["unapproved"]


def test_candidates_excluded_by_default():
    k = knowledge(approved=False)
    assert not k.assets and not k.screens
    assert from_snapshot(sealed_graph(approved=False), include_candidates=True).assets


# --- Additional E4 interface cases (entry/navigation kinds, retired mappings, seed shape) ---

def _edge(identifier, src, dst, g, **props):
    rev = {n["id"]: n["revision"] for n in g["nodes"]}
    ref = g["nodes"][0]["sourceRefs"]
    return schema.seal({"id": identifier, "type": "NEXT", "src": {"id": src, "revision": rev[src]},
                        "dst": {"id": dst, "revision": rev[dst]}, "sourceRefs": ref, "provenance": "declared",
                        "reviewState": "candidate", "tombstone": False, **({"properties": props} if props else {})})


def _mini(entry=True, extra=()):
    """entry → form → done, plus a back edge form → entry."""
    g = sealed_graph()
    keep = {"intro", "amount", "done", "savings-signup", "single-task", "title", "product-summary", "cta-next",
            "amount-field", "amount-input", "body-text", "rate-summary", "next-button", "color-brand", "space-m",
            "step-bar", "eligibility-check", "terms-consent", "auto-transfer", "branch-select", "notice-alert",
            "consent-row", "consent-check"}
    nodes = [n for n in g["nodes"] if n["id"] in keep]
    if not entry:
        nodes = [schema.seal({**n, "properties": {}}) if n["id"] == "savings-signup" else n for n in nodes]
    g["nodes"] = nodes
    ids = {n["id"] for n in nodes}
    g["edges"] = [e for e in g["edges"] if e["type"] != "NEXT" and e["src"]["id"] in ids and e["dst"]["id"] in ids]
    g["edges"] = [e for e in g["edges"] if e["dst"]["id"] in ids]
    g["edges"] += [_edge("m1", "intro", "amount", g), _edge("m2", "amount", "done", g),
                   _edge("m3", "amount", "intro", g, navigation="back", retains="session.amount"), *extra]
    return g


def test_back_edges_do_not_create_ambiguous_entry():
    for entry in (True, False):
        k = from_snapshot(_mini(entry=entry))
        proc = k.procedures["savings-signup"]
        assert proc["entry"] == "intro" and proc["screens"] == ["intro", "amount", "done"]
        assert {t["id"]: t["navigation"] for t in proc["transitions"]} == {"m1": "forward", "m2": "forward", "m3": "back"}
        assert k.complete, k.coverage


def test_forward_cycle_without_declared_entry_is_ambiguous():
    g = _mini(entry=False)
    g["edges"] = [e for e in g["edges"] if e["id"] != "m3"] + [_edge("m4", "amount", "intro", g)]
    k = from_snapshot(g)
    assert "ambiguous-entry" in k.coverage["unknown"] and not k.complete


def test_declared_entry_must_be_a_member():
    g = sealed_graph()
    g["nodes"] = [schema.seal({**n, "properties": {"entryScreenId": "ghost"}}) if n["id"] == "savings-signup" else n
                  for n in g["nodes"]]
    k = from_snapshot(g)
    assert "ambiguous-entry" in k.coverage["unknown"] and not k.complete


def test_rejected_or_retired_member_blocks_instead_of_disappearing():
    for change in ({"reviewState": "rejected"}, {"reviewState": "deprecated", "tombstone": True}):
        g = sealed_graph()
        g["nodes"] = [schema.seal({**n, **change}) if n["id"] == "terms" else n for n in g["nodes"]]
        k = from_snapshot(g)
        assert not k.complete and "retired-or-rejected-mapping" in k.coverage["unknown"]


def test_seed_knowledge_is_complete_with_rules_tokens_and_aliases():
    k = knowledge()
    assert k.complete, (k.coverage, k.unresolved)
    assert k.procedures["savings-signup"]["entry"] == "intro"
    assert sorted(k.rules["r-terms-required"]["targets"]) == ["terms", "terms-consent"]
    assert k.rules["r-ineligible-reason"]["appliesWhen"] == "!cond:eligible"
    assert k.tokens["color-brand"] == "#1d4ed8"
    assert k.templates["single-task"]["slots"]["footer"]["allowed"] == ["cta-next"]
