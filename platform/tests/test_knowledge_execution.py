"""Actual intake/CAS/RSA/model-boundary protocol; AWS and model transport doubles."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_design_execution import pipeline, design_state, env, api
from workspace import ontology_schema as schema
from workspace.knowledge_execution import submit
from workspace.knowledge_api import route
from workspace.ontology_store import Ontology
from workspace.design_stages import knowledge
from workspace.design_api import read_object
from workspace.collaboration import CollaborationError
from workspace.execution_ledger import LedgerError
from ontology_runtime.knowledge_workflow import KnowledgeWorkflow
from ontology_runtime.design_model import DesignModel
from engine import gate


@pytest.fixture
def knowledge_pipeline(pipeline, monkeypatch):
    state = pipeline
    original = state.ctx.get("design", state.design["id"])
    k = knowledge(read_object(state.ctx, original["input"])["snapshot"])
    state.target = k.aliases["terms-consent"]
    state.body = {"requestId": "learn-guidelines", "name": "guide-learning",
                  "decisionIds": original["decisionIds"], "nodeIds": [state.target],
                  "model": original["model"], "expectedGeneration": Ontology(state.ctx).current()["generation"]}
    state.bad_quote = False
    state.after_model = None
    state.model_error = False
    class Model(DesignModel):
        def generate(self, system, user, on_token=None):
            outer = self
            class Adapter:
                model_id = original["model"]
                tier = "0/1"
                endpoint = "offline-converse"
                def generate(self, system, user, **kwargs):
                    state.models.append(system)
                    page = outer.manifest["pages"][0]
                    answer = {"rules": [{"statement": "선택 자산은 승인된 원문 가이드를 따릅니다.",
                        "required": True, "severity": "critical", "appliesTo": [state.target],
                        "citation": {"sourceId": page["sourceRef"]["sourceId"], "page": page["page"],
                                     "quote": "원문에 없는 인용" if state.bad_quote else page["text"]}}]}
                    if state.after_model:
                        state.after_model()
                    if state.model_error:
                        raise TimeoutError("synthetic uncertain provider result")
                    return outer.refs.encode(json.dumps(answer, ensure_ascii=False)), {"inputTokens": 12, "outputTokens": 24}
            monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: Adapter())
            return super().generate(system, user, on_token)
    state.knowledge = KnowledgeWorkflow(state.workflow.gateway, state.workflow.signer, state.config, model_factory=Model)
    return state


def execute(state):
    queued = submit(state.ctx, state.body, state.ledger, state.config, lambda *args: None)
    job = state.ledger.dispatcher().allocate(state.ctx.owner, queued["job"]["id"], operation_id="knowledge-allocate-" + "a" * 24)
    token, claims, key = state.caps.issue(job)
    state.ledger.dispatcher().bind_capability(state.ctx.owner, job["id"], job["attempt"]["id"], job["fence"],
        digest=hashlib.sha256(token.encode()).hexdigest(), claims=claims, key_check=key,
        operation_id="knowledge-bind-" + "b" * 24)
    event = {"capability": token, "executionId": job["id"], "runtimeSessionId": job["attempt"]["sessionId"],
             "workloadIdentity": "workload", "operation": job["operation"]}
    return state.knowledge.run(event, job["attempt"]["sessionId"]), queued


def test_guideline_job_creates_cited_candidates_only_after_measured_single_model_call(knowledge_pipeline):
    state = knowledge_pipeline
    before = Ontology(state.ctx).current()["generation"]
    result, queued = execute(state)
    assert result["status"] == "succeeded"
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    assert artifact["status"] == "completed" and artifact["proposalStatus"] == "review"
    proposal = read_object(state.ctx, artifact["result"])
    assert proposal["approvalEffect"] is False and len(proposal["rules"]) == 1
    assert proposal["graph"]["nodes"][0]["reviewState"] == "candidate"
    assert proposal["rules"][0]["extraction"]["method"] == "model"
    assert len(state.models) == 1 and state.compiles == 0 and state.browsers == 0
    assert sum(state.charges.values()) == 36
    assert Ontology(state.ctx).current()["generation"] == before
    status, visible = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", [artifact["id"]], {}, {})
    assert status == 200 and visible["resultHash"] == artifact["result"]["sha256"]
    assert "workspace/" not in json.dumps(visible)


def test_unknown_quote_is_needs_changes_and_cannot_be_human_published(knowledge_pipeline):
    state = knowledge_pipeline
    state.bad_quote = True
    result, queued = execute(state)
    assert result["status"] == "needs_changes"
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    assert artifact["proposalStatus"] == "needs-changes"
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [artifact["id"], "publish"],
              {"requestId": "bad-adoption", "version": artifact["version"],
               "resultHash": artifact["result"]["sha256"], "expectedGeneration": state.body["expectedGeneration"]}, {})
    assert error.value.code == "knowledge-review-required"


def test_guideline_adoption_publishes_exact_candidates_and_policy_still_needs_human_review(knowledge_pipeline):
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    body = {"requestId": "adopt-guide", "version": artifact["version"],
            "resultHash": artifact["result"]["sha256"], "expectedGeneration": state.body["expectedGeneration"]}
    status, result = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST",
                           [artifact["id"], "publish"], body, {})
    assert status == 201 and result["approvalEffect"] is False
    rid = next(iter(result["publication"]["identities"].values()))
    node = Ontology(state.ctx).read([rid])["nodes"][0]
    assert node["reviewState"] == "candidate" and node["properties"]["extraction"]["method"] == "model"
    assert node["properties"]["citation"]["derivativeHash"]
    assert len(state.models) == 1


def test_project_cohort_and_target_approval_block_before_model_or_job(knowledge_pipeline):
    state = knowledge_pipeline
    before = copy.deepcopy(state.storage.list(state.ctx.owner, "job"))
    for body, configuration, code in [
        ({**state.body, "model": "not-enabled"}, state.config, "knowledge-execution-unavailable"),
        (state.body, {**state.config, "design": {"projects": [], "models": []}}, "knowledge-execution-unavailable"),
    ]:
        with pytest.raises(LedgerError) as error:
            submit(state.ctx, body, state.ledger, configuration, lambda *args: None)
        assert error.value.code == code
    assert state.models == [] and state.storage.list(state.ctx.owner, "job") == before


def test_http_submission_preserves_cohort_and_replay_errors(knowledge_pipeline, env, monkeypatch):
    from ontology_runtime import execution_entrypoints
    state = knowledge_pipeline
    config = {**state.config, "design": {"projects": [], "models": []}}
    monkeypatch.setattr(execution_entrypoints, "knowledge_operation",
                        lambda ctx, body: submit(ctx, body, state.ledger, config, lambda *args: None))

    def post(body):
        response = env.api.handle({"rawPath": "/studio-api/ontology/knowledge/guidelines",
            "headers": {"X-Workspace-Project": env.pid}, "body": json.dumps(body),
            "requestContext": {"http": {"method": "POST"},
                "authorizer": {"jwt": {"claims": {**state.ctx.claims, "token_use": "access"}}}}})
        return response["statusCode"], json.loads(response["body"])

    status, response = post(state.body)
    assert (status, response["code"]) == (503, "knowledge-execution-unavailable")
    config = state.config
    assert post(state.body)[0] == 202
    status, response = post({**state.body, "name": "changed"})
    assert (status, response["code"]) == (409, "request-changed")
    assert len([job for job in state.storage.list(state.ctx.owner, "job")
                if job.get("operation") == "design.guidelines"]) == 1 and not state.models


def test_revoked_source_during_call_never_publishes_success(knowledge_pipeline, env):
    from intake.records import INTAKE_OWNER
    state = knowledge_pipeline
    def revoke():
        grant = state.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
        env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    state.after_model = revoke
    with pytest.raises(Exception):
        execute(state)
    assert len(state.models) == 1
    artifact = state.storage.list(state.ctx.owner, "wb_artifact")[0]
    assert artifact["status"] != "completed" and not artifact.get("result")


def adoption_body(state, artifact, **overrides):
    return {"requestId": "adopt-guide", "version": artifact["version"],
            "resultHash": artifact["result"]["sha256"],
            "expectedGeneration": state.body["expectedGeneration"], **overrides}


def test_membership_change_at_adoption_commit_blocks_every_canonical_write(knowledge_pipeline, env, monkeypatch):
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    before = Ontology(state.ctx).current()["generation"]
    put_many = state.storage.put_many
    raced = []

    def commit_after_demotion(writes, *args, **kwargs):
        if any(write["kind"] == "knowledge_decision" for write in writes) and not raced:
            raced.append(True)
            project = state.storage.get(state.ctx.owner, "project", env.pid)
            members = copy.deepcopy(project["members"])
            members["carol"]["role"] = "developer"
            status, _ = env.http("PUT", f"/projects/{env.pid}/members",
                                 {"version": project["version"], "members": members})
            assert status == 200
        return put_many(writes, *args, **kwargs)

    monkeypatch.setattr(state.storage, "put_many", commit_after_demotion)
    with pytest.raises(CollaborationError) as error:
        route(env.api, env.scope("carol"), {"sub": "carol", "exp": state.ctx.claims["exp"]},
              "POST", [artifact["id"], "publish"], adoption_body(state, artifact), {})
    assert error.value.status == 409 and raced
    assert Ontology(state.ctx).current()["generation"] == before
    assert not state.storage.list(state.ctx.owner, "knowledge_decision")
    assert len(state.models) == 1


def test_submission_replay_keeps_one_job_and_changed_request_cannot_reuse_it(knowledge_pipeline):
    state = knowledge_pipeline
    enqueued = []
    first = submit(state.ctx, state.body, state.ledger, state.config, lambda *args: enqueued.append(args))
    second = submit(state.ctx, state.body, state.ledger, state.config, lambda *args: enqueued.append(args))
    assert first == second and len(enqueued) == 2 and enqueued[0] == enqueued[1]
    assert len([j for j in state.storage.list(state.ctx.owner, "job") if j.get("operation") == "design.guidelines"]) == 1
    with pytest.raises(LedgerError) as error:
        submit(state.ctx, {**state.body, "name": "changed"}, state.ledger, state.config, lambda *args: None)
    assert error.value.code == "request-changed" and state.models == []


@pytest.mark.parametrize("actor,readable", [("alice", True), ("carol", True), ("dana", True), ("bob", False)])
def test_read_and_adoption_follow_current_project_roles(knowledge_pipeline, env, actor, readable):
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    claims = {"sub": actor, "exp": state.ctx.claims["exp"]}
    scope = env.scope(actor)
    before = Ontology(state.ctx).current()["generation"]
    if not readable:
        with pytest.raises(CollaborationError) as error:
            route(env.api, scope, claims, "GET", [artifact["id"]], {}, {})
        assert error.value.status == 403
    else:
        assert route(env.api, scope, claims, "GET", [artifact["id"]], {}, {})[0] == 200
    if actor in {"bob", "dana"}:
        with pytest.raises(CollaborationError) as error:
            route(env.api, scope, claims, "POST", [artifact["id"], "publish"], adoption_body(state, artifact), {})
        assert error.value.status == 403
        assert Ontology(state.ctx).current()["generation"] == before
    assert len(state.models) == 1


@pytest.mark.parametrize("fault", ["hash", "version", "generation", "bytes"])
def test_human_adoption_refuses_changed_evidence_without_canonical_write(knowledge_pipeline, fault):
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    body = adoption_body(state, artifact)
    before = Ontology(state.ctx).current()["generation"]
    if fault == "hash":
        body["resultHash"] = "0" * 64
    elif fault == "version":
        body["version"] += 1
    elif fault == "generation":
        body["expectedGeneration"] = "0" * 64
    else:
        state.storage.put_blob(artifact["result"]["key"], b"{}", "application/json")
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [artifact["id"], "publish"], body, {})
    assert error.value.status == 409
    assert Ontology(state.ctx).current()["generation"] == before
    assert not state.storage.list(state.ctx.owner, "knowledge_decision") and len(state.models) == 1


def test_completed_evidence_survives_job_ttl_and_adoption_replay(knowledge_pipeline, monkeypatch):
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    get = state.storage.get
    monkeypatch.setattr(state.storage, "get", lambda owner, kind, identifier:
        None if kind == "job" and identifier == artifact["jobId"] else get(owner, kind, identifier))
    args = state.ctx.host, state.ctx.scope, state.ctx.claims
    assert route(*args, "GET", [artifact["id"]], {}, {})[0] == 200
    first = route(*args, "POST", [artifact["id"], "publish"], adoption_body(state, artifact), {})
    generation = Ontology(state.ctx).current()["generation"]
    assert route(*args, "GET", [artifact["id"]], {}, {})[0] == 200
    assert route(*args, "POST", [artifact["id"], "publish"], adoption_body(state, artifact), {}) == first
    assert Ontology(state.ctx).current()["generation"] == generation
    assert len(state.storage.list(state.ctx.owner, "knowledge_decision")) == 1 and len(state.models) == 1


def test_revoked_completed_evidence_is_hidden_before_private_result_reads(knowledge_pipeline, env, monkeypatch):
    from intake.records import INTAKE_OWNER
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    grant = state.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    get_blob = state.storage.get_blob
    protected = {artifact["input"]["ref"], artifact["result"]["key"]}
    def observed(key, *args, **kwargs):
        assert key not in protected, "revoked knowledge evidence was opened"
        return get_blob(key, *args, **kwargs)
    monkeypatch.setattr(state.storage, "get_blob", observed)
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", [artifact["id"]], {}, {})
    assert error.value.status == 404
    assert route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", [], {}, {})[1]["items"] == []


def test_singular_read_preserves_authorization_expiry_after_result_read(knowledge_pipeline, monkeypatch):
    state = knowledge_pipeline
    _, queued = execute(state)
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    get_blob = state.storage.get_blob

    def expire_after_read(key, *args, **kwargs):
        value = get_blob(key, *args, **kwargs)
        if key == artifact["result"]["key"]:
            monkeypatch.setattr(state.storage, "clock", lambda: state.ctx.claims["exp"] * 1000)
        return value

    monkeypatch.setattr(state.storage, "get_blob", expire_after_read)
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", [artifact["id"]], {}, {})
    assert (error.value.status, error.value.code) == (401, "authorization-expired")


def test_uncertain_provider_result_keeps_intent_and_never_replays_model(knowledge_pipeline):
    state = knowledge_pipeline
    state.model_error = True
    with pytest.raises(TimeoutError):
        execute(state)
    job = next(j for j in state.storage.list(state.ctx.owner, "job") if j.get("operation") == "design.guidelines")
    assert job["status"] == "failed" and job["unknownOutcome"] is True
    assert len(job["calls"]) == 1 and job["calls"][0]["status"] == "intent"
    artifact = state.ctx.get("wb_artifact", job["artifact"]["id"])
    assert artifact["status"] != "completed" and not artifact.get("result")
    submit(state.ctx, state.body, state.ledger, state.config, lambda *args: pytest.fail("terminal job requeued"))
    assert len(state.models) == 1
