"""Unified source path with real RSA signatures, real intake and conditional storage (O mode)."""
import copy
import hashlib
import io
import json
from types import SimpleNamespace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

from test_intake_admission import env  # noqa: F401
from intake_support import api  # noqa: F401
from test_intake_collection import admitted_collection
from ontology_runtime.execution_entrypoints import Dispatcher
from ontology_runtime.execution_protocol import ExecutionCapabilities, ReceiptSigner, ExecutionAccess
from ontology_runtime.execution_tools import ExecutionTools
from ontology_runtime.execution_workflow import ExecutionWorkflow
from workspace.execution_ledger import Ledger, LedgerError, CostGuardGate
from workspace.execution_source import submit, SourcePublication
from workspace.execution_sources import ExecutionSources, reference
from workspace.execution_verifier import KmsVerifier, KEY_OWNER, ALGORITHM
from workspace.ontology_analysis import local_analyze, ANALYZER_ROOT
from workspace.ontology_sources import Sources
from workspace.ontology_store import Ontology
from workbench.service import Service


class RSAKms:
    """Independent RSA oracle; a wrong digest/domain/algorithm cannot verify."""
    def __init__(self):
        self.keys = {purpose: rsa.generate_private_key(public_exponent=65537, key_size=2048)
                     for purpose in ("cap", "evidence")}
        self.signs = []

    def key(self, identifier):
        return self.keys[identifier.rsplit("/", 1)[-1]]

    def describe_key(self, **kwargs):
        self.key(kwargs["KeyId"])
        return {"KeyMetadata": {"KeyState": "Enabled", "KeyUsage": "SIGN_VERIFY", "KeySpec": "RSA_2048"}}

    def get_public_key(self, **kwargs):
        raw = self.key(kwargs["KeyId"]).public_key().public_bytes(serialization.Encoding.DER,
                                                               serialization.PublicFormat.SubjectPublicKeyInfo)
        return {"PublicKey": raw, "KeyUsage": "SIGN_VERIFY", "SigningAlgorithms": [ALGORITHM]}

    def sign(self, **kwargs):
        assert kwargs["SigningAlgorithm"] == ALGORITHM and kwargs["MessageType"] == "DIGEST"
        self.signs.append(kwargs["KeyId"])
        return {"Signature": self.key(kwargs["KeyId"]).sign(kwargs["Message"], padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))}

    def verify(self, **kwargs):
        assert kwargs["SigningAlgorithm"] == ALGORITHM and kwargs["MessageType"] == "DIGEST"
        try:
            self.key(kwargs["KeyId"]).public_key().verify(kwargs["Signature"], kwargs["Message"],
                                                       padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
            return {"SignatureValid": True}
        except Exception:
            return {"SignatureValid": False}


@pytest.fixture
def integrated(env, monkeypatch):
    storage = env.api.storage
    storage.single_attempt = True  # injected offline transports; no SDK client is reused
    monkeypatch.setattr(CostGuardGate, "check", lambda *args, **kwargs: None)
    decision, original = admitted_collection(env, {
        f"src/{env.term}/Example.tsx": "export function Example() { return <div>안내</div>; }\n"})
    kms = RSAKms()
    for identifier, purpose in (("cap", "execution-capability"), ("evidence", "runtime-evidence")):
        arn = "arn:aws:kms:ap-northeast-2:000000000000:key/" + identifier
        storage.put(KEY_OWNER, "ac_key", {"id": identifier, "purpose": purpose, "keyArn": arn,
            "algorithm": "RS256", "state": "active", "notBefore": storage.clock() // 1000 - 30,
            "notAfter": storage.clock() // 1000 + 86400,
            "publicKeyHash": hashlib.sha256(kms.get_public_key(KeyId=arn)["PublicKey"]).hexdigest()})
    storage.put(KEY_OWNER, "ac_key", {"id": "gateway-binding", "gatewayId": "gateway", "targetId": "target",
                                     "workloadIdentityArn": "workload"})
    config = {"protocol": "platform-execution/1", "revision": "cfg-1", "capabilityKeyId": "cap", "evidenceKeyId": "evidence",
              "evidenceKeyArn": "arn:aws:kms:ap-northeast-2:000000000000:key/evidence", "workloadIdentityArn": "workload",
              "runtimeArn": "arn:aws:bedrock-agentcore:ap-northeast-2:000000000000:runtime/synthetic", "runtimeQualifier": "v1",
              "interpreter": {"identifier": "interpreter", "region": "ap-northeast-2", "bucket": "synthetic-tools",
                "key": "tools/" + "a" * 64 + ".tar.gz", "version": "1", "archiveHash": "a" * 64,
                "architecture": "arm64", "nodeMajor": 24,
                "analyzerCodeHash": hashlib.sha256((ANALYZER_ROOT / "analyze.cjs").read_bytes()).hexdigest(),
                "dependencyLockHash": hashlib.sha256((ANALYZER_ROOT / "package-lock.json").read_bytes()).hexdigest()}}
    claims = {"sub": "alice", "exp": storage.clock() // 1000 + 3600}
    ctx = Service(env.api, env.scope(), claims)
    verifier = KmsVerifier(storage, kms, ["evidence"])
    capabilities = ExecutionCapabilities(storage, kms, config)
    ledger = Ledger.production(storage, verifier=verifier, sources=ExecutionSources(env.api))
    queued = []
    response = submit(ctx, {"requestId": "unified-1", "name": "source-analysis", "collectionDecisionId": decision["id"]},
                      ledger, config, lambda *value: queued.append(value))
    metadata = SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreGatewayId": "gateway",
        "bedrockAgentCoreTargetId": "target", "bedrockAgentCoreToolName": "ontology___execution"}))
    state = SimpleNamespace(env=env, storage=storage, ctx=ctx, config=config, kms=kms, verifier=verifier,
        capabilities=capabilities, ledger=ledger, job_id=response["job"]["id"], artifact_id=response["artifact"]["id"],
        owner=ctx.owner, decision=decision, original=original, metadata=metadata, queued=queued, invocations=[], tool_calls=[])
    tools = ExecutionTools(env.api, verifier, capabilities, config)
    class Gateway:
        def call(self, name, arguments, *, capability, operation_id):
            state.tool_calls.append(arguments["action"])
            assert name == "ontology___execution"
            return tools.invoke({**arguments, "_executionAuthorization": {"capability": capability,
                                 "operationId": operation_id}}, metadata)
    class Interpreter:
        def __init__(self):
            self.config = config["interpreter"]
        def execute(self, operation, payload):
            state.invocations.append(copy.deepcopy(payload))
            assert operation == "analyze"
            value = local_analyze(payload)
            value["execution"].update(backend="agentcore-code-interpreter", sessionId="observed-session", region="ap-northeast-2",
                interpreterId="interpreter", architecture="arm64", nodeVersion="v24.0.0", toolArchiveHash="a" * 64)
            return value
    state.workflow = ExecutionWorkflow(Gateway(), Interpreter(), ReceiptSigner(kms, config["evidenceKeyArn"], "evidence"), clock=storage.clock)
    return state


def run(state):
    job = state.ledger.dispatcher().allocate(state.owner, state.job_id, operation_id="a" * 32)
    token, claims, key = state.capabilities.issue(job)
    state.ledger.dispatcher().bind_capability(state.owner, state.job_id, job["attempt"]["id"], job["fence"],
        digest=hashlib.sha256(token.encode()).hexdigest(), claims=claims, key_check=key, operation_id="b" * 32)
    event = {"capability": token, "executionId": job["id"], "runtimeSessionId": job["attempt"]["sessionId"], "workloadIdentity": "workload"}
    return state.workflow.analyze(event, job["attempt"]["sessionId"])


def test_real_admission_to_signed_runtime_to_one_publication(integrated):
    s = integrated
    result = run(s)
    assert result["status"] == "succeeded"
    assert len(s.invocations) == 1
    job = s.storage.get(s.owner, "job", s.job_id)
    artifact = s.storage.get(s.owner, "wb_artifact", s.artifact_id)
    assert artifact["status"] == "completed" and artifact["generation"] == Ontology(s.ctx).current()["generation"]
    assert job["task"] == "agentcore-execution" and len(job["stages"]) == 2
    final = [t for t in s.storage._table.transactions if any(
        entry.get("Put", {}).get("Item", {}).get("status") == "succeeded" for entry in t["TransactItems"])]
    assert len(final) == 1
    kinds = [entry["Put"]["Item"]["sk"].split("#")[0] for entry in final[0]["TransactItems"] if "Put" in entry]
    assert "job" in kinds and "wb_artifact" in kinds and kinds.count("ontology") == 2
    assert not s.storage.list_page("ontology-executions", "ac_execution")["items"]
    node = Ontology(s.ctx).read()["nodes"][0]
    assert node["sourceRefs"][0]["sourceKind"] == "admitted-code"


def test_linked_artifact_cancellation_uses_same_writer(integrated):
    s = integrated
    s.ledger.api().cancel(s.owner, s.job_id, actor="alice")
    assert s.storage.get(s.owner, "wb_artifact", s.artifact_id)["status"] == "cancelled"
    assert Ontology(s.ctx).current() is None


def test_normalized_content_is_readable_but_original_path_is_not(integrated):
    s = integrated
    ref = reference(s.decision)
    reader = Sources(s.ctx)
    assert "안내" in reader.resolve({**ref, "location": {"path": "src/neutral_1/Example.tsx"}}, text=True)["text"]
    assert "key" not in reader.resolve(ref)["record"]
    with pytest.raises(Exception):
        reader.resolve({**ref, "location": {"path": f"src/{s.env.term}/Example.tsx"}}, text=True)


def test_rsa_verifier_refuses_modified_body_and_wrong_domain(integrated):
    s = integrated
    body = {"iat": s.storage.clock(), "exp": s.storage.clock() + 60000, "evidence": "synthetic"}
    signed = ReceiptSigner(s.kms, s.config["evidenceKeyArn"], "evidence").sign(body)
    assert s.verifier.verify(signed)
    assert not s.verifier.verify({**signed, "evidence": "changed"})
    from ontology_runtime.capability import _encode
    signature = s.kms.sign(KeyId=s.config["evidenceKeyArn"], Message=hashlib.sha256(json.dumps({**body, "keyId": "evidence"},
        sort_keys=True, separators=(",", ":")).encode()).digest(), MessageType="DIGEST", SigningAlgorithm=ALGORITHM)["Signature"]
    assert not s.verifier.verify({**signed, "signature": _encode(signature)})


def allocated(s):
    job = s.ledger.dispatcher().allocate(s.owner, s.job_id, operation_id="c" * 32)
    token, claims, key = s.capabilities.issue(job)
    s.ledger.dispatcher().bind_capability(s.owner, s.job_id, job["attempt"]["id"], job["fence"],
        digest=hashlib.sha256(token.encode()).hexdigest(), claims=claims, key_check=key, operation_id="d" * 32)
    return job, token


def test_dispatch_duplicate_never_invokes_runtime_twice(integrated):
    s = integrated
    calls = []
    def invoke(**kwargs):
        calls.append(kwargs)
        event = json.loads(kwargs["payload"])
        result = s.workflow.analyze(event, kwargs["runtimeSessionId"])
        return {"response": io.BytesIO(json.dumps(result).encode())}
    dispatcher = Dispatcher(s.ledger, s.capabilities, SimpleNamespace(invoke_agent_runtime=invoke), s.config)
    assert dispatcher.dispatch(s.owner, s.job_id)["status"] == "succeeded"
    assert dispatcher.dispatch(s.owner, s.job_id)["status"] == "not-dispatched"
    assert len(calls) == len(s.invocations) == 1


def test_uncertain_runtime_response_is_recoverable_without_reinvocation(integrated):
    s = integrated
    calls = []
    def invoke(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("transport ended")
    dispatcher = Dispatcher(s.ledger, s.capabilities, SimpleNamespace(invoke_agent_runtime=invoke), s.config)
    assert dispatcher.dispatch(s.owner, s.job_id)["status"] == "recovery_required"
    assert dispatcher.dispatch(s.owner, s.job_id)["status"] == "not-dispatched"
    assert len(calls) == 1
    job = s.storage.get(s.owner, "job", s.job_id)
    assert job["unknownOutcome"] and job["attempt"]["dispatchIntentAt"]
    assert s.storage.get(s.owner, "wb_artifact", s.artifact_id)["status"] == "recovery_required"


@pytest.mark.parametrize("alter", ["key", "gateway", "membership", "cancel", "attempt", "token"])
def test_capability_rejects_changed_authority(integrated, alter):
    s = integrated
    job, token = allocated(s)
    access = ExecutionAccess(s.capabilities, token, s.metadata.client_context.custom)
    access.authorize(s.owner, s.job_id)
    if alter in {"key", "gateway"}:
        identifier = "cap" if alter == "key" else "gateway-binding"
        row = s.storage.get(KEY_OWNER, "ac_key", identifier)
        extra = {"state": "revoked"} if alter == "key" else {"targetId": "changed"}
        s.storage.put(KEY_OWNER, "ac_key", {**row, **extra}, row["version"])
    elif alter == "membership":
        project = s.storage.get(s.owner, "project", s.env.pid)
        members = dict(project["members"]); members.pop("alice")
        s.storage.put(s.owner, "project", {**project, "members": members}, project["version"])
    elif alter == "cancel":
        s.ledger.api().cancel(s.owner, s.job_id, actor="alice")
    elif alter == "attempt":
        from workspace.execution_ledger import _seed_for_tests
        current = s.storage.get(s.owner, "job", s.job_id)
        _seed_for_tests(s.storage, s.owner, "job", {**current, "fence": current["fence"] + 1}, current["version"])
    else:
        access.token = token[:-8] + "A" * 8
    with pytest.raises(Exception):
        access.authorize(s.owner, s.job_id)
    assert not s.invocations and s.storage.get(s.owner, "ontology", "project-current") is None


def test_reviewer_revocation_prevents_paid_dispatch(integrated):
    s = integrated
    from intake.records import INTAKE_OWNER
    grant = s.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    s.env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    calls = []
    dispatcher = Dispatcher(s.ledger, s.capabilities,
        SimpleNamespace(invoke_agent_runtime=lambda **kwargs: calls.append(kwargs)), s.config)
    dispatcher.dispatch(s.owner, s.job_id)
    assert not calls and not s.invocations
    assert s.storage.get(s.owner, "job", s.job_id)["status"] == "failed"


def test_publication_rejects_key_revocation_at_actual_transaction(integrated, monkeypatch):
    s = integrated
    original = s.storage.put_many
    def guarded(writes, checks=(), **kwargs):
        if any(write["kind"] == "job" and write["item"].get("status") == "succeeded" for write in writes):
            def revoke():
                row = s.storage.get(KEY_OWNER, "ac_key", "evidence")
                s.storage.put(KEY_OWNER, "ac_key", {**row, "state": "revoked"}, row["version"])
            s.storage._table.before_transaction = revoke
        return original(writes, checks, **kwargs)
    monkeypatch.setattr(s.storage, "put_many", guarded)
    with pytest.raises(LedgerError, match="conflict"):
        run(s)
    assert Ontology(s.ctx).current() is None
    assert s.storage.get(s.owner, "job", s.job_id)["status"] == "running"
    assert s.storage.get(s.owner, "wb_artifact", s.artifact_id)["status"] == "running"


def test_recovery_publishes_existing_evidence_without_new_interpreter_call(integrated, monkeypatch):
    s = integrated
    finish = s.workflow.gateway.call
    def interrupt(name, arguments, **kwargs):
        if arguments["action"] == "finish":
            raise TimeoutError()
        return finish(name, arguments, **kwargs)
    monkeypatch.setattr(s.workflow.gateway, "call", interrupt)
    with pytest.raises(TimeoutError):
        run(s)
    job = s.storage.get(s.owner, "job", s.job_id)
    s.ledger.dispatcher().dispatch_uncertain(s.owner, s.job_id, job["attempt"]["id"])
    final = next(output for row in job["stages"] for output in row["outputs"] if output["key"].endswith("/result.json"))
    result = {"manifestRef": final["key"], "manifestHash": final["sha256"], "receipts": [r["receiptHash"] for r in job["stages"]]}
    completed = s.ledger.reconciler().reconcile(s.owner, s.job_id, job["attempt"]["id"], receipts=[], status="succeeded",
        result=result, operation_id="recovery" * 4, stage_completion=SourcePublication(s.env.api, s.config))
    assert completed["status"] == "succeeded" and len(s.invocations) == 1
    assert s.storage.get(s.owner, "wb_artifact", s.artifact_id)["status"] == "completed"


def test_prior_resolver_carries_lineage_and_rejects_changed_contract(env):
    from test_sources_adapters import published_product, approved_contract, react_run
    product = published_product(env)
    contract = approved_contract(env, product)
    run = react_run(env, contract)
    row = run["rounds"][0]
    resolver = ExecutionSources(env.api)
    job = {"actor": "alice", "projectId": env.pid, "authorizationExpiresAt": env.api.storage.clock() + 3600000}
    prior = {"sourceKind": "run-round", "sourceId": run["id"], "revision": "1",
             "key": row["sourceKey"], "sha256": row["sourceArchiveSha256"]}
    result = resolver.prior("project:" + env.pid, job, prior)
    assert result["kind"] == "run" and {check["kind"] for check in result["checks"]} >= {"contract", "product", "guideline"}
    storage = env.api.storage
    storage.put("project:" + env.pid, "contract", {**contract, "status": "draft"}, contract["version"])
    with pytest.raises(Exception):
        resolver.prior("project:" + env.pid, job, prior)


def test_api_can_cancel_owned_job_after_input_revocation(integrated, monkeypatch):
    from ontology_runtime import execution_entrypoints as entry
    from intake.records import INTAKE_OWNER
    s = integrated
    grant = s.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    s.env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    monkeypatch.setenv("SOURCE_EXECUTION_CONFIGURATION", "configured")
    monkeypatch.setenv("SOURCE_EXECUTION_QUEUE_URL", "queue")
    session = SimpleNamespace(client=lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(entry, "dependencies", lambda: (s.env.api, s.config, s.ledger, s.verifier, s.capabilities, session, None))
    result = entry.api_operation(s.ctx, "cancel", {}, s.job_id)
    assert result["job"]["status"] == "cancelled"
    assert s.storage.get(s.owner, "wb_artifact", s.artifact_id)["status"] == "cancelled"


def test_staged_source_expiry_is_checked_after_final_receipt_verification(integrated, monkeypatch):
    s = integrated
    stage = SourcePublication.__call__
    original_clock = s.storage.clock
    clock = [original_clock()]
    s.storage.clock = lambda: clock[0]
    s.workflow.clock = s.storage.clock
    verify = s.verifier.verify
    expire_during_verify = []
    def staged(self, prepared):
        result = stage(self, prepared)
        result["expiresAt"] = clock[0] + 1000
        expire_during_verify.append(True)
        return result
    def verified(receipt):
        result = verify(receipt)
        if expire_during_verify:
            clock[0] += 1000
        return result
    monkeypatch.setattr(SourcePublication, "__call__", staged)
    monkeypatch.setattr(s.verifier, "verify", verified)
    with pytest.raises(LedgerError, match="authority-changed"):
        run(s)
    assert s.storage.get(s.owner, "ontology", "project-current") is None
    assert s.storage.get(s.owner, "wb_artifact", s.artifact_id)["status"] == "running"
