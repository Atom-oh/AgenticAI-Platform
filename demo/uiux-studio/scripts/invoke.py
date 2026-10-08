"""Invoke the design-draft harness: python scripts/invoke.py "계좌이체 화면 시안"."""
import json
import pathlib
import sys

import boto3
from botocore.config import Config

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_config import client as deployment_client, config_path, load_config, save_config


def main():
    brief = sys.argv[1] if len(sys.argv) > 1 else "고객사 A 모바일 계좌이체 화면 시안"
    cfg = load_config()
    client = deployment_client(cfg, "bedrock-agentcore", region_name=cfg["region"],
                     config=Config(read_timeout=900, connect_timeout=10, retries={"total_max_attempts": 1}))
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=cfg["runtime_arn"], qualifier="DEFAULT",
        payload=json.dumps({"brief": brief}, ensure_ascii=False).encode())
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    out = json.loads(body)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\ngallery: https://{cfg['distribution_domain']}/")


if __name__ == "__main__":
    main()
