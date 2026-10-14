"""Real admission, parser, signed execution and human CAS; offline AWS/model transports."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_execution_integration import integrated, env, api, run
from test_intake_collection import admitted_collection
from workspace.execution_source import submit as analyze
from workspace.knowledge_execution import submit
from workspace.knowledge_api import route
from workspace.design_api import read_object
from workspace.ontology_store import Ontology
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.execution_ledger import CostGuardGate, LedgerError
from ontology_runtime.knowledge_workflow import KnowledgeWorkflow
from ontology_runtime.design_model import DesignModel
from engine import gate
from workspace.derive_proposals import aliases


@pytest.fixture
def derivation(integrated, monkeypatch, request):
    state = integrated
    state.ledger.api().cancel(state.owner, state.job_id, actor="alice")
    mode = getattr(request, "param", "sequence")
    names = ("Heading", "Body", "Layout") if mode == "template" else ("Heading", "Body")
    jsx = "<Layout><Heading/><Body/></Layout>" if mode == "template" else "<><Heading/><Body/></>"
    imports = ", ".join(names)
    decision, _ = admitted_collection(state.env, {
        "src/parts.tsx": "".join(f"export const {name} = () => null;\n" for name in names),
        "src/First.tsx": f'import {{ {imports} }} from "./parts";\nexport const First = () => {jsx};\n',
        "src/Second.tsx": f'import {{ {imports} }} from "./parts";\nexport const Second = () => {jsx};\n',
    }, setup=False)
    state.decision = decision
    queued = analyze(state.ctx, {"requestId": "repeat-source", "name": "repeat-source",
        "collectionDecisionId": decision["id"]}, state.ledger, state.config, lambda *args: None)
    state.job_id, state.artifact_id = queued["job"]["id"], queued["artifact"]["id"]
    assert run(state)["status"] == "succeeded"
    artifact = state.ctx.get("wb_artifact", state.artifact_id)
    store = Ontology(state.ctx)
    source = artifact["sourceRefs"][0]
    nodes = store.read(limit=100)["nodes"]
    code = {node["properties"]["path"]: node for node in nodes if node["type"] == "CodeFile"}
    base = {"scope": {"kind": "project", "projectId": state.ctx.project_id}, "revision": 1,
        "sourceRefs": [source], "provenance": "declared", "reviewState": "candidate", "tombstone": False}
    declared = [schema.seal({**base, "id": "part-" + name.lower(),
        "type": "Molecule" if mode == "organism" and name == "Heading" else "Atom", "title": name,
        "properties": {"uxModel": {"layers": {"code": {"importPath": "src/parts.tsx",
            "exportName": name, "props": {}}}}}}) for name in names]
    declared += [schema.seal({**base, "id": "screen-" + name.lower(), "type": "Screen", "title": name,
        "properties": {}}) for name in ("First", "Second")]
    edges = [schema.seal({"id": "impl-" + name.lower(), "type": "IMPLEMENTS",
        "src": {"id": code[f"src/{name}.tsx"]["id"], "revision": 1},
        "dst": {"id": "screen-" + name.lower(), "revision": 1}, "sourceRefs": [source],
        "provenance": "declared", "reviewState": "candidate", "tombstone": False}) for name in ("First", "Second")]
    # Connect approved declared atoms into each selected Screen's bounded context.
    edges += [schema.seal({"id": f"uses-{screen.lower()}-{name.lower()}", "type": "COMPOSES",
        "src": {"id": "screen-" + screen.lower(), "revision": 1},
        "dst": {"id": "part-" + name.lower(), "revision": 1}, "sourceRefs": [source],
        "provenance": "declared", "reviewState": "candidate", "tombstone": False})
        for screen in ("First", "Second") for name in names]
    state.ids = store.publish_candidate("screen-review", {"schemaVersion": 1, "projectId": state.ctx.project_id,
        "nodes": declared, "edges": edges}, expected_generation=store.current()["generation"],
        request_id="screen-review")["identities"]
    state.code = code
    for node in [*code.values(), *declared]:
        identifier = state.ids.get(node["id"], node["id"])
        for decision_name in ("reviewed", "approved"):
            store.review_node(identifier, expected_generation=store.current()["generation"], revision=1,
                decision=decision_name, reason="offline exact source review", request_id=decision_name + "-" + node["id"])
    state.config["design"] = {"projects": [state.ctx.project_id], "models": ["global.anthropic.claude-sonnet-5"]}
    state.body = {"requestId": "derive-repeat", "name": "repeat-candidates",
        "analysisId": state.artifact_id, "decisionIds": [decision["id"]],
        "nodeIds": [state.ids["screen-first"], state.ids["screen-second"]],
        "model": state.config["design"]["models"][0], "expectedGeneration": store.current()["generation"]}
    state.calls, state.charges, state.after_model = [], {}, None
    state.answer = lambda candidates: {"proposals": [{"id": alias, "title": "반복 안내 구성",
        "intent": "화면에 일관된 안내 구조를 제공합니다."} for alias in aliases(candidates)]}
    monkeypatch.setenv("BEDROCK_READ_TIMEOUT", "180")
    monkeypatch.setattr(CostGuardGate, "record", lambda self, tokens, charge_id: state.charges.setdefault(charge_id, tokens))
    class Model(DesignModel):
        def generate(self, system, user, on_token=None):
            outer = self
            class Adapter:
                model_id = state.body["model"]
                tier = "0/1"
                endpoint = "offline-converse"
                def generate(self, system, user, **kwargs):
                    state.calls.append(user)
                    answer = state.answer(outer.manifest["derivation"]["evidence"]["candidates"])
                    if state.after_model:
                        state.after_model()
                    return outer.refs.encode(json.dumps(answer, ensure_ascii=False)), {"inputTokens": 12, "outputTokens": 24}
            monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: Adapter())
            return super().generate(system, user, on_token)
    state.knowledge = KnowledgeWorkflow(state.workflow.gateway, state.workflow.signer, state.config, model_factory=Model)
    return state


def execute(state):
    queued = submit(state.ctx, state.body, state.ledger, state.config, lambda *args: None, operation="design.derive")
    job = state.ledger.dispatcher().allocate(state.owner, queued["job"]["id"], operation_id="derive-allocate-" + "a" * 24)
    token, claims, key = state.capabilities.issue(job)
    state.ledger.dispatcher().bind_capability(state.owner, job["id"], job["attempt"]["id"], job["fence"],
        digest=hashlib.sha256(token.encode()).hexdigest(), claims=claims, key_check=key,
        operation_id="derive-bind-" + "b" * 24)
    event = {"capability": token, "executionId": job["id"], "runtimeSessionId": job["attempt"]["sessionId"],
        "workloadIdentity": "workload", "operation": job["operation"]}
    result = state.knowledge.run(event, job["attempt"]["sessionId"])
    return result, state.ctx.get("wb_artifact", queued["artifact"]["id"])


def test_measured_runtime_mines_independent_screens_and_human_adopts_candidates(derivation):
    state = derivation
    generation = Ontology(state.ctx).current()["generation"]
    result, artifact = execute(state)
    assert result["status"] == "succeeded"
    checked = read_object(state.ctx, artifact["result"])
    assert len(state.calls) == 1 and sum(state.charges.values()) == 36
    assert checked["proposals"][0]["fileSupport"] == checked["proposals"][0]["screenSupport"] == 2
    assert checked["coverage"]["runtimeCompositionObserved"] is False and checked["approvalEffect"] is False
    assert Ontology(state.ctx).current()["generation"] == generation
    status, publication = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [artifact["id"], "publish"],
        {"requestId": "adopt-repeat", "version": artifact["version"], "resultHash": artifact["result"]["sha256"],
         "expectedGeneration": generation}, {})
    assert status == 201 and publication["approvalEffect"] is False
    created = Ontology(state.ctx).read(list(publication["publication"]["identities"].values()))["nodes"]
    assert created and all(node["reviewState"] == "candidate" for node in created)


@pytest.mark.parametrize("derivation", ["template", "organism"], indirect=True)
def test_template_slots_and_higher_composition_are_mined_without_inventing_code(derivation):
    state = derivation
    result, artifact = execute(state)
    assert result["status"] == "succeeded" and len(state.calls) == 1
    checked = read_object(state.ctx, artifact["result"])
    nodes = checked["graph"]["nodes"]
    templates = [node for node in nodes if node["type"] == "PageTemplate"]
    if templates:
        ux = templates[0]["properties"]["uxModel"]
        assert ux["slots"] and "code" not in ux.get("layers", {})
        assert all(slot["allowed"] for slot in ux["slots"].values())
    else:
        assert any(node["type"] == "Organism" for node in nodes)


def test_several_files_for_one_screen_are_not_independent_evidence(derivation):
    state = derivation
    state.body["nodeIds"] = state.body["nodeIds"][:1]
    result, artifact = execute(state)
    assert result["status"] == "needs_changes"
    checked = read_object(state.ctx, artifact["result"])
    assert checked["proposals"][0]["fileSupport"] == 2 and checked["proposals"][0]["screenSupport"] == 1
    assert checked["rejected"][0]["reason"] == "independent-screen-support-required"
    with pytest.raises(CollaborationError):
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [artifact["id"], "publish"],
            {"requestId": "cannot-adopt", "version": artifact["version"],
             "resultHash": artifact["result"]["sha256"], "expectedGeneration": state.body["expectedGeneration"]}, {})


@pytest.mark.parametrize("metadata", ["연 9% 특별 구성", "화면 3개 구성", "안내\x1f구성"])
def test_model_cannot_supply_invalid_metadata_financial_values_or_evidence_counts(derivation, metadata):
    state = derivation
    state.answer = lambda candidates: {"proposals": [{"id": alias, "title": metadata, "intent": "안내"} for alias in aliases(candidates)]}
    result, artifact = execute(state)
    assert result["status"] == "needs_changes"
    assert not read_object(state.ctx, artifact["result"])["graph"]["nodes"]


def test_decoded_lone_surrogate_is_rejected_as_metadata(derivation):
    from workspace.derive_proposals import verify_proposal
    from workspace.knowledge_execution import manifest_for
    state = derivation
    queued = submit(state.ctx, state.body, state.ledger, state.config, lambda *args: None, operation="design.derive")
    artifact = state.ctx.get("wb_artifact", queued["artifact"]["id"])
    manifest, _ = manifest_for(state.ctx, artifact)
    response = json.dumps({"proposals": [{"id": alias, "title": "안내\ud800", "intent": "안내"}
        for alias in aliases(manifest["derivation"]["evidence"]["candidates"])]})
    checked = verify_proposal(manifest, response)
    assert checked["rejected"][0]["reason"] == "metadata-invalid" and not checked["graph"]["nodes"]
    assert not state.calls


@pytest.mark.parametrize("missing", ["decisionIds", "nodeIds", "analysisId"])
def test_derive_missing_required_input_fails_before_admission(derivation, missing):
    state = derivation
    body = {key: value for key, value in state.body.items() if key != missing}
    with pytest.raises(CollaborationError) as error:
        submit(state.ctx, body, state.ledger, state.config, lambda *args: None, operation="design.derive")
    assert error.value.status == 422 and not state.calls
    assert not [row for row in state.storage.list(state.owner, "wb_artifact") if row.get("kind") == "knowledge-stage"]


def test_retained_source_input_is_required_and_current_analysis_tampering_blocks(derivation, monkeypatch):
    state = derivation
    status, listing = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", ["derive-inputs"], {}, {})
    assert status == 200 and any(row["analysisId"] == state.artifact_id for row in listing["items"])
    artifact = state.ctx.get("wb_artifact", state.artifact_id)
    original = state.ctx.read_json(artifact["input"]["ref"], artifact["input"]["hash"])
    changed = {**original, "payload": {**original["payload"], "files": []}}
    raw = json.dumps(changed).encode()
    key = state.storage.key_for(state.owner, "wb_artifact", artifact["id"], "changed.json")
    state.storage.put_blob_once(key, raw, "application/json")
    # Legacy writes cannot alter a completed B0 artifact, even after job TTL.
    from workspace.storage import ReservedRecord
    altered = {**artifact, "input": {"ref": key, "hash": hashlib.sha256(raw).hexdigest()}}
    with pytest.raises(ReservedRecord):
        state.storage.put(state.owner, "wb_artifact", altered, artifact["version"])
    get = state.storage.get
    monkeypatch.setattr(state.storage, "get", lambda owner, kind, identifier:
        altered if kind == "wb_artifact" and identifier == artifact["id"] else get(owner, kind, identifier))
    with pytest.raises(CollaborationError) as error:
        submit(state.ctx, state.body, state.ledger, state.config, lambda *args: None, operation="design.derive")
    assert error.value.code == "derivation-analysis-changed" and not state.calls


def test_source_job_ttl_does_not_erase_current_authorized_analysis_evidence(derivation, monkeypatch):
    state = derivation
    expired = {state.job_id}
    get = state.storage.get
    monkeypatch.setattr(state.storage, "get", lambda owner, kind, identifier:
        None if kind == "job" and identifier in expired else get(owner, kind, identifier))
    result, artifact = execute(state)
    assert result["status"] == "succeeded"
    expired.add(artifact["jobId"])
    status, visible = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", [artifact["id"]], {}, {})
    assert status == 200 and visible["operation"] == "design.derive"
    assert "workspace/" not in json.dumps(visible)


def test_code_approval_alone_cannot_approve_screen_mapping(derivation):
    state = derivation
    store = Ontology(state.ctx)
    screen_id = state.ids["screen-first"]
    store.review_node(screen_id, expected_generation=store.current()["generation"], revision=1,
        decision="rejected", reason="mapping needs review", request_id="reject-mapping")
    store.review_node(screen_id, expected_generation=store.current()["generation"], revision=1,
        decision="reviewed", reason="screen not yet approved", request_id="review-mapping")
    code_id = state.code["src/First.tsx"]["id"]
    store.review_node(code_id, expected_generation=store.current()["generation"], revision=1,
        decision="reviewed", reason="code only", request_id="review-code-again")
    store.review_node(code_id, expected_generation=store.current()["generation"], revision=1,
        decision="approved", reason="code only", request_id="approve-code-again")
    edges = store.context([screen_id])["edges"]
    assert all(edge["reviewState"] == "candidate" for edge in edges
               if edge["type"] == "IMPLEMENTS" and edge["dst"]["id"] == screen_id)


@pytest.mark.parametrize("fault", ["unknown", "duplicate"])
def test_model_cannot_add_or_repeat_candidate_identities(derivation, fault):
    state = derivation
    state.answer = lambda candidates: {"proposals": (
        [{"id": "unknown", "title": "안내", "intent": "안내"}] if fault == "unknown" else
        [{"id": next(iter(aliases(candidates))), "title": "안내", "intent": "안내"}] * 2)}
    before = Ontology(state.ctx).current()["generation"]
    with pytest.raises(LedgerError, match="derivation-proposal-invalid"):
        execute(state)
    assert len(state.calls) == 1 and Ontology(state.ctx).current()["generation"] == before
    artifacts = state.storage.list(state.owner, "wb_artifact")
    assert all(not row.get("result") for row in artifacts if row.get("kind") == "knowledge-stage")


def test_changed_screen_after_model_blocks_independent_completion(derivation):
    state = derivation
    def reject():
        store = Ontology(state.ctx)
        store.review_node(state.ids["screen-first"], expected_generation=store.current()["generation"],
            revision=1, decision="rejected", reason="withdrawn screen basis", request_id="withdraw-screen")
    state.after_model = reject
    with pytest.raises((LedgerError, CollaborationError)):
        execute(state)
    assert len(state.calls) == 1
    assert all(not row.get("result") for row in state.storage.list(state.owner, "wb_artifact")
               if row.get("kind") == "knowledge-stage")


def test_developer_reads_but_cannot_create_or_adopt(derivation):
    from workbench.service import Service
    state = derivation
    developer = Service(state.env.api, state.env.scope("dana"), {"sub": "dana", "exp": state.ctx.claims["exp"]})
    with pytest.raises(CollaborationError) as error:
        submit(developer, state.body, state.ledger, state.config, lambda *args: None, operation="design.derive")
    assert error.value.status == 403 and not state.calls
    _, artifact = execute(state)
    assert route(developer.host, developer.scope, developer.claims, "GET", [artifact["id"]], {}, {})[0] == 200
    with pytest.raises(CollaborationError) as error:
        route(developer.host, developer.scope, developer.claims, "POST", [artifact["id"], "publish"],
            {"requestId": "developer-adopt", "version": artifact["version"],
             "resultHash": artifact["result"]["sha256"], "expectedGeneration": state.body["expectedGeneration"]}, {})
    assert error.value.status == 403


def test_membership_demotion_at_adoption_commit_blocks_canonical_writes(derivation, monkeypatch):
    state = derivation
    _, artifact = execute(state)
    put_many = state.storage.put_many
    raced = []
    def demote(writes, *args, **kwargs):
        if any(write["kind"] == "knowledge_decision" for write in writes) and not raced:
            raced.append(True)
            project = state.storage.get(state.owner, "project", state.env.pid)
            members = copy.deepcopy(project["members"])
            members["carol"]["role"] = "developer"
            assert state.env.http("PUT", f"/projects/{state.env.pid}/members",
                {"version": project["version"], "members": members})[0] == 200
        return put_many(writes, *args, **kwargs)
    monkeypatch.setattr(state.storage, "put_many", demote)
    with pytest.raises(CollaborationError) as error:
        route(state.env.api, state.env.scope("carol"), {"sub": "carol", "exp": state.ctx.claims["exp"]},
            "POST", [artifact["id"], "publish"], {"requestId": "raced-adopt", "version": artifact["version"],
            "resultHash": artifact["result"]["sha256"], "expectedGeneration": state.body["expectedGeneration"]}, {})
    assert error.value.status == 409 and raced
    assert Ontology(state.ctx).current()["generation"] == state.body["expectedGeneration"]
    assert not state.storage.list(state.owner, "knowledge_decision")


def test_http_derive_dispatches_closed_operation_and_keeps_retry_identity(derivation, monkeypatch):
    from ontology_runtime import execution_entrypoints
    state = derivation
    observed = []
    def operation(ctx, body, *, operation):
        observed.append(operation)
        return submit(ctx, body, state.ledger, state.config, lambda *args: None, operation=operation)
    monkeypatch.setattr(execution_entrypoints, "knowledge_operation", operation)
    args = state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", ["derive"], state.body, {}
    first, second = route(*args), route(*args)
    assert first == second and first[0] == 202 and observed == ["design.derive"] * 2
    assert not state.calls


@pytest.mark.parametrize("operation", ["design.derive", "design.guidelines"])
def test_recovery_selects_registered_knowledge_completion(operation, monkeypatch):
    from types import SimpleNamespace
    from ontology_runtime import execution_entrypoints as entries
    from workspace.knowledge_execution import KnowledgePublication
    output = {"key": "private/job/out/a1/verify/result.json", "sha256": "1" * 64}
    job = {"id": "recovery", "operation": operation, "status": "recovery_required", "attempt": {"id": "a1"},
        "stages": [{"stage": "verify", "attemptId": "a1", "outputs": [output], "receiptHash": "receipt"}]}
    observed = {}
    class Reconciler:
        def reconcile(self, *args, **kwargs):
            observed.update(kwargs)
            return {"status": "succeeded"}
    ledger = SimpleNamespace(_get=lambda *args: job, _terminal_status=lambda *args: "succeeded",
        reconciler=lambda: Reconciler())
    monkeypatch.setattr(entries, "dependencies", lambda: (SimpleNamespace(), {}, ledger, None, None, None, None))
    monkeypatch.setattr(entries, "view_job", lambda result: result)
    assert entries.reconcile_handler({"owner": "project:p1", "jobId": job["id"]}, None)["status"] == "succeeded"
    assert type(observed["stage_completion"]) is KnowledgePublication
