const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { buildSync } = require('esbuild');
function load(name) {
  const filename = path.join(__dirname, '../src/workspace/', name + '.ts');
  const result = buildSync({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs' });
  const compiled = new Module(filename); compiled._compile(result.outputFiles[0].text, filename);
  return compiled.exports;
}
test('project clients retain their scope across overlapping requests and personal/root has no project header', async () => {
  const { createWorkspaceClient } = load('client');
  const calls = [];
  const options = { token: () => 'offline', fetcher: async (url, init) => {
    calls.push([url, init.headers.get('X-Workspace-Project')]); return Response.json({});
  } };
  const first = createWorkspaceClient({ ...options, projectId: 'project-a' });
  const second = createWorkspaceClient({ ...options, projectId: 'project-b' });
  const root = createWorkspaceClient(options);
  await Promise.all([first.get('/assets'), second.get('/runs'), root.get('/projects')]);
  await first.get('/jobs/first'); await root.get('/assets');
  assert.deepEqual(calls.map(call => call[1]), ['project-a', 'project-b', null, 'project-a', null]);
  assert.throws(() => createWorkspaceClient({ ...options, projectId: 'bad\r\nscope' }));
});
test('roles come from canonical project members and permissions fail closed', () => {
  const { roleFor, can } = load('project');
  const project = { id: 'p', members: { planner: { role: 'planner' }, dev: { role: 'developer' } } };
  assert.equal(roleFor(project, 'revoked'), null);
  assert.equal(roleFor(project, undefined), null);
  assert.equal(can(roleFor(project, 'planner'), 'publish'), true);
  assert.equal(can(roleFor(project, 'planner'), 'generate'), false);
  assert.equal(can(roleFor(project, 'dev'), 'export'), true);
  assert.equal(can(roleFor(project, 'dev'), 'approve'), false);
  assert.equal(can('designer', 'release'), true);
  assert.equal(can('designer', 'export'), false);
  assert.equal(can(null, 'upload'), false);
  assert.equal(can('owner', 'generate'), true);
});
test('React build gate requires every gate and real matching hashes', () => {
  const { buildPassed, generationRequest } = load('project');
  const hash = 'a'.repeat(64);
  const round = { sourceHash: hash, bundleHash: hash, catalogHash: hash,
    gates: Object.fromEntries(['policy', 'types', 'build', 'components'].map(key => [key, { status: 'pass' }])) };
  assert.equal(buildPassed(round, hash), true);
  for (const key of Object.keys(round.gates)) {
    const gates = { ...round.gates }; delete gates[key];
    assert.equal(buildPassed({ ...round, gates }, hash), false);
    assert.equal(buildPassed({ ...round, gates: { ...round.gates, [key]: { status: 'fail' } } }, hash), false);
  }
  assert.equal(buildPassed(round, 'b'.repeat(64)), false);
  assert.equal(buildPassed({}, hash), false);
  assert.equal(generationRequest('creative', 5).variationCount, undefined);
  assert.equal(generationRequest('guided', 2).variationCount, 2);
  assert.equal(generationRequest('guided', 5).variationCount, 5);
  assert.throws(() => generationRequest('guided', 1));
  assert.throws(() => generationRequest('guided', 6));
});
test('actual nested React build metadata must be successful and match outer hashes', () => {
  const { buildPassed } = load('project');
  const hash = 'a'.repeat(64);
  const build = { ok: true, sourceHash: hash, bundleHash: hash, catalogHash: hash,
    gates: Object.fromEntries(['policy', 'types', 'build', 'components'].map(key => [key, { status: 'pass' }])) };
  const round = { build, sourceHash: hash, bundleHash: hash, catalogHash: hash };
  assert.equal(buildPassed(round, hash), true);
  assert.equal(buildPassed({ ...round, build: { ...build, ok: false } }, hash), false);
  assert.equal(buildPassed({ ...round, build: { ...build, bundleHash: 'b'.repeat(64) } }, hash), false);
});
test('missing ontology identity does not authorize new generation or export', () => {
  const { currentGuideline } = load('project');
  const product = { id: 'p', publishedGuidelineId: 'g', ontologyHash: 'a'.repeat(64) };
  const snapshot = { productId: 'p', guidelineId: 'g', ontologyHash: product.ontologyHash };
  assert.equal(currentGuideline(product, snapshot), true);
  assert.equal(currentGuideline({ ...product, ontologyHash: undefined }, { ...snapshot, ontologyHash: undefined }), false);
  assert.equal(currentGuideline({ ...product, publishedGuidelineId: 'new' }, snapshot), false);
});
test('manual asset selection excludes system guides and retains the exact fixed guide on edit', () => {
  const { manualAssets, applyManualAssets, editable } = load('rules');
  const assets = [
    { id: 'upload', name: '화면.html' },
    { id: 'old-guide', system: true, productId: 'p', guidelineId: 'g1' },
    { id: 'current-guide', system: true, productId: 'p', guidelineId: 'g2' },
  ];
  assert.deepEqual(manualAssets(assets).map(asset => asset.id), ['upload']);
  const draft = { title: '과거 규칙', brief: '', viewport: { width: 390, height: 844 }, rules: [], unresolved: [],
    assetIds: ['old-guide'], projectId: 'project', productId: 'p', guidelineId: 'g1', guidelineAssetId: 'old-guide',
    ontologyHash: 'old-ontology', catalogHash: 'kit' };
  const next = applyManualAssets(draft, assets, ['upload', 'current-guide']);
  assert.deepEqual(next.assetIds, ['upload', 'old-guide']);
  assert.equal(next.guidelineAssetId, 'old-guide');
  assert.equal(next.guidelineId, 'g1');
  assert.equal(editable(next).guidelineAssetId, 'old-guide');
  assert.deepEqual(draft.assetIds, ['old-guide']);
  assert.deepEqual(applyManualAssets(draft, [], []).assetIds, ['old-guide']);
});
test('ontology pages expose readable notice targets and full-copy checks preserve whitespace semantics', () => {
  const { guidelinePages, noticeProblems, noticeRule } = load('guidelinePages');
  const { editable } = load('rules');
  const ontology = { nodes: [
    { id: 'policy', label: 'PolicyRule', props: { required: true } },
    { id: 'screen', label: 'ScreenMeta', props: { pageId: 'notice-rate', title: '금리 안내', content: '금리는\n조건에 따라  달라집니다.' } },
  ], edges: [{ src: 'policy', rel: 'CONSTRAINS', dst: 'screen' }] };
  const pages = guidelinePages(ontology);
  assert.deepEqual(pages.map(page => [page.pageId, page.title, page.required]), [['notice-rate', '금리 안내', true]]);
  const draft = { title: '규칙', brief: '', assetIds: ['guide'], viewport: { width: 390, height: 844 }, rules: [], unresolved: [] };
  assert.equal(noticeProblems(draft, pages).length, 1);
  const rule = noticeRule(pages[0], 'guide');
  assert.equal(rule.required, true);
  assert.equal(rule.steps[0].target, 'notice-rate');
  assert.equal(rule.steps[0].normalizeWhitespace, true);
  draft.rules.push(rule);
  assert.deepEqual(noticeProblems(draft, pages), []);
  assert.equal(editable(draft).rules[0].steps[0].normalizeWhitespace, true);
  rule.steps[0].value = '금리는';
  assert.equal(noticeProblems(draft, pages).length, 1);
  const long = { ...pages[0], content: '전체 문구 확인 '.repeat(350) };
  const longRule = noticeRule(long, 'guide');
  assert(longRule.steps.length > 1);
  assert(longRule.steps.every(step => [...step.value].length <= 2000));
  assert.deepEqual(noticeProblems({ ...draft, rules: [longRule] }, [long]), []);
});
test('release readiness requires actual rebuild gates and the approved-screen 2% comparison', () => {
  const { releaseChecksPassed } = load('project');
  const hashes = { sourceHash: 'a'.repeat(64), bundleHash: 'b'.repeat(64), catalogHash: 'c'.repeat(64) };
  const release = { ...hashes, status: 'ready', build: { ...hashes, ok: true,
    gates: Object.fromEntries(['policy', 'types', 'build', 'components'].map(key => [key, { status: 'pass' }])) },
    verification: { functionalStatus: 'pass', accessibility: { status: 'pass' },
      visual: { status: 'pass', tolerance: 0.02, changedRatio: 0.001 } } };
  assert.equal(releaseChecksPassed(release), true);
  for (const status of ['queued', 'running', 'failed']) assert.equal(releaseChecksPassed({ ...release, status }), false);
  assert.equal(releaseChecksPassed({ ...release, build: undefined }), false);
  assert.equal(releaseChecksPassed({ ...release, verification: undefined }), false);
  for (const visual of [{ status: 'review-required' }, { tolerance: 0.15 }, { changedRatio: 0.03 }, { changedRatio: undefined }]) {
    assert.equal(releaseChecksPassed({ ...release, verification: { ...release.verification, visual: { ...release.verification.visual, ...visual } } }), false);
  }
});
