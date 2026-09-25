"""Pension/report contracts use actual private storage and deterministic facts."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_storage import FakeS3, FakeTable
from workspace.storage import Storage
from workspace.collaboration import Collaboration, CollaborationError
from test_workbench_core import wb, call as project_call, source, documents, queue, run


def setup():
    from workbench.business import route
    from workspace.http import WorkspaceAPI
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test",
                      clock=lambda: 1_800_000_000_000)
    api = WorkspaceAPI(storage=storage)
    scope = api.collaboration.resolve_scope("tester", None)
    return route, api, scope


def call(route, api, scope, method, path, body=None):
    result = route(api, scope, {"sub": scope["actor"], "token_use": "access"},
                   method, path.split("/"), body or {}, {})
    assert result is not None
    return result[1]


def test_pension_projection_preserves_principal_and_explicit_assumptions():
    from workbench.business import calculate, PERSONAS
    result = calculate(PERSONAS[0], {"annualReturnBps": 0, "yearsToRetirement": 10,
                                   "monthlyContribution": 300000, "monthlyTarget": 2000000,
                                   "withdrawalYears": 20})
    principal = sum(a["balance"] for a in PERSONAS[0]["accounts"])
    assert result["projectedBalance"] == principal + 300000 * 120
    assert result["monthlyPension"] == round(result["projectedBalance"] / 240)
    assert result["monthlyGap"] == max(2000000 - result["monthlyPension"], 0)
    assert result["calculationVersion"] and result["caveats"]
    assert result["assumptions"]["annualReturnBps"] == 0


@pytest.mark.parametrize("bad", [float("nan"), True, -100, "300000"])
def test_pension_rejects_invalid_money(bad):
    from workbench.business import calculate, PERSONAS
    with pytest.raises(ValueError):
        calculate(PERSONAS[0], {"monthlyContribution": bad})


def test_sessions_are_owner_scoped_and_retry_safe():
    route, api, scope = setup()
    body = {"requestId": "same", "personaId": "starter"}
    first = call(route, api, scope, "POST", "pension/sessions", body)["session"]
    second = call(route, api, scope, "POST", "pension/sessions", body)["session"]
    assert first["id"] == second["id"]
    other = api.collaboration.resolve_scope("other", None)
    with pytest.raises(CollaborationError):
        call(route, api, other, "GET", f"pension/sessions/{first['id']}")
    with pytest.raises(CollaborationError):
        call(route, api, scope, "POST", "pension/sessions",
             {"requestId": "same", "personaId": "retiring"})


def test_assumption_change_invalidates_old_answer_evidence():
    route, api, scope = setup()
    session = call(route, api, scope, "POST", "pension/sessions",
                   {"requestId": "case", "personaId": "starter"})["session"]
    answer = call(route, api, scope, "POST", f"pension/sessions/{session['id']}/ask",
                  {"version": session["version"], "topic": "planning",
                   "question": "은퇴 준비 부족분을 알려주세요.", "mode": "baseline"})
    assert answer["answer"]["mode"] == "baseline"
    previous_hash = answer["answer"]["factHash"]
    updated = call(route, api, scope, "POST", f"pension/sessions/{session['id']}/calculate",
                   {"version": answer["session"]["version"],
                    "assumptions": {"monthlyContribution": 600000}})["session"]
    assert updated["factHash"] != previous_hash
    assert updated["answers"][0]["factHash"] == previous_hash
    assert updated["answers"][0]["stale"] is True


def test_feedback_requires_a_real_answer_and_valid_rating():
    route, api, scope = setup()
    session = call(route, api, scope, "POST", "pension/sessions",
                   {"requestId": "feedback", "personaId": "starter"})["session"]
    with pytest.raises(CollaborationError):
        call(route, api, scope, "POST", f"pension/sessions/{session['id']}/feedback",
             {"answerId": "invented", "rating": 5})


def test_model_placeholders_reject_invented_numbers_and_unknown_metrics():
    from workbench.business import render_model_answer
    facts = {"monthlyGap": 1500000, "projectedBalance": 120000000}
    assert "1,500,000" in render_model_answer(
        '{"text":"가정 기준 부족분은 {{monthlyGap}}원입니다.","metricIds":["monthlyGap"]}', facts)
    for text in ['{"text":"부족분은 999만원입니다.","metricIds":["monthlyGap"]}',
                 '{"text":"{{unknown}}원입니다.","metricIds":["unknown"]}',
                 '{"text":"{{monthlyGap}}원이며 백만 원을 더 받습니다.","metricIds":["monthlyGap"]}']:
        with pytest.raises(ValueError):
            render_model_answer(text, facts)


@pytest.mark.parametrize("surrounding", [
    "월 부족분은 -{{monthlyGap}}원입니다.",
    "월 부족분은 {{monthlyGap}}달러입니다.",
    "월 연금 수령액은 {{monthlyGap}}원입니다.",
])
def test_model_cannot_change_financial_sign_unit_or_metric_label(surrounding):
    import json
    from workbench.business import render_model_answer
    rendered = render_model_answer(json.dumps({"text": surrounding, "metricIds": ["monthlyGap"]}),
                                   {"monthlyGap": 1500000})
    assert "목표 대비 월 부족분: 1,500,000원" in rendered
    assert "-1,500,000" not in rendered and "달러" not in rendered and "월 연금 수령액" not in rendered


def test_feedback_does_not_persist_free_text_without_private_processing(monkeypatch):
    from common import privacy
    route, api, scope = setup()
    session = call(route, api, scope, "POST", "pension/sessions",
                   {"requestId": "private-feedback", "personaId": "starter"})["session"]
    answered = call(route, api, scope, "POST", f"pension/sessions/{session['id']}/ask",
                    {"version": session["version"], "topic": "diagnosis"})
    path = f"pension/sessions/{session['id']}/feedback"
    def unavailable(*args):
        raise privacy.PrivacyUnavailable()
    monkeypatch.setattr(privacy, "process", unavailable)
    with pytest.raises(CollaborationError) as error:
        call(route, api, scope, "POST", path, {"rating": 4, "answerId": answered["answer"]["id"],
                                              "comment": "Synthetic phone 010-1234-5678"})
    assert error.value.status == 503
    assert api.storage.get(scope["owner"], "wb_pension", session["id"])["feedback"] == []
    saved = call(route, api, scope, "POST", path, {"rating": 4, "answerId": answered["answer"]["id"],
                                                  "commentCode": "clear"})["feedback"]
    assert saved["commentMode"] == "structured"
    assert saved["comment"] == "계산 가정과 근거가 명확합니다."
    monkeypatch.setattr(privacy, "process", lambda *args: {"text": "연락처 삭제", "evidence": {"processor": "test-private-adapter"}})
    call(route, api, scope, "POST", path, {"rating": 4, "answerId": answered["answer"]["id"],
                                          "comment": "Synthetic phone 010-1234-5678"})
    stored = api.storage.get(scope["owner"], "wb_pension", session["id"])
    assert stored["feedback"][0]["comment"] == "연락처 삭제"
    assert "010-1234-5678" not in repr(stored)


def test_change_reports_enforce_transitive_source_acl_on_every_read_and_approval(wb):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner"]
    docs[0]["entities"][1]["title"] = "PRIVATE_SCREEN_SYNTHETIC_MARKER"
    run(wb, queue(wb, src, docs))
    change = project_call(wb, "POST", "changes", {
        "requestId": "restricted-change", "title": "Synthetic change", "targetId": "guide",
        "changeType": "update", "before": "old", "after": "new", "reason": "synthetic"})["change"]
    project_call(wb, "POST", f"changes/{change['id']}/analyze", {"version": change["version"]})
    body = {"requestId": "restricted-report", "type": "change-impact", "title": "Synthetic report", "changeId": change["id"]}
    with pytest.raises(CollaborationError):
        project_call(wb, "POST", "reports", body, actor="carol")
    report = project_call(wb, "POST", "reports", body)["report"]
    with pytest.raises(CollaborationError):
        project_call(wb, "POST", "reports", body, actor="carol")
    with pytest.raises(CollaborationError):
        project_call(wb, "GET", f"reports/{report['id']}/document", actor="carol")
    assert project_call(wb, "GET", "reports", actor="carol")["items"] == []
    assert project_call(wb, "GET", "overview", actor="carol")["stats"]["reports"] == 0
    report = project_call(wb, "POST", f"reports/{report['id']}/approve",
                          {"version": report["version"], "contentHash": report["contentHash"]})["report"]
    assert report["status"] == "approved"
    current = wb.storage.get(wb.owner, "wb_source", src["id"])
    wb.storage.put(wb.owner, "wb_source", {**current, "status": "failed",
        "permissionVersion": current["permissionVersion"] + 1}, current["version"])
    assert project_call(wb, "GET", "reports")["items"] == []
    with pytest.raises(CollaborationError):
        project_call(wb, "GET", f"reports/{report['id']}/document")
    with pytest.raises(CollaborationError):
        project_call(wb, "POST", f"reports/{report['id']}/approve",
                     {"version": report["version"], "contentHash": report["contentHash"]})


def test_direct_knowledge_report_preserves_historical_audience_after_public_revision(wb):
    src = source(wb)
    original = [{"id": "policy", "title": "Policy", "revision": "1",
                 "content": "Historical restricted marker", "allowedRoles": ["owner"]}]
    run(wb, queue(wb, src, original))
    identifier = project_call(wb, "GET", "knowledge")["items"][0]["id"]
    body = {"requestId": "historical-direct", "type": "regulation",
            "title": "Historical report", "evidenceIds": [identifier]}
    report = project_call(wb, "POST", "reports", body)["report"]
    public = [{"id": "policy", "title": "Policy", "revision": "2",
               "content": "Current shared guidance", "allowedRoles": ["owner", "designer"]}]
    run(wb, queue(wb, src, public, request="publish-current"))
    assert project_call(wb, "GET", f"knowledge/{identifier}", actor="carol")["document"]["content"] == "Current shared guidance"
    with pytest.raises(CollaborationError):
        project_call(wb, "GET", f"reports/{report['id']}/document", actor="carol")
    with pytest.raises(CollaborationError):
        project_call(wb, "POST", "reports", body, actor="carol")
    assert project_call(wb, "GET", "reports", actor="carol")["items"] == []
    assert project_call(wb, "GET", "overview", actor="carol")["stats"]["reports"] == 0
    assert "Historical restricted marker" in project_call(wb, "GET", f"reports/{report['id']}/document")["markdown"]
    fresh = project_call(wb, "POST", "reports", {**body, "requestId": "current-direct"}, actor="carol")["report"]
    assert "Current shared guidance" in project_call(wb, "GET", f"reports/{fresh['id']}/document", actor="carol")["markdown"]


def test_report_approval_rechecks_source_expiry_after_transaction_preparation(wb, monkeypatch):
    wb.storage.clock = lambda: wb.now
    src = source(wb)
    run(wb, queue(wb, src))
    current = wb.storage.get(wb.owner, "wb_source", src["id"])
    wb.now = current["accessExpiresAt"] - 1000
    identifier = project_call(wb, "GET", "knowledge")["items"][0]["id"]
    report = project_call(wb, "POST", "reports", {"requestId": "deadline-report", "type": "regulation",
        "title": "Bound source deadline", "evidenceIds": [identifier]})["report"]
    original = wb.storage._prepare

    def expire(owner, kind, item, version, now):
        prepared = original(owner, kind, item, version, now)
        if kind == "wb_report" and item.get("status") == "approved":
            wb.now += 2000
        return prepared

    monkeypatch.setattr(wb.storage, "_prepare", expire)
    with pytest.raises(CollaborationError) as error:
        project_call(wb, "POST", f"reports/{report['id']}/approve",
                     {"version": report["version"], "contentHash": report["contentHash"]})
    assert error.value.code == "stale-evidence"
    assert wb.storage.get(wb.owner, "wb_report", report["id"])["status"] == "draft"


def test_draft_report_creation_fences_source_expiry_before_publication(wb, monkeypatch):
    wb.storage.clock = lambda: wb.now
    src = source(wb)
    run(wb, queue(wb, src))
    current = wb.storage.get(wb.owner, "wb_source", src["id"])
    wb.now = current["accessExpiresAt"] - 1000
    identifier = project_call(wb, "GET", "knowledge")["items"][0]["id"]
    original = wb.storage._prepare

    def expire(owner, kind, item, version, now):
        result = original(owner, kind, item, version, now)
        if kind == "wb_report":
            wb.now += 2000
        return result

    monkeypatch.setattr(wb.storage, "_prepare", expire)
    with pytest.raises(CollaborationError) as error:
        project_call(wb, "POST", "reports", {"requestId": "expired-draft", "type": "regulation",
            "title": "Source expiry during draft publication", "evidenceIds": [identifier]})
    assert error.value.code == "stale-evidence"
    assert wb.storage.list_page(wb.owner, "wb_report")["items"] == []


def test_report_creation_compares_the_actual_snapshot_rendered_into_markdown(wb, monkeypatch):
    from workbench import business
    change = project_call(wb, "POST", "changes", {"requestId": "render-race", "title": "Original title",
        "targetId": "target", "changeType": "update", "before": "", "after": "", "reason": "Synthetic"})["change"]
    render = business._report_sources

    def update_after_render(api, scope, claims, body):
        result = render(api, scope, claims, body)
        current = wb.storage.get(wb.owner, "wb_change", change["id"])
        wb.storage.put(wb.owner, "wb_change", {**current, "title": "Concurrent update"}, current["version"])
        return result

    monkeypatch.setattr(business, "_report_sources", update_after_render)
    with pytest.raises(CollaborationError) as error:
        project_call(wb, "POST", "reports", {"requestId": "render-race-report", "type": "change-impact",
            "changeId": change["id"]})
    assert error.value.code == "source-changed"
    assert wb.storage.list_page(wb.owner, "wb_report")["items"] == []


def test_approval_fences_a_source_changed_during_commit(monkeypatch):
    route, api, scope = setup()
    session = call(route, api, scope, "POST", "pension/sessions",
                   {"requestId": "race-source", "personaId": "starter"})["session"]
    report = call(route, api, scope, "POST", "reports",
                  {"requestId": "race-report", "type": "management",
                   "sessionId": session["id"]})["report"]
    original = api.storage.put_many
    def concurrent_write(writes, checks=None, **kwargs):
        current = api.storage.get(scope["owner"], "wb_pension", session["id"])
        api.storage.put(scope["owner"], "wb_pension", {**current, "title": "revised"}, current["version"])
        return original(writes, checks=checks, **kwargs)
    monkeypatch.setattr(api.storage, "put_many", concurrent_write)
    with pytest.raises(CollaborationError):
        call(route, api, scope, "POST", f"reports/{report['id']}/approve",
             {"version": report["version"], "contentHash": report["contentHash"]})


def test_pension_report_approval_is_exact_and_stale_sources_cannot_approve():
    route, api, scope = setup()
    session = call(route, api, scope, "POST", "pension/sessions",
                   {"requestId": "report-source", "personaId": "starter"})["session"]
    report = call(route, api, scope, "POST", "reports",
                  {"requestId": "report", "type": "pension-evaluation",
                   "title": "합성 연금 검토", "sessionId": session["id"]})["report"]
    with pytest.raises(CollaborationError):
        call(route, api, scope, "POST", f"reports/{report['id']}/approve",
             {"version": report["version"], "contentHash": "0" * 64})
    call(route, api, scope, "POST", f"pension/sessions/{session['id']}/calculate",
         {"version": session["version"], "assumptions": {"monthlyContribution": 500000}})
    with pytest.raises(CollaborationError):
        call(route, api, scope, "POST", f"reports/{report['id']}/approve",
             {"version": report["version"], "contentHash": report["contentHash"]})
