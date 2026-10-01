const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { chromium } = require('playwright');

async function mount(t, entry, handler) {
  const root = path.resolve(__dirname, '..');
  const bundle = await build({ stdin: { contents: entry, resolveDir: root, loader: 'tsx' },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', plugins: [{ name: 'offline-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'auth', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const auth={token:"offline-canvas"};' }));
    } }] });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking'] });
  t.after(() => browser.close());
  const context = await browser.newContext({ offline: true, viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://canvas.invalid') throw new Error('Unexpected external request');
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><body><div id="root"></div></body></html>' });
    return handler(route, url.pathname.replace('/studio-api', ''));
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  await page.goto('https://canvas.invalid/');
  await page.addStyleTag({ content: 'body{margin:0;padding:32px;background:#fbfcfb;font-family:Arial,sans-serif}' + fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  return page;
}

test('canvas selection preserves simulated state, binds refinement to the viewed round and clears stale selection', { timeout: 30000 }, async t => {
  const contract = { id: 'criteria', title: '환전 신청', brief: '받을 금액과 신청 내용을 확인합니다.', version: 2, status: 'approved',
    assetIds: [], viewport: { width: 390, height: 844 }, unresolved: [], rules: [{ id: 'R1', title: '신청 확인', required: true,
      source: { kind: 'manual' }, steps: [{ action: 'expectVisible', target: 'next', targetLabel: '신청 버튼', value: true }] }] };
  const rounds = [1, 2].map(number => ({ number, passed: false, artifactSha256: String(number).repeat(64), hasHtml: true,
    hasReport: false, hasScreenshot: false, functionalStatus: 'incomplete', visualStatus: 'not-run' }));
  const run = { id: 'draft', status: 'needs_changes', version: 1, contractId: contract.id, contractVersion: contract.version,
    outputType: 'react', model: 'fable', bestRound: 2, contract, rounds };
  const submitted = [];
  let releaseRevision, receivedRevision;
  const received = new Promise(resolve => { receivedRevision = resolve; });
  const release = new Promise(resolve => { releaseRevision = resolve; });
  const html = Buffer.from(`<html lang="ko"><head><style>body{font-family:Arial,sans-serif;padding:24px;color:#173b36;background:white}h1{font-size:25px}input{padding:14px;width:85%;border:1px solid #bdd4cc;border-radius:8px}button{background:#008485;color:white;border:0;border-radius:10px;padding:16px;width:100%;margin-top:24px}.card{background:#f2f7f5;padding:20px;margin:24px 0;border-radius:12px}</style></head><body>
    <img id="asset" alt="반입 이미지" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=">
    <h1>환전 신청</h1><p>받을 금액을 먼저 확인하세요.</p><div class="card"><label>환전 금액<input id="amount" value="1000"></label><p>환율 우대 90%</p></div>
    <button id="next" data-studio-outline="application-owned" onpointerdown="document.querySelector('#result').textContent='pointer activated'"
      onclick="document.querySelector('#result').textContent=document.querySelector('input').value"><svg width="18" height="18" viewBox="0 0 18 18"><path id="icon" d="M1 1h16v16H1z" fill="white"/></svg>지금 환전하기</button><p id="result"></p>
    <script>try{top.document.body.dataset.escaped='yes'}catch{}</script></body></html>`);
  const page = await mount(t, `import React from 'react';import {createRoot} from 'react-dom/client';import RunsPanel from './src/workspace/RunsPanel';
    const contract=${JSON.stringify(contract)},run=${JSON.stringify(run)};
    createRoot(document.getElementById('root')).render(<div className="designer-workspace"><RunsPanel
      config={{models:[{id:'fable',label:'Claude Fable 5.1'}],defaultModel:'fable'}} assets={[]} contracts={[contract]} runs={[run]}
      preferredContract="criteria" initialRunId="draft" initialRound={2} editing={{id:'',dirty:false}} refresh={()=>{}}
      onCriteria={()=>{}} onAssets={()=>{}} /></div>);`, (route, target) => {
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (target === '/batches') return json({ batches: [] });
    if (target === '/contracts/criteria') return json({ contract });
    if (target === '/runs/draft') return json({ run });
    if (target === '/runs/draft/blob') return route.fulfill({ body: html, headers: {
      'Content-Type': 'application/octet-stream', 'X-Total-Size': String(html.length), 'X-Chunk-Size': String(html.length),
      'X-SHA256': createHash('sha256').update(html).digest('hex'),
    } });
    if (target === '/runs' && route.request().method() === 'POST') {
      submitted.push(route.request().postDataJSON()); receivedRevision();
      return release.then(() => json({ run: { ...run, id: 'revision' } }));
    }
    throw new Error('Unexpected path ' + target);
  });
  const content = () => page.frameLocator('iframe[title="생성 시안 라운드 2"]').frameLocator('[data-workspace-preview-content]');
  await content().getByLabel('환전 금액').fill('2500');
  assert.equal(await content().locator('#asset').evaluate(image => image.complete && image.naturalWidth), 1,
    'The wrapper CSP must preserve allowed data images in the inner srcdoc');
  await page.getByRole('button', { name: '요소 선택', exact: true }).click();
  await content().locator('#icon').click();
  await page.locator('.ws-canvas-selection').getByText('button · 지금 환전하기', { exact: true }).waitFor();
  assert.equal(await content().locator('#result').innerText(), '', 'Selection does not activate the simulated button');
  assert.equal(await content().locator('#next').getAttribute('data-studio-outline'), 'application-owned');
  await content().locator('#next').focus();
  await content().locator('#next').press('Enter');
  assert.equal(await content().locator('#result').innerText(), '', 'Keyboard selection does not submit the simulated form');
  assert.equal(await content().getByLabel('환전 금액').inputValue(), '2500');
  await page.getByRole('button', { name: '전체 폭 보기', exact: true }).click();
  assert.equal(await content().getByLabel('환전 금액').inputValue(), '2500', 'Width changes do not reload the preview');
  await page.getByRole('button', { name: '전체 폭 보기', exact: true }).click();
  const instruction = page.getByLabel('라운드 2 수정 지시', { exact: true });
  await instruction.fill('버튼의 여백을 넓혀 주세요.');
  await page.getByRole('button', { name: '라운드 1 · 확인 필요', exact: true }).click();
  await page.locator('.ws-canvas-selection').getByText('전체 화면 수정', { exact: true }).waitFor();
  await page.getByLabel('라운드 1 수정 지시', { exact: true }).fill('첫 라운드의 제목을 확인해 주세요.');
  await page.getByRole('button', { name: '라운드 2 · 확인 필요 · 최종 선택', exact: true }).click();
  assert.equal(await instruction.inputValue(), '버튼의 여백을 넓혀 주세요.', 'Returning to a round restores its unsent revision');
  await page.locator('.ws-canvas-selection').getByText('button · 지금 환전하기', { exact: true }).waitFor();
  assert.equal(await page.locator('iframe[title="생성 시안 라운드 2"]').getAttribute('sandbox'), 'allow-scripts');
  assert.equal(await page.evaluate(() => document.body.dataset.escaped), undefined);
  // Neither the host nor another frame is the trusted relay for selection hints.
  await page.evaluate(() => window.postMessage({ type: 'studio-element', channel: 'forged', selection: { selector: '#other', label: 'forged' } }, '*'));
  assert.match(await page.locator('.ws-canvas-selection').innerText(), /지금 환전하기/);
  if (process.env.WORKSPACE_QA_DIR) {
    fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'canvas-desktop.png'), fullPage: true });
  }
  await page.getByRole('button', { name: '보고 있는 라운드 2 수정·재검수', exact: true }).click();
  await received;
  await instruction.fill('접수 중 새로 작성한 수정 지시를 유지해 주세요.');
  releaseRevision();
  await page.getByRole('button', { name: '라운드 2 수정 결과 보기', exact: true }).waitFor();
  assert.equal(await instruction.inputValue(), '접수 중 새로 작성한 수정 지시를 유지해 주세요.', 'Acceptance must not erase newer typing');
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].baseRunId, 'draft'); assert.equal(submitted[0].baseRound, 2);
  assert.equal(submitted[0].contractVersion, 2); assert.equal(submitted[0].model, 'fable');
  assert.match(submitted[0].instruction, /요소 위치: #next/);
  assert.match(submitted[0].instruction, /버튼의 여백을 넓혀 주세요/);
  await page.getByRole('button', { name: '라운드 1 · 확인 필요', exact: true }).click();
  await page.locator('.ws-canvas-selection').getByText('전체 화면 수정', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('라운드 1 수정 지시', { exact: true }).inputValue(), '첫 라운드의 제목을 확인해 주세요.');
  await page.getByText('검수 근거·승인', { exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '라운드 1 시안 승인' }).isDisabled(), true);
  await page.getByText('검수 근거·승인', { exact: true }).click();
  for (const width of [900, 390]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  }
  if (process.env.WORKSPACE_QA_DIR) await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'canvas-mobile.png'), fullPage: true });
});

test('request-first entry preserves model and source context, retries idempotently, and opens criteria without approving or generating', { timeout: 30000 }, async t => {
  const calls = []; let failed = false, pollFailed = false;
  const page = await mount(t, `import React from 'react';import {createRoot} from 'react-dom/client';import CanvasRequest from './src/workspace/CanvasRequest';
    function App(){const [opened,setOpened]=React.useState('');return <div className="designer-workspace"><CanvasRequest model="fable" available
      assetIds={['private-guide']} guideRefs={[]} onDirty={value=>{window.requestDirty=value}} onReady={setOpened}/><output>{opened}</output></div>}
    createRoot(document.getElementById('root')).render(<App/>);`, (route, target) => {
    if (route.request().method() === 'POST') calls.push({ target, payload: route.request().postDataJSON() });
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (target === '/contracts/propose') {
      if (!failed) { failed = true; return json({ error: '일시 오류입니다. 다시 시도하세요.' }, 503); }
      return json({ job: { id: 'prepare', task: 'propose', status: 'queued' } });
    }
    if (target === '/jobs/prepare') {
      if (!pollFailed) { pollFailed = true; return json({ error: '진행 조회 연결이 끊겼습니다.' }, 503); }
      return json({ job: { id: 'prepare', task: 'propose', status: 'completed', result: { contractId: 'new-criteria' } } });
    }
    throw new Error('Unexpected mutation or endpoint: ' + target);
  });
  await page.getByLabel('무엇을 만들까요?', { exact: true }).fill('환전 화면에서 우대 쿠폰을 강조해 주세요.');
  await page.waitForFunction(() => window.requestDirty === true);
  await page.getByRole('button', { name: '이 요청으로 시작하기' }).click();
  await page.getByText('일시 오류입니다. 다시 시도하세요.', { exact: true }).waitFor();
  await page.getByRole('button', { name: '이 요청으로 시작하기' }).click();
  await page.getByText('진행 조회 연결이 끊겼습니다.', { exact: false }).waitFor();
  await page.getByRole('button', { name: '이 요청으로 시작하기' }).click();
  await page.locator('output').getByText('new-criteria', { exact: true }).waitFor();
  assert.equal(calls.length, 3); assert.deepEqual(calls[0], calls[1]); assert.deepEqual(calls[1], calls[2]);
  assert.equal(calls[0].payload.model, 'fable'); assert.deepEqual(calls[0].payload.assetIds, ['private-guide']);
  assert.equal(await page.getByLabel('무엇을 만들까요?', { exact: true }).inputValue(), '');
  assert.equal(calls.some(call => call.target.includes('approve') || call.target === '/batches'), false);
  pollFailed = false;
  await page.getByLabel('무엇을 만들까요?', { exact: true }).fill('접수할 원래 요청');
  await page.getByRole('button', { name: '이 요청으로 시작하기' }).click();
  await page.getByText('진행 조회 연결이 끊겼습니다.', { exact: false }).waitFor();
  await page.getByLabel('무엇을 만들까요?', { exact: true }).fill('조회 실패 뒤 새로 작성한 요청을 유지하세요.');
  const beforePollRetry = calls.length;
  await page.getByRole('button', { name: '진행 다시 조회', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.ws-job'));
  assert.equal(await page.getByLabel('무엇을 만들까요?', { exact: true }).inputValue(), '조회 실패 뒤 새로 작성한 요청을 유지하세요.');
  assert.equal(calls.length, beforePollRetry, 'Resuming observation does not start another proposal');
});
