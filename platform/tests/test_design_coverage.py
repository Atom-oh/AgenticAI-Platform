# platform/tests/test_design_coverage.py
"""Task E15: coverage over cases, conditions, rules and notices (V-03; Codex #9, #11)."""
import copy
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT, knowledge
from design_loop.composition import validate, walk
from design_loop.coverage import check
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.gui import fill, generate_states, states_for
from design_loop.prd_extract import bindings
from design_loop.route import strategy
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
EXP = expected(GOOD, K)
VALUES = bindings(GOOD)
CHROMIUM = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
needs = pytest.mark.skipif(not (ROOT / "react-kit" / "node_modules").exists() or not shutil.which("node"), reason="react-kit")


def screens_for(k=K, flow=FLOW, exp=EXP):
    screens = {(s, "default"): fill(k, flow, s, VALUES) for s in flow["screens"]}
    for s in flow["screens"]:
        for st in states_for(s, k, flow, exp):
            if st != "default":
                [state] = generate_states(screens[(s, "default")], [st], k, {}, strategy=strategy("structured"),
                                          flow=flow, binding_paths=frozenset(VALUES))["states"]
                screens[(s, st)] = state["composition"]
    return screens


def run(screens=None, k=K, flow=FLOW, exp=EXP):
    return check(GOOD, flow, exp, screens if screens is not None else screens_for(k, flow, exp), k, VALUES)


def codes(out):
    return {f["code"] for f in out["findings"]}


def nodes_of(c, asset):
    return [n for _, n, _ in walk(c) if n["asset"] == asset]


def test_complete_seed_has_no_findings():
    out = run()
    assert out == {"findings": [], "blocked": False}


def test_removing_a_screen_composition_is_missing_screen():
    screens = screens_for(); screens.pop(("evidence-auto", "default"))
    out = run(screens)
    found = [f for f in out["findings"] if f["code"] == "missing-screen"]
    assert found and all(f["severity"] == "critical" and f["screen"] == "evidence-auto" for f in found)
    assert all(f["case"]["autoTransfer"] and f["case"]["eligible"] for f in found)   # only the paths visiting it


def test_removing_the_conditional_target_is_missing_conditional():
    screens = screens_for()
    c = screens[("evidence-auto", "default")]
    c["slots"]["body"] = [n for n in c["slots"]["body"] if n["asset"] != "branch-select"]
    out = run(screens)
    assert codes(out) == {"missing-conditional"}
    assert all(f["target"] == "branch-select" and f["severity"] == "critical" for f in out["findings"])


def shared_condition_knowledge(effect):
    """A screen every eligible case visits (`preferential`) hosts the conditional node, so one screen sees both
    outcomes (AJ1). `include` holds when the condition holds; `exclude` names the negated expression."""
    k = copy.deepcopy(K)
    when = "cond:autoTransfer" if effect == "include" else "!cond:autoTransfer"
    k.assets["product-summary"]["conditions"] = [{"id": "c-pref-branch", "when": when, "effect": effect,
                                                  "target": "branch-select"}]
    return k


@pytest.mark.parametrize("effect", ["include", "exclude"])
def test_same_screen_both_outcomes(effect):
    k = shared_condition_knowledge(effect)
    exp = expected(GOOD, k)
    screens = screens_for(k, FLOW, exp)
    pref = screens[("preferential", "default")]
    [branch] = nodes_of(pref, "branch-select")
    assert branch["visibleWhen"] == {"when": "cond:autoTransfer" if effect == "include" else "!cond:autoTransfer",
                                     "negate": effect == "exclude"}
    assert validate(pref, k, flow=FLOW, binding_paths=frozenset(VALUES)) == []
    assert run(screens, k=k, exp=exp) == {"findings": [], "blocked": False}   # both autoTransfer cases pass
    del branch["visibleWhen"]
    assert {f["code"] for f in validate(pref, k, flow=FLOW, binding_paths=frozenset(VALUES))} == {"visibility-missing"}
    out = run(screens, k=k, exp=exp)
    bad = [f for f in out["findings"] if f["screen"] == "preferential"]
    assert bad and {f["code"] for f in bad} == {"forbidden-present"}
    assert {f["case"]["autoTransfer"] for f in bad} == {False}               # only the hidden outcome fails


def test_four_assignment_exclude_coverage():
    # E10 deferred part: `exclude` on `cond:a & cond:b` negates the whole expression in coverage as well.
    k = copy.deepcopy(K)
    k.assets["auto-transfer"]["conditions"] = [{"id": "c-x", "when": "cond:a & cond:b", "effect": "exclude",
                                                "target": "branch-select"}]
    exp = copy.deepcopy(EXP); exp["conditions"] = exp["conditions"] + ["a", "b"]
    screens = screens_for(k, FLOW, exp)
    assert run(screens, k=k, exp=exp) == {"findings": [], "blocked": False}
    [branch] = nodes_of(screens[("evidence-auto", "default")], "branch-select")
    del branch["visibleWhen"]
    bad = run(screens, k=k, exp=exp)["findings"]
    assert bad and {f["code"] for f in bad} == {"forbidden-present"}
    assert {(f["case"]["a"], f["case"]["b"]) for f in bad} == {(True, True)}


def test_incomplete_knowledge_is_blocked():
    k = copy.deepcopy(K)
    k.coverage["unknown"] = ["unapproved-dependency"]
    assert not k.complete
    out = check(GOOD, FLOW, EXP, screens_for(), k, VALUES)
    assert out["blocked"] is True


def test_truncated_case_enumeration_is_blocked():
    exp = copy.deepcopy(EXP)
    exp["conditions"] = exp["conditions"] + [f"x{i}" for i in range(6)]      # 2**8 cases > 64
    out = run(exp=exp)
    assert out["blocked"] is True and "case-enumeration-truncated" in codes(out)


def test_changing_a_notice_binding_is_missing_notice():
    screens = screens_for()
    [alert] = nodes_of(screens[("confirm", "default")], "notice-alert")
    alert["bind"]["message"] = "product.notice.notice-2"
    out = run(screens)
    assert codes(out) == {"missing-notice", "missing-required"}             # the notice text is also required there
    notice = [f for f in out["findings"] if f["code"] == "missing-notice"]
    assert notice and all(f["severity"] == "critical" and f["notice"] == "n1" for f in notice)
    # the ontology moving the notice off every designated screen is detected as well
    k = copy.deepcopy(K)
    k.assets["notice-alert"]["bindings"] = []
    k.screens["confirm"]["assets"] = [a for a in k.screens["confirm"]["assets"] if a != "notice-alert"]
    assert "missing-notice" in codes(run(k=k))


def test_a_flow_without_a_designated_notice_screen_fails_closed():
    exp = copy.deepcopy(EXP)
    flow = copy.deepcopy(FLOW)
    k = copy.deepcopy(K)
    k.screens["confirm"]["pageId"] = "review"
    k.screens["review"] = k.screens.pop("confirm")
    k.screens["review"]["id"] = "review"
    flow["screens"] = ["review" if s == "confirm" else s for s in flow["screens"]]
    flow["transitions"] = [{**t, "src": "review" if t["src"] == "confirm" else t["src"],
                            "dst": "review" if t["dst"] == "confirm" else t["dst"]} for t in flow["transitions"]]
    k.assets["notice-alert"]["bindings"] = []                                 # no rule or binding designates a screen
    screens = {(("review" if s == "confirm" else s), st): {**c, "screenId": "review" if s == "confirm" else s}
               for (s, st), c in screens_for().items()}
    out = check(GOOD, flow, exp, screens, k, VALUES)
    assert any(f["code"] == "missing-notice" and f.get("reason") == "no-designated-screen" for f in out["findings"])


def test_missing_required_value_and_rule_target():
    screens = screens_for()
    c = screens[("intro", "default")]
    for n in nodes_of(c, "rate-summary"):
        n["bind"] = {}
        n["props"] = {"items": [{"label": "기본금리", "value": "문의"}]}
    t = screens[("terms", "default")]
    t["slots"]["body"] = [n for n in t["slots"]["body"] if n["asset"] != "terms-consent"]
    out = run(screens)
    missing = [f for f in out["findings"] if f["code"] == "missing-required"]
    assert missing and {f["screen"] for f in missing} == {"intro"} and {f["value"] for f in missing} == {"연 2.0%", "12개월"}
    rule = [f for f in out["findings"] if f["code"] == "missing-rule-target"]
    assert rule and {(f["ruleId"], f["target"], f["screen"]) for f in rule} == {("r-terms-required", "terms-consent", "terms")}


def test_missing_state_is_major():
    screens = screens_for(); screens.pop(("eligibility", "ineligible"))
    out = run(screens)
    assert [(f["code"], f["severity"], f["screen"], f["state"]) for f in out["findings"]] == \
        [("missing-state", "major", "eligibility", "ineligible")]


def test_flow_issues_are_critical():
    flow = copy.deepcopy(FLOW)
    flow["transitions"] = [t for t in flow["transitions"] if t["id"] != "n06-preferential-evidence-auto"]
    out = check(GOOD, flow, EXP, screens_for(), K, VALUES)
    assert "dead-end" in codes(out) and all(f["severity"] == "critical" for f in out["findings"] if f["code"] == "dead-end")


# ---- node-enabled: the compiled page shows the node for one case and hides it for the other ----------------------

@pytest.fixture
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and os.path.isfile(CHROMIUM):
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", CHROMIUM)


@needs
def test_compiled_page_shows_and_hides_the_conditional_node_through_the_browser(local_browser):
    from test_design_contract import compiled, only, seed
    from workspace.browser import evaluate_bundle
    out, registry, flow = seed()
    contract = only(out["contract"], "visible-")
    assert contract["rules"]
    screens = screens_for()
    bundle, _ = compiled(K, contract, registry, flow, screens)
    assert evaluate_bundle(bundle, contract)["functionalStatus"] == "pass"
    [branch] = nodes_of(screens[("evidence-auto", "default")], "branch-select")
    del branch["visibleWhen"]                                  # always shown: the hidden outcome must fail
    bundle, _ = compiled(K, contract, registry, flow, screens)
    report = evaluate_bundle(bundle, contract)
    assert report["functionalStatus"] == "fail"
    [failed] = [c for c in report["checks"] if c["status"] == "fail"]
    step = next(s for s in failed["steps"] if s["status"] != "pass")
    assert step["action"] == "expectVisible" and step["expected"] is False


def test_coverage_module_imports_only_stdlib_and_pure_schema():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for item in ast.walk(ast.parse((root / "coverage.py").read_text(encoding="utf-8"))):
        if isinstance(item, ast.ImportFrom) and item.level == 0:
            assert item.module in {"workspace.ontology_schema", "workspace.ontology_ux"} \
                or item.module.split(".")[0] in sys.stdlib_module_names, item.module
        elif isinstance(item, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names)
