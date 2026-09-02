/**
 * 메인 스택 — 클라우드 플레인 (SPEC §7).
 *  S3(프라이빗)+CloudFront(유일한 퍼블릭 진입점) · WebSocket API($connect Cognito 검증) · WsFn(VPC 밖: Bedrock·Cognito·프록시)
 *  AdminFn(IAM invoke 전용) · ReaderFn(F7, 내부 도구 권한 없음) · GatesFn(F5, Node 20)
 *  테이블: 연결 · 트레이스(F6, 원문 없음) · 캐시/예산(§6.3·§10) · Registry(F4)
 *  Bedrock Guardrails(IaC) · CloudWatch 알람/대시보드
 * 플레인 스택 값은 SSM(/bank-platform/plane/*)에서 배포 시점에 해석한다.
 * 기존 리소스(WebBucket·WebDist·ConnTable·WsFn·WsApi·WsStage)의 construct ID는 유지해 교체를 막는다.
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as fs from 'fs';
import * as path from 'path';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import { PLANE_PARAM_PREFIX } from './plane-stack';

export interface BankPlatformStackProps extends cdk.StackProps {
  /** 플레인 스택이 배포되어 SSM 파라미터가 존재할 때 true (브리지·Writer 연결) */
  planeDeployed: boolean;
  graphBackend: 'local' | 'neptune';
  cognitoUserPoolId: string;
  cognitoClientId: string;
  /** 커스텀 도메인 (기본 agent.atomai.click) 과 us-east-1 ACM 인증서 ARN */
  domainName?: string;
  certificateArn?: string;
}

export class BankPlatformStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BankPlatformStackProps) {
    super(scope, id, props);
    const region = this.region;
    const account = this.account;

    // ---------- 프론트엔드: S3 (프라이빗) + CloudFront ----------
    const webBucket = new s3.Bucket(this, 'WebBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    const dist = new cloudfront.Distribution(this, 'WebDist', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(webBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
      },
      defaultRootObject: 'index.html',
      // 커스텀 도메인 (Route53 존 atomai.click, ACM us-east-1 *.atomai.click) — CDK에 고정해 out-of-band 변경이 되돌려지지 않게 한다
      domainNames: [props.domainName ?? 'agent.atomai.click'],
      certificate: acm.Certificate.fromCertificateArn(this, 'WebCert',
        props.certificateArn ?? 'arn:aws:acm:us-east-1:180294183052:certificate/f6b6907a-5747-4039-967a-a8c7c73116a7'),
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
      comment: 'Agentic AI Platform - bank demo',
    });

    // ---------- 테이블 ----------
    const connTable = new dynamodb.Table(this, 'ConnTable', {
      partitionKey: { name: 'connId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const traceTable = new dynamodb.Table(this, 'TraceTable', {
      partitionKey: { name: 'traceId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });
    traceTable.addGlobalSecondaryIndex({
      indexName: 'byDay',
      partitionKey: { name: 'day', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'ts', type: dynamodb.AttributeType.NUMBER },
    });
    const cacheTable = new dynamodb.Table(this, 'CacheTable', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const registryTable = new dynamodb.Table(this, 'RegistryTable', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });
    registryTable.addGlobalSecondaryIndex({
      indexName: 'byStatus',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'updatedAt', type: dynamodb.AttributeType.NUMBER },
    });

    // ---------- Bedrock Guardrails — 코드로 정의 (§12.4 목 금지, 하드코딩 ID 제거) ----------
    const guardrail = new bedrock.CfnGuardrail(this, 'Guardrail', {
      name: `${id}-guardrail`,
      description: '아톰은행 상담: 투자권유 차단 · PII 탐지/익명화 · 근거 점수 · 비속어',
      blockedInputMessaging: '이 요청은 은행 상담 정책(투자권유 금지)에 따라 답변할 수 없습니다. 상품 조건·자격·한도 등 사실 확인 질문을 해 주세요.',
      blockedOutputsMessaging: '생성된 답변이 은행 상담 정책에 맞지 않아 표시하지 않습니다.',
      // STANDARD 티어 + APAC 가드레일 프로파일: 한국어 토픽 탐지에 필요 (CLASSIC은 S5 질문을 놓친다 — 2026-09-02 실측)
      crossRegionConfig: { guardrailProfileArn: `arn:aws:bedrock:${region}:${account}:guardrail-profile/apac.guardrail.v1:0` },
      topicPolicyConfig: {
        topicsTierConfig: { tierName: 'STANDARD' },
        topicsConfig: [{
          name: 'investment-solicitation',   // CFN 제약: 이름은 ASCII만 — UI 라벨은 '투자권유'
          type: 'DENY',
          definition: '특정 금융상품·펀드·주식·코인의 수익률을 비교하거나 매수·매도·가입을 권유하는 질문과 답변. '
            + '"어떤 상품이 돈을 가장 많이 벌 수 있는지", "지금 무엇을 사야 하는지" 같은 수익 추구형 추천 요청을 포함한다. '
            + '상품의 자격 조건·우대금리 조건·한도 계산·규정 안내는 해당하지 않는다.',
          examples: [
            '어떤 상품이 제일 돈 많이 벌어요?',
            '지금 가입하면 수익 제일 좋은 펀드 추천해줘',
            '주식이랑 예금 중에 뭐가 더 돈 돼요?',
            '코인 지금 사야 돼요?',
            '수익률 높은 순으로 상품 알려줘',
          ],
        }],
      },
      sensitiveInformationPolicyConfig: {
        piiEntitiesConfig: [
          { type: 'NAME', action: 'ANONYMIZE', inputAction: 'NONE', outputAction: 'NONE' },   // 탐지·표시만 — 한국어 NER 오탐(예: '인정'→{NAME}) 방지, 이름 마스킹은 VPC 내부 게이트가 담당
          { type: 'EMAIL', action: 'ANONYMIZE', inputAction: 'NONE', outputAction: 'ANONYMIZE' },
          { type: 'PHONE', action: 'ANONYMIZE', inputAction: 'NONE', outputAction: 'ANONYMIZE' },
          { type: 'CREDIT_DEBIT_CARD_NUMBER', action: 'BLOCK' },
        ],
        regexesConfig: [
          { name: 'KR_RRN', description: '주민등록번호/외국인등록번호 형식', pattern: '\\d{6}-?[1-8]\\d{6}', action: 'BLOCK' },
          { name: 'CUSTOMER_TOKEN', description: '고객 토큰 식별자', pattern: 'CUST-\\d{3,}', action: 'ANONYMIZE', inputAction: 'NONE', outputAction: 'ANONYMIZE' },
          { name: 'ACCOUNT_TOKEN', description: '계좌 토큰 식별자', pattern: 'ACCT-\\d{3,}', action: 'ANONYMIZE', inputAction: 'NONE', outputAction: 'ANONYMIZE' },
        ],
      },
      contextualGroundingPolicyConfig: {
        filtersConfig: [
          { type: 'GROUNDING', threshold: 0.5, action: 'NONE' },   // 점수만 산출·표시 (차단은 수치 검증기가 담당)
          { type: 'RELEVANCE', threshold: 0.5, action: 'NONE' },
        ],
      },
      wordPolicyConfig: { managedWordListsConfig: [{ type: 'PROFANITY' }] },
    });
    const guardrailVersion = new bedrock.CfnGuardrailVersion(this, 'GuardrailV', {
      guardrailIdentifier: guardrail.attrGuardrailId,
      description: 'bank platform release 3 (STANDARD tier + APAC profile, NAME detect-only)',
    });

    // ---------- 플레인 스택 값 (SSM, 배포 시 해석) ----------
    const planeParam = (name: string) => ssm.StringParameter.valueForStringParameter(this, `${PLANE_PARAM_PREFIX}/${name}`);
    const bridgeFnName = props.planeDeployed ? planeParam('bridgeFnName') : '';
    const bridgeFnArn = props.planeDeployed ? planeParam('bridgeFnArn') : '';
    const writerFnName = props.planeDeployed ? planeParam('writerFnName') : '';
    const writerFnArn = props.planeDeployed ? planeParam('writerFnArn') : '';
    const internalToolFnName = props.planeDeployed ? planeParam('internalToolFnName') : '';
    const neptuneEndpoint = props.planeDeployed ? planeParam('neptuneEndpoint') : '';

    // ---------- 공용 코드 자산 (deploy.sh가 api-dist 조립) ----------
    const apiCode = lambda.Code.fromAsset('../api-dist');
    const bedrockInvoke = new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['arn:aws:bedrock:*::foundation-model/*', `arn:aws:bedrock:*:${account}:inference-profile/*`],
    });
    const guardrailApply = new iam.PolicyStatement({
      actions: ['bedrock:ApplyGuardrail'],
      resources: [`arn:aws:bedrock:${region}:${account}:guardrail/*`, `arn:aws:bedrock:*:${account}:guardrail-profile/*`],
    });

    // ---------- F5 게이트 실행기 (Node 20) — deploy.sh가 gates/에 npm ci ----------
    const gatesDir = path.resolve(__dirname, '../../gates');
    const gatesFn = fs.existsSync(path.join(gatesDir, 'index.js')) ? new lambda.Function(this, 'GatesFn', {
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(gatesDir, { exclude: ['test', '*.md', 'package-lock.json'] }),
      memorySize: 2048,
      timeout: cdk.Duration.seconds(120),
      architecture: lambda.Architecture.ARM_64,
      logRetention: logs.RetentionDays.ONE_WEEK,
      description: 'bank-platform screen generation gates: tsc / eslint / axe-core (real, no mocks)',
    }) : undefined;

    // ---------- F7 Reader — 외부 콘텐츠 전용. 내부 도구 invoke 권한이 없다(의도된 AccessDenied) ----------
    const readerFn = new lambda.Function(this, 'ReaderFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'report.reader_handler.handler',
      code: apiCode,
      memorySize: 1024,
      timeout: cdk.Duration.seconds(90),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        GEN_MODEL: 'global.anthropic.claude-sonnet-5',
        INTERNAL_TOOL_FN: internalToolFnName,     // 시도는 하되 IAM이 막는다
        ALLOWED_SAMPLE_HOSTS: dist.distributionDomainName,
      },
      description: 'bank-platform report reader: external web only, no internal tool permission',
    });
    readerFn.addToRolePolicy(bedrockInvoke);

    // ---------- AgentCore: 플랫폼 도구 Gateway(MCP, IAM 인바운드) + Lambda 타깃 + Harness 실행 역할 + Skills(S3) ----------
    const toolsFn = new lambda.Function(this, 'PlatformToolsFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'agentcore.gateway_tools.handler',
      code: apiCode,
      memorySize: 1536,
      timeout: cdk.Duration.seconds(120),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        REGISTRY_TABLE: registryTable.tableName,
        GRAPH_BACKEND: props.graphBackend,
        NEPTUNE_ENDPOINT: neptuneEndpoint,
        BRIDGE_FN: bridgeFnName,
        ALLOW_LOCAL_PLANE: props.planeDeployed ? '0' : '1',
        GATES_FN: gatesFn ? gatesFn.functionName : '',
        GEN_MODEL: 'global.anthropic.claude-sonnet-5',
      },
      description: 'bank-platform AgentCore Gateway target: platform tools (masked customer lookup, calc, ontology, registry consumer, gates)',
    });
    registryTable.grantReadData(toolsFn);
    toolsFn.addToRolePolicy(bedrockInvoke);
    if (gatesFn) gatesFn.grantInvoke(toolsFn);
    if (props.planeDeployed) {
      toolsFn.addToRolePolicy(new iam.PolicyStatement({ actions: ['lambda:InvokeFunction'], resources: [bridgeFnArn] }));
    }
    const gatewayRole = new iam.Role(this, 'GatewayExecRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'bank-platform-tools gateway execution role (invoke tool lambda)',
    });
    toolsFn.grantInvoke(gatewayRole);
    const gateway = new agentcore.CfnGateway(this, 'ToolsGateway', {
      name: `${id.toLowerCase()}-tools`,
      description: 'Bank Agentic AI Platform tools (MCP) - IAM inbound; used by AgentCore Harness agents',
      authorizerType: 'AWS_IAM',
      protocolType: 'MCP',
      roleArn: gatewayRole.roleArn,
      exceptionLevel: 'DEBUG',
    });
    const toolSchema = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../agentcore/tool_schema.json'), 'utf8'));
    const gatewayTarget = new agentcore.CfnGatewayTarget(this, 'ToolsTarget', {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: 'platform',
      description: 'platform tools lambda target',
      targetConfiguration: { mcp: { lambda: { lambdaArn: toolsFn.functionArn, toolSchema: { inlinePayload: toolSchema } } } },
      credentialProviderConfigurations: [{ credentialProviderType: 'GATEWAY_IAM_ROLE' }],
    });
    gatewayTarget.addDependency(gateway);

    // Skills — Harness가 S3의 SKILL.md 폴더를 로드한다 (Registry SKILL 레코드와 이름 일치)
    const skillsBucket = new s3.Bucket(this, 'SkillsBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL, encryption: s3.BucketEncryption.S3_MANAGED, enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY, autoDeleteObjects: true,
    });
    const skillsDir = path.resolve(__dirname, '../../skills-dist');
    if (fs.existsSync(skillsDir)) {
      new s3deploy.BucketDeployment(this, 'SkillsDeploy', {
        sources: [s3deploy.Source.asset(skillsDir)], destinationBucket: skillsBucket, destinationKeyPrefix: 'skills',
        logRetention: logs.RetentionDays.ONE_WEEK,
      });
    }
    const harnessRole = new iam.Role(this, 'HarnessExecRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': account } },
      }),
      description: 'bank-platform AgentCore Harness execution role',
    });
    harnessRole.addToPolicy(bedrockInvoke);
    harnessRole.addToPolicy(guardrailApply);
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeGateway', 'bedrock-agentcore:GetGateway', 'bedrock-agentcore:ListGatewayTargets'],
      resources: [gateway.attrGatewayArn, `${gateway.attrGatewayArn}/*`],
    }));
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:CreateEvent', 'bedrock-agentcore:GetEvent', 'bedrock-agentcore:ListEvents', 'bedrock-agentcore:ListSessions',
        'bedrock-agentcore:RetrieveMemoryRecords', 'bedrock-agentcore:GetMemoryRecord', 'bedrock-agentcore:ListMemoryRecords', 'bedrock-agentcore:DeleteEvent'],
      resources: [`arn:aws:bedrock-agentcore:${region}:${account}:memory/*`],
    }));
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogGroups', 'logs:DescribeLogStreams',
        'xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'cloudwatch:PutMetricData'],
      resources: ['*'],
    }));
    skillsBucket.grantRead(harnessRole);
    const agentcoreOps = new iam.PolicyStatement({
      actions: ['bedrock-agentcore:CreateHarness', 'bedrock-agentcore:GetHarness', 'bedrock-agentcore:ListHarnesses', 'bedrock-agentcore:UpdateHarness',
        'bedrock-agentcore:ListHarnessVersions', 'bedrock-agentcore:DeleteHarness', 'bedrock-agentcore:InvokeHarness',
        'bedrock-agentcore:CreateMemory', 'bedrock-agentcore:GetMemory', 'bedrock-agentcore:ListMemories',
        'bedrock-agentcore:GetGateway', 'bedrock-agentcore:ListGatewayTargets', 'bedrock-agentcore:GetGatewayTarget',
        'bedrock-agentcore:CreateRegistryRecord', 'bedrock-agentcore:UpdateRegistryRecord', 'bedrock-agentcore:GetRegistryRecord',
        'bedrock-agentcore:ListRegistryRecords', 'bedrock-agentcore:SubmitRegistryRecordForApproval', 'bedrock-agentcore:UpdateRegistryRecordStatus',
        'bedrock-agentcore:GetRegistry', 'bedrock-agentcore:ListRegistries', 'bedrock-agentcore:CreateWorkloadIdentity', 'bedrock-agentcore:GetWorkloadIdentity'],
      resources: ['*'],
    });
    const passHarnessRole = new iam.PolicyStatement({ actions: ['iam:PassRole'], resources: [harnessRole.roleArn] });

    // ---------- AgentCore Runtime: Strands Agents 컨테이너 (시나리오 에이전트 4종, SPEC v2 §16 ①) ----------
    const agentsDir = path.resolve(__dirname, '../../agents');
    let agentsRuntime: agentcore.CfnRuntime | undefined;
    if (fs.existsSync(path.join(agentsDir, '_ctx', 'agent_specs.py'))) {
      const agentsImage = new ecrAssets.DockerImageAsset(this, 'AgentsImage', {
        directory: agentsDir, platform: ecrAssets.Platform.LINUX_ARM64,
      });
      const runtimeRole = new iam.Role(this, 'AgentsRuntimeRole', {
        assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
          conditions: { StringEquals: { 'aws:SourceAccount': account } },
        }),
        description: 'bank-platform AgentCore Runtime execution role (Strands agents)',
      });
      runtimeRole.addToPolicy(bedrockInvoke);
      runtimeRole.addToPolicy(guardrailApply);
      runtimeRole.addToPolicy(new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeGateway', 'bedrock-agentcore:GetGateway', 'bedrock-agentcore:ListGatewayTargets'],
        resources: [gateway.attrGatewayArn, `${gateway.attrGatewayArn}/*`],
      }));
      runtimeRole.addToPolicy(new iam.PolicyStatement({
        actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchCheckLayerAvailability'],
        resources: [agentsImage.repository.repositoryArn],
      }));
      runtimeRole.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
      runtimeRole.addToPolicy(new iam.PolicyStatement({
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogGroups', 'logs:DescribeLogStreams',
          'xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules', 'xray:GetSamplingTargets', 'cloudwatch:PutMetricData',
          'bedrock-agentcore:GetWorkloadAccessToken', 'bedrock-agentcore:GetWorkloadAccessTokenForJWT', 'bedrock-agentcore:GetWorkloadAccessTokenForUserId'],
        resources: ['*'],
      }));
      agentsRuntime = new agentcore.CfnRuntime(this, 'AgentsRuntime', {
        agentRuntimeName: 'bank_platform_agents',
        description: 'Bank Agentic AI Platform scenario agents — Strands Agents SDK, tools via AgentCore Gateway (MCP, IAM), boundary gate hook',
        agentRuntimeArtifact: { containerConfiguration: { containerUri: agentsImage.imageUri } },
        roleArn: runtimeRole.roleArn,
        networkConfiguration: { networkMode: 'PUBLIC' },
        protocolConfiguration: 'HTTP',
        environmentVariables: {
          AWS_REGION: region,
          GATEWAY_URL: gateway.attrGatewayUrl,
          GATEWAY_ARN: gateway.attrGatewayArn,
          GEN_MODEL: 'global.anthropic.claude-sonnet-5',
          GUARDRAIL_ID: guardrail.attrGuardrailId,
          GUARDRAIL_VERSION: guardrailVersion.attrVersion,
        },
      });
      agentsRuntime.node.addDependency(gatewayTarget);
      // Runtime 생성 시 서비스가 실행 역할로 ECR URI를 검증한다 — 역할의 DefaultPolicy(ECR 권한)가 먼저 붙어야 한다
      const runtimeRolePolicy = runtimeRole.node.tryFindChild('DefaultPolicy');
      if (runtimeRolePolicy) agentsRuntime.node.addDependency(runtimeRolePolicy);
      agentsRuntime.node.addDependency(runtimeRole);
    }

    // ---------- WsFn — 클라우드 플레인 본체 (VPC 밖: Bedrock·Cognito·API GW·프록시) ----------
    const fn = new lambda.Function(this, 'WsFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'ws_handler.handler',
      code: apiCode,
      memorySize: 1536,
      timeout: cdk.Duration.seconds(150),
      reservedConcurrentExecutions: 10,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        CONN_TABLE: connTable.tableName,
        TRACE_TABLE: traceTable.tableName,
        CACHE_TABLE: cacheTable.tableName,
        REGISTRY_TABLE: registryTable.tableName,
        REGISTRY_EMBED: '1',
        GRAPH_BACKEND: props.graphBackend,
        NEPTUNE_ENDPOINT: neptuneEndpoint,
        BRIDGE_FN: bridgeFnName,
        ALLOW_LOCAL_PLANE: props.planeDeployed ? '0' : '1',
        READER_FN: readerFn.functionName,
        WRITER_FN: writerFnName,
        INTERNAL_TOOL_FN: internalToolFnName,
        GATES_FN: gatesFn ? gatesFn.functionName : '',
        GEN_MODEL: 'global.anthropic.claude-sonnet-5',
        GUARDRAIL_ID: guardrail.attrGuardrailId,
        GUARDRAIL_VERSION: guardrailVersion.attrVersion,
        DAILY_TOKEN_CAP: '2000000',
        AGENTCORE_REGISTRY_ID: 'b2hOSZL4eOhDXAyk',
        COGNITO_USER_POOL_ID: props.cognitoUserPoolId,
        COGNITO_CLIENT_ID: props.cognitoClientId,
        LLM_ROUTE: 'claude',
        WEB_URL: `https://${props.domainName ?? 'agent.atomai.click'}`,   // F7 Reader 샘플 페이지 원본
        HARNESS_ROLE_ARN: harnessRole.roleArn,
        GATEWAY_ARN: gateway.attrGatewayArn,
        GATEWAY_URL: gateway.attrGatewayUrl,
        SKILLS_S3_URI: `s3://${skillsBucket.bucketName}/skills/`,
        AGENTCORE_REGISTRY_REGION: 'us-east-1',
        AGENTS_RUNTIME_ARN: agentsRuntime ? agentsRuntime.attrAgentRuntimeArn : '',
      },
      description: 'bank-platform websocket backend (cloud plane)',
    });
    connTable.grantReadWriteData(fn);
    traceTable.grantReadWriteData(fn);
    cacheTable.grantReadWriteData(fn);
    registryTable.grantReadWriteData(fn);
    fn.addToRolePolicy(bedrockInvoke);
    fn.addToRolePolicy(guardrailApply);
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:ListRegistryRecords', 'bedrock-agentcore:GetRegistryRecord'],
      resources: [`arn:aws:bedrock-agentcore:us-east-1:${account}:registry/b2hOSZL4eOhDXAyk*`],
    }));
    readerFn.grantInvoke(fn);
    if (gatesFn) gatesFn.grantInvoke(fn);
    fn.addToRolePolicy(agentcoreOps);
    fn.addToRolePolicy(passHarnessRole);
    if (agentsRuntime) {
      fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [agentsRuntime.attrAgentRuntimeArn, `${agentsRuntime.attrAgentRuntimeArn}/*`] }));
    }
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['bedrock:CallWithBearerToken'], resources: ['*'] }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['secretsmanager:GetSecretValue'], resources: [`arn:aws:secretsmanager:*:${account}:secret:bedrock/api-key*`] }));
    if (props.planeDeployed) {
      fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['lambda:InvokeFunction'], resources: [bridgeFnArn, writerFnArn] }));
    }

    // ---------- AdminFn — IAM invoke 전용 (Neptune 적재·Registry 시드·리셋). WebSocket 경로에서 도달 불가 ----------
    const adminFn = new lambda.Function(this, 'AdminFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'admin_handler.handler',
      code: apiCode,
      memorySize: 1536,
      timeout: cdk.Duration.minutes(10),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        REGISTRY_TABLE: registryTable.tableName,
        REGISTRY_EMBED: '1',
        GRAPH_BACKEND: props.graphBackend,
        NEPTUNE_ENDPOINT: neptuneEndpoint,
        BRIDGE_FN: bridgeFnName,
        HARNESS_ROLE_ARN: harnessRole.roleArn,
        GATEWAY_ARN: gateway.attrGatewayArn,
        SKILLS_S3_URI: `s3://${skillsBucket.bucketName}/skills/`,
        AGENTCORE_REGISTRY_REGION: 'us-east-1',
        AGENTCORE_REGISTRY_ID: 'b2hOSZL4eOhDXAyk',
        AGENTS_RUNTIME_ARN: agentsRuntime ? agentsRuntime.attrAgentRuntimeArn : '',
      },
      description: 'bank-platform admin ops (IAM invoke only)',
    });
    registryTable.grantReadWriteData(adminFn);
    adminFn.addToRolePolicy(bedrockInvoke);
    adminFn.addToRolePolicy(agentcoreOps);
    adminFn.addToRolePolicy(passHarnessRole);
    if (props.planeDeployed) {
      adminFn.addToRolePolicy(new iam.PolicyStatement({ actions: ['lambda:InvokeFunction'], resources: [bridgeFnArn] }));
    }

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

    // ---------- 관측성: 알람 + 대시보드 (§10) ----------
    const alarm = (name: string, metric: cloudwatch.Metric, threshold: number, desc: string) =>
      new cloudwatch.Alarm(this, name, { metric, threshold, evaluationPeriods: 1, alarmDescription: desc,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING, comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD });
    alarm('WsFnErrors', fn.metricErrors({ period: cdk.Duration.minutes(5) }), 3, 'WsFn 5분 내 오류 3건 이상');
    alarm('WsFnThrottles', fn.metricThrottles({ period: cdk.Duration.minutes(5) }), 1, 'WsFn 스로틀 발생 (예약 동시성 10)');
    alarm('WsFnDuration', fn.metricDuration({ period: cdk.Duration.minutes(5), statistic: 'p95' }), 120000, 'WsFn p95 > 120s');
    alarm('ReaderFnErrors', readerFn.metricErrors({ period: cdk.Duration.minutes(5) }), 3, 'ReaderFn 오류');
    const dash = new cloudwatch.Dashboard(this, 'Dashboard', { dashboardName: `${id}-ops` });
    dash.addWidgets(
      new cloudwatch.GraphWidget({ title: 'WsFn invocations / errors', left: [fn.metricInvocations(), fn.metricErrors()], width: 8 }),
      new cloudwatch.GraphWidget({ title: 'WsFn duration p50/p95', left: [fn.metricDuration({ statistic: 'p50' }), fn.metricDuration({ statistic: 'p95' })], width: 8 }),
      new cloudwatch.GraphWidget({ title: 'Concurrent executions', left: [fn.metric('ConcurrentExecutions', { statistic: 'Maximum' })], width: 8 }),
    );
    dash.addWidgets(
      new cloudwatch.GraphWidget({ title: 'Reader / Gates', left: [readerFn.metricInvocations(), readerFn.metricErrors(), ...(gatesFn ? [gatesFn.metricInvocations(), gatesFn.metricErrors()] : [])], width: 12 }),
      new cloudwatch.GraphWidget({ title: 'DynamoDB consumed (trace/cache/registry)', left: [traceTable.metricConsumedWriteCapacityUnits(), cacheTable.metricConsumedReadCapacityUnits(), registryTable.metricConsumedReadCapacityUnits()], width: 12 }),
    );

    new cdk.CfnOutput(this, 'WebBucketName', { value: webBucket.bucketName });
    new cdk.CfnOutput(this, 'DistributionId', { value: dist.distributionId });
    new cdk.CfnOutput(this, 'WebUrl', { value: `https://${props.domainName ?? 'agent.atomai.click'}` });
    new cdk.CfnOutput(this, 'WebCloudFrontUrl', { value: `https://${dist.distributionDomainName}` });
    new cdk.CfnOutput(this, 'WssUrl', { value: wsStage.url });
    new cdk.CfnOutput(this, 'AdminFnName', { value: adminFn.functionName });
    new cdk.CfnOutput(this, 'WsFnName', { value: fn.functionName });
    new cdk.CfnOutput(this, 'GuardrailId', { value: guardrail.attrGuardrailId });
    new cdk.CfnOutput(this, 'GuardrailVersion', { value: guardrailVersion.attrVersion });
    new cdk.CfnOutput(this, 'CognitoClientId', { value: props.cognitoClientId });
    new cdk.CfnOutput(this, 'DashboardName', { value: dash.dashboardName });
    new cdk.CfnOutput(this, 'PlaneDeployed', { value: String(props.planeDeployed) });
    new cdk.CfnOutput(this, 'GraphBackend', { value: props.graphBackend });
    new cdk.CfnOutput(this, 'GatewayArn', { value: gateway.attrGatewayArn });
    new cdk.CfnOutput(this, 'GatewayUrl', { value: gateway.attrGatewayUrl });
    new cdk.CfnOutput(this, 'HarnessRoleArn', { value: harnessRole.roleArn });
    new cdk.CfnOutput(this, 'SkillsBucketName', { value: skillsBucket.bucketName });
    new cdk.CfnOutput(this, 'PlatformToolsFnName', { value: toolsFn.functionName });
    if (agentsRuntime) new cdk.CfnOutput(this, 'AgentsRuntimeArn', { value: agentsRuntime.attrAgentRuntimeArn });
  }
}
