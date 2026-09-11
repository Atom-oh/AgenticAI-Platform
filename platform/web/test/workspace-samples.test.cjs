// Browser plugin unavailable: use regular Playwright with synthetic static files.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const catalog = { id: 'studio-ui', label: '플랫폼 기본 React 컴포넌트', version: '1.0.0', hash: 'a'.repeat(64) };
const ids = ['amount-review', 'required-consent', 'product-compare'];
function manifest() {
  return { schemaVersion: 1, catalog, samples: ids.map((id, index) => ({
    id, title: ['납입금액 확인', '필수 동의', '상품 비교'][index],
    description: ['입력한 금액이 확인 화면에 이어지는 예제입니다.', '동의해야 다음 단계로 진행할 수 있습니다.', '선택한 상품을 비교하며 확인합니다.'][index],
    checks: ['입력과 선택을 바꾸어 보세요.', '화면에 표시되는 결과를 확인하세요.'],
    components: ['Screen', 'Input', 'Checkbox', 'Button', 'Summary'],
    sourceHash: 'b'.repeat(64), bundleHash: 'c'.repeat(64),
    preview: `${id}/preview.html`, source: `${id}/source.zip`, dist: `${id}/dist.zip`,
    guide: `${id}/guide.md`, contract: `${id}/contract.json`,
  })) };
}

function preview(id) {
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${id}</title>
  <style>body{font:16px sans-serif;margin:0;padding:24px;box-sizing:border-box}main{max-width:720px;margin:auto}input{max-width:100%;box-sizing:border-box}button{padding:12px}label,output{display:block;margin:16px 0}</style></head><body><main>
  <h1>연습용 ${id}</h1><label>납입금액<input id="amount" value="10000"></label>
  <button id="confirm">금액 확인</button><output id="summary">확인 전</output>
  <label><input type="checkbox" id="consent">필수 동의</label><button id="next" disabled>다음</button>
  <button onclick="location.href='https://outside.invalid/sample'">외부로 이동</button>
  </main><script>
  document.getElementById('confirm').onclick=()=>document.getElementById('summary').textContent=document.getElementById('amount').value;
  document.getElementById('consent').onchange=event=>document.getElementById('next').disabled=!event.target.checked;
  try{parent.document.body.dataset.sampleEscaped='yes'}catch{document.documentElement.dataset.isolated='yes'}
  </script></body></html>`;
}

async function mount(t, { workspace = false, initialCatalog = catalog, respond } = {}) {
  const component = workspace ? 'Workspace' : 'SampleGallery';
  const bundle = await build({
    stdin: { contents: `import React from 'react';import{createRoot}from'react-dom/client';import Component from './src/workspace/${component}';
      const root=createRoot(document.getElementById('root'));let catalog=${JSON.stringify(initialCatalog)},active=true;
      function render(){root.render(${workspace ? '<Component/>' : '<Component catalog={catalog} active={active}/>'})}
      window.changeCatalog=value=>{catalog=value;render()};window.setActive=value=>{active=value;render()};window.dispose=()=>root.unmount();render();`,
      loader: 'tsx', resolveDir: root },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: { '.css': 'empty' },
    plugins: [{ name: 'offline-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
      builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents: "export const auth={token:'private-api-test-token'};" }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM,
    headless: true, args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  t.after(() => browser.close());
  const context = await browser.newContext({ offline: true, acceptDownloads: true, viewport: { width: 1920, height: 1080 } });
  await context.routeWebSocket('**/*', socket => socket.close());
  const requests = [], errors = [], outside = [];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://offline.test') { outside.push(url.href); return route.abort(); }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html',
      body: '<html lang="ko"><head><title>Studio sample gallery test</title></head><body style="margin:0;background:#f4f8f6"><main style="padding:16px"><div class="designer-workspace" id="root"></div></main></body></html>' });
    if (url.pathname.startsWith('/studio-api/')) {
      const target = url.pathname.slice('/studio-api/'.length);
      const value = target === 'config' ? { actorId: 'actor', componentCatalog: catalog, models: [],
        defaultModel: '', extensions: ['html', 'txt'], maxFileBytes: 52428800, chunkBytes: 2097152 }
        : target === 'git-connections' ? { connections: [] } : { [target]: [] };
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    }
    requests.push({ path: url.pathname, headers: route.request().headers() });
    assert.equal(route.request().headers().authorization, undefined);
    if (respond && await respond(route, url, requests)) return;
    if (url.pathname === '/studio-samples/index.json') return route.fulfill({
      contentType: 'application/json', body: JSON.stringify(manifest()) });
    const match = url.pathname.match(/^\/studio-samples\/(amount-review|required-consent|product-compare)\/(preview\.html|guide\.md|contract\.json|source\.zip|dist\.zip)$/);
    assert.ok(match, `Unexpected static path: ${url.pathname}`);
    return route.fulfill({ contentType: match[2] === 'preview.html' ? 'text/html' : 'application/octet-stream',
      body: match[2] === 'preview.html' ? preview(match[1]) : 'synthetic download' });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('https://offline.test/');
  await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  t.after(() => { assert.deepEqual(errors, []); assert.deepEqual(outside, []); });
  return { page, requests };
}

test('files step lazily opens three isolated interactive samples and native downloads', { timeout: 45_000 }, async t => {
  const { page, requests } = await mount(t, { workspace: true });
  const gallery = page.getByRole('region', { name: 'React 예제로 연습하기' });
  await page.getByRole('heading', { name: '파일·스킬 작업실' }).waitFor();
  assert.equal(await page.title(), 'Studio sample gallery test');
  assert.equal(await gallery.locator('details').first().getAttribute('open'), null);
  assert.equal(requests.length, 0);
  assert.equal(await gallery.locator('iframe').count(), 0);
  await page.getByText('React 샘플 둘러보기', { exact: true }).click();
  await gallery.getByRole('button', { name: '납입금액 확인 미리보기', exact: true }).waitFor();
  assert.equal(await gallery.locator('article').count(), 3);
  const frame = page.frameLocator('iframe[title="납입금액 확인 예제 미리보기"]').frameLocator('[data-workspace-preview-content]');
  await frame.getByLabel('납입금액').fill('25000');
  await frame.getByRole('button', { name: '금액 확인', exact: true }).click();
  assert.equal(await frame.locator('#summary').textContent(), '25000');
  assert.equal(await frame.locator('html').getAttribute('data-isolated'), 'yes');
  await frame.getByRole('button', { name: '외부로 이동', exact: true }).click();
  assert.equal(page.url(), 'https://offline.test/');
  assert.equal(await page.locator('body').getAttribute('data-sample-escaped'), null);
  assert.equal(await gallery.locator('iframe').getAttribute('sandbox'), 'allow-scripts');
  assert.equal(await gallery.locator('iframe').getAttribute('referrerpolicy'), 'no-referrer');
  assert.deepEqual(requests.map(item => item.path), ['/studio-samples/index.json', '/studio-samples/amount-review/preview.html']);
  await gallery.getByText('React 소스 ZIP은 현재 직접 반입할 수 없습니다.', { exact: false }).waitFor();
  assert.equal(await gallery.getByRole('link', { name: 'React 소스 ZIP', exact: true }).isVisible(), false);
  const download = page.waitForEvent('download');
  await gallery.getByRole('link', { name: 'HTML 예제 내려받기', exact: true }).click();
  assert.equal(new URL((await download).url()).pathname, '/studio-samples/amount-review/preview.html');
  await gallery.getByRole('button', { name: '필수 동의 미리보기', exact: true }).click();
  const consent = page.frameLocator('iframe[title="필수 동의 예제 미리보기"]').frameLocator('[data-workspace-preview-content]');
  assert.equal(await consent.getByRole('button', { name: '다음', exact: true }).isDisabled(), true);
  await consent.getByLabel('필수 동의', { exact: true }).check();
  assert.equal(await consent.getByRole('button', { name: '다음', exact: true }).isEnabled(), true);
  await gallery.locator('summary').filter({ hasText: '개발용 소스·검증 규칙' }).click();
  for (const [name, file] of [['React 소스 ZIP', 'source.zip'], ['배포용 dist ZIP', 'dist.zip'], ['검증 규칙 JSON', 'contract.json']]) {
    assert.equal(await gallery.getByRole('link', { name, exact: true }).getAttribute('href'), `/studio-samples/required-consent/${file}`);
  }
  for (const [width, height] of [[1920, 1080], [2560, 1440], [3440, 1440], [768, 1024], [360, 800]]) {
    await page.setViewportSize({ width, height });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, `Horizontal overflow at ${width}`);
    const box = await gallery.boundingBox();
    assert.ok(box && box.width > 0 && box.x >= 0 && box.x + box.width <= width + 1);
    if (process.env.STUDIO_SAMPLES_SCREENSHOT_DIR && [1920, 360].includes(width)) {
      fs.mkdirSync(process.env.STUDIO_SAMPLES_SCREENSHOT_DIR, { recursive: true });
      await gallery.screenshot({ path: path.join(process.env.STUDIO_SAMPLES_SCREENSHOT_DIR, `gallery-${width}.png`) });
    }
  }
  await page.getByRole('button', { name: '2 규칙 확인·승인' }).click();
  assert.equal(await page.locator('iframe').count(), 0);
  await page.getByRole('button', { name: '1 파일 준비' }).click();
  await page.getByText('React 샘플 둘러보기', { exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.ws-samples iframe'));
  assert.equal(await gallery.locator('iframe').count(), 0);
  assert.equal(await page.locator('.vite-error-overlay').count(), 0);
});

test('stale or unknown catalog hashes never render previews or downloads', { timeout: 30_000 }, async t => {
  let value = manifest();
  const { page, requests } = await mount(t, { respond: async (route, url) => {
    if (url.pathname !== '/studio-samples/index.json') return false;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) }); return true;
  } });
  value.catalog = { ...catalog, hash: 'd'.repeat(64) };
  await page.getByText('React 샘플 둘러보기', { exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '기준' }).waitFor();
  assert.equal(await page.locator('iframe,a[download]').count(), 0);
  value = manifest();
  await page.getByRole('button', { name: '예제 다시 불러오기' }).click();
  await page.frameLocator('iframe').frameLocator('[data-workspace-preview-content]').getByLabel('납입금액').waitFor();
  await page.evaluate(() => window.changeCatalog({ id: 'studio-ui', version: '1.0.0', label: 'Unknown' }));
  await page.getByRole('alert').filter({ hasText: '기준' }).waitFor();
  assert.equal(await page.locator('iframe,a[download]').count(), 0);
  assert.equal(requests.filter(item => item.path.endsWith('/preview.html')).length, 1);
});

test('encoded, external, cross-sample and unknown manifest paths fail closed', { timeout: 45_000 }, async t => {
  let value = manifest();
  const { page, requests } = await mount(t, { respond: async (route, url) => {
    if (url.pathname !== '/studio-samples/index.json') return false;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) }); return true;
  } });
  const invalid = [
    { preview: 'https://outside.invalid/preview.html' }, { preview: '//outside.invalid/preview.html' },
    { preview: 'amount-review/../required-consent/preview.html' }, { preview: 'amount-review/%2e%2e/preview.html' },
    { preview: 'amount-review\\\\preview.html' }, { preview: 'amount-review/preview.html?next=https://outside.invalid' },
    { source: 'required-consent/source.zip' }, { guide: 'amount-review/guide.md#outside' },
    { contract: 'amount-review/%63ontract.json' }, { dist: 'amount-review/dist.zip\" onclick=\"alert(1)' },
    { id: '__proto__' },
  ];
  for (const change of invalid) {
    value = manifest(); Object.assign(value.samples[0], change);
    if (requests.length === 0) await page.getByText('React 샘플 둘러보기', { exact: true }).click();
    else await page.getByRole('button', { name: '예제 다시 불러오기' }).click();
    await page.getByRole('alert').waitFor();
    assert.equal(await page.locator('iframe,a[download]').count(), 0, JSON.stringify(change));
  }
  assert.ok(requests.every(item => item.path === '/studio-samples/index.json'));
});

test('closing aborts a held request and retry cannot reveal a stale response', { timeout: 30_000 }, async t => {
  let held, requestsCount = 0;
  const { page } = await mount(t, { respond: async (route, url) => {
    if (url.pathname !== '/studio-samples/index.json') return false;
    requestsCount += 1;
    if (requestsCount === 1) { held = route; return true; }
    if (requestsCount === 2) { await route.fulfill({ status: 503, body: 'Unavailable' }); return true; }
    return false;
  } });
  await page.evaluate(() => {
    window.sampleAborts = [];
    const realFetch = window.fetch;
    window.fetch = (input, options) => {
      options?.signal?.addEventListener('abort', () => window.sampleAborts.push(String(input)), { once: true });
      return realFetch(input, options);
    };
  });
  await page.getByText('React 샘플 둘러보기', { exact: true }).click();
  await page.getByRole('status').filter({ hasText: '목록' }).waitFor();
  await page.getByText('React 샘플 둘러보기', { exact: true }).click();
  await page.waitForFunction(() => window.sampleAborts.includes('/studio-samples/index.json'));
  assert.ok(await page.evaluate(() => window.sampleAborts.includes('/studio-samples/index.json')));
  await page.getByText('React 샘플 둘러보기', { exact: true }).click();
  await page.getByRole('alert').waitFor();
  await page.getByRole('button', { name: '예제 다시 불러오기' }).click();
  await page.frameLocator('iframe').frameLocator('[data-workspace-preview-content]').getByLabel('납입금액').waitFor();
  const stale = manifest(); stale.samples[0].title = 'Aborted response must not appear';
  await held.fulfill({ contentType: 'application/json', body: JSON.stringify(stale) }).catch(() => {});
  assert.equal(await page.getByText('Aborted response must not appear').count(), 0);
});
