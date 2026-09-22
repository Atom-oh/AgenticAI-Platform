"""Actual Runtime orchestration; every source read crosses the scoped Gateway."""
from __future__ import annotations

import logging
from workspace import ontology_schema as schema
from ontology_runtime.capability import AuthorizationDenied
log = logging.getLogger("ontology.runtime")


class Workflow:
    def __init__(self, gateway, interpreter, memory, evidence):
        self.gateway, self.interpreter, self.memory, self.evidence = gateway, interpreter, memory, evidence

    def analyze(self, event, runtime_session_id):
        schema._fields(event, {"capability", "executionId", "runtimeSessionId", "workloadIdentity"})
        if not runtime_session_id or event["runtimeSessionId"] != runtime_session_id:
            raise AuthorizationDenied()
        capability = event["capability"]
        log.info("stage=authorize")

        def call(tool, arguments, operation_id):
            return self.gateway.call("ontology___" + tool, arguments,
                                     capability=capability, operation_id=operation_id)

        started = call("stage", {"stage": "context", "receiptHash": schema.digest({
            "executionId": event["executionId"], "runtimeSessionId": runtime_session_id})}, "stage-context")
        claims, request = started["scope"], started["request"]
        if (claims["executionId"] != event["executionId"] or claims["runtimeSessionId"] != runtime_session_id
                or claims["workloadIdentity"] != event["workloadIdentity"]
                or schema.digest(request) != claims["resourcesHash"]):
            raise AuthorizationDenied()
        if request["toolArchiveHash"] != self.interpreter.config["archiveHash"]:
            raise AuthorizationDenied()
        continuity = self.memory.read(claims)
        log.info("stage=context")
        context = call("context", {"nodeIds": request["nodeIds"]}, "ontology-context")
        # Memory contributes continuity hints only; actual source/context reads
        # and their current authorization are repeated through the Gateway.
        self.memory.append(claims, "context", schema.digest({"context": context, "continuity": continuity}))
        files = []
        log.info("stage=source")
        for index, file in enumerate(request["files"]):
            value = call("source", {"sourceRef": file["sourceRef"]}, f"source-{index}")
            if value["sourceRef"] != file["sourceRef"] or value["kind"] != file["kind"]:
                raise AuthorizationDenied()
            row = {"path": file["path"], "kind": file["kind"], "sha256": file["sourceRef"]["sha256"]}
            if file["kind"] != "asset":
                row["text"] = value["text"]
            files.append(row)
        payload = {"schemaVersion": 1, "files": files, "resolver": request["resolver"]}
        if schema.digest(payload) != request["inputHash"]:
            raise AuthorizationDenied()
        log.info("stage=analyze")
        value = self.interpreter(payload)
        log.info("stage=evidence")
        receipt = self.evidence.sign(claims, value)
        self.memory.append(claims, "analyzed", schema.digest(receipt))
        call("stage", {"stage": "analyzed", "receiptHash": schema.digest(receipt)}, "stage-analyzed")
        call("finish", {"manifestHash": schema.digest(value)}, "finish")
        return {**value, "runtimeReceipt": receipt}
