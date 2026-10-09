"""Closed machine-only Gateway facade over the single B0 ledger."""
from __future__ import annotations

from ontology_runtime.capability import AuthorizationDenied
from ontology_runtime.execution_protocol import ExecutionAccess
from workspace import ontology_schema as schema
from workspace.execution_ledger import Ledger, LedgerError
from workspace.execution_sources import ExecutionSources
from workspace.execution_source import SourcePublication

# Exact arguments, including required vs optional fields. Owner/job/attempt/fence always come from the token.
ACTIONS = {
    "claim": (set(), set()), "heartbeat": (set(), set()),
    "open_manifest": (set(), set()), "open_input": ({"decision_id", "stage"}, set()),
    "open_prior": ({"ref"}, set()), "read_chunk": ({"handle_id", "index"}, set()),
    "open_output": ({"stage", "name", "total", "sha256"}, set()),
    "write_chunk": ({"handle_id", "index", "data"}, set()), "close_output": ({"handle_id"}, set()),
    "intent": ({"stage", "kind", "min_remaining_ms"}, set()),
    "outcome": ({"call_id", "status", "service_session_id"}, set()),
    "stage": ({"receipt"}, set()), "finish": ({"status", "result"}, set()), "fail": ({"code"}, set()),
}


class ExecutionTools:
    def __init__(self, host, verifier, capabilities, configuration):
        self.host, self.verifier, self.capabilities, self.configuration = host, verifier, capabilities, configuration

    def invoke(self, event, context):
        schema._fields(event, {"action", "arguments", "_executionAuthorization"})
        action, args, envelope = event["action"], event["arguments"], event["_executionAuthorization"]
        if not isinstance(action, str) or action not in ACTIONS:
            raise AuthorizationDenied()
        schema._fields(envelope, {"capability", "operationId"})
        schema._fields(args, *ACTIONS[action])
        metadata = getattr(getattr(context, "client_context", None), "custom", {}) or {}
        access = ExecutionAccess(self.capabilities, envelope["capability"], metadata, allow_success=action == "finish")
        claims, job, _, _ = self.capabilities.verify(envelope["capability"], allow_success=action == "finish")
        owner = "project:" + job["projectId"]
        access.authorize(owner, job["id"])
        ledger = Ledger.production(self.host.storage, verifier=self.verifier, sources=ExecutionSources(self.host), access=access)
        # Also protect replay-only paths: they do not necessarily call the writer themselves.
        checks, guard = ledger._protect(owner, job)
        ledger._fence(owner, job, checks, guard)
        if action == "claim" and job["status"] != "dispatched":
            raise LedgerError("already-claimed")
        if action == "intent" and (args["stage"] != "analyze" or args["kind"] != "interpreter"):
            raise LedgerError("call-invalid")
        kwargs = dict(args)
        if action not in {"heartbeat", "outcome"}:
            kwargs["operation_id"] = envelope["operationId"]
        if action == "finish":
            kwargs["stage_completion"] = SourcePublication(self.host, self.configuration)
        result = getattr(ledger.tool(), action)(owner, job["id"], claims["attemptId"], claims["fence"], **kwargs)
        if action == "read_chunk":
            return result
        if action.startswith("open_"):
            value = {key: result[key] for key in ("handleId", "total", "sha256", "chunks")}
            handle = result["job"]["handles"][result["handleId"]]
            value["key"] = handle["key"]
            return value
        if action == "close_output":
            output = next(row for row in result["transfers"] if row["handleId"] == args["handle_id"])
            return {key: output[key] for key in ("key", "sha256", "size")}
        if action == "intent":
            return {key: result[key] for key in ("callId", "replayed", "callStatus") if key in result}
        if action == "claim":
            return {"actor": job["actor"], "projectId": job["projectId"], "executionId": job["id"], "attemptId": claims["attemptId"], "fence": claims["fence"],
                    "sessionId": claims["runtimeSessionId"], "profileHash": job["profile"]["hash"],
                    "admissions": job["admissions"], "deadlineAt": job["deadlineAt"],
                    "heartbeatMs": job["profileBody"]["heartbeatMs"], "toolProfiles": job["profileBody"]["toolProfiles"]}
        return {"status": result["status"], "receiptHashes": [row["receiptHash"] for row in result.get("stages", [])]}
