const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

async function mount(t, entry, handler) {
  const root = path.resolve(__dirname, '..');
  const bundle = await build({
    stdin: { contents: entry, resolveDir: root, loader: 'tsx' },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"development"' }, plugins: [{ name: 'auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
      builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents: 'export const auth={token:"offline"};' }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true, args: ['--no-sandbox'] });
  t.after(() => browser.close());
  const context = await browser.newContext({ offline: true });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'async.invalid') return route.abort();
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><body><div id="root"></div></body></html>' });
    return handler(route, url.pathname.replace('/studio-api', ''));
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  await page.goto('https://async.invalid/');
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  return page;
}

test('late run response preserves the latest explicit missing round and clearing navigation clears evidence', { timeout: 45000 }, async t => {
  const contract = { id: 'contract', title: 'Fixture', brief: '', version: 1, status: 'draft', assetIds: [],
    viewport: { width: 390, height: 844 }, rules: [], unresolved: [] };
  const run = { id: 'run', version: 1, outputType: 'html', status: 'needs_changes', contractId: 'contract',
    contractVersion: 1, contract, bestRound: 1, rounds: [{ number: 1, passed: false, artifactSha256: 'a'.repeat(64),
      hasHtml: false, hasReport: false, hasScreenshot: false, checks: { pass: 0, fail: 0, incomplete: 1 } }] };
  let release, arrived, reads = 0;
  const waiting = new Promise(resolve => { release = resolve; });
  const firstRequest = new Promise(resolve => { arrived = resolve; });
  const page = await mount(t, `import React from 'react';import {createRoot} from 'react-dom/client';import RunsPanel from './src/workspace/RunsPanel';
    const run=${JSON.stringify(run)},contract=${JSON.stringify(contract)};
    const config={models:[{id:'test',label:'Test'}],defaultModel:'test'};
    const selected=value=>{window.observed=value?.round?.number||0;window.observations.push(window.observed)};
    window.observations=[];
    function App(){const [request,setRequest]=React.useState({id:'run',round:1});window.selectRequest=setRequest;
      return <RunsPanel config={config} assets={[]} contracts={[contract]} runs={[run]} refresh={()=>{}} preferredContract=""
        editing={{id:'',dirty:false}} initialRunId={request.id} initialRound={request.round} onSelection={selected}/>;}
    createRoot(document.getElementById('root')).render(<App/>);`, async (route, target) => {
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) }).catch(() => {});
    if (target === '/batches') return json({ batches: [] });
    if (target === '/contracts/contract') return json({ contract });
    if (target === '/runs/run') { reads++; if (reads === 1) { arrived(); await waiting; } return json({ run }); }
    throw new Error('Unexpected ' + target);
  });
  await page.waitForFunction(() => typeof window.selectRequest === 'function');
  await firstRequest;
  await page.evaluate(() => { window.observations = []; window.selectRequest({ id: 'run', round: 4 }); });
  release();
  await page.getByText('아직 검수가 끝난 라운드가 없습니다.', { exact: false }).waitFor();
  assert.equal(await page.evaluate(() => window.observed), 0);
  assert.equal(await page.evaluate(() => window.observations.includes(1)), false);
  await page.evaluate(() => window.selectRequest({ id: 'run', round: 1 }));
  await page.waitForFunction(() => window.observed === 1);
  await page.evaluate(() => window.selectRequest({ id: '', round: 0 }));
  await page.waitForFunction(() => window.observed === 0);
  await page.locator('.ws-run-list button').click();
  await page.waitForFunction(() => window.observed === 1);
});

test('a screen citation removed from current page selection remains visible and removable', { timeout: 30000 }, async t => {
  const ref = { assetId: 'asset', sourceId: 'guide', page: 1, sourceSha256: 'a'.repeat(64), textSha256: 'b'.repeat(64) };
  const draft = { title: 'Fixture', brief: '', assetIds: ['asset'], rules: [], unresolved: [], viewport: { width: 390, height: 844 },
    guideRefs: [{ ...ref, page: 2, textSha256: 'c'.repeat(64) }], changeRequest: { kind: 'change', channel: '', requester: '', dueDate: '',
      baselineNote: '', preserve: '', allowedFiles: [], transitions: [], screens: [{ id: 'entry', title: 'Entry', kind: 'page', change: 'modify',
        instruction: '', uiuxId: '', developerId: '', canonicalId: '', sourceNote: '', states: ['entry'], sourceRefs: [ref] }] } };
  const page = await mount(t, `import React from 'react';import {createRoot} from 'react-dom/client';import ChangeRequestPanel from './src/workspace/ChangeRequestPanel';
    function App(){const [draft,setDraft]=React.useState(${JSON.stringify(draft)});window.snapshot=draft;
      return <ChangeRequestPanel draft={draft} disabled={false} stage="design" onChange={setDraft}/>;}
    createRoot(document.getElementById('root')).render(<App/>);`, () => { throw new Error('No API call expected'); });
  await page.getByText('원본 화면 ID·출처 연결', { exact: true }).click();
  await page.getByText('선택에서 빠진 원문:', { exact: false }).waitFor();
  await page.getByRole('button', { name: '이전 원문 연결 제외' }).click();
  assert.equal(await page.evaluate(() => window.snapshot.changeRequest.screens[0].sourceRefs.length), 0);
  assert.equal(await page.evaluate(() => window.snapshot.guideRefs[0].page), 2);
});

test('continuing with an opened approved contract selects it even after a different generation choice', { timeout: 45000 }, async t => {
  const contracts = ['a', 'b'].map(id => ({ id, title: id.toUpperCase(), version: 1, status: 'approved', brief: '', assetIds: [],
    viewport: { width: 390, height: 844 }, unresolved: [], rules: [{ id: 'R1', title: 'Display', required: true, source: { kind: 'manual' },
      steps: [{ action: 'expectVisible', target: 'entry', targetLabel: 'Entry', value: true }] }] }));
  const submitted = [];
  const page = await mount(t, `import React from 'react';import {createRoot} from 'react-dom/client';
    import RulesPanel from './src/workspace/RulesPanel';import RunsPanel from './src/workspace/RunsPanel';
    const contracts=${JSON.stringify(contracts)},config={models:[{id:'test',label:'Test'}],defaultModel:'test'};
    function App(){const [stage,setStage]=React.useState('design'),[target,setTarget]=React.useState({id:'a',sequence:0});
      const choose=React.useCallback(value=>setTarget(old=>({id:value.id,sequence:old.sequence+1})),[]);
      return <><button onClick={()=>setStage('design')}>기준 다시 보기</button>
        <div hidden={stage!=='design'}><RulesPanel config={config} assets={[]} selected={[]} contracts={contracts} initialContractId="a"
          onEditing={()=>{}} refresh={()=>{}} onApproved={choose} onContinue={()=>setStage('review')}/></div>
        <div hidden={stage!=='review'}><RunsPanel config={config} assets={[]} contracts={contracts} runs={[]} refresh={()=>{}}
          preferredContract={target.id} preferredSelection={target.sequence} editing={{id:'',dirty:false}}/></div></>;}
    createRoot(document.getElementById('root')).render(<App/>);`, async (route, target) => {
    const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (target === '/batches' && route.request().method() === 'GET') return json({ batches: [] });
    if (target.startsWith('/contracts/')) return json({ contract: contracts.find(item => item.id === target.split('/')[2]) });
    if (target === '/batches') {
      submitted.push(route.request().postDataJSON());
      return json({ error: 'Synthetic stop after capturing the selected criteria.' }, 503);
    }
    throw new Error('Unexpected ' + target);
  });
  await page.getByLabel('저장한 규칙', { exact: true }).selectOption('b');
  await page.waitForFunction(() => [...document.querySelectorAll('.ws-rules-panel input')].some(input => input.value === 'B'));
  await page.getByRole('button', { name: '승인 기준으로 시안·검수' }).click();
  await page.getByRole('button', { name: '시안 1개 만들기', exact: true }).click();
  await page.getByText('작업실에서 요청을 처리하지 못했습니다.', { exact: false }).waitFor();
  assert.equal(submitted[0].contractId, 'b');
  await page.getByLabel('사용할 규칙', { exact: true }).selectOption('a');
  await page.getByRole('button', { name: '기준 다시 보기' }).click();
  await page.getByRole('button', { name: '승인 기준으로 시안·검수' }).click();
  await page.getByRole('button', { name: '시안 1개 만들기', exact: true }).click();
  await page.getByText('작업실에서 요청을 처리하지 못했습니다.', { exact: false }).waitFor();
  assert.equal(submitted.length, 2);
  assert(submitted.every(body => body.contractId === 'b'));
});
