"""Actual local Browser observations feeding the independent benchmark oracle."""
import copy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_design_contract import seed, compiled, seed_screens, K, local_browser
from design_fixtures import SEED
from browser_retry import evaluate_bundle
from design_loop.benchmark import score
from workspace.design_observations import normalize
from workspace.rules import validate_contract, contract_hash


def test_observation_plan_is_frozen_and_rejects_incomplete_cases_or_ambiguous_pages():
    value, _, _ = seed()
    contract = value["contract"]
    assert validate_contract(contract) == contract
    assert contract_hash(contract) != contract_hash({key: value for key, value in contract.items() if key != "observationPlan"})
    for mutation in ["missing-case", "duplicate-page", "unknown-entry", "nonboolean", "unknown-field"]:
        plan = copy.deepcopy(contract["observationPlan"])
        if mutation == "missing-case":
            plan["cases"].pop()
        elif mutation == "duplicate-page":
            plan["pages"].append(copy.deepcopy(plan["pages"][0]))
        elif mutation == "unknown-entry":
            plan["entry"] = "unlisted-page"
        elif mutation == "nonboolean":
            next(iter(plan["cases"])).update(eligible="false")
        else:
            plan["callerScript"] = "untrusted"
        with pytest.raises(ValueError):
            normalize(plan)


def test_real_browser_page_checkpoints_and_actual_journey_ends_score_the_frozen_oracle(local_browser):
    derived, registry, flow = seed()
    contract = derived["contract"]
    files, _ = compiled(K, contract, registry, flow, seed_screens(K, flow))
    report = evaluate_bundle(files, contract)
    assert report["passed"], report["blockingFindings"]
    observed = report["observations"]
    assert observed["status"] == "complete", observed["incomplete"]
    assert len(observed["ends"]) == 4
    assert {tuple(sorted(end["case"].items())) for end in observed["ends"]} == {
        tuple(sorted(case.items())) for case in contract["observationPlan"]["cases"]}
    golden = json.loads((SEED / "golden.json").read_text())
    result = score(observed, golden)
    assert result["passed"] == ["g-inel", "g-auto", "g-noauto"] and not result["failed"] and not result["blocked"], json.dumps(result, ensure_ascii=False)
    assert all(key.startswith("journey-") for key in observed["checkpoints"])
    assert any(point["page"] == "intro" and "연 2.0%" in point["visibleText"]
               for point in observed["checkpoints"].values())


def test_actual_missing_ineligible_guidance_fails_the_same_independent_oracle(local_browser):
    from design_loop.composition import walk
    derived, registry, flow = seed()
    screens = seed_screens(K, flow)
    removed = 0
    for composition in screens.values():
        for _, node, _ in walk(composition):
            if node["asset"] == "ineligible-reason-copy":
                node["props"]["children"] = ""
                removed += 1
    assert removed
    files, _ = compiled(K, derived["contract"], registry, flow, screens)
    report = evaluate_bundle(files, derived["contract"])
    assert report["passed"], report["blockingFindings"]
    observed = report["observations"]
    assert observed["status"] == "complete", observed["incomplete"]
    result = score(observed, json.loads((SEED / "golden.json").read_text()))
    assert result["passed"] == ["g-auto", "g-noauto"] and result["failed"] == ["g-inel"] and not result["blocked"]
    assert {"kind": "must-show", "screen": "ineligible", "text": "가입 조건"} in result["details"]["g-inel"]


def test_missing_actual_page_observation_stays_incomplete_even_when_normal_rules_pass(local_browser):
    derived, registry, flow = seed()
    contract = derived["contract"]
    files, _ = compiled(K, contract, registry, flow)
    corrupted = copy.deepcopy(contract)
    mapping = next(page for page in corrupted["observationPlan"]["pages"] if page["screen"] == "intro")
    mapping["target"] = "absent-observation-page"
    corrupted["observationPlan"]["entry"] = "absent-observation-page"
    # Observation plans describe measurement; they do not drop/replace ordinary assertions.
    report = evaluate_bundle(files, corrupted)
    assert report["observations"]["status"] == "incomplete"
    assert report["observations"]["incomplete"]
    assert score(report["observations"], json.loads((SEED / "golden.json").read_text()))["blocked"]
