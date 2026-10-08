import aws_cdk as cdk
from aws_cdk.assertions import Match, Template


def synth(context=None):
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "infra"))
    from stack import BankUiuxPlatformStack
    app = cdk.App(context=context or {})
    return Template.from_stack(BankUiuxPlatformStack(
        app, "BankUiuxPlatform",
        env=cdk.Environment(account="180294183052", region="ap-northeast-2")))


def test_cognito_self_signup_disabled():
    t = synth()
    t.has_resource_properties("AWS::Cognito::UserPool", {
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}})


def test_m2m_client_uses_client_credentials():
    t = synth()
    t.has_resource_properties("AWS::Cognito::UserPoolClient", {
        "AllowedOAuthFlows": ["client_credentials"], "GenerateSecret": True})


def test_no_public_bucket_and_oac_distribution():
    t = synth()
    t.resource_count_is("AWS::CloudFront::Distribution", 1)
    for props in t.find_resources("AWS::S3::Bucket").values():
        cfg = props["Properties"]["PublicAccessBlockConfiguration"]
        assert cfg["BlockPublicPolicy"] is True


def test_lambdas_have_env():
    t = synth()
    t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "bank-figma-sync",
        "Environment": {"Variables": Match.object_like({"FIGMA_SECRET_ID": "bank/figma-token"})}}))


def test_feedback_lambda_and_api_behavior():
    t = synth()
    t.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})
    dist = list(t.find_resources("AWS::CloudFront::Distribution").values())[0]
    behaviors = dist["Properties"]["DistributionConfig"]["CacheBehaviors"]
    assert any(b["PathPattern"] == "/api/*" for b in behaviors)


def test_distribution_id_output_exists():
    t = synth()
    outputs = t.find_outputs("*")
    assert "DistributionId" in outputs
    assert "Ref" in outputs["DistributionId"]["Value"]


def test_dispatcher_async_invoke_has_no_retries():
    t = synth()
    t.has_resource_properties("AWS::Lambda::EventInvokeConfig", {
        "MaximumRetryAttempts": 0})


def test_history_table_and_dispatcher():
    t = synth()
    t.has_resource_properties("AWS::DynamoDB::Table", Match.object_like({
        "TableName": "bank-asset-history"}))
    t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "bank-generate-dispatcher", "Timeout": 900}))
    t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "bank-draft-feedback",
        "Environment": {"Variables": Match.object_like({
            "DISPATCHER_FN": Match.any_value(), "HISTORY_TABLE": Match.any_value()})}}))



def _app_statements(template):
    for logical_id, policy in template.find_resources("AWS::IAM::Policy").items():
        if any(name in logical_id for name in ("Dispatcher", "Feedback", "RuntimeRole")):
            yield from policy["Properties"]["PolicyDocument"]["Statement"]


def test_demo_runtime_memory_and_registry_access_has_scoped_resources():
    import json
    statements = list(_app_statements(synth()))
    for statement in statements:
        resources = statement.get("Resource")
        if resources == "*" or resources == ["*"]:
            assert statement.get("Condition", {}).get("StringEquals", {}).get("aws:RequestedRegion") == "ap-northeast-2"
    invoke = [s for s in statements if s["Action"] == "bedrock-agentcore:InvokeAgentRuntime"]
    assert len(invoke) == 1 and "runtime/bank_design_harness-*" in json.dumps(invoke[0]["Resource"])
    memory = [s for s in statements if "bedrock-agentcore:CreateEvent" in (
        s["Action"] if isinstance(s["Action"], list) else [s["Action"]])]
    assert len(memory) == 2 and all("memory/bank_design_memory-*" in json.dumps(s["Resource"]) for s in memory)
    assert not any("bedrock:InvokeModel" in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
                   for s in statements)


def test_observed_arns_pin_the_runtime_memory_and_model_grants():
    model = ["arn:aws:bedrock:us-east-1::foundation-model/synthetic-approved-model",
             "arn:aws:bedrock:ap-northeast-2:180294183052:inference-profile/synthetic-approved-profile"]
    runtime = "arn:aws:bedrock-agentcore:ap-northeast-2:180294183052:runtime/observed_runtime-abc"
    memory = "arn:aws:bedrock-agentcore:ap-northeast-2:180294183052:memory/observed_memory-abc"
    statements = list(_app_statements(synth({"runtimeArn": runtime, "memoryArn": memory, "modelResources": model})))
    invocation = next(s for s in statements if s["Action"] == "bedrock-agentcore:InvokeAgentRuntime")
    assert set(invocation["Resource"]) == {runtime, runtime + "/runtime-endpoint/*"}
    models = next(s for s in statements if "bedrock:InvokeModel" in (
        s["Action"] if isinstance(s["Action"], list) else [s["Action"]]))
    assert set(models["Resource"]) == set(model)
    for statement in statements:
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        if "bedrock-agentcore:CreateEvent" in actions:
            assert statement["Resource"] == memory


def test_cross_account_service_or_wildcard_model_context_is_refused():
    import pytest
    with pytest.raises(ValueError, match="exact ARN"):
        synth({"memoryArn": "arn:aws:bedrock-agentcore:ap-northeast-2:222222222222:memory/foreign"})
    with pytest.raises(ValueError, match="exact observed"):
        synth({"modelResources": ["*"]})
