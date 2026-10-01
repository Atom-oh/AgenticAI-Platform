"""IAM-only admission/dispatch; claims are derived from an already admitted job."""
from __future__ import annotations

import json
import hashlib
import secrets

from ontology_runtime import admission
from ontology_runtime.authorization import active_job, attempt_deadline, commit_execution, key_check, key_deadline
from ontology_runtime.capability import AuthorizationDenied
from ontology_runtime.tools import EXECUTIONS, KEYS
from workbench.service import fail, fields, reader_deadlines
from workspace import ontology_schema as schema
from workspace.collaboration import Collaboration
from workspace.ontology_analysis import source_input, validate_analysis
from workspace.ontology_store import Ontology


class Authority:
    def __init__(self, storage, runtime, capabilities, evidence, configuration):
        self.storage, self.runtime, self.capabilities, self.evidence = storage, runtime, capabilities, evidence
        self.config = configuration
        self.collaboration = Collaboration(storage)

    def _keys(self, key_id, claims, verified):
        """Both registry keys, revalidated now (state, KMS and exact versions)."""
        return self.capabilities.active(key_id), self.evidence.active(claims, verified)

    def _deliver(self, ctx, checks, key_id, claims, verified, deadline, collected=()):
        """The aggregate final delivery guard of a replay: a check-only
        transaction fencing `checks` plus both keys' exact versions, whose
        guard collects both keys' validity windows and the attempt deadline
        with the deadlines already collected by the caller's reads."""
        keys = self._keys(key_id, claims, verified)

        def guard():
            current = self._keys(key_id, claims, verified)
            return [*collected, *(key_deadline(record) for record in current), attempt_deadline(deadline)]
        commit_execution(ctx, [], [*checks, *(key_check(record) for record in keys)], guard=guard)

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
        admission.preflight_sources(pinned["sourceRefs"])
        source_checks = sources.verify(pinned["sourceRefs"])
        checks.extend(source_checks)
        if current:
            checks.append(ctx.check("ontology", current))
        # Each `require()` call returns the admission check plus its exact pinned
        # policy/provenance/grant revision checks (source-admission/1); flatten
        # them into one fenced list, and keep that flat list as `admissions` for
        # the identity comparisons below (a drift in any of them is a difference).
        admissions = [check for reference in pinned["sourceRefs"] for check in admission.require(ctx, reference)]
        # ONT-10: recheck the complete source-check budget before any paid work.
        admission.preflight([*source_checks, *admissions])
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
                evidence_key = self.evidence.verify(previous["capabilityClaims"],
                    {key: value for key, value in result.items() if key != "runtimeReceipt"}, result["runtimeReceipt"])
                # source-admission/1 final delivery check (AUTH-04/08): this
                # branch writes nothing, so its linearization point is one
                # check-only transaction over every fence -- the artifact/job,
                # ontology, source and admission records, both registry keys
                # and this execution -- taken after the cached result read.
                # Every deadline (source access, intake decision/policy/
                # provenance/grant expiry, token, both keys' validity and the
                # execution deadline) is collected by the reads and compared
                # once, with a single clock read taken after the last of them.
                fences, reader = sources.recheck_deadlines()
                self._deliver(ctx, [*checks, *fences, {"owner": EXECUTIONS, "kind": "ac_execution",
                              "id": previous["id"], "version": previous["version"]}],
                              previous["capabilityKeyId"], previous["capabilityClaims"], evidence_key,
                              previous["deadline"], reader_deadlines(reader))
                return result
            fail(409, "agentcore-already-dispatched", "이미 실행된 작업입니다. 현재 작업 상태를 확인하세요.")
        identity = "execution-" + secrets.token_hex(24)
        signing_key = self.capabilities.active(self.config["capabilityKeyId"])
        if type(signing_key.get("notAfter")) is not int:
            raise AuthorizationDenied()
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
            "authorizationExpiresAt": pinned["authorizationExpiresAt"] // 1000,
            # AUTH-04: the attempt (and so the capability `exp`) never outlives
            # the signing key's validity window at issue time.
            "deadline": min(now + 840, pinned["authorizationExpiresAt"] // 1000, signing_key["notAfter"]),
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
                        "artifactId": artifact_id, "inputHash": event["inputHash"], "ttl": ledger["ttl"]})],
                    [*checks, key_check(signing_key)],
                    guard=lambda: [key_deadline(self.capabilities.active(binding["capabilityKeyId"])),
                                   attempt_deadline(ledger["deadline"])])
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
        evidence_key = self.evidence.verify(binding["capabilityClaims"], value, receipt)
        # AUTH-04: a capability revoked while Runtime was executing must still
        # abort finalization; the evidence key alone does not stand in for it.
        self.capabilities.active(binding["capabilityKeyId"])
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
        current_admissions = [check for reference in pinned["sourceRefs"] for check in admission.require(ctx, reference)]
        if current_admissions != admissions:
            raise AuthorizationDenied()
        checks.extend(current_admissions)
        final_sources = sources.recheck()
        checks.extend(final_sources)
        # ONT-10: recheck the final source-check count before the commit.
        admission.preflight([*final_sources, *current_admissions])
        # AUTH-04 / execution-capability/1: revalidate both registry keys (the
        # capability key and the evidence key that verified the receipt)
        # immediately before the commit attempt and carry their exact registry
        # versions into the completion transaction itself -- a revocation
        # racing this very commit must still abort it.
        key_record, evidence_record = self._keys(binding["capabilityKeyId"], binding["capabilityClaims"], evidence_key)
        checks.extend([key_check(key_record), key_check(evidence_record)])
        if self.storage.clock() // 1000 >= current["deadline"]:
            raise AuthorizationDenied()
        # AUTH-04: key expiry changes no registry version, so the version
        # fences above cannot see it. Before every attempt the completion guard
        # rereads both keys and joins their validity windows and the attempt
        # deadline with the source/admission deadlines, compared with one
        # clock read taken after the last read.
        commit_execution(ctx, [self.collaboration._write(EXECUTIONS, "ac_execution",
            {**current, "status": "completed", "resultKey": key, "resultHash": digest}, current["version"])], checks,
            guard=lambda: [*(key_deadline(record) for record in self._keys(
                binding["capabilityKeyId"], binding["capabilityClaims"], evidence_key)),
                attempt_deadline(current["deadline"])])
        return result
