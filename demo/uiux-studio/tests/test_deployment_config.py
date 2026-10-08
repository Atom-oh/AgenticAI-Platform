"""Deployment records are observed, private, account-bound and namespace-independent for cleanup."""
import copy
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import deployment_config as config
from scripts import teardown, write_config

ACCOUNT, REGION = "111111111111", "ap-northeast-2"


def record(name="LegacyDemo"):
    return {"schema_version": 1, "status": "observed", "account": ACCOUNT, "region": REGION,
            "stack_name": name, "stack_id": f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/{name}/recorded-id",
            "runtime_role_arn": f"arn:aws:iam::{ACCOUNT}:role/legacy-runtime-role",
            "gateway_role_arn": f"arn:aws:iam::{ACCOUNT}:role/legacy-gateway-role"}


def test_tracked_config_is_explicitly_unconfigured():
    with pytest.raises(ValueError, match="observed"):
        config.load_config(ROOT / "config" / "stack.json")


def test_private_record_roundtrips_and_public_source_destination_is_refused(tmp_path):
    cfg = record()
    path = tmp_path / "deployment.json"
    config.save_config(cfg, path)
    assert config.load_config(path) == cfg
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="outside tracked"):
        config.save_config(cfg, ROOT / "config" / "stack.json")


def test_legacy_record_is_available_for_cleanup_but_not_implicit_renamed_deploy(tmp_path):
    path = tmp_path / "legacy.json"
    config.save_config(record(), path)
    assert config.load_config(path)["stack_name"] == "LegacyDemo"
    with pytest.raises(ValueError, match="cannot migrate"):
        config.load_config(path, for_deploy=True)


def test_wrong_account_refuses_before_any_service_client(monkeypatch):
    called = []
    class Session:
        def client(self, service):
            called.append(service)
            assert service == "sts"
            return SimpleNamespace(get_caller_identity=lambda: {"Account": "222222222222", "Arn": "unused"})
    monkeypatch.setattr(config.boto3, "Session", lambda **kw: Session())
    with pytest.raises(ValueError, match="account"):
        config.client(record(), "cloudformation")
    assert called == ["sts"]


def test_samples_requires_the_named_assumed_role(monkeypatch):
    monkeypatch.delenv("BANK_UIUX_PROFILE", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with pytest.raises(ValueError, match="samples-atomoh"):
        config.session_for("061525506239", REGION)
    monkeypatch.setattr(config.boto3, "Session", lambda **kw: SimpleNamespace(client=lambda _: SimpleNamespace(
        get_caller_identity=lambda: {"Account": "061525506239", "Arn": "arn:aws:iam::061525506239:user/wrong"})))
    with pytest.raises(ValueError, match="assumed atomoh"):
        config.session_for("061525506239", REGION, "samples-atomoh")


def test_capture_keeps_actual_legacy_ids_and_never_substitutes_name_prefixes():
    cfg = record()
    outputs = {"AssetsBucket": "legacy-assets", "SkillsBucket": "legacy-skills", "DraftsBucket": "legacy-drafts",
               "UserPoolId": "actual-pool", "RuntimeRoleArn": cfg["runtime_role_arn"],
               "GatewayRoleArn": cfg["gateway_role_arn"], "DispatcherFn": "legacy-dispatcher"}
    rt_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/legacy_runtime-realId"
    class Session:
        def client(self, service):
            if service == "cloudformation":
                return SimpleNamespace(describe_stacks=lambda **kw: {"Stacks": [{"StackName": "LegacyDemo",
                    "StackId": cfg["stack_id"], "StackStatus": "CREATE_COMPLETE", "Outputs": [
                    {"OutputKey": k, "OutputValue": v} for k, v in outputs.items()]}]})
            assert service == "bedrock-agentcore-control"
            return SimpleNamespace(get_agent_runtime=lambda **kw: {"agentRuntimeArn": rt_arn,
                "agentRuntimeName": "legacy_runtime", "roleArn": cfg["runtime_role_arn"],
                "agentRuntimeArtifact": {"containerConfiguration": {
                    "containerUri": f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/legacy-repo:observed"}},
                "environmentVariables": {"M2M_SECRET_ARN": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:legacy/client-ABCDEF"}})
    result = write_config.capture(Session(), "LegacyDemo", account=ACCOUNT, region=REGION, runtime_id="legacy_runtime-realId")
    assert result["runtime_arn"] == rt_arn and result["ecr_repository"] == "legacy-repo"
    assert result["assets_bucket"] == "legacy-assets" and "bank_" not in json.dumps(result)


def test_teardown_uses_recorded_legacy_identifiers_and_exact_stack(monkeypatch):
    cfg = record()
    cfg.update(runtime_arn=f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/legacy_runtime-realId",
               gateway_id="legacy-gateway-realId", memory_id="legacy-memory-realId", ecr_repository="legacy-repo",
               m2m_secret_arn=f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:legacy/client-ABCDEF")
    calls, deleted = [], set()
    def missing():
        raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")
    def get_runtime(**kw):
        if "runtime" in deleted: missing()
        return {"agentRuntimeArn": cfg["runtime_arn"], "roleArn": cfg["runtime_role_arn"]}
    def get_gateway(**kw):
        if "gateway" in deleted: missing()
        return {"roleArn": cfg["gateway_role_arn"]}
    def remove(kind, **kw):
        deleted.add(kind); calls.append((kind, kw)); return {}
    ac = SimpleNamespace(get_agent_runtime=get_runtime, get_gateway=get_gateway,
        delete_agent_runtime=lambda **kw: remove("runtime", **kw), delete_gateway=lambda **kw: remove("gateway", **kw),
        list_gateway_targets=lambda **kw: {"items": []}, delete_memory=lambda **kw: remove("memory", **kw),
        get_memory=lambda **kw: missing())
    services = {"bedrock-agentcore-control": ac,
        "ecr": SimpleNamespace(delete_repository=lambda **kw: remove("repository", **kw)),
        "secretsmanager": SimpleNamespace(describe_secret=lambda **kw: {}, delete_secret=lambda **kw: remove("secret", **kw)),
        "cloudformation": SimpleNamespace(delete_stack=lambda **kw: remove("stack", **kw))}
    teardown.remove(cfg, SimpleNamespace(client=lambda service: services[service]), include_stack=True)
    assert calls[0] == ("runtime", {"agentRuntimeId": "legacy_runtime-realId"})
    assert ("memory", {"memoryId": "legacy-memory-realId"}) in calls
    assert ("repository", {"repositoryName": "legacy-repo", "force": True}) in calls
    assert ("secret", {"SecretId": cfg["m2m_secret_arn"], "RecoveryWindowInDays": 7}) in calls
    assert calls[-1] == ("stack", {"StackName": cfg["stack_id"]})
