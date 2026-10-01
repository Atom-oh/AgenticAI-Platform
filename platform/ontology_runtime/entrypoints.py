"""Deployment-owned dependency construction; user input never selects a backend."""
from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.config import Config

from ontology_runtime.authority import Authority
from ontology_runtime.capability import Capabilities, Evidence
from ontology_runtime.tools import Tools, KEYS
from workspace.storage import Storage
from workspace.collaboration import CollaborationError


def dependencies():
    session = boto3.Session(region_name=os.environ["AWS_REGION"])
    storage = Storage()
    configuration = json.loads(os.environ["ONTOLOGY_CONFIGURATION"])
    registry = lambda identifier: storage.get(KEYS, "ac_key", identifier) if identifier == configuration["capabilityKeyId"] else None
    kms = session.client("kms")
    capabilities = Capabilities(kms, registry)
    evidence = Evidence(kms, configuration["evidenceKeyArn"],
        lambda: storage.get(KEYS, "ac_key", configuration["evidenceKeyId"]))
    return session, storage, configuration, capabilities, evidence


def authority_handler(event, context):
    try:
        session, storage, configuration, capabilities, evidence = dependencies()
        client = session.client("bedrock-agentcore", config=Config(connect_timeout=10, read_timeout=850,
            retries={"total_max_attempts": 1}))
        return Authority(storage, client, capabilities, evidence, configuration).dispatch(event)
    except CollaborationError as error:
        return {"error": error.code, "message": error.message}
    except Exception:
        # No upstream exception/headers, source payloads or capability values.
        logging.getLogger("ontology.authority").warning("AgentCore authority execution failed")
        return {"error": "agentcore-execution-unavailable"}


def tools_handler(event, context):
    try:
        _, storage, configuration, capabilities, _ = dependencies()
        gateway = storage.get(KEYS, "ac_key", "gateway-binding")
        if not gateway or not gateway.get("workloadIdentityArn"):
            return {"error": "gateway-not-configured"}
        return Tools(storage, capabilities, gateway_id=gateway["gatewayId"], target_id=gateway["targetId"],
            target_name="ontology", workload=gateway["workloadIdentityArn"]).invoke(event, context)
    except Exception:
        return {"error": "execution-not-authorized"}
