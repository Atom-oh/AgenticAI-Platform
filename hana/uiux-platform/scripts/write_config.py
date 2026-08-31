"""Read HanaUiuxPlatform stack outputs into config/stack.json (idempotent merge)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEYMAP = {
    "AssetsBucket": "assets_bucket", "SkillsBucket": "skills_bucket",
    "DraftsBucket": "drafts_bucket", "RegistryTable": "registry_table",
    "UserPoolId": "user_pool_id", "M2MClientId": "m2m_client_id",
    "CognitoDomain": "cognito_domain", "CognitoDiscoveryUrl": "discovery_url",
    "DistributionDomain": "distribution_domain", "GatewayRoleArn": "gateway_role_arn",
    "RuntimeRoleArn": "runtime_role_arn", "FigmaSyncFn": "figma_sync_fn",
    "AssetToolsFnArn": "asset_tools_fn_arn", "FigmaSecretArn": "figma_secret_arn",
    "HistoryTable": "history_table", "DispatcherFn": "dispatcher_fn",
    "DistributionId": "distribution_id",
}


def main():
    cfn = boto3.client("cloudformation", region_name="ap-northeast-2")
    outputs = cfn.describe_stacks(StackName="HanaUiuxPlatform")["Stacks"][0]["Outputs"]
    path = ROOT / "config" / "stack.json"
    path.parent.mkdir(exist_ok=True)
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg.update({"region": "ap-northeast-2", "account": "180294183052"})
    cfg.update({KEYMAP[o["OutputKey"]]: o["OutputValue"]
                for o in outputs if o["OutputKey"] in KEYMAP})
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
