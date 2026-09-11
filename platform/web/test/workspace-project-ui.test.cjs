const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('project scope, collaboration, guided baselines and real release metadata remain isolated', { timeout: 120_000 }, async t => {
  const root = path.resolve(__dirname, '..');
  const hash = text => createHash('sha256').update(text).digest('hex');
  const html = '<html lang="ko"><body><p data-testid="summary">확인</p></body></html>';
  const catalogHash = hash('kit'), contractHash = hash('contract'), artifactSha256 = hash(html);
  const catalog = { ...JSON.parse(fs.readFileSync(path.join(root, '../react-kit/catalog.json'), 'utf8')), hash: catalogHash };
  const gates = Object.fromEntries(['policy', 'types', 'build', 'components'].map(key => [key, { status: 'pass' }]));
  const requiredCopy = '기준 금리 표시\n안내 내용은 모두 확인해야 합니다.';
  const projects = Object.fromEntries(['a', 'b', 'c', 'revoked'].map((id, index) => [id, {
    id, name: `프로젝트 ${id.toUpperCase()}`, version: 1,
    members: id === 'revoked' ? {} : { actor: { role: ['owner', 'developer', 'planner'][index], displayName: '참여자 김' } },
  }]));
  const product = { id: 'product', projectId: 'a', version: 1, title: '적금 가입', description: '가입 지침',
    conditions: [{ id: 'condition-age', text: '성인만 가입' }], steps: [{ id: 'entry', title: '조건 확인', description: '동의 후 진행' }],
    notices: [{ id: 'notice-rate', title: '금리 안내', content: requiredCopy, required: true }],
    publishedGuidelineId: 'guide-1', publishedRevision: 1, ontologyHash: hash('ontology-1'), guideAssetId: 'guide-1-asset' };
  const contract = { id: 'contract', version: 3, status: 'approved', hash: contractHash, title: '가입 규칙', brief: '가입 화면',
    assetIds: ['asset'], viewport: { width: 390, height: 500 }, unresolved: [], catalogHash,
    productId: product.id, projectId: 'a', guidelineId: product.publishedGuidelineId, guidelineAssetId: 'guide-1-asset', ontologyHash: product.ontologyHash,
    rules: [{ id: 'R1', title: '확인 문구', required: true, source: { kind: 'manual' },
      steps: [{ action: 'expectText', target: 'summary', targetLabel: '확인 문구', value: '확인' }] },
      { id: 'R2', title: '필수 금리 안내', required: true, source: { kind: 'manual' },
        steps: [{ action: 'expectText', target: 'notice-rate', targetLabel: '금리 안내', value: requiredCopy, normalizeWhitespace: true }] }] };
  const round = { number: 1, passed: true, artifactSha256, hasHtml: true, hasReport: true, hasScreenshot: true,
    hasSource: true, hasDist: true, hasCandidate: true,
    build: { gates, ok: true, catalogHash, sourceHash: hash('source'), bundleHash: hash('bundle') },
    catalogHash, sourceHash: hash('source'), bundleHash: hash('bundle'), checks: { pass: 1, fail: 0, incomplete: 0 },
    blockingFindings: [], functionalStatus: 'pass', visualStatus: 'not-run', pageSources: [{ pageId: 'entry', path: 'src/App.tsx' }, { pageId: 'review', path: 'src/pages/review.tsx' }] };
  const approvedRun = id => ({ id, version: 1, contractId: contract.id, contractVersion: 3, contractHash, catalogHash,
    outputType: 'react', mode: 'generate', model: 'fable', status: 'completed', contract, rounds: [structuredClone(round)], bestRound: 1,
    productId: product.id, projectId: id.startsWith('b') ? 'b' : 'a', guidelineId: 'guide-1', ontologyHash: hash('ontology-1'),
    approval: { round: 1, artifactSha256, contractVersion: 3, contractHash, actor: 'designer', at: 123456,
      sourceHash: round.sourceHash, bundleHash: round.bundleHash, catalogHash, guidelineId: 'guide-1' } });
  const runs = { a: [approvedRun('a-existing')], b: [approvedRun('b-approved'),
    { ...approvedRun('b-missing'), rounds: [{ ...round, build: undefined }] }], c: [], personal: [], revoked: [] };
  let heldAssets, holdA = true, batchSequence = 0, release, connections = [];
  let proposedContract;
  const guideText = revision => `게시 지침 ${revision}차 원본\n${requiredCopy}`;
  const systemGuide = revision => ({ id: `guide-${revision}-asset`, name: `guideline-${revision}.txt`, system: true, productId: 'product',
    projectId: 'a', guidelineId: `guide-${revision}`, version: 1, importRevision: revision, purpose: 'guide',
    size: Buffer.byteLength(guideText(revision)), sha256: hash(guideText(revision)), uploadStatus: 'stored', parseStatus: 'complete', previews: [] });
  const systemGuides = [systemGuide(1), systemGuide(0)];
  const calls = [], external = [], errors = [], discussions = [], batches = [];
  batches.push({ id: 'partial-batch', mode: 'guided', status: 'partial', contractId: contract.id, contractVersion: 3,
    contractHash, catalogHash, runIds: ['a-existing'], variationCount: 2,
    slots: [{ index: 0, role: 'baseline', variant: 'baseline', error: '기준안 준비 실패' },
      { index: 1, role: 'variation', variant: 'layout', runId: 'a-existing' },
      { index: 2, role: 'variation', variant: 'dense', error: '변형안 준비 실패' }] });
  const bundle = await build({ stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import Workspace from './src/workspace/Workspace';
    const root=createRoot(document.getElementById('root'));root.render(<Workspace/>);window.dispose=()=>root.unmount();`,
    loader: 'tsx', resolveDir: root }, bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"development"' }, plugins: [{ name: 'offline-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
      builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents: 'export const auth={token:"offline"};' }));
    } }] });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, offline: true });
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.hostname !== 'offline.test') { external.push(url.href); return route.abort(); }
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><body><div id="root"></div></body></html>' });
      assert.equal(route.request().headers().authorization, 'Bearer offline');
      const scope = route.request().headers()['x-workspace-project'] || 'personal';
      const target = url.pathname.replace('/studio-api', '');
      const method = route.request().method(), body = method === 'GET' ? null : route.request().postDataJSON();
      calls.push({ target, method, scope, body, query: Object.fromEntries(url.searchParams) });
      if (target === '/config') { assert.equal(scope, 'personal'); return json({ actorId: 'actor', models: [{ id: 'fable', label: 'Fable' }],
        componentCatalog: { id: catalog.id, label: catalog.label, version: catalog.version, hash: catalog.hash },
        defaultModel: 'fable', extensions: ['html', 'png', 'jpg', 'svg', 'css', 'pdf', 'fig'], maxFileBytes: 52428800, chunkBytes: 2097152 }); }
      if (target === '/components') return json({ catalog });
      if (target === '/projects') { assert.equal(scope, 'personal'); return json({ projects: Object.values(projects).map(item =>
        ({ ...item, members: { actor: { role: 'owner', displayName: 'Stale index must not authorize' } } })) }); }
      if (target.startsWith('/projects/')) {
        assert.equal(scope, 'personal');
        const id = target.split('/')[2];
        if (target.endsWith('/people')) return json({ people: [{ sub: 'person', displayName: '개발 참여자' }] });
        if (target.endsWith('/members')) { assert.equal(body.version, projects[id].version); projects[id].members = body.members; projects[id].version++; }
        return json({ project: projects[id] });
      }
      if (target === '/git-connections') return json({ connections });
      if (target === '/assets') {
        const asset = { id: 'asset', version: 19, importRevision: 1, name: `${scope}-자료.html`, size: html.length,
          sha256: artifactSha256, purpose: 'prototype', uploadStatus: 'stored', parseStatus: 'complete', previews: [] };
        const assets = [asset, ...(scope === 'a' ? systemGuides : [])];
        if (scope === 'a' && holdA) { holdA = false; heldAssets = () => json({ assets }).catch(() => {}); return; }
        return json({ assets });
      }
      if (target === '/contracts') return json({ contracts: scope === 'a' || scope === 'b' ? [contract, ...(proposedContract ? [proposedContract] : [])] : [] });
      if (target === '/contracts/contract') return json({ contract });
      if (target === '/contracts/propose') {
        assert.equal(scope, 'a'); assert.equal(body.productId, 'product'); assert.deepEqual(body.assetIds, []);
        proposedContract = { ...contract, id: 'proposed', status: 'draft', assetIds: [product.guideAssetId], rules: [contract.rules[0]],
          unresolved: ['필수 금리 안내 전체 문구를 확인해야 합니다.'] };
        return json({ job: { id: 'proposal', task: 'propose', status: 'queued' } });
      }
      if (target === '/jobs/proposal') return json({ job: { id: 'proposal', task: 'propose', status: 'completed', result: { contractId: 'proposed' } } });
      if (target === '/contracts/proposed') {
        if (method === 'PUT') {
          assert.equal(body.guidelineAssetId, 'guide-1-asset'); assert.deepEqual(body.assetIds, ['guide-1-asset']);
          const step = body.rules.flatMap(rule => rule.steps).find(step => step.target === 'notice-rate');
          assert.equal(step.normalizeWhitespace, true);
          assert.equal(step.value, requiredCopy.replace(/\s+/g, ' '));
          proposedContract = { ...proposedContract, ...body, version: proposedContract.version + 1 };
        }
        return json({ contract: proposedContract });
      }
      if (target === '/products' && method === 'GET') return json({ products: [{ ...product, projectId: scope }] });
      if (target === '/products/product' && method === 'GET') return json({ product: { ...product, projectId: scope } });
      if (target === '/products/product' && method === 'PUT') {
        assert.equal(body.version, product.version);
        assert.equal(body.conditions[0].id, 'condition-age');
        Object.assign(product, body, { version: product.version + 1 }); return json({ product });
      }
      if (target === '/products/product/publish') {
        assert.equal(body.version, product.version);
        systemGuides.unshift(systemGuide(2));
        Object.assign(product, { version: product.version + 1, publishedRevision: 2, publishedGuidelineId: 'guide-2', ontologyHash: hash('ontology-2'), guideAssetId: 'guide-2-asset' });
        return json({ product });
      }
      if (target === '/products/product/ontology') {
        const guidelineId = url.searchParams.get('revision') || product.publishedGuidelineId;
        const revision = Number(guidelineId.split('-').at(-1));
        return json({ ontology: { schemaVersion: 1, projectId: scope, productId: 'product',
          guidelineId, revision, hash: hash(`ontology-${revision}`),
          nodes: [{ id: 'policy-rate', label: 'PolicyRule', props: { required: true } },
            { id: 'screen-rate', label: 'ScreenMeta', props: { pageId: 'notice-rate', title: '금리 안내', content: requiredCopy } }],
          edges: [{ src: 'policy-rate', rel: 'CONSTRAINS', dst: 'screen-rate' }] } });
      }
      if (target === '/products/product/impact') return json({ currentGuidelineId: product.publishedGuidelineId,
        affectedRuns: product.publishedRevision > 1 ? runs[scope].filter(run => run.guidelineId !== product.publishedGuidelineId) : [] });
      if (target === '/comments' && method === 'GET') return json({ comments: discussions.filter(item =>
        Object.entries(item.anchor).every(([key, value]) => String(value) === url.searchParams.get(key))) });
      if (target === '/comments' && method === 'POST') {
        const comment = { id: `comment-${discussions.length}`, ...body, author: 'actor', createdAt: 123456 };
        discussions.push(comment); return json({ comment });
      }
      if (target === '/runs') return json({ runs: runs[scope] });
      if (target === '/batches' && method === 'GET') return json({ batches: scope === 'a' ? batches : [] });
      if (target.startsWith('/batches/')) {
        const batch = batches.find(batch => batch.id === target.split('/')[2]);
        return json({ batch, runs: runs[scope].filter(run => batch.runIds.includes(run.id)) });
      }
      if (target === '/batches' && method === 'POST') {
        assert.equal(scope, 'a'); assert.equal(body.mode, 'guided'); assert.equal(body.outputType, 'react'); assert([2, 5].includes(body.variationCount));
        const id = `batch-${++batchSequence}`, baselineRunId = `${id}-baseline`;
        const created = [baselineRunId, ...Array.from({ length: body.variationCount }, (_, index) => `${id}-variant-${index}`)].map((id, index) => {
          const run = { ...approvedRun(id), approval: undefined, batchId: `batch-${batchSequence}`, visualPolicy: index ? 'variation-review' : 'exact' };
          if (index === 1) { run.status = 'failed'; run.rounds = []; }
          if (index === 2) {
            run.referenceAssetId = 'image';
            run.rounds = [{ ...round, visualStatus: 'review-required' }, { ...round, number: 2, visualStatus: 'review-required' }];
          }
          return run;
        });
        runs.a.unshift(...created);
        const batch = { id, mode: 'guided', baselineRunId, runIds: created.map(run => run.id).reverse(),
          variationCount: body.variationCount, contractId: contract.id, contractVersion: contract.version, contractHash, catalogHash };
        batches.push(batch); return json({ batch, runs: created }, 202);
      }
      if (target.startsWith('/runs/') && target.endsWith('/approve')) {
        const run = runs[scope].find(run => run.id === target.split('/')[2]);
        assert.equal(body.sourceHash, round.sourceHash); assert.equal(body.bundleHash, round.bundleHash);
        assert.equal(body.acceptVariation, true);
        run.approval = { ...body, acceptedVariation: true, catalogHash, guidelineId: run.guidelineId, contractHash, actor: 'actor', at: Date.now() };
        return json({ run });
      }
      if (target === '/releases' && method === 'GET') return json({ releases: scope === 'b' && release ? [release] : [] });
      if (target === '/releases' && method === 'POST') {
        assert.equal(scope, 'b'); assert.equal(body.runId, 'b-approved'); assert.equal(body.round, 1);
        release = { id: 'release', runId: body.runId, round: 1, status: 'queued', sourceHash: round.sourceHash, bundleHash: round.bundleHash,
          catalogHash, contractHash, guidelineId: 'guide-1', approval: runs.b[0].approval, jobId: 'release-job' };
        return json({ release, job: { id: 'release-job', task: 'release', status: 'queued' } });
      }
      if (target === '/jobs/release-job') {
        Object.assign(release, { status: 'ready', build: round.build,
          verification: { functionalStatus: 'pass', accessibility: { status: 'pass' },
            visual: { status: 'pass', tolerance: 0.02, changedRatio: 0.001 } },
          hasSource: true, hasDist: true, hasManifest: true, hasReport: true });
        return json({ job: { id: 'release-job', task: 'release', status: 'completed', result: { releaseId: release.id } } });
      }
      if (target === '/releases/release/git') {
        assert.equal(scope, 'b'); assert.equal(body.connectionId, 'connection');
        assert.equal(body.connectionHash, '6'.repeat(64));
        release.git = { status: 'committed', branch: 'feature/approved-ui', commitSha: 'a'.repeat(40), commitUrl: 'https://git.invalid/commit/' + 'a'.repeat(40) };
        return json({ release });
      }
      if (target === '/releases/release') return json({ release });
      if (target.includes('/blob')) {
        const run = runs[scope].find(run => target === `/runs/${run.id}/blob`);
        const selectedRound = run?.rounds.find(round => round.number === Number(url.searchParams.get('round')));
        const report = { artifactSha256, contractHash, contractVersion: 3, passed: true, functionalStatus: 'pass', accessibility: { status: 'pass' },
          sourceHash: round.sourceHash, bundleHash: round.bundleHash, catalogHash, build: round.build,
          visualPolicy: run?.visualPolicy,
          visual: selectedRound?.visualStatus === 'review-required' ? { status: 'review-required', comparisonStatus: 'fail' } : { status: 'not-run' },
          checks: contract.rules.map(rule => ({ caseId: rule.id, title: `${rule.title} 근거`, status: 'pass', required: true })),
          blockingFindings: [], networkRequests: [], consoleErrors: [] };
        const guideAsset = systemGuides.find(asset => target === `/assets/${asset.id}/blob`);
        const bytes = Buffer.from(guideAsset ? guideText(guideAsset.importRevision) : url.searchParams.get('kind') === 'report' ? JSON.stringify(report) : html);
        const offset = Number(url.searchParams.get('offset')), chunk = bytes.subarray(offset, offset + 128);
        return route.fulfill({ body: chunk, headers: { 'X-SHA256': hash(bytes), 'X-Content-Type': 'application/octet-stream',
          'X-Total-Size': String(bytes.length), 'X-Chunk-Size': String(chunk.length) } });
      }
      if (target.startsWith('/runs/')) return json({ run: runs[scope].find(run => run.id === target.split('/')[2]) });
      throw new Error(`Unexpected fixture: ${method} ${scope} ${target}`);
    });
    const page = await context.newPage(); page.setDefaultTimeout(10_000);
    page.on('pageerror', error => errors.push(String(error))); page.on('dialog', dialog => dialog.accept());
    await page.goto('https://offline.test/');
    await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    const space = page.getByLabel('작업 공간', { exact: true });
    await page.getByText('personal-자료.html', { exact: true }).waitFor();
    await space.selectOption('a');
    await page.waitForFunction(() => document.querySelector('.ws-project-bar')?.textContent.includes('관리자'));
    while (!heldAssets) await page.waitForTimeout(20);
    await space.selectOption('b');
    await page.getByText('b-자료.html', { exact: true }).waitFor();
    await heldAssets();
    assert.equal(await page.getByText('a-자료.html', { exact: true }).count(), 0);
    assert.match(await page.locator('.ws-project-bar').innerText(), /개발/);
    await page.getByRole('button', { name: '3 생성·검수·수정' }).click();
    await page.getByLabel('사용할 규칙', { exact: true }).selectOption('contract');
    assert.equal(await page.getByRole('button', { name: '시안 1개 만들기', exact: true }).isDisabled(), true);
    await page.locator('.ws-run-choice').nth(1).click();
    await page.getByRole('button', { name: '개발·내보내기', exact: true }).click();
    await page.getByText('컴포넌트 목록·개발 속성', { exact: true }).click();
    await page.getByText('Button', { exact: true }).waitFor();
    assert.match(await page.locator('.ws-component-catalog').innerText(), /플랫폼 기본 React 컴포넌트.*1\.0\.0/s);
    assert.equal(await page.getByRole('button', { name: '승인 소스 재빌드·릴리스 검사' }).isDisabled(), true);
    await page.locator('.ws-run-choice').first().click();
    await page.getByRole('button', { name: '승인 소스 재빌드·릴리스 검사' }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === '승인 소스 재빌드·릴리스 검사' && !button.disabled));
    const savedApproval = runs.b[0].approval;
    delete runs.b[0].approval; runs.b[0].version += 1;
    await page.getByRole('button', { name: '내 작업 새로 조회', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === '승인 소스 재빌드·릴리스 검사' && button.disabled));
    runs.b[0].approval = savedApproval; runs.b[0].version += 1;
    await page.getByRole('button', { name: '내 작업 새로 조회', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === '승인 소스 재빌드·릴리스 검사' && !button.disabled));
    await page.getByRole('button', { name: '승인 소스 재빌드·릴리스 검사' }).click();
    await page.getByRole('heading', { name: '저장된 릴리스' }).waitFor();
    assert.equal(await page.getByRole('button', { name: '기능 브랜치로 내보내기' }).isDisabled(), true);
    await page.getByText('설정된 Git 저장소가 없습니다.', { exact: false }).waitFor();
    connections = [{ id: 'connection', label: '등록된 비공개 저장소', visibility: 'private', connectionHash: '6'.repeat(64) }];
    await page.getByRole('button', { name: '개발 기록 다시 조회' }).click();
    await page.getByLabel('등록된 Git 저장소').selectOption('connection');
    await page.getByRole('button', { name: '기능 브랜치로 내보내기' }).click();
    const commitLink = page.getByRole('link', { name: '저장된 커밋 열기' });
    await commitLink.waitFor();
    assert.equal(await commitLink.getAttribute('href'), 'https://git.invalid/commit/' + 'a'.repeat(40));
    assert.deepEqual(external, []);
    await space.selectOption('a');
    await page.getByText('a-자료.html', { exact: true }).waitFor();
    assert.equal(await page.locator('.ws-assets article').count(), 1);
    assert.equal(await page.locator('.ws-intake').getByText('guideline-1.txt', { exact: true }).count(), 0);
    await page.getByRole('button', { name: '2 규칙 확인·승인' }).click();
    assert.equal(await page.getByRole('button', { name: '선택한 파일로 규칙 제안받기', exact: true }).isDisabled(), true);
    await page.getByLabel('공유 상품', { exact: true }).selectOption('product');
    await page.getByRole('button', { name: '선택한 파일로 규칙 제안받기', exact: true }).click();
    await page.getByText('AI 제안은 아직 승인되지 않았습니다.', { exact: false }).waitFor();
    const rulePanel = page.locator('.ws-rules-panel');
    await rulePanel.getByRole('button', { name: '금리 안내 전체 안내문 검사 추가', exact: true }).click();
    const noticeCard = rulePanel.locator('.ws-rule-card').last();
    assert.equal(await noticeCard.getByLabel('게시 지침의 안내 화면', { exact: true }).inputValue(), 'notice-rate');
    assert.equal(await noticeCard.getByLabel('줄바꿈·연속 공백을 하나의 공백으로 비교', { exact: true }).isChecked(), true);
    await rulePanel.getByRole('button', { name: '해결됨', exact: true }).click();
    await page.getByRole('button', { name: '현재 선택한 파일 적용', exact: true }).click();
    assert.match(await page.locator('.ws-rule-files').innerText(), /guideline-1.txt/);
    await page.getByLabel('규칙 묶음 이름', { exact: true }).fill('자동 지침 유지 규칙');
    await page.getByRole('button', { name: '규칙 저장', exact: true }).click();
    await page.getByText('규칙을 저장했습니다.', { exact: false }).waitFor();
    await noticeCard.getByLabel('기대 결과', { exact: true }).fill('기준 금리');
    await rulePanel.getByText('필수 안내 ‘금리 안내’ 화면의 전체 문구를 확인하는 필수 검사가 필요합니다.', { exact: true }).waitFor();
    assert.equal(await rulePanel.getByRole('button', { name: /규칙 승인$/, exact: false }).isDisabled(), true);
    await noticeCard.getByLabel('기대 결과', { exact: true }).fill(requiredCopy.replace(/\s+/g, ' '));
    for (const width of [390, 1440, 3440]) {
      await page.setViewportSize({ width, height: 1080 });
      const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert(geometry.scroll <= geometry.width + 1, `Notice editor overflow: ${JSON.stringify(geometry)}`);
    }
    await page.getByText('지침 버전과 원본 확인', { exact: true }).click();
    await page.getByText(guideText(1), { exact: true }).waitFor();
    await page.getByLabel('게시 지침 버전', { exact: true }).selectOption('guide-0-asset');
    await page.getByText(guideText(0), { exact: true }).waitFor();
    assert.equal(await page.locator('.ws-guideline-history').getByRole('button', { name: '사용 목록에서 제외' }).count(), 0);
    await page.getByRole('button', { name: '3 생성·검수·수정' }).click();
    await page.getByLabel('사용할 규칙', { exact: true }).selectOption('contract');
    await page.getByLabel('생성 방식', { exact: true }).selectOption('guided');
    await page.getByRole('button', { name: '기준안 + 변형 2개 만들기' }).click();
    await page.getByRole('heading', { name: '엄격 기준안 고정 비교' }).waitFor();
    assert.equal(await page.locator('.ws-comparison button').count(), 3);
    assert.match(await page.locator('.ws-comparison button').first().innerText(), /엄격 기준안.*고정/s);
    assert.match(await page.locator('.ws-comparison').innerText(), /실패/);
    await page.getByLabel('추가 변형 수', { exact: true }).selectOption('5');
    await page.getByRole('button', { name: '기준안 + 변형 5개 만들기' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.ws-comparison button').length === 6);
    await page.locator('.ws-comparison button').filter({ hasText: '시안 4' }).click();
    await page.getByText('시작 화면 변형은 사람의 검토가 필요합니다.', { exact: true }).waitFor();
    const consent = page.getByLabel('허용된 배치·강조 변형과 시작 화면 차이를 확인하고 수용합니다', { exact: true });
    const reviewed = page.getByLabel('이 라운드의 동작·시작 화면 비교 범위와 근거를 확인했습니다', { exact: true });
    await reviewed.check();
    assert.equal(await page.getByRole('button', { name: '라운드 1 시안 승인', exact: true }).isDisabled(), true);
    await consent.check();
    await page.getByRole('button', { name: '라운드 2 · 필수 검사 통과', exact: true }).click();
    assert.equal(await consent.isChecked(), false);
    await page.getByRole('button', { name: '라운드 1 · 필수 검사 통과 · 최종 선택', exact: true }).click();
    await reviewed.check(); await consent.check();
    await page.getByRole('button', { name: '라운드 1 시안 승인', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.ws-loop')?.dataset.complete === 'true');
    assert.equal(await page.locator('.ws-loop [data-stage="browser"]').getAttribute('data-state'), 'review');
    const sourceDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: 'React 소스 ZIP 내려받기', exact: true }).click();
    assert.equal((await sourceDownload).suggestedFilename(), 'react-source-r1.zip');
    await page.getByLabel('저장된 시안 비교', { exact: true }).selectOption('batch-1');
    await page.waitForFunction(() => document.querySelectorAll('.ws-comparison button').length === 3);
    await page.getByLabel('저장된 시안 비교', { exact: true }).selectOption('partial-batch');
    await page.getByText('엄격 기준안이 아직 준비되지 않았습니다.', { exact: false }).waitFor();
    assert.equal(await page.getByRole('button', { name: /엄격 기준안 · 준비 필요/ }).isDisabled(), true);
    assert.match(await page.locator('.ws-comparison').innerText(), /변형안 준비 실패/);
    await page.getByLabel('저장된 시안 비교', { exact: true }).selectOption('batch-2');
    await page.waitForFunction(() => document.querySelectorAll('.ws-comparison button').length === 6);
    await page.getByLabel('협업할 화면', { exact: false }).selectOption('review');
    await page.getByRole('button', { name: '의견', exact: true }).click();
    await page.getByLabel('의견 내용', { exact: true }).fill('선택한 화면의 강조를 확인해 주세요.');
    await page.getByRole('button', { name: '이 맥락에 의견 남기기' }).click();
    await page.getByText('선택한 화면의 강조를 확인해 주세요.', { exact: true }).waitFor();
    assert.deepEqual(discussions[0].anchor, { productId: 'product', guidelineId: 'guide-1', runId: 'batch-2-baseline', round: 1, pageId: 'review' });
    await page.getByRole('button', { name: '기획·지침', exact: true }).click();
    await page.getByLabel('조건 1', { exact: true }).fill('만 19세 이상만 가입');
    await page.getByRole('button', { name: '의견', exact: true }).click();
    await page.getByRole('button', { name: '기획·지침', exact: true }).click();
    assert.equal(await page.getByLabel('조건 1', { exact: true }).inputValue(), '만 19세 이상만 가입');
    await page.getByRole('button', { name: '상품 지침 저장', exact: true }).click();
    await page.getByLabel('이 초안을 새 기준으로 게시합니다', { exact: true }).check();
    await page.getByRole('button', { name: '버전 고정·지침 게시', exact: true }).click();
    await page.getByRole('heading', { name: '게시 지침 2차' }).waitFor();
    await page.getByLabel('게시 지침 버전', { exact: true }).selectOption('guide-1-asset');
    await page.getByText(guideText(1), { exact: true }).waitFor();
    assert.equal(await page.getByLabel('게시 지침 버전', { exact: true }).locator('option').count(), 3);
    assert.equal(product.conditions[0].id, 'condition-age');
    await page.getByRole('button', { name: '개발·내보내기', exact: true }).click();
    await page.getByText('게시 지침과 이 시안의 기준이 다르거나', { exact: false }).waitFor();
    assert.equal(await page.getByRole('button', { name: '승인 소스 재빌드·릴리스 검사' }).isDisabled(), true);
    await page.getByRole('button', { name: '의견', exact: true }).click();
    await page.getByText('선택한 화면의 강조를 확인해 주세요.', { exact: true }).waitFor();
    assert(calls.filter(call => call.target === '/comments' && call.method === 'GET').at(-1).query.guidelineId === 'guide-1');
    for (const width of [390, 768, 1440, 1920, 2560, 3440]) {
      await page.setViewportSize({ width, height: 1080 });
      await page.waitForTimeout(30);
      const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert(geometry.scroll <= geometry.width + 1, JSON.stringify(geometry));
      if (process.env.WORKSPACE_QA_DIR) {
        fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, `project-${width}.png`) });
      }
    }
    await page.getByText('참여자 관리', { exact: true }).click();
    await page.getByLabel('기존 사용자 검색', { exact: true }).fill('개발');
    await page.getByRole('button', { name: '사용자 찾기', exact: true }).click();
    await page.getByRole('button', { name: '개발 참여자 추가', exact: true }).click();
    await page.getByRole('button', { name: '참여 권한 저장', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.ws-project-bar')?.textContent.includes('관리자'));
    assert.equal(projects.a.members.person.displayName, '개발 참여자');
    assert.equal(calls.find(call => call.target === '/projects/a/members').scope, 'personal');
    await space.selectOption('c');
    await page.getByText('c-자료.html', { exact: true }).waitFor();
    assert.match(await page.locator('.ws-project-bar').innerText(), /기획/);
    await page.getByRole('button', { name: '3 생성·검수·수정' }).click();
    assert.equal(await page.getByRole('button', { name: '시안 1개 만들기' }).isDisabled(), true);
    await space.selectOption('revoked');
    await page.getByText('이 프로젝트의 현재 참여 권한을 확인할 수 없습니다.', { exact: true }).waitFor();
    assert.equal(await page.locator('.ws-work-area').count(), 0);
    await space.selectOption('');
    await page.getByText('personal-자료.html', { exact: true }).waitFor();
    assert.equal(calls.some(call => call.target === '/products' && call.scope === 'personal'), false);
    assert.equal(calls.some(call => call.target.startsWith('/assets/guide-') && call.method !== 'GET'), false);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    await page.evaluate(() => window.dispose());
    t.diagnostic('Offline scope switch/canonical roles, 3/6 guided comparison, revision/page comments, publish invalidation, release/Git metadata and six widths passed.');
  } finally { await browser.close(); }
});
