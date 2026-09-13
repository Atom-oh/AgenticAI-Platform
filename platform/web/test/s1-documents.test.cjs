const { test } = require('node:test');
const assert = require('node:assert/strict');
const { build } = require('esbuild');
const { chromium } = require('playwright');
const path = require('node:path');
const fs = require('node:fs');

// Only the HTTP/auth boundary is fake. Components, helpers, routing and events are real.
async function openUI(entry = 'S1', options = {}) {
  const root = path.resolve(__dirname, '..');
  const calls = [], errors = [], external = [];
  const hash = 'a'.repeat(64);
  const document = { id: 'doc-1', version: 4, title: '합성 담보 기준', kind: 'regulation', graphRef: 'REG-1',
    createdBy: 'actor', projectId: null, readRoles: ['owner', 'planner'], aclVersion: 1, status: 'active',
    latestRevisionId: 'doc-1--r2', approvedRevisionId: 'doc-1--r1', provenance: 'synthetic_sample', createdAt: 1, updatedAt: 2 };
  const revision = { id: 'doc-1--r1', documentId: 'doc-1', version: 3, revision: 1, name: 'sample.txt',
    size: 4, sha256: 'b'.repeat(64), versionLabel: '기준 1', effectiveDate: null, createdBy: 'actor',
    status: 'approved', parseStatus: 'complete', textHash: hash, paragraphCount: 2, pages: 1, warnings: [],
    createdAt: 1, updatedAt: 2, reviewedBy: 'reviewer', reviewedAt: 2, reviewNote: '합성 자료 검토' };
  const analysis = { id: 'ana-1', version: 7, query: '합성 규정 변경 검토', regulationRef: 'REG-1', modelId: 'fixture-model',
    createdBy: 'actor', projectId: null, status: 'needs_review', jobId: 'job-1', createdAt: 1, updatedAt: 2, decisionCount: 0 };
  const result = { regulation: { id: 'REG-1', title: '합성 담보 기준' }, counts: { products: 1, documents: 1 },
    candidates: { products: [{ id: 'PROD-1', label: 'Product', name: '합성 상품' }],
      documents: [{ id: 'DOC-missing', label: 'Document', name: '원문 없는 안내서' }] },
    sources: [{ documentId: 'doc-1', revisionId: revision.id, graphRef: 'REG-1', title: document.title,
      revision: 1, versionLabel: revision.versionLabel, sha256: revision.sha256, textHash: hash, provenance: 'synthetic_sample' }],
    evidence: [{ id: 'E1', documentId: 'doc-1', revisionId: revision.id, paragraphId: 'p000002', title: document.title,
      revision: 1, versionLabel: revision.versionLabel, originalSha256: revision.sha256, textHash: hash,
      quote: '합성 문단: 담당자가 변경 범위를 검토합니다.', page: 1, provenance: 'synthetic_sample' }],
    findings: [{ nodeId: 'PROD-1', reason: '안내 변경 여부를 확인하세요. [외부](https://invented.invalid)', citationIds: ['E1'] }],
    summary: '원문을 대조하여 변경 여부를 검토하세요.',
    coverage: { graphBackend: 'local', linkedSources: 1, unavailableSources: 1, sourceLimitReached: false,
      truncated: true, evidenceParagraphs: 1, availableParagraphs: 2, contextCharacters: 120, candidateContextsOmitted: 1 },
    verification: { sourceIntegrity: 'verified_at_analysis', references: 'checked', semantic: 'requires_human_review' },
    model: { invoked: true, modelId: 'fixture-model', usage: { inputTokens: 120, outputTokens: 30 } } };
  const state = { document, revision, analysis, result, decisions: [], staleSources: [], canDecide: true, ...options.state };
  const imports = entry === 'router'
    ? `import S1 from './src/S1';import Library from './src/documents/LibraryPage';
       function Page(){const [hash,setHash]=React.useState(location.hash);React.useEffect(()=>{const change=()=>setHash(location.hash);window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change)},[]);return hash.startsWith('#/documents')?<Library/>:<S1/>;}`
    : `import Page from './src/${entry}';`;
  const bundle = await build({ stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';
    ${imports} import {auth} from './src/lib';
    const root=createRoot(document.getElementById('root'));root.render(<React.StrictMode><Page/></React.StrictMode>);
    window.dispose=()=>root.unmount();window.expire=()=>{auth.token=null;window.dispatchEvent(new Event('authchange'));};`,
    loader: 'tsx', resolveDir: root }, bundle: true, write: false, outdir: '/virtual', jsx: 'automatic', format: 'iife',
    define: { 'process.env.NODE_ENV': '"development"' }, plugins: [{ name: 'offline-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
      builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents:
        'export const auth={token:"offline"};export const sock={run:()=>{throw Error("Legacy S1 is forbidden")}};' }));
    } }] });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, offline: true });
  await context.routeWebSocket('**/*', socket => { external.push('websocket'); socket.close(); });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.hostname !== 'offline.test') { external.push(url.href); return route.abort(); }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>문서 UI 오프라인 검증</title></head><body style="margin:0;background:#f4f7f6"><main style="padding:20px"><div id="root"></div></main></body></html>' });
    assert.equal(route.request().headers().authorization, 'Bearer offline');
    const target = url.pathname.replace('/studio-api', ''), method = route.request().method();
    const body = method === 'GET' || /\/parts\//.test(target) ? null : route.request().postDataJSON();
    const scope = route.request().headers()['x-workspace-project'] || 'personal';
    calls.push({ target, method, body, scope, query: Object.fromEntries(url.searchParams) });
    if (options.route && await options.route({ route, target, method, body, scope, url, json, state })) return;
    if (target === '/projects') return json({ projects: [{ id: 'team-a', name: '검토 프로젝트', version: 1, members: { actor: { role: 'owner' } } }] });
    if (target === '/documents/config') return json({ roles: ['owner', 'planner', 'designer', 'developer'],
      maxFileBytes: 20971520, chunkBytes: 2097152, extensions: ['txt', 'pdf', 'html', 'htm', 'md', 'markdown'],
      role: 'owner', actorId: 'actor', projectId: scope === 'personal' ? null : scope });
    if (target === '/config') return json({ models: [{ id: 'fixture-model', label: '검증용 모델' }], defaultModel: 'fixture-model' });
    if (target === '/documents/references') return json({ references: [
      { id: 'REG-1', label: 'Regulation', title: '합성 담보 기준' }, { id: 'DOC-missing', label: 'Document', title: '원문 없는 안내서' }],
      backend: 'local', note: 'Shared demonstration catalog' });
    if (target === '/documents') return json({ documents: [state.document] });
    if (target === '/impact-analyses' && method === 'GET') return json({ analyses: [state.analysis] });
    if (target === '/impact-analyses' && method === 'POST') return json({ analysis: { ...state.analysis, status: 'queued' }, job: { id: 'job-1', status: 'queued', progress: 0 } }, 202);
    if (target === '/jobs/job-1') return json({ job: { id: 'job-1', status: 'completed', progress: 100 } });
    if (target === '/impact-analyses/ana-1/decisions') {
      assert.equal(body.version, state.analysis.version);
      state.decisions.push({ id: 'decision-1', nodeId: body.nodeId, decision: body.decision, note: body.note,
        actorId: 'actor', actorRole: 'owner', createdAt: 3 });
      state.analysis = { ...state.analysis, version: state.analysis.version + 1, decisionCount: state.decisions.length };
    }
    if (target.startsWith('/impact-analyses/ana-1')) return json({ analysis: state.analysis, result: state.result,
      decisions: state.decisions, decisionsTruncated: false, staleSources: state.staleSources, canDecide: state.canDecide });
    if (target === '/documents/doc-1') return json({ document: state.document, revisions: [state.revision,
      { ...state.revision, id: 'doc-1--r2', revision: 2, versionLabel: '다음 기준', status: 'draft' }],
      capabilities: { read: true, edit: true, review: true, manage: true } });
    if (target === '/documents/doc-1/activity') return json({ events: [] });
    if (target === '/documents/doc-1/revisions/doc-1--r1') return json({ document: state.document, revision: state.revision,
      totalParagraphs: 2, paragraphs: [{ id: 'p000001', text: '<script>window.bad=true</script>', page: 1, sha256: hash },
        { id: 'p000002', text: '합성 문단: 담당자가 변경 범위를 검토합니다.', page: 1, sha256: hash }] });
    return json({ error: '합성 테스트에서 처리하지 않는 경로', code: 'not-found' }, 404);
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('https://offline.test/' + (options.hash || '#/s1'));
  const boot = async () => {
    if (options.ignoreAbort) await page.evaluate(() => {
      const original = window.fetch;
      window.fetch = (url, init) => original(url, { ...init, signal: undefined });
    });
    const css = bundle.outputFiles.find(file => file.path.endsWith('.css'));
    if (css) await page.addStyleTag({ content: css.text });
    await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.js')).text });
  };
  await boot();
  const capture = async name => {
    if (!process.env.WORKSPACE_QA_DIR) return;
    fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, name + '.png'), fullPage: true });
  };
  return { page, browser, state, calls, errors, external, capture, reload: async () => { await page.reload(); await boot(); } };
}

if (require.main === module) test('S1 creates a private stored analysis and records an explicit version-bound human decision', { timeout: 40000 }, async () => {
  const ui = await openUI();
  const { page, calls } = ui;
  try {
    await page.getByLabel('분석할 규정', { exact: true }).selectOption('REG-1');
    await page.getByLabel('변경 내용 또는 검토 질문', { exact: true }).fill('합성 규정의 안내 변경을 검토하세요.');
    await page.getByRole('button', { name: '영향 분석 시작', exact: true }).click();
    await page.getByText('원문을 대조하여 변경 여부를 검토하세요.', { exact: true }).waitFor();
    assert.match(page.url(), /analysisId=ana-1/);
    assert.equal(calls.find(c => c.target === '/impact-analyses' && c.method === 'POST').body.modelId, 'fixture-model');
    assert.equal(calls.filter(c => c.target.endsWith('/decisions')).length, 0);
    const evidence = page.getByRole('link', { name: /E1.*근거 문단/ }).first();
    const href = await evidence.getAttribute('href');
    assert.match(href, /revisionId=doc-1--r1/);
    assert.match(href, /paragraph=p000002/);
    assert.match(href, /analysisId=ana-1/);
    assert.match(href, /textHash=a{64}/);
    assert.equal(await page.locator('a[href^="https:"]').count(), 0);
    await page.getByText(/^상품 · /).click();
    await page.getByRole('button', { name: '합성 상품 검토', exact: true }).click();
    await page.getByLabel('검토 판단', { exact: true }).selectOption('change_required');
    await page.getByLabel('판단 근거', { exact: true }).fill('합성 원문 1차 버전과 안내 범위를 대조했습니다.');
    assert.equal(await page.getByRole('button', { name: '검토 판단 저장', exact: true }).isDisabled(), true);
    await page.getByLabel('표시된 분석 버전과 원문 근거를 확인했습니다', { exact: true }).check();
    await page.getByRole('button', { name: '검토 판단 저장', exact: true }).click();
    await page.getByText('합성 원문 1차 버전과 안내 범위를 대조했습니다.', { exact: true }).waitFor();
    assert.equal(calls.find(c => c.target.endsWith('/decisions')).body.version, 7);
    await ui.capture('s1-desktop');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    await ui.capture('s1-narrow');
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

module.exports = { openUI };
