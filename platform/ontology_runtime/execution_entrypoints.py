"""Deployment-owned B0 dependencies, queue dispatcher, tools and watchdog."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from types import SimpleNamespace

import boto3
from botocore.config import Config

from ontology_runtime.execution_protocol import ExecutionCapabilities
from ontology_runtime.execution_tools import ExecutionTools
from workspace.collaboration import Collaboration
from workspace.execution_ledger import Ledger, LedgerError
from workspace.execution_source import SourcePublication, submit, view_job
from workspace.execution_sources import ExecutionSources
from workspace.execution_verifier import KmsVerifier
from workspace.storage import Storage


def configuration(value=None):
    result = json.loads(value if value is not None else os.environ["SOURCE_EXECUTION_CONFIGURATION"])
    if (not isinstance(result, dict) or set(result) != {"protocol", "revision", "capabilityKeyId", "evidenceKeyId",
            "evidenceKeyArn", "workloadIdentityArn", "interpreter"}
            or result["protocol"] != "platform-execution/1" or not isinstance(result["revision"], str)
            or not result["revision"]):
        raise ValueError("A versioned source-execution deployment is required")
    return result


def dependencies():
    config = configuration()
    session = boto3.Session(region_name=os.environ["AWS_REGION"])
    options = Config(connect_timeout=10, read_timeout=850, retries={"total_max_attempts": 1})
    storage = Storage(single_attempt=True)
    host = SimpleNamespace(storage=storage, collaboration=Collaboration(storage),
                           intake_package_registry=json.loads(os.environ.get("INTAKE_PACKAGE_REGISTRY", "{}")))
    kms = session.client("kms", config=Config(connect_timeout=10, read_timeout=30, retries={"total_max_attempts": 1}))
    verifier = KmsVerifier(storage, kms, [config["evidenceKeyId"]])
    ledger = Ledger.production(storage, verifier=verifier, sources=ExecutionSources(host))
    return host, config, ledger, verifier, ExecutionCapabilities(storage, kms, config), session, options


class Dispatcher:
    def __init__(self, ledger, capabilities, client, config):
        self.ledger, self.capabilities, self.client, self.config = ledger, capabilities, client, config

    def dispatch(self, owner, identifier):
        try:
            job = self.ledger.dispatcher().allocate(owner, identifier, operation_id=secrets.token_hex(24))
        except LedgerError as error:
            if error.code in {"not-queued", "terminal", "deadline", "authority-changed", "not-an-execution"}:
                return {"status": "not-dispatched"}
            raise
        attempt = job["attempt"]["id"]
        try:
            manifest_bytes = self.ledger.storage.get_blob(job["manifest"]["ref"])
            if (hashlib.sha256(manifest_bytes).hexdigest() != job["manifest"]["hash"]
                    or json.loads(manifest_bytes)["tool"] != self.config["interpreter"]):
                raise ValueError("Pinned execution configuration changed")
            token, claims, key = self.capabilities.issue(job)
            job = self.ledger.dispatcher().bind_capability(owner, identifier, attempt, job["fence"],
                digest=hashlib.sha256(token.encode()).hexdigest(), claims=claims, key_check=key,
                operation_id=secrets.token_hex(24))
            payload = {"capability": token, "executionId": job["id"], "runtimeSessionId": job["attempt"]["sessionId"],
                       "workloadIdentity": self.config["workloadIdentityArn"]}
            response = self.client.invoke_agent_runtime(agentRuntimeArn=self.config["runtimeArn"],
                qualifier=self.config["runtimeQualifier"], runtimeSessionId=job["attempt"]["sessionId"],
                contentType="application/json", payload=json.dumps(payload).encode())
            stream = response.get("response")
            try:
                raw = stream.read(65537) if stream else b""
                if len(raw) > 65536 or not isinstance(json.loads(raw), dict):
                    raise ValueError("Invalid Runtime acknowledgement")
            finally:
                if stream:
                    stream.close()
            current = self.ledger.api().read(owner, identifier)
            if current["status"] not in {"succeeded", "failed", "cancelled", "expired"}:
                self.ledger.dispatcher().dispatch_uncertain(owner, identifier, attempt)
            return view_job(self.ledger.api().read(owner, identifier))
        except Exception:
            # A dispatch intent may already have caused paid work. Never invoke it again on queue retry.
            current = self.ledger.dispatcher().dispatch_uncertain(owner, identifier, attempt)
            return view_job(current)


def dispatch_handler(event, context):
    _, config, ledger, _, capabilities, session, options = dependencies()
    runtime = json.loads(os.environ["SOURCE_EXECUTION_RUNTIME"])
    if set(runtime) != {"runtimeArn", "runtimeQualifier"} or not runtime["runtimeQualifier"].startswith("v"):
        raise ValueError("An immutable Runtime endpoint is required")
    dispatcher = Dispatcher(ledger, capabilities, session.client("bedrock-agentcore", config=options), {**config, **runtime})
    failures = []
    for row in event.get("Records", []):
        try:
            value = json.loads(row["body"])
            if set(value) != {"owner", "jobId"} or value["owner"] != "project:" + ledger.api().read(value["owner"], value["jobId"])["projectId"]:
                raise ValueError("Invalid dispatch reference")
            dispatcher.dispatch(value["owner"], value["jobId"])
        except Exception:
            failures.append({"itemIdentifier": row["messageId"]})
    return {"batchItemFailures": failures}


def tools_handler(event, context):
    try:
        host, config, _, verifier, capabilities, _, _ = dependencies()
        return ExecutionTools(host, verifier, capabilities, config).invoke(event, context)
    except LedgerError as error:
        return {"error": error.code}
    except Exception:
        return {"error": "execution-not-authorized"}


def watchdog_handler(event, context):
    host, config, ledger, _, _, _, _ = dependencies()
    # The due index discovers jobs; no caller supplies a replacement authority or arbitrary job mutation.
    return {"handled": ledger.reconciler().run_due(limit=100)}


def reconcile_handler(event, context):
    host, config, ledger, _, _, _, _ = dependencies()
    if set(event) != {"owner", "jobId"}:
        raise ValueError("Only a persisted execution reference is accepted")
    job = ledger.api().read(event["owner"], event["jobId"])
    stages = job.get("stages", [])
    outputs = [output for row in stages if row["stage"] == "analyze" for output in row["outputs"]
               if output["key"].endswith("/analyze/result.json")]
    if len(outputs) != 1:
        return {"status": "evidence-incomplete"}
    result = {"manifestRef": outputs[0]["key"], "manifestHash": outputs[0]["sha256"],
              "receipts": [row["receiptHash"] for row in stages]}
    completed = ledger.reconciler().reconcile(event["owner"], job["id"], job["attempt"]["id"], receipts=[],
        status="succeeded", result=result, operation_id=hashlib.sha256((job["id"] + job["attempt"]["id"] + "reconcile").encode()).hexdigest(),
        stage_completion=SourcePublication(host, config))
    return view_job(completed)


def api_operation(ctx, action, body=None, identifier=None):
    if not os.environ.get("SOURCE_EXECUTION_CONFIGURATION") or not os.environ.get("SOURCE_EXECUTION_QUEUE_URL"):
        from workbench.service import fail
        fail(503, "source-execution-unavailable", "검증된 소스 분석 실행 환경이 아직 구성되지 않았습니다.")
    host, config, ledger, _, _, session, _ = dependencies()
    # Fresh single-attempt clients: never mutate the legacy API's shared retrying Storage object.
    from workbench.service import Service
    request = Service(host, host.collaboration.resolve_scope(ctx.actor, ctx.project_id), ctx.claims)
    queue = session.client("sqs", config=Config(retries={"total_max_attempts": 1}))
    def enqueue(owner, identifier):
        queue.send_message(QueueUrl=os.environ["SOURCE_EXECUTION_QUEUE_URL"],
                           MessageBody=json.dumps({"owner": owner, "jobId": identifier}))
    if action == "submit":
        return submit(request, body, ledger, config, enqueue)
    job = ledger.api().read(request.owner, identifier)
    from workbench.service import fields
    if action == "cancel":
        fields(body or {}, set())
        # Stopping owned work remains available when its input grant was withdrawn.
        return {"job": view_job(ledger.api().cancel(request.owner, identifier, actor=request.actor))}
    artifact = request.get("wb_artifact", job["artifact"]["id"])
    from workspace.ontology_sources import Sources
    Sources(request).verify(artifact["sourceRefs"])
    if action == "retry":
        from workbench.service import fields
        fields(body, {"acknowledgeUnknownOutcome"})
        job = ledger.api().retry(request.owner, identifier, actor=request.actor,
                                acknowledge_unknown_outcome=body.get("acknowledgeUnknownOutcome") is True)
        enqueue(request.owner, identifier)
    elif action != "read":
        raise ValueError("Unknown execution operation")
    return {"job": view_job(job)}
