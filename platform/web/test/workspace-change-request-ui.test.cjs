const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('change request preserves baseline, file scope and screen identities through readable review and approval',
  { timeout: 60000 }, async () => {
    const root = path.resolve(__dirname, '..');
    const bundle = await build({
      stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';
        import RulesPanel from './src/workspace/RulesPanel';import {WorkspaceScope} from './src/workspace/WorkspaceScope';
        import {createWorkspaceClient} from './src/workspace/client';
        const config={actorId:'actor',models:[{id:'model',label:'Test model'}],defaultModel:'model'};
        const runs=[{id:'base',outputType:'react',approval:{round:1},contract:{title:'Approved baseline'}}];
        const scope={client:createWorkspaceClient(),project:null,actorId:'actor',role:'owner'};
        function App(){const [stage,setStage]=React.useState('define');return <WorkspaceScope.Provider value={scope}>
          <button onClick={()=>setStage(stage==='define'?'design':'define')}>설계 단계 전환</button>
          <RulesPanel config={config} assets={[]} selected={[]} contracts={[]} runs={runs}
            stage={stage} refresh={()=>{}} onApproved={()=>{}} onEditing={()=>{}} />
        </WorkspaceScope.Provider>}createRoot(document.getElementById('root')).render(<App/>);`,
        resolveDir: root, loader: 'tsx' },
      bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
      define: { 'process.env.NODE_ENV': '"development"' },
      plugins: [{ name: 'auth', setup(builder) {
        builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
        builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents: 'export const auth={token:"offline"};' }));
      } }],
    });
    const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
      args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, offline: true });
      let saved, proposal, approved = false;
      const calls = [], errors = [];
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname !== 'change.invalid') return route.abort();
        const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><body><div id="root"></div></body></html>' });
        const target = url.pathname.replace('/studio-api', ''), method = route.request().method();
        const body = method === 'GET' ? null : route.request().postDataJSON();
        calls.push({ target, method, body });
        if (target === '/runs/base/baseline') return json({ baseline: { runId: 'base', round: 1, sourceHash: 'a'.repeat(64) },
          files: ['src/App.tsx', 'src/pages/keep.tsx'] });
        if (target === '/contracts' && method === 'POST') {
          saved = { ...body, id: 'work', version: 1, status: 'draft' }; return json({ contract: saved });
        }
        if (target === '/contracts/propose') {
          proposal = body;
          saved = { ...saved, ...body, id: 'proposal', version: 1, status: 'draft',
            rules: body.changeRequest.screens.map((screen, index) => ({
              id: `rule-${index}`, title: `${screen.title} 표시 확인`, required: true, screenId: screen.id, scenario: 'entry',
              source: { kind: 'inferred' }, steps: [{ action: 'expectVisible', target: screen.id, targetLabel: screen.title, value: true }],
            })) };
          return json({ job: { id: 'job', status: 'queued', task: 'propose' } });
        }
        if (target === '/jobs/job') return json({ job: { id: 'job', task: 'propose', status: 'completed', result: { contractId: 'proposal' } } });
        if (target === '/contracts/proposal' && method === 'GET') return json({ contract: saved });
        if (target === '/contracts/proposal/approve') {
          approved = true; saved = { ...saved, status: 'approved', version: 2 }; return json({ contract: saved });
        }
        throw new Error(`Unexpected ${method} ${target}`);
      });
      const page = await context.newPage(); page.setDefaultTimeout(8000);
      page.on('pageerror', error => errors.push(String(error)));
      page.on('dialog', dialog => dialog.accept());
      await page.goto('https://change.invalid/');
      await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
      await page.addScriptTag({ content: bundle.outputFiles[0].text });
      await page.getByLabel('사용자 목적·완료 조건').fill('기존 가입 순서를 유지하고 두 화면과 배너 문구를 변경합니다.');
      await page.getByRole('button', { name: '설계 단계 전환' }).click();
      await page.getByLabel('화면 요소', { exact: true }).first().fill('담당자가 편집한 검사 대상');
      await page.getByRole('button', { name: '설계 단계 전환' }).click();
      await page.getByRole('button', { name: '두 화면·한 슬롯으로 범위 잡기' }).click();
      assert.equal(await page.locator('.ws-scope-card').count(), 3);
      await page.getByLabel('적용 채널').fill('합성 모바일 앱');
      await page.getByLabel('요청 담당').fill('합성 요청자');
      await page.getByText('승인된 React 시안에서 변경하기', { exact: true }).click();
      await page.getByLabel('기준 시안', { exact: true }).selectOption('base');
      await page.getByLabel('src/App.tsx', { exact: true }).check();
      assert.equal(await page.getByLabel('src/pages/keep.tsx', { exact: true }).isChecked(), false);
      await page.getByRole('button', { name: '업무 요청 저장', exact: true }).click();
      await page.getByText(/저장된 규칙 v1/).waitFor();
      assert.equal(saved.rules.length, 1);
      assert.equal(saved.rules[0].steps[0].targetLabel, '담당자가 편집한 검사 대상');
      assert.deepEqual(saved.changeRequest.allowedFiles, ['src/App.tsx']);
      assert.equal(saved.changeRequest.screens.filter(screen => screen.kind === 'slot').length, 1);
      await page.getByRole('button', { name: '설계 단계 전환' }).click();
      await page.getByRole('button', { name: '선택한 파일로 규칙 제안받기' }).click();
      await page.getByText('AI 제안은 아직 승인되지 않았습니다. 근거와 단계를 확인하세요.', { exact: true }).waitFor();
      assert.equal(proposal.changeRequest.baseline.sourceHash, 'a'.repeat(64));
      assert.equal(await page.locator('.ws-rule-readable').count(), 3);
      assert.equal(await page.locator('.ws-rule-details[open]').count(), 0);
      await page.getByLabel('이 버전의 근거와 모든 확인 단계를 검토했습니다').check();
      await page.getByRole('button', { name: '버전 1 규칙 승인', exact: true }).click();
      await page.getByRole('button', { name: '승인 기준으로 시안·검수' }).waitFor();
      assert.equal(approved, true);
      for (const width of [390, 900, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      }
      if (process.env.WORKSPACE_QA_DIR) {
        fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'change-request-review.png'), fullPage: true });
      }
      assert.deepEqual(errors, []);
      assert(calls.some(call => call.target === '/contracts' && call.body.changeRequest.requester === '합성 요청자'));
    } finally { await browser.close(); }
  });
