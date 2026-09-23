/* Offline synth and regression checks. No deploy, lookup, Docker build or install. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const { assertMainPrivacyDelta, assertReviewedMainChanges } = require('./check-main-privacy-delta.cjs');
const { loadBaselineModule } = require('./load-baseline.cjs');
const root = path.resolve(__dirname, '../..');
const infra = path.join(root, 'infra');
// Existing stack assets are relative to the CDK project working directory.
process.chdir(infra);
process.env.TS_NODE_PROJECT = path.join(infra, 'tsconfig.json');
require(path.join(infra, 'node_modules/ts-node/register'));
const cdk = require(path.join(infra, 'node_modules/aws-cdk-lib'));
const { Template } = require(path.join(infra, 'node_modules/aws-cdk-lib/assertions'));
const ts = require(path.join(infra, 'node_modules/typescript'));
const { BankPlatformPrivacyStack } = require(path.join(infra, 'lib/privacy-stack.ts'));
const { BankPlatformStack } = require(path.join(infra, 'lib/stack.ts'));
const env = { account: '180294183052', region: 'ap-northeast-2' };
const out = path.join(infra, 'cdk.out/privacy-check');
const app = name => new cdk.App({
  autoSynth: false, outdir: path.join(out, name),
  context: { 'aws:cdk:asset-staging': false },
});
const scope = app('privacy');
const privacyProps = {
  env, privacyVpcId: 'vpc-04e77172c67f19814', privacyVpcCidr: '10.0.0.0/16',
  privacySubnetIds: ['subnet-0381e6c41375cbc53', 'subnet-037c396f41efedba8'],
  privacySubnetRouteTableIds: ['rtb-0d3f6ff29f519ebe1', 'rtb-075c2e281c3624165'],
  privacyAvailabilityZones: ['ap-northeast-2a', 'ap-northeast-2b'],
  privacyTargetSecurityGroupIds: ['sg-0143b07a17cc02c27'],
};
const stack = new BankPlatformPrivacyStack(scope, 'BankPlatformPrivacy', privacyProps);
for (const cidr of ['10.999.0.0/16', '10.0.0.1/16', '10.0.0.0/29', '10.0.0.0/8']) {
  assert.throws(() => new BankPlatformPrivacyStack(app(`invalid-${cidr.replaceAll('/', '-')}`),
    'InvalidPrivacy', { ...privacyProps, privacyVpcCidr: cidr }), /explicit private VPC/);
}
assert.throws(() => new BankPlatformPrivacyStack(app('duplicate-subnets'), 'InvalidPrivacy', {
  ...privacyProps, privacySubnetIds: [privacyProps.privacySubnetIds[0], privacyProps.privacySubnetIds[0]],
}), /explicit private VPC/);
for (const routeTables of [[], ['rtb-0d3f6ff29f519ebe1'], ['bad', 'rtb-075c2e281c3624165']]) {
  assert.throws(() => new BankPlatformPrivacyStack(app(`invalid-routes-${routeTables.length}`),
    'InvalidPrivacy', { ...privacyProps, privacySubnetRouteTableIds: routeTables }), /explicit private VPC/);
}
const template = Template.fromStack(stack);
template.resourceCountIs('AWS::Lambda::Function', 1);
template.resourceCountIs('AWS::Lambda::Url', 0);
template.resourceCountIs('AWS::EKS::Cluster', 0);
template.resourceCountIs('AWS::EC2::VPC', 0);
template.resourceCountIs('AWS::EC2::VPCPeeringConnection', 0);
template.resourceCountIs('AWS::ECR::Repository', 1);
template.resourceCountIs('AWS::EC2::SecurityGroup', 2);
template.resourceCountIs('AWS::EC2::SecurityGroupEgress', 2);
template.resourceCountIs('AWS::Lambda::Permission', 0);
template.hasResourceProperties('AWS::ElasticLoadBalancingV2::LoadBalancer', {
  Scheme: 'internal', Type: 'network',
});
template.hasResourceProperties('AWS::ElasticLoadBalancingV2::TargetGroup', {
  Port: 8080, Protocol: 'TCP', TargetType: 'ip', HealthCheckPath: '/health',
  HealthCheckProtocol: 'HTTP', Matcher: { HttpCode: '200' },
});
template.hasResourceProperties('AWS::Lambda::Function', {
  Timeout: 150, ReservedConcurrentExecutions: 4,
  FunctionName: 'bank-platform-mydata-privacy-relay',
  Handler: 'handler.handler', Runtime: 'python3.12', Architectures: ['arm64'],
  VpcConfig: { SubnetIds: privacyProps.privacySubnetIds },
});
const json = template.toJSON();
const ec2Actions = ['ec2:CreateNetworkInterface', 'ec2:DescribeNetworkInterfaces',
  'ec2:DescribeSubnets', 'ec2:DeleteNetworkInterface',
  'ec2:AssignPrivateIpAddresses', 'ec2:UnassignPrivateIpAddresses'].sort();
const statements = Object.values(json.Resources)
  .filter(r => r.Type === 'AWS::IAM::Policy').flatMap(r => r.Properties.PolicyDocument.Statement);
const eniAllow = statements.find(s => s.Effect === 'Allow'
  && [].concat(s.Action).includes('ec2:DeleteNetworkInterface'));
assert.deepEqual([].concat(eniAllow.Action).sort(), ec2Actions);
assert.equal(eniAllow.Resource, '*', 'Lambda VPC preflight requires all-resource ENI grants');
assert.deepEqual(eniAllow.Condition, { StringEquals: { 'aws:RequestedRegion': env.region } });
const handlerDeny = statements.find(s => s.Effect === 'Deny'
  && [].concat(s.Action).includes('ec2:DeleteNetworkInterface'));
assert.deepEqual([].concat(handlerDeny.Action).sort(), ec2Actions);
assert.ok(handlerDeny.Condition.ArnEquals['lambda:SourceFunctionArn'],
  'Handler code must remain denied all six EC2 operations');
const resourceId = type => Object.keys(json.Resources).find(id => json.Resources[id].Type === type);
const relayId = resourceId('AWS::Lambda::Function');
const relayPolicyId = resourceId('AWS::IAM::Policy');
const relaySgId = json.Resources[relayId].Properties.VpcConfig.SecurityGroupIds[0]['Fn::GetAtt'][0];
const nlbId = resourceId('AWS::ElasticLoadBalancingV2::LoadBalancer');
const nlbSgId = json.Resources[nlbId].Properties.SecurityGroups[0]['Fn::GetAtt'][0];
const sgRef = id => ({ 'Fn::GetAtt': [id, 'GroupId'] });
template.hasResourceProperties('AWS::EC2::SecurityGroupEgress', {
  GroupId: sgRef(relaySgId), DestinationSecurityGroupId: sgRef(nlbSgId),
  IpProtocol: 'tcp', FromPort: 8080, ToPort: 8080,
});
template.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
  GroupId: sgRef(nlbSgId), SourceSecurityGroupId: sgRef(relaySgId),
  IpProtocol: 'tcp', FromPort: 8080, ToPort: 8080,
});
template.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
  GroupId: privacyProps.privacyTargetSecurityGroupIds[0], SourceSecurityGroupId: sgRef(nlbSgId),
});
assert.ok(json.Resources[relayId].DependsOn.includes(relayPolicyId), 'VPC role policy must exist before relay');
const attrs = json.Resources[resourceId('AWS::ElasticLoadBalancingV2::TargetGroup')].Properties.TargetGroupAttributes;
assert.ok(attrs.some(a => a.Key === 'preserve_client_ip.enabled' && a.Value === 'false'),
  'gateway NetworkPolicy expects NLB source IPs');
for (const resource of Object.values(json.Resources)) {
  const p = resource.Properties || {};
  if (resource.Type === 'AWS::IAM::Role') assert.equal(p.ManagedPolicyArns, undefined);
  if (resource.Type === 'AWS::IAM::Policy') {
    for (const statement of p.PolicyDocument.Statement) {
      const resources = [].concat(statement.Resource || []);
      if (resources.includes('*')) assert.ok(statement.Condition, 'unconditioned wildcard resource');
      if (statement.Effect === 'Allow') {
        const actions = [].concat(statement.Action);
        assert.ok(actions.every(a => a.startsWith('ec2:') || a.startsWith('logs:')),
          'relay gained unrelated service permissions');
      }
    }
  }
  if (resource.Type === 'AWS::EC2::SecurityGroupIngress') {
    assert.ok(p.SourceSecurityGroupId);
    assert.equal(p.CidrIp, undefined);
    assert.equal(p.FromPort, 8080);
    assert.equal(p.ToPort, 8080);
  }
  if (resource.Type === 'AWS::EC2::SecurityGroupEgress') {
    assert.ok(p.DestinationSecurityGroupId, 'unexpected CIDR egress');
    assert.equal(p.CidrIp, undefined);
    assert.equal(p.CidrIpv6, undefined);
    assert.equal(p.IpProtocol, 'tcp');
    assert.equal(p.FromPort, 8080);
    assert.equal(p.ToPort, 8080);
  }
  if (resource.Type === 'AWS::EC2::SecurityGroup') {
    for (const ingress of p.SecurityGroupIngress || []) {
      assert.ok(ingress.SourceSecurityGroupId);
      assert.equal(ingress.FromPort, 8080);
      assert.equal(ingress.ToPort, 8080);
    }
    for (const egress of p.SecurityGroupEgress || []) {
      assert.ok(egress.DestinationSecurityGroupId, 'unexpected CIDR egress');
      assert.equal(egress.FromPort, 8080);
      assert.equal(egress.ToPort, 8080);
    }
  }
}
scope.synth();

// Verify stdin against the named revision and compile its entire relative source
// dependency closure, including workspace.ts, without reusing HEAD modules.
const repository = path.dirname(root);
const git = args => execFileSync('git', args, { cwd: repository, encoding: 'utf8' });
const baselineHead = process.env.PR_BASE_SHA || git(['rev-parse', 'HEAD']).trim();
assert.match(baselineHead, /^[0-9a-f]{40}$/);
const entry = 'platform/infra/lib/stack.ts';
const tracked = new Set(git(['ls-tree', '-r', '--name-only', baselineHead]).trim().split('\n'));
const baselineSources = new Map();
const sourceHashes = {};
const readSource = name => {
  if (!tracked.has(name)) return null;
  if (!baselineSources.has(name)) {
    const text = git(['show', `${baselineHead}:${name}`]);
    baselineSources.set(name, text);
    sourceHashes[name] = createHash('sha256').update(text).digest('hex');
  }
  return baselineSources.get(name);
};
const oldSource = fs.readFileSync(0, 'utf8');
assert.ok(oldSource.includes('export class BankPlatformStack'), 'PR-base stack source required on stdin');
assert.equal(oldSource, readSource(entry), 'stdin does not match PR_BASE_SHA (defaults to HEAD)');
for (const file of ['package.json', 'package-lock.json', 'tsconfig.json']) {
  const name = `platform/infra/${file}`;
  assert.equal(fs.readFileSync(path.join(repository, name), 'utf8'), readSource(name),
    'Baseline package/compiler configuration changed; independent dependency installation is required');
}
const oldExports = loadBaselineModule(entry, {
  root: repository, readSource,
  compile: text => ts.transpileModule(text, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText,
});
const arn = 'arn:aws:lambda:ap-northeast-2:180294183052:function:bank-platform-mydata-privacy-relay';
function mainTemplate(Stack, name, privacyArn) {
  return Template.fromStack(new Stack(app(name), 'BankPlatform', {
    env, planeDeployed: false, graphBackend: 'local',
    cognitoUserPoolId: 'ap-northeast-2_h2rhe1TKo', cognitoClientId: '3o8u65rhccnr1ug1f94tctmb0b',
    mydataPrivacyFunctionArn: privacyArn,
  })).toJSON();
}
const baseline = mainTemplate(oldExports.BankPlatformStack, 'baseline');
const baselineEnabled = mainTemplate(oldExports.BankPlatformStack, 'baseline-enabled', arn);
assertMainPrivacyDelta(baseline, baselineEnabled, arn);
const disabled = mainTemplate(BankPlatformStack, 'disabled');
const enabled = mainTemplate(BankPlatformStack, 'enabled', arn);
// Compare feature-on/off at the SAME revision. Independently reviewed IAM or
// application changes between revisions must not become privacy-toggle deltas.
const changed = assertMainPrivacyDelta(disabled, enabled, arn);
const mainIds = new Set([...Object.keys(baseline.Resources), ...Object.keys(disabled.Resources)]);
const reviewedMainChanges = [...mainIds].filter(id =>
  JSON.stringify(baseline.Resources[id]) !== JSON.stringify(disabled.Resources[id]));
const reviewedDelta = JSON.parse(fs.readFileSync(path.join(__dirname, 'reviewed-main-delta.json'), 'utf8'));
assertReviewedMainChanges(baselineHead, reviewedMainChanges, reviewedDelta);
console.log('Main-stack resources changed between revisions (review separately):', reviewedMainChanges.join(', '));
fs.writeFileSync(path.join(out, 'verified.json'), JSON.stringify({
  privacyResources: Object.keys(json.Resources).length,
  existingMainResources: Object.keys(disabled.Resources).length,
  baselinePrivacyDeltaVerified: true, currentPrivacyDeltaVerified: true,
  baselineHead, baselineSourceHashes: sourceHashes,
  reviewedDeltaManifestHash: createHash('sha256').update(JSON.stringify(reviewedDelta)).digest('hex'),
  assetFixture: 'shared current-checkout assets; source modules loaded from their named revision',
  separatelyReviewedMainChangedResources: reviewedMainChanges,
  enabledMainChangedResources: changed,
}, null, 2));
console.log('PASS private stack assertions; both revisions add only exact WS and Workspace API privacy env/policy when enabled.');
