# platform/tests/test_ontology_ux.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ontology_schema import node  # noqa: E402
from workspace import ontology_schema as schema  # noqa: E402
from workspace.ontology_ux import references, rewrite, validate_ux_model  # noqa: E402

CODE = {"importPath": "@studio/approved-ui", "exportName": "Text",
        "props": {"tone": {"type": "enum", "required": False, "values": ["default", "muted"]}},
        "childrenProp": "children"}


def test_valid_models_per_level():
    assert validate_ux_model({"intent": "금리 강조", "layers": {"code": CODE}}, "Atom")
    assert validate_ux_model({"layers": {"code": {**CODE, "exportName": "Panel"}}}, "Pattern")
    tpl = {"slots": {"body": {"required": True, "allowed": ["org-1"]}},
           "layers": {"code": {"importPath": "@studio/approved-ui", "exportName": "Screen",
                               "props": {"body": {"type": "node", "required": True}}, "childrenProp": None}}}
    assert validate_ux_model(tpl, "PageTemplate")


@pytest.mark.parametrize("value,kind", [
    ({"requiredStates": ["sleepy"]}, "Organism"),
    ({"slots": {"body": {"required": True, "allowed": ["a"]}}}, "Organism"),
    ({"slots": {"body": {"required": True, "allowed": []}}}, "PageTemplate"),
    ({"conditions": [{"id": "c1", "when": "x", "effect": "include", "target": "t"}]}, "Organism"),
    ({"conditions": [{"id": "c1", "when": "cond:a", "effect": "state", "target": "t"}]}, "Organism"),
    ({"layers": {"code": {**CODE, "props": {"tone": {"type": "enum", "required": False}}}}}, "Atom"),
    ({"layers": {"gui": {"snapshotHash": "x", "width": 1, "height": 1}}}, "Atom"),
])
def test_invalid_models_rejected(value, kind):
    with pytest.raises(ValueError):
        validate_ux_model(value, kind)


def test_ux_model_not_allowed_on_foundation_screen_or_procedure():
    for kind in ("Screen", "Procedure"):
        with pytest.raises(ValueError):
            schema.validate_node(node("n1", kind=kind, properties={"uxModel": {"intent": "x"}}))
    with pytest.raises(ValueError):   # Foundation: layers/changeReason only
        schema.validate_node(node("f1", kind="Foundation", subtype="color", properties={"uxModel": {"intent": "x"}}))
    ok = {"layers": {"wireframe": {"blocks": ["swatch"]}}}
    assert schema.validate_node(node("f2", kind="Foundation", subtype="color", properties={"uxModel": ok}))


def test_references_and_rewrite():
    value = {"conditions": [{"id": "c1", "when": "cond:auto", "effect": "include", "target": "branch"}],
             "slots": {"body": {"required": True, "allowed": ["summary", "branch"]}}}
    assert sorted(references(value)) == ["branch", "branch", "summary"]
    out = rewrite(value, {"branch": "node-b", "summary": "node-s"})
    assert out["conditions"][0]["target"] == "node-b" and out["slots"]["body"]["allowed"] == ["node-s", "node-b"]


def test_policy_rule_citation_fields():
    rule = node("r1", kind="PolicyRule", properties={
        "ruleId": "r1", "statement": "약관 동의 단계 필수", "required": True, "severity": "critical",
        "citation": {"sourceKind": "document-revision", "page": 3, "quote": "약관 동의", "derivativeHash": "a" * 64},
        "extraction": {"method": "model", "model": "m", "promptVersion": "1", "admissionId": "adm-1"}})
    assert schema.validate_node(rule)["properties"]["severity"] == "critical"
    bad = node("r2", kind="PolicyRule", properties={"ruleId": "r2", "severity": "urgent"})
    with pytest.raises(ValueError):
        schema.validate_node(bad)


@pytest.mark.parametrize("properties", [
    {"ruleId": "r3", "appliesWhen": "eligible"},
    {"ruleId": "r3", "citation": {"sourceKind": "asset", "page": 1, "quote": "q", "derivativeHash": "a" * 64}},
    {"ruleId": "r3", "citation": {"sourceKind": "document-revision", "page": 0, "quote": "q", "derivativeHash": "a" * 64}},
    {"ruleId": "r3", "extraction": {"method": "manual", "model": "m", "promptVersion": "1", "admissionId": "a"}},
])
def test_policy_rule_structured_fields_fail_closed(properties):
    with pytest.raises(ValueError):
        schema.validate_node(node("r3", kind="PolicyRule", properties=properties))


def test_deep_ux_model_fits_the_canonical_nesting_limit():
    """graph → nodes → node → properties → uxModel → layers → code → props → prop → values (review round 4, X1)."""
    ux = {"layers": {"code": CODE}}
    value = {"schemaVersion": 1, "projectId": "project-1",
             "nodes": [node("atom-1", properties={"uxModel": ux})], "edges": []}
    assert schema.validate_graph(value)["nodes"][0]["properties"]["uxModel"] == ux
    assert schema.canonical({"part": {"graph": value}})
