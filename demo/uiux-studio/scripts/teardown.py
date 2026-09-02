"""Remove AgentCore + ECR resources (run before `cdk destroy`)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


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
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    region = cfg["region"]
    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    for rt in _paginate(ac.list_agent_runtimes, "agentRuntimes"):
        if rt["agentRuntimeName"] == "hana_design_harness":
            ac.delete_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
            print("deleted runtime", rt["agentRuntimeId"])
    for gw in _paginate(ac.list_gateways, "items"):
        if gw["name"] == "hana-design-assets-gw":
            gid = gw["gatewayId"]
            for t in _paginate(ac.list_gateway_targets, "items", gatewayIdentifier=gid):
                ac.delete_gateway_target(gatewayIdentifier=gid, targetId=t["targetId"])
            ac.delete_gateway(gatewayIdentifier=gid)
            print("deleted gateway", gid)
    ecr = boto3.client("ecr", region_name=region)
    try:
        ecr.delete_repository(repositoryName="hana-design-harness", force=True)
        print("deleted ECR repo")
    except ecr.exceptions.RepositoryNotFoundException:
        pass
    sm = boto3.client("secretsmanager", region_name=region)
    for sid in ("hana/m2m-client-secret",):
        try:
            sm.delete_secret(SecretId=sid, ForceDeleteWithoutRecovery=True)
            print("deleted secret", sid)
        except sm.exceptions.ResourceNotFoundException:
            pass
    print("now run: cd infra && cdk destroy  (and revoke the Figma PAT)")


if __name__ == "__main__":
    main()
