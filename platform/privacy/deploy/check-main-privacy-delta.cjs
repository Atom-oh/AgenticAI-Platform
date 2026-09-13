const assert = require('node:assert/strict');

function assertMainPrivacyDelta(baseline, enabled, arn) {
  const resources = baseline.Resources;
  assert.deepEqual(Object.keys(enabled.Resources).sort(), Object.keys(resources).sort(),
    'existing main stack logical IDs changed');
  const consumers = [
    { name: 'WebSocket', fn: /^WsFn[A-F0-9]+$/, policy: /^WsFnServiceRoleDefaultPolicy[A-F0-9]+$/ },
    { name: 'Workspace API', fn: /^DesignerWorkspaceApi[A-F0-9]+$/, policy: /^DesignerWorkspaceApiRoleDefaultPolicy[A-F0-9]+$/ },
  ];
  const identify = (pattern, type, description) => {
    const ids = Object.keys(resources).filter(id => pattern.test(id) && resources[id].Type === type);
    assert.equal(ids.length, 1, `expected exactly one ${description}`);
    return ids[0];
  };
  const expected = [];
  const restored = structuredClone(enabled);
  for (const consumer of consumers) {
    const fnId = identify(consumer.fn, 'AWS::Lambda::Function', `${consumer.name} function`);
    const policyId = identify(consumer.policy, 'AWS::IAM::Policy', `${consumer.name} policy`);
    expected.push(fnId, policyId);
    const fn = restored.Resources[fnId];
    assert.equal(fn.Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN, arn,
      `${consumer.name} must reference the exact privacy relay`);
    delete fn.Properties.Environment.Variables.MYDATA_PRIVACY_FUNCTION_ARN;
    const policy = restored.Resources[policyId].Properties.PolicyDocument;
    const grant = { Effect: 'Allow', Action: 'lambda:InvokeFunction', Resource: arn };
    const indices = policy.Statement.flatMap((statement, index) =>
      statement.Action === grant.Action && statement.Resource === arn ? [index] : []);
    assert.equal(indices.length, 1, `${consumer.name} must add exactly one relay invocation grant`);
    assert.deepEqual(policy.Statement[indices[0]], grant, `${consumer.name} relay grant changed`);
    policy.Statement.splice(indices[0], 1);
  }
  const changed = Object.keys(resources).filter(
    id => JSON.stringify(enabled.Resources[id]) !== JSON.stringify(resources[id]));
  assert.deepEqual(changed.slice().sort(), expected.sort(),
    `unexpected changed resources: ${changed}`);
  assert.deepEqual(restored, baseline,
    'enabled main stack changed beyond exact privacy env/invoke grants');
  return changed;
}

module.exports = { assertMainPrivacyDelta };
