"""Declared human-reviewed copy, deterministic placement and actual rendered guidance."""
import copy
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from design_fixtures import raw_graph, knowledge, sealed_graph, published_knowledge
from test_design_contract import seed, compiled, local_browser
from test_workbench_core import wb
from design_loop.gui import fill
from design_loop.composition import walk
from design_loop.knowledge import from_snapshot
from design_loop.prd_extract import bindings
from design_loop.flow import build_flow
from test_design_prd_extract import GOOD
from workspace import ontology_schema as schema
from browser_retry import evaluate_bundle

COPY = "가입 조건을 충족하지 않아 가입할 수 없습니다. 상담을 신청해 주세요."


def test_ineligible_copy_is_candidate_until_human_review_and_has_actual_code_mapping():
    raw = raw_graph()
    node = next(node for node in raw["nodes"] if node["id"] == "ineligible-reason-copy")
    assert node["reviewState"] == "candidate" and node["title"] == COPY
    assert node["type"] == "Atom" and node["sourceRefs"]
    assert node["properties"]["uxModel"]["layers"]["code"]["exportName"] == "Text"
    snapshot = sealed_graph()
    target = next(node for node in snapshot["nodes"] if node["id"] == "ineligible-reason-copy")
    target["reviewState"] = "candidate"
    target.update(schema.seal({key: value for key, value in target.items() if key != "contentHash"}))
    k = from_snapshot(snapshot)
    assert not k.complete
    with pytest.raises(ValueError, match="knowledge-incomplete"):
        fill(k, build_flow(knowledge(), "savings-signup"), "ineligible", bindings(GOOD))


def test_human_reviewed_source_places_reason_without_model_or_financial_rewrite(wb):
    k, ids = published_knowledge(wb, request_id="copy-reviewed")
    procedure = ids["savings-signup"]
    screen = ids["ineligible"]
    flow = build_flow(k, procedure)
    page = fill(k, flow, screen, bindings(GOOD))
    reason = next(node for _, node, _ in walk(page) if node["asset"] == ids["ineligible-reason-copy"])
    assert reason["props"]["children"] == COPY
    assert all(node["asset"] != ids["ineligible-reason-copy"] for _, node, _ in
               walk(fill(k, flow, ids["intro"], bindings(GOOD))))
    assert bindings(GOOD)["product.baseRate"] == "연 2.0%"


def test_actual_browser_shows_reviewed_reason_and_alternative_in_ineligible_state(local_browser):
    k = knowledge()
    derived, registry, flow = seed(k=k)
    contract = copy.deepcopy(derived["contract"])
    contract["rules"].append({
        "id": "independent-ineligible-copy", "title": "미자격 사유와 대체 행동",
        "required": True, "source": {"kind": "manual"},
        "steps": [
            {"action": "select", "target": "case-select", "targetLabel": "조건 사례", "value": "0:ineligible:default"},
            {"action": "expectText", "target": registry.page_id("ineligible"), "targetLabel": "미자격 화면",
             "value": COPY, "match": "contains"},
        ],
    })
    bundle, _ = compiled(k, contract, registry, flow)
    report = evaluate_bundle(bundle, contract)
    assert report["passed"], report["blockingFindings"]
    check = next(item for item in report["checks"] if item["caseId"] == "independent-ineligible-copy")
    assert COPY in check["visibleText"]
    assert COPY in check["steps"][-1]["actual"]
    assert "가입 완료" not in check["steps"][-1]["actual"]
