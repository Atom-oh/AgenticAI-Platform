"""Regressions for the three recorded core findings; synthetic local storage."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "platform/tests"), str(ROOT / "platform"), str(ROOT / "platform/api")]

import pytest
from test_workbench_core import wb, call, source, documents, queue, run
from test_workbench_skills import draft, install_runtime, runtime_response
from workspace.collaboration import CollaborationError


def restricted_skill(wb):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner"]
    docs[0]["content"] = "SYNTHETIC_OWNER_ONLY_POLICY"
    run(wb, queue(wb, src, docs))
    hit = next(row for row in call(wb, "GET", "knowledge")["items"] if row["sourceDocumentId"] == "guide")
    ref = call(wb, "GET", f"knowledge/{hit['id']}")["evidence"]
    skill = draft(wb, sourceRefs=[ref], instructions="SYNTHETIC_OWNER_ONLY_SKILL_INSTRUCTIONS")
    return src, docs, skill


def test_unreadable_snapshot_cannot_be_replaced_to_disclose_historical_skill(wb):
    src, docs, skill = restricted_skill(wb)
    assert call(wb, "GET", "skills", actor="bob")["items"] == []
    with pytest.raises(CollaborationError):
        call(wb, "GET", f"skills/{skill['id']}", actor="bob")
    # Bob can enumerate source IDs, but cannot read the original document.
    assert any(row["id"] == src["id"] for row in call(wb, "GET", "sources", actor="bob")["items"])
    replacement = [{"id": "guide", "title": "Unauthorized replacement", "revision": "r2",
                    "content": "New public text; no original private content is known.",
                    "allowedRoles": ["owner", "planner"]}]
    try:
        call(wb, "POST", f"sources/{src['id']}/batches",
             {"requestId": "unauthorized-source-replacement", "documents": replacement}, actor="bob")
    except CollaborationError:
        return
    rows = call(wb, "GET", "skills", actor="bob")["items"]
    leaked = [row for row in rows if row.get("instructions") == "SYNTHETIC_OWNER_ONLY_SKILL_INSTRUCTIONS"]
    assert not leaked, json.dumps({"leakedOldSkill": True, "oldSkillId": skill["id"],
                                   "sourceStatus": leaked[0].get("sourceStatus")}, ensure_ascii=False)


def test_new_source_audience_does_not_disclose_old_skill_to_new_reader(wb):
    src, docs, skill = restricted_skill(wb)
    replacement = copy.deepcopy(docs)
    replacement[0].update(revision="r2", content="New shared guidance.", allowedRoles=["owner", "planner"])
    run(wb, queue(wb, src, replacement, request="owner-authorized-revision"))
    assert call(wb, "GET", "knowledge", actor="bob")["items"]
    assert call(wb, "GET", "skills", actor="bob")["items"] == []
    with pytest.raises(CollaborationError):
        call(wb, "GET", f"skills/{skill['id']}", actor="bob")
    assert call(wb, "GET", f"skills/{skill['id']}")["skill"]["sourceStatus"] == "stale"


def test_unreadable_skill_cannot_be_disclosed_by_deprecation_response(wb):
    _, _, skill = restricted_skill(wb)
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/deprecate",
             {"version": skill["version"], "reason": "Review lifecycle"}, actor="bob")


def test_snapshot_creator_with_read_access_can_refresh_own_source(wb):
    src = call(wb, "POST", "sources", {
        "requestId": "planner-source", "name": "Planner guidance", "kind": "snapshot"}, actor="bob")["source"]
    for revision in ("r1", "r2"):
        docs = [{"id": "guide", "title": "Plan", "content": "Shared planning content.",
                 "revision": revision, "allowedRoles": ["owner", "planner"]}]
        run(wb, call(wb, "POST", f"sources/{src['id']}/batches",
                     {"requestId": revision, "documents": docs}, actor="bob"))
    assert call(wb, "GET", "knowledge", actor="bob")["items"][0]["sourceRef"]["revision"] == "r2"


@pytest.mark.parametrize("revocation", ["source", "membership"])
def test_impact_rechecks_access_after_blob_io(wb, monkeypatch, revocation):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner", "designer"]
    docs[0]["entities"][1]["title"] = "SYNTHETIC_PRIVATE_SCREEN_MARKER"
    run(wb, queue(wb, src, docs))
    change = call(wb, "POST", "changes", {"requestId": "private-impact", "title": "Private impact",
        "targetId": "guide", "changeType": "update", "before": "old", "after": "new", "reason": "synthetic"})["change"]
    call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    stored = wb.storage.get(wb.owner, "wb_change", change["id"])
    original = wb.storage.get_blob
    fired = []

    def revoke_after_read(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key == stored["impactKey"] and not fired:
            fired.append(True)
            if revocation == "source":
                replacement = copy.deepcopy(docs)
                replacement[0]["allowedRoles"] = ["owner"]
                queue(wb, src, replacement, request="revoke-during-impact-read")
            else:
                project = wb.storage.get(wb.owner, "project", wb.project["id"])
                members = {key: value for key, value in project["members"].items() if key != "carol"}
                wb.collab.handle("PUT", ["projects", project["id"], "members"],
                    {"version": project["version"], "members": members}, {}, "alice", None)
        return data

    monkeypatch.setattr(wb.storage, "get_blob", revoke_after_read)
    try:
        response = call(wb, "GET", f"changes/{change['id']}/impact", actor="carol")
    except CollaborationError:
        assert fired
        return
    assert fired
    assert "SYNTHETIC_PRIVATE_SCREEN_MARKER" not in json.dumps(response), (
        "Revocation committed before response; private impact was still returned: " + revocation)


@pytest.mark.parametrize("answer", ["한도는 999 원입니다.", "한도는 999원입니다.",
                                    "한도는 999달러입니다.", "한도는 ９９９원입니다."])
def test_execution_rejects_a_numeric_value_absent_from_input_and_sources(wb, answer):
    src = source(wb)
    docs = documents()
    docs[0]["content"] = "The authorized amount is 100 KRW."
    run(wb, queue(wb, src, docs))
    hit = next(row for row in call(wb, "GET", "knowledge")["items"] if row["sourceDocumentId"] == "guide")
    ref = call(wb, "GET", f"knowledge/{hit['id']}")["evidence"]

    def physical_model(system, user, images, model, max_tokens, trace_id, purpose):
        result = runtime_response(user) if purpose == "workbench-skill-behavior" else {
            "status": "answered", "answer": answer, "evidenceIds": ["e1"]}
        return json.dumps(result, ensure_ascii=False), {}, {"backend": "independent-local-probe"}

    install_runtime(wb, physical_model)
    skill = draft(wb, sourceRefs=[ref], instructions="Use only the supplied source facts. Preserve their exact numeric values.")
    run(wb, call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]}))
    checked = call(wb, "GET", f"skills/{skill['id']}")["skill"]
    approved = call(wb, "POST", f"skills/{skill['id']}/approve", {
        "version": checked["version"], "contentHash": checked["contentHash"]})["skill"]
    queued = call(wb, "POST", f"skills/{skill['id']}/execute", {"requestId": "numeric-output",
        "version": approved["version"], "contentHash": approved["contentHash"], "input": "한도를 설명해 주세요."})
    try:
        result = run(wb, queued)
    except CollaborationError as error:
        assert error.code in {"model-output-invalid", "boundary-blocked"}
        return
    output = call(wb, "GET", f"skills/{skill['id']}/executions/{result['artifactId']}")
    assert "999" not in output["result"]["answer"], json.dumps({
        "persistedStatus": output["artifact"]["status"],
        "answer": output["result"]["answer"],
        "executionMode": output["result"].get("executionMode")}, ensure_ascii=False)


def test_numeric_extraction_preserves_known_values_signs_and_citation_identifiers():
    from workbench.runtime import numeric_tokens
    assert numeric_tokens("e1의 근거에서 한도는 １００원입니다.", ["e1"]) == {"100"}
    assert numeric_tokens("한도는 −100원입니다.") == {"-100"}
    assert numeric_tokens("한도는 100,000원이고 비율은 3.5%입니다.") == {"100,000", "3.5"}
