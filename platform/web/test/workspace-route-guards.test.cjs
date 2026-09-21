const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('history protects unsaved scope and product switches never reopen the previous product contract',
  { timeout: 60000 }, async () => {
    const root = path.resolve(__dirname, '..');
    const built = await build({
      stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import Workspace from './src/workspace/Workspace';
        createRoot(document.getElementById('root')).render(<Workspace/>);`, resolveDir: root, loader: 'tsx' },
      bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
      define: { 'process.env.NODE_ENV': '"development"' }, plugins: [{ name: 'auth', setup(builder) {
        builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
        builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents: 'export const auth={token:"offline"};' }));
      } }],
    });
    const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true, args: ['--no-sandbox'] });
    try {
      const context = await browser.newContext({ offline: true });
      const project = id => ({ id, name: id, version: 1, members: { actor: { role: 'owner', displayName: 'Synthetic owner' } } });
      const products = ['a', 'b'].map(id => ({ id, projectId: 'p', version: 1, title: `Product ${id}`, description: '',
        conditions: [], steps: [], notices: [], publishedGuidelineId: `guide-${id}`, publishedRevision: 1, ontologyHash: 'a'.repeat(64) }));
      const saved = { id: 'contract-a', version: 1, productId: 'a', projectId: 'p', guidelineId: 'guide-a',
        ontologyHash: 'a'.repeat(64), status: 'draft', title: 'Request A', brief: 'Original request A',
        assetIds: [], viewport: { width: 390, height: 844 }, unresolved: [], rules: [{
          id: 'R1', title: 'Display', required: true, source: { kind: 'manual' },
          steps: [{ action: 'expectVisible', target: 'entry', targetLabel: 'Entry', value: true }],
        }] };
      const calls = [], errors = [];
      await context.route('**/*', route => {
        const url = new URL(route.request().url()), target = url.pathname.replace('/studio-api', '');
        const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
        if (url.hostname !== 'route.invalid') return route.abort();
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><body><div id="root"></div></body></html>' });
        calls.push(target);
        if (target === '/config') return json({ actorId: 'actor', models: [{ id: 'test', label: 'Test' }], defaultModel: 'test',
          extensions: ['txt'], maxFileBytes: 52428800, chunkBytes: 2097152 });
        if (target === '/projects') return json({ projects: [project('p'), project('q')] });
        if (target.startsWith('/projects/')) return json({ project: project(target.split('/')[2]) });
        if (target === '/products') return json({ products });
        if (/^\/products\/[ab]\/ontology$/.test(target)) return json({ ontology: { schemaVersion: 1, projectId: 'p',
          productId: target.split('/')[2], guidelineId: url.searchParams.get('revision'), hash: 'a'.repeat(64), nodes: [], edges: [] } });
        if (target.endsWith('/impact')) return json({ affectedRuns: [] });
        if (target === '/contracts') return json({ contracts: [saved] });
        if (target === '/contracts/contract-a') return json({ contract: saved });
        if (target === '/contracts/missing') return json({ error: '연결된 작업을 찾을 수 없습니다.' }, 404);
        for (const name of ['assets', 'runs', 'batches', 'comments', 'releases'])
          if (target === '/' + name) return json({ [name]: [] });
        if (target === '/git-connections') return json({ connections: [] });
        throw new Error('Unexpected route ' + target);
      });
      const page = await context.newPage(); page.setDefaultTimeout(8000);
      page.on('pageerror', error => errors.push(String(error)));
      await page.goto('https://route.invalid/#/studio?projectId=p&productId=a&contractId=contract-a&step=define');
      await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
      await page.addScriptTag({ content: built.outputFiles[0].text });
      const brief = page.getByLabel('사용자 목적·완료 조건');
      await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(element => element.value === 'Original request A'));
      await brief.fill('Unsaved request A');
      page.once('dialog', dialog => dialog.dismiss());
      await page.evaluate(() => { location.hash = '#/studio?projectId=q&step=define'; });
      await page.waitForFunction(() => location.hash.includes('projectId=p'));
      assert.equal(await brief.inputValue(), 'Unsaved request A');
      assert.equal(calls.includes('/projects/q'), false);
      const reads = calls.filter(target => target === '/contracts/contract-a').length;
      page.once('dialog', dialog => dialog.accept());
      await page.getByLabel('공유 상품', { exact: true }).selectOption('b');
      await page.waitForFunction(() => location.hash.includes('productId=b') && !location.hash.includes('contractId='));
      assert.equal(await brief.inputValue(), '');
      assert.equal(calls.filter(target => target === '/contracts/contract-a').length, reads);
      await page.evaluate(() => { location.hash = '#/studio?projectId=p&productId=a&contractId=contract-a&step=define'; });
      await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(element => element.value === 'Original request A'));
      await page.evaluate(() => { location.hash = '#/studio?projectId=p&productId=a&contractId=missing&step=define'; });
      await page.getByRole('button', { name: '연결된 작업 다시 조회' }).waitFor();
      assert.equal(await brief.inputValue(), '');
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
