"""Actual human actions through B0/API and real local React evidence; AWS/model doubles."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_design_execution import pipeline, design_state, env, api, execute, ready
from workspace import ontology_schema as schema
from workspace.design_api import approve_variant, route, read_object
from workspace.design_execution import submit
from workspace.collaboration import CollaborationError
from workspace.execution_ledger import LedgerError
from workspace.storage import Conflict


def get_view(state, artifact_id, *, actor=None):
    scope = state.ctx.collaboration.resolve_scope(actor, state.ctx.project_id) if actor else state.ctx.scope
    claims = {**state.ctx.claims, "sub": actor} if actor else state.ctx.claims
    return route(state.ctx.host, scope, claims, "GET",
                 [state.design["id"], "artifacts", artifact_id, "interventions"], {}, {})[1]


def generate(state, **kwargs):
    ready(state)
    outcome, _ = execute(state, "generate", **kwargs)
    return outcome, state.ctx.get("design", state.design["id"])["variants"][-1]


def test_exact_approval_rejection_and_candidate_feedback_revoke_delivery_readiness(pipeline):
    state = pipeline
    outcome, variant = generate(state)
    assert outcome["status"] == "succeeded"
    empty = get_view(state, variant["artifactId"])
    assert empty["coverage"] == "complete" and empty["events"] == []
    design = state.ctx.get("design", state.design["id"])
    approve_variant(state.ctx, design["id"], {"version": design["version"],
        **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")}})
    release_outcome, _ = execute(state, "release", baseArtifactId=variant["artifactId"])
    assert release_outcome["status"] == "succeeded"
    design = state.ctx.get("design", design["id"])
    release_id = design["releases"][-1]["id"]
    before_calls = len(state.models), state.compiles, state.browsers
    generation = design["generation"]
    body = {"requestId": "reject-exact", "version": design["version"], "reason": "필수 고지를 더 명확하게 보여주세요.",
            **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")}}
    status, first = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [design["id"], "reject"], body, {})
    assert status == 200 and not first["design"]["approvals"].get("ux")
    assert state.ctx.get("run", variant["runId"])["approval"] is None
    again = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [design["id"], "reject"], body, {})[1]
    assert again == first
    view = get_view(state, variant["artifactId"])
    assert [event["kind"] for event in view["events"]] == ["approve", "reject"]
    assert view["editRequests"] == 0 and view["rejectionCount"] == 1
    assert view["events"][-1]["reason"] == body["reason"]
    assert view["rejectionCandidates"][0]["queueLabel"] == "업무 근거 연결 필요"
    assert all(node["reviewState"] == "candidate" for node in view["rejectionCandidates"][0]["nodes"])
    assert state.ctx.get("design", design["id"])["generation"] == generation
    assert (len(state.models), state.compiles, state.browsers) == before_calls
    assert "workspace/" not in json.dumps(view)
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET",
              [design["id"], "releases", release_id], {}, {})
    assert error.value.code == "not-found"
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST",
              [design["id"], "reject"], {**body, "reason": "다른 반려 사유"}, {})
    assert error.value.code == "request-changed"
    planner = get_view(state, variant["artifactId"], actor="bob")
    assert planner["rejectionCount"] == 1
    with pytest.raises(CollaborationError):
        route(state.ctx.host, state.ctx.collaboration.resolve_scope("bob", state.ctx.project_id),
              {**state.ctx.claims, "sub": "bob"}, "POST", [design["id"], "reject"],
              {**body, "requestId": "planner-reject"}, {})


def test_edit_action_is_recorded_atomically_at_admission_once_and_retains_exact_scope(pipeline, monkeypatch):
    from intake import prompts, review
    from design_loop.composition import walk
    from workspace.design_stages import unpacked
    state = pipeline
    _, variant = generate(state)
    artifact = state.ctx.get("wb_artifact", variant["artifactId"])
    generated = read_object(state.ctx, artifact["result"])["generated"]
    key, composition = next(iter(unpacked(generated["screens"]).items()))
    target = next(node["id"] for _, node, _ in walk(composition))
    instruction = "선택한 요소의 구성을 유지하세요."
    decision = prompts.admit_prompt_text(state.ctx.host, state.ctx.scope, instruction,
        purpose="edit-instruction", claims=state.ctx.claims)
    review.decide(state.ctx.host, state.ctx.collaboration.resolve_scope("bob", state.ctx.project_id),
                 decision["id"], approve=True, reason="synthetic instruction")
    design = state.ctx.get("design", state.design["id"])
    body = {"requestId": "edit-once", "version": design["version"], "baseArtifactId": variant["artifactId"],
            "instructionDecisionId": decision["id"], "screenId": key[0], "state": key[1], "targetId": target,
            "maxRounds": 1}
    original = state.storage.put_many
    def refuse(writes, *args, **kwargs):
        if any(write["kind"] == "job" and write["item"].get("operation") == "design.edit" for write in writes):
            raise Conflict("synthetic atomic refusal")
        return original(writes, *args, **kwargs)
    monkeypatch.setattr(state.storage, "put_many", refuse)
    with pytest.raises((Conflict, LedgerError)):
        submit(state.ctx, design["id"], "edit", body, state.ledger, state.config, lambda *args: None)
    assert state.ctx.get("design", design["id"])["interventions"] == []
    monkeypatch.setattr(state.storage, "put_many", original)
    queued = submit(state.ctx, design["id"], "edit", body, state.ledger, state.config, lambda *args: None)
    assert submit(state.ctx, design["id"], "edit", body, state.ledger, state.config, lambda *args: None)["jobId"] == queued["jobId"]
    admitted = state.ctx.get("design", design["id"])
    assert len(admitted["interventions"]) == 1
    event = admitted["interventions"][0]
    assert event["kind"] == "edit" and event["outcome"] == "submitted"
    assert event["actor"] == state.ctx.actor and event["baseArtifactId"] == variant["artifactId"]
    assert event["scopes"][0]["screenId"] == key[0] and event["scopes"][0]["targetId"] == target
    assert instruction not in json.dumps(admitted, ensure_ascii=False)
    job = state.ledger.dispatcher().allocate(state.ctx.owner, queued["jobId"], operation_id="allocate-edit-metric-" + "a" * 24)
    token, claims, key_check = state.caps.issue(job)
    state.ledger.dispatcher().bind_capability(state.ctx.owner, job["id"], job["attempt"]["id"], job["fence"],
        digest=hashlib.sha256(token.encode()).hexdigest(), claims=claims, key_check=key_check,
        operation_id="bind-edit-metric-" + "b" * 24)
    outcome = state.workflow.run({"capability": token, "executionId": job["id"],
        "runtimeSessionId": job["attempt"]["sessionId"], "workloadIdentity": "workload", "operation": "design.edit"},
        job["attempt"]["sessionId"])
    assert outcome["status"] == "succeeded"
    stored = state.ctx.get("design", design["id"])
    assert len(stored["interventions"]) == 1 and stored["interventions"][0]["outcome"] == "succeeded"
    edited = stored["variants"][-1]
    view = get_view(state, edited["artifactId"])
    assert view["editRequests"] == 1 and view["metrics"]["editsPerScreen"] == {body["screenId"]: 1}
    assert view["events"][0]["resultSourceHash"] == edited["sourceHash"]


def test_failed_round_checklist_and_rejection_details_follow_editor_permissions(pipeline, monkeypatch):
    state = pipeline
    actual = state.workflow.browser.evaluate
    def failed(*args, **kwargs):
        report = actual(*args, **kwargs)
        report["functionalStatus"] = "fail"
        report["passed"] = False
        return report
    monkeypatch.setattr(state.workflow.browser, "evaluate", failed)
    outcome, variant = generate(state, maxRounds=1)
    assert outcome["status"] == "needs_changes"
    view = get_view(state, variant["artifactId"])
    assert view["failureChecklist"] and any(not item["resolved"] for item in view["failureChecklist"])
    with pytest.raises(CollaborationError) as error:
        get_view(state, variant["artifactId"], actor="bob")
    assert error.value.code == "not-found"
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [state.design["id"], "reject"],
              {"requestId": "stale", "version": state.ctx.get("design", state.design["id"])["version"],
               "artifactId": variant["artifactId"], "sourceHash": "0" * 64,
               "bundleHash": variant["bundleHash"], "reason": "잘못된 버전"}, {})
    assert error.value.code == "design-review-stale"
