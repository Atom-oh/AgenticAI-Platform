"""Dedicated IAM-authenticated AgentCore Runtime, separate from the bank agents."""
from __future__ import annotations

import json
import logging
import os
import re

import boto3
import httpx
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.config import Config

from ontology_runtime.capability import Evidence
from ontology_runtime.identity import Gateway
from ontology_runtime.interpreter import Interpreter
from ontology_runtime.memory import Memory
from ontology_runtime.workflow import Workflow
from workspace import ontology_schema as schema

app = BedrockAgentCoreApp()
log = logging.getLogger("ontology.runtime")
log.setLevel(logging.INFO)
log.addHandler(logging.StreamHandler())
log.propagate = False


@app.entrypoint
def invoke(payload, context):
    try:
        configuration = json.loads(os.environ["ONTOLOGY_RUNTIME_CONFIGURATION"])
        schema._fields(payload, {"capability", "executionId", "runtimeSessionId", "workloadIdentity"})
        workload = payload["workloadIdentity"]
        if workload != configuration["workloadIdentityArn"]:
            raise ValueError("The registered Gateway machine identity is required")
        session = boto3.Session(region_name=os.environ["AWS_REGION"])
        log.info("runtimePrincipal=%s", session.client("sts").get_caller_identity()["Arn"])
        client = session.client("bedrock-agentcore", config=Config(connect_timeout=10, read_timeout=60,
            retries={"total_max_attempts": 1}))
        with httpx.Client(trust_env=False) as http:
            # The signed execution carries user/project authority; this separate
            # machine identity brokers only the dedicated Gateway credential.
            gateway = Gateway(client, http, endpoint=configuration["gatewayUrl"], workload=configuration["workload"],
                provider=configuration["provider"], scopes=["ontology/tools"])
            workflow = Workflow(gateway, Interpreter(client, configuration["interpreter"]),
                Memory(client, configuration["memoryId"]), Evidence(session.client("kms"), configuration["evidenceKeyArn"]))
            return workflow.analyze(payload, context.session_id)
    except Exception as error:
        operation = getattr(error, "operation_name", None)
        log.warning("Execution failed: type=%s operation=%s", type(error).__name__,
                    operation if isinstance(operation, str) and operation.isidentifier() else "internal")
        if getattr(error, "response", {}).get("Error", {}).get("Code") == "AccessDeniedException":
            # Emit only AWS resource identifiers and fixed reason categories,
            # never the upstream message, token, request headers or payload.
            message = error.response["Error"].get("Message", "")
            resources = re.findall(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[A-Za-z0-9_./*-]+", message)
            reasons = [label for text, label in [
                ("no identity-based policy", "missing-identity-policy"),
                ("explicit deny", "explicit-deny"), ("permissions boundary", "permissions-boundary"),
                ("service control policy", "organization-policy")] if text in message.lower()]
            log.warning("Authorization resources=%s reasons=%s", sorted(set(resources))[:8], reasons)
        return {"error": "agentcore-workflow-failed"}


if __name__ == "__main__":
    app.run()
