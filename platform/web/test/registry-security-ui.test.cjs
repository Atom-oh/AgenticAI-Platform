const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');
let browser, bundle;
before(async () => {
  const root = path.resolve(__dirname, '..');
  const result = await build({
    stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client';
      import RegistryView from './src/views/RegistryView';
      createRoot(document.getElementById('root')).render(<RegistryView/>);`, loader: 'tsx', resolveDir: root },
    bundle: true, write: false, jsx: 'automatic', format: 'iife',
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{ name: 'registry-fixture', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'socket', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
        export const sock = { async request(action, body) {
          window.calls.push({action,body});
          if (action === 'registry_get') return {ok:true,record:window.records.find(r => r.recordVersion === body.version),
            audit:[],versionChain:[]};
          if (action === 'registry_list') return {records:window.records,counts:{total:2,approved:1,
            byType:{CUSTOM:2},byStatus:{APPROVED:1,PENDING_APPROVAL:1}},backend:'synthetic'};
          if (action === 'registry_consumer') return {records:[window.records[0]]};
          if (action === 'surfaces') return {records:[]};
          throw Error('Unexpected mutation: '+action);
        }};` }));
    } }],
  });
  bundle = result.outputFiles[0].text;
  browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
});
after(async () => { await browser?.close(); });

test('Registry displays administrator decisions without offering staff approval', async t => {
  const context = await browser.newContext({ offline: true });
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  await page.setContent('<html lang="ko"><div id="root"></div></html>');
  await page.evaluate(() => {
    window.calls = [];
    window.records = [
      {name:'Button',recordVersion:'v2',recordType:'CUSTOM',subtype:'COMPONENT',status:'APPROVED',
       payload:{},allowedTargets:[],adminRequiredTargets:['DEPRECATED']},
      {name:'Button',recordVersion:'v3',recordType:'CUSTOM',subtype:'COMPONENT',status:'PENDING_APPROVAL',
       payload:{},allowedTargets:[],adminRequiredTargets:['APPROVED','REJECTED']},
    ];
  });
  await page.addScriptTag({ content: bundle });
  await page.locator('tr').filter({ hasText: 'v3' }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText('관리자 검토가 필요합니다.', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '→ 승인', exact: true }).isDisabled(), true);
  assert.doesNotMatch(await page.locator('body').innerText(), /원클릭 반전|① Button v2 →/);
  assert.deepEqual(await page.evaluate(() => window.calls.filter(call => call.action === 'registry_transition')), []);
  await dialog.getByRole('button', { name: '닫기 ✕', exact: true }).click();
  const form = page.locator('details').filter({ hasText: '새 레코드 등록' }).last();
  await form.locator('summary').click();
  assert.deepEqual(await form.locator('select option').allTextContents(), ['MCP', 'SKILL', 'CUSTOM']);
  assert.equal(await form.locator('select').inputValue(), 'MCP');
  assert.equal(await form.getByRole('link').getAttribute('href'), '#/agents');
});
