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
def pipeline(wb):
    artifact = admitted(wb)
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
    state.artifact, state.memory, state.tools = artifact, memory, tools
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


@pytest.mark.parametrize("change", ["receipt", "revoked-key", "deadline"])
def test_authority_independently_rejects_late_or_invalid_runtime_results(wb, pipeline, change):
    def mutate(result):
        if change == "receipt":
            result["execution"]["inputHash"] = "f" * 64
        elif change == "revoked-key":
            key = wb.storage.get(KEYS, "ac_key", "evidence-v1")
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        else:
            wb.storage.clock = lambda: wb.now + 900000
    pipeline.after_runtime = mutate
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def test_execution_writer_cannot_modify_project_sources_or_key_registry(wb):
    artifact = admitted(wb)
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
