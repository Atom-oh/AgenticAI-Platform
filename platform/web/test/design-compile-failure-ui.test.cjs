const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('a compile refusal shows escaped private diagnostics and permits explicit regeneration, never approval', { timeout: 45000 }, async () => {
  const root = path.resolve(__dirname, '..');
  const bundle = await build({ stdin: { resolveDir: root, loader: 'tsx', contents: `
    import React from 'react';import {createRoot} from 'react-dom/client';
    import DesignPanel from './src/workspace/DesignPanel';import {WorkspaceScope} from './src/workspace/WorkspaceScope';
    import {createWorkspaceClient} from './src/workspace/client';
    createRoot(document.getElementById('root')).render(<WorkspaceScope.Provider value={{client:createWorkspaceClient({projectId:'p-test'}),project:{id:'p-test'},role:'designer'}}>
      <DesignPanel config={{models:[{id:'test',label:'Test'}],defaultModel:'test'}} onDirty={()=>{}}/></WorkspaceScope.Provider>);` },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', plugins: [{ name: 'identity', setup(b) {
      b.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'auth', namespace: 'fixture' }));
      b.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const auth={token:"synthetic"};' }));
    } }],
  });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKSPACE_CHROMIUM, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ offline: true });
    const posts = [];
    const design = { id: 'broken', title: '합성 설계', status: 'interrupted', version: 3, revision: 1,
      model: 'test', variants: [], approvals: { flow: {} }, error: { code: 'design-compile-failed' } };
    await context.route('**/*', route => {
      const p = new URL(route.request().url()).pathname.replace(/^\/studio-api/, '');
      const send = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
      if (p === '/') return route.fulfill({ contentType: 'text/html', body: '<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>' });
      if (p === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: bundle.outputFiles[0].text });
      assert.equal(route.request().headers()['x-workspace-project'], 'p-test');
      if (p === '/designs/config') return send({ enabled: true, models: ['test'] });
      if (p === '/designs/inputs') return send({ inputs: [] });
      if (p === '/documents') return send({ documents: [] });
      if (p === '/ontology') return send({ nodes: [] });
      if (p === '/git-connections') return send({ connections: [] });
      if (p === '/designs') return send({ designs: [design] });
      if (p === '/designs/broken') return send({ design, failure: { stage: 'compile', status: 'failed', approved: false,
        diagnostics: ['Synthetic type mismatch'], files: { 'src/App.tsx': '<script>parent.executed=true</script>' } } });
      if (p === '/designs/broken/generate') { posts.push(route.request().postDataJSON()); return send({ design }); }
      throw new Error('Unexpected route ' + p);
    });
    const page = await context.newPage(); await page.goto('https://compile.invalid/');
    await page.getByLabel('진행 중인 설계').selectOption('broken');
    const failure = page.getByRole('region', { name: '컴파일 실패 근거' });
    await failure.getByText('Synthetic type mismatch').waitFor();
    await failure.getByText('src/App.tsx · 미검증 소스', { exact: true }).click();
    assert.equal(await failure.locator('pre').innerText(), '<script>parent.executed=true</script>');
    assert.equal(await page.evaluate(() => window.executed), undefined);
    assert.equal(await page.getByRole('button', { name: '이 시안 승인', exact: true }).count(), 0);
    await page.getByRole('button', { name: '시안 만들기', exact: true }).click();
    assert.equal(posts.length, 1); assert.equal(posts[0].version, 3); assert.equal(posts[0].maxRounds, 3);
    assert(posts[0].requestId);
  } finally { await browser.close(); }
});
