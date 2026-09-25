# platform/tests/test_design_gui.py
"""Task E11: strategy-driven layout and state variants (G-02, G-03)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge, published_knowledge, raw_graph
from design_loop.composition import validate, walk
from design_loop.flow import build_flow, expected
from design_loop.gui import fill, generate_screen, generate_states, states_for
from design_loop.prd_extract import bindings
from design_loop.route import classify, strategy
from test_design_prd_extract import GOOD
from test_workbench_core import wb  # noqa: F401  (fixture)
from workspace.ontology_ux import validate_ux_model

K = knowledge()
FLOW = build_flow(K, "savings-signup")
VALUES = bindings(GOOD)
PATHS = frozenset(VALUES)
OK = lambda text: text  # noqa: E731  verify-only normalization fake


def refuse(*_):
    raise AssertionError("structured generation must not call a model")


def nodes(c):
    return [n for _, n, _ in walk(c)]


def test_fill_produces_a_composition_that_validates():
    c = fill(K, FLOW, "amount", VALUES)
    assert validate(c, K, flow=FLOW, binding_paths=PATHS) == []
    assert list(c["slots"]) == ["header", "body", "footer"]
    assert [n["asset"] for n in c["slots"]["body"]] == ["amount-field"]
    cta = next(n for n in nodes(c) if n["asset"] == "next-button")
    assert cta["on"] == {"click": "next"} and cta["props"]["label"] == "다음 버튼"   # copy from the asset title


def test_every_seed_screen_fills_validly_and_terminals_finish():
    for s in FLOW["screens"]:
        c = fill(K, FLOW, s, VALUES)
        assert validate(c, K, flow=FLOW, binding_paths=PATHS) == [], s
    for terminal in ("ineligible", "done"):
        cta = next(n for n in nodes(fill(K, FLOW, terminal, VALUES)) if n["asset"] == "next-button")
        assert cta["on"] == {"click": "finish"}                      # no successor is invented (Z4)


def test_fill_binds_required_props_and_marks_conditional_targets():
    intro = fill(K, FLOW, "intro", VALUES)
    summary = next(n for n in nodes(intro) if n["asset"] == "rate-summary")
    assert summary["bind"] == {"items": "product.summaryItems"} and "items" not in summary.get("props", {})
    evidence = fill(K, FLOW, "evidence-auto", VALUES)
    branch = next(n for n in nodes(evidence) if n["asset"] == "branch-select")
    assert branch["visibleWhen"] == {"when": "cond:autoTransfer", "negate": False}
    amount = fill(K, FLOW, "amount", VALUES)
    field = next(n for n in nodes(amount) if n["asset"] == "amount-input")
    assert "value" not in field.get("bind", {}) and "value" not in field.get("props", {})   # adapter-owned


def test_structured_variants_make_zero_model_calls():
    out = generate_screen("amount", K, FLOW, {"generate": refuse, "normalize": OK},
                          strategy=strategy("structured"), binding_paths=PATHS)
    assert out["modelCalls"] == 0 and [v["variant"] for v in out["variants"]] == ["a", "b", "c"]
    assert all(v["findings"] == [] for v in out["variants"])
    assert len({json.dumps(v["composition"]["slots"], sort_keys=True) for v in out["variants"]}) == 3
    gaps = [next(n for n in nodes(v["composition"]) if n["asset"] == "amount-field").get("props", {}).get("gap")
            for v in out["variants"]]
    assert gaps == [None, "2", "4"]                                   # Stack.gap variation axis, declared values
    assert generate_screen("amount", K, FLOW, {}, strategy=strategy("structured"), binding_paths=PATHS)["variants"]


def _unstructured():
    k = copy.deepcopy(K)
    k.screens["amount"]["templateId"] = None
    return k


def test_compose_retries_once_with_the_findings_and_keeps_both_attempts():
    k = _unstructured()
    good = fill(K, FLOW, "amount", VALUES)
    bad = copy.deepcopy(good)
    bad["slots"]["body"][0]["children"].append({"id": "x1", "asset": "next-button", "props": {"label": "다음"}})
    calls = []

    def gen(system, user, on_token):
        calls.append(user)
        return json.dumps(bad if len(calls) == 1 else good, ensure_ascii=False)

    out = generate_screen("amount", k, build_flow(k, "savings-signup"), {"generate": gen, "normalize": OK},
                          strategy=strategy(classify(k, FLOW, "amount")["route"]), binding_paths=PATHS,
                          variants=1, retries=1)
    assert out["mode"] == "compose" and len(calls) == 2
    variant = out["variants"][0]
    assert [a["attempt"] for a in variant["attempts"]] == [1, 2]
    assert {f["code"] for f in variant["attempts"][0]["findings"]} == {"not-composed"}
    assert variant["attempts"][1]["findings"] == [] and variant["findings"] == []
    assert "not-composed" in calls[1]                                  # findings are appended on retry


def test_compose_proposing_an_unknown_asset_is_a_new_asset_candidate():
    k = _unstructured()
    proposal = fill(K, FLOW, "amount", VALUES)
    proposal["slots"]["body"].append({"id": "x9", "asset": "savings-calculator"})
    gen = lambda s, u, t: json.dumps(proposal, ensure_ascii=False)  # noqa: E731
    out = generate_screen("amount", k, FLOW, {"generate": gen, "normalize": OK}, strategy=strategy("unstructured"),
                          binding_paths=PATHS, variants=1, retries=0)
    finding = next(f for f in out["variants"][0]["findings"] if f["code"] == "new-asset-candidate")
    assert finding["severity"] == "major" and finding["approvable"] is False


def test_adapt_uses_the_fill_base_and_rejects_out_of_scope_changes():
    k = copy.deepcopy(K)
    k.rules["r-terms-required"]["reviewState"] = "candidate"
    assert classify(k, FLOW, "terms")["route"] == "hybrid"
    base = fill(k, FLOW, "terms", VALUES)
    moved = copy.deepcopy(base)
    moved["slots"]["header"][0]["props"]["children"] = "다른 제목"
    answers = [moved, base]
    gen = lambda s, u, t: json.dumps(answers.pop(0), ensure_ascii=False)  # noqa: E731
    out = generate_screen("terms", k, FLOW, {"generate": gen, "normalize": OK}, strategy=strategy("hybrid"),
                          binding_paths=PATHS, variants=1, retries=1)
    attempts = out["variants"][0]["attempts"]
    assert {f["code"] for f in attempts[0]["findings"]} == {"adapt-scope"} and attempts[1]["findings"] == []


def test_missing_model_is_blocked_before_any_call():
    k = _unstructured()
    out = generate_screen("amount", k, FLOW, {"normalize": OK}, strategy=strategy("unstructured"), binding_paths=PATHS)
    assert out["blocked"] == "model-unavailable" and "variants" not in out
    seen = []
    out = generate_screen("amount", k, FLOW, {"generate": lambda *a: seen.append(a)}, strategy=strategy("unstructured"),
                          binding_paths=PATHS)
    assert out["blocked"] == "normalization-unavailable" and seen == []


def test_strategy_must_match_the_route_and_knowledge_must_be_complete():
    out = generate_screen("amount", _unstructured(), FLOW, {"generate": refuse, "normalize": OK},
                          strategy=strategy("structured"), binding_paths=PATHS)
    assert out["blocked"] == "strategy-mismatch"
    k = copy.deepcopy(K)
    k.coverage["truncated"] = True
    assert generate_screen("amount", k, FLOW, {}, strategy=strategy("structured"),
                           binding_paths=PATHS)["blocked"] == "knowledge-incomplete"
    with pytest.raises(ValueError, match="knowledge-incomplete"):
        fill(k, FLOW, "amount", VALUES)
    with pytest.raises(TypeError):
        generate_screen("amount", K, FLOW, {}, binding_paths=PATHS)          # the strategy argument is required


def test_states_for_eligibility_contains_ineligible():
    expectation = expected(GOOD, K)
    assert states_for("eligibility", K, FLOW, expectation) == ["default", "ineligible"]
    assert states_for("amount", K, FLOW, expectation) == ["default"]
    assert "ineligible" in states_for("ineligible", K, FLOW, expectation)    # terminal-state expectation (and asset)


def test_fill_states_apply_state_props_and_condition_states():
    base = fill(K, FLOW, "eligibility", VALUES)
    out = generate_states(base, ["default", "ineligible"], K, {"generate": refuse}, strategy=strategy("structured"),
                          flow=FLOW, binding_paths=PATHS)
    [state] = out["states"]
    assert state["state"] == "ineligible" and state["findings"] == []
    panel = next(n for n in nodes(state["composition"]) if n["asset"] == "eligibility-check")
    assert panel["props"]["tone"] == "subtle" and state["composition"]["state"] == "ineligible"
    assert base["state"] == "default" and "tone" not in next(
        n for n in nodes(base) if n["asset"] == "eligibility-check").get("props", {})


def test_state_prop_outside_the_code_layer_is_rejected():
    k = copy.deepcopy(K)
    k.assets["eligibility-check"]["stateProps"] = {"ineligible": {"glow": "on"}}
    base = fill(k, FLOW, "eligibility", VALUES)
    out = generate_states(base, ["ineligible"], k, {}, strategy=strategy("structured"), flow=FLOW, binding_paths=PATHS)
    assert "state-prop-unknown" in {f["code"] for f in out["states"][0]["findings"]}


def test_invalid_state_props_are_rejected_by_the_ux_model():
    with pytest.raises(ValueError):
        validate_ux_model({"requiredStates": ["error"], "stateProps": "invalid"}, "Atom")


def error_state_seed():
    raw = raw_graph()
    title = next(n for n in raw["nodes"] if n["id"] == "title")
    ux = title["properties"]["uxModel"]
    ux["layers"]["code"]["props"]["tone"]["values"] = ["default", "brand", "danger"]
    ux["requiredStates"] = ["default", "error"]
    ux["stateProps"] = {"error": {"tone": "danger"}}
    return raw


def test_published_state_props_reach_the_error_state_composition(wb):  # noqa: F811
    k, ids = published_knowledge(wb, raw=error_state_seed())
    assert k.complete
    flow = build_flow(k, ids["savings-signup"])
    amount = ids["amount"]
    assert k.assets[ids["title"]]["stateProps"] == {"error": {"tone": "danger"}}
    base = fill(k, flow, amount, VALUES)
    assert validate(base, k, flow=flow, binding_paths=PATHS) == []
    out = generate_states(base, states_for(amount, k, flow, expected(GOOD, k)), k, {}, strategy=strategy("structured"),
                          flow=flow, binding_paths=PATHS)
    [error] = out["states"]
    assert error["state"] == "error" and error["findings"] == []
    title = next(n for n in nodes(error["composition"]) if n["asset"] == ids["title"])
    assert title["props"]["tone"] == "danger"


def test_gui_imports_only_stdlib_and_pure_schema():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for item in ast.walk(ast.parse((root / "gui.py").read_text(encoding="utf-8"))):
        if isinstance(item, ast.ImportFrom) and item.level == 0:
            assert item.module in {"workspace.ontology_schema", "workspace.ontology_ux"} \
                or item.module.split(".")[0] in sys.stdlib_module_names, item.module
        elif isinstance(item, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names)
