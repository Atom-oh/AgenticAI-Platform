import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as neptune from 'aws-cdk-lib/aws-neptune';

export class BankPlatformStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ---------- 프론트엔드: S3 (프라이빗) + CloudFront (유일한 퍼블릭 진입점) ----------
    const webBucket = new s3.Bucket(this, 'WebBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const dist = new cloudfront.Distribution(this, 'WebDist', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(webBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
      comment: 'Agentic AI Platform - bank demo',
    });

    // ---------- WebSocket 연결 테이블 ----------
    const connTable = new dynamodb.Table(this, 'ConnTable', {
      partitionKey: { name: 'connId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------- Two-Plane VPC (SPEC §3.1) ----------
    // onprem-isolated: NAT 없음·인터넷 경로 없음 — ECS 온프렘 플레인 + RDS
    // cloud-egress: NAT 경유 — 클라우드 플레인 Lambda (Bedrock·프록시·Neptune 접근)
    const vpc = new ec2.Vpc(this, 'PlatformVpc', {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'cloud-egress', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
        { name: 'onprem-isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });
    const isolated = { subnetType: ec2.SubnetType.PRIVATE_ISOLATED };
    // 격리 서브넷의 Fargate가 이미지·로그에 닿도록 VPC 엔드포인트만 허용 (인터넷 아님)
    vpc.addGatewayEndpoint('S3Gw', { service: ec2.GatewayVpcEndpointAwsService.S3, subnets: [isolated] });
    vpc.addInterfaceEndpoint('EcrApi', { service: ec2.InterfaceVpcEndpointAwsService.ECR, subnets: isolated });
    vpc.addInterfaceEndpoint('EcrDkr', { service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER, subnets: isolated });
    vpc.addInterfaceEndpoint('Logs', { service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS, subnets: isolated });
    vpc.addInterfaceEndpoint('SecretsEp', { service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER, subnets: isolated });

    // ---------- 온프렘 플레인: RDS PostgreSQL (합성 개인데이터, §3.2) ----------
    const db = new rds.DatabaseInstance(this, 'PersonalDb', {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16_13 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      vpc, vpcSubnets: isolated,
      credentials: rds.Credentials.fromGeneratedSecret('bankadmin'),
      databaseName: 'bank',
      allocatedStorage: 20,
      multiAz: false,
      publiclyAccessible: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      deletionProtection: false,
    });

    // ---------- 온프렘 플레인: ECS Fargate 서비스 ----------
    const cluster = new ecs.Cluster(this, 'OnpremCluster', { vpc });
    const image = new ecrAssets.DockerImageAsset(this, 'OnpremImage', {
      directory: '../onprem', platform: ecrAssets.Platform.LINUX_ARM64,
    });
    const taskDef = new ecs.FargateTaskDefinition(this, 'OnpremTask', {
      cpu: 256, memoryLimitMiB: 512,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64 },
    });
    taskDef.addContainer('onprem', {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'onprem' }),
      environment: { DATA_BACKEND: 'rds', PGDATABASE: 'bank' },
      secrets: {
        PGUSER: ecs.Secret.fromSecretsManager(db.secret!, 'username'),
        PGPASSWORD: ecs.Secret.fromSecretsManager(db.secret!, 'password'),
        PGHOST: ecs.Secret.fromSecretsManager(db.secret!, 'host'),
      },
      portMappings: [{ containerPort: 8080 }],
    });
    const onpremSvc = new ecs.FargateService(this, 'OnpremSvc', {
      cluster, taskDefinition: taskDef, desiredCount: 1,
      vpcSubnets: isolated, assignPublicIp: false,
    });
    db.connections.allowFrom(onpremSvc, ec2.Port.tcp(5432), 'onprem plane to rds');

    // 내부 ALB — 온프렘 플레인의 유일한 입구 (클라우드 플레인 Lambda만 허용)
    const alb = new elbv2.ApplicationLoadBalancer(this, 'OnpremAlb', {
      vpc, internetFacing: false, vpcSubnets: isolated,
    });
    const listener = alb.addListener('L', { port: 80, open: false });
    listener.addTargets('T', {
      port: 8080, protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [onpremSvc],
      healthCheck: { path: '/health', healthyHttpCodes: '200' },
      deregistrationDelay: cdk.Duration.seconds(10),
    });

    // ---------- 클라우드 플레인: Neptune Serverless (온톨로지 그래프, §3.2) ----------
    const nepSg = new ec2.SecurityGroup(this, 'NeptuneSg', { vpc, allowAllOutbound: true });
    const nepSubnets = new neptune.CfnDBSubnetGroup(this, 'NepSubnets', {
      dbSubnetGroupDescription: 'neptune (cloud plane)',
      subnetIds: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds,
    });
    const nepCluster = new neptune.CfnDBCluster(this, 'NepCluster', {
      dbSubnetGroupName: nepSubnets.ref,
      vpcSecurityGroupIds: [nepSg.securityGroupId],
      serverlessScalingConfiguration: { minCapacity: 1, maxCapacity: 2.5 },
      storageEncrypted: true,
    });
    new neptune.CfnDBInstance(this, 'NepInstance', {
      dbClusterIdentifier: nepCluster.ref,
      dbInstanceClass: 'db.serverless',
    });

    // ---------- 엔진 Lambda (api-dist는 deploy.sh가 조립) ----------
    const fnSg = new ec2.SecurityGroup(this, 'WsFnSg', { vpc, allowAllOutbound: true });
    const fn = new lambda.Function(this, 'WsFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'ws_handler.handler',
      code: lambda.Code.fromAsset('../api-dist'),
      memorySize: 1536,
      timeout: cdk.Duration.seconds(150),
      reservedConcurrentExecutions: 10, // 남용 상한 (동시 5명 시연 + 여유)
      vpc, vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [fnSg],
      environment: {
        CONN_TABLE: connTable.tableName,
        GRAPH_BACKEND: process.env.GRAPH_BACKEND || 'local', // neptune 전환은 deploy 시 지정
        NEPTUNE_ENDPOINT: nepCluster.attrEndpoint,
        ONPREM_URL: `http://${alb.loadBalancerDnsName}`,
        GEN_MODEL: 'apac.anthropic.claude-sonnet-4-20250514-v1:0',
        GUARDRAIL_ID: 'iol2t2rp0q9i',
        GUARDRAIL_VERSION: '3',
      },
    });
    // 클라우드 플레인 → 온프렘 ALB / Neptune 접근 허용 (그 외 인바운드 없음)
    listener.connections.allowFrom(fnSg, ec2.Port.tcp(80), 'cloud lambda to onprem alb');
    nepSg.addIngressRule(fnSg, ec2.Port.tcp(8182), 'cloud lambda to neptune');
    connTable.grantReadWriteData(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*::foundation-model/*`,
        `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
      ],
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:ApplyGuardrail'],
      resources: [
        `arn:aws:bedrock:*:${this.account}:guardrail/*`,
        `arn:aws:bedrock:*:${this.account}:guardrail-profile/*`,
      ],
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:ListRegistryRecords', 'bedrock-agentcore:GetRegistryRecord'],
      resources: [`arn:aws:bedrock-agentcore:us-east-1:${this.account}:registry/b2hOSZL4eOhDXAyk*`],
    }));

    // ---------- WebSocket API — $connect에서 Cognito 토큰 필수 ----------
    const wsApi = new apigwv2.WebSocketApi(this, 'WsApi', {
      apiName: 'bank-platform-ws',
      connectRouteOptions: { integration: new integrations.WebSocketLambdaIntegration('C', fn) },
      disconnectRouteOptions: { integration: new integrations.WebSocketLambdaIntegration('D', fn) },
      defaultRouteOptions: { integration: new integrations.WebSocketLambdaIntegration('M', fn) },
    });
    const wsStage = new apigwv2.WebSocketStage(this, 'WsStage', {
      webSocketApi: wsApi, stageName: 'prod', autoDeploy: true,
      throttle: { rateLimit: 20, burstLimit: 40 },
    });
    wsStage.grantManagementApiAccess(fn);

    new cdk.CfnOutput(this, 'WebBucketName', { value: webBucket.bucketName });
    new cdk.CfnOutput(this, 'DistributionId', { value: dist.distributionId });
    new cdk.CfnOutput(this, 'WebUrl', { value: `https://${dist.distributionDomainName}` });
    new cdk.CfnOutput(this, 'WssUrl', { value: wsStage.url });
    new cdk.CfnOutput(this, 'OnpremAlbDns', { value: alb.loadBalancerDnsName });
    new cdk.CfnOutput(this, 'NeptuneEndpoint', { value: nepCluster.attrEndpoint });
  }
}
