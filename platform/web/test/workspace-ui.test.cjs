// Offline integration: Playwright is a required project dependency. A missing
// dependency/browser fails this test. WORKSPACE_CHROMIUM may override its browser.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('designer workflow, private previews, exact rounds and responsive state', { timeout: 120_000 }, async t => {
  const root = path.resolve(__dirname, '..');
  const sha = bytes => createHash('sha256').update(bytes).digest('hex');
  const contractHash = sha(Buffer.from('approved-contract-fixture'));
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=', 'base64');
  const generated = number => Buffer.from(`<html lang="ko"><body><label>금액<input data-testid="amount"></label><button id="next" data-testid="next" style="background-color:#008485">확인</button><p data-testid="summary"></p><script>
document.getElementById('next').onclick=()=>document.querySelector('[data-testid=summary]').textContent=document.querySelector('input').value;
window.roundNumber=${number};try{window.parent.document.body.dataset.leaked='yes'}catch(e){window.opaque=true}
</script></body></html>`);
  const uploaded = Buffer.from('<html lang="ko"><body><p>외부 화면</p><label>금액<input id="amount" name="amount"></label><button id="next" style="background-color:#008485">확인</button><p id="summary"></p><script>window.uploadScriptRan=true;document.getElementById("next").onclick=()=>document.getElementById("summary").textContent=document.getElementById("amount").value;</script></body></html>');
  const guideText = '납입금액을 입력하고 다음 화면에서 같은 금액을 확인한다.';
  const contextWarnings = [
    '가이드.md: AI 문맥에는 추출 텍스트 앞부분만 포함됩니다. 적용 범위를 확인하세요.',
    '참고 이미지.png: 이미지 직접 참조는 OCR·크기·최대 5개 제한 때문에 제외되었습니다.',
  ];
  const blobs = new Map([['guide:original', Buffer.from(guideText)], ['image:original', png], ['image:preview:1', png]]);
  const assets = [
    { id: 'guide', version: 29, name: '가입 업무 가이드.md', size: Buffer.byteLength(guideText), sha256: sha(Buffer.from(guideText)),
      purpose: 'guide', uploadStatus: 'stored', parseStatus: 'complete', previews: [] },
    { id: 'image', version: 17, importRevision: 2, lineageId: 'image-lineage', parentId: 'previous-image',
      name: '기준 화면.png', size: png.length, sha256: sha(png),
      purpose: 'reference', uploadStatus: 'stored', parseStatus: 'complete', previews: [{ page: 1, mime: 'image/png', ocrStatus: 'partial' }] },
    { id: 'fig', version: 5, importRevision: 1, lineageId: 'fig', name: '원본.fig', size: 12, sha256: 'a'.repeat(64), purpose: 'archive',
      uploadStatus: 'stored', parseStatus: 'unsupported', previews: [] },
  ];
  const contracts = [], runs = [], jobs = new Map(), calls = [];
  let failedPart = false, failedVariant = false, failedProposal = false, sequence = 0, heldReport = null, holdFirstReport = false;
  const models = [{ id: 'astra', label: 'GPT-6 Astra', provider: 'OpenAI' }, { id: 'fable', label: 'Claude Fable 5.1', provider: 'Anthropic' }];
  let advertisedModels = models;
  const bundle = await build({
    stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import Studio from './src/studio/Studio';
const root=createRoot(document.getElementById('root'));root.render(<div className="flex h-full"><nav className="w-56 shrink-0"/><main className="app-main flex-1 overflow-y-auto"><div className="app-content"><Studio/></div></main></div>);window.dispose=()=>root.unmount();`,
      resolveDir: root, loader: 'tsx' },
    bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{ name: 'offline-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
      builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ loader: 'js', contents:
        `export const auth={token:'offline-test-token',studioToken:null};export const sock={
          request:async(action)=>action==='assets'?{assets:Array.from({length:23},(_,i)=>({name:'기존 자산 '+i,type:'palette',version:'1',scope:'shared'}))}:{},
          run:async()=>{throw Error('Legacy write forbidden')}};` }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, offline: true, acceptDownloads: true });
    const external = [], errors = [];
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.hostname !== 'offline.test') { external.push(url.href); return route.abort(); }
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html',
        body: '<html lang="ko"><head><title>작업실 오프라인 검증</title></head><body><div id="root"></div></body></html>' });
      assert.equal(route.request().headers().authorization, 'Bearer offline-test-token');
      const routePath = url.pathname.replace('/studio-api', '');
      const binary = routePath.includes('/parts/');
      const body = method === 'GET' ? null : binary ? route.request().postDataBuffer() : route.request().postDataJSON();
      calls.push({ routePath, method, body });
      if (routePath === '/config') return json({ models: advertisedModels, defaultModel: 'astra', maxFileBytes: 52428800, chunkBytes: 64,
        extensions: ['html', 'htm', 'css', 'png', 'jpg', 'jpeg', 'svg', 'pdf', 'fig', 'md', 'markdown', 'txt', 'json'] });
      if (routePath === '/assets' && method === 'GET') return json({ assets });
      if (routePath === '/contracts' && method === 'GET') return json({ contracts });
      if (routePath === '/runs' && method === 'GET') return json({ runs });
      if (routePath === '/assets' && method === 'POST') {
        const asset = { ...body, id: 'upload', version: 1, importRevision: 1, lineageId: 'upload',
          uploadStatus: 'uploading', parseStatus: 'pending', previews: [] };
        assets.push(asset); return json({ asset, chunkBytes: 64 });
      }
      if (binary) {
        if (routePath.endsWith('/1') && !failedPart) { failedPart = true; return json({ error: '전송을 다시 시도하세요.' }, 503); }
        return json({ ok: true });
      }
      if (routePath === '/assets/upload/complete') {
        const asset = assets.find(value => value.id === 'upload');
        asset.version = 29; asset.uploadStatus = 'stored'; asset.parseStatus = 'complete'; asset.previews = [{ page: 1, mime: 'text/html' }];
        blobs.set('upload:original', uploaded); blobs.set('upload:preview:1', uploaded);
        const job = { id: 'finalize-upload', task: 'finalize', status: 'queued' }; jobs.set(job.id, { ...job, result: { assetId: 'upload' } });
        return json({ asset, job }, 202);
      }
      if (routePath === '/contracts/propose') {
        if (!failedProposal) { failedProposal = true; return json({ error: '제안 요청을 다시 시도하세요.' }, 503); }
        const contract = { id: 'c1', version: 1, status: 'draft', schemaVersion: 1, title: '입력 금액 확인',
          brief: body.brief, assetIds: body.assetIds, viewport: { width: 390, height: 844 }, unresolved: [],
          bindings: { amount: '#amount', next: '#next', summary: '#summary' }, rules: [{
            id: 'R1', title: '입력 금액이 확인 화면에 나타난다', required: true,
            source: { kind: 'explicit', assetId: 'guide', quote: guideText },
            steps: [{ action: 'fill', target: 'amount', targetLabel: '납입금액', value: '10000' },
              { action: 'click', target: 'next', targetLabel: '다음' },
              { action: 'expectText', target: 'summary', targetLabel: '확인 금액', value: '10000', match: 'equals' }],
          }] };
        contracts.push(contract); const job = { id: 'proposal', task: 'propose', status: 'queued' };
        jobs.set(job.id, { ...job, result: { contractId: 'c1' } }); return json({ job }, 202);
      }
      if (routePath === '/contracts/c1/approve') {
        assert.equal(body.version, contracts[0].version);
        contracts[0] = { ...contracts[0], version: body.version + 1, status: 'approved' };
        return json({ contract: contracts[0] });
      }
      if (routePath === '/contracts/c1') {
        if (method === 'PUT') {
          assert.equal(body.version, contracts[0].version);
          assert.deepEqual(body.rules[0].steps.at(-1).action, 'expectStyle');
          assert.equal(body.rules[0].steps.at(-1).property, 'backgroundColor');
          assert.equal(body.rules[0].steps.at(-1).value, '#008485');
          assert.deepEqual(body.bindings, { amount: '[name="amount"]', next: '#next', summary: '#summary' });
          contracts[0] = { ...contracts[0], ...body, version: body.version + 1, status: 'draft' };
        }
        return json({ contract: contracts[0] });
      }
      if (routePath === '/runs' && method === 'POST') {
        if (body.mode === 'verify') {
          assert.equal(body.sourceAssetId, 'upload'); assert.equal(body.maxRounds, 1); assert.equal(body.contractVersion, 3);
          assert.equal(body.model, undefined); assert.equal(body.variant, undefined);
          const run = { ...body, id: 'verify-run', version: 1, mode: 'verify', model: 'no-inference', status: 'completed',
            contractHash, contract: contracts[0], assetSnapshots: [{ id: 'upload', name: '외부 시안.html', sha256: sha(uploaded) }],
            bestRound: 1, functionalStatus: 'pass', visualStatus: body.referenceAssetId ? 'pass' : 'not-run', contextWarnings: [], rounds: [{
              number: 1, passed: true, artifactSha256: sha(uploaded), hasHtml: true, hasScreenshot: true, hasReport: true, hasDiff: false,
              functionalStatus: 'pass', visualStatus: body.referenceAssetId ? 'pass' : 'not-run', checks: { pass: 1, fail: 0, incomplete: 0 }, blockingFindings: [],
            }] };
          runs.unshift(run);
          const job = { id: 'job-verify', task: 'run', status: 'queued' }; jobs.set(job.id, { ...job, result: { runId: run.id, bestRound: 1, passed: true } });
          return json({ job, run }, 202);
        }
        if (body.variant === 'dense' && !failedVariant) { failedVariant = true; return json({ error: '잠시 후 다시 시도하세요.' }, 503); }
        const id = `run-${++sequence}`;
        const roundList = [1, 2].map(number => ({ number, passed: number === 2, artifactSha256: sha(generated(number)),
          hasHtml: true, hasReport: true, hasScreenshot: number === 2, hasDiff: false,
          functionalStatus: number === 2 ? 'pass' : 'fail', visualStatus: body.referenceAssetId ? 'pass' : 'not-run',
          checks: { pass: number === 2 ? 1 : 0, fail: number === 2 ? 0 : 1, incomplete: 0 },
          blockingFindings: number === 2 ? [] : ['금액 전달 실패'] }));
        const run = { ...body, id, version: 1, status: 'completed', contractHash,
          contract: contracts[0], bestRound: 2, rounds: roundList, functionalStatus: 'pass', visualStatus: body.referenceAssetId ? 'pass' : 'not-run', contextWarnings };
        runs.unshift(run);
        const job = { id: `job-${id}`, task: 'run', status: 'queued' }; jobs.set(job.id, { ...job, result: { runId: id, bestRound: 2, passed: true } });
        return json({ job, run }, 202);
      }
      if (routePath.startsWith('/jobs/')) {
        const job = jobs.get(routePath.split('/')[2]); assert(job);
        job.polls = (job.polls || 0) + 1;
        return json({ job: { ...job, status: job.polls > 1 ? 'completed' : 'running', progress: job.polls > 1 ? 100 : 40 } });
      }
      if (routePath.startsWith('/runs/') && routePath.endsWith('/approve')) {
        const run = runs.find(value => value.id === routePath.split('/')[2]);
        assert.equal(body.round, 2); assert.equal(body.artifactSha256, sha(generated(2))); assert.equal(body.contractVersion, 3);
        run.approval = { ...body, contractHash: run.contractHash, actor: 'fixture-reviewer', at: Date.now() }; return json({ run });
      }
      if (routePath.includes('/blob')) {
        const [, kind, id] = routePath.split('/');
        const blobKind = url.searchParams.get('kind'), number = Number(url.searchParams.get('round'));
        const sourceRun = kind === 'runs' ? runs.find(run => run.id === id) : null;
        const isVerify = sourceRun?.mode === 'verify';
        let data, mime = 'application/octet-stream';
        if (kind === 'assets') {
          data = blobs.get(`${id}:${blobKind}${blobKind === 'preview' ? ':' + url.searchParams.get('page') : ''}`);
          if (id === 'image' && blobKind === 'preview') mime = 'image/png';
        } else if (blobKind === 'html') data = isVerify ? uploaded : generated(number);
        else if (blobKind === 'screenshot') { data = png; mime = 'image/png'; }
        else data = Buffer.from(JSON.stringify({ passed: isVerify || number === 2, functionalStatus: isVerify || number === 2 ? 'pass' : 'fail',
          artifactSha256: sha(isVerify ? uploaded : generated(number)), contractVersion: sourceRun.contractVersion, contractHash: sourceRun.contractHash,
          model: isVerify ? 'no-inference' : 'fable',
          accessibility: { status: 'pass', violations: [] }, visual: { status: sourceRun.referenceAssetId ? 'pass' : 'not-run' },
          networkRequests: [], consoleErrors: [],
          blockingFindings: isVerify || number === 2 ? [] : ['금액 전달 실패'],
          checks: [{ caseId: 'R1', required: true, title: isVerify ? '원본 HTML 검사 근거' : `라운드 ${number}의 금액 전달 근거`,
            status: isVerify || number === 2 ? 'pass' : 'fail', evidence: '한글 근거 '.repeat(80) }] }));
        assert(data, `Missing fixture ${routePath}`);
        const offset = Number(url.searchParams.get('offset')), part = data.subarray(offset, offset + 400);
        const respond = () => route.fulfill({ body: part, headers: { 'Content-Type': 'application/octet-stream',
          'X-Content-Type': mime, 'X-Total-Size': String(data.length), 'X-Chunk-Size': String(part.length), 'X-SHA256': sha(data) } }).catch(() => {});
        if (holdFirstReport && blobKind === 'report' && number === 1 && offset === 0) { heldReport = respond; return; }
        return respond();
      }
      if (routePath.startsWith('/assets/')) return json({ asset: assets.find(value => value.id === routePath.split('/')[2]),
        analysis: { text: guideText, warnings: [] } });
      if (routePath.startsWith('/runs/')) return json({ run: runs.find(value => value.id === routePath.split('/')[2]) });
      throw new Error(`Unexpected route ${method} ${routePath}`);
    });
    const page = await context.newPage(); page.setDefaultTimeout(12_000);
    page.on('pageerror', error => { errors.push(String(error)); t.diagnostic(String(error)); });
    page.on('dialog', dialog => dialog.accept());
    await page.goto('https://offline.test/');
    await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'dist/assets', fs.readdirSync(path.join(root, 'dist/assets')).find(p => p.endsWith('.css'))), 'utf8') +
      fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
    await page.evaluate(() => {
      window.objectURLs = new Set(); const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
      URL.createObjectURL = blob => { const value = create(blob); window.objectURLs.add(value); return value; };
      URL.revokeObjectURL = value => { window.objectURLs.delete(value); revoke(value); };
    });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.getByRole('heading', { name: '파일·스킬 작업실', exact: true }).waitFor();
    const summary = page.locator('.studio-summary');
    assert.equal(await summary.isVisible(), true, 'The default workspace must have its own workflow summary');
    assert.match(await summary.innerText(), /외부.*파일.*승인.*규칙.*실행 가능한 시안/s);
    assert.match(await summary.innerText(), /실제 금융 API.*별도/s);
    assert.equal(await page.getByLabel('기존 Studio 집계', { exact: true }).count(), 0);
    await page.locator('.studio-tabs').getByRole('button', { name: /시안 갤러리/ }).click();
    await page.getByLabel('기존 Studio 집계', { exact: true }).getByText('23', { exact: true }).waitFor();
    assert.match(await summary.innerText(), /정적 시안/);
    assert.match(await summary.innerText(), /픽셀 일치는 아직 검증하지 않습니다/);
    await page.locator('.studio-tabs').getByRole('button', { name: '파일·스킬 작업실', exact: true }).click();
    assert.equal(await page.getByLabel('기존 Studio 집계', { exact: true }).count(), 0);
    assert(!((await summary.innerText()).includes('정적 시안')));
    const capture = async name => {
      if (!process.env.WORKSPACE_QA_DIR) return;
      fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
      for (const width of [900, 1440, 3440]) {
        await page.setViewportSize({ width, height: 1080 });
        await page.locator('main').evaluate(element => { element.scrollTop = 0; });
        assert.equal(await page.locator('main').evaluate(element => element.scrollWidth > element.clientWidth), false);
        await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, `${name}-${width}.png`) });
      }
      await page.setViewportSize({ width: 1440, height: 900 });
    };
    await page.getByLabel('반입할 파일').waitFor({ state: 'attached' }).catch(async error => {
      t.diagnostic(await page.locator('body').innerText()); throw error;
    });
    const guideCard = page.locator('.ws-asset').filter({ hasText: '가입 업무 가이드.md' });
    assert.match(await guideCard.innerText(), /반입 v1/);
    assert(!((await guideCard.innerText()).includes('29')));
    assert.match(await page.locator('.ws-asset').filter({ hasText: '기준 화면.png' }).innerText(), /반입 v2/);
    assert.match(await page.locator('.ws-asset').filter({ hasText: '원본.fig' }).innerText(), /반입 v1/);
    assert((await page.getByLabel('반입할 파일').getAttribute('accept')).split(',').includes('.css'));
    await page.getByText('HTML에 연결된 CSS 파일도 함께 추가하세요. 스타일 가이드 자료로 보관합니다.', { exact: true }).waitFor();
    await page.getByLabel('반입할 파일').setInputFiles({ name: '외부 시안.html', mimeType: 'text/html', buffer: uploaded });
    await page.getByRole('button', { name: '비공개 보관하기', exact: true }).click();
    await page.getByRole('button', { name: '전송 다시 시도', exact: true }).waitFor();
    await page.getByRole('button', { name: '전송 다시 시도', exact: true }).click();
    await page.getByLabel('외부 시안.html', { exact: true }).waitFor();
    assert.equal(calls.filter(c => c.routePath === '/assets' && c.method === 'POST').length, 1);
    assert.equal(calls.find(c => c.routePath === '/assets' && c.method === 'POST').body.purpose, 'prototype');
    await page.getByLabel('가입 업무 가이드.md', { exact: true }).check();
    await page.getByLabel('외부 시안.html', { exact: true }).check();
    await page.getByLabel('기준 화면.png', { exact: true }).check();
    await page.locator('.ws-asset').filter({ hasText: '기준 화면.png' }).getByRole('button', { name: '파일 확인', exact: true }).click();
    await page.locator('.ws-inspector').getByRole('button', { name: '사용 목록에서 제외', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '보관 처리', exact: true }).count(), 0);
    await page.getByText('1페이지 · 일부만 문자 인식', { exact: true }).waitFor();
    await page.getByText('문자 인식 상태는 AI 반영 여부나 시작 화면 비교 결과와 별개입니다.', { exact: true }).waitFor();
    await capture('files');
    const uploadedCard = page.locator('.ws-asset').filter({ hasText: '외부 시안.html' });
    assert.match(await uploadedCard.innerText(), /반입 v1/);
    assert(!((await uploadedCard.innerText()).includes('29')));
    await uploadedCard.getByRole('button', { name: '파일 확인', exact: true }).click();
    await page.locator('.ws-inspector').getByText('반입 v1', { exact: true }).waitFor();
    await page.frameLocator('iframe[title="외부 시안.html 업로드 파일 미리보기"]').getByText('외부 화면').waitFor();
    assert.equal(await page.locator('iframe[title="외부 시안.html 업로드 파일 미리보기"]').getAttribute('sandbox'), '');
    assert.equal(await page.frames().find(f => f.parentFrame() && f.url() === 'about:srcdoc').evaluate(() => !!window.uploadScriptRan), false);
    const originalDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: '원본 내려받기', exact: true }).click();
    assert.equal((await originalDownload).suggestedFilename(), '외부 시안.html');
    await page.getByRole('button', { name: '선택한 3개로 규칙 만들기', exact: true }).click();
    await page.getByLabel('만들 화면 설명', { exact: true }).fill('가입 금액을 다음 화면에 전달하세요.');
    await page.getByRole('button', { name: '선택한 파일로 규칙 제안받기', exact: true }).click();
    await page.getByText('제안 요청을 다시 시도하세요.', { exact: true }).waitFor();
    await page.getByRole('button', { name: '선택한 파일로 규칙 제안받기', exact: true }).click();
    // Wait for the proposal to finish before editing its rule.
    await page.getByText('AI 제안은 아직 승인되지 않았습니다.', { exact: false }).waitFor();
    assert.equal(new Set(calls.filter(c => c.routePath === '/contracts/propose').map(c => c.body.requestId)).size, 1);
    await page.getByLabel('규칙 1', { exact: true }).fill('납입금액 전달 확인');
    await page.getByRole('button', { name: '확인 단계 추가', exact: true }).click();
    const styleStep = page.locator('.ws-step').last();
    await styleStep.getByLabel(/^동작·확인/).selectOption('expectStyle');
    await styleStep.getByLabel('화면 요소', { exact: true }).fill('다음');
    await styleStep.getByLabel(/^스타일 속성/).selectOption('backgroundColor');
    await styleStep.getByLabel('기대 결과', { exact: true }).fill('#008485');
    await page.getByText('고급 설정 · 원본 HTML 요소 연결', { exact: true }).click();
    await page.getByLabel('납입금액 CSS 선택자', { exact: true }).fill('[name="amount"]');
    await page.getByRole('button', { name: '규칙 저장', exact: true }).click();
    await page.getByLabel('이 버전의 근거와 모든 확인 단계를 검토했습니다', { exact: true }).check();
    await page.getByRole('button', { name: '버전 2 규칙 승인', exact: true }).click();
    await page.getByText('버전 3의 규칙을 승인했습니다.', { exact: false }).waitFor();
    await capture('rules');
    await page.getByLabel('규칙 1', { exact: true }).fill('미저장 변경');
    await page.getByRole('button', { name: '3 생성·검수·수정', exact: true }).click();
    const loop = page.locator('.ws-loop');
    assert.equal(await loop.isVisible(), true);
    assert.equal(await loop.locator('[data-stage]').count(), 5);
    assert.equal(await loop.locator('[aria-current]').count(), 0);
    assert.equal(await loop.locator('[data-stage="browser"]').getAttribute('data-state'), 'pending');
    assert.equal(await page.getByRole('button', { name: '시안 1개 만들기', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '반입 HTML 검사', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '반입 HTML 검사', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '2 규칙 확인·승인', exact: true }).click();
    await page.getByLabel('규칙 1', { exact: true }).fill('납입금액 전달 확인');
    await page.getByRole('button', { name: '3 생성·검수·수정', exact: true }).click();
    await page.locator('.ws-runs').getByLabel('AI 모델', { exact: true }).selectOption('fable').catch(async error => {
      t.diagnostic(JSON.stringify(await page.locator('.ws-runs label').allTextContents())); throw error;
    });
    await page.getByText('반복 횟수와 시작 화면 비교 설정', { exact: true }).click();
    const referenceControl = page.locator('.ws-runs').getByLabel(/^시작 화면 비교 기준/);
    await referenceControl.selectOption('image');
    await page.getByRole('button', { name: '내 작업 새로 조회', exact: true }).click();
    await page.getByRole('button', { name: '내 작업 새로 조회', exact: true }).waitFor();
    assert.equal(await referenceControl.inputValue(), 'image', 'A metadata refresh must preserve the selected visual reference');
    await page.getByRole('button', { name: '방향이 다른 3개 만들기', exact: true }).click();
    await page.getByRole('button', { name: '같은 요청 다시 시도', exact: true }).click();
    await page.getByRole('button', { name: '기본 구성 결과 보기', exact: true }).click();
    await page.getByText('라운드 2의 금액 전달 근거', { exact: true }).waitFor();
    assert.equal(await loop.locator('[data-stage="browser"]').getAttribute('data-state'), 'passed');
    assert.equal(await loop.locator('[data-stage="evidence"]').getAttribute('data-state'), 'recorded');
    assert.equal(await loop.locator('[data-stage="approval"]').getAttribute('data-state'), 'pending');
    assert.equal(await loop.getAttribute('data-complete'), 'false');
    for (const warning of contextWarnings) await page.locator('.ws-context-warnings').getByText(warning, { exact: true }).waitFor();
    await page.getByText('통과 1 · 실패 0 · 미판정 0', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '시작 화면 차이 보기', exact: true }).count(), 0);
    const generationRequests = calls.filter(c => c.routePath === '/runs' && c.method === 'POST');
    assert.equal(new Set(generationRequests.map(c => c.body.requestId)).size, 3);
    assert(generationRequests.every(c => c.body.contractVersion === 3 && c.body.model === 'fable'));
    assert(generationRequests.every(c => c.body.referenceAssetId === 'image' && c.body.referencePage === 1));
    const generatedFrame = page.locator('iframe[title="생성 시안 라운드 2"]');
    assert.equal(await generatedFrame.getAttribute('sandbox'), 'allow-scripts');
    const frame = page.frameLocator('iframe[title="생성 시안 라운드 2"]');
    await frame.getByLabel('금액', { exact: true }).fill('12345');
    await frame.getByRole('button', { name: '확인', exact: true }).click();
    await frame.getByText('12345', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.body.dataset.leaked), undefined);
    await page.getByRole('button', { name: '라운드 1 · 확인 필요', exact: true }).click();
    await page.getByText('통과 0 · 실패 1 · 미판정 0', { exact: true }).waitFor();
    assert.equal(await loop.locator('[data-stage="browser"]').getAttribute('data-state'), 'failed');
    assert.equal(await loop.locator('.ws-loop-return').getAttribute('data-contract-version'), '3');
    assert.equal(await loop.locator('.ws-loop-return').getAttribute('data-contract-hash'), contractHash);
    assert.equal(await page.getByRole('button', { name: '검수 시작 화면', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '라운드 1 시안 승인', exact: true }).isDisabled(), true);
    await page.getByLabel('라운드 1 수정 지시', { exact: true }).fill('입력한 금액을 보존하세요.');
    for (const width of [900, 1440, 1920, 2560, 3440]) {
      await page.setViewportSize({ width, height: 1080 });
      assert.equal(await page.getByLabel('라운드 1 수정 지시', { exact: true }).inputValue(), '입력한 금액을 보존하세요.');
      assert.equal(await page.locator('main').evaluate(e => e.scrollWidth > e.clientWidth), false);
      assert.equal(await loop.evaluate(e => e.scrollWidth > e.clientWidth), false);
      assert.equal(await loop.locator('[data-stage]').evaluateAll(nodes => nodes.some(node => node.scrollWidth > node.clientWidth)), false);
      if (process.env.WORKSPACE_QA_DIR) await loop.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, `loop-failed-${width}.png`) });
    }
    await page.getByRole('button', { name: '보고 있는 라운드 1 수정·재검수', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.ws-attempts').textContent.includes('라운드 1 수정'));
    const revisionRequest = calls.filter(c => c.routePath === '/runs' && c.body?.baseRunId).at(-1);
    assert.equal(revisionRequest.body.baseRunId, 'run-1'); assert.equal(revisionRequest.body.baseRound, 1);
    assert.equal(revisionRequest.body.contractVersion, 3);
    assert.equal(revisionRequest.body.referenceAssetId, 'image');
    await page.getByRole('button', { name: '라운드 2 · 필수 검사 통과 · 최종 선택', exact: true }).click();
    await page.getByLabel('이 라운드의 동작·시작 화면 비교 범위와 근거를 확인했습니다', { exact: true }).check();
    assert.equal(await loop.locator('[data-stage="approval"]').getAttribute('data-state'), 'pending');
    await page.getByRole('button', { name: '라운드 2 시안 승인', exact: true }).click();
    await page.getByText('이 라운드 승인됨', { exact: true }).waitFor();
    assert.equal(await loop.locator('[data-stage="approval"]').getAttribute('data-state'), 'approved');
    assert.equal(await loop.getAttribute('data-complete'), 'true');
    if (process.env.WORKSPACE_QA_DIR) await loop.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'loop-approved.png') });
    // Hold an old report after selecting a newer round.
    holdFirstReport = true;
    await page.getByRole('button', { name: '라운드 1 · 확인 필요', exact: true }).click();
    assert.equal(await loop.locator('[data-stage="approval"]').getAttribute('data-state'), 'pending');
    assert.equal(await loop.getAttribute('data-complete'), 'false');
    await page.waitForTimeout(100);
    await page.getByRole('button', { name: '라운드 2 · 필수 검사 통과 · 최종 선택', exact: true }).click();
    if (heldReport) await heldReport();
    await page.getByText('라운드 2의 금액 전달 근거', { exact: true }).waitFor();
    assert.equal(await page.getByText('라운드 1의 금액 전달 근거', { exact: true }).count(), 0);
    await page.getByRole('button', { name: '검수 시작 화면', exact: true }).click();
    await page.getByAltText('검수 시작 화면', { exact: true }).waitFor();
    if (process.env.WORKSPACE_QA_DIR) {
      fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
      for (const [width, name] of [[1440, 'results-1440'], [3440, 'results-3440']]) {
        await page.setViewportSize({ width, height: 1080 });
        await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, name + '.png'), fullPage: false });
      }
    }
    holdFirstReport = false;
    advertisedModels = [];
    await page.getByRole('button', { name: '내 작업 새로 조회', exact: true }).click();
    await page.getByRole('button', { name: '내 작업 새로 조회', exact: true }).waitFor();
    const sourceControl = page.getByLabel(/^검사할 반입 HTML/);
    await sourceControl.selectOption('upload');
    assert.equal(await sourceControl.locator('option').count(), 2); // placeholder + selected HTML; no PNG/FIG/CSS.
    assert.equal(await page.getByRole('button', { name: '시안 1개 만들기', exact: true }).isDisabled(), true);
    const beforeVerify = calls.filter(call => call.routePath === '/runs' && call.method === 'POST').length;
    await page.getByRole('button', { name: '반입 HTML 검사', exact: true }).click();
    await page.getByRole('heading', { name: '원본 HTML 검사', exact: true }).waitFor();
    await page.getByText('원본 HTML 검사 근거', { exact: true }).waitFor();
    assert.match(await loop.locator('[data-stage="artifact"]').innerText(), /반입 HTML/);
    assert.equal(await loop.locator('[data-stage="approval"]').getAttribute('data-state'), 'pending');
    assert.equal(calls.filter(call => call.routePath === '/runs' && call.method === 'POST').length, beforeVerify + 1);
    assert.equal(calls.filter(call => call.routePath === '/runs' && call.body?.mode === 'verify').length, 1);
    const verifiedFrame = page.frameLocator('iframe[title="원본 HTML 검사 라운드 1"]');
    await verifiedFrame.getByLabel('금액', { exact: true }).fill('23456');
    await verifiedFrame.getByRole('button', { name: '확인', exact: true }).click();
    await verifiedFrame.getByText('23456', { exact: true }).waitFor();
    assert.equal(await verifiedFrame.locator('[data-testid]').count(), 0);
    assert.equal(await page.locator('.ws-run-choice.is-selected').getByText('원본 HTML 검사', { exact: true }).count(), 1);
    await page.evaluate(() => window.dispose());
    assert.equal(await page.evaluate(() => window.objectURLs.size), 0);
    const polls = calls.filter(c => c.routePath.startsWith('/jobs/')).length;
    await page.waitForTimeout(1700);
    assert.equal(calls.filter(c => c.routePath.startsWith('/jobs/')).length, polls);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    t.diagnostic('Offline upload retry, rules/CAS approval, 3 variants, exact-round revise/approve, opaque iframe, multi-chunk evidence, resize and cleanup passed.');
  } finally { await browser.close(); }
});
