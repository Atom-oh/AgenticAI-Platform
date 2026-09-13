"""Source-bound impact analysis using real private library and storage code."""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_documents_library import api, approve, call, finalize, path, project, upload


def catalog(api):
    from graph.store import Edge, Node
    api.graph_store.upsert_nodes([
        Node("REG-1", "Regulation", {"code": "REG-1", "title": "합성 담보 기준", "article": "예제 1조",
                                    "version": "1", "effectiveDate": "2026-09-13"}),
        Node("DOC-1", "Document", {"title": "공유 목록의 합성 문서"}),
        Node("CND-1", "Condition", {"conditionId": "CND-1"}),
        Node("PRD-1", "Product", {"name": "합성 상품"}),
        Node("SCR-1", "Screen", {"name": "합성 화면"}),
        Node("CMP-1", "Component", {"name": "합성 컴포넌트"}),
        Node("D-1", "Department", {"name": "합성 기획팀"}),
    ])
    api.graph_store.upsert_edges([
        Edge("CND-1", "DERIVED_FROM", "REG-1"),
        Edge("PRD-1", "HAS_CONDITION", "CND-1"),
        Edge("PRD-1", "SOLD_VIA", "SCR-1"),
        Edge("SCR-1", "USES", "CMP-1"),
        Edge("SCR-1", "OWNED_BY", "D-1"),
        Edge("DOC-1", "REFERENCES", "REG-1"),
    ])


def source(api, ref, data=None, project_id=None, **fields):
    raw = data or f"{ref} 합성 검토 기준입니다.\n\n개정 시 관련 항목을 검토합니다.".encode()
    created = upload(api, data=raw, request="source-" + ref, project=project_id, graphRef=ref, **fields)
    extracted = finalize(api, created, project=project_id)
    status, approved = approve(api, extracted, project=project_id)
    assert status == 200, approved
    return approved


def start(api, actor="alice", project_id=None, request="analysis"):
    status, result, _ = call(api, "POST", "/impact-analyses", {
        "requestId": request, "query": "담보 기준 변경 시 검토할 문서는?",
        "regulationRef": "REG-1",
    }, actor=actor, project=project_id)
    assert status == 202, result
    return result


def run(api, created, model=None, owner="alice"):
    from workspace.worker import Worker
    calls = []

    def invoke(system, user, images, model_id, max_tokens, trace_id, purpose):
        calls.append({"system": system, "user": user, "images": images, "purpose": purpose})
        if model:
            return model(system, user)
        return json.dumps({"summary": "담당자의 검토가 필요합니다.", "findings": [
            {"nodeId": "DOC-1", "reason": "등록된 범위를 원문과 비교하세요.", "citationIds": ["E1"]},
        ]}), {"inputTokens": 12, "outputTokens": 10}, {"modelId": "synthetic-model"}

    worker = Worker(storage=api.storage, model_call=invoke)
    worker.graph_store = api.graph_store
    result = worker.handle({"owner": owner, "jobId": created["job"]["id"]})
    return result, calls


def result(api, created, actor="alice", project_id=None):
    return call(api, "GET", "/impact-analyses/" + created["analysis"]["id"], actor=actor, project=project_id)


def replace_source(api, previous, project_id=None):
    raw = "새 버전의 합성 담보 기준입니다.".encode()
    doc_id = previous["document"]["id"]
    owner = "project:" + project_id if project_id else "alice"
    doc = api.storage.get(owner, "document", doc_id)
    status, created, _ = call(api, "POST", f"/documents/{doc_id}/revisions", {
        "requestId": "replacement", "version": doc["version"], "name": "revision2.txt",
        "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }, project=project_id)
    assert status == 201, created
    assert call(api, "PUT", path(created) + "/parts/0", raw, project=project_id)[0] == 200
    status, queued, _ = call(api, "POST", path(created) + "/complete", {}, project=project_id)
    assert status == 202
    parsed = finalize(api, queued, project=project_id)
    status, approved = approve(api, parsed, project=project_id)
    assert status == 200, approved
    return approved


def test_missing_regulation_original_returns_candidates_without_model(api):
    catalog(api)
    created = start(api)
    done, calls = run(api, created)
    assert done["status"] == "completed" and calls == []
    status, payload, _ = result(api, created)
    assert status == 200 and payload["analysis"]["status"] == "needs_sources"
    assert payload["result"]["counts"]["documents"] == 1
    assert payload["result"]["model"]["invoked"] is False
    assert payload["result"]["findings"] == []
    coverage = payload["result"]["coverage"]
    assert coverage["unavailableSources"] == 1
    assert coverage["uncheckedSources"] == 1
    assert coverage["sourceResolution"] == [
        {"graphRef": "REG-1", "status": "unavailable", "reason": "approved_source_unavailable"},
        {"graphRef": "DOC-1", "status": "not_checked", "reason": "regulation_source_required"},
    ]


def test_source_limit_marks_skipped_originals_unchecked_instead_of_unavailable(api, monkeypatch):
    import documents.analysis as analysis
    catalog(api); source(api, "REG-1"); source(api, "DOC-1")
    monkeypatch.setattr(analysis, "MAX_MODEL_SOURCES", 1)
    created = start(api); run(api, created)
    coverage = result(api, created)[1]["result"]["coverage"]
    assert coverage["linkedSources"] == 1 and coverage["unavailableSources"] == 0
    assert coverage["sourceLimitReached"] is True and coverage["uncheckedSources"] == 1
    assert coverage["sourceResolution"][-1] == {
        "graphRef": "DOC-1", "status": "not_checked", "reason": "source_limit",
    }


def test_graph_query_limit_is_preserved_in_the_private_result(api):
    catalog(api); source(api, "REG-1")
    original = api.graph_store.impact_of_regulation_id
    def capped(identifier):
        impact = original(identifier)
        impact.traversal_limit_reached = True
        return impact
    api.graph_store.impact_of_regulation_id = capped
    created = start(api); run(api, created)
    coverage = result(api, created)[1]["result"]["coverage"]
    assert coverage["graphTraversalLimited"] is True and coverage["graphCountsExact"] is False


def test_approved_sources_have_exact_private_paragraph_references_and_no_auto_approval(api):
    catalog(api)
    regulation = source(api, "REG-1")
    source(api, "DOC-1")
    created = start(api)
    done, calls = run(api, created)
    assert done["status"] == "completed" and len(calls) == 1
    status, payload, _ = result(api, created)
    assert status == 200 and payload["analysis"]["status"] == "needs_review"
    outcome = payload["result"]
    assert outcome["verification"]["semantic"] == "requires_human_review"
    assert outcome["verification"]["references"] == "checked"
    assert outcome["findings"][0]["citationIds"] == ["E1"]
    evidence = outcome["evidence"][0]
    assert evidence["revisionId"] == regulation["revision"]["id"]
    assert evidence["textHash"] == regulation["revision"]["textHash"]
    status, view, _ = call(api, "GET", f"/documents/{evidence['documentId']}/revisions/{evidence['revisionId']}",
                          query={"paragraph": evidence["paragraphId"]})
    assert status == 200
    assert evidence["quote"] == next(p["text"] for p in view["paragraphs"] if p["id"] == evidence["paragraphId"])
    assert "originalKey" not in repr(payload) and "resultKey" not in repr(payload)
    assert len(calls[0]["user"]) <= 36_000


def test_forbidden_doc_is_absent_from_model_context_and_private_result(api):
    catalog(api); team = project(api)
    source(api, "REG-1", project_id=team)
    hidden = source(api, "DOC-1", data=b"SECRET-CONTENT", project_id=team,
                    title="SECRET-TITLE", readRoles=["owner"])
    created = start(api, actor="bob", project_id=team)
    done, calls = run(api, created, owner="project:" + team)
    assert done["status"] == "completed"
    assert "SECRET-CONTENT" not in calls[0]["user"] and "SECRET-TITLE" not in calls[0]["user"]
    status, payload, _ = result(api, created, actor="bob", project_id=team)
    assert status == 200 and "SECRET-" not in repr(payload)
    assert len(payload["result"]["sources"]) == 1
    assert hidden["document"]["id"] not in repr(payload)
    assert payload["result"]["coverage"]["sourceResolution"][-1] == {
        "graphRef": "DOC-1", "status": "unavailable", "reason": "approved_source_unavailable",
    }


def test_revocation_during_model_call_blocks_result_publication(api):
    catalog(api); team = project(api)
    regulation = source(api, "REG-1", project_id=team)
    created = start(api, actor="bob", project_id=team)

    def revoked(system, user):
        current = api.storage.get("project:" + team, "document", regulation["document"]["id"])
        assert call(api, "PUT", "/documents/" + current["id"] + "/permissions", {
            "version": current["version"], "readRoles": ["owner"],
        }, project=team)[0] == 200
        return json.dumps({"summary": "private-model-answer", "findings": []}), {}, {}

    done, _ = run(api, created, model=revoked, owner="project:" + team)
    assert done["status"] == "failed"
    stored = api.storage.get("project:" + team, "docanalysis", created["analysis"]["id"])
    assert not stored.get("resultKey")
    assert result(api, created, actor="bob", project_id=team)[0] in (403, 409)


def test_result_read_rechecks_source_access_and_does_not_replay_other_owners(api):
    catalog(api); team = project(api)
    regulation = source(api, "REG-1", project_id=team)
    created = start(api, actor="bob", project_id=team)
    run(api, created, owner="project:" + team)
    current = api.storage.get("project:" + team, "document", regulation["document"]["id"])
    assert call(api, "PUT", "/documents/" + current["id"] + "/permissions", {
        "version": current["version"], "readRoles": ["owner"],
    }, project=team)[0] == 200
    assert result(api, created, actor="bob", project_id=team)[0] == 403
    assert call(api, "GET", "/jobs/" + created["job"]["id"], actor="bob", project=team)[0] == 403
    assert result(api, created, actor="dana")[0] in (403, 404)
    own = start(api, actor="dana", request="same-question")
    done, calls = run(api, own, owner="dana")
    assert done["status"] == "completed" and calls == []
    assert result(api, own, actor="dana")[1]["analysis"]["status"] == "needs_sources"


def test_unknown_model_references_are_not_published_as_verified_findings(api):
    catalog(api); source(api, "REG-1")
    created = start(api)

    def invented(system, user):
        return json.dumps({"summary": "확정", "findings": [
            {"nodeId": "DOC-forged", "reason": "미등록", "citationIds": ["E99"]},
        ]}), {}, {}

    run(api, created, model=invented)
    payload = result(api, created)[1]
    assert payload["result"]["findings"] == []
    assert payload["result"]["verification"]["references"] == "failed"
    assert "DOC-forged" not in repr(payload)


def test_decisions_require_business_review_role_and_leave_actor_bound_history(api):
    catalog(api); team = project(api); source(api, "REG-1", project_id=team)
    created = start(api, project_id=team); run(api, created, owner="project:" + team)
    payload = result(api, created, project_id=team)[1]
    route = "/impact-analyses/" + created["analysis"]["id"] + "/decisions"
    body = {"version": payload["analysis"]["version"], "nodeId": "DOC-1",
            "decision": "change_required", "note": "합성 검토 의견"}
    assert call(api, "POST", route, body, actor="dana", project=team)[0] == 403
    status, reviewed, _ = call(api, "POST", route, body, actor="bob", project=team)
    assert status == 200
    assert reviewed["decisions"][-1]["actorId"] == "bob"
    assert reviewed["decisions"][-1]["decision"] == "change_required"
    assert call(api, "POST", route, body, actor="bob", project=team)[0] == 409


def test_request_history_is_own_scoped_while_shared_bound_results_recheck_access(api):
    catalog(api); team = project(api); source(api, "REG-1", project_id=team)
    created = start(api, project_id=team)
    assert result(api, created, actor="bob", project_id=team)[0] == 403
    run(api, created, owner="project:" + team)
    assert result(api, created, actor="bob", project_id=team)[0] == 200
    mine = call(api, "GET", "/impact-analyses", project=team)[1]["analyses"]
    peer = call(api, "GET", "/impact-analyses", actor="bob", project=team)[1]["analyses"]
    assert [row["id"] for row in mine] == [created["analysis"]["id"]]
    assert peer == []


def test_new_approved_revision_blocks_inflight_publication(api):
    catalog(api); regulation = source(api, "REG-1")
    created = start(api)

    def changed(system, user):
        replace_source(api, regulation)
        return json.dumps({"summary": "old-input-result", "findings": []}), {}, {}

    done, _ = run(api, created, model=changed)
    assert done["status"] == "failed"
    stored = api.storage.get("alice", "docanalysis", created["analysis"]["id"])
    assert not stored.get("resultKey")


def test_completed_analysis_keeps_historical_links_but_stale_decisions_are_blocked(api):
    catalog(api); regulation = source(api, "REG-1")
    created = start(api); run(api, created)
    original = result(api, created)[1]["result"]["evidence"][0]
    replace_source(api, regulation)
    status, historical, _ = result(api, created)
    assert status == 200 and historical["staleSources"] == [regulation["document"]["id"]]
    assert historical["canDecide"] is False
    assert historical["result"]["evidence"][0] == original
    assert call(api, "GET", f"/documents/{original['documentId']}/revisions/{original['revisionId']}",
                query={"paragraph": original["paragraphId"]})[0] == 200
    assert call(api, "POST", "/impact-analyses/" + created["analysis"]["id"] + "/decisions", {
        "version": historical["analysis"]["version"], "nodeId": "DOC-1",
        "decision": "change_required", "note": "stale",
    })[0] == 409


def test_valid_ids_cannot_publish_model_urls_or_approval_declarations(api):
    catalog(api); source(api, "REG-1")
    created = start(api)

    def prohibited(system, user):
        return json.dumps({"summary": "검증 통과. 수정 확정. 실제 은행 정책으로 적용하세요.", "findings": [
            {"nodeId": "DOC-1", "reason": "https://untrusted.invalid/source ; s3://private/source",
             "citationIds": ["E1"]},
        ]}), {}, {}

    done, _ = run(api, created, model=prohibited)
    assert done["status"] == "completed"
    payload = result(api, created)[1]
    assert payload["analysis"]["status"] == "needs_review"
    assert payload["result"]["findings"] == []
    assert payload["result"]["verification"]["outputPolicy"] == "failed"
    assert "untrusted.invalid" not in repr(payload)
    assert "s3://private" not in repr(payload)
    assert "수정 확정" not in payload["result"]["summary"]


def test_selected_regulation_is_context_not_an_unreviewable_finding(api):
    catalog(api); source(api, "REG-1")
    created = start(api)

    def context_only(system, user):
        return json.dumps({"summary": "규정 검토", "findings": [
            {"nodeId": "REG-1", "reason": "규정 자체 검토", "citationIds": ["E1"]},
        ]}), {}, {}

    run(api, created, model=context_only)
    payload = result(api, created)[1]
    assert payload["result"]["findings"] == []
    assert payload["result"]["verification"]["references"] == "failed"


@pytest.mark.parametrize("prose,accepted", [
    ("https％3A％2F％2Funtrusted.invalid％2Fsource", False),
    ("원문 경로는 /policy.md 입니다.", False),
    ("Validation has passed.", False),
    ("All checks have passed. The analysis is approved.", False),
    ("검증 통과 여부는 담당자가 확인해야 합니다.", True),
    ("Validation has not passed.", True),
    ("The analysis has been verified.", False),
    ("분석이 검증되었습니다.", False),
    ("The analysis has not been verified.", True),
])
def test_output_policy_variants_are_enforced_before_persistence(api, prose, accepted):
    catalog(api); source(api, "REG-1")
    created = start(api)

    def answer(system, user):
        return json.dumps({"summary": prose, "findings": [
            {"nodeId": "DOC-1", "reason": "원문을 비교하여 검토하세요.", "citationIds": ["E1"]},
        ]}), {}, {}

    done, _ = run(api, created, model=answer)
    assert done["status"] == "completed"
    status, payload, _ = result(api, created)
    assert status == 200 and payload["analysis"]["status"] == "needs_review"
    value = payload["result"]
    assert value["verification"]["references"] == "checked"
    assert value["verification"]["outputPolicy"] == ("passed" if accepted else "failed")
    assert (value["summary"] == prose) is accepted
    assert bool(value["findings"]) is accepted
    assert value["evidence"]
