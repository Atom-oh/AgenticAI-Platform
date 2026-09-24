# platform/tests/test_design_flow.py
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge
from design_loop.flow import build_flow, enumerate_cases, evaluate, expected, flow_issues, mermaid, traverse
from test_design_prd_extract import GOOD

K = knowledge()


def test_cases_and_paths():
    flow = build_flow(K, "savings-signup")
    exp = expected(GOOD, K)
    assert exp["conditions"] == ["eligible", "autoTransfer"]
    cases = enumerate_cases(exp["conditions"])["cases"]
    assert len(cases) == 4
    assert traverse(flow, {"eligible": False, "autoTransfer": True})["path"] == ["intro", "eligibility", "ineligible"]
    on = traverse(flow, {"eligible": True, "autoTransfer": True})["path"]
    assert "evidence-auto" in on and on[-1] == "done"
    assert "evidence-auto" not in traverse(flow, {"eligible": True, "autoTransfer": False})["path"]


def test_complete_seed_has_no_issues():
    assert flow_issues(build_flow(K, "savings-signup"), expected(GOOD, K), K) == []


def test_removing_the_conditional_parent_is_detected():
    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"] = [t for t in proc["transitions"] if t["dst"] != "evidence-auto" and t["src"] != "evidence-auto"]
    proc["transitions"].append({"id": "t-x", "src": "preferential", "dst": "confirm", "conditionId": None, "condition": "cond:autoTransfer"})
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert "conditional-visit-missing" in codes and "unreachable-screen" in codes


def test_missing_ineligible_terminal_is_detected():
    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"] = [t for t in proc["transitions"] if t["dst"] != "ineligible"]
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert {"dead-end", "terminal-state-missing"} & codes


def test_truncated_enumeration_blocks():
    assert enumerate_cases([f"c{i}" for i in range(8)], limit=64)["truncated"]


def test_mermaid_highlights_case_path():
    text = mermaid(build_flow(K, "savings-signup"), {"eligible": True, "autoTransfer": True})
    assert text.startswith("flowchart TD") and "preferential ==> evidence-auto" in text


# ---- additional cases -------------------------------------------------------------------------------------

def test_flow_shape_and_expectation_are_independent_of_transitions():
    flow = build_flow(K, "savings-signup")
    assert flow["entry"] == "intro" and set(flow["terminals"]) == {"ineligible", "done"}
    assert {"id": "n02-eligibility-terms", "src": "eligibility", "dst": "terms", "when": "cond:eligible",
            "navigation": "forward"} in flow["transitions"]
    k = copy.deepcopy(K)
    k.procedures["savings-signup"]["transitions"] = []
    assert expected(GOOD, k) == expected(GOOD, K)          # derived from the PRD and approved rules only
    kinds = [r["kind"] for r in expected(GOOD, K)["requirements"]]
    assert kinds.count("reach-terminal") == 1 and "terminal-state" in kinds and kinds.count("notice") == 1
    visit = next(r for r in expected(GOOD, K)["requirements"] if r["kind"] == "visit-when")
    assert visit["when"] == "cond:eligible & cond:autoTransfer" and visit["asset"] == "auto-transfer"
    rules = {r["rule"]: r["when"] for r in expected(GOOD, K)["requirements"] if r["kind"] == "rule-screen"}
    assert rules == {"r-terms-required": "cond:eligible", "r-ineligible-reason": "!cond:eligible"}


def test_enumeration_is_complete_up_to_the_limit():
    out = enumerate_cases([f"c{i}" for i in range(6)], limit=64)
    assert not out["truncated"] and len(out["cases"]) == 64
    assert len({tuple(sorted(c.items())) for c in out["cases"]}) == 64
    truncated = enumerate_cases([f"c{i}" for i in range(8)], limit=64)
    assert len(truncated["cases"]) == 64
    codes = {i["code"] for i in flow_issues(build_flow(K, "savings-signup"),
                                            {**expected(GOOD, K), "conditions": [f"c{i}" for i in range(8)]}, K)}
    assert "case-enumeration-truncated" in codes


def test_evaluate_is_strict():
    assert evaluate(None, {}) is True
    assert evaluate("cond:a & !cond:b", {"a": True, "b": False}) is True
    assert evaluate("cond:a & !cond:b", {"a": True, "b": True}) is False
    import pytest
    with pytest.raises(ValueError):
        evaluate("cond:missing", {"a": True})


def test_ambiguous_branch_loop_and_back_edges():
    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"].append({"id": "t-dup", "src": "intro", "dst": "terms", "conditionId": None, "condition": None})
    flow = build_flow(k, "savings-signup")
    assert traverse(flow, {"eligible": True, "autoTransfer": True})["end"] == "ambiguous"
    assert "ambiguous-branch" in {i["code"] for i in flow_issues(flow, expected(GOOD, k), k)}

    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"] = [t for t in proc["transitions"] if t["id"] != "n09-confirm-done"]
    proc["transitions"].append({"id": "t-loop", "src": "confirm", "dst": "amount", "conditionId": None, "condition": None})
    flow = build_flow(k, "savings-signup")
    assert traverse(flow, {"eligible": True, "autoTransfer": False})["end"] == "loop"
    assert "loop" in {i["code"] for i in flow_issues(flow, expected(GOOD, k), k)}

    # a back edge is a user action, not a forward move: the journey still reaches the terminal
    k = copy.deepcopy(K)
    k.procedures["savings-signup"]["transitions"].append(
        {"id": "t-back", "src": "amount", "dst": "terms", "conditionId": None, "condition": None, "navigation": "back"})
    flow = build_flow(k, "savings-signup")
    assert traverse(flow, {"eligible": True, "autoTransfer": False})["end"] == "terminal"
    assert flow_issues(flow, expected(GOOD, k), k) == []


def test_leak_rule_and_unused_transition_are_detected():
    k = copy.deepcopy(K)
    k.screens["confirm"]["assets"].append("auto-transfer")          # evidence asset on the path without the condition
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert "conditional-visit-leak" in codes

    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"] = [t for t in proc["transitions"] if t["id"] not in ("n04-terms-amount", "n02-eligibility-terms")]
    proc["transitions"].append({"id": "t-skip", "src": "eligibility", "dst": "amount", "conditionId": None,
                                "condition": "cond:eligible"})
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert "rule-screen-missing" in codes and "unreachable-screen" in codes

    k = copy.deepcopy(K)
    k.procedures["savings-signup"]["transitions"].append(
        {"id": "t-never", "src": "eligibility", "dst": "done", "conditionId": None, "condition": "cond:eligible & !cond:eligible"})
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert codes == {"unused-transition"}


def test_missing_evidence_asset_is_reported():
    k = copy.deepcopy(K)
    k.assets["auto-transfer"]["conditions"] = []
    exp = expected(GOOD, k)
    assert next(r for r in exp["requirements"] if r["kind"] == "visit-when")["asset"] is None
    assert "conditional-visit-missing" in {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), exp, k)}
