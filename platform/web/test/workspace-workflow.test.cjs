const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { buildSync } = require('esbuild');

function load(name, extension = 'ts') {
  const filename = path.join(__dirname, '../src/workspace/', name + '.' + extension);
  const result = buildSync({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'] });
  const compiled = new Module(filename);
  compiled.paths = Module._nodeModulePaths(path.dirname(filename));
  compiled._compile(result.outputFiles[0].text, filename);
  return compiled.exports;
}

test('workflow links retain exact scoped artifacts and reject malformed identities instead of selecting another workspace', () => {
  const { readWorkflowRoute, workflowHash, initialWorkflowStep } = load('workflow');
  const value = readWorkflowRoute('#/studio?projectId=p&productId=product&step=handoff&runId=run&round=2');
  assert.equal(value.projectId, 'p'); assert.equal(value.productId, 'product'); assert.equal(value.round, 2);
  assert.equal(initialWorkflowStep(value, 'designer'), 'handoff');
  assert.equal(initialWorkflowStep(readWorkflowRoute('#/studio?runId=run'), 'developer'), 'review');
  assert.equal(initialWorkflowStep(readWorkflowRoute('#/studio'), 'developer'), 'handoff');
  for (const hash of ['#/studio?projectId=../other', '#/studio?projectId=p%0Aother', '#/studio?runId=bad/id', '#/studio?round=0'])
    assert.equal(readWorkflowRoute(hash).invalid, true);
  const changed = workflowHash('#/studio?projectId=old&runId=private-old&round=2&productId=old',
    { projectId: 'new', step: 'define' });
  assert.equal(changed, '#/studio?projectId=new&step=define');
  assert.equal(workflowHash('#/portal?component=Button', { projectId: 'p', step: 'assets' }),
    '#/portal?tab=guides&projectId=p&step=assets');
});

test('state results do not pass missing, duplicated or malformed evidence', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { StateEvidence } = load('StatePlan', 'tsx');
  const contract = { requiredStates: ['error'], rules: [{ id: 'error-case', scenario: 'error', required: true }] };
  for (const checks of [undefined, [], [null], [{ caseId: 'error-case', status: 'pass' }, { caseId: 'error-case', status: 'pass' }]]) {
    const html = renderToStaticMarkup(React.createElement(StateEvidence, { contract, evidence: { checks } }));
    assert.match(html, /미판정/);
    assert.doesNotMatch(html, />통과</);
  }
  assert.match(renderToStaticMarkup(React.createElement(StateEvidence, {
    contract, evidence: { checks: [{ caseId: 'error-case', status: 'fail' }] },
  })), /실패/);
});

test('required state coverage needs required assertions and is preserved in editable contracts', () => {
  const { stateCoverageIssues } = load('workflow');
  const { editable, newRule } = load('rules');
  const rule = { ...newRule(), scenario: 'error' };
  const contract = { title: '회복 흐름', brief: '정정할 수 있어야 합니다.', assetIds: [], viewport: { width: 390, height: 844 },
    rules: [rule], unresolved: [], requiredStates: ['error', 'back'] };
  assert.deepEqual(stateCoverageIssues(contract), ['이전·재진입 상태에 연결된 필수 검증 규칙을 작성하세요.']);
  assert.equal(stateCoverageIssues({ ...contract, rules: [{ ...rule, required: false }] }).length, 2);
  assert.equal(stateCoverageIssues({ ...contract, rules: [{ ...rule, steps: [{ action: 'click', target: 'submit' }] }] }).length, 2);
  const copy = editable(contract);
  copy.requiredStates.pop();
  assert.deepEqual(contract.requiredStates, ['error', 'back']);
  assert.equal(copy.rules[0].scenario, 'error');
  assert.deepEqual(stateCoverageIssues({ rules: [], requiredStates: [] }), []);
});
