import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

from ingestion.normalizer import normalize_figma_file

FIGMA_API = "https://api.figma.com/v1"


def fetch_figma_file(file_key: str, token: str) -> dict:
    req = urllib.request.Request(f"{FIGMA_API}/files/{file_key}",
                                 headers={"X-Figma-Token": token})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def _figma_token() -> str:
    sm = boto3.client("secretsmanager")
    return sm.get_secret_value(SecretId=os.environ["FIGMA_SECRET_ID"])["SecretString"]


def handler(event, context):
    file_key = event["file_key"]
    normalized = normalize_figma_file(fetch_figma_file(file_key, _figma_token()))
    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(os.environ["REGISTRY_TABLE"])
    bucket = os.environ["ASSETS_BUCKET"]
    now = datetime.now(timezone.utc).isoformat()

    s3.put_object(Bucket=bucket, Key="tokens/latest.json",
                  Body=json.dumps(normalized, ensure_ascii=False).encode(),
                  ContentType="application/json")
    table.put_item(Item={"asset_id": "token:latest", "type": "token", "name": "hana-tokens",
                         "version": "latest", "s3_key": "tokens/latest.json",
                         "figma_node_id": file_key, "updated_at": now})
    for comp in normalized["components"]:
        key = f"components/{comp['node_id']}.json"
        s3.put_object(Bucket=bucket, Key=key,
                      Body=json.dumps(comp, ensure_ascii=False).encode(),
                      ContentType="application/json")
        table.put_item(Item={"asset_id": f"component:{comp['node_id']}", "type": "component",
                             "name": comp["name"], "version": "latest", "s3_key": key,
                             "figma_node_id": comp["node_id"], "updated_at": now})
    return {"synced": {"tokens": 1, "components": len(normalized["components"])}}
