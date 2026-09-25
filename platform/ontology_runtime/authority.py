"""IAM-only admission/dispatch; claims are derived from an already admitted job."""
from __future__ import annotations

import json
import hashlib
import secrets

from ontology_runtime import admission
from ontology_runtime.authorization import active_job, commit_execution
from ontology_runtime.capability import AuthorizationDenied
from ontology_runtime.tools import EXECUTIONS, KEYS
from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.collaboration import Collaboration
from workspace.ontology_analysis import source_input, validate_analysis
from workspace.ontology_store import Ontology


class Authority:
    def __init__(self, storage, runtime, capabilities, evidence, configuration):
        self.storage, self.runtime, self.capabilities, self.evidence = storage, runtime, capabilities, evidence
        self.config = configuration
        self.collaboration = Collaboration(storage)

    def dispatch(self, event):
        fields(event, {"projectId", "artifactId", "inputHash"})
        project_id = schema._identifier(event.get("projectId"))
        artifact_id = schema._identifier(event.get("artifactId"))
        ctx, artifact, job, sources, checks = active_job(self.storage, self.collaboration, project_id, artifact_id)
        pinned = artifact["jobInput"]
        if (pinned.get("backend", {}).get("name") != "agentcore"
                or pinned["backend"].get("toolArchiveHash") != self.config["toolArchiveHash"]):
            fail(409, "agentcore-backend-changed", "요청 당시의 실행 도구 버전이 변경되었습니다.")
        current, _ = Ontology(ctx).authorize_publication(pinned["name"])
        if (current or {}).get("generation") != pinned["expectedGeneration"]:
            raise AuthorizationDenied()
        checks.extend(sources.verify(pinned["sourceRefs"]))
        if current:
            checks.append(ctx.check("ontology", current))
        # Each admitted source contributes its source fence and a distinct
        # classification fence. Keep every operation within DynamoDB's limit.
        if len(pinned["sourceRefs"]) > 40:
            fail(422, "agentcore-source-budget", "AgentCore 분석 묶음은 원본 40개 이하로 나누세요.")
        admissions = [admission.require(ctx, reference) for reference in pinned["sourceRefs"]]
        checks.extend(admissions)
        payload, bindings = source_input(ctx, pinned["files"], pinned["resolver"])
        from ontology_runtime.inspection import inspect_payload
        boundary = inspect_payload(payload)
        if schema.digest(payload) != event.get("inputHash"):
            fail(409, "agentcore-input-changed", "승인한 분석 입력이 변경되었습니다.")
        for file, reference in zip(pinned["files"], pinned["sourceRefs"]):
            if bindings[file["path"]]["ref"] != reference:
                raise AuthorizationDenied()
        marker_id = schema.identity("dispatch", project_id, artifact_id)
        marker = self.storage.get(EXECUTIONS, "ac_operation", marker_id)
        if marker:
            previous = self.storage.get(EXECUTIONS, "ac_execution", marker["executionId"])
            if (not previous or previous.get("projectId") != project_id
                    or previous.get("actor") != ctx.actor or previous.get("artifactId") != artifact_id
                    or previous.get("authorityHash") != pinned["authorityHash"]
                    or previous.get("admissions") != admissions
                    or self.storage.clock() // 1000 >= previous.get("deadline", 0)):
                raise AuthorizationDenied()
            # Runtime invocation is at most once for this admitted artifact.
            # A new attempt requires a new authorized job, never an implicit retry.
            if previous.get("status") == "completed" and previous.get("inputHash") == event["inputHash"]:
                key = previous["resultKey"]
                if not self.storage.owns_key(EXECUTIONS, key) or self.storage.blob_info(key)["size"] > 4_000_000:
                    raise AuthorizationDenied()
                raw_result = self.storage.get_blob(key, length=4_000_000)
                if hashlib.sha256(raw_result).hexdigest() != previous["resultHash"]:
                    raise AuthorizationDenied()
                result = json.loads(raw_result)
                self.evidence.verify(previous["capabilityClaims"],
                    {key: value for key, value in result.items() if key != "runtimeReceipt"}, result["runtimeReceipt"])
                sources.recheck()
                return result
            fail(409, "agentcore-already-dispatched", "이미 실행된 작업입니다. 현재 작업 상태를 확인하세요.")
        identity = "execution-" + secrets.token_hex(24)
        now = self.storage.clock() // 1000
        gateway_binding = self.storage.get(KEYS, "ac_key", "gateway-binding")
        if (not gateway_binding or not gateway_binding.get("workloadIdentityArn")
                or gateway_binding["workloadIdentityArn"] != self.config["workloadIdentityArn"]):
            raise AuthorizationDenied()
        request = {"files": [{"path": file["path"], "kind": file["kind"],
                             "sourceRef": bindings[file["path"]]["ref"]} for file in payload["files"]],
                   "resolver": payload["resolver"], "inputHash": event["inputHash"], "nodeIds": [],
                   "toolArchiveHash": self.config["toolArchiveHash"], "admissions": admissions, "boundary": boundary}
        ledger = {"id": identity, "executionId": identity, "projectId": project_id, "actor": pinned["actor"],
            "attemptId": "attempt-" + secrets.token_hex(24), "runtimeSessionId": "session-" + secrets.token_hex(24),
            "workloadIdentity": gateway_binding["workloadIdentityArn"], "operations": [
                "ontology.context", "ontology.source", "execution.stage", "execution.finish"],
            "resourcesHash": schema.digest(request), "request": request, "inputHash": event["inputHash"],
            "authorizationExpiresAt": pinned["authorizationExpiresAt"] // 1000, "deadline": min(now + 840, pinned["authorizationExpiresAt"] // 1000),
            "status": "admitted", "stage": "admitted", "sourceRefs": pinned["sourceRefs"],
            "files": request["files"], "nodeIds": [], "graphGeneration": pinned["expectedGeneration"],
            "artifactId": artifact_id, "authorityHash": pinned["authorityHash"], "admissions": admissions,
            "calls": 0, "retrievedBytes": 0, "ttl": now + 30 * 86400}
        capability, binding = self.capabilities.issue(ledger, self.config["capabilityKeyId"])
        ledger.update(binding)
        sources.recheck()
        commit_execution(ctx, [self.collaboration._write(EXECUTIONS, "ac_execution", ledger),
                    self.collaboration._write(EXECUTIONS, "ac_operation", {
                        "id": marker_id, "projectId": project_id, "executionId": identity,
                        "artifactId": artifact_id, "inputHash": event["inputHash"], "ttl": ledger["ttl"]})], checks)
        try:
            response = self.runtime.invoke_agent_runtime(agentRuntimeArn=self.config["runtimeArn"],
                qualifier=self.config["runtimeQualifier"],
                runtimeSessionId=ledger["runtimeSessionId"], contentType="application/json", accept="application/json",
                payload=json.dumps({"capability": capability, "executionId": identity,
                    "runtimeSessionId": ledger["runtimeSessionId"], "workloadIdentity": ledger["workloadIdentity"]},
                    separators=(",", ":")).encode())
            body = response["response"]
            try:
                raw = body.read(4_500_001)
            finally:
                body.close()
        except Exception:
            fail(503, "agentcore-outcome-unknown", "Runtime 응답을 확인하지 못했습니다. 실행 상태와 비용을 확인하세요.")
        if len(raw) > 4_500_000:
            fail(503, "agentcore-result-limit", "실행 결과가 전송 한도를 초과했습니다.")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("error"):
            fail(503, "agentcore-execution-failed", "AgentCore 작업이 완료되지 않았습니다.")
        receipt = value.pop("runtimeReceipt")
        if value.get("execution", {}).get("toolArchiveHash") != self.config["toolArchiveHash"]:
            raise AuthorizationDenied()
        self.evidence.verify(binding["capabilityClaims"], value, receipt)
        validate_analysis(payload, value["analysis"])
        current = self.storage.get(EXECUTIONS, "ac_execution", identity)
        if (current.get("status") != "result-ready" or current.get("manifestHash") != schema.digest(value)
                or current["attemptId"] != ledger["attemptId"]):
            raise AuthorizationDenied()
        sources.recheck()
        if self.storage.clock() // 1000 >= current["deadline"]:
            raise AuthorizationDenied()
        result = {**value, "runtimeReceipt": receipt}
        key = self.storage.key_for(EXECUTIONS, "ac_execution", identity, "result.json")
        raw_result = schema.canonical(result)
        digest = hashlib.sha256(raw_result).hexdigest()
        self.storage.put_blob_once(key, raw_result, "application/json")
        ctx, live_artifact, _, sources, checks = active_job(self.storage, self.collaboration,
            project_id, artifact_id, deadline=current["deadline"])
        if live_artifact["jobInput"] != pinned:
            raise AuthorizationDenied()
        checks.extend(sources.verify(pinned["sourceRefs"]))
        current_admissions = [admission.require(ctx, reference) for reference in pinned["sourceRefs"]]
        if current_admissions != admissions:
            raise AuthorizationDenied()
        checks.extend(current_admissions)
        checks.extend(sources.recheck())
        if self.storage.clock() // 1000 >= current["deadline"]:
            raise AuthorizationDenied()
        commit_execution(ctx, [self.collaboration._write(EXECUTIONS, "ac_execution",
            {**current, "status": "completed", "resultKey": key, "resultHash": digest}, current["version"])], checks)
        return result
