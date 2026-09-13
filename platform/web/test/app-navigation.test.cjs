// Exercise the actual app router and login boundary. Page contents and AWS
// clients are explicit doubles; Portal's own query handling has separate tests.
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

const web = path.resolve(__dirname, '..');
const origin = 'https://app-navigation.invalid';
let browser, script;
before(async () => {
  const result = await build({
    stdin: {
      contents: 'import React from "react"; import {createRoot} from "react-dom/client"; import App from "./src/App"; createRoot(document.getElementById("root")).render(<App/>);',
      resolveDir: web, loader: 'tsx',
    },
    bundle: true, write: false, format: 'iife', jsx: 'automatic',
    plugins: [{
      name: 'explicit-page-and-service-doubles',
      setup(plugin) {
        plugin.onResolve({ filter: /^\.\// }, args => {
          if (args.importer === path.join(web, 'src/App.tsx')) {
            return { path: args.path, namespace: 'app-double' };
          }
        });
        plugin.onLoad({ filter: /.*/, namespace: 'app-double' }, args => {
          if (args.path === './lib') return {
            contents: 'export const auth={email:"synthetic@example.invalid",logout(){}};export async function login(){};export async function loadConfig(){return {}};export const sock={request:async()=>({})};',
            loader: 'js',
          };
          const name = args.path.split('/').at(-1);
          const marker = label => `()=>React.createElement("div",{"data-view":${JSON.stringify(label)}},${JSON.stringify(label)})`;
          const contents = args.path === './Views'
            ? ['Agents', 'Dashboard', 'Explore', 'Frame', 'TwoPlane'].map(n => `export const ${n}=${marker(n)};`).join('')
            : `export default ${marker(name)};`;
          return { contents: 'import React from "react";' + contents, loader: 'js', resolveDir: web };
        });
      },
    }],
  });
  script = result.outputFiles[0].text;
  browser = await chromium.launch({ headless: true, executablePath: process.env.WORKSPACE_CHROMIUM });
});
after(async () => { await browser?.close(); });

async function authenticatedPage(t, hash) {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.route('**/*', route => route.request().url().startsWith(origin + '/')
    ? route.fulfill({ contentType: 'text/html', body: `<div id="root"></div><script>${script.replace(/<\/script/gi, '<\\/script')}</script>` })
    : route.abort());
  await page.goto(origin + '/' + hash);
  await page.getByPlaceholder('비밀번호', { exact: true }).fill('synthetic-test-only');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  return page;
}

test('asset and component bookmarks mount Portal after login and keep their query', async t => {
  for (const hash of ['#/portal?id=CMP-Button-v2', '#/portal?component=Checkbox']) {
    const page = await authenticatedPage(t, hash);
    await page.locator('[data-view="Portal"]').waitFor({ timeout: 2000 });
    assert.equal(new URL(page.url()).hash, hash);
  }
});

test('document evidence and saved analysis links preserve their identity after login', async t => {
  for (const [hash, view] of [
    ['#/documents?documentId=d-a&revisionId=d-a--r000001&paragraph=p000003&projectId=team-1&textHash=' + 'a'.repeat(64), 'LibraryPage'],
    ['#/s1?analysisId=ana-1&projectId=team-1', 'S1'],
  ]) {
    const page = await authenticatedPage(t, hash);
    await page.locator(`[data-view="${view}"]`).waitFor({ timeout: 2000 });
    assert.equal(new URL(page.url()).hash, hash);
  }
});

test('same-page asset links keep Portal mounted; browser history retains selection queries', async t => {
  const page = await authenticatedPage(t, '#/portal');
  await page.locator('[data-view="Portal"]').waitFor();
  await page.evaluate(() => { location.hash = '#/portal?id=PRC-000'; });
  await page.waitForFunction(() => location.hash === '#/portal?id=PRC-000');
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.locator('[data-view="Portal"]').waitFor({ timeout: 2000 });
  await page.getByRole('button', { name: '대시보드', exact: false }).click();
  await page.locator('[data-view="Dashboard"]').waitFor();
  await page.goBack();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.locator('[data-view="Portal"]').waitFor({ timeout: 2000 });
  assert.equal(new URL(page.url()).hash, '#/portal?id=PRC-000');
});

test('legacy route aliases and the empty home route remain supported', async t => {
  const legacy = await authenticatedPage(t, '#/twoplane?source=bookmark');
  await legacy.locator('[data-view="TwoPlane"]').waitFor({ timeout: 2000 });
  const home = await authenticatedPage(t, '');
  await home.locator('[data-view="Dashboard"]').waitFor({ timeout: 2000 });
});
