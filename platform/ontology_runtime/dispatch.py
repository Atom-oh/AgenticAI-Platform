"""Workspace transport to the separate IAM execution authority."""
from __future__ import annotations

import json
import os
import re

from workbench.service import fail
from workspace import ontology_schema as schema


class RuntimeAnalyzer:
    backend = "agentcore-code-interpreter"

    def __init__(self, function_arn, expected_archive_hash, client=None):
        if not re.fullmatch(r"arn:aws:lambda:[a-z0-9-]+:\d{12}:function:[A-Za-z0-9_-]+:[1-9]\d*", function_arn):
            raise ValueError("An immutable execution authority Lambda version is required")
        schema._hash(expected_archive_hash)
        self.function, self.archive, self.client = function_arn, expected_archive_hash, client

    def configuration(self):
        return {"name": "agentcore", "authorityArn": self.function, "toolArchiveHash": self.archive}

    def analyze(self, ctx, pinned, payload):
        if self.client is None:
            import boto3
            from botocore.config import Config
            self.client = boto3.client("lambda", region_name=self.function.split(":")[3],
                config=Config(connect_timeout=10, read_timeout=880,
                retries={"total_max_attempts": 1}))
        try:
            response = self.client.invoke(FunctionName=self.function, InvocationType="RequestResponse",
                Payload=json.dumps({"projectId": ctx.project_id, "artifactId": pinned["artifactId"],
                    "inputHash": schema.digest(payload)}, separators=(",", ":")).encode())
        except Exception:
            fail(503, "agentcore-outcome-unknown", "실행 응답을 확인하지 못했습니다. 비용이 발생했을 수 있으므로 작업 상태를 확인하세요.")
        stream = response["Payload"]
        try:
            raw = stream.read(4_500_001)
        except Exception:
            fail(503, "agentcore-outcome-unknown", "실행 결과를 수신하지 못했습니다. 비용이 발생했을 수 있으므로 작업 상태를 확인하세요.")
        finally:
            stream.close()
        if response.get("FunctionError"):
            fail(503, "agentcore-outcome-unknown", "실행 결과가 불명확합니다. 재시도 전에 작업 상태와 비용을 확인하세요.")
        if len(raw) > 4_500_000:
            fail(503, "agentcore-execution-unavailable", "AgentCore 실행 결과를 확인하지 못했습니다.")
        result = json.loads(raw)
        if not isinstance(result, dict):
            fail(503, "agentcore-execution-unavailable", "AgentCore 실행 기록의 형식이 올바르지 않습니다.")
        if result.get("error"):
            if result["error"] == "agentcore-outcome-unknown":
                fail(503, "agentcore-outcome-unknown", "실행 결과가 불명확합니다. 재시도 전에 작업 상태와 비용을 확인하세요.")
            fail(503, "agentcore-execution-unavailable", "AgentCore 작업이 완료되지 않았습니다. 원본 분류와 실행 상태를 확인하세요.")
        if (result.get("execution", {}).get("backend") != self.backend
                or result["execution"].get("toolArchiveHash") != self.archive
                or result["execution"].get("inputHash") != schema.digest(payload)
                or not result.get("runtimeReceipt")):
            fail(409, "agentcore-execution-mismatch", "AgentCore 실행 근거가 승인한 요청·도구와 다릅니다.")
        return result


def install(host):
    if os.environ.get("ONTOLOGY_ANALYZER_BACKEND") != "agentcore":
        return
    function = os.environ.get("ONTOLOGY_EXECUTION_AUTHORITY_ARN", "")
    archive = os.environ.get("ONTOLOGY_TOOL_ARCHIVE_HASH", "")
    if not function or not archive:
        return
    host.ontology_analyzer = RuntimeAnalyzer(function, archive)
    host.ontology_analyzer_ready = True


def selected_backend(host):
    analyzer = getattr(host, "ontology_analyzer", None)
    return analyzer.configuration() if type(analyzer) is RuntimeAnalyzer else {"name": "local-offline"}
