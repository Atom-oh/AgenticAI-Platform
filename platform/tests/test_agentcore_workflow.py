"""Exercise real orchestration and authorization, replacing only AWS transports."""
import hashlib
import hmac
import io
import json
from types import SimpleNamespace

import pytest

from test_workbench_core import wb
from test_agentcore_authorization import admitted
from test_agentcore_capability import Kms
from test_ontology_sources import asset, context
from ontology_runtime.admission import classify, identifier
from ontology_runtime.authority import Authority
from ontology_runtime.authorization import active_job, commit_execution
from ontology_runtime.capability import AuthorizationDenied, Capabilities, Evidence
from ontology_runtime.memory import Memory
from ontology_runtime.tools import Tools, EXECUTIONS, KEYS
from ontology_runtime.workflow import Workflow
from workspace.collaboration import CollaborationError
from workspace.ontology_analysis import local_analyze, source_input
from workspace.ontology_sources import asset_reference
from workspace import ontology_schema as schema


class PhysicalKms(Kms):
    def generate_mac(self, **kwargs):
        assert kwargs["MacAlgorithm"] == "HMAC_SHA_256"
        return {"Mac": hmac.digest(b"synthetic-namespace-key", kwargs["Message"], "sha256")}


class Events:
    def __init__(self):
        self.events = []

    def create_event(self, **kwargs):
        assert kwargs["extractionMode"] == "SKIP"
        self.events.append(kwargs)
        return {"event": {"eventId": f"event-{len(self.events)}"}}

    def list_events(self, **kwargs):
        return {"events": [event for event in self.events if all(
            event[key] == kwargs[key] for key in ("memoryId", "actorId", "sessionId"))]}


@pytest.fixture
def pipeline(wb, monkeypatch):
    artifact = admitted(wb, monkeypatch)
    kms = PhysicalKms()
    for key, purpose in [("cap-v1", "execution-capability"), ("evidence-v1", "runtime-evidence")]:
        wb.storage.put(KEYS, "ac_key", {"id": key, "keyArn": key, "purpose": purpose,
            "state": "active", "algorithm": "RS256", "notBefore": wb.now // 1000 - 1,
            "notAfter": wb.now // 1000 + 3600, "publicKeyHash": hashlib.sha256(b"synthetic-public-key").hexdigest()})
    wb.storage.put(KEYS, "ac_key", {"id": "gateway-binding", "gatewayId": "gateway",
        "targetId": "target", "workloadIdentityArn": "workload"})
    caps = Capabilities(kms, lambda key: wb.storage.get(KEYS, "ac_key", key), clock=lambda: wb.storage.clock() // 1000)
    evidence = Evidence(kms, "evidence-v1", lambda: wb.storage.get(KEYS, "ac_key", "evidence-v1"))
    tools = Tools(wb.storage, caps, gateway_id="gateway", target_id="target", target_name="ontology", workload="workload")
    state = SimpleNamespace(before_tool=None, after_runtime=None, replay_finish=False, runtime_calls=0, parser_calls=0)

    def gateway_call(name, arguments, *, capability, operation_id):
        metadata = {"bedrockAgentCoreGatewayId": "gateway", "bedrockAgentCoreTargetId": "target",
                    "bedrockAgentCoreToolName": name}
        if state.before_tool:
            state.before_tool(name, arguments, metadata)
        invocation = {**arguments, "_executionAuthorization": {"capability": capability, "operationId": operation_id}}
        ctx = SimpleNamespace(client_context=SimpleNamespace(custom=metadata))
        result = tools.invoke(invocation, ctx)
        if state.replay_finish and name == "ontology___finish":
            assert tools.invoke(invocation, ctx) == result
        return result

    class Parser:
        config = {"archiveHash": "a" * 64}

        def __call__(self, payload):
            state.parser_calls += 1
            result = local_analyze(payload)
            result["execution"].update(backend="agentcore-code-interpreter", toolArchiveHash="a" * 64,
                interpreterId="synthetic-interpreter", sessionId="synthetic-session",
                nodeVersion="v24.0.0", architecture="arm64", region="ap-northeast-2")
            return result

    memory = Memory(Events(), "synthetic-memory", kms, "namespace-key", "organization")
    workflow = Workflow(SimpleNamespace(call=gateway_call), Parser(), memory, evidence)

    def invoke_agent_runtime(**kwargs):
        state.runtime_calls += 1
        assert kwargs["qualifier"] == "v1"
        event = json.loads(kwargs["payload"])
        result = workflow.analyze(event, kwargs["runtimeSessionId"])
        if state.after_runtime:
            state.after_runtime(result)
        return {"response": io.BytesIO(json.dumps(result).encode())}

    authority = Authority(wb.storage, SimpleNamespace(invoke_agent_runtime=invoke_agent_runtime), caps, evidence,
        {"toolArchiveHash": "a" * 64, "workloadIdentityArn": "workload", "capabilityKeyId": "cap-v1",
         "runtimeArn": "synthetic-runtime", "runtimeQualifier": "v1"})
    payload, _ = source_input(context(wb), artifact["jobInput"]["files"], artifact["jobInput"]["resolver"])
    state.dispatch = lambda: authority.dispatch({"projectId": wb.project["id"], "artifactId": artifact["id"],
                                                "inputHash": schema.digest(payload)})
    state.artifact, state.memory, state.tools, state.authority = artifact, memory, tools, authority
    return state


def test_authority_tools_workflow_evidence_and_finish_replay_complete_once(wb, pipeline):
    pipeline.replay_finish = True
    result = pipeline.dispatch()
    assert result["execution"]["backend"] == "agentcore-code-interpreter"
    assert pipeline.parser_calls == pipeline.runtime_calls == 1
    assert pipeline.dispatch() == result
    assert pipeline.runtime_calls == 1
    executions = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"]
    assert executions[0]["status"] == "completed"
    assert set(executions[0]["stageReceipts"]) == {"context", "analyzed"}
    assert wb.storage.owns_key(EXECUTIONS, executions[0]["resultKey"])


@pytest.mark.parametrize("change", ["admission", "membership", "gateway", "cancel", "source"])
def test_protected_tools_reject_changed_authority_before_the_parser(wb, pipeline, change):
    def mutate(name, arguments, metadata):
        if name != "ontology___source":
            return
        if change == "admission":
            ref = pipeline.artifact["sourceRefs"][0]
            record = wb.storage.get(wb.owner, "ac_admission", identifier(ref))
            wb.storage.put(wb.owner, "ac_admission", {**record, "reason": "Changed review"}, record["version"])
        elif change == "membership":
            project = wb.storage.get(wb.owner, "project", wb.project["id"])
            project["members"].pop("bob")
            wb.storage.put(wb.owner, "project", project, project["version"])
        elif change == "gateway":
            metadata["bedrockAgentCoreTargetId"] = "other-target"
        elif change == "cancel":
            job = wb.storage.get(wb.owner, "job", pipeline.artifact["jobId"])
            wb.storage.put(wb.owner, "job", {**job, "status": "cancelled"}, job["version"])
        else:
            arguments["sourceRef"] = {**arguments["sourceRef"], "sourceId": "unadmitted-source"}
    pipeline.before_tool = mutate
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert pipeline.parser_calls == 0
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


@pytest.mark.parametrize("change", ["receipt", "revoked-key", "revoked-capability-key", "deadline"])
def test_authority_independently_rejects_late_or_invalid_runtime_results(wb, pipeline, change):
    def mutate(result):
        if change == "receipt":
            result["execution"]["inputHash"] = "f" * 64
        elif change == "revoked-key":
            key = wb.storage.get(KEYS, "ac_key", "evidence-v1")
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        elif change == "revoked-capability-key":
            # PR #22 review 1, finding 4: finalization checked only the evidence
            # key; a capability (cap-v1) revoked while Runtime was executing must
            # independently abort completion too (AUTH-04).
            key = wb.storage.get(KEYS, "ac_key", "cap-v1")
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        else:
            wb.storage.clock = lambda: wb.now + 900000
    pipeline.after_runtime = mutate
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def test_authority_fences_the_capability_key_into_the_completion_transaction(wb, pipeline):
    """PR #22 review 2, finding 2: round 1's fix (`revoked-capability-key` above)
    checked `cap-v1` before the final reads, but that Python-level check was never
    fenced into the completion transaction itself -- a revocation landing in the
    narrow window between that check and the actual DynamoDB write still let the
    execution complete. Revoke `cap-v1` from inside the real `TransactWriteItems`
    call that carries the completion write (identified by its own payload, not by
    counting calls -- several earlier transactions happen first, one per Gateway
    tool invocation) and confirm it still aborts, via the DB-level version fence,
    not only the earlier Python-level check."""
    table = wb.storage.table()
    client = table.meta.client
    original = client.transact_write_items

    def intercept(**kwargs):
        puts = [entry["Put"]["Item"] for entry in kwargs["TransactItems"] if "Put" in entry]
        if any(item.get("status") == "completed" for item in puts):
            key = wb.storage.get(KEYS, "ac_key", "cap-v1")
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        return original(**kwargs)

    client.transact_write_items = intercept
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def test_authority_rejects_a_cached_replay_once_its_capability_key_is_revoked(wb, pipeline):
    """PR #22 review 1, finding 4: the cached-replay branch must also refuse a
    revoked capability key, not only a fresh dispatch's finalization."""
    result = pipeline.dispatch()
    assert pipeline.runtime_calls == 1
    key = wb.storage.get(KEYS, "ac_key", "cap-v1")
    wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert pipeline.runtime_calls == 1
    executions = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"]
    assert executions[0]["status"] == "completed" and executions[0]["resultHash"] == hashlib.sha256(
        schema.canonical(result)).hexdigest()


def test_authority_rejects_a_cached_replay_revoked_during_its_own_final_source_recheck(wb, pipeline, monkeypatch):
    """PR #22 review 2, finding 2: "Recheck replay authority after its final
    reads." The cached-replay branch's capability check must run LAST, after
    `sources.recheck()` (its other final read) -- round 1's fix ran it BEFORE
    that read, so a revocation landing during that very read would have gone
    unnoticed until the next replay attempt."""
    from workspace.ontology_sources import Sources
    result = pipeline.dispatch()
    assert pipeline.runtime_calls == 1
    original = Sources.recheck
    # `sources.verify(pinned["sourceRefs"])`, earlier in dispatch(), makes two of
    # its own `recheck()`-equivalent reads before the cached-replay branch is
    # even reached; only the THIRD call within this one dispatch() attempt is
    # the branch's own explicit `sources.recheck()` -- the final read whose
    # ordering relative to the capability check this test exercises.
    calls = {"n": 0}

    def revoke_on_the_branchs_own_recheck(self):
        calls["n"] += 1
        if calls["n"] == 3:
            key = wb.storage.get(KEYS, "ac_key", "cap-v1")
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        return original(self)

    monkeypatch.setattr(Sources, "recheck", revoke_on_the_branchs_own_recheck)
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert pipeline.runtime_calls == 1
    assert calls["n"] == 3


def test_execution_writer_cannot_modify_project_sources_or_key_registry(wb, monkeypatch):
    artifact = admitted(wb, monkeypatch)
    ctx, _, _, _, checks = active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"])
    for owner, kind in [(wb.owner, "project"), (KEYS, "ac_key"), (wb.owner, "asset")]:
        write = wb.collab._write(owner, kind, {"id": "forbidden", "ttl": wb.now // 1000 + 60})
        with pytest.raises(AuthorizationDenied):
            commit_execution(ctx, [write], checks)
        assert wb.storage.get(owner, kind, "forbidden") is None


def test_human_classification_cannot_bypass_private_identifier_inspection(wb, capsys):
    source = asset(wb, "sensitive", b'export const original = "CUST-0042";')
    with pytest.raises(CollaborationError) as error:
        classify(context(wb), {"requestId": "sensitive", "sourceRef": asset_reference(source),
            "classification": "public", "reason": "Caller label is not inspection"})
    assert error.value.code == "agentcore-private-inspection"
    assert "CUST-0042" not in capsys.readouterr().out
    assert wb.storage.list_page(wb.owner, "ac_admission")["items"] == []


def test_memory_namespace_and_attempt_filtering(pipeline):
    memory = pipeline.memory
    claims = {"projectId": "p", "actor": "a", "executionId": "e", "attemptId": "first",
              "resourcesHash": "a" * 64, "iat": 1800000000}
    memory.append(claims, "context", "b" * 64)
    assert memory.read(claims)["events"]
    assert memory.read({**claims, "attemptId": "second"})["events"] == []
    other = Memory(memory.client, memory.identifier, memory.kms, memory.key, "other-organization")
    assert other.scope(claims)["actorId"] != memory.scope(claims)["actorId"]


def _replace_decision(wb, decision_id):
    """Replace the stored `adm_decision` with a new storage version (same sealed
    content): every fence pinned to the observed version must now fail."""
    decision = wb.storage.get(wb.owner, "adm_decision", decision_id)
    wb.storage.put(wb.owner, "adm_decision", decision, decision["version"])


def test_dispatch_consumes_and_fences_the_private_intake_decisions(wb, pipeline):
    """PR #34 review 1, finding 1: every attempt binds the exact consumed
    private-intake decisions, not only the policy/provenance/grant revisions."""
    pipeline.dispatch()
    decisions = {row["id"]: row for row in wb.storage.list_page(wb.owner, "adm_decision")["items"]}
    assert len(decisions) == len(pipeline.artifact["sourceRefs"])
    execution = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]
    fenced = {(check["id"], check["version"]) for check in execution["admissions"] if check["kind"] == "adm_decision"}
    assert fenced == {(row["id"], row["version"]) for row in decisions.values()}
    for ref in pipeline.artifact["sourceRefs"]:
        binding = wb.storage.get(wb.owner, "ac_admission", identifier(ref))["binding"]["decision"]
        assert decisions[binding["id"]]["source"] == {key: ref[key] for key in (
            "sourceKind", "sourceId", "revision", "sha256", "audienceRevision")}


def test_a_replaced_intake_decision_blocks_the_next_protected_tool(wb, pipeline):
    def replace(name, arguments, metadata):
        if name == "ontology___source":
            binding = wb.storage.get(wb.owner, "ac_admission", identifier(arguments["sourceRef"]))["binding"]
            _replace_decision(wb, binding["decision"]["id"])
    pipeline.before_tool = replace
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert pipeline.parser_calls == 0
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def test_a_decision_replaced_during_the_completion_transaction_aborts_it(wb, pipeline):
    table = wb.storage.table()
    client = table.meta.client
    original = client.transact_write_items
    ref = pipeline.artifact["sourceRefs"][0]
    decision_id = wb.storage.get(wb.owner, "ac_admission", identifier(ref))["binding"]["decision"]["id"]

    def intercept(**kwargs):
        puts = [entry["Put"]["Item"] for entry in kwargs["TransactItems"] if "Put" in entry]
        if any(item.get("status") == "completed" for item in puts):
            _replace_decision(wb, decision_id)
        return original(**kwargs)

    client.transact_write_items = intercept
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def _retire_policy(wb, monkeypatch):
    from test_agentcore_authorization import _admin
    from intake.records import INTAKE_OWNER
    policy = wb.storage.get(INTAKE_OWNER, "adm_policy", "ontology-admission-policy")
    _admin(wb.storage, monkeypatch, {"op": "retire_policy", "id": policy["id"], "expectedRevision": policy["revision"]})


def test_a_cached_replay_rechecks_admission_after_its_final_result_read(wb, pipeline, monkeypatch):
    """PR #34 review 1, finding 2: the cached-replay branch commits no
    transaction, so its admission checks (collected before the cached result is
    read) must be rechecked after that read. Retiring the policy during the
    cached result's `get_blob()` must make the replay fail."""
    pipeline.dispatch()
    execution = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]
    original = type(wb.storage).get_blob
    seen = {"result": 0}

    def get_blob(self, key, *args, **kwargs):
        data = original(self, key, *args, **kwargs)
        if key == execution["resultKey"]:
            seen["result"] += 1
            _retire_policy(wb, monkeypatch)
        return data

    monkeypatch.setattr(type(wb.storage), "get_blob", get_blob)
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert seen["result"] == 1 and pipeline.runtime_calls == 1


def test_a_replayed_tool_operation_rechecks_admission_after_its_final_reads(wb, pipeline, monkeypatch):
    """The prior-marker replay in `Tools.invoke` returns without a transaction:
    admission retired during its reads (after `require()` ran) must refuse it."""
    from workspace.ontology_store import Ontology
    tools, outcome = pipeline.tools, {}
    invoke = tools.invoke
    current = Ontology.current
    armed = {"on": False}

    def retiring_current(self, *args, **kwargs):
        value = current(self, *args, **kwargs)
        if armed["on"]:
            armed["on"] = False
            _retire_policy(wb, monkeypatch)
        return value

    def replaying(event, context):
        result = invoke(event, context)
        if context.client_context.custom["bedrockAgentCoreToolName"] == "ontology___finish" and not outcome:
            armed["on"] = True
            try:
                invoke(event, context)
                outcome["replay"] = "returned"
            except (AuthorizationDenied, CollaborationError):
                outcome["replay"] = "refused"
        return result

    monkeypatch.setattr(Ontology, "current", retiring_current)
    tools.invoke = replaying
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert outcome == {"replay": "refused"}
