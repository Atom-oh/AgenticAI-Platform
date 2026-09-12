import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as path from 'path';
import { isIPv4 } from 'node:net';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';

export interface BankPlatformPrivacyStackProps extends cdk.StackProps {
  privacyVpcId: string;
  privacyVpcCidr: string;
  privacySubnetIds: string[];
  privacyAvailabilityZones: string[];
  /** SGs actually attached to the existing CPU nodes hosting the gateway. */
  privacyTargetSecurityGroupIds: string[];
}

/** Additive resources only: existing GPU service, VPC, routes and nodes stay owned elsewhere. */
export class BankPlatformPrivacyStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BankPlatformPrivacyStackProps) {
    super(scope, id, props);
    const cidr = /^([0-9.]+)\/(1[6-9]|2[0-8])$/.exec(props.privacyVpcCidr);
    const address = cidr?.[1] ?? '';
    const hostBits = cidr ? 32 - Number(cidr[2]) : 0;
    const addressNumber = address.split('.').reduce((n, part) => n * 256 + Number(part), 0);
    if (!/^vpc-[0-9a-f]{8,17}$/.test(props.privacyVpcId)
        || !cidr || !isIPv4(address) || !address.startsWith('10.')
        || addressNumber % (2 ** hostBits) !== 0
        || props.privacySubnetIds.length < 2
        || props.privacySubnetIds.length !== props.privacyAvailabilityZones.length
        || new Set(props.privacySubnetIds).size !== props.privacySubnetIds.length
        || new Set(props.privacyAvailabilityZones).size !== props.privacyAvailabilityZones.length
        || props.privacySubnetIds.some(v => !/^subnet-[0-9a-f]{8,17}$/.test(v))
        || props.privacyAvailabilityZones.some(v => !/^ap-northeast-2[a-d]$/.test(v))
        || props.privacyTargetSecurityGroupIds.length === 0
        || new Set(props.privacyTargetSecurityGroupIds).size !== props.privacyTargetSecurityGroupIds.length
        || props.privacyTargetSecurityGroupIds.some(v => !/^sg-[0-9a-f]{8,17}$/.test(v))) {
      throw new Error('Privacy stack needs explicit private VPC/subnet/AZ/target-SG metadata.');
    }
    const vpc = ec2.Vpc.fromVpcAttributes(this, 'ExistingFsiVpc', {
      vpcId: props.privacyVpcId,
      vpcCidrBlock: props.privacyVpcCidr,
      availabilityZones: props.privacyAvailabilityZones,
      privateSubnetIds: props.privacySubnetIds,
    });
    const subnets = { subnets: vpc.privateSubnets };
    const relaySg = new ec2.SecurityGroup(this, 'RelaySg', {
      vpc, allowAllOutbound: false, description: 'MyData relay: only private gateway NLB TCP 8080',
    });
    const nlbSg = new ec2.SecurityGroup(this, 'NlbSg', {
      vpc, allowAllOutbound: false, description: 'MyData private NLB: relay SG only',
    });
    relaySg.addEgressRule(nlbSg, ec2.Port.tcp(8080), 'Relay to private gateway only');
    nlbSg.addIngressRule(relaySg, ec2.Port.tcp(8080), 'Only IAM relay may reach gateway');
    props.privacyTargetSecurityGroupIds.forEach((groupId, index) => {
      const target = ec2.SecurityGroup.fromSecurityGroupId(this, `TargetSg${index}`, groupId, {
        mutable: false,
      });
      nlbSg.addEgressRule(target, ec2.Port.tcp(8080), 'NLB to gateway targets and health checks');
      // CDK owns this new rule, not the existing group. TGB must not manage SG rules.
      new ec2.CfnSecurityGroupIngress(this, `GatewayTargetIngress${index}`, {
        groupId, sourceSecurityGroupId: nlbSg.securityGroupId,
        ipProtocol: 'tcp', fromPort: 8080, toPort: 8080,
        description: 'MyData NLB to dedicated gateway pod targets',
      });
    });

    const nlb = new elbv2.NetworkLoadBalancer(this, 'PrivacyNlb', {
      vpc, vpcSubnets: subnets, internetFacing: false,
      securityGroups: [nlbSg], crossZoneEnabled: true,
    });
    const targets = new elbv2.NetworkTargetGroup(this, 'PrivacyTargets', {
      vpc, port: 8080, protocol: elbv2.Protocol.TCP, targetType: elbv2.TargetType.IP,
      preserveClientIp: false, deregistrationDelay: cdk.Duration.seconds(150),
      healthCheck: {
        protocol: elbv2.Protocol.HTTP, path: '/health', port: '8080',
        healthyHttpCodes: '200', interval: cdk.Duration.seconds(15),
      },
    });
    nlb.addListener('PrivacyListener', {
      port: 8080, protocol: elbv2.Protocol.TCP, defaultTargetGroups: [targets],
    });
    const repository = new ecr.Repository(this, 'GatewayRepository', {
      repositoryName: 'bank-platform/mydata-privacy-gateway',
      imageScanOnPush: true, imageTagMutability: ecr.TagMutability.IMMUTABLE,
      encryption: ecr.RepositoryEncryption.AES_256,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const functionName = 'bank-platform-mydata-privacy-relay';
    const functionArn = this.formatArn({
      service: 'lambda', resource: 'function', resourceName: functionName,
      arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });
    const logGroup = new logs.LogGroup(this, 'RelayLogs', {
      logGroupName: `/aws/lambda/${functionName}`,
      retention: logs.RetentionDays.ONE_WEEK, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const role = new iam.Role(this, 'RelayRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Private MyData relay; no model, PII-store or other Lambda access',
    });
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`${logGroup.logGroupArn}:*`],
    }));
    const regionCondition = { StringEquals: { 'aws:RequestedRegion': this.region } };
    const ec2Arn = (resource: string, resourceName: string) =>
      this.formatArn({ service: 'ec2', resource, resourceName,
        arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME });
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ec2:CreateNetworkInterface'],
      resources: [
        ec2Arn('network-interface', '*'),
        ...props.privacySubnetIds.map(id => ec2Arn('subnet', id)),
        ec2Arn('security-group', relaySg.securityGroupId),
      ],
      conditions: regionCondition,
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ec2:DeleteNetworkInterface', 'ec2:AssignPrivateIpAddresses', 'ec2:UnassignPrivateIpAddresses'],
      resources: [ec2Arn('network-interface', '*')],
      conditions: {
        ...regionCondition,
        ArnEquals: { 'ec2:Vpc': ec2Arn('vpc', props.privacyVpcId) },
      },
    }));
    // EC2 Describe* does not support resource-level permissions. This is the
    // required VPCAccess discovery portion, constrained to the deployment region.
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ec2:DescribeNetworkInterfaces', 'ec2:DescribeSubnets'],
      resources: ['*'], conditions: regionCondition,
    }));
    // Lambda's service may manage Hyperplane ENIs; the handler itself may not.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.DENY,
      actions: ['ec2:CreateNetworkInterface', 'ec2:DeleteNetworkInterface',
        'ec2:DescribeNetworkInterfaces', 'ec2:DescribeSubnets',
        'ec2:AssignPrivateIpAddresses', 'ec2:UnassignPrivateIpAddresses'],
      resources: ['*'], conditions: { ArnEquals: { 'lambda:SourceFunctionArn': functionArn } },
    }));
    const relay = new lambda.Function(this, 'Relay', {
      functionName, runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../privacy/relay'), {
        exclude: ['__pycache__', '*.pyc', '.pytest_cache'],
      }),
      // Prevent Function from adding unconditioned AWS managed VPC/basic policies.
      // All required grants are explicitly owned above.
      role: role.withoutPolicyUpdates(),
      logGroup, tracing: lambda.Tracing.DISABLED,
      memorySize: 256, timeout: cdk.Duration.seconds(150),
      reservedConcurrentExecutions: 4,
      vpc, vpcSubnets: subnets, securityGroups: [relaySg],
      environment: {
        PRIVACY_GATEWAY_URL: `http://${nlb.loadBalancerDnsName}:8080`,
        PRIVACY_VPC_CIDR: props.privacyVpcCidr,
      },
      description: 'IAM-only MyData privacy relay to fixed private EKS CPU gateway',
    });
    relay.node.addDependency(role);
    // No Function URL, resource-policy wildcard, cross-stack export, or Docker asset.
    for (const [name, value] of Object.entries({
      PrivacyFunctionArn: relay.functionArn,
      PrivacyFunctionName: relay.functionName,
      PrivacyTargetGroupArn: targets.targetGroupArn,
      PrivacyNlbDns: nlb.loadBalancerDnsName,
      PrivacyNlbArn: nlb.loadBalancerArn,
      PrivacyNlbSecurityGroupId: nlbSg.securityGroupId,
      PrivacyRelaySecurityGroupId: relaySg.securityGroupId,
      PrivacyGatewayRepositoryUri: repository.repositoryUri,
      PrivacyVpcId: props.privacyVpcId,
      PrivacyVpcCidr: props.privacyVpcCidr,
    })) new cdk.CfnOutput(this, name, { value });
  }
}
