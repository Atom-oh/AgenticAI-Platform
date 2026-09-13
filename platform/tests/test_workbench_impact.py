import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb, call, indexed, queue, documents, source, run
from workspace.collaboration import CollaborationError


def change(wb):
    return call(wb, "POST", "changes", {
        "requestId": "change", "title": "출금 안내 변경", "targetId": "guide",
        "changeType": "update", "before": "old", "after": "new", "reason": "정책 변경"})["change"]


def analyze(wb):
    indexed(wb)
    item = change(wb)
    return call(wb, "POST", f"changes/{item['id']}/analyze", {"version": item["version"]})


def test_impact_follows_reverse_dependencies_with_witnesses_and_terminal_ownership(wb):
    result = analyze(wb)
    items = {item["targetId"]: item for item in result["impact"]["items"]}
    assert set(items) == {"guide", "screen", "api"}
    assert items["screen"]["role"] == "designer"
    assert items["api"]["role"] == "developer"
    assert [node for node in items["api"]["witnessPath"]] == ["guide", "screen", "api"]
    assert items["screen"]["confidence"] == "candidate"
    assert items["screen"]["sourceRevision"] == "r1"
    assert len(result["tasks"]) == 3
    assert result["impact"]["coverage"]["complete"] is False


def test_task_completion_requires_current_matching_evidence_and_role(wb):
    result = analyze(wb)
    task = next(x for x in result["tasks"] if x["role"] == "designer")
    with pytest.raises(CollaborationError):
        call(wb, "PUT", f"tasks/{task['id']}", {"version": task["version"], "status": "done"})
    with pytest.raises(CollaborationError):
        call(wb, "PUT", f"tasks/{task['id']}",
             {"version": task["version"], "status": "in-progress"}, actor="dana")
    doc = call(wb, "GET", "knowledge", query={"q": "withdrawal"})["items"][0]
    evidence = call(wb, "GET", "knowledge/" + doc["id"])["evidence"]
    done = call(wb, "PUT", f"tasks/{task['id']}", {"version": task["version"], "status": "done",
                 "evidenceRefs": [evidence]}, actor="carol")["task"]
    assert done["status"] == "done" and done["completedBy"] == "carol"
    with pytest.raises(CollaborationError) as err:
        call(wb, "PUT", f"tasks/{task['id']}", {"version": task["version"], "status": "open"})
    assert err.value.status == 409


def test_stale_source_cannot_reuse_impact_or_finish_task(wb):
    result = analyze(wb)
    src = call(wb, "GET", "sources")["items"][0]
    task = result["tasks"][0]
    queue(wb, src, request="invalidate")
    with pytest.raises(CollaborationError):
        call(wb, "PUT", f"tasks/{task['id']}", {"version": task["version"], "status": "done",
                                            "evidenceRefs": [{"sourceId": src["id"], "revision": "r1"}]})


def test_frontend_change_types_are_authorable_and_unknown_target_is_explicit(wb):
    item = call(wb, "POST", "changes", {"requestId": "condition", "title": "상품 조건 변경",
        "targetId": "unmapped", "changeType": "condition", "before": "기존", "after": "신규", "reason": "예시"})["change"]
    result = call(wb, "POST", f"changes/{item['id']}/analyze", {"version": item["version"]})
    assert result["impact"]["items"][0]["confidence"] == "unknown"
    assert result["impact"]["coverage"]["complete"] is False


def test_current_impact_cannot_leak_after_source_change(wb):
    result = analyze(wb)
    assert call(wb, "GET", f"changes/{result['change']['id']}/impact")["impact"]["items"]
    src = call(wb, "GET", "sources")["items"][0]
    queue(wb, src, request="next")
    with pytest.raises(CollaborationError):
        call(wb, "GET", f"changes/{result['change']['id']}/impact")


def test_task_update_does_not_disclose_source_restricted_title(wb):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner"]
    run(wb, queue(wb, src, docs))
    item = change(wb)
    result = call(wb, "POST", f"changes/{item['id']}/analyze", {"version": item["version"]})
    task = next(t for t in result["tasks"] if t["role"] == "designer")
    with pytest.raises(CollaborationError):
        call(wb, "PUT", f"tasks/{task['id']}",
             {"version": task["version"], "status": "in-progress"}, actor="carol")


def test_synthetic_product_change_includes_flow_guide_and_planner_work(wb):
    call(wb, "POST", "examples", {"requestId": "product-flow-guide"})
    change = call(wb, "POST", "changes", {
        "requestId": "product-change", "title": "가상 상품 조건 변경", "targetId": "example-product",
        "changeType": "condition", "before": "기존 조건", "after": "개정 조건", "reason": "합성 영향 검증"})["change"]
    result = call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    guide = next(item for item in result["impact"]["items"] if item["targetId"] == "example-guide")
    assert guide["witnessPath"] == ["example-product", "example-flow", "example-guide"]
    assert guide["role"] == "planner" and guide["confidence"] == "candidate"
    assert all(edge["provenance"] == "synthetic-fixture" for edge in guide["witnessEdges"])
    assert any(task["targetId"] == "example-guide" and task["role"] == "planner" for task in result["tasks"])
    assert not any(item["targetId"] in {"example-component", "example-icon"} for item in result["impact"]["items"])
