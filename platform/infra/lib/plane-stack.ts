/**
 * Two-Plane 스택 (SPEC §3.1) — "온프렘 역할" VPC. 인터넷 경로 없음(NAT 0, 퍼블릭 서브넷 0).
 *
 *  onprem-isolated : ECS Fargate 온프렘 서비스(정확 조회·계산·마스킹·감사 원문·벡터 인덱스) + RDS PostgreSQL
 *                    → 이 서브넷의 SG는 Bedrock 엔드포인트에 접근할 수 없다 (경계 강제).
 *  cloud-isolated  : Neptune Serverless + 브리지 Lambda + Writer Lambda(외부 원문 미접근 — 인터넷 경로 자체가 없다)
 *  진입점           : 브리지 Lambda(IAM invoke)만. 내부 ALB는 브리지 SG에서만 허용.
 *  출력             : SSM 파라미터 /bank-platform/plane/* (메인 스택이 배포 시점에 해석 — 스택 간 export 결합 없음)
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as neptune from 'aws-cdk-lib/aws-neptune';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as aoss from 'aws-cdk-lib/aws-opensearchserverless';

export const PLANE_PARAM_PREFIX = '/bank-platform/plane';

export class BankPlatformPlaneStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const vpc = new ec2.Vpc(this, 'PlaneVpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.77.0.0/16'),
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'onprem-isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
        { name: 'cloud-isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });
    const onpremSubnets: ec2.SubnetSelection = { subnetGroupName: 'onprem-isolated' };
    const cloudSubnets: ec2.SubnetSelection = { subnetGroupName: 'cloud-isolated' };

    // ---------- 보안 그룹 (경계 규칙의 실체) ----------
    const taskSg = new ec2.SecurityGroup(this, 'OnpremTaskSg', { vpc, description: 'onprem plane ECS task', allowAllOutbound: true });
    const albSg = new ec2.SecurityGroup(this, 'OnpremAlbSg', { vpc, description: 'onprem internal alb', allowAllOutbound: false });
    const bridgeSg = new ec2.SecurityGroup(this, 'BridgeSg', { vpc, description: 'bridge lambda (cloud plane)', allowAllOutbound: true });
    const writerSg = new ec2.SecurityGroup(this, 'WriterSg', { vpc, description: 'report writer lambda (no internet route)', allowAllOutbound: true });
    const nepSg = new ec2.SecurityGroup(this, 'NeptuneSg', { vpc, description: 'neptune (cloud plane)', allowAllOutbound: false });
    const infraEpSg = new ec2.SecurityGroup(this, 'InfraEndpointSg', { vpc, description: 'ecr/logs/secrets endpoints', allowAllOutbound: false });
    const cloudEpSg = new ec2.SecurityGroup(this, 'CloudEndpointSg', { vpc, description: 'bedrock-runtime/lambda endpoints - cloud plane only', allowAllOutbound: false });

    albSg.addIngressRule(bridgeSg, ec2.Port.tcp(80), 'bridge to onprem alb only');
    albSg.addEgressRule(taskSg, ec2.Port.tcp(8080), 'alb to task');
    taskSg.addIngressRule(albSg, ec2.Port.tcp(8080), 'alb to task');
    nepSg.addIngressRule(bridgeSg, ec2.Port.tcp(8182), 'bridge to neptune');
    // 인프라 엔드포인트(ECR·Logs·Secrets): 온프렘 태스크와 클라우드 Lambda 모두 사용
    infraEpSg.addIngressRule(taskSg, ec2.Port.tcp(443), 'task to ecr/logs/secrets');
    infraEpSg.addIngressRule(bridgeSg, ec2.Port.tcp(443), 'bridge to secrets/logs');
    infraEpSg.addIngressRule(writerSg, ec2.Port.tcp(443), 'writer to logs');
    // 클라우드 엔드포인트(Bedrock·Lambda): 온프렘 태스크 SG는 여기 없다 — 온프렘은 Bedrock에 닿을 수 없다
    cloudEpSg.addIngressRule(writerSg, ec2.Port.tcp(443), 'writer to bedrock/lambda endpoints');
    cloudEpSg.addIngressRule(bridgeSg, ec2.Port.tcp(443), 'bridge to lambda endpoint (health)');

    vpc.addGatewayEndpoint('S3Gw', { service: ec2.GatewayVpcEndpointAwsService.S3 });
    vpc.addInterfaceEndpoint('EcrApi', { service: ec2.InterfaceVpcEndpointAwsService.ECR, subnets: onpremSubnets, securityGroups: [infraEpSg] });
    vpc.addInterfaceEndpoint('EcrDkr', { service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER, subnets: onpremSubnets, securityGroups: [infraEpSg] });
    vpc.addInterfaceEndpoint('Logs', { service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS, subnets: onpremSubnets, securityGroups: [infraEpSg] });
    vpc.addInterfaceEndpoint('Secrets', { service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER, subnets: onpremSubnets, securityGroups: [infraEpSg] });
    vpc.addInterfaceEndpoint('BedrockRt', { service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME, subnets: cloudSubnets, securityGroups: [cloudEpSg] });
    vpc.addInterfaceEndpoint('LambdaEp', { service: ec2.InterfaceVpcEndpointAwsService.LAMBDA, subnets: cloudSubnets, securityGroups: [cloudEpSg] });

    // ---------- 온프렘 플레인: RDS PostgreSQL (합성 개인데이터 + 감사 원문, §3.2) ----------
    const db = new rds.DatabaseInstance(this, 'PersonalDb', {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.of('16.9', '16') }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      vpc, vpcSubnets: onpremSubnets,
      credentials: rds.Credentials.fromGeneratedSecret('bankadmin'),
      databaseName: 'bank',
      allocatedStorage: 20,
      multiAz: false,
      publiclyAccessible: false,
      storageEncrypted: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      deletionProtection: false,
      backupRetention: cdk.Duration.days(1),
    });
    db.connections.allowFrom(taskSg, ec2.Port.tcp(5432), 'onprem task to rds');

    // 브리지 ↔ 온프렘 공유 토큰 (평문은 어디에도 적지 않는다)
    const planeToken = new secretsmanager.Secret(this, 'PlaneToken', {
      description: 'bank-platform onprem plane shared token (bridge -> onprem)',
      generateSecretString: { excludePunctuation: true, passwordLength: 40 },
    });

    // ---------- 온프렘 플레인: ECS Fargate ----------
    const cluster = new ecs.Cluster(this, 'OnpremCluster', { vpc, containerInsightsV2: ecs.ContainerInsights.ENABLED });
    const image = new ecrAssets.DockerImageAsset(this, 'OnpremImage', {
      directory: '../onprem', platform: ecrAssets.Platform.LINUX_ARM64,
    });
    const taskDef = new ecs.FargateTaskDefinition(this, 'OnpremTask', {
      cpu: 512, memoryLimitMiB: 1024,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64 },
    });
    const onpremContainer = taskDef.addContainer('onprem', {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'onprem', logRetention: logs.RetentionDays.ONE_WEEK }),
      environment: { DATA_BACKEND: 'rds', PGDATABASE: 'bank', DATA_SOURCE: 'RDS PostgreSQL (프라이빗 서브넷)' },
      secrets: {
        PGUSER: ecs.Secret.fromSecretsManager(db.secret!, 'username'),
        PGPASSWORD: ecs.Secret.fromSecretsManager(db.secret!, 'password'),
        PGHOST: ecs.Secret.fromSecretsManager(db.secret!, 'host'),
        PLANE_TOKEN: ecs.Secret.fromSecretsManager(planeToken),
      },
      portMappings: [{ containerPort: 8080 }],
      healthCheck: { command: ['CMD-SHELL', 'python3 -c "import urllib.request;urllib.request.urlopen(\'http://127.0.0.1:8080/health\',timeout=3)" || exit 1'],
        interval: cdk.Duration.seconds(30), startPeriod: cdk.Duration.seconds(120) },
    });
    const onpremSvc = new ecs.FargateService(this, 'OnpremSvc', {
      cluster, taskDefinition: taskDef, desiredCount: 1,
      vpcSubnets: onpremSubnets, assignPublicIp: false, securityGroups: [taskSg],
      minHealthyPercent: 0, maxHealthyPercent: 200,
      circuitBreaker: { rollback: true },
    });

    const alb = new elbv2.ApplicationLoadBalancer(this, 'OnpremAlb', {
      vpc, internetFacing: false, vpcSubnets: onpremSubnets, securityGroup: albSg,
    });
    const listener = alb.addListener('L', { port: 80, open: false });
    listener.addTargets('T', {
      port: 8080, protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [onpremSvc],
      healthCheck: { path: '/health', healthyHttpCodes: '200', interval: cdk.Duration.seconds(15) },
      deregistrationDelay: cdk.Duration.seconds(10),
    });

    // ---------- 클라우드 플레인(VPC 내부): Neptune Serverless (온톨로지, §3.2·§11) ----------
    const nepSubnets = new neptune.CfnDBSubnetGroup(this, 'NepSubnets', {
      dbSubnetGroupDescription: 'neptune (cloud plane)',
      subnetIds: vpc.selectSubnets(cloudSubnets).subnetIds,
    });
    const nepCluster = new neptune.CfnDBCluster(this, 'NepCluster', {
      dbSubnetGroupName: nepSubnets.ref,
      vpcSecurityGroupIds: [nepSg.securityGroupId],
      serverlessScalingConfiguration: { minCapacity: 1, maxCapacity: 2.5 },
      storageEncrypted: true,
      deletionProtection: false,
    });
    nepCluster.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);
    const nepInstance = new neptune.CfnDBInstance(this, 'NepInstance', {
      dbClusterIdentifier: nepCluster.ref,
      dbInstanceClass: 'db.serverless',
    });
    nepInstance.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    // 브리지 실행 역할을 먼저 만든다 (AOSS 데이터 접근 정책이 참조)
    const bridgeRole = new iam.Role(this, 'BridgeFnRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole')],
      description: 'bank-platform bridge lambda role',
    });

    // ---------- 벡터 인덱스: OpenSearch Serverless (SPEC v2 §16 확정) — VPC 엔드포인트로만 접근, 데이터는 VPC 내부 ----------
    const aossSg = new ec2.SecurityGroup(this, 'AossEndpointSg', { vpc, description: 'opensearch serverless vpc endpoint', allowAllOutbound: false });
    aossSg.addIngressRule(taskSg, ec2.Port.tcp(443), 'vpc-internal service to aoss');
    aossSg.addIngressRule(bridgeSg, ec2.Port.tcp(443), 'bridge to aoss (index bootstrap)');
    const collectionName = 'bank-platform-rag';
    const aossVpce = new aoss.CfnVpcEndpoint(this, 'AossVpce', {
      name: 'bank-platform-rag-vpce',
      vpcId: vpc.vpcId,
      subnetIds: vpc.selectSubnets(onpremSubnets).subnetIds,
      securityGroupIds: [aossSg.securityGroupId],
    });
    const aossEncryption = new aoss.CfnSecurityPolicy(this, 'AossEncryption', {
      name: 'bank-platform-rag-enc', type: 'encryption',
      policy: JSON.stringify({ Rules: [{ ResourceType: 'collection', Resource: [`collection/${collectionName}`] }], AWSOwnedKey: true }),
    });
    // 네트워크 정책: 퍼블릭 접근 금지 — VPC 엔드포인트에서만 (대시보드/API 모두)
    const aossNetwork = new aoss.CfnSecurityPolicy(this, 'AossNetwork', {
      name: 'bank-platform-rag-net', type: 'network',
      policy: JSON.stringify([{
        Rules: [{ ResourceType: 'collection', Resource: [`collection/${collectionName}`] },
                { ResourceType: 'dashboard', Resource: [`collection/${collectionName}`] }],
        AllowFromPublic: false, SourceVPCEs: [aossVpce.attrId],
      }]),
    });
    aossNetwork.addDependency(aossVpce);
    const aossCollection = new aoss.CfnCollection(this, 'AossCollection', {
      name: collectionName, type: 'VECTORSEARCH', standbyReplicas: 'DISABLED',
      description: 'bank platform RAG index (hybrid BM25 + knn) — VPC endpoint only',
    });
    aossCollection.addDependency(aossEncryption);
    aossCollection.addDependency(aossNetwork);
    // 데이터 접근 정책: 플레인 태스크 역할(검색·적재) + 브리지 역할(부트스트랩)
    const aossAccess = new aoss.CfnAccessPolicy(this, 'AossAccess', {
      name: 'bank-platform-rag-access', type: 'data',
      policy: JSON.stringify([{
        Rules: [
          { ResourceType: 'index', Resource: [`index/${collectionName}/*`],
            Permission: ['aoss:CreateIndex', 'aoss:DeleteIndex', 'aoss:UpdateIndex', 'aoss:DescribeIndex', 'aoss:ReadDocument', 'aoss:WriteDocument'] },
          { ResourceType: 'collection', Resource: [`collection/${collectionName}`],
            Permission: ['aoss:CreateCollectionItems', 'aoss:DescribeCollectionItems', 'aoss:UpdateCollectionItems'] },
        ],
        Principal: [taskDef.taskRole.roleArn, bridgeRole.roleArn],
      }]),
    });
    aossAccess.addDependency(aossCollection);
    taskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({ actions: ['aoss:APIAccessAll'], resources: [aossCollection.attrArn] }));
    bridgeRole.addToPrincipalPolicy(new iam.PolicyStatement({ actions: ['aoss:APIAccessAll'], resources: [aossCollection.attrArn] }));
    // VPC 내부 서비스가 AOSS를 쓰도록 (컨테이너 정의는 위에서 만들었으므로 여기서 env 추가)
    onpremContainer.addEnvironment('VECTOR_BACKEND', 'aoss');
    onpremContainer.addEnvironment('AOSS_ENDPOINT', aossCollection.attrCollectionEndpoint);
    onpremContainer.addEnvironment('AOSS_INDEX', 'bank-rag-chunks');
    onpremContainer.addEnvironment('AOSS_REGION', this.region);

    // ---------- 브리지 Lambda — 플레인의 유일한 입구 (IAM invoke) ----------
    const bridgeFn = new lambda.Function(this, 'BridgeFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset('../bridge'),
      memorySize: 256,
      timeout: cdk.Duration.seconds(60),
      vpc, vpcSubnets: cloudSubnets, securityGroups: [bridgeSg], role: bridgeRole,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        ONPREM_URL: `http://${alb.loadBalancerDnsName}`,
        NEPTUNE_ENDPOINT: nepCluster.attrEndpoint,
        AOSS_ENDPOINT: aossCollection.attrCollectionEndpoint,
        AOSS_INDEX: 'bank-rag-chunks',
        PLANE_TOKEN: planeToken.secretValue.unsafeUnwrap(), // CFN 동적 참조 — 템플릿에는 평문이 남지 않는다
      },
      description: 'bank-platform bridge: cloud plane -> onprem alb / neptune (no NAT, no AWS API calls)',
    });

    // ---------- Writer Lambda (F7) — 인터넷 경로가 없는 서브넷: 외부 원문 접근이 물리적으로 불가 ----------
    const writerFn = new lambda.Function(this, 'WriterFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'report.writer_handler.handler',
      code: lambda.Code.fromAsset('../api-dist'),
      memorySize: 1024,
      timeout: cdk.Duration.seconds(90),
      vpc, vpcSubnets: cloudSubnets, securityGroups: [writerSg],
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: { GEN_MODEL: 'global.anthropic.claude-sonnet-5' },
      description: 'bank-platform report writer: internal docs + bedrock via VPC endpoint, no internet route',
    });
    writerFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['arn:aws:bedrock:*::foundation-model/*', `arn:aws:bedrock:*:${this.account}:inference-profile/*`],
    }));
    // 내부 문서 조회 도구 — Writer만 호출 가능, Reader(메인 스택)는 권한이 없다
    const internalToolFn = new lambda.Function(this, 'InternalToolFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'report.internal_tool_handler.handler',
      code: lambda.Code.fromAsset('../api-dist'),
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      logRetention: logs.RetentionDays.ONE_WEEK,
      description: 'bank-platform internal document search tool (Writer only)',
    });
    internalToolFn.grantInvoke(writerFn);
    writerFn.addEnvironment('INTERNAL_TOOL_FN', internalToolFn.functionName);

    // ---------- 메인 스택으로 넘기는 값 (SSM — 스택 간 export 결합을 피한다) ----------
    const param = (name: string, value: string) =>
      new ssm.StringParameter(this, `P${name}`, { parameterName: `${PLANE_PARAM_PREFIX}/${name}`, stringValue: value });
    param('bridgeFnName', bridgeFn.functionName);
    param('bridgeFnArn', bridgeFn.functionArn);
    param('writerFnName', writerFn.functionName);
    param('writerFnArn', writerFn.functionArn);
    param('internalToolFnName', internalToolFn.functionName);
    param('internalToolFnArn', internalToolFn.functionArn);
    param('neptuneEndpoint', nepCluster.attrEndpoint);
    param('onpremAlbDns', alb.loadBalancerDnsName);
    param('vpcId', vpc.vpcId);
    param('aossEndpoint', aossCollection.attrCollectionEndpoint);
    param('aossCollectionArn', aossCollection.attrArn);

    new cdk.CfnOutput(this, 'BridgeFnName', { value: bridgeFn.functionName });
    new cdk.CfnOutput(this, 'WriterFnName', { value: writerFn.functionName });
    new cdk.CfnOutput(this, 'InternalToolFnName', { value: internalToolFn.functionName });
    new cdk.CfnOutput(this, 'NeptuneEndpoint', { value: nepCluster.attrEndpoint });
    new cdk.CfnOutput(this, 'OnpremAlbDns', { value: alb.loadBalancerDnsName });
    new cdk.CfnOutput(this, 'OnpremClusterName', { value: cluster.clusterName });
    new cdk.CfnOutput(this, 'OnpremServiceName', { value: onpremSvc.serviceName });
    new cdk.CfnOutput(this, 'AossEndpoint', { value: aossCollection.attrCollectionEndpoint });
  }
}
