"""Admitted source-analysis admission and ledger-owned atomic publication."""
from __future__ import annotations

import copy
import hashlib
import json

from workspace import ontology_schema as schema
from workspace.execution_ledger import LedgerError
from workspace.execution_sources import ExecutionSources, context, reference, unique
from workspace.ontology_analysis import project_analysis, validate_analysis, validate_execution
from workspace.ontology_sources import Sources
from workspace.ontology_store import Ontology
from workbench.service import fields


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def submit(ctx, body, ledger, configuration, enqueue):
    fields(body, {"requestId", "name", "collectionDecisionId", "expectedGeneration"})
    ctx.authorization()
    name = schema._identifier(body.get("name"))
    identifier = ctx.identity("wb_artifact", body.get("requestId"))
    request_hash = schema.digest(body)
    prior = ctx.storage.get(ctx.owner, "wb_artifact", identifier)
    if prior:
        if (prior.get("requestHash") != request_hash or prior.get("createdBy") != ctx.actor
                or prior.get("executionSchemaVersion") != 1):
            raise LedgerError("request-changed")
        Sources(ctx).verify(prior["sourceRefs"])
        job = ledger.api().read(ctx.owner, prior["jobId"])
        if job["status"] == "queued":
            enqueue(ctx.owner, job["id"])
        return {"artifact": view_artifact(prior), "job": view_job(job)}
    current, _ = Ontology(ctx).authorize_publication(name)
    generation = (current or {}).get("generation")
    if "expectedGeneration" in body and body["expectedGeneration"] != generation:
        raise LedgerError("ontology-changed")
    decision, payload, checks, expiry = ExecutionSources(ctx.host).collection(ctx, body.get("collectionDecisionId"))
    ref = reference(decision)
    bindings = [{"sourceKind": ref["sourceKind"], "sourceId": ref["sourceId"], "revision": ref["revision"],
                 "audience": ref["audienceRevision"]}]
    admissions = [{"decisionId": decision["id"], "revision": str(decision["revision"]), "artifactHash": ref["sha256"]}]
    # Original path mappings, user partition names and administrative record bodies never leave the private store.
    manifest = {"schemaVersion": 1, "operation": "source.analyze", "payload": payload,
                "tool": configuration["interpreter"], "admissions": admissions,
                "sourceChecks": checks, "sourceBindings": bindings}
    raw = encode(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    key = ctx.storage.key_for(ctx.owner, "wb_artifact", identifier, "input-" + digest + ".json")
    ctx.storage.put_blob_once(key, raw, "application/json")
    project = ctx.scope["project"]
    job = ledger.api().admit(ctx.owner, request_key=body["requestId"], actor=ctx.actor, project_id=ctx.project_id,
        operation="source.analyze", model=None, manifest={"ref": key, "hash": digest}, admissions=admissions,
        backend_config_revision=configuration["revision"], authorization_expires_at=min(expiry, ctx.authorization()["authorizationExpiresAt"]),
        project_authority={"authorityRevision": project["authorityRevision"], "membershipDigest": schema.digest(project["members"])},
        completion_scope={"sourceChecks": len(checks), "sourceBindings": len(bindings), "nonSourceOperations": 20},
        artifact={"id": identifier, "name": name, "expectedGeneration": generation,
                  "sourceRefs": [ref], "requestHash": request_hash})
    enqueue(ctx.owner, job["id"])
    return {"artifact": view_artifact(ctx.get("wb_artifact", identifier)), "job": view_job(job)}


def view_job(job):
    return {key: copy.deepcopy(job[key]) for key in ("id", "status", "operation", "backend", "deadlineAt", "error") if key in job}


def view_artifact(artifact):
    return {key: copy.deepcopy(artifact[key]) for key in ("id", "name", "status", "jobId", "executionId",
        "executionSchemaVersion", "requestId", "createdBy", "createdAt", "updatedAt", "coverage", "generation") if key in artifact}


class SourcePublication:
    """The sole installed source.analyze staging adapter; never commits independently."""
    def __init__(self, host, configuration):
        self.host, self.configuration = host, configuration

    def __call__(self, prepared):
        job = prepared["job"]
        ctx = context(self.host, job)
        if job["operation"] != "source.analyze" or job["backendConfigRevision"] != self.configuration["revision"]:
            raise LedgerError("profile-invalid")
        artifact = ctx.get("wb_artifact", job["artifact"]["id"])
        if artifact.get("executionId") != job["id"] or any(artifact.get(k) != v for k, v in job["artifact"].items()):
            raise LedgerError("artifact-changed")
        raw = ctx.storage.get_blob(job["manifest"]["ref"])
        if hashlib.sha256(raw).hexdigest() != job["manifest"]["hash"]:
            raise LedgerError("manifest-invalid")
        manifest = json.loads(raw)
        decision, payload, checks, expiry = ExecutionSources(self.host).collection(ctx, job["admissions"][0]["decisionId"])
        if (len(job["admissions"]) != 1 or manifest["payload"] != payload
                or manifest["tool"] != self.configuration["interpreter"]
                or unique(checks) != unique(job["obligations"]["sourceChecks"])):
            raise LedgerError("authority-changed")
        stages = [row for row in job["stages"] if row["stage"] == "analyze" and row["attemptId"] == job["attempt"]["id"]]
        if len(stages) != 1 or stages[0]["service"]["kind"] != "interpreter":
            raise LedgerError("receipt-invalid")
        output = stages[0]["result"].get("analysis")
        if output not in stages[0]["outputs"]:
            raise LedgerError("receipt-invalid")
        raw = ctx.storage.get_blob(output["key"])
        if hashlib.sha256(raw).hexdigest() != output["sha256"] or len(raw) != output["size"]:
            raise LedgerError("receipt-invalid")
        result = json.loads(raw)
        validate_execution(result["execution"])
        execution = result["execution"]
        expected = self.configuration["interpreter"]
        if (execution["backend"] != "agentcore-code-interpreter" or execution["inputHash"] != schema.digest(payload)
                or execution["sessionId"] != stages[0]["service"]["sessionId"]
                or int(execution["nodeVersion"].split(".")[0][1:]) != expected["nodeMajor"]
                or any(execution.get(key) != expected[name] for key, name in (
                    ("toolArchiveHash", "archiveHash"), ("analyzerCodeHash", "analyzerCodeHash"),
                    ("dependencyLockHash", "dependencyLockHash"), ("interpreterId", "identifier"),
                    ("region", "region"), ("architecture", "architecture")))):
            raise LedgerError("receipt-invalid")
        validate_analysis(payload, result["analysis"])
        if stages[0]["result"].get("coverage") != result["analysis"]["coverage"]:
            raise LedgerError("receipt-invalid")
        ref = reference(decision)
        bindings = {file["path"]: {"ref": {**ref, "location": {"path": file["path"]}},
                                    "record": {"id": decision["id"]}} for file in payload["files"]}
        graph = project_analysis(ctx, artifact["name"], payload, bindings, result["analysis"])
        graph_key, graph_hash = ctx.put_json("wb_artifact", artifact["id"], "candidate/" + job["attempt"]["id"] + ".json", graph)
        store = Ontology(ctx)
        plan = store.publish_candidate(artifact["name"], graph,
            expected_generation=artifact["expectedGeneration"], request_id=artifact["id"],
            _producer="parser-extracted", _stage=True)
        published = plan["result"]
        write = ctx.write("wb_artifact", {**artifact, "status": "completed", "analysisKey": output["key"],
            "analysisHash": output["sha256"], "graphKey": graph_key, "graphHash": graph_hash,
            "execution": execution, "coverage": result["analysis"]["coverage"],
            "generation": published["generation"], "partitionId": published["partitionId"],
            "identities": published["identities"], "receiptHashes": job["result"]["receipts"]}, artifact["version"])
        fences, deadlines = store.sources.recheck_deadlines()
        expiry = min([expiry, *[bound for bound, _ in deadlines]])
        return {"writes": [*plan["writes"], write], "checks": unique([*checks, *plan["checks"], *fences]),
                "expiresAt": expiry,
                "sourceChecks": job["obligations"]["sourceChecks"], "sourceBindings": job["obligations"]["sourceBindings"]}
