"""Create (or update) the AgentCore Gateway exposing design-asset MCP tools."""
import json
import pathlib
import sys
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_config import client as deployment_client, config_path, load_config, save_config, require_fields
from mcp.tool_schemas import TOOL_SCHEMAS  # noqa: E402

GW_NAME = "bank-design-assets-gw"
TARGET_NAME = "design-asset-tools"


def _paginate(fn, key, **kwargs):
    """Collect every item across pages for a nextToken-paginated bedrock-agentcore-control call."""
    items, token = [], None
    while True:
        call_kwargs = dict(kwargs)
        if token:
            call_kwargs["nextToken"] = token
        resp = fn(**call_kwargs)
        items.extend(resp.get(key, []))
        token = resp.get("nextToken")
        if not token:
            return items


def main():
    cfg_path = config_path()
    cfg = load_config(cfg_path, for_deploy=True)
    require_fields(cfg, 'gateway_role_arn', 'discovery_url', 'm2m_client_id', 'asset_tools_fn_arn')
    client = deployment_client(cfg, "bedrock-agentcore-control", region_name=cfg["region"])

    gws = _paginate(client.list_gateways, "items")
    gw = next((g for g in gws if g["name"] == GW_NAME), None)
    if gw is None:
        gw = client.create_gateway(
            name=GW_NAME,
            roleArn=cfg["gateway_role_arn"],
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={"customJWTAuthorizer": {
                "discoveryUrl": cfg["discovery_url"],
                "allowedClients": [cfg["m2m_client_id"]]}},
            description="Org-shared Bank design asset MCP")
    gw_id = gw["gatewayId"]
    for attempt in range(60):
        detail = client.get_gateway(gatewayIdentifier=gw_id)
        if detail["status"] in ("READY", "FAILED"):
            break
        print(f"[{attempt+1}/60] gateway status: {detail['status']}")
        time.sleep(5)
    else:
        raise TimeoutError(f"gateway not READY after 300s (status: {detail.get('status')})")
    assert detail["status"] == "READY", detail
    if detail["roleArn"] != cfg["gateway_role_arn"]:
        raise ValueError("Existing gateway belongs to a different execution role")

    targets = _paginate(client.list_gateway_targets, "items", gatewayIdentifier=gw_id)
    target_cfg = {"mcp": {"lambda": {
        "lambdaArn": cfg["asset_tools_fn_arn"],
        "toolSchema": {"inlinePayload": TOOL_SCHEMAS}}}}
    creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    existing = next((t for t in targets if t["name"] == TARGET_NAME), None)
    if existing:
        client.update_gateway_target(gatewayIdentifier=gw_id, targetId=existing["targetId"],
                                     name=TARGET_NAME, targetConfiguration=target_cfg,
                                     credentialProviderConfigurations=creds)
    else:
        client.create_gateway_target(gatewayIdentifier=gw_id, name=TARGET_NAME,
                                     targetConfiguration=target_cfg,
                                     credentialProviderConfigurations=creds)

    cfg["gateway_id"] = gw_id
    cfg["gateway_name"] = detail["name"]
    cfg["gateway_arn"] = detail["gatewayArn"]
    cfg["gateway_url"] = detail["gatewayUrl"]
    save_config(cfg, cfg_path)
    print(f"gateway READY: {detail['gatewayUrl']}")


if __name__ == "__main__":
    main()
