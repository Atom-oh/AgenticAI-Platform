const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('Studio exposes every tool, restores bookmarks/history and retains edits on tool switches', { timeout: 45000 }, async t => {
  const web = path.resolve(__dirname, '..');
  const result = await build({
    stdin: { resolveDir: web, loader: 'tsx', contents:
      "import React from 'react';import {createRoot} from 'react-dom/client';import Studio from './src/studio/Studio';createRoot(document.getElementById('root')).render(<Studio/>);" },
    bundle: true, write: false, jsx: 'automatic', format: 'iife',
    plugins: [{ name: 'studio-child-boundaries', setup(builder) {
      builder.onResolve({ filter: /^\.\.?\// }, args => {
        if (args.importer === path.join(web, 'src/studio/Studio.tsx')) return { path: args.path, namespace: 'fixture' };
      });
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({
        loader: 'tsx', resolveDir: web, contents: args.path === '../lib'
          ? "export const auth={token:'fixture'};export const sock={request:async()=>({})};"
          : `import React,{useState} from 'react';export default function Child(){const [text,setText]=useState('');return <label>${args.path}<input aria-label="${args.path}" value={text} onChange={e=>setText(e.target.value)}/></label>;}`,
      }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => route.fulfill({ contentType: 'text/html',
    body: `<div id="root"></div><script>${result.outputFiles[0].text.replace(/<\/script/gi, '<\\/script')}</script>` }));
  await page.goto('https://studio-tools.invalid/#/studio?projectId=team-1&productId=product-1&contractId=work-1&tool=play');
  const tools = page.getByRole('navigation', { name: 'UX 제작 도구' });
  assert.equal(await tools.getByRole('button').count(), 5);
  await page.getByLabel('./Playground', { exact: true }).fill('keep this draft');
  await tools.getByRole('button', { name: '프로세스·흐름 생성', exact: true }).click();
  await page.getByLabel('./ProcessStudio', { exact: true }).fill('keep this flow');
  assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('tool'), 'process');
  await page.goBack();
  await page.getByLabel('./Playground', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('./Playground', { exact: true }).inputValue(), 'keep this draft');
  await page.goForward();
  await page.getByLabel('./ProcessStudio', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('./ProcessStudio', { exact: true }).inputValue(), 'keep this flow');
  await tools.getByRole('button', { name: 'UX 설계 작업실', exact: true }).click();
  await page.getByLabel('../workspace/Workspace', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).hash, '#/studio?projectId=team-1&productId=product-1&contractId=work-1');
  await page.reload();
  await page.getByLabel('../workspace/Workspace', { exact: true }).waitFor();
});
