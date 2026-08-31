"""Invoke the design-draft harness: python scripts/invoke.py "계좌이체 화면 시안"."""
import json
import pathlib
import sys

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    brief = sys.argv[1] if len(sys.argv) > 1 else "하나은행 모바일 계좌이체 화면 시안"
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    client = boto3.client("bedrock-agentcore", region_name=cfg["region"])
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=cfg["runtime_arn"], qualifier="DEFAULT",
        payload=json.dumps({"brief": brief}, ensure_ascii=False).encode())
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    out = json.loads(body)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\ngallery: https://{cfg['distribution_domain']}/")


if __name__ == "__main__":
    main()
