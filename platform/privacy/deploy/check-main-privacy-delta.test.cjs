const { test } = require('node:test');
const assert = require('node:assert/strict');
const { assertMainPrivacyDelta } = require('./check-main-privacy-delta.cjs');

const arn = 'arn:aws:lambda:ap-northeast-2:000000000000:function:privacy-test';
const functionIds = ['WsFnABCD', 'DesignerWorkspaceApiABCD'];
const policyIds = ['WsFnServiceRoleDefaultPolicyABCD', 'DesignerWorkspaceApiRoleDefaultPolicyABCD'];

function fixture() {
  const baseline = { Resources: {} };
  for (const id of [...functionIds, 'DesignerWorkspaceWorkerABCD']) {
    baseline.Resources[id] = {
      Type: 'AWS::Lambda::Function',
      Properties: { Environment: { Variables: { EXISTING: 'retained' } } },
    };
  }
  for (const id of policyIds) {
    baseline.Resources[id] = {
      Type: 'AWS::IAM::Policy',
      Properties: { PolicyDocument: { Statement: [
        { Effect: 'Allow', Action: 'logs:PutLogEvents', Resource: 'existing-log-arn' },
      ] } },
    };
  }
  const enabled = structuredClone(baseline);
  for (const id of functionIds) {
    enabled.Resources[id].Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN = arn;
  }
  for (const id of policyIds) {
    enabled.Resources[id].Properties.PolicyDocument.Statement.push({
      Effect: 'Allow', Action: 'lambda:InvokeFunction', Resource: arn,
    });
  }
  return { baseline, enabled };
}

test('accepts exact privacy wiring for WebSocket and Workspace API consumers', () => {
  const { baseline, enabled } = fixture();
  assert.deepEqual(assertMainPrivacyDelta(baseline, enabled, arn).sort(),
    [...functionIds, ...policyIds].sort());
});

const mutations = {
  'missing workspace consumer': ({ baseline, enabled }) => {
    for (const id of [functionIds[1], policyIds[1]]) enabled.Resources[id] = structuredClone(baseline.Resources[id]);
  },
  'wrong workspace function ARN': ({ enabled }) => {
    enabled.Resources[functionIds[1]].Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN = arn + '-other';
  },
  'wildcard invocation resource': ({ enabled }) => {
    enabled.Resources[policyIds[1]].Properties.PolicyDocument.Statement.at(-1).Resource = '*';
  },
  'additional broad permission': ({ enabled }) => {
    enabled.Resources[policyIds[1]].Properties.PolicyDocument.Statement.push({
      Effect: 'Allow', Action: 'lambda:*', Resource: '*',
    });
  },
  'unrelated environment mutation': ({ enabled }) => {
    enabled.Resources[functionIds[1]].Properties.Environment.Variables.UNRELATED = 'changed';
  },
  'worker gains privacy configuration': ({ enabled }) => {
    enabled.Resources.DesignerWorkspaceWorkerABCD.Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN = arn;
  },
  'logical ID removal': ({ enabled }) => {
    delete enabled.Resources.DesignerWorkspaceWorkerABCD;
  },
};

for (const [name, mutate] of Object.entries(mutations)) {
  test(`rejects ${name}`, () => {
    const value = fixture();
    mutate(value);
    assert.throws(() => assertMainPrivacyDelta(value.baseline, value.enabled, arn), assert.AssertionError);
  });
}
