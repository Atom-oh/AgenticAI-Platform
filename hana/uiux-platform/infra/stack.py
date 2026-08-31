import pathlib

import aws_cdk as cdk
from aws_cdk import (
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_secretsmanager as sm,
)
from constructs import Construct

ACCOUNT = "180294183052"


class HanaUiuxPlatformStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)

        def bucket(name):
            return s3.Bucket(self, name.title().replace("-", ""),
                             bucket_name=f"hana-{name}-{ACCOUNT}",
                             block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                             removal_policy=cdk.RemovalPolicy.DESTROY,
                             auto_delete_objects=True)

        assets = bucket("design-assets")
        skills = bucket("skill-registry")
        drafts = bucket("design-drafts")

        registry = ddb.Table(self, "Registry", table_name="hana-design-registry",
                             partition_key=ddb.Attribute(name="asset_id",
                                                         type=ddb.AttributeType.STRING),
                             billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                             removal_policy=cdk.RemovalPolicy.DESTROY)

        history = ddb.Table(self, "History", table_name="hana-asset-history",
                            partition_key=ddb.Attribute(name="asset_id",
                                                        type=ddb.AttributeType.STRING),
                            sort_key=ddb.Attribute(name="version",
                                                   type=ddb.AttributeType.STRING),
                            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                            removal_policy=cdk.RemovalPolicy.DESTROY)

        figma_secret = sm.Secret(self, "FigmaToken", secret_name="hana/figma-token",
                                 description="Figma PAT (operator-injected, temporary)",
                                 removal_policy=cdk.RemovalPolicy.DESTROY)

        project_root = str(pathlib.Path(__file__).resolve().parent.parent)
        code = lambda_.Code.from_asset(project_root, exclude=[
            "infra", "harness", "gallery", "docs", "design-canvas", "tests",
            "scripts", "config", ".venv", "**/__pycache__", ".pytest_cache"])
        common_env = {"ASSETS_BUCKET": assets.bucket_name,
                      "REGISTRY_TABLE": registry.table_name,
                      "SKILLS_BUCKET": skills.bucket_name,
                      "FIGMA_SECRET_ID": "hana/figma-token"}

        figma_sync = lambda_.Function(
            self, "FigmaSync", function_name="hana-figma-sync",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="ingestion.figma_sync.handler", code=code,
            timeout=cdk.Duration.minutes(2), environment=common_env)
        asset_tools = lambda_.Function(
            self, "AssetTools", function_name="hana-design-asset-tools",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="mcp.asset_tools.handler", code=code,
            timeout=cdk.Duration.seconds(30), environment=common_env)

        assets.grant_read_write(figma_sync)
        registry.grant_read_write_data(figma_sync)
        figma_secret.grant_read(figma_sync)
        assets.grant_read(asset_tools)
        skills.grant_read(asset_tools)
        registry.grant_read_data(asset_tools)

        dispatcher = lambda_.Function(
            self, "Dispatcher", function_name="hana-generate-dispatcher",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="dispatch.handler.handler", code=code,
            timeout=cdk.Duration.seconds(900),
            environment={"HISTORY_TABLE": history.table_name, "RUNTIME_ARN": ""})
        history.grant_read_write_data(dispatcher)
        dispatcher.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"], resources=["*"]))

        pool = cognito.UserPool(
            self, "Pool", user_pool_name="hana-uiux-platform",
            self_sign_up_enabled=False,  # org policy: admin-created users only
            removal_policy=cdk.RemovalPolicy.DESTROY)
        domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(
            domain_prefix=f"hana-uiux-{ACCOUNT}"))
        server = pool.add_resource_server("Rs", identifier="hana-mcp", scopes=[
            cognito.ResourceServerScope(scope_name="invoke", scope_description="invoke MCP")])
        m2m = pool.add_client("M2M", generate_secret=True, o_auth=cognito.OAuthSettings(
            flows=cognito.OAuthFlows(client_credentials=True),
            scopes=[cognito.OAuthScope.resource_server(
                server, cognito.ResourceServerScope(scope_name="invoke",
                                                    scope_description="invoke MCP"))]))

        drafts_origin = origins.S3BucketOrigin.with_origin_access_control(drafts)
        dist = cloudfront.Distribution(
            self, "Gallery",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=drafts_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS),
            additional_behaviors={
                "/drafts.json": cloudfront.BehaviorOptions(
                    origin=drafts_origin,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS),
                "/approved-patterns/index.json": cloudfront.BehaviorOptions(
                    origin=drafts_origin,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS),
            })

        feedback = lambda_.Function(
            self, "Feedback", function_name="hana-draft-feedback",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="feedback.handler.handler", code=code,
            timeout=cdk.Duration.seconds(15),
            environment={"DRAFTS_BUCKET": drafts.bucket_name,
                        "ASSETS_BUCKET": assets.bucket_name,
                        "REGISTRY_TABLE": registry.table_name,
                        "SKILLS_BUCKET": skills.bucket_name,
                        "HISTORY_TABLE": history.table_name,
                        "DISPATCHER_FN": dispatcher.function_name})
        drafts.grant_read_write(feedback)
        assets.grant_read_write(feedback)
        skills.grant_write(feedback)
        registry.grant_read_write_data(feedback)
        history.grant_read_write_data(feedback)
        dispatcher.grant_invoke(feedback)
        feedback_url = feedback.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)
        # OAC needs BOTH InvokeFunctionUrl (added by the origin construct) and
        # InvokeFunction for the CloudFront principal, per the CloudFront OAC docs.
        feedback.add_permission(
            "CloudFrontOacInvokeFunction",
            principal=iam.ServicePrincipal("cloudfront.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:cloudfront::{ACCOUNT}:distribution/{dist.distribution_id}")
        dist.add_behavior(
            "/api/*",
            origins.FunctionUrlOrigin.with_origin_access_control(feedback_url),
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            # AllViewerExceptHostHeader forwards Content-Type and the viewer's
            # x-amz-content-sha256 (required for OAC-signed POST to a Function URL);
            # a custom whitelist policy cannot name x-amz-* headers and 403s the origin.
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY)

        gw_role = iam.Role(self, "GatewayRole", role_name="hana-agentcore-gateway",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        asset_tools.grant_invoke(gw_role)

        rt_role = iam.Role(self, "RuntimeRole", role_name="hana-agentcore-runtime",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        drafts.grant_read_write(rt_role)
        skills.grant_read(rt_role)
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"]))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken", "ecr:BatchGetImage",
                     "ecr:GetDownloadUrlForLayer", "logs:CreateLogGroup",
                     "logs:CreateLogStream", "logs:PutLogEvents",
                     "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                     "cloudwatch:PutMetricData"],
            resources=["*"]))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{self.region}:{ACCOUNT}:secret:"
                       f"hana/m2m-client-secret*"]))

        discovery = (f"https://cognito-idp.{self.region}.amazonaws.com/"
                     f"{pool.user_pool_id}/.well-known/openid-configuration")
        for name, value in {
            "AssetsBucket": assets.bucket_name, "SkillsBucket": skills.bucket_name,
            "DraftsBucket": drafts.bucket_name, "RegistryTable": registry.table_name,
            "FigmaSecretArn": figma_secret.secret_arn, "UserPoolId": pool.user_pool_id,
            "M2MClientId": m2m.user_pool_client_id,
            "CognitoDomain": f"{domain.domain_name}.auth.{self.region}.amazoncognito.com",
            "CognitoDiscoveryUrl": discovery,
            "DistributionDomain": dist.distribution_domain_name,
            "GatewayRoleArn": gw_role.role_arn, "RuntimeRoleArn": rt_role.role_arn,
            "FigmaSyncFn": figma_sync.function_name, "AssetToolsFnArn": asset_tools.function_arn,
            "HistoryTable": history.table_name, "DispatcherFn": dispatcher.function_name,
        }.items():
            cdk.CfnOutput(self, name, value=value)
