"""Create (or find) the AgentCore Memory resource for designer preferences.

Idempotent by name; waits for ACTIVE; writes memory_id into config/stack.json
and merges MEMORY_ID into the platform-api and dispatcher-adjacent env vars
(the runtime gets it via deploy_runtime.py).
"""
import json
import pathlib
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from scripts.deployment_config import client as deployment_client, config_path, load_config, save_config, require_fields
MEMORY_NAME = "bank_design_memory"


def _paginate(fn, list_key, **kwargs):
    items, token = [], None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = fn(**kwargs)
        items.extend(page.get(list_key, []))
        token = page.get("nextToken")
        if not token:
            return items


def main():
    cfg_path = config_path()
    cfg = load_config(cfg_path, for_deploy=True)
    require_fields(cfg, 'feedback_fn')
    if not cfg.get("feedback_fn"):
        raise ValueError("Refresh actual stack outputs before configuring Memory")
    ac = deployment_client(cfg, "bedrock-agentcore-control", region_name=cfg["region"])

    memories = _paginate(ac.list_memories, "memories")
    mem = next((m for m in memories if m.get("id", "").startswith(MEMORY_NAME)
                or m.get("name") == MEMORY_NAME), None)
    if mem is None:
        created = ac.create_memory(
            name=MEMORY_NAME,
            description="Designer preference & feedback memory for the Bank UI/UX platform",
            eventExpiryDuration=90,
            memoryStrategies=[{"semanticMemoryStrategy": {
                "name": "designerPreferences",
                "namespaces": ["/designers/{actorId}"]}}])
        mem = created["memory"]
    memory_id = mem.get("id") or mem.get("memoryId")

    for attempt in range(60):
        detail = ac.get_memory(memoryId=memory_id)["memory"]
        status = detail["status"]
        print(f"[{attempt + 1}/60] memory status: {status}")
        if status in ("ACTIVE", "FAILED"):
            break
        time.sleep(10)
    assert status == "ACTIVE", status

    cfg["memory_id"] = memory_id
    cfg["memory_arn"] = detail["arn"]
    cfg["memory_name"] = detail["name"]
    save_config(cfg, cfg_path)

    lam = deployment_client(cfg, "lambda", region_name=cfg["region"])
    for fn in (cfg["feedback_fn"],):
        env = lam.get_function_configuration(FunctionName=fn)["Environment"]["Variables"]
        env["MEMORY_ID"] = memory_id
        lam.update_function_configuration(FunctionName=fn, Environment={"Variables": env})
        print(f"updated {fn} MEMORY_ID")
    print(f"memory ACTIVE: {memory_id}")


if __name__ == "__main__":
    main()
