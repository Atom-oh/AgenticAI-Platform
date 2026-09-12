import * as cdk from 'aws-cdk-lib';
import { BankPlatformStack } from '../lib/stack';
import { BankPlatformPlaneStack } from '../lib/plane-stack';
import { BankPlatformPrivacyStack } from '../lib/privacy-stack';

const app = new cdk.App();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-northeast-2' };

// 컨텍스트: -c planeDeployed=true -c graphBackend=neptune (deploy.sh가 지정)
const planeDeployed = String(app.node.tryGetContext('planeDeployed') ?? process.env.PLANE_DEPLOYED ?? 'false') === 'true';
const graphBackend = (app.node.tryGetContext('graphBackend') ?? process.env.GRAPH_BACKEND ?? 'local') as 'local' | 'neptune';
const cognitoUserPoolId = app.node.tryGetContext('cognitoUserPoolId') ?? process.env.COGNITO_USER_POOL_ID ?? 'ap-northeast-2_h2rhe1TKo';
const mainStackName = app.node.tryGetContext('mainStackName') ?? process.env.MAIN_STACK ?? 'BankPlatform';
const cognitoClientId = app.node.tryGetContext('cognitoClientId') ?? process.env.COGNITO_CLIENT_ID ?? '3o8u65rhccnr1ug1f94tctmb0b';
const mydataPrivacyFunctionArn = app.node.tryGetContext('mydataPrivacyFunctionArn') as string | undefined;

// Optional additive stack. All existing-cluster metadata is explicit: no lookups,
// GPU resources, VPC peering or changes to the existing plane stack.
const privacyVpcId = app.node.tryGetContext('privacyVpcId') as string | undefined;
if (privacyVpcId) {
  const listContext = (name: string): string[] => {
    const value = app.node.tryGetContext(name);
    return Array.isArray(value) ? value : String(value ?? '').split(',').map(v => v.trim()).filter(Boolean);
  };
  new BankPlatformPrivacyStack(app, 'BankPlatformPrivacy', {
    env, privacyVpcId,
    privacyVpcCidr: String(app.node.tryGetContext('privacyVpcCidr') ?? ''),
    privacySubnetIds: listContext('privacySubnetIds'),
    privacyAvailabilityZones: listContext('privacyAvailabilityZones'),
    privacyTargetSecurityGroupIds: listContext('privacyTargetSecurityGroupIds'),
    description: 'Private MyData CPU gateway entry point and IAM relay; reuse existing EKS GPU model',
  });
}

new BankPlatformPlaneStack(app, 'BankPlatformPlane', {
  env,
  description: 'Agentic AI Platform bank demo - Two-Plane: isolated onprem VPC (ECS/RDS), Neptune, bridge, writer (no NAT)',
});
new BankPlatformStack(app, mainStackName, {
  env, planeDeployed, graphBackend, cognitoUserPoolId, cognitoClientId, mydataPrivacyFunctionArn,
  description: 'Agentic AI Platform bank demo (SPEC.md) - cloud plane: web, websocket api, engines, registry, guardrails',
});
