require('ts-node/register');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const cdk = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { BankPlatformStack } = require('../lib/stack');

function checkUserPolicy(resources) {
  const fn = Object.entries(resources).find(([id, resource]) => id.startsWith('WsFn') && resource.Type === 'AWS::Lambda::Function');
  const roleId = fn[1].Properties.Role['Fn::GetAtt'][0];
  const role = resources[roleId];
  const statements = (role.Properties.Policies || []).flatMap(policy => policy.PolicyDocument.Statement);
  const attached = value => [].concat(value || []).some(item => item.Ref === roleId);
  for (const resource of Object.values(resources)) {
    if (['AWS::IAM::Policy', 'AWS::IAM::ManagedPolicy'].includes(resource.Type) && attached(resource.Properties.Roles))
      statements.push(...resource.Properties.PolicyDocument.Statement);
  }
  for (const policy of role.Properties.ManagedPolicyArns || []) {
    if (policy.Ref && resources[policy.Ref]?.Type === 'AWS::IAM::ManagedPolicy') {
      statements.push(...resources[policy.Ref].Properties.PolicyDocument.Statement);
    } else {
      assert(JSON.stringify(policy).includes(':iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'),
        'Every external managed policy must be explicitly reviewed');
    }
  }
  const allowed = new Set(['GetHarness', 'ListHarnesses', 'GetGateway', 'ListGatewayTargets',
    'GetGatewayTarget', 'GetRegistryRecord', 'ListRegistryRecords', 'InvokeHarness', 'InvokeAgentRuntime']);
  let harnessGrants = 0;
  for (const row of statements) {
    assert(!row.NotAction && !row.NotResource, 'Complement policies cannot establish least privilege');
    if (row.Effect === 'Deny') continue;
    assert.equal(row.Effect, 'Allow');
    for (const action of [].concat(row.Action)) {
      assert(action !== '*' && !action.startsWith('iam:'), 'No global action or IAM authority on WsFn');
      if (action.startsWith('bedrock-agentcore:'))
        assert(allowed.has(action.split(':')[1]), `Unreviewed AgentCore action ${action}`);
      if (action.startsWith('lambda:')) {
        assert.equal(action, 'lambda:InvokeFunction');
        assert(!JSON.stringify(row.Resource).includes('AdminFn'));
        assert(![].concat(row.Resource).some(resource => typeof resource === 'string' && resource.includes('*')));
      }
      if (action === 'bedrock-agentcore:InvokeHarness') {
        harnessGrants++;
        const resolved = [].concat(row.Resource).map(resource => typeof resource === 'string' ? resource :
          resource['Fn::Join'][1].map(part => part.Ref === 'AWS::Partition' ? 'aws' : part).join(resource['Fn::Join'][0]));
        assert.deepEqual(resolved, ['arn:aws:bedrock-agentcore:ap-northeast-2:111122223333:harness/bank_*']);
      }
      if (action === 'bedrock-agentcore:InvokeAgentRuntime') {
        assert(![].concat(row.Resource).includes('*'));
        assert(JSON.stringify(row.Resource).includes('AgentsRuntime'));
      }
    }
  }
  assert(harnessGrants > 0);
  return roleId;
}

test('bank user path cannot administer AgentCore or pass its execution role', () => {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'bank-security-cdk-'));
  try {
  const stack = new BankPlatformStack(new cdk.App({ outdir }), 'BankSecurityTest', {
    env: { account: '111122223333', region: 'ap-northeast-2' }, planeDeployed: false, graphBackend: 'local',
    cognitoUserPoolId: 'ap-northeast-2_synthetic', cognitoClientId: 'synthetic-client',
  });
  const resources = Template.fromStack(stack).toJSON().Resources;
  const userRole = checkUserPolicy(resources);
  const statements = prefix => Object.entries(resources).filter(([id, resource]) =>
    id.startsWith(prefix) && resource.Type === 'AWS::IAM::Policy')
    .flatMap(([, resource]) => resource.Properties.PolicyDocument.Statement);
  const actions = rows => rows.flatMap(row => [].concat(row.Action));
  const user = statements('WsFn');
  assert(user.length);
  assert(!actions(user).some(action => /^bedrock-agentcore:(Create|Update|Delete|Submit|GetWorkloadAccessToken)/.test(action)));
  assert(!actions(user).includes('iam:PassRole'));
  assert(!JSON.stringify(user).includes('AdminFn'));
  const invoke = user.find(row => [].concat(row.Action).includes('bedrock-agentcore:InvokeHarness'));
  const resolved = [].concat(invoke.Resource).map(resource => typeof resource === 'string' ? resource :
    resource['Fn::Join'][1].map(part => part.Ref === 'AWS::Partition' ? 'aws' : part).join(resource['Fn::Join'][0]));
  assert.deepEqual(resolved, ['arn:aws:bedrock-agentcore:ap-northeast-2:111122223333:harness/bank_*']);
  const admin = actions(statements('AdminFn'));
  for (const action of ['iam:PassRole', 'bedrock-agentcore:DeleteHarness', 'bedrock-agentcore:UpdateRegistryRecordStatus'])
    assert(admin.includes(action));
  const runtime = statements('AgentsRuntimeRole');
  assert(runtime.length, 'Prepare the agent context so the Runtime is included in this test');
  assert(!actions(runtime).some(action => action.startsWith('bedrock-agentcore:GetWorkloadAccessToken')));
  const gateway = Object.values(resources).find(resource => resource.Type === 'AWS::BedrockAgentCore::Gateway');
  assert.equal(gateway.Properties.AuthorizerType, 'AWS_IAM');
  assert.equal(gateway.Properties.ExceptionLevel, undefined);
  for (const statement of [
    { Effect: 'Allow', Action: 'bedrock-agentcore:*', Resource: '*' },
    { Effect: 'Allow', Action: 'iam:*', Resource: '*' },
    { Effect: 'Allow', Action: 'bedrock-agentcore:InvokeHarness', Resource: '*' },
    { Effect: 'Allow', Action: 'lambda:InvokeFunction', Resource: '*' },
  ]) {
    const changed = structuredClone(resources);
    changed.UnrelatedLogicalName = { Type: 'AWS::IAM::Policy', Properties: {
      Roles: [{ Ref: userRole }], PolicyDocument: { Statement: [statement] },
    } };
    assert.throws(() => checkUserPolicy(changed));
  }
  } finally {
    fs.rmSync(outdir, { recursive: true, force: true });
  }
});
