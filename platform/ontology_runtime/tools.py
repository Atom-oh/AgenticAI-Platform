"""Separate Gateway Lambda authority; no bank plane, bridge or Registry tools."""
from __future__ import annotations

import copy

from ontology_runtime import admission
from ontology_runtime.authorization import active_job, commit_execution
from ontology_runtime.capability import AuthorizationDenied
from ontology_runtime.identity import RESERVED
from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.collaboration import Collaboration
from workspace.ontology_store import Ontology

EXECUTIONS = "ontology-executions"
KEYS = "ontology-key-registry"
STAGES = {"admitted": {"context"}, "context": {"analyzed", "generated"},
          "generated": {"compiled"}, "compiled": {"verified"}, "analyzed": {"completed"},
          "verified": {"completed"}}
TOOLS = {"context": "ontology.context", "source": "ontology.source", "stage": "execution.stage", "finish": "execution.finish"}


class Tools:
    def __init__(self, storage, capabilities, *, gateway_id, target_id, target_name, workload):
        self.storage, self.capabilities = storage, capabilities
        self.gateway, self.target, self.name, self.workload = gateway_id, target_id, target_name, workload
        self.collaboration = Collaboration(storage)

    def load(self, identifier):
        return self.storage.get(EXECUTIONS, "ac_execution", identifier)

    def invoke(self, event, context):
        metadata = getattr(getattr(context, "client_context", None), "custom", {}) or {}
        if (metadata.get("bedrockAgentCoreGatewayId") != self.gateway
                or metadata.get("bedrockAgentCoreTargetId") != self.target):
            raise AuthorizationDenied()
        names = {self.name + "___" + key: value for key, value in TOOLS.items()}
        operation = names.get(metadata.get("bedrockAgentCoreToolName"))
        if not operation or not isinstance(event, dict):
            raise AuthorizationDenied()
        envelope = event.get(RESERVED)
        try:
            schema._fields(envelope, {"capability", "operationId"})
            operation_id = schema._identifier(envelope["operationId"])
        except (ValueError, TypeError):
            raise AuthorizationDenied() from None
        claims, ledger = self.capabilities.verify(envelope["capability"], self.load,
            operation=operation, workload=self.workload, allow_result_ready=operation == "execution.finish")
        ctx, artifact, _, sources, checks = active_job(self.storage, self.collaboration,
            claims["projectId"], ledger["artifactId"], deadline=min(claims["exp"], ledger["deadline"]))
        if (ctx.actor != claims["actor"] or artifact["jobInput"]["sourceRefs"] != ledger["sourceRefs"]
                or artifact["jobInput"]["authorityHash"] != ledger.get("authorityHash")):
            raise AuthorizationDenied()
        ctx.fresh({"owner", "planner", "designer", "developer"})
        arguments = {key: value for key, value in event.items() if key != RESERVED}
        fingerprint = schema.digest({"operation": operation, "arguments": arguments})
        marker_id = schema.identity("operation", claims["executionId"], claims["attemptId"], operation_id)
        prior = self.storage.get(EXECUTIONS, "ac_operation", marker_id)
        if ledger.get("status") == "result-ready" and not prior:
            raise AuthorizationDenied()
        if prior and prior["requestHash"] != fingerprint:
            fail(409, "execution-operation-changed", "같은 작업 ID의 내용이 다릅니다.")
        if not prior and (ledger.get("calls", 0) >= 60 or ledger.get("retrievedBytes", 0) > 4 * 1024 * 1024):
            fail(422, "execution-budget", "실행의 도구 조회 한도를 초과했습니다.")
        admissions = [admission.require(ctx, reference) for reference in ledger["sourceRefs"]]
        if admissions != ledger.get("admissions"):
            raise AuthorizationDenied()
        checks.extend(admissions)
        sources.verify(ledger["sourceRefs"])
        manifest = Ontology(ctx).current()
        if (manifest or {}).get("generation") != ledger["graphGeneration"]:
            raise AuthorizationDenied()
        if manifest:
            checks.append(ctx.check("ontology", manifest))
        if operation == "ontology.source":
            fields(arguments, {"sourceRef"})
            reference = schema.source_ref(arguments.get("sourceRef"))
            if reference not in ledger["sourceRefs"]:
                raise AuthorizationDenied()
            file = next((file for file in ledger["files"] if file["sourceRef"] == reference), None)
            if not file:
                raise AuthorizationDenied()
            value = sources.resolve(reference, text=file["kind"] != "asset")
            result = {"sourceRef": reference, "text": value["text"], "kind": file["kind"]}
        elif operation == "ontology.context":
            fields(arguments, {"nodeIds"})
            if arguments.get("nodeIds") != ledger.get("nodeIds", []):
                raise AuthorizationDenied()
            if arguments["nodeIds"]:
                result = Ontology(ctx).context(arguments["nodeIds"])
                if result["generation"] != ledger["graphGeneration"]:
                    fail(409, "execution-context-changed", "실행의 온톨로지 기준이 변경되었습니다.")
                if any(ref not in ledger["sourceRefs"] for ref in result["sourceRefs"]):
                    raise AuthorizationDenied()
            else:
                result = {"nodes": [], "edges": [], "generation": ledger["graphGeneration"],
                    "coverage": {"complete": False, "unknown": ["no-ontology-selection"]}}
        elif operation == "execution.stage":
            fields(arguments, {"stage", "receiptHash"})
            stage = arguments.get("stage")
            schema._hash(arguments.get("receiptHash"))
            if not prior and stage not in STAGES.get(ledger["stage"], set()):
                fail(409, "execution-stage", "실행 단계 순서가 올바르지 않습니다.")
            result = {"stage": stage, "receiptHash": arguments["receiptHash"],
                "runtimeSessionId": claims["runtimeSessionId"], "executionId": claims["executionId"],
                "attemptId": claims["attemptId"]}
            if stage == "context":
                result.update(scope=claims, request=ledger["request"])
        else:
            fields(arguments, {"manifestHash"})
            schema._hash(arguments.get("manifestHash"))
            if not prior and ledger["stage"] not in {"analyzed", "verified"}:
                fail(409, "execution-stage", "검증된 실행 결과가 필요합니다.")
            result = {"manifestHash": arguments["manifestHash"], "stage": "completed"}
        # Reads are never replayed without current source/admission checks.
        checks.extend(sources.recheck())
        _, latest = self.capabilities.verify(envelope["capability"], self.load,
            operation=operation, workload=self.workload, allow_result_ready=operation == "execution.finish")
        if latest["version"] != ledger["version"]:
            fail(409, "execution-changed", "실행 기록이 변경되었습니다.")
        if prior:
            if prior["resultHash"] != schema.digest(result):
                fail(409, "execution-result-changed", "이전 작업과 현재 결과의 근거가 다릅니다.")
            return result
        size = len(schema.canonical(result))
        if size > 300000 or ledger.get("retrievedBytes", 0) + size > 4 * 1024 * 1024:
            fail(422, "execution-result-limit", "도구 결과의 전송 한도를 초과했습니다.")
        changed = copy.deepcopy(ledger)
        changed.update(calls=ledger.get("calls", 0) + 1, retrievedBytes=ledger.get("retrievedBytes", 0) + size)
        if operation == "execution.stage":
            changed["stage"] = arguments["stage"]
            changed["stageReceipts"] = {**ledger.get("stageReceipts", {}), arguments["stage"]: arguments["receiptHash"]}
        if operation == "execution.finish":
            changed.update(stage="completed", status="result-ready", manifestHash=arguments["manifestHash"])
        marker = {"id": marker_id, "projectId": ctx.project_id, "executionId": claims["executionId"],
            "requestHash": fingerprint, "resultHash": schema.digest(result), "operation": operation,
            "expiresAt": claims["exp"] * 1000, "ttl": claims["exp"] + 86400}
        commit_execution(ctx, [
            self.collaboration._write(EXECUTIONS, "ac_execution", changed, ledger["version"]),
            self.collaboration._write(EXECUTIONS, "ac_operation", marker),
        ], checks)
        return result
