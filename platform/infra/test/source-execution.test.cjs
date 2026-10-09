require('ts-node/register');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const cdk = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { BankPlatformStack } = require('../lib/stack');

function synth(context) {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'source-opt-in-'));
  try {
    return Template.fromStack(new BankPlatformStack(new cdk.App({ outdir, context }), 'SourceOptIn', {
      env: { account: '111122223333', region: 'ap-northeast-2' }, planeDeployed: false, graphBackend: 'local',
      cognitoUserPoolId: 'ap-northeast-2_synthetic', cognitoClientId: 'synthetic-client',
    })).toJSON().Resources;
  } finally { fs.rmSync(outdir, { recursive: true, force: true }); }
}
const queueArn = 'arn:aws:sqs:ap-northeast-2:111122223333:source-dispatch';
const configuration = { protocol: 'platform-execution/1', revision: 'synthetic' };

test('source execution is absent by default and opt-in grants API queue send only', () => {
  const baseline = synth({});
  assert(!JSON.stringify(baseline).includes('SOURCE_EXECUTION_CONFIGURATION'));
  const resources = synth({ intakeDeployment: 'SourceOptIn', sourceExecution: { queueArn, configuration } });
  const [, api] = Object.entries(resources).find(([id, row]) => row.Type === 'AWS::Lambda::Function' && row.Properties.Handler === 'workspace.http.handler');
  const role = api.Properties.Role['Fn::GetAtt'][0];
  const policies = Object.values(resources).filter(row => row.Type === 'AWS::IAM::Policy' && row.Properties.Roles.some(item => item.Ref === role));
  const statements = policies.flatMap(row => row.Properties.PolicyDocument.Statement);
  const actions = statements.filter(row => row.Effect === 'Allow').flatMap(row => [].concat(row.Action));
  assert(actions.includes('sqs:SendMessage'));
  assert(!actions.includes('kms:Sign'));
  assert(!actions.includes('bedrock-agentcore:InvokeAgentRuntime'));
  assert(!actions.includes('sqs:ReceiveMessage'));
  assert.equal(JSON.parse(api.Properties.Environment.Variables.SOURCE_EXECUTION_CONFIGURATION).protocol, 'platform-execution/1');
});

test('source execution refuses foreign queues and missing intake scope', () => {
  assert.throws(() => synth({ sourceExecution: { queueArn, configuration } }), /sourceExecution/);
  assert.throws(() => synth({ intakeDeployment: 'SourceOptIn', sourceExecution: {
    queueArn: queueArn.replace('111122223333', '444455556666'), configuration,
  } }), /sourceExecution/);
});
