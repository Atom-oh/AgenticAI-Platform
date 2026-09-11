const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('private previews block frame navigation without blocking trusted app frames', { timeout: 45_000 }, async () => {
  const bundle = await build({
    stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import {PrivatePreview} from './src/workspace/shared';
const root=createRoot(document.getElementById('root'));window.showPreview=(id,executable)=>root.render(<PrivatePreview key={id}
path={'/runs/fixture/blob?kind=html&scenario='+id} title="Boundary preview" format="text/html" executable={executable}/>);`,
      resolveDir: path.resolve(__dirname, '..'), loader: 'tsx' },
    bundle: true, write: false, format: 'iife', jsx: 'automatic',
    plugins: [{ name: 'offline-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'mock' }));
      builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ loader: 'js', contents: "export const auth={token:'offline-boundary-fixture'};" }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  const cases = [
    { id: 'inert-link', executable: false, html: '<a id="action" href="https://outside.invalid/inert">Leave preview</a>' },
    { id: 'script-location', executable: true, html: `<button id="action" onclick="location.href='https://outside.invalid/script'">Leave preview</button>` },
    { id: 'same-origin-location', executable: true, html: `<button id="action" onclick="location.href='/should-not-navigate'">Leave preview</button>` },
    { id: 'attribute-escape', executable: true, html: `"></iframe><script>try{parent.document.body.dataset.compromised='yes'}catch{window.isolated=true}</script>
      <button id="local" onclick="this.textContent='Local interaction works'">Local interaction</button>
      <button id="hash" onclick="location.hash='step-2'">Local hash</button>
      <button id="action" onclick="location.href='https://outside.invalid/escaped'">Leave preview</button>` },
  ];
  const permitted = [], forbidden = [];
  try {
    const context = await browser.newContext({ offline: true });
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'outside.invalid' || url.pathname === '/should-not-navigate') {
        forbidden.push(url.href);
        return route.fulfill({ contentType: 'text/html', body: '<p>Forbidden navigation intercepted locally</p>' });
      }
      if (url.origin === 'https://offline.test' && url.pathname === '/') {
        return route.fulfill({ contentType: 'text/html', body: '<html><head><title>Offline boundary test</title></head><body><div id="root"></div><div id="trusted"></div></body></html>' });
      }
      if (url.origin === 'https://offline.test' && url.pathname === '/studio-api/runs/fixture/blob') {
        const bytes = Buffer.from(cases.find(item => item.id === url.searchParams.get('scenario')).html);
        return route.fulfill({ body: bytes, headers: {
          'Content-Type': 'application/octet-stream', 'X-Content-Type': 'application/octet-stream',
          'X-Total-Size': String(bytes.length), 'X-Chunk-Size': String(bytes.length),
          'X-SHA256': createHash('sha256').update(bytes).digest('hex'),
        } });
      }
      if ((url.origin === 'https://offline.test' && ['/studio/drafts/fixture.html', '/design-runs/fixture/step.html'].includes(url.pathname)) ||
          url.href === 'https://www.atomai.click/AgenticAI-Platform/') {
        permitted.push(url.href);
        return route.fulfill({ contentType: 'text/html', body: '<p>Trusted frame loaded</p>' });
      }
      throw Error('Unexpected intercepted request: ' + url.href);
    });
    const page = await context.newPage(); page.setDefaultTimeout(5000);
    await page.goto('https://offline.test/');
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(() => {
      for (const [title, src] of [
        ['legacy-gallery', '/studio/drafts/fixture.html'],
        ['legacy-process', '/design-runs/fixture/step.html'],
        ['guidebook', 'https://www.atomai.click/AgenticAI-Platform/'],
      ]) {
        const frame = document.createElement('iframe'); frame.title = title; frame.src = src;
        document.getElementById('trusted').append(frame);
      }
    });
    for (const title of ['legacy-gallery', 'legacy-process', 'guidebook']) {
      await page.frameLocator(`iframe[title="${title}"]`).getByText('Trusted frame loaded', { exact: true }).waitFor();
    }
    for (const fixture of cases) {
      await page.evaluate(({ id, executable }) => window.showPreview(id, executable), fixture);
      const outer = page.frameLocator('iframe[title="Boundary preview"]');
      // Works before and after the fix, so the regression fails on actual
      // navigation leakage, rather than merely on a missing wrapper element.
      let preview;
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await outer.locator('iframe[data-workspace-preview-content]').count()) {
          preview = outer.frameLocator('iframe[data-workspace-preview-content]'); break;
        }
        if (await outer.locator('#action').count()) { preview = outer; break; }
        await page.waitForTimeout(20);
      }
      assert(preview, 'Preview content must load');
      if (fixture.id === 'attribute-escape') {
        await preview.getByRole('button', { name: 'Local interaction', exact: true }).click();
        await preview.getByRole('button', { name: 'Local interaction works', exact: true }).waitFor();
        await preview.getByRole('button', { name: 'Local hash', exact: true }).click();
        await preview.getByRole('button', { name: 'Local interaction works', exact: true }).waitFor();
        assert.equal(await page.evaluate(() => document.body.dataset.compromised), undefined);
      }
      await preview.locator('#action').click();
      await page.waitForTimeout(150);
      assert.deepEqual(forbidden, [], `${fixture.id}: preview navigation must be stopped before any request`);
      assert.equal(await page.locator('iframe[title="Boundary preview"]').getAttribute('sandbox'), fixture.executable ? 'allow-scripts' : '');
    }
    assert.equal(permitted.length, 3);
  } finally { await browser.close(); }
});
