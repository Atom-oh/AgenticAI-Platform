const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('real Playground retains a result across tool switches but never carries it into a gallery draft', { timeout: 45000 }, async t => {
  const web = path.resolve(__dirname, '..');
  const draft = { draftId: 'draft-b', jobId: 'job-b', title: 'Failing draft B', productCode: 'PRD-DEP-001',
    productName: 'Synthetic product', outputType: 'ux-flow', axis: '흐름', status: '검토중',
    score: 11, passed: false, rounds: 1, bestRound: 1, url: '/b.html', model: 'fixture' };
  const failedRound = { round: 1, score: 11, passed: false, url: '/b.html', failures: [], undetermined: [], elapsedMs: 1 };
  const done = { type: 'studio.done', jobId: 'job-a', draftId: 'draft-a', score: 100, passed: true,
    rounds: 2, maxRounds: 3, bestRound: 2, stopReason: 'passed', url: '/a.html', items: [],
    history: [{ round: 2, score: 100, passed: true, url: '/a.html', failures: [], undetermined: [], elapsedMs: 1 }],
    usage: { inputTokens: 1, outputTokens: 1 }, model: 'fixture', elapsedMs: 1 };
  const result = await build({
    stdin: { resolveDir: web, loader: 'tsx', contents:
      "import React from 'react';import {createRoot} from 'react-dom/client';import Studio from './src/studio/Studio';createRoot(document.getElementById('root')).render(<Studio/>);" },
    bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
    plugins: [{ name: 'service-and-unvisited-workspace-boundaries', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'fixture' }));
      builder.onResolve({ filter: /(?:workspace\/Workspace|\.\/ProcessStudio|\.\/Assets)$/ }, args => {
        if (args.importer === path.join(web, 'src/studio/Studio.tsx')) return { path: args.path, namespace: 'fixture' };
      });
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ loader: 'tsx', resolveDir: web,
        contents: args.path !== 'lib' ? 'export default function Child(){return null;}' : `
export const auth={token:'fixture',studioToken:null};export async function loadConfig(){return {};}
export const sock={
 request:async(action,payload={})=>{
  if(action==='studio_models')return {models:[{id:'fixture',label:'Fixture',provider:'offline'}],defaultModel:'fixture'};
  if(action==='studio_drafts')return {drafts:[${JSON.stringify(draft)}]};
  if(action==='studio_products')return {products:[{code:'PRD-DEP-001',name:'Synthetic product'}]};
  if(action==='studio_spec')return {spec:{productName:'Synthetic product',category:'Synthetic',conditions:[],steps:[],items:[]}};
  if(action==='studio_jobs')return {job:{rounds:[${JSON.stringify(failedRound)}]}};
  if(action==='studio_round')return {round:{round:1,itemsComplete:true,items:[]}};
  return {};
 },
 run:async(action,payload,receive)=>{receive(${JSON.stringify(done)});}
};`,
      }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/a.html' || url.pathname === '/b.html')
      return route.fulfill({ contentType: 'text/html', body: `<p>${url.pathname}</p>` });
    return route.fulfill({ contentType: 'text/html',
      body: `<div id="root"></div><script>${result.outputFiles[0].text.replace(/<\/script/gi, '<\\/script')}</script>` });
  });
  await page.goto('https://studio-draft.invalid/#/studio?tool=play');
  const tools = page.getByRole('navigation', { name: 'UX 제작 도구' });
  await page.getByRole('button', { name: '시안 만들고 정적 검수하기', exact: true }).click();
  await page.locator('.studio-layout').getByText('선택 시안 100점', { exact: false }).waitFor();
  await tools.getByRole('button', { name: '프로세스·흐름 생성', exact: true }).click();
  assert.equal(await page.locator('.studio-layout').isVisible(), false);
  await tools.getByRole('button', { name: 'UX 만들어보기 · 플레이그라운드', exact: true }).click();
  await page.locator('.studio-layout').getByText('선택 시안 100점', { exact: false }).waitFor();
  await tools.getByRole('button', { name: '시안 갤러리', exact: true }).click();
  await page.getByRole('button', { name: '편집 →', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.studio-layout iframe')?.getAttribute('src') === '/b.html');
  await page.locator('.studio-layout').getByRole('button', { name: /R1/ }).waitFor();
  const content = await page.locator('.studio-layout').innerText();
  assert.doesNotMatch(content, /선택 시안 100점|라운드 2 선택|정적 검수 통과/);
  assert.deepEqual(await page.locator('.studio-layout [title="정적 체크리스트 점수 · 실제 동작은 미검증"]').allTextContents(), ['11/100 정적']);
  assert.deepEqual(errors, []);
});
