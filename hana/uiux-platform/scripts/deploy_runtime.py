"""Build/push the harness image (linux/arm64) and create/update the AgentCore Runtime."""
import base64
import json
import pathlib
import subprocess
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = "hana-design-harness"
RUNTIME = "hana_design_harness"


def sh(*args):
    print("+", " ".join(args))
    subprocess.run(args, check=True, cwd=ROOT)


def main():
    cfg_path = ROOT / "config" / "stack.json"
    cfg = json.loads(cfg_path.read_text())
    region, account = cfg["region"], cfg["account"]
    ecr = boto3.client("ecr", region_name=region)
    try:
        ecr.create_repository(repositoryName=REPO)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user_pw = base64.b64decode(auth["authorizationToken"]).decode()
    registry = auth["proxyEndpoint"].removeprefix("https://")
    subprocess.run(["docker", "login", "-u", "AWS", "--password-stdin", registry],
                   input=user_pw.split(":", 1)[1].encode(), check=True)
    uri = f"{account}.dkr.ecr.{region}.amazonaws.com/{REPO}:latest"
    sh("docker", "buildx", "build", "--platform", "linux/arm64",
       "-f", "harness/Dockerfile", "-t", uri, "--push", ".")

    client_desc = boto3.client("cognito-idp", region_name=region).describe_user_pool_client(
        UserPoolId=cfg["user_pool_id"], ClientId=cfg["m2m_client_id"])
    # store client secret in Secrets Manager so the runtime never gets it via env
    sm = boto3.client("secretsmanager", region_name=region)
    secret_value = client_desc["UserPoolClient"]["ClientSecret"]
    try:
        sec = sm.create_secret(Name="hana/m2m-client-secret", SecretString=secret_value)
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId="hana/m2m-client-secret", SecretString=secret_value)
        sec = sm.describe_secret(SecretId="hana/m2m-client-secret")
    boto3.client("iam").put_role_policy(
        RoleName="hana-agentcore-runtime", PolicyName="read-m2m-secret",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
            "Resource": sec["ARN"]}]}))

    env = {"GATEWAY_URL": cfg["gateway_url"],
           "USER_POOL_DOMAIN": cfg["cognito_domain"],
           "M2M_CLIENT_ID": cfg["m2m_client_id"],
           "M2M_SECRET_ARN": sec["ARN"],
           "DRAFTS_BUCKET": cfg["drafts_bucket"],
           "DISTRIBUTION_DOMAIN": cfg["distribution_domain"],
           "MODEL_ID": "global.anthropic.claude-sonnet-5"}

    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    existing = next((r for r in ac.list_agent_runtimes()["agentRuntimes"]
                     if r["agentRuntimeName"] == RUNTIME), None)
    kwargs = {"agentRuntimeArtifact": {"containerConfiguration": {"containerUri": uri}},
              "networkConfiguration": {"networkMode": "PUBLIC"},
              "roleArn": cfg["runtime_role_arn"],
              "environmentVariables": env}
    if existing:
        ac.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **kwargs)
        arn = existing["agentRuntimeArn"]
        rid = existing["agentRuntimeId"]
    else:
        created = ac.create_agent_runtime(agentRuntimeName=RUNTIME, **kwargs)
        arn, rid = created["agentRuntimeArn"], created["agentRuntimeId"]
    for attempt in range(90):
        status = ac.get_agent_runtime(agentRuntimeId=rid)["status"]
        print(f"[{attempt+1}/90] runtime status: {status}")
        if status not in ("CREATING", "UPDATING"):
            break
        time.sleep(10)
    else:
        raise TimeoutError(f"runtime failed to reach READY within 15 minutes, last status: {status}")
    assert status == "READY", status
    cfg["runtime_arn"] = arn
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"runtime READY: {arn}")


if __name__ == "__main__":
    main()
