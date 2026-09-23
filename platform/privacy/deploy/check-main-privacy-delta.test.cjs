const { test } = require('node:test');
const assert = require('node:assert/strict');
const { assertMainPrivacyDelta, assertReviewedMainChanges, resourceHash } = require('./check-main-privacy-delta.cjs');
const { loadBaselineModule } = require('./load-baseline.cjs');
const Module = require('node:module');
const path = require('node:path');

test('cross-revision drift requires the exact committed base and resource inventory', () => {
  const manifest = {base: 'a'.repeat(40), resources: ['WsPolicy', 'Gateway']};
  const resources = {WsPolicy: {Action: 'read-only'}, Gateway: {auth: 'IAM'}};
  manifest.resourceSha256 = Object.fromEntries(Object.entries(resources).map(([id, value]) => [id, resourceHash(value)]));
  assertReviewedMainChanges(manifest.base, ['Gateway', 'WsPolicy'], manifest, resources);
  assertReviewedMainChanges('b'.repeat(40), [], manifest, resources);
  assert.throws(() => assertReviewedMainChanges('b'.repeat(40), ['Gateway', 'WsPolicy'], manifest, resources));
  assert.throws(() => assertReviewedMainChanges(manifest.base, ['Gateway', 'WsPolicy', 'WorkspacePolicy'], manifest, resources));
  assert.throws(() => assertReviewedMainChanges(manifest.base, ['Gateway'], manifest, resources));
  resources.WsPolicy.Action = '*';
  assert.throws(() => assertReviewedMainChanges(manifest.base, ['Gateway', 'WsPolicy'], manifest, resources));
});

test('baseline compilation cannot reuse changed HEAD workspace policy code', () => {
  const root = path.resolve(__dirname, '../../../');
  const workspace = path.join(root, 'lib/workspace.ts');
  const previous = require.cache[workspace];
  const head = new Module(workspace);
  head.exports = { policy: 'HEAD_USER_ADMIN' };
  require.cache[workspace] = head;
  const sources = new Map([
    ['lib/stack.ts', "module.exports = {policy: require('./workspace').policy};"],
    ['lib/workspace.ts', "module.exports = {policy: require('./policy').value};"],
    ['lib/policy.ts', "module.exports = {value: 'BASE_ADMIN_ONLY'};"],
  ]);
  try {
    const result = loadBaselineModule('lib/stack.ts', {
      root, readSource: name => sources.get(name) ?? null, compile: text => text,
    });
    assert.equal(result.policy, 'BASE_ADMIN_ONLY');
    assert.equal(require.cache[workspace].exports.policy, 'HEAD_USER_ADMIN');
    sources.delete('lib/policy.ts');
    assert.throws(() => loadBaselineModule('lib/stack.ts', {
      root, readSource: name => sources.get(name) ?? null, compile: text => text,
    }), /Missing baseline dependency/);
  } finally {
    if (previous) require.cache[workspace] = previous;
    else delete require.cache[workspace];
  }
});

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

test('checks current privacy grants after an independent main-stack IAM change', () => {
  const before = fixture();
  const current = fixture();
  for (const template of [current.baseline, current.enabled]) {
    template.Resources[policyIds[0]].Properties.PolicyDocument.Statement.unshift({
      Effect: 'Allow', Action: 'bedrock-agentcore:InvokeHarness',
      Resource: 'arn:aws:bedrock-agentcore:ap-northeast-2:000000000000:harness/bank_*',
    });
  }
  assertMainPrivacyDelta(before.baseline, before.enabled, arn);
  assertMainPrivacyDelta(current.baseline, current.enabled, arn);
  assert.throws(() => assertMainPrivacyDelta(before.baseline, current.enabled, arn));
  current.enabled.Resources[policyIds[0]].Properties.PolicyDocument.Statement.at(-1).Resource = '*';
  assert.throws(() => assertMainPrivacyDelta(current.baseline, current.enabled, arn));
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
