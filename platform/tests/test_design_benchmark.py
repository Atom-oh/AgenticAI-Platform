# platform/tests/test_design_benchmark.py
"""Task E18: independent golden benchmark oracle and success metrics (O-05, section 8)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import SEED, knowledge
from design_loop.benchmark import compare, score, success_metrics
from design_loop.composition import text_of, visible
from design_loop.coverage import check
from design_loop.flow import build_flow, enumerate_cases, expected, traverse
from design_loop.gui import fill
from design_loop.prd_extract import bindings
from test_design_prd_extract import GOOD

K = knowledge()
GOLDEN = json.loads((SEED / "golden.json").read_text(encoding="utf-8"))
VALUES = bindings(GOOD)
REASON = "가입 조건을 충족하지 않아 가입할 수 없습니다. 상담을 신청해 주세요."


def generate(k):
    """A fake generator that honours the approved rules it is given: with r-ineligible-reason it adds the reason."""
    flow = build_flow(k, "savings-signup")
    screens = {s: fill(k, flow, s, VALUES) for s in flow["screens"]}
    rule = k.rules.get("r-ineligible-reason")
    if rule and rule["required"] and rule.get("reviewState", "approved") == "approved":
        screens["ineligible"]["slots"]["body"].append({"id": "why", "asset": "body-text", "props": {"children": REASON}})
    return flow, screens


def outputs(k, flow, screens):
    """Per-page checkpoints as the Browser records them at each page root, plus the recorded end of each case."""
    points, ends = {}, []
    for i, case in enumerate(enumerate_cases(expected(GOOD, k)["conditions"])["cases"]):
        run = traverse(flow, case)
        for step, s in enumerate(run["path"]):
            text = k.screens[s]["title"] + "\n" + text_of(visible(screens[s], case), VALUES, k)
            points[f"page-{i}:{step}"] = {"case": case, "page": s, "state": "default", "visibleText": text,
                                          "truncated": False}
        ends.append({"case": case, "screen": run["path"][-1]})
    return {"checkpoints": points, "ends": ends}


def test_golden_pass_and_golden_fail():
    result = score(outputs(K, *generate(K)), GOLDEN)
    assert result["passed"] == ["g-inel", "g-auto", "g-noauto"] and result["failed"] == [] and result["rate"] == 1.0
    assert not result["blocked"]
    flow, screens = generate(K)
    screens["confirm"]["slots"]["body"] = [n for n in screens["confirm"]["slots"]["body"] if n["asset"] != "notice-alert"]
    bad = score(outputs(K, flow, screens), GOLDEN)
    assert bad["failed"] == ["g-auto", "g-noauto"] and bad["passed"] == ["g-inel"] and bad["rate"] == pytest.approx(1 / 3)
    assert {"kind": "must-show", "screen": "confirm", "text": "예금자보호법"} in bad["details"]["g-auto"]


def test_removing_a_rule_cannot_raise_the_score():
    before = score(outputs(K, *generate(K)), GOLDEN)
    k = copy.deepcopy(K)
    del k.rules["r-ineligible-reason"]                               # the ontology change under judgement
    flow, screens = generate(k)                                       # the fake now omits the reason text
    after = score(outputs(k, flow, screens), GOLDEN)
    assert compare(before, after) == {"improved": [], "regressed": ["g-inel"], "blocked": False}
    # the rule-derived checks cannot see it any more: coverage passes on the weakened knowledge
    exp = expected(GOOD, k)
    cov = check(GOOD, flow, exp, {(s, "default"): c for s, c in screens.items()}
                | {("eligibility", "ineligible"): {**screens["eligibility"], "state": "ineligible"},
                   ("ineligible", "ineligible"): {**screens["ineligible"], "state": "ineligible"}}, k, VALUES)
    assert not [f for f in cov["findings"] if f["severity"] == "critical"]


def test_a_defect_on_a_page_the_rule_leaves_by_navigation_is_detected_from_its_checkpoint():
    out = outputs(K, *generate(K))
    inel = next(key for key, p in out["checkpoints"].items()
                if p["page"] == "intro" and p["case"] == {"eligible": False, "autoTransfer": True})
    out["checkpoints"][inel]["visibleText"] += "\n가입 완료"             # intro is left by the rule's click
    result = score(out, GOLDEN)
    assert "g-inel" in result["failed"]
    assert {"kind": "must-not-show", "screen": "*", "text": "가입 완료", "at": ["intro"]} in result["details"]["g-inel"]


def test_missing_or_truncated_checkpoints_block_and_never_pass():
    out = outputs(K, *generate(K))
    for key in [k for k, p in out["checkpoints"].items() if p["page"] == "ineligible"]:
        del out["checkpoints"][key]
    result = score(out, GOLDEN)
    assert result["blocked"] and "g-inel" in result["incomplete"] and "g-inel" not in result["passed"]
    out = outputs(K, *generate(K))
    key = next(k for k, p in out["checkpoints"].items() if p["page"] == "confirm")
    out["checkpoints"][key]["truncated"] = True
    assert score(out, GOLDEN)["blocked"]
    out = outputs(K, *generate(K)); out["ends"] = []
    result = score(out, GOLDEN)
    assert result["blocked"] and result["passed"] == []
    out = outputs(K, *generate(K))
    del next(iter(out["checkpoints"].values()))["truncated"]           # an unstated truncation flag is unusable
    assert score(out, GOLDEN)["blocked"]


def test_a_wrong_end_fails():
    out = outputs(K, *generate(K))
    out["ends"] = [dict(e, screen="terms") if e["case"] == {"eligible": False, "autoTransfer": True} else e
                   for e in out["ends"]]
    assert "g-inel" in score(out, GOLDEN)["failed"]


def test_golden_shape_is_validated():
    with pytest.raises(ValueError):
        score({"checkpoints": {}}, [{"id": "x", "case": {}, "endsAt": "done"}])
    with pytest.raises(ValueError):
        score({}, GOLDEN)


def test_success_metric_math():
    runs = [{"coveragePassed": True, "rounds": 1, "screens": 10, "approvedWithoutEdit": 8, "edits": 3,
             "stableEdits": 3, "startedAt": 100, "handoffAt": 700},
            {"coveragePassed": False, "rounds": 3, "screens": 10, "approvedWithoutEdit": 4, "edits": 5,
             "stableEdits": 4, "startedAt": 1000, "handoffAt": None}]
    assert success_metrics(runs) == {"runs": 2, "coveragePassRate": 0.5, "meanRegenerationRounds": 2.0,
                                     "maxRegenerationRounds": 3, "approvedWithoutEditRate": 0.6,
                                     "editStabilityRate": 0.875, "handedOff": 1, "meanTimeToHandoffSeconds": 600.0}
    assert success_metrics([{**runs[0], "edits": 0, "stableEdits": 0}])["editStabilityRate"] is None
    for bad in ([], [{**runs[0], "rounds": 0}], [{**runs[0], "stableEdits": 9}], [{**runs[0], "handoffAt": 1}],
                [{**runs[0], "coveragePassed": 1}], [{**runs[0], "approvedWithoutEdit": 11}]):
        with pytest.raises(ValueError):
            success_metrics(bad)


def test_benchmark_imports_only_stdlib():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for item in ast.walk(ast.parse((root / "benchmark.py").read_text(encoding="utf-8"))):
        if isinstance(item, ast.ImportFrom) and item.level == 0:
            assert item.module.split(".")[0] in sys.stdlib_module_names, item.module
        elif isinstance(item, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names)
