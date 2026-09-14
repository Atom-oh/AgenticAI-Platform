const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { build } = require('esbuild');
const { chromium } = require('playwright');
const { buildRenderer } = require('../portal-versions/build.cjs');
const web = path.resolve(__dirname, '..');
const ORIGIN = 'https://portal-versions.invalid';
let prepared;
const assets = () => prepared ||= Promise.all([
  buildRenderer({ write: false }),
  build({ stdin: { loader: 'tsx', resolveDir: web, contents: `
import React from 'react';import {createRoot} from 'react-dom/client';import {flushSync} from 'react-dom';
import Detail from './src/portal/VersionComponentDetail';import './src/portal/portal.css';
const root=createRoot(document.getElementById('app'));
window.showVersion=(binding)=>flushSync(()=>root.render(<Detail binding={binding}/>));
` }, bundle: true, write: false, format: 'iife', outdir: '/portal-versions-test',
    define: { 'process.env.NODE_ENV': '"production"' } }),
]);
async function mount(t) {
  const [renderer, app] = await assets();
  const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKSPACE_CHROMIUM,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  t.after(() => browser.close());
  const context = await browser.newContext({ offline: true, viewport: { width: 1000, height: 900 } });
  const errors = [], outside = [], requests = [];
  await context.routeWebSocket('**/*', socket => socket.close());
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) { outside.push(url.href); return route.abort(); }
    requests.push(url.pathname);
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><html lang="ko"><head><title>Version QA</title><link rel="stylesheet" href="/app.css"></head><body><div id="app"></div><script src="/app.js"></script></body></html>' });
    if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: app.outputFiles.find(f => f.path.endsWith('.js')).text });
    if (url.pathname === '/app.css') return route.fulfill({ contentType: 'text/css', body: app.outputFiles.find(f => f.path.endsWith('.css')).text });
    if (url.pathname.endsWith('/index.json')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(renderer.manifest) });
    if (url.pathname.endsWith('/' + renderer.manifest.renderer.file)) return route.fulfill({ contentType: 'text/html', body: renderer.html });
    errors.push(`unexpected request ${url.pathname}`); return route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(ORIGIN);
  t.after(() => { assert.deepEqual(errors, []); assert.deepEqual(outside, []); });
  const binding = id => {
    const { id: assetId, name, version, exportName, sourceHash } = renderer.manifest.catalog.components.find(c => c.id === id);
    return { kind: 'react-component', id: assetId, name, version, exportName, sourceHash, package: renderer.manifest.catalog.package };
  };
  const show = async id => {
    await page.evaluate(binding => window.showVersion(binding), binding(id));
    await page.locator(`[data-version-id="${id}"][data-version-state="rendered"]`).waitFor();
    return page.frameLocator('iframe[data-portal-version]');
  };
  return { page, renderer, requests, binding, show };
}

test('all 22 IDs render their exact versions and source; sandbox remains isolated and bundle is reused', { timeout: 90_000 }, async t => {
  const { page, renderer, requests, show } = await mount(t);
  for (const c of renderer.manifest.catalog.components) {
    const frame = await show(c.id);
    const marker = frame.locator(`[data-component-id="${c.id}"]`).first();
    assert.equal(await marker.getAttribute('data-component-version'), c.version);
    assert.equal(await page.locator('iframe').getAttribute('sandbox'), 'allow-scripts');
    await page.getByRole('button', { name: /^React 소스/ }).click();
    assert.equal(await page.getByLabel('소스 파일', { exact: true }).inputValue(), c.entry);
    assert.equal(await page.locator('.portal-version-source pre').textContent(), c.files.find(f => f.path === c.entry).content);
  }
  assert.equal(requests.filter(p => p.endsWith('index.json')).length, 1);
  assert.equal(requests.filter(p => p.endsWith('.html')).length, 1);
  assert.equal(await page.evaluate(() => { try { void document.querySelector('iframe').contentWindow.document; return false; } catch { return true; } }), true);
});

test('Select v2 is searchable while v1 remains the original simple selection contract', { timeout: 60_000 }, async t => {
  const { show } = await mount(t);
  let frame = await show('CMP-Select-v1');
  assert.equal(await frame.locator('input[type=search]').count(), 0);
  const v1 = frame.getByRole('combobox').first();
  const second = await v1.locator('option').nth(1).getAttribute('value');
  await v1.selectOption(second);
  assert.equal(await v1.inputValue(), second);
  frame = await show('CMP-Select-v2');
  const search = frame.locator('input[type=search]');
  await search.fill('없는선택항목');
  assert.equal(await frame.getByRole('combobox').locator('option:not([value=""])').count(), 0);
  await search.fill('');
  assert.ok(await frame.getByRole('combobox').locator('option:not([value=""])').count() >= 2);
});

test('source download includes exact TSX and shared file contents; mobile stays contained', { timeout: 60_000 }, async t => {
  const { page, renderer, show } = await mount(t);
  await page.setViewportSize({ width: 390, height: 844 });
  await show('CMP-Select-v2');
  await page.getByRole('button', { name: /^React 소스/ }).click();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '전체 소스 묶음 다운로드 (JSON)' }).click();
  const download = await pending;
  const data = JSON.parse(await fs.readFile(await download.path(), 'utf8'));
  const c = renderer.manifest.catalog.components.find(c => c.id === 'CMP-Select-v2');
  assert.equal(data.sourceHash, c.sourceHash);
  assert.deepEqual(data.files, c.files);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test('mismatched API version and hash remove the prior preview and never alias a same-name component', { timeout: 60_000 }, async t => {
  const { page, binding, show } = await mount(t);
  await show('CMP-Select-v2');
  await page.evaluate(binding => window.showVersion(binding), { ...binding('CMP-Select-v2'), version: '3.0.0' });
  await page.locator('[data-version-state="error"]').waitFor();
  assert.equal(await page.locator('iframe').count(), 0);
  await show('CMP-Select-v2');
  await page.evaluate(binding => window.showVersion(binding), { ...binding('CMP-Select-v2'), sourceHash: 'a'.repeat(64) });
  await page.locator('[data-version-state="error"]').waitFor();
  assert.equal(await page.locator('iframe').count(), 0);
  assert.equal(await page.getByRole('button', { name: /^React 소스/ }).count(), 0);
});
