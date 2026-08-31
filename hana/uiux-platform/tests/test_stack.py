import aws_cdk as cdk
from aws_cdk.assertions import Match, Template


def synth():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "infra"))
    from stack import HanaUiuxPlatformStack
    app = cdk.App()
    return Template.from_stack(HanaUiuxPlatformStack(
        app, "HanaUiuxPlatform",
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
        "FunctionName": "hana-figma-sync",
        "Environment": {"Variables": Match.object_like({"FIGMA_SECRET_ID": "hana/figma-token"})}}))
