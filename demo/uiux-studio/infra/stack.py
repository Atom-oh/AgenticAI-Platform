import json
import pathlib
import re

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


class BankUiuxPlatformStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        # Service IDs are allocated after this base stack. Bootstrap grants
        # are restricted to this application's named resource families; a
        # subsequent synth can pin the observed ARNs exactly.
        prefix = f"arn:{self.partition}:bedrock-agentcore:{self.region}:{self.account}:"

        def service_resource(key, kind, family):
            value = self.node.try_get_context(key)
            if value is None:
                return prefix + kind + "/" + family + "-*"
            parts = value.split(":", 5) if isinstance(value, str) else []
            if (len(parts) != 6 or parts[:3] != ["arn", "aws", "bedrock-agentcore"]
                    or parts[3:5] != [self.region, self.account] or not parts[5].startswith(kind + "/")
                    or any(c in value for c in "*?")):
                raise ValueError(f"{key} must be an exact ARN in this stack's account and region")
            return value

        runtime_arn = service_resource("runtimeArn", "runtime", "bank_design_harness")
        memory_arn = service_resource("memoryArn", "memory", "bank_design_memory")
        region_only = {"StringEquals": {"aws:RequestedRegion": self.region}}
        model_resources = self.node.try_get_context("modelResources") or []
        if isinstance(model_resources, str):
            model_resources = json.loads(model_resources)
        if not isinstance(model_resources, list) or any(not isinstance(arn, str) or
                not re.fullmatch(r"arn:aws:bedrock:[a-z0-9-]+:(?:[0-9]{12})?:(?:foundation-model|inference-profile|application-inference-profile)/[^*?]+", arn)
                for arn in model_resources):
            raise ValueError("modelResources must contain exact observed Bedrock model/profile ARNs")
        if any(arn.split(":", 5)[4] not in ("", self.account) for arn in model_resources):
            raise ValueError("Model profiles must belong to this deployment account")

        def bucket(name):
            return s3.Bucket(self, name.title().replace("-", ""),
                             bucket_name=f"bank-{name}-{ACCOUNT}",
                             block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                             removal_policy=cdk.RemovalPolicy.DESTROY,
                             auto_delete_objects=True)

        assets = bucket("design-assets")
        skills = bucket("skill-registry")
        drafts = bucket("design-drafts")

        registry = ddb.Table(self, "Registry", table_name="bank-design-registry",
                             partition_key=ddb.Attribute(name="asset_id",
                                                         type=ddb.AttributeType.STRING),
                             billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                             removal_policy=cdk.RemovalPolicy.DESTROY)

        history = ddb.Table(self, "History", table_name="bank-asset-history",
                            partition_key=ddb.Attribute(name="asset_id",
                                                        type=ddb.AttributeType.STRING),
                            sort_key=ddb.Attribute(name="version",
                                                   type=ddb.AttributeType.STRING),
                            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                            removal_policy=cdk.RemovalPolicy.DESTROY)

        figma_secret = sm.Secret(self, "FigmaToken", secret_name="bank/figma-token",
                                 description="Figma PAT (operator-injected, temporary)",
                                 removal_policy=cdk.RemovalPolicy.DESTROY)

        project_root = str(pathlib.Path(__file__).resolve().parent.parent)
        code = lambda_.Code.from_asset(project_root, exclude=[
            "infra", "harness", "gallery", "docs", "design-canvas", "tests",
            "scripts", "config", ".venv", "**/__pycache__", ".pytest_cache"])
        common_env = {"ASSETS_BUCKET": assets.bucket_name,
                      "REGISTRY_TABLE": registry.table_name,
                      "SKILLS_BUCKET": skills.bucket_name,
                      "FIGMA_SECRET_ID": "bank/figma-token"}

        figma_sync = lambda_.Function(
            self, "FigmaSync", function_name="bank-figma-sync",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="ingestion.figma_sync.handler", code=code,
            timeout=cdk.Duration.minutes(2), environment=common_env)
        asset_tools = lambda_.Function(
            self, "AssetTools", function_name="bank-design-asset-tools",
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
            self, "Dispatcher", function_name="bank-generate-dispatcher",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="dispatch.handler.handler", code=code,
            timeout=cdk.Duration.seconds(900),
            environment={"HISTORY_TABLE": history.table_name, "RUNTIME_ARN": ""})
        history.grant_read_write_data(dispatcher)
        dispatcher.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"], resources=[runtime_arn, runtime_arn + "/runtime-endpoint/*"]))
        # async (Event) invokes default to 2 retries on failure, which would re-run
        # (and re-bill) the AgentCore Runtime call that already failed once.
        dispatcher.configure_async_invoke(retry_attempts=0)

        pool = cognito.UserPool(
            self, "Pool", user_pool_name="bank-uiux-platform",
            self_sign_up_enabled=False,  # org policy: admin-created users only
            removal_policy=cdk.RemovalPolicy.DESTROY)
        domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(
            domain_prefix=f"bank-uiux-{ACCOUNT}"))
        server = pool.add_resource_server("Rs", identifier="bank-mcp", scopes=[
            cognito.ResourceServerScope(scope_name="invoke", scope_description="invoke MCP")])
        m2m = pool.add_client("M2M", generate_secret=True, o_auth=cognito.OAuthSettings(
            flows=cognito.OAuthFlows(client_credentials=True),
            scopes=[cognito.OAuthScope.resource_server(
                server, cognito.ResourceServerScope(scope_name="invoke",
                                                    scope_description="invoke MCP"))]))
        # public browser client for designer sign-in (no secret; accounts are
        # still admin-created only — self sign-up stays disabled above)
        spa = pool.add_client("Spa", generate_secret=False,
                              auth_flows=cognito.AuthFlow(user_password=True, user_srp=True))

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
            self, "Feedback", function_name="bank-draft-feedback",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="feedback.handler.handler", code=code,
            timeout=cdk.Duration.seconds(15),
            environment={"DRAFTS_BUCKET": drafts.bucket_name,
                        "ASSETS_BUCKET": assets.bucket_name,
                        "REGISTRY_TABLE": registry.table_name,
                        "SKILLS_BUCKET": skills.bucket_name,
                        "HISTORY_TABLE": history.table_name,
                        "DISPATCHER_FN": dispatcher.function_name,
                        "SPA_CLIENT_ID": spa.user_pool_client_id,
                        "DEFAULT_MODEL": "global.anthropic.claude-sonnet-5",
                        "MEMORY_ID": ""})
        drafts.grant_read_write(feedback)
        assets.grant_read_write(feedback)
        skills.grant_write(feedback)
        registry.grant_read_write_data(feedback)
        history.grant_read_write_data(feedback)
        dispatcher.grant_invoke(feedback)
        feedback.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:ListInferenceProfiles"], resources=["*"], conditions=region_only))
        feedback.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:CreateEvent"], resources=[memory_arn]))
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

        gw_role = iam.Role(self, "GatewayRole", role_name="bank-agentcore-gateway",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", conditions={
                               "StringEquals": {"aws:SourceAccount": self.account},
                               "ArnLike": {"aws:SourceArn": prefix + "gateway/bank-design-assets-gw-*"}}))
        asset_tools.grant_invoke(gw_role)

        rt_role = iam.Role(self, "RuntimeRole", role_name="bank-agentcore-runtime",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", conditions={
                               "StringEquals": {"aws:SourceAccount": self.account},
                               "ArnLike": {"aws:SourceArn": runtime_arn}}))
        drafts.grant_read_write(rt_role)
        skills.grant_read(rt_role)
        # Model availability/permissions must come from observed profile and
        # foundation-model ARNs. An unconfigured model set gets no invoke grant.
        if model_resources:
            rt_role.add_to_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=model_resources))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
            resources=[f"arn:{self.partition}:ecr:{self.region}:{self.account}:repository/bank-design-harness"]))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken", "xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            resources=["*"], conditions=region_only))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/bank_design_harness-*"]))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"], resources=["*"], conditions={"StringEquals": {
                "aws:RequestedRegion": self.region, "cloudwatch:namespace": "bedrock-agentcore"}}))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:CreateEvent", "bedrock-agentcore:RetrieveMemoryRecords",
                     "bedrock-agentcore:ListMemoryRecords"], resources=[memory_arn]))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{self.region}:{ACCOUNT}:secret:"
                       f"bank/m2m-client-secret-??????"]))

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
            "DistributionId": dist.distribution_id,
            "GatewayRoleArn": gw_role.role_arn, "RuntimeRoleArn": rt_role.role_arn,
            "FigmaSyncFn": figma_sync.function_name, "AssetToolsFnArn": asset_tools.function_arn,
            "HistoryTable": history.table_name, "DispatcherFn": dispatcher.function_name,
            "FeedbackFn": feedback.function_name, "McpScope": "bank-mcp/invoke",
            "ModelResources": json.dumps(model_resources),
            "SpaClientId": spa.user_pool_client_id,
        }.items():
            cdk.CfnOutput(self, name, value=value)
