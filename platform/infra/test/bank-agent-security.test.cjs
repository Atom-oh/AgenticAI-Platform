require('ts-node/register');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const cdk = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { BankPlatformStack } = require('../lib/stack');

test('bank user path cannot administer AgentCore or pass its execution role', () => {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'bank-security-cdk-'));
  try {
  const stack = new BankPlatformStack(new cdk.App({ outdir }), 'BankSecurityTest', {
    env: { account: '111122223333', region: 'ap-northeast-2' }, planeDeployed: false, graphBackend: 'local',
    cognitoUserPoolId: 'ap-northeast-2_synthetic', cognitoClientId: 'synthetic-client',
  });
  const resources = Template.fromStack(stack).toJSON().Resources;
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
  } finally {
    fs.rmSync(outdir, { recursive: true, force: true });
  }
});
