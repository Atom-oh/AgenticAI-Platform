const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { build } = require('esbuild');
const { chromium } = require('playwright');
const { buildRenderer } = require('../portal-react/build.cjs');
const web = path.resolve(__dirname, '..');
let prepared;
const assets = () => prepared ||= Promise.all([
  buildRenderer({ write: false }),
  build({ stdin: { loader: 'tsx', resolveDir: web, contents: `
import React from 'react'; import {createRoot} from 'react-dom/client'; import {flushSync} from 'react-dom';
import {ReactComponentPreview} from './src/portal/ReactComponentPreview';
import ReactSourcePanel from './src/portal/ReactSourcePanel';
const root=createRoot(document.getElementById('app'));
window.showPreview=(name, compact=false)=>flushSync(()=>root.render(<ReactComponentPreview name={name} compact={compact}/>));
window.showSources=(hash,version='1.0.0')=>flushSync(()=>root.render(<ReactSourcePanel hash={hash} version={version}/>));
window.receipts=[];window.addEventListener('message',e=>{if(e.data?.channel==='portal-react/v1')window.receipts.push(e.data);});
` }, bundle: true, write: false, format: 'iife', define: { 'process.env.NODE_ENV': '"production"' } }),
]);
async function mount(t, respond, intentionalCsp = false) {
  const [renderer, app] = await assets();
  const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKSPACE_CHROMIUM,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  t.after(() => browser.close());
  const context = await browser.newContext({ offline: true, viewport: { width: 1100, height: 900 } });
  const requests = [], errors = [], consoleErrors = [], outside = [];
  await context.routeWebSocket('**/*', socket => socket.close());
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://portal-react.invalid') { outside.push(url.href); return route.abort(); }
    requests.push(url.pathname);
    if (respond && await respond(route, url, renderer)) return;
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><html lang="ko"><head><title>Portal React QA</title><style>body{margin:16px}.sui-button{background:red!important}main{color:red!important}</style></head><body><div id="app"></div><script src="/app.js"></script></body></html>' });
    if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: app.outputFiles[0].text });
    if (url.pathname === '/portal-renderers/react/index.json') return route.fulfill({
      contentType: 'application/json', body: JSON.stringify(renderer.manifest),
    });
    if (url.pathname === `/portal-renderers/react/${renderer.manifest.renderer.file}`) return route.fulfill({
      contentType: 'text/html', body: renderer.html,
    });
    if (url.pathname === `/portal-renderers/react/${renderer.manifest.sources.file}`) return route.fulfill({
      contentType: 'application/json', body: renderer.sources,
    });
    if (url.pathname === `/portal-renderers/react/${renderer.manifest.sources.archive.file}`) return route.fulfill({
      contentType: 'application/zip', body: renderer.archive,
    });
    throw new Error(`Unexpected local request ${url.pathname}`);
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  await page.goto('https://portal-react.invalid/');
  t.after(() => {
    assert.deepEqual(errors, []);
    assert.deepEqual(outside, []);
    if (intentionalCsp) assert.ok(consoleErrors.every(message => /Content Security Policy|blocked|Refused/i.test(message)));
    else assert.deepEqual(consoleErrors, []);
  });
  return { page, requests, renderer };
}
async function show(page, name, compact = false) {
  await page.evaluate(({ name, compact }) => window.showPreview(name, compact), { name, compact });
  await page.locator('[data-react-preview-state="rendered"]').waitFor();
  return page.frameLocator('iframe[data-portal-react]');
}

test('source panel downloads real ZIP and selected TSX and rejects a stale catalog', { timeout: 45000 }, async t => {
  const { page, renderer } = await mount(t);
  await page.evaluate(hash => window.showSources(hash), renderer.manifest.catalog.hash);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'React 코드 다운로드 (ZIP)', exact: true }).click();
  const zip = await download;
  assert.match(zip.suggestedFilename(), /^studio-ui-1\.0\.0-[a-f0-9]{12}\.zip$/);
  assert.deepEqual(await fs.readFile(await zip.path()), renderer.archive);
  await page.getByRole('button', { name: '소스 보기', exact: true }).click();
  await page.getByLabel('컴포넌트 소스 파일', { exact: true }).waitFor();
  assert.match(await page.locator('pre').innerText(), /export function Button/);
  const current = page.waitForEvent('download');
  await page.getByRole('button', { name: '현재 파일 다운로드', exact: true }).click();
  const tsx = await current;
  assert.equal(tsx.suggestedFilename(), 'index.tsx');
  assert.equal(await fs.readFile(await tsx.path(), 'utf8'), JSON.parse(renderer.sources).files.find(f => f.path === 'ui/index.tsx').content);
  await page.evaluate(() => window.showSources('a'.repeat(64)));
  await page.getByRole('button', { name: 'React 코드 다운로드 (ZIP)', exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(await page.locator('pre').count(), 0);
});

test('a late source download cannot start after the displayed revision changes', { timeout: 45000 }, async t => {
  let release;
  const { page, renderer } = await mount(t, async (route, url, renderer) => {
    if (!url.pathname.endsWith('.zip')) return false;
    await new Promise(resolve => { release = async () => {
      await route.fulfill({ contentType: 'application/zip', body: renderer.archive }); resolve();
    }; });
    return true;
  });
  const downloads = [];
  page.on('download', download => downloads.push(download.suggestedFilename()));
  await page.evaluate(hash => window.showSources(hash), renderer.manifest.catalog.hash);
  await page.getByRole('button', { name: 'React 코드 다운로드 (ZIP)', exact: true }).click();
  while (!release) await new Promise(resolve => setTimeout(resolve, 10));
  await page.evaluate(() => window.showSources('b'.repeat(64)));
  await release();
  await page.getByRole('button', { name: '소스 보기', exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.deepEqual(downloads, []);
});

test('all 15 real exports render with original markers, styles, isolated landmarks and one cached bundle', { timeout: 60_000 }, async t => {
  const { page, requests, renderer } = await mount(t);
  for (const { name } of renderer.manifest.catalog.components) {
    const frame = await show(page, name);
    const target = frame.locator(`[data-preview-example="${name}"] [data-studio-component="${name}"]`).first();
    await target.waitFor();
    assert.equal(await target.getAttribute('data-studio-version'), renderer.manifest.catalog.version);
    assert.equal(await frame.getByRole('main').count(), 1);
    if (name === 'AssetImage') assert.equal(await target.evaluate(image => image.naturalWidth), 240);
  }
  assert.equal(requests.filter(url => url.endsWith('index.json')).length, 1);
  assert.equal(requests.filter(url => url.endsWith('.html')).length, 1);
  const frame = await show(page, 'Button');
  const button = frame.getByRole('button', { name: '눌러 보기', exact: true });
  assert.equal(await button.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(0, 132, 133)');
  assert.equal(await page.locator('[data-studio-component]').count(), 0);
  assert.equal(await page.locator('iframe').getAttribute('sandbox'), 'allow-scripts');
  assert.equal(await page.locator('iframe').getAttribute('referrerpolicy'), 'no-referrer');
  assert.equal(await page.evaluate(() => {
    try { void document.querySelector('iframe').contentWindow.document; return false; } catch { return true; }
  }), true);
  assert.equal(await page.title(), 'Portal React QA');
  assert.equal(page.url(), 'https://portal-react.invalid/');
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
});

test('original kit callbacks and controls update local state, keyboard behavior and reset', { timeout: 60_000 }, async t => {
  const { page } = await mount(t);
  let frame = await show(page, 'Button');
  await frame.getByRole('button', { name: '눌러 보기', exact: true }).click();
  await frame.getByText('버튼을 1번 눌렀습니다.', { exact: true }).waitFor();
  await frame.getByRole('combobox', { name: '버튼 표현', exact: true }).selectOption('danger');
  assert.equal(await frame.getByRole('button', { name: '눌러 보기', exact: true }).getAttribute('data-kind'), 'danger');
  await frame.getByRole('checkbox', { name: '버튼 비활성화', exact: true }).check();
  assert.equal(await frame.getByRole('button', { name: '눌러 보기', exact: true }).isDisabled(), true);
  await frame.getByRole('button', { name: '예시 초기화', exact: true }).click();
  await frame.getByText('버튼을 0번 눌렀습니다.', { exact: true }).waitFor();
  assert.equal(await frame.getByRole('button', { name: '눌러 보기', exact: true }).isDisabled(), false);
  frame = await show(page, 'Input');
  await frame.getByRole('textbox', { name: '입력 예시', exact: true }).fill('지역 예시');
  await frame.getByText('입력한 값: 지역 예시', { exact: true }).waitFor();
  frame = await show(page, 'Checkbox');
  await frame.getByRole('checkbox', { name: '선택 예시', exact: true }).check();
  await frame.getByText('선택했습니다.', { exact: true }).waitFor();
  frame = await show(page, 'Select');
  await frame.getByRole('combobox', { name: '선택 예시', exact: true }).selectOption('two');
  await frame.getByText('현재 선택: 두 번째', { exact: true }).waitFor();
  frame = await show(page, 'RadioGroup');
  await frame.getByRole('radio', { name: '첫 번째', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await frame.getByRole('radio', { name: '두 번째', exact: true }).isChecked(), true);
  await frame.getByText('현재 선택: 두 번째', { exact: true }).waitFor();
  frame = await show(page, 'Stepper');
  await frame.getByRole('button', { name: '다음 단계 예시', exact: true }).click();
  assert.match(await frame.locator('[aria-current="step"]').innerText(), /다음 단계/);
  frame = await show(page, 'Alert');
  await frame.getByRole('combobox', { name: '알림 표현', exact: true }).selectOption('warning');
  assert.equal(await frame.locator('[data-studio-component="Alert"]').getAttribute('role'), 'alert');
  frame = await show(page, 'Button');
  await frame.getByText('버튼을 0번 눌렀습니다.', { exact: true }).waitFor();
  for (const width of [1100, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    assert.equal(await frame.locator('body').evaluate(el => el.scrollWidth > innerWidth + 1), false);
    if (process.env.PORTAL_REACT_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.PORTAL_REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.PORTAL_REACT_SCREENSHOT_DIR, `react-${width}.png`), fullPage: true });
    }
  }
});

test('compact previews are lazy, inert and decorative with no knobs', { timeout: 60_000 }, async t => {
  const { page, requests } = await mount(t);
  await page.evaluate(() => {
    document.getElementById('app').style.marginTop = '8000px';
    window.showPreview('Button', true);
  });
  assert.equal(await page.locator('iframe').count(), 0);
  assert.equal(requests.filter(url => url.includes('portal-renderers')).length, 0);
  await page.locator('[data-portal-react-container]').scrollIntoViewIfNeeded();
  await page.locator('[data-react-preview-state="rendered"]').waitFor();
  assert.equal(await page.locator('[data-portal-react-container]').getAttribute('inert'), '');
  assert.equal(await page.locator('[data-portal-react-container]').getAttribute('aria-hidden'), 'true');
  assert.equal(await page.locator('iframe').getAttribute('loading'), 'lazy');
  assert.equal(await page.locator('iframe').getAttribute('tabindex'), '-1');
  const frame = page.frameLocator('iframe');
  assert.equal(await frame.getByRole('group', { name: '예시 조정' }).count(), 0);
  assert.equal(await frame.getByRole('button', { name: '예시 초기화' }).count(), 0);
});

test('selection removes old state and rejects stale, cross-frame and mismatched nonce replies', { timeout: 60_000 }, async t => {
  const { page } = await mount(t);
  await show(page, 'Input');
  const stale = await page.evaluate(() => {
    window.oldFrame = document.querySelector('iframe').contentWindow;
    return window.receipts.find(message => message.type === 'rendered');
  });
  const removed = await page.evaluate(() => {
    const old = document.querySelector('iframe');
    window.showPreview('Button');
    return !old.isConnected;
  });
  assert.equal(removed, true);
  await page.locator('[data-react-preview-state="rendered"]').waitFor();
  await page.evaluate(stale => {
    const current = window.receipts.filter(message => message.type === 'rendered').at(-1);
    for (const [source, data] of [
      [window.oldFrame, { ...stale, type: 'error' }],
      [window, { ...current, type: 'error' }],
      [document.querySelector('iframe').contentWindow, { ...stale, type: 'error' }],
      [document.querySelector('iframe').contentWindow, { ...current, nonce: '0'.repeat(64), type: 'error' }],
    ]) window.dispatchEvent(new MessageEvent('message', { source, origin: 'null', data }));
  }, stale);
  assert.equal(await page.locator('[data-react-preview-state="rendered"]').count(), 1);
  assert.equal(await page.getByRole('alert').count(), 0);
  const current = await page.evaluate(() => window.receipts.filter(message => message.type === 'rendered').at(-1));
  assert.notEqual(current.nonce, stale.nonce);
  assert.notEqual(current.id, stale.id);
});

test('unknown component metadata never creates a frame; corrupt HTML fails closed and retry recovers', { timeout: 60_000 }, async t => {
  let corrupt = true;
  const { page, requests } = await mount(t, async (route, url, renderer) => {
    if (corrupt && url.pathname.endsWith('.html')) {
      await route.fulfill({ contentType: 'text/html', body: renderer.html.replace('<title>', '<TITLE>') });
      return true;
    }
  });
  await page.evaluate(() => window.showPreview('@atom/ui/button'));
  await page.getByRole('alert').waitFor();
  assert.equal(await page.locator('iframe').count(), 0);
  assert.equal(requests.filter(url => url.includes('portal-renderers')).length, 0);
  await page.evaluate(() => window.showPreview('Button'));
  await page.getByRole('alert').waitFor();
  assert.equal(await page.locator('iframe').count(), 0);
  corrupt = false;
  await page.getByRole('button', { name: '다시 불러오기', exact: true }).click();
  await page.locator('[data-react-preview-state="rendered"]').waitFor();
});

test('the running opaque sandbox blocks network, untrusted script, storage and parent DOM access', { timeout: 60_000 }, async t => {
  const { page } = await mount(t, undefined, true);
  const frame = await show(page, 'Button');
  const result = await frame.locator('body').evaluate(async () => {
    const violations = [];
    document.addEventListener('securitypolicyviolation', event => violations.push(event.violatedDirective));
    let parentBlocked = false, storageBlocked = false;
    try { void parent.document.body; } catch { parentBlocked = true; }
    try { localStorage.setItem('test', 'blocked'); } catch { storageBlocked = true; }
    const fetchBlocked = await fetch('https://outside.invalid/probe').then(() => false, () => true);
    const script = document.createElement('script');
    script.textContent = 'window.injectedScriptExecuted = true';
    document.body.append(script);
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return { parentBlocked, storageBlocked, fetchBlocked, injected: window.injectedScriptExecuted === true, violations };
  });
  assert.equal(result.parentBlocked, true);
  assert.equal(result.storageBlocked, true);
  assert.equal(result.fetchBlocked, true);
  assert.equal(result.injected, false);
  assert.ok(result.violations.includes('connect-src'));
  assert.ok(result.violations.includes('script-src-elem'));
  await frame.getByRole('button', { name: '눌러 보기', exact: true }).click();
  await frame.getByText('버튼을 1번 눌렀습니다.', { exact: true }).waitFor();
});
