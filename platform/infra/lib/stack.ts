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

    // ---------- 엔진 Lambda (api-dist는 deploy.sh가 조립) ----------
    const fn = new lambda.Function(this, 'WsFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'ws_handler.handler',
      code: lambda.Code.fromAsset('../api-dist'),
      memorySize: 1536,
      timeout: cdk.Duration.seconds(150),
      reservedConcurrentExecutions: 10, // 남용 상한 (동시 5명 시연 + 여유)
      environment: {
        CONN_TABLE: connTable.tableName,
        GRAPH_BACKEND: 'local', // Phase 3에서 neptune 전환. UI에 항상 표시된다.
        GEN_MODEL: 'apac.anthropic.claude-sonnet-4-20250514-v1:0',
        GUARDRAIL_ID: 'iol2t2rp0q9i',
        GUARDRAIL_VERSION: '3',
      },
    });
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
  }
}
