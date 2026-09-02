"""Post-deploy verification: figma-sync → MCP tools via gateway → harness → gallery."""
import base64
import json
import pathlib
import urllib.parse
import urllib.request

import boto3
from botocore.config import Config

ROOT = pathlib.Path(__file__).resolve().parents[1]


def m2m_token(cfg):
    client = boto3.client("cognito-idp", region_name=cfg["region"])
    secret = client.describe_user_pool_client(
        UserPoolId=cfg["user_pool_id"],
        ClientId=cfg["m2m_client_id"])["UserPoolClient"]["ClientSecret"]
    basic = base64.b64encode(f"{cfg['m2m_client_id']}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "scope": "hana-mcp/invoke"}).encode()
    req = urllib.request.Request(f"https://{cfg['cognito_domain']}/oauth2/token", data=data,
                                 headers={"Authorization": f"Basic {basic}",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def mcp_call(cfg, token, method, params, rpc_id):
    req = urllib.request.Request(cfg["gateway_url"], method="POST", data=json.dumps(
        {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
    # streamable HTTP may answer as SSE; extract the data line
    if body.startswith("event:") or "\ndata:" in body or body.startswith("data:"):
        body = next(l[5:] for l in body.splitlines() if l.startswith("data:"))
    return json.loads(body)


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    file_key = json.loads((ROOT / "config" / "figma.json").read_text())["file_key"]

    print("1. figma-sync ...")
    lam = boto3.client("lambda", region_name=cfg["region"])
    r = lam.invoke(FunctionName=cfg["figma_sync_fn"],
                   Payload=json.dumps({"file_key": file_key}).encode())
    sync = json.loads(r["Payload"].read())
    assert "synced" in sync, sync
    print("   ", sync)

    print("2. gateway MCP tools ...")
    token = m2m_token(cfg)
    tools = mcp_call(cfg, token, "tools/list", {}, 1)["result"]["tools"]
    names = {t["name"].split("___")[-1] for t in tools}
    assert "list_design_tokens" in names and "get_skill" in names, names
    tok = mcp_call(cfg, token, "tools/call", {
        "name": next(t["name"] for t in tools if t["name"].endswith("list_design_tokens")),
        "arguments": {}}, 2)["result"]
    assert "008485" in json.dumps(tok), tok
    print("    tokens OK,", len(tools), "tools")

    print("3. harness invoke (takes a few minutes) ...")
    ac = boto3.client("bedrock-agentcore", region_name=cfg["region"],
                     config=Config(read_timeout=900, connect_timeout=10, retries={"total_max_attempts": 1}))
    resp = ac.invoke_agent_runtime(agentRuntimeArn=cfg["runtime_arn"], qualifier="DEFAULT",
                                   payload=json.dumps({"brief": "모바일 계좌이체 화면 시안"},
                                                      ensure_ascii=False).encode())
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    out = json.loads(body)
    assert len(out.get("drafts", [])) == 3, out
    print("    drafts:", [d["url"] for d in out["drafts"]])

    print("4. gallery + draft render ...")
    with urllib.request.urlopen(f"https://{cfg['distribution_domain']}/drafts.json") as r:
        manifest = json.loads(r.read())
    assert len(manifest["drafts"]) >= 3
    with urllib.request.urlopen(out["drafts"][0]["url"]) as r:
        assert b"<html" in r.read()[:2000].lower()
    print("\nALL GREEN — gallery:", f"https://{cfg['distribution_domain']}/")


if __name__ == "__main__":
    main()
