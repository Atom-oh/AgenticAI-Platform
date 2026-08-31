"""Remove AgentCore + ECR resources (run before `cdk destroy`)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    region = cfg["region"]
    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    for rt in ac.list_agent_runtimes()["agentRuntimes"]:
        if rt["agentRuntimeName"] == "hana_design_harness":
            ac.delete_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
            print("deleted runtime", rt["agentRuntimeId"])
    for gw in ac.list_gateways()["items"]:
        if gw["name"] == "hana-design-assets-gw":
            gid = gw["gatewayId"]
            for t in ac.list_gateway_targets(gatewayIdentifier=gid)["items"]:
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
