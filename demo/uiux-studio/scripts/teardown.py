"""Preview or remove the exact resources in a private deployment record, including legacy stacks."""
from __future__ import annotations

import argparse
import json
import pathlib
import os
import sys
import time

from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_config import load_config, session_for, validate_config


def _missing(error):
    return isinstance(error, ClientError) and error.response.get("Error", {}).get("Code") in {
        "ResourceNotFoundException", "RepositoryNotFoundException", "ResourceNotFound"}


def _paginate(fn, key, **kwargs):
    items, token = [], None
    while True:
        value = fn(**kwargs, **({"nextToken": token} if token else {}))
        items.extend(value.get(key, []))
        token = value.get("nextToken")
        if not token:
            return items


def plan(cfg):
    validate_config(cfg)
    resources = []
    if cfg.get("runtime_arn"):
        resources.append({"kind": "runtime", "id": cfg["runtime_arn"].split("/", 1)[1]})
    for key, kind in [("gateway_id", "gateway"), ("memory_id", "memory"),
                      ("ecr_repository", "repository"), ("m2m_secret_arn", "secret")]:
        if cfg.get(key):
            resources.append({"kind": kind, "id": cfg[key]})
    return {"account": cfg["account"], "region": cfg["region"], "stack": cfg["stack_id"], "resources": resources}


def _wait_gone(read, **kwargs):
    for _ in range(60):
        try:
            read(**kwargs)
        except ClientError as error:
            if _missing(error):
                return
            raise
        time.sleep(5)
    raise TimeoutError("Resource deletion is still pending; dependent resources were preserved")


def remove(cfg, session, *, include_stack=False):
    inventory = plan(cfg)
    ac = session.client("bedrock-agentcore-control")
    for item in inventory["resources"]:
        kind, identifier = item["kind"], item["id"]
        try:
            if kind == "runtime":
                actual = ac.get_agent_runtime(agentRuntimeId=identifier)
                if actual["agentRuntimeArn"] != cfg["runtime_arn"] or actual["roleArn"] != cfg["runtime_role_arn"]:
                    raise ValueError("Runtime differs from the selected deployment record")
                ac.delete_agent_runtime(agentRuntimeId=identifier)
                _wait_gone(ac.get_agent_runtime, agentRuntimeId=identifier)
            elif kind == "gateway":
                actual = ac.get_gateway(gatewayIdentifier=identifier)
                if actual["roleArn"] != cfg["gateway_role_arn"]:
                    raise ValueError("Gateway differs from the selected deployment record")
                for target in _paginate(ac.list_gateway_targets, "items", gatewayIdentifier=identifier):
                    ac.delete_gateway_target(gatewayIdentifier=identifier, targetId=target["targetId"])
                    _wait_gone(ac.get_gateway_target, gatewayIdentifier=identifier, targetId=target["targetId"])
                ac.delete_gateway(gatewayIdentifier=identifier)
                _wait_gone(ac.get_gateway, gatewayIdentifier=identifier)
            elif kind == "memory":
                ac.delete_memory(memoryId=identifier)
                _wait_gone(ac.get_memory, memoryId=identifier)
            elif kind == "repository":
                session.client("ecr").delete_repository(repositoryName=identifier, force=True)
            elif kind == "secret":
                secrets = session.client("secretsmanager")
                if not secrets.describe_secret(SecretId=identifier).get("DeletedDate"):
                    secrets.delete_secret(SecretId=identifier, RecoveryWindowInDays=7)
        except ClientError as error:
            if not _missing(error):
                raise
        print(f"removed or scheduled {kind}: {identifier}")
    if include_stack:
        session.client("cloudformation").delete_stack(StackName=cfg["stack_id"])
        print("requested deletion of the recorded stack; monitor its actual CloudFormation status")
    else:
        print("Stack retained:", cfg["stack_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Private observed record; otherwise BANK_UIUX_CONFIG")
    parser.add_argument("--execute", action="store_true", help="Delete the printed resources; default is preview only")
    parser.add_argument("--include-stack", action="store_true", help="Also delete the exact recorded CloudFormation stack")
    args = parser.parse_args()
    cfg = load_config(args.config)
    session = session_for(cfg["account"], cfg["region"], os.environ.get("BANK_UIUX_PROFILE") or cfg.get("profile"))
    print(json.dumps(plan(cfg), indent=2))
    if args.execute:
        remove(cfg, session, include_stack=args.include_stack)


if __name__ == "__main__":
    main()
