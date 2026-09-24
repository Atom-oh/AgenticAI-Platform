# platform/tests/test_design_guide_rules.py
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_loop.guide_rules import cited_pages, extract_rules, rule_graph
from workspace import ontology_schema as schema
from test_workbench_core import wb  # noqa: F401,E402  (store fixture)

REF = {"sourceKind": "document-revision", "sourceId": "guide-1", "revision": "3", "sha256": "c" * 64, "audienceRevision": "2"}
PAGES = [{"admissionId": "adm-1", "derivativeHash": "d" * 64, "sourceRef": REF, "page": 12,
          "text": "가입 절차에는 반드시 약관 동의 단계를 둔다. 자격이 없으면 사유와 대안을 보여준다."}]


def gen(payload):
    # The verify-only normalization hook is mandatory (Global Constraints); the fake passes admitted text.
    return {"generate": lambda s, u, t: json.dumps(payload, ensure_ascii=False), "normalize": lambda text: text}


def rule(**over):
    base = {"statement": "약관 동의 단계 필수", "required": True, "severity": "critical", "appliesTo": ["terms-consent"],
            "citation": {"sourceId": "guide-1", "page": 12, "quote": "반드시 약관 동의 단계를 둔다"}}
    base.update(over)
    return base


def test_single_key_shape():
    assert list(cited_pages(PAGES)) == [("guide-1", 12)]


def test_verified_quote_keeps_model_provenance():
    out = extract_rules(PAGES, gen({"rules": [rule()]}), asset_ids=["terms-consent"], model="model-a")
    r = out["rules"][0]
    assert r["extraction"] == {"method": "model", "model": "model-a", "promptVersion": "guide-rules-1", "admissionId": "adm-1"}
    assert r["citation"]["derivativeHash"] == "d" * 64 and r["citation"]["sourceKind"] == "document-revision"


def test_unverified_quote_rejected():
    out = extract_rules(PAGES, gen({"rules": [rule(citation={"sourceId": "guide-1", "page": 12, "quote": "배너는 세 줄"})]}),
                        asset_ids=[], model="m")
    assert not out["rules"] and out["rejected"][0]["reason"] == "quote-not-found"


def test_rule_graph_uses_live_revisions_and_is_contract_valid():
    out = extract_rules(PAGES, gen({"rules": [rule()]}), asset_ids=["terms-consent"], model="m")
    g = rule_graph(out["rules"], project_id="p1", source_ref_for=lambda c: REF, revisions={"terms-consent": 4})
    assert g["edges"][0]["src"] == {"id": "terms-consent", "revision": 4}
    for n in g["nodes"]:
        schema.validate_node(n)


# ---- additional fail-closed cases -------------------------------------------------------------------------

def test_quote_match_normalizes_whitespace_and_rule_id_is_deterministic():
    spaced = rule(citation={"sourceId": "guide-1", "page": 12, "quote": "반드시  약관\n동의 단계를 둔다"})
    a = extract_rules(PAGES, gen({"rules": [spaced]}), asset_ids=["terms-consent"], model="m")["rules"][0]
    b = extract_rules(PAGES, gen({"rules": [rule()]}), asset_ids=["terms-consent"], model="m")["rules"][0]
    assert a["ruleId"] == b["ruleId"] == "rule-" + schema.digest(["guide-1", 12, "약관 동의 단계 필수"])[:24]
    assert a["citation"]["quote"] == "반드시 약관 동의 단계를 둔다"


def test_unknown_page_and_bad_fields_are_rejected():
    wrong_page = rule(citation={"sourceId": "guide-1", "page": 13, "quote": "반드시 약관 동의 단계를 둔다"})
    long = rule(statement="가" * 501)
    bad_sev = rule(severity="blocker")
    out = extract_rules(PAGES, gen({"rules": [wrong_page, long, bad_sev]}), asset_ids=["terms-consent"], model="m")
    assert not out["rules"]
    assert [r["reason"] for r in out["rejected"]] == ["quote-not-found", "bad-field", "bad-field"]


def test_rule_count_is_bounded():
    many = [rule(statement=f"규칙 {i}") for i in range(201)]
    out = extract_rules(PAGES, gen({"rules": many}), asset_ids=["terms-consent"], model="m")
    assert len(out["rules"]) == 200 and out["rejected"][-1]["reason"] == "rule-limit"


def test_applies_when_is_parsed_and_bound_to_prd_conditions():
    ok = rule(appliesWhen="cond:eligible")
    bad_grammar = rule(statement="b", appliesWhen="eligible or not")
    unknown = rule(statement="c", appliesWhen="cond:vip")
    out = extract_rules(PAGES, gen({"rules": [ok, bad_grammar, unknown]}), asset_ids=["terms-consent"], model="m",
                        condition_ids=["eligible"])
    assert [r["appliesWhen"] for r in out["rules"]] == ["cond:eligible"]
    assert [r["reason"] for r in out["rejected"]] == ["applies-when", "applies-when"]
    # without declared PRD conditions nothing can be conditional (fail closed)
    none = extract_rules(PAGES, gen({"rules": [ok]}), asset_ids=["terms-consent"], model="m")
    assert not none["rules"] and none["rejected"][0]["reason"] == "applies-when"


def test_targets_outside_the_asset_list_and_unknown_live_assets_are_rejected():
    out = extract_rules(PAGES, gen({"rules": [rule(appliesTo=["terms-consent", "ghost"])]}), asset_ids=["terms-consent"],
                        model="m")
    assert out["rules"][0]["appliesTo"] == ["terms-consent"]
    assert {"reason": "unknown-target", "ruleId": out["rules"][0]["ruleId"], "target": "ghost"} in out["rejected"]
    g = rule_graph(out["rules"], project_id="p1", source_ref_for=lambda c: REF, revisions={})
    assert g["edges"] == [] and g["rejected"][0]["reason"] == "unknown-target"
    node = g["nodes"][0]
    assert node["provenance"] == "model-inferred" and node["reviewState"] == "candidate"
    assert node["properties"]["guidelineId"] == "guide-1" and "sourceId" not in node["properties"]["citation"]


def test_missing_callables_block_before_any_model_call():
    called = []
    deps = {"generate": lambda s, u, t: called.append(1) or "{}"}
    out = extract_rules(PAGES, deps, asset_ids=[], model="m")
    assert out["blocked"] == "normalization-unavailable" and not called and not out["rules"]
    assert extract_rules(PAGES, {"normalize": lambda t: t}, asset_ids=[], model="m")["blocked"] == "model-unavailable"

    def refuse(text):
        raise ValueError("identifier")
    blocked = extract_rules(PAGES, {**gen({"rules": [rule()]}), "normalize": refuse}, asset_ids=[], model="m")
    assert blocked["blocked"] == "normalization-blocked" and not blocked["rules"]


def test_parse_failure_is_explicit():
    deps = {"generate": lambda s, u, t: "not json", "normalize": lambda t: t}
    out = extract_rules(PAGES, deps, asset_ids=[], model="m")
    assert out["rules"] == [] and out["rejected"] == [{"reason": "parse-failed"}]


def test_cited_pages_rejects_ambiguous_and_non_positive_pages():
    with pytest.raises(ValueError):
        cited_pages(PAGES + PAGES)
    with pytest.raises(ValueError):
        cited_pages([{**PAGES[0], "page": 0}])


def test_conditional_rule_keeps_applies_when_after_publish(wb):
    """review round 4, N14: appliesWhen survives real publication next to a live GOVERNED_BY target."""
    from design_fixtures import raw_graph_with, seed_document_ref
    from test_ontology_sources import context
    from workspace.ontology_store import Ontology
    ontology = Ontology(context(wb))
    ref = seed_document_ref(wb)
    seed = ontology.publish_candidate("design-seed", raw_graph_with(ref, project_id=wb.project["id"]),
                                      expected_generation=None, request_id="seed-1")
    target = seed["identities"]["terms-consent"]
    live = ontology.read([target])["nodes"][0]
    pages = [{**PAGES[0], "sourceRef": ref, "page": 1}]
    cond = rule(appliesTo=[target], appliesWhen="cond:eligible",
                citation={"sourceId": ref["sourceId"], "page": 1, "quote": "반드시 약관 동의 단계를 둔다"})
    out = extract_rules(pages, gen({"rules": [cond]}), asset_ids=[target], model="m", condition_ids=["eligible"])
    g = rule_graph(out["rules"], project_id=wb.project["id"], source_ref_for=lambda c: ref,
                   revisions={target: live["revision"]})
    published = ontology.publish_candidate("guide-rules", {"schemaVersion": 1, "projectId": wb.project["id"],
                                                           "nodes": g["nodes"], "edges": g["edges"]},
                                           expected_generation=seed["generation"], request_id="rules-1")
    rid = published["identities"][out["rules"][0]["ruleId"]]
    view = ontology.read([rid])
    stored = view["nodes"][0]
    assert stored["properties"]["appliesWhen"] == "cond:eligible"
    assert stored["properties"]["citation"]["page"] == 1
