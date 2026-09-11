import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as sqs from 'aws-cdk-lib/aws-sqs';

export interface StudioWorkspaceProps {
  apiCode: lambda.Code;
  distribution: cloudfront.Distribution;
  cognitoUserPoolId: string;
  cognitoClientId: string;
  cacheTable: dynamodb.ITable;
  guardrailId: string;
  guardrailVersion: string;
}

export class StudioWorkspace extends Construct {
  constructor(scope: Construct, id: string, props: StudioWorkspaceProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);
    const bucket = new s3.Bucket(this, 'PrivateFiles', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true, versioned: true, removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [{ id: 'AbandonedUploadParts', tagFilters: { 'workspace-temporary': 'true' },
        expiration: cdk.Duration.days(7), noncurrentVersionExpiration: cdk.Duration.days(7) }],
    });
    const table = new dynamodb.Table(this, 'Records', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const renderVpc = new ec2.Vpc(this, 'RenderVpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.79.0.0/24'),
      maxAzs: 2, natGateways: 0, createInternetGateway: false,
      subnetConfiguration: [{ name: 'IsolatedBrowser', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 28 }],
    });
    const renderSg = new ec2.SecurityGroup(this, 'RenderSecurityGroup', {
      vpc: renderVpc, allowAllOutbound: false, allowAllIpv6Outbound: false,
      description: 'Browser assertions: no inbound rules and no network egress',
    });
    const image = (handler: string) => lambda.DockerImageCode.fromImageAsset(path.resolve(__dirname, '../..'), {
      file: 'workspace/Dockerfile', platform: ecrAssets.Platform.LINUX_ARM64,
      cmd: [handler],
      exclude: ['web', 'infra', 'api-dist', 'agents', 'onprem', 'tests', '.pytest_cache',
        '**/__pycache__', 'gates/node_modules', 'seed/out/corpus*', 'skills-dist', '.git'],
    });
    const browserLogs = new logs.LogGroup(this, 'BrowserLogs', {
      retention: logs.RetentionDays.ONE_WEEK, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const browserRole = new iam.Role(this, 'BrowserRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Isolated browser: scoped logging and ENI management only',
    });
    browserLogs.grantWrite(browserRole);
    browserRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ec2:CreateNetworkInterface', 'ec2:DescribeNetworkInterfaces', 'ec2:DescribeSubnets',
        'ec2:DeleteNetworkInterface', 'ec2:AssignPrivateIpAddresses', 'ec2:UnassignPrivateIpAddresses'],
      resources: ['*'], conditions: { StringEquals: { 'aws:RequestedRegion': stack.region } },
    }));
    // Prevent CDK from adding the broader managed VPC role. Its required rights
    // are explicitly scoped above. It has no data, Bedrock or application rights.
    const fixedRole = iam.Role.fromRoleArn(this, 'BrowserRoleReference', browserRole.roleArn, { mutable: false });
    const browser = new lambda.DockerImageFunction(this, 'Browser', {
      code: image('workspace.browser_handler.handler'),
      architecture: lambda.Architecture.ARM_64, role: fixedRole,
      memorySize: 3072, timeout: cdk.Duration.seconds(120),
      ephemeralStorageSize: cdk.Size.mebibytes(1024),
      reservedConcurrentExecutions: 3, logGroup: browserLogs,
      vpc: renderVpc, vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [renderSg],
      description: 'Offline browser assertions, accessibility and pixel comparison; no service data access',
    });
    browser.node.addDependency(browserRole);
    if (browserRole.node.tryFindChild('DefaultPolicy')) {
      browser.node.addDependency(browserRole.node.findChild('DefaultPolicy'));
    }
    const workerLogs = new logs.LogGroup(this, 'WorkerLogs', {
      retention: logs.RetentionDays.ONE_WEEK, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const workerRole = new iam.Role(this, 'WorkerRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    workerLogs.grantWrite(workerRole);
    const commonEnv = { WORKSPACE_BUCKET: bucket.bucketName, WORKSPACE_TABLE: table.tableName };
    const worker = new lambda.DockerImageFunction(this, 'Worker', {
      code: image('workspace.worker.handler'), architecture: lambda.Architecture.ARM_64,
      role: workerRole, memorySize: 4096, timeout: cdk.Duration.minutes(15),
      ephemeralStorageSize: cdk.Size.mebibytes(1024), reservedConcurrentExecutions: 3,
      logGroup: workerLogs,
      deadLetterQueue: new sqs.Queue(this, 'WorkerDlq', { retentionPeriod: cdk.Duration.days(7) }),
      environment: {
        ...commonEnv, WORKSPACE_BROWSER_FN: browser.functionName,
        CACHE_TABLE: props.cacheTable.tableName,
        DAILY_TOKEN_CAP: '2000000', GUARDRAIL_ID: props.guardrailId,
        GUARDRAIL_VERSION: props.guardrailVersion, LLM_ROUTE: 'bedrock',
        GEN_MODEL: 'global.anthropic.claude-sonnet-5',
        BEDROCK_READ_TIMEOUT: '240',
      },
      description: 'Private file extraction and frozen-guide generation/verification loop',
    });
    worker.configureAsyncInvoke({ retryAttempts: 0, maxEventAge: cdk.Duration.minutes(5) });
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:PutObjectTagging'],
      resources: [bucket.arnForObjects('workspace/*')],
    }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query'],
      resources: [table.tableArn],
    }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
      resources: [props.cacheTable.tableArn],
      conditions: { 'ForAllValues:StringLike': { 'dynamodb:LeadingKeys': ['usage#*'] } },
    }));
    // Exact model families verified with Bedrock GetInferenceProfile 2026-09-11.
    // Global profiles may route regions; model identities remain restricted.
    const modelNames = ['anthropic.claude-sonnet-5', 'anthropic.claude-opus-5',
      'openai.gpt-6-astra', 'anthropic.claude-fable-5-1', 'anthropic.claude-fable-5'];
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: modelNames.flatMap(model => [
        `arn:${stack.partition}:bedrock:*::foundation-model/${model}`,
        `arn:${stack.partition}:bedrock:*:${stack.account}:inference-profile/global.${model}`,
      ]),
    }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:ApplyGuardrail'],
      resources: [
        `arn:${stack.partition}:bedrock:${stack.region}:${stack.account}:guardrail/${props.guardrailId}`,
        `arn:${stack.partition}:bedrock:*:${stack.account}:guardrail-profile/apac.guardrail.v1:0`,
      ],
    }));
    browser.grantInvoke(workerRole);
    const apiLogs = new logs.LogGroup(this, 'ApiLogs', {
      retention: logs.RetentionDays.ONE_WEEK, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const apiRole = new iam.Role(this, 'ApiRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    apiLogs.grantWrite(apiRole);
    const apiFn = new lambda.Function(this, 'Api', {
      runtime: lambda.Runtime.PYTHON_3_12, handler: 'workspace.http.handler',
      code: props.apiCode, role: apiRole, memorySize: 1024, timeout: cdk.Duration.seconds(28),
      reservedConcurrentExecutions: 10, logGroup: apiLogs,
      environment: { ...commonEnv, WORKSPACE_WORKER_FN: worker.functionName },
      description: 'JWT-owned private file chunks, contracts, runs and approvals',
    });
    apiRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:PutObjectTagging'],
      resources: [bucket.arnForObjects('workspace/*')],
    }));
    apiRole.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query'],
      resources: [table.tableArn],
    }));
    worker.grantInvoke(apiRole);
    const jwt = new authorizers.HttpJwtAuthorizer('WorkspaceCognito',
      `https://cognito-idp.${stack.region}.amazonaws.com/${props.cognitoUserPoolId}`,
      { jwtAudience: [props.cognitoClientId] });
    const api = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: `${stack.stackName}-studio-workspace`,
      defaultIntegration: new integrations.HttpLambdaIntegration('WorkspaceIntegration', apiFn),
      defaultAuthorizer: jwt, defaultAuthorizationScopes: ['aws.cognito.signin.user.admin'],
    });
    if (api.defaultStage) {
      const stage = api.defaultStage.node.defaultChild as apigwv2.CfnStage;
      stage.defaultRouteSettings = { throttlingRateLimit: 30, throttlingBurstLimit: 60 };
    }
    props.distribution.addBehavior('/studio-api/*', new origins.HttpOrigin(
      `${api.apiId}.execute-api.${stack.region}.${stack.urlSuffix}`,
      { protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY }), {
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
    });
    new cdk.CfnOutput(this, 'PrivateBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'RecordsTableName', { value: table.tableName });
    new cdk.CfnOutput(this, 'WorkerFunctionName', { value: worker.functionName });
    new cdk.CfnOutput(this, 'BrowserFunctionName', { value: browser.functionName });
    new cdk.CfnOutput(this, 'ApiFunctionName', { value: apiFn.functionName });
    new cdk.CfnOutput(this, 'ApiId', { value: api.apiId });
  }
}
