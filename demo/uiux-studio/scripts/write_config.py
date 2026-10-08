"""Record actual CloudFormation outputs and explicitly selected service identities outside Git."""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_config import config_path, load_config, save_config, session_for, validate_config

KEYMAP = {
    "AssetsBucket": "assets_bucket", "SkillsBucket": "skills_bucket", "DraftsBucket": "drafts_bucket",
    "RegistryTable": "registry_table", "UserPoolId": "user_pool_id", "M2MClientId": "m2m_client_id",
    "CognitoDomain": "cognito_domain", "CognitoDiscoveryUrl": "discovery_url",
    "DistributionDomain": "distribution_domain", "GatewayRoleArn": "gateway_role_arn",
    "RuntimeRoleArn": "runtime_role_arn", "FigmaSyncFn": "figma_sync_fn",
    "AssetToolsFnArn": "asset_tools_fn_arn", "FigmaSecretArn": "figma_secret_arn",
    "HistoryTable": "history_table", "DispatcherFn": "dispatcher_fn", "SpaClientId": "spa_client_id",
    "DistributionId": "distribution_id", "FeedbackFn": "feedback_fn", "McpScope": "mcp_scope",
    "ModelResources": "configured_model_resources",
}


def capture(session, stack_name, *, account, region, runtime_id=None, gateway_id=None, memory_id=None, model_profiles=(), previous=None):
    stack = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    cfg = {"schema_version": 1, "status": "observed", "account": account, "region": region,
           "stack_name": stack["StackName"], "stack_id": stack["StackId"],
           "stack_status": stack["StackStatus"],
           "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    cfg.update({KEYMAP[o["OutputKey"]]: o["OutputValue"] for o in stack.get("Outputs", [])
                if o["OutputKey"] in KEYMAP})
    required = {"assets_bucket", "skills_bucket", "drafts_bucket", "user_pool_id",
                "runtime_role_arn", "gateway_role_arn", "dispatcher_fn"}
    if required - cfg.keys():
        raise ValueError("The selected stack does not expose the UI/UX deployment outputs")
    validate_config(cfg)
    # Retain prior services only for this exact stack instance, and query each
    # again below; a same-name replacement stack cannot inherit stale IDs.
    if previous and previous.get("stack_id") == cfg["stack_id"]:
        runtime_id = runtime_id or previous.get("runtime_arn", "").split("/")[-1] or None
        gateway_id = gateway_id or previous.get("gateway_id")
        memory_id = memory_id or previous.get("memory_id")
        model_profiles = model_profiles or previous.get("model_profiles", [])
    cfg["configured_model_resources"] = json.loads(cfg.get("configured_model_resources", "[]"))
    if model_profiles:
        bedrock = session.client("bedrock")
        resources = []
        for profile in model_profiles:
            observed = bedrock.get_inference_profile(inferenceProfileIdentifier=profile)
            resources.extend([observed["inferenceProfileArn"], *(item["modelArn"] for item in observed["models"])])
        cfg["model_profiles"] = list(model_profiles)
        cfg["model_id"] = model_profiles[0]
        cfg["model_resources"] = sorted(set(resources))
    if runtime_id or gateway_id or memory_id:
        ac = session.client("bedrock-agentcore-control")
        if runtime_id:
            rt = ac.get_agent_runtime(agentRuntimeId=runtime_id)
            if rt["roleArn"] != cfg["runtime_role_arn"]:
                raise ValueError("Runtime is not bound to the recorded stack role")
            cfg.update(runtime_arn=rt["agentRuntimeArn"], runtime_name=rt["agentRuntimeName"])
            uri = rt.get("agentRuntimeArtifact", {}).get("containerConfiguration", {}).get("containerUri", "")
            prefix = f"{account}.dkr.ecr.{region}.amazonaws.com/"
            if uri.startswith(prefix):
                cfg["ecr_repository"] = uri[len(prefix):].split("@", 1)[0].rsplit(":", 1)[0]
            secret = rt.get("environmentVariables", {}).get("M2M_SECRET_ARN")
            if secret:
                cfg["m2m_secret_arn"] = secret
        if gateway_id:
            gw = ac.get_gateway(gatewayIdentifier=gateway_id)
            if gw["roleArn"] != cfg["gateway_role_arn"]:
                raise ValueError("Gateway is not bound to the recorded stack role")
            cfg.update(gateway_id=gw["gatewayId"], gateway_url=gw["gatewayUrl"], gateway_name=gw["name"])
        if memory_id:
            memory = ac.get_memory(memoryId=memory_id)["memory"]
            cfg.update(memory_id=memory["id"], memory_name=memory["name"], memory_arn=memory["arn"])
    return validate_config(cfg)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", required=True, help="Actual stack name or ARN, including an existing legacy stack")
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--output", help="Private file; defaults to BANK_UIUX_CONFIG or .local/uiux-studio/deployment.json")
    parser.add_argument("--runtime-id")
    parser.add_argument("--gateway-id")
    parser.add_argument("--memory-id")
    parser.add_argument("--model-profile", action="append", default=[], help="Exact selected inference profile; queried, never guessed")
    args = parser.parse_args()
    session = session_for(args.account, args.region, args.profile)
    previous = load_config(args.output) if config_path(args.output).is_file() else None
    cfg = capture(session, args.stack, account=args.account, region=args.region,
                  runtime_id=args.runtime_id, gateway_id=args.gateway_id, memory_id=args.memory_id,
                  model_profiles=args.model_profile, previous=previous)
    if args.profile:
        cfg["profile"] = args.profile
    print(f"wrote observed deployment record: {save_config(cfg, args.output)}")


if __name__ == "__main__":
    main()
