// Real Chromium and production React/CSS; only identity and private API transport are fixtures.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('flow branches, scoped side-by-side previews and role menus remain usable and independent of approval', { timeout: 90000 }, async () => {
  const root = path.resolve(__dirname, '..');
  const compiled = await build({ stdin: { resolveDir: root, loader: 'tsx', contents: `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import FlowDiagram from './src/workspace/FlowDiagram';
    import DesignComparison from './src/workspace/DesignComparison';
    import {WorkspaceScope} from './src/workspace/WorkspaceScope';
    import {createWorkspaceClient} from './src/workspace/client';
    import {MENU_GROUPS} from './src/navigation';
    const screens=[{id:'start',title:'가입 조건 확인'},{id:'yes',title:'가입 가능 안내'},{id:'no',title:'가입 불가 안내'}];
    const links=[{id:'eligible',from:'start',to:'yes',condition:'가입 조건 충족',action:'다음',retention:'입력 금액'},
      {id:'ineligible',from:'start',to:'no',condition:'가입 조건 미충족'}, {id:'back',from:'yes',to:'start',condition:'다시 입력'},
      {id:'self',from:'no',to:'no',condition:'오류 확인'}, {id:'broken',from:'no',to:'missing',condition:'미연결'}];
    const variants=['a','b'].map((id)=>({artifactId:id,sourceHash:id.repeat(64),bundleHash:'c'.repeat(64),passed:id==='a'}));
    function App(){const [scope,setScope]=React.useState('p-one');const [selected,setSelected]=React.useState('');
      const client=React.useMemo(()=>createWorkspaceClient({projectId:scope}),[scope]);
      return <WorkspaceScope.Provider value={{client,project:{id:scope},actorId:'designer',role:'designer'}}>
        <div className="designer-workspace"><button onClick={()=>setScope('p-two')}>프로젝트 전환</button>
        <nav aria-label="검사 메뉴">{MENU_GROUPS.flatMap(g=>g.items).map(n=><button key={n.id} data-route={n.id}>{n.label}</button>)}</nav>
        <FlowDiagram screens={screens} transitions={links}/>
        <DesignComparison designId="design" variants={variants} selected={selected} onSelect={setSelected}/>
        <output aria-label="검토 선택">{selected}</output></div>
      </WorkspaceScope.Provider>;
    } createRoot(document.getElementById('root')).render(<App/>);` },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{ name: 'synthetic-identity', setup(b) {
      b.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'auth', namespace: 'fixture' }));
      b.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const auth={token:"synthetic"};' }));
    } }],
  });
  const css = fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8');
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, offline: true });
    const calls = [], errors = [];
    let mismatch = true, hold = false, pending;
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://review.invalid') return route.abort();
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body:
        `<html lang="ko"><head><meta charset="utf-8"><style>body{margin:0}*{box-sizing:border-box}${css}</style></head><body><div id="root"></div><script src="/app.js"></script></body></html>` });
      if (url.pathname === '/app.js') return route.fulfill({ contentType: 'application/javascript', body: compiled.outputFiles[0].text });
      const scope = route.request().headers()['x-workspace-project'];
      calls.push({ path: url.pathname, scope, method: route.request().method() });
      assert.equal(route.request().headers().authorization, 'Bearer synthetic');
      assert.match(url.pathname, /^\/studio-api\/designs\/design\/artifacts\/[ab]$/);
      const id = url.pathname.split('/').at(-1);
      if (hold && id === 'a') await new Promise(resolve => { pending = resolve; });
      if (scope === 'p-two') return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'Resource not found' }) });
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        sourceHash: mismatch && id === 'b' ? '0'.repeat(64) : id.repeat(64), bundleHash: 'c'.repeat(64),
        verification: { verdict: id === 'a' ? 'pass' : 'fail' },
        previewHtml: `<html><body><h1>합성 시안 ${id}</h1><button onclick="this.textContent='동작 확인'">동작</button></body></html>`,
      }) });
    });
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('https://review.invalid/');
    const menu = page.getByRole('navigation', { name: '검사 메뉴' });
    for (const route of ['wb-development', 'wb-knowledge']) assert.equal(await menu.locator(`[data-route="${route}"]`).count(), 1);
    const diagram = page.getByRole('region', { name: '화면 이동 흐름도', exact: true });
    await diagram.getByRole('img', { name: '화면 3개, 이동 5개' }).waitFor();
    assert.equal(await diagram.locator('path[marker-end]').count(), 4, 'Missing destinations must not get a made-up edge');
    await diagram.getByRole('alert').getByText('연결할 화면이 없거나', { exact: false }).waitFor();
    await diagram.getByLabel('강조할 이동').selectOption('ineligible');
    assert.match(await diagram.locator('li[aria-current="true"]').innerText(), /가입 조건 확인 → 가입 불가 안내[\s\S]*가입 조건 미충족/);
    await diagram.getByLabel('강조할 이동').selectOption('back');
    assert.match(await diagram.locator('li[aria-current="true"]').innerText(), /가입 가능 안내 → 가입 조건 확인/);
    await page.getByRole('button', { name: '시안 나란히 비교', exact: true }).click();
    const first = page.frameLocator('iframe[title="시안 1 비교 미리보기"]').frameLocator('iframe[data-workspace-preview-content]');
    await first.getByRole('heading', { name: '합성 시안 a' }).waitFor();
    await page.getByRole('alert').filter({ hasText: '선택한 시안과 표시할 검증 근거가 다릅니다.' }).waitFor();
    assert.equal(await page.locator('iframe[title="시안 2 비교 미리보기"]').count(), 0);
    mismatch = false; await page.getByRole('button', { name: '비교 미리보기 다시 조회' }).click();
    const second = page.frameLocator('iframe[title="시안 2 비교 미리보기"]').frameLocator('iframe[data-workspace-preview-content]');
    await second.getByRole('heading', { name: '합성 시안 b' }).waitFor();
    await first.getByRole('button', { name: '동작', exact: true }).click();
    await first.getByRole('button', { name: '동작 확인', exact: true }).waitFor();
    await second.getByRole('button', { name: '동작', exact: true }).waitFor();
    assert.match(await first.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content'), /connect-src 'none'/);
    for (const frame of await page.locator('.ws-comparison-card > iframe').all()) {
      assert.equal(await frame.getAttribute('sandbox'), 'allow-scripts');
      assert.equal(await frame.getAttribute('referrerpolicy'), 'no-referrer');
    }
    const cards = page.locator('.ws-comparison-card');
    const a = await cards.nth(0).boundingBox(), b = await cards.nth(1).boundingBox();
    assert.equal(a.y, b.y); assert(b.x >= a.x + a.width, 'Previews are side by side, not stacked');
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Page overflow at ${width}px`);
    }
    if (process.env.WORKSPACE_QA_DIR) {
      fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
      await diagram.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'flow-diagram.png') });
      await page.getByRole('region', { name: '시안 비교 화면', exact: true }).screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'side-by-side.png') });
    }
    await page.getByRole('button', { name: '시안 2 검토하기' }).click();
    assert.equal(await page.getByLabel('검토 선택').innerText(), 'b');
    assert(calls.every(call => call.method === 'GET'), 'Comparison must never approve or mutate a design');
    await page.getByRole('button', { name: '나란히 비교 닫기' }).click();
    hold = true;
    const nextRequest = page.waitForRequest(request => request.url().endsWith('/artifacts/a'));
    await page.getByRole('button', { name: '시안 나란히 비교', exact: true }).click();
    await nextRequest;
    await page.getByRole('button', { name: '프로젝트 전환' }).click();
    pending?.(); hold = false;
    assert.equal(await page.locator('.ws-comparison-card iframe').count(), 0);
    await page.getByRole('button', { name: '시안 나란히 비교', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '이 파일이나 작업을 찾을 수 없습니다.' }).first().waitFor();
    assert.equal(await page.locator('.ws-comparison-card iframe').count(), 0, 'Old project content must not survive a scope change');
    assert(calls.some(call => call.scope === 'p-two'));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
