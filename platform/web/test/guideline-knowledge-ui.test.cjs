const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('guideline proposals preserve retry identity, exact citations and human role boundaries',
  { timeout: 60000 }, async () => {
    const root = path.resolve(__dirname, '..'), submissions = [], errors = [];
    const resultHash = 'a'.repeat(64), generation = 'b'.repeat(64);
    const bundle = await build({
      stdin: { contents: `
        import React,{useMemo,useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import GuidelineKnowledge from './src/workbench/GuidelineKnowledge';
        import {WorkbenchScope} from './src/workbench/shared';
        import {createWorkspaceClient} from './src/workspace/client';
        function App(){
          const [role,setRole]=useState('owner'),[projectId,setProjectId]=useState('p-test');
          const client=useMemo(()=>createWorkspaceClient({projectId}),[projectId]);
          return <WorkbenchScope.Provider value={{client,project:{id:projectId},
            overview:{role,actorId:'alice'},params:new URLSearchParams(),navigate:()=>{},refresh:()=>{}}}>
            <button onClick={()=>setRole('developer')}>개발자 역할 보기</button>
            <button onClick={()=>setProjectId('p-other')}>다른 프로젝트 보기</button>
            <GuidelineKnowledge target={{id:'atom-1',title:'약관 동의',reviewState:'approved'}}
              generation="${generation}" onPublished={()=>{window.adoptions=(window.adoptions||0)+1;}}/>
          </WorkbenchScope.Provider>;
        }
        createRoot(document.getElementById('root')).render(<App/>);`,
        resolveDir: root, loader: 'tsx' },
      bundle: true, write: false, format: 'iife', jsx: 'automatic',
      define: { 'process.env.NODE_ENV': '"development"' },
      plugins: [{ name: 'auth', setup(builder) {
        builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'auth', namespace: 'stub' }));
        builder.onLoad({ filter: /.*/, namespace: 'stub' },
          () => ({ contents: "export const auth={token:'synthetic-token',studioToken:null};", loader: 'js' }));
      } }],
    });
    const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM,
      headless: true, args: ['--no-sandbox'] });
    let createFirst = true, adoptFirst = true;
    const proposal = id => ({
      artifact: { id, name: id === 'good' ? '원문 인용 후보' : '수정이 필요한 후보', status: 'completed', createdBy: 'alice' },
      version: 3, generation, resultHash,
      result: { approvalEffect: false, rejected: id === 'good' ? [] : [{ reason: 'unknown-quote' }],
        rules: [{ ruleId: 'rule-1', statement: '약관 동의 단계를 제공하세요.', required: true, severity: 'critical',
          citation: { sourceId: 'document-1', page: 2, quote: '가입 절차에는 약관 동의 단계가 있어야 합니다.',
            derivativeHash: 'c'.repeat(64) } }] },
    });
    try {
      const context = await browser.newContext({ offline: true });
      await context.route('**/*', async route => {
        const url = new URL(route.request().url()), method = route.request().method();
        const send = (body, status = 200) => route.fulfill({
          status, contentType: 'application/json', body: JSON.stringify(body) });
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8',
          body: '<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>' });
        if (url.pathname === '/app.js') return route.fulfill({
          contentType: 'application/javascript', body: bundle.outputFiles[0].text });
        const project = route.request().headers()['x-workspace-project'];
        assert(['p-test', 'p-other'].includes(project));
        const endpoint = url.pathname.replace(/^\/studio-api/, '');
        if (endpoint === '/designs/config') return send({ enabled: true, models: ['global.anthropic.claude-sonnet-5'] });
        if (endpoint === '/designs/inputs') return send({ inputs: [
          { id: 'decision-1', title: `${project} 가이드`, kind: 'document-pages' },
          { id: 'prompt-1', title: '일반 프롬프트', kind: 'prompt-text' }] });
        if (endpoint === '/ontology/knowledge' && method === 'GET') return send({
          items: project === 'p-test' ? [proposal('good'), proposal('bad')] : [] });
        if (endpoint === '/ontology/knowledge/guidelines' && method === 'POST') {
          submissions.push({ kind: 'create', body: route.request().postDataJSON() });
          if (createFirst) { createFirst = false; return send({ error: '접수 결과를 다시 확인하세요.' }, 503); }
          return send({ artifact: proposal('good').artifact }, 202);
        }
        if (endpoint === '/ontology/knowledge/good/publish' && method === 'POST') {
          submissions.push({ kind: 'adopt', body: route.request().postDataJSON() });
          if (adoptFirst) { adoptFirst = false; return send({ error: '연결 결과를 다시 확인하세요.' }, 503); }
          return send({ approvalEffect: false, publication: {} }, 201);
        }
        if (endpoint === '/ontology/knowledge/good') return send(proposal('good'));
        if (endpoint === '/ontology/knowledge/bad') return send(proposal('bad'));
        assert.fail(`Unexpected API request: ${method} ${endpoint}`);
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('https://guidelines.test/');
      const create = page.getByRole('button', { name: '가이드 인용 규칙 후보 생성', exact: true });
      const adopt = page.getByRole('button', { name: '인용 검토 후 정본 초안 연결', exact: true });
      assert(await create.isDisabled());
      assert.equal(await page.getByText('일반 프롬프트', { exact: true }).count(), 0);
      await page.getByRole('checkbox', { name: 'p-test 가이드', exact: true }).check();
      await page.getByLabel('가이드 추출 모델').selectOption('global.anthropic.claude-sonnet-5');
      await create.click();
      await page.getByText('접수 결과를 다시 확인하세요.', { exact: true }).waitFor();
      await create.click();
      await page.getByText('원문 2페이지: 가입 절차에는 약관 동의 단계가 있어야 합니다.', { exact: true }).waitFor();
      assert.deepEqual(submissions[0].body, submissions[1].body);
      assert.equal(submissions[1].body.expectedGeneration, generation);
      assert.deepEqual(submissions[1].body.decisionIds, ['decision-1']);
      assert.deepEqual(submissions[1].body.nodeIds, ['atom-1']);
      await adopt.click();
      await page.getByText('연결 결과를 다시 확인하세요.', { exact: true }).waitFor();
      await adopt.click();
      await page.getByText('검토한 후보를 정본 초안에 연결했습니다. 정책 승인 단계는 별도로 진행하세요.', { exact: true }).waitFor();
      assert.deepEqual(submissions[2].body, submissions[3].body);
      assert.equal(submissions[3].body.version, 3);
      assert.equal(submissions[3].body.resultHash, resultHash);
      assert.equal(await page.evaluate(() => window.adoptions), 1);
      await page.getByRole('button', { name: '수정이 필요한 후보 · 완료', exact: true }).click();
      await page.getByText('수정이 필요한 추출 결과', { exact: true }).waitFor();
      assert(await adopt.isDisabled());
      await page.getByRole('button', { name: '원문 인용 후보 · 완료', exact: true }).click();
      await page.getByText('수정이 필요한 추출 결과', { exact: true }).waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: '개발자 역할 보기', exact: true }).click();
      assert(await create.isDisabled() && await adopt.isDisabled());
      assert(await page.getByLabel('가이드 추출 모델').isDisabled());
      await page.getByRole('button', { name: '다른 프로젝트 보기', exact: true }).click();
      await page.getByRole('checkbox', { name: 'p-other 가이드', exact: true }).waitFor();
      await page.getByText('원문 2페이지: 가입 절차에는 약관 동의 단계가 있어야 합니다.', { exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await page.getByRole('button', { name: '원문 인용 후보 · 완료', exact: true }).count(), 0);
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
