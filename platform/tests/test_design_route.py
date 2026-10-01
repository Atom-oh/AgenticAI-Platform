# platform/tests/test_design_route.py
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge, sealed_graph
from design_loop.flow import build_flow
from design_loop.knowledge import from_snapshot
from design_loop.route import classify, strategy

K = knowledge()
FLOW = build_flow(K, "savings-signup")


def test_seed_screen_is_structured_and_needs_no_model():
    out = classify(K, FLOW, "amount")
    assert out == {"route": "structured", "score": 3, "missing": {}}
    assert strategy(out["route"]) == {"mode": "fill", "modelCalls": 0}
    assert all(classify(K, FLOW, s)["route"] == "structured" for s in FLOW["screens"])


def test_missing_code_layer_makes_the_screen_hybrid():
    k = copy.deepcopy(K)
    k.assets["amount-field"]["code"] = None
    out = classify(k, FLOW, "amount")
    assert out["route"] == "hybrid" and out["score"] == 2 and out["missing"] == {"assets": ["amount-field"]}
    assert strategy("hybrid")["mode"] == "adapt" and strategy("hybrid")["modelCalls"] >= 1


def test_screen_without_template_is_unstructured():
    k = copy.deepcopy(K)
    k.screens["amount"]["templateId"] = None
    out = classify(k, FLOW, "amount")
    assert out["route"] == "unstructured" and out["missing"]["template"] == [None]
    assert strategy("unstructured")["mode"] == "compose"


def test_unapproved_members_lower_the_score():
    g = sealed_graph()
    for n in g["nodes"]:
        if n["id"] in ("single-task", "r-terms-required", "terms-consent"):
            n["reviewState"] = "candidate"
    k = from_snapshot(g, include_candidates=True)
    out = classify(k, build_flow(k, "savings-signup"), "terms")
    assert out["score"] == 0 and out["route"] == "unstructured"
    assert out["missing"] == {"template": ["single-task"], "assets": ["terms-consent"], "rules": ["r-terms-required"]}


def test_nested_and_conditional_assets_count():
    k = copy.deepcopy(K)
    k.assets["branch-select"]["code"] = None                       # included only through auto-transfer's condition
    assert classify(k, FLOW, "evidence-auto")["missing"] == {"assets": ["branch-select"]}
    k = copy.deepcopy(K)
    del k.assets["amount-input"]                                    # nested under amount-field, unknown -> missing
    assert classify(k, FLOW, "amount")["missing"] == {"assets": ["amount-input"]}


def test_unknown_inputs_fail_closed():
    with pytest.raises(ValueError):
        classify(K, FLOW, "ghost")
    with pytest.raises(ValueError):
        strategy("freeform")
