/* Offline synth and regression checks. No deploy, lookup, Docker build or install. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
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

// Compile HEAD in memory, using the original directory for relative imports.
// No shared file is temporarily replaced while the parent works in this tree.
const sourcePath = path.join(infra, 'lib/stack.ts');
// Supply `git show HEAD:platform/infra/lib/stack.ts` on stdin. Keeping Git outside
// Node also works in runners which prohibit nested subprocesses.
const oldSource = fs.readFileSync(0, 'utf8');
assert.ok(oldSource.includes('export class BankPlatformStack'), 'HEAD stack source required on stdin');
const oldModule = new Module(sourcePath, module);
oldModule.filename = sourcePath;
oldModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
oldModule._compile(ts.transpileModule(oldSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText, sourcePath);
const arn = 'arn:aws:lambda:ap-northeast-2:180294183052:function:bank-platform-mydata-privacy-relay';
function mainTemplate(Stack, name, privacyArn) {
  return Template.fromStack(new Stack(app(name), 'BankPlatform', {
    env, planeDeployed: false, graphBackend: 'local',
    cognitoUserPoolId: 'ap-northeast-2_h2rhe1TKo', cognitoClientId: '3o8u65rhccnr1ug1f94tctmb0b',
    mydataPrivacyFunctionArn: privacyArn,
  })).toJSON();
}
const baseline = mainTemplate(oldModule.exports.BankPlatformStack, 'baseline');
const disabled = mainTemplate(BankPlatformStack, 'disabled');
assert.deepEqual(disabled, baseline, 'default main stack changed with privacy disabled');
const enabled = mainTemplate(BankPlatformStack, 'enabled', arn);
assert.deepEqual(Object.keys(enabled.Resources), Object.keys(baseline.Resources),
  'existing main stack logical IDs changed');
const changed = Object.keys(baseline.Resources).filter(
  id => JSON.stringify(enabled.Resources[id]) !== JSON.stringify(baseline.Resources[id]));
assert.equal(changed.length, 2, `unexpected changed resources: ${changed}`);
assert.ok(changed.some(id => /^WsFn[A-F0-9]+$/.test(id)));
assert.ok(changed.some(id => /^WsFnServiceRoleDefaultPolicy/.test(id)));
const fn = enabled.Resources[changed.find(id => /^WsFn[A-F0-9]+$/.test(id))];
assert.equal(fn.Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN, arn);
const policy = enabled.Resources[changed.find(id => /^WsFnServiceRoleDefaultPolicy/.test(id))];
assert.ok(policy.Properties.PolicyDocument.Statement.some(s =>
  s.Action === 'lambda:InvokeFunction' && s.Resource === arn));
const restored = structuredClone(enabled);
delete restored.Resources[changed.find(id => /^WsFn[A-F0-9]+$/.test(id))]
  .Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN;
const restoredPolicy = restored.Resources[changed.find(id => /^WsFnServiceRoleDefaultPolicy/.test(id))]
  .Properties.PolicyDocument;
restoredPolicy.Statement = restoredPolicy.Statement.filter(s =>
  !(s.Action === 'lambda:InvokeFunction' && s.Resource === arn));
assert.deepEqual(restored, baseline, 'enabled main stack changed beyond exact privacy env/invoke grant');
fs.writeFileSync(path.join(out, 'verified.json'), JSON.stringify({
  privacyResources: Object.keys(json.Resources).length,
  existingMainResources: Object.keys(baseline.Resources).length,
  defaultMainUnchanged: true, enabledMainChangedResources: changed,
}, null, 2));
console.log('PASS private stack security assertions; default main unchanged; enabled main changes only WS env/policy.');
