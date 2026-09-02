import * as cdk from 'aws-cdk-lib';
import { BankPlatformStack } from '../lib/stack';
import { BankPlatformPlaneStack } from '../lib/plane-stack';

const app = new cdk.App();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-northeast-2' };

// 컨텍스트: -c planeDeployed=true -c graphBackend=neptune (deploy.sh가 지정)
const planeDeployed = String(app.node.tryGetContext('planeDeployed') ?? process.env.PLANE_DEPLOYED ?? 'false') === 'true';
const graphBackend = (app.node.tryGetContext('graphBackend') ?? process.env.GRAPH_BACKEND ?? 'local') as 'local' | 'neptune';
const cognitoUserPoolId = app.node.tryGetContext('cognitoUserPoolId') ?? process.env.COGNITO_USER_POOL_ID ?? 'ap-northeast-2_h2rhe1TKo';
const mainStackName = app.node.tryGetContext('mainStackName') ?? process.env.MAIN_STACK ?? 'BankPlatform';
const cognitoClientId = app.node.tryGetContext('cognitoClientId') ?? process.env.COGNITO_CLIENT_ID ?? '3o8u65rhccnr1ug1f94tctmb0b';

new BankPlatformPlaneStack(app, 'BankPlatformPlane', {
  env,
  description: 'Agentic AI Platform bank demo - Two-Plane: isolated onprem VPC (ECS/RDS), Neptune, bridge, writer (no NAT)',
});
new BankPlatformStack(app, mainStackName, {
  env, planeDeployed, graphBackend, cognitoUserPoolId, cognitoClientId,
  description: 'Agentic AI Platform bank demo (SPEC.md) - cloud plane: web, websocket api, engines, registry, guardrails',
});
