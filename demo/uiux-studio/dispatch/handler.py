import json
import os

import boto3
from botocore.config import Config


def _agentcore():
    return boto3.client("bedrock-agentcore",
                        config=Config(read_timeout=850, retries={"total_max_attempts": 1}))


def _job_table():
    return boto3.resource("dynamodb").Table(os.environ["HISTORY_TABLE"])


def handler(event, context):
    job_id = event["job_id"]
    key = {"asset_id": f"job:{job_id}", "version": "job"}
    try:
        resp = _agentcore().invoke_agent_runtime(
            agentRuntimeArn=os.environ["RUNTIME_ARN"], qualifier="DEFAULT",
            payload=json.dumps({"brief": event["brief"],
                                "asset_ids": event.get("asset_ids", []),
                                "model_id": event.get("model_id", ""),
                                "agent_cfg": event.get("agent_cfg"),
                                "actor": event.get("actor", ""),
                                "mode": event.get("mode", "generate"),
                                "output_type": event.get("output_type", "design"),
                                "base_draft_id": event.get("base_draft_id", ""),
                                "selector": event.get("selector", ""),
                                "element_html": event.get("element_html", "")},
                               ensure_ascii=False).encode())
        body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
        out = json.loads(body)
        _job_table().update_item(
            Key=key, UpdateExpression="SET #s=:s, drafts=:d, summary=:m, #u=:u, model_id=:mid",
            ExpressionAttributeNames={"#s": "status", "#u": "usage"},
            ExpressionAttributeValues={":s": "done", ":d": out.get("drafts", []),
                                       ":m": out.get("summary", "")[:4000],
                                       ":u": {k: int(v) for k, v in
                                              (out.get("usage") or {}).items()},
                                       ":mid": out.get("model_id", "")})
    except Exception as e:
        _job_table().update_item(
            Key=key, UpdateExpression="SET #s=:s, #e=:e",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "failed", ":e": str(e)[:1000]})
