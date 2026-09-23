const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
let browser, bundle;
before(async () => {
  const built = await build({
    stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client';
      import AgentBuilder from './src/views/AgentBuilder';
      createRoot(document.getElementById('root')).render(<AgentBuilder/>);`, loader: 'tsx', resolveDir: root },
    bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{ name: 'socket-fixture', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'socket', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
        export const sock = {
          async request(action, payload) {
            window.calls.push({action,payload});
            if (action === 'agents_catalog') return window.catalog;
            if (action === 'agent_transition') return {type:action,ok:true,record:{status:'PENDING_APPROVAL'},
              request:{name:'synthetic-request',status:'PENDING_ADMIN'},agentcoreRegistry:{status:'PENDING_ADMIN'}};
            if (action === 'agent_create') return {type:action,ok:true,
              record:{name:payload.name,recordVersion:'v1',status:'PENDING_APPROVAL',payload:{title:payload.title}},
              harness:{arn:null,status:'PENDING_ADMIN'},agentcoreRegistry:{status:'PENDING_ADMIN'}};
            return {type:action,ok:true};
          },
          async run(action,payload,onEvent) {
            window.calls.push({action,payload});
            onEvent({type:'agent.done',sessionId:'client-session',runtimeSessionId:'server-session',
              toolsMissing:['missing_tool'],code:502,error:'Configured Gateway tools are unavailable',
              usage:{inputTokens:0,outputTokens:0}, ...window.completion});
          }
        };` }));
    } }],
  });
  bundle = built.outputFiles[0].text;
  browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
});
after(async () => { await browser?.close(); });

async function pageFor(t, status) {
  const context = await browser.newContext({ offline: true });
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  await page.setContent('<html lang="ko"><div id="root"></div></html>');
  await page.evaluate(status => {
    window.calls = [];
    window.catalog = {type:'agents_catalog',agents:[{
      name:'security_probe',version:'v1',title:'보안 검증 에이전트',description:'Synthetic',status,
      harnessStatus:'none',harnessArn:null,agentcoreStatus:null,model:'global.anthropic.claude-sonnet-5',
      allowedTools:['missing_tool'],skills:[],memory:false,scenario:'custom',runtime:'agentcore-runtime/strands'}],
      tools:[{name:'missing_tool',description:'Synthetic'}],skills:[],models:['global.anthropic.claude-sonnet-5'],
      defaultModel:'global.anthropic.claude-sonnet-5',gateway:{arn:'',url:''}};
  }, status);
  await page.addScriptTag({ content: bundle });
  await page.getByText('보안 검증 에이전트', { exact: true }).click();
  return page;
}

test('missing configured tools are visible with an unsuccessful invocation', async t => {
  const page = await pageFor(t, 'APPROVED');
  await page.getByPlaceholder('메시지', { exact: true }).fill('Synthetic hello');
  await page.getByRole('button', { name: '보내기', exact: true }).click();
  await page.getByText('도구 연결 미완료 · missing_tool', { exact: true }).waitFor();
  assert.match(await page.locator('body').innerText(), /Configured Gateway tools are unavailable/);
});

for (const completion of [
  {error: undefined, toolsMissing: ['missing_tool'], code: 502},
  {error: undefined, toolsMissing: [], code: 409},
]) {
  test(`terminal code ${completion.code} fails even without an error body`, async t => {
    const page = await pageFor(t, 'APPROVED');
    await page.evaluate(completion => { window.completion = completion; }, completion);
    await page.getByPlaceholder('메시지', { exact: true }).fill('Synthetic hello');
    await page.getByRole('button', { name: '보내기', exact: true }).click();
    await page.getByText('⚠ 호출 실패: 에이전트 요청을 완료하지 못했습니다.', { exact: true }).waitFor();
    assert.doesNotMatch(await page.locator('body').innerText(), /입력 0 · 출력 0 토큰/);
    await page.getByPlaceholder('메시지', { exact: true }).fill('Synthetic next turn');
    await page.getByRole('button', { name: '보내기', exact: true }).click();
    const calls = await page.evaluate(() => window.calls.filter(call => call.action === 'agent_invoke'));
    assert.equal(calls.length, 2);
    assert.equal(calls[1].payload.sessionId, 'client-session');
  });
}

test('an accepted administration request is not shown as completed approval', async t => {
  const page = await pageFor(t, 'PENDING_APPROVAL');
  await page.getByRole('button', { name: '관리자 승인 요청', exact: true }).click();
  await page.getByText('관리자 승인 요청을 접수했습니다.', { exact: true }).waitFor();
  assert.doesNotMatch(await page.locator('body').innerText(), /승인됨 —/);
  assert.equal(await page.evaluate(() => window.calls.filter(call => call.action === 'agent_transition').length), 1);
});

test('a saved local specification is not displayed as a provisioned service', async t => {
  const page = await pageFor(t, 'APPROVED');
  await page.getByPlaceholder('card_benefit_agent', { exact: true }).fill('synthetic_pending');
  await page.getByPlaceholder('카드 혜택 상담 에이전트', { exact: true }).fill('합성 검토 명세');
  await page.getByRole('button', { name: 'missing_tool', exact: true }).click();
  await page.getByRole('button', { name: '명세 저장 → 승인 대기', exact: true }).click();
  await page.getByText('관리자 처리 대기 — 실행 환경과 Registry 미러가 아직 준비되지 않았습니다.', { exact: true }).waitFor();
  assert.doesNotMatch(await page.locator('body').innerText(), /PENDING_ADMIN/);
});
