const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { buildSync } = require('esbuild');

const filename = path.join(__dirname, '../src/workspace/VerificationLoop.tsx');
function load() {
  const result = buildSync({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs' });
  const compiled = new Module(filename);
  compiled._compile(result.outputFiles[0].text, filename);
  return compiled.exports;
}
function fixture() {
  const round = { number: 2, artifactSha256: 'a'.repeat(64), passed: true, hasHtml: true, hasReport: true, hasScreenshot: true,
    functionalStatus: 'pass', visualStatus: 'not-run', checks: { pass: 1, fail: 0, incomplete: 0 }, blockingFindings: [] };
  const run = { id: 'run', mode: 'generate', status: 'completed', contractId: 'contract', contractVersion: 3, contractHash: 'b'.repeat(64),
    contract: { rules: [{ id: 'R1', required: true }] }, bestRound: 2, rounds: [round] };
  const evidence = { artifactSha256: round.artifactSha256, contractVersion: 3, contractHash: run.contractHash, passed: true,
    functionalStatus: 'pass', accessibility: { status: 'pass' }, visual: { status: 'not-run' },
    checks: [{ caseId: 'R1', required: true, status: 'pass' }], blockingFindings: [], networkRequests: [], consoleErrors: [] };
  return { run, round, evidence };
}
const stage = (state, id) => state.nodes.find(node => node.id === id);

test('personal React approval accepts an absent or null product revision without weakening hashes', () => {
  const { run, round } = fixture();
  Object.assign(run, { outputType: 'react', catalogHash: 'c'.repeat(64) });
  Object.assign(round, { sourceHash: 'd'.repeat(64), bundleHash: 'e'.repeat(64) });
  run.approval = { round: round.number, artifactSha256: round.artifactSha256, contractVersion: run.contractVersion,
    contractHash: run.contractHash, catalogHash: run.catalogHash, sourceHash: round.sourceHash, bundleHash: round.bundleHash,
    guidelineId: null, actor: 'designer', at: 1 };
  assert.equal(load().hasExactApproval(run, round), true);
  run.approval.sourceHash = 'f'.repeat(64);
  assert.equal(load().hasExactApproval(run, round), false);
});

test('loading matching evidence does not imply human approval or loop completion', () => {
  const state = load().deriveVerificationLoop(fixture());
  assert.equal(stage(state, 'browser').state, 'passed');
  assert.equal(stage(state, 'evidence').state, 'recorded');
  assert.equal(stage(state, 'approval').state, 'pending');
  assert.equal(state.complete, false);
});

test('React loop fails closed when browser pass contradicts missing or failed build evidence', () => {
  const fixtureData = fixture();
  fixtureData.run.outputType = 'react'; fixtureData.run.catalogHash = 'c'.repeat(64);
  assert.equal(stage(load().deriveVerificationLoop(fixtureData), 'browser').state, 'incomplete');
  Object.assign(fixtureData.round, { sourceHash: 'd'.repeat(64), bundleHash: 'e'.repeat(64), catalogHash: fixtureData.run.catalogHash,
    gates: Object.fromEntries(['policy', 'types', 'build', 'components'].map(key => [key, { status: 'pass' }])) });
  Object.assign(fixtureData.evidence, { sourceHash: fixtureData.round.sourceHash, bundleHash: fixtureData.round.bundleHash,
    catalogHash: fixtureData.round.catalogHash, build: { ...fixtureData.round, ok: true } });
  assert.equal(stage(load().deriveVerificationLoop(fixtureData), 'browser').state, 'passed');
  fixtureData.round.gates.types.status = 'fail';
  assert.equal(stage(load().deriveVerificationLoop(fixtureData), 'browser').state, 'incomplete');
});
test('review-required variations need exact source approval and explicit acceptance, without becoming pixel passes', () => {
  const { deriveVerificationLoop, hasExactApproval } = load();
  const value = fixture();
  const hashes = { sourceHash: 'c'.repeat(64), bundleHash: 'd'.repeat(64), catalogHash: 'e'.repeat(64) };
  const build = { ...hashes, ok: true, gates: Object.fromEntries(['policy', 'types', 'build', 'components'].map(key => [key, { status: 'pass' }])) };
  Object.assign(value.round, hashes, { build, visualStatus: 'review-required' });
  Object.assign(value.run, { outputType: 'react', catalogHash: hashes.catalogHash, visualPolicy: 'variation-review', referenceAssetId: 'image' });
  Object.assign(value.evidence, hashes, { build, visualPolicy: 'variation-review', visual: { status: 'review-required', comparisonStatus: 'fail' } });
  let state = deriveVerificationLoop(value);
  assert.equal(stage(state, 'browser').state, 'review');
  assert.equal(state.complete, false);
  assert.equal(state.retry, false);
  const approval = { round: 2, artifactSha256: value.round.artifactSha256, contractVersion: 3, contractHash: value.run.contractHash,
    actor: 'reviewer', at: 123456, ...hashes };
  value.run.approval = approval;
  assert.equal(hasExactApproval(value.run, value.round), false);
  value.run.approval = { ...approval, acceptedVariation: true };
  assert.equal(deriveVerificationLoop(value).complete, true);
  value.run.approval.sourceHash = 'f'.repeat(64);
  assert.equal(hasExactApproval(value.run, value.round), false);
  assert.equal(deriveVerificationLoop({ ...value, run: { ...value.run, visualPolicy: 'exact' } }).complete, false);
  value.evidence.networkRequests = ['https://outside.invalid'];
  assert.equal(stage(deriveVerificationLoop(value), 'browser').state, 'failed');
});

test('approval must match the selected round, artifact, contract version and contract hash', () => {
  const { deriveVerificationLoop } = load();
  const base = fixture();
  const approval = { round: 2, artifactSha256: base.round.artifactSha256, contractVersion: 3,
    contractHash: base.run.contractHash, actor: 'fixture-reviewer', at: 123456 };
  assert.equal(deriveVerificationLoop({ ...base, run: { ...base.run, approval } }).complete, true);
  for (const patch of [{ round: 1 }, { artifactSha256: 'c'.repeat(64) }, { contractVersion: 2 }, { contractHash: 'd'.repeat(64) }]) {
    const state = deriveVerificationLoop({ ...base, run: { ...base.run, approval: { ...approval, ...patch } } });
    assert.equal(stage(state, 'approval').state, 'pending');
    assert.equal(state.complete, false);
  }
});

test('failed and incomplete selected rounds return to the same frozen rule version', () => {
  const { deriveVerificationLoop } = load();
  for (const status of ['fail', 'incomplete']) {
    const base = fixture();
    const round = { ...base.round, passed: false, functionalStatus: status,
      checks: { pass: 0, fail: status === 'fail' ? 1 : 0, incomplete: status === 'incomplete' ? 1 : 0 } };
    const state = deriveVerificationLoop({ ...base, round, run: { ...base.run, rounds: [round] },
      evidence: { ...base.evidence, passed: false, functionalStatus: status, checks: [{ caseId: 'R1', required: true, status }] } });
    assert.equal(state.retry, true);
    assert.equal(state.contractVersion, 3);
    assert.equal(state.contractHash, base.run.contractHash);
    assert.equal(stage(state, 'browser').state, status === 'fail' ? 'failed' : 'incomplete');
  }
});

test('another round or mismatching report cannot complete browser verification', () => {
  const { deriveVerificationLoop } = load();
  const base = fixture();
  for (const evidence of [{ ...base.evidence, artifactSha256: 'c'.repeat(64) },
    { ...base.evidence, contractVersion: 4 }, { ...base.evidence, contractHash: 'd'.repeat(64) }]) {
    const state = deriveVerificationLoop({ ...base, evidence });
    assert.notEqual(stage(state, 'browser').state, 'passed');
    assert.equal(stage(state, 'evidence').state, 'incomplete');
    assert.equal(state.complete, false);
  }
});

test('queued or running without selected round never invents the current asynchronous phase', () => {
  const { deriveVerificationLoop } = load();
  const base = fixture();
  for (const status of ['queued', 'running']) {
    const state = deriveVerificationLoop({ run: { ...base.run, status, rounds: [] } });
    assert.equal(stage(state, 'artifact').state, 'pending');
    assert.equal(stage(state, 'browser').state, 'pending');
    assert.equal(stage(state, 'approval').state, 'pending');
    assert.equal(state.nodes.some(node => node.state === 'running' || node.current), false);
  }
});

test('a bare pass with missing rule or required visual evidence cannot complete verification', () => {
  const { deriveVerificationLoop } = load();
  const base = fixture();
  for (const checks of [[], [{ caseId: 'other', status: 'pass' }], [{ caseId: 'R1', status: 'incomplete' }]]) {
    const state = deriveVerificationLoop({ ...base, evidence: { ...base.evidence, checks } });
    assert.notEqual(stage(state, 'browser').state, 'passed');
    assert.equal(state.retry, true);
  }
  assert.notEqual(stage(deriveVerificationLoop({ ...base, run: { ...base.run, referenceAssetId: 'reference' } }), 'browser').state, 'passed');
});

test('original HTML and generated artifacts have distinct preparation labels', () => {
  const { deriveVerificationLoop } = load();
  const base = fixture();
  assert.match(stage(deriveVerificationLoop(base), 'artifact').detail, /AI 생성/);
  assert.match(stage(deriveVerificationLoop({ ...base, run: { ...base.run, mode: 'verify' } }), 'artifact').detail, /반입 HTML/);
});

test('network and console evidence must both be present as empty arrays to pass', () => {
  const { deriveVerificationLoop } = load();
  const base = fixture();
  for (const field of ['networkRequests', 'consoleErrors']) {
    for (const value of [undefined, null, '', {}]) {
      const state = deriveVerificationLoop({ ...base, evidence: { ...base.evidence, [field]: value } });
      assert.equal(stage(state, 'browser').state, 'incomplete');
      assert.equal(state.complete, false);
    }
    const state = deriveVerificationLoop({ ...base, evidence: { ...base.evidence, [field]: ['recorded violation'] } });
    assert.equal(stage(state, 'browser').state, 'failed');
    assert.equal(state.retry, true);
    assert.equal(state.complete, false);
  }
});
