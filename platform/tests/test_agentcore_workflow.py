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
    return build_pipeline(wb, monkeypatch)


def build_pipeline(wb, monkeypatch, files=None, **admission):
    artifact = admitted(wb, monkeypatch, files=files, **admission)
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


def _revoke(wb, key_id):
    key = wb.storage.get(KEYS, "ac_key", key_id)
    wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])


@pytest.mark.parametrize("key_id", ["cap-v1", "evidence-v1"])
def test_authority_rejects_a_cached_replay_whose_key_is_revoked_during_its_final_source_check(
        wb, pipeline, monkeypatch, key_id):
    """PR #22 review 2, finding 2 / PR #34 review 1, finding 3: either registry
    key revoked during the replay's final source check (after the cached result
    read) still aborts the replay; the keys are rechecked after that read and
    fenced into the replay's check-only delivery transaction."""
    pipeline.dispatch()
    state = _arm_after_result_read(wb, monkeypatch, lambda: _revoke(wb, key_id))
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert state["fired"] == 1 and pipeline.runtime_calls == 1


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


@pytest.mark.parametrize("key_id", ["evidence-v1", "cap-v1"])
def test_authority_fences_each_registry_key_into_the_completion_transaction(wb, pipeline, key_id):
    """PR #34 review 1, finding 3: like the capability key, the evidence key
    verified before finalization must ride into the completion transaction as
    an exact registry-version fence; revoking it immediately before that
    transaction must leave the execution uncompleted."""
    client = wb.storage.table().meta.client
    original = client.transact_write_items

    def intercept(**kwargs):
        puts = [entry["Put"]["Item"] for entry in kwargs["TransactItems"] if "Put" in entry]
        if any(item.get("status") == "completed" for item in puts):
            key = wb.storage.get(KEYS, "ac_key", key_id)
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        return original(**kwargs)

    client.transact_write_items = intercept
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def _text_sources(wb, count):
    files = []
    for index in range(count):
        # Letters only: digits can resemble identifiers to the private inspection.
        name = "".join("abcdefghij"[int(digit)] for digit in f"{index:03d}")
        source = asset(wb, f"budget-{name}", f"export const {name} = '{name}';".encode())
        files.append({"assetId": source["id"], "path": f"src/budget{index:03d}.ts"})
    return files


def _submit_sources(wb, monkeypatch, files):
    from test_agentcore_authorization import authorize_admission, classify_admitted
    from workspace.ontology_jobs import submit
    from ontology_runtime.dispatch import RuntimeAnalyzer
    wb.api.ontology_analyzer_ready = True
    wb.api.ontology_analyzer = RuntimeAnalyzer(
        "arn:aws:lambda:ap-northeast-2:180294183052:function:synthetic-authority:1", "a" * 64)
    refs = [asset_reference(wb.storage.get(wb.owner, "asset", file["assetId"])) for file in files]
    authorize_admission(wb, monkeypatch, refs)
    for file, ref in zip(files, refs):
        classify_admitted(wb, ref, request_id=file["assetId"])
    return submit(context(wb), {"requestId": "budget", "name": "budget", "files": files})


# Synthetic sources: a source fence, a classification, a provenance
# registration and an intake decision each (4n), plus the shared policy.
BUDGET_SOURCES = (90 - 1) // 4


def test_the_source_budget_is_derived_from_the_complete_transaction_budget():
    from ontology_runtime import admission
    assert admission.SOURCE_CHECK_BUDGET == min(90, 100 - max(10, admission.NON_SOURCE_OPERATIONS)) == 90
    assert admission.MAX_SOURCES == admission.SOURCE_CHECK_BUDGET // admission.MIN_CHECKS_PER_SOURCE == 30


def test_an_accepted_source_set_at_the_budget_boundary_is_executable(wb, monkeypatch):
    """PR #34 review 1, finding 4: every job submission accepts is executable --
    dispatch, every tool call and completion stay within one transaction each."""
    files = _text_sources(wb, BUDGET_SOURCES)
    client = wb.storage.table().meta.client
    original, sizes = client.transact_write_items, []

    def measure(**kwargs):
        sizes.append(len(kwargs["TransactItems"]))
        return original(**kwargs)

    state = build_pipeline(wb, monkeypatch, files=files)
    client.transact_write_items = measure
    result = state.dispatch()
    assert result["execution"]["backend"] == "agentcore-code-interpreter"
    assert wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]["status"] == "completed"
    assert sizes and max(sizes) <= 100


@pytest.mark.parametrize("count", [BUDGET_SOURCES + 1, 32, 40])
def test_a_source_set_over_the_budget_is_rejected_unchanged_at_submission(wb, monkeypatch, count):
    with pytest.raises(CollaborationError) as error:
        _submit_sources(wb, monkeypatch, _text_sources(wb, count))
    assert error.value.code == "execution-completion-scope"
    assert wb.storage.list_page(wb.owner, "wb_artifact")["items"] == []
    assert wb.storage.list_page(wb.owner, "job")["items"] == []


def _arm_after_result_read(wb, monkeypatch, action):
    """Run `action` on the first `Sources.recheck_deadlines` (the replay's final
    source check) after the cached result blob has been read."""
    from workspace.ontology_sources import Sources
    execution = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]
    get_blob, recheck = type(wb.storage).get_blob, Sources.recheck_deadlines
    state = {"armed": False, "fired": 0}

    def reading(self, key, *args, **kwargs):
        data = get_blob(self, key, *args, **kwargs)
        if key == execution["resultKey"]:
            state["armed"] = True
        return data

    def rechecking(self):
        value = recheck(self)
        if state["armed"]:
            state["armed"] = False
            state["fired"] += 1
            action()
        return value

    monkeypatch.setattr(type(wb.storage), "get_blob", reading)
    monkeypatch.setattr(Sources, "recheck_deadlines", rechecking)
    return state


def test_a_cached_replay_refuses_a_policy_retired_during_its_final_source_check(wb, pipeline, monkeypatch):
    """PR #34 review 2, finding 1: the replay's final delivery guard aggregates
    source, admission, key and attempt authority; a retirement during the last
    source check must abort the replay, not only one before it."""
    pipeline.dispatch()
    state = _arm_after_result_read(wb, monkeypatch, lambda: _retire_policy(wb, monkeypatch))
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert state["fired"] == 1 and pipeline.runtime_calls == 1


def test_a_cached_replay_refuses_a_decision_expiring_during_its_final_source_check(wb, monkeypatch):
    """A legitimately short-lived decision (bounded by its provenance) expiring
    during the final source check, before the execution deadline, refuses the
    replay: every deadline is compared once, after the last read."""
    state = build_pipeline(wb, monkeypatch, provenance_ttl_ms=300_000)
    state.dispatch()
    original = wb.storage.clock
    armed = _arm_after_result_read(wb, monkeypatch, lambda: setattr(wb.storage, "clock", lambda: original() + 301_000))
    execution = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]
    assert (original() + 301_000) // 1000 < execution["deadline"]
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        state.dispatch()
    assert armed["fired"] == 1 and state.runtime_calls == 1


def test_a_cached_replay_refuses_an_execution_deadline_crossed_during_the_result_read(wb, pipeline, monkeypatch):
    pipeline.dispatch()
    execution = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]
    original = type(wb.storage).get_blob
    seen = {"result": 0}

    def get_blob(self, key, *args, **kwargs):
        data = original(self, key, *args, **kwargs)
        if key == execution["resultKey"]:
            seen["result"] += 1
            wb.storage.clock = lambda: execution["deadline"] * 1000
        return data

    monkeypatch.setattr(type(wb.storage), "get_blob", get_blob)
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert seen["result"] == 1 and pipeline.runtime_calls == 1


def test_a_replayed_tool_operation_refuses_a_capability_key_revoked_during_its_final_guard(
        wb, pipeline, monkeypatch):
    """The same aggregate guard on the `Tools.invoke` prior-marker replay: the
    capability key is fenced with the admission, source and attempt authority,
    so a revocation reaching after the token's own final verification aborts it."""
    from intake.records import INTAKE_OWNER
    tools, outcome = pipeline.tools, {}
    invoke = tools.invoke
    get = type(wb.storage).get
    armed = {"on": False}

    def revoking_get(self, owner, kind, identifier, *args, **kwargs):
        value = get(self, owner, kind, identifier, *args, **kwargs)
        if armed["on"] and owner == INTAKE_OWNER and kind == "adm_policy":
            armed["on"] = False
            key = get(self, KEYS, "ac_key", "cap-v1")
            wb.storage.put(KEYS, "ac_key", {**key, "state": "revoked"}, key["version"])
        return value

    def replaying(event, context):
        result = invoke(event, context)
        if context.client_context.custom["bedrockAgentCoreToolName"] == "ontology___finish" and not outcome:
            from ontology_runtime.capability import Capabilities
            verify = Capabilities.verify
            calls = {"n": 0}

            def arming_verify(self, *args, **kwargs):
                value = verify(self, *args, **kwargs)
                calls["n"] += 1
                if calls["n"] == 2:  # the replay's own final token verification
                    armed["on"] = True
                return value

            monkeypatch.setattr(Capabilities, "verify", arming_verify)
            try:
                invoke(event, context)
                outcome["replay"] = "returned"
            except (AuthorizationDenied, CollaborationError):
                outcome["replay"] = "refused"
            monkeypatch.setattr(Capabilities, "verify", verify)
        return result

    monkeypatch.setattr(type(wb.storage), "get", revoking_get)
    tools.invoke = replaying
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert outcome == {"replay": "refused"}


def _short_lived_capability_key(wb, seconds):
    key = wb.storage.get(KEYS, "ac_key", "cap-v1")
    return wb.storage.put(KEYS, "ac_key", {**key, "notAfter": wb.now // 1000 + seconds}, key["version"])


@pytest.mark.parametrize("transaction", ["completion", "tool"])
def test_capability_key_expiry_inside_the_commit_guard_aborts_it(wb, pipeline, monkeypatch, transaction):
    """PR #34 review 2, finding 3: key expiry changes no registry version, so a
    version fence alone never sees it. Advancing the clock through
    `cap-v1.notAfter` inside the commit's own `before_attempt` guard (before
    the execution and authorization deadlines) must abort the commit."""
    key = _short_lived_capability_key(wb, 300)
    original = type(wb.storage).put_many
    fired = {"n": 0}

    def selected(writes):
        items = [write["item"] for write in writes]
        if transaction == "completion":
            return any(item.get("status") == "completed" for item in items)
        return any(item.get("operation") == "execution.stage" for item in items)

    def put_many(self, writes, checks=None, *, before_attempt=None, **kwargs):
        if before_attempt is not None and selected(writes) and not fired["n"]:
            guard = before_attempt

            def advanced():
                fired["n"] += 1
                wb.storage.clock = lambda: key["notAfter"] * 1000
                return guard()
            before_attempt = advanced
            try:
                result = original(self, writes, checks, before_attempt=before_attempt, **kwargs)
            except BaseException:
                fired["aborted"] = True
                raise
            fired["aborted"] = False
            return result
        return original(self, writes, checks, before_attempt=before_attempt, **kwargs)

    monkeypatch.setattr(type(wb.storage), "put_many", put_many)
    with pytest.raises((AuthorizationDenied, CollaborationError)):
        pipeline.dispatch()
    assert fired == {"n": 1, "aborted": True}
    assert all(item["status"] != "completed" for item in wb.storage.list_page(EXECUTIONS, "ac_execution")["items"])


def test_the_attempt_and_capability_expiry_are_bounded_by_the_key_validity_at_issue(wb, pipeline):
    key = _short_lived_capability_key(wb, 300)
    pipeline.dispatch()
    execution = wb.storage.list_page(EXECUTIONS, "ac_execution")["items"][0]
    assert execution["deadline"] <= key["notAfter"]
    assert execution["capabilityClaims"]["exp"] <= key["notAfter"]
