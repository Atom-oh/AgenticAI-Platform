const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('reverse proposals show independent evidence, preserve retries and respect scope and role',
  { timeout: 60000 }, async () => {
    const root = path.resolve(__dirname, '..'), submissions = [], errors = [], selections = [];
    const generation = 'b'.repeat(64), resultHash = 'a'.repeat(64);
    const bundle = await build({
      stdin: { contents: `
        import React,{useMemo,useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import ScreenDerivation from './src/workbench/ScreenDerivation';
        import AssetLayers from './src/workbench/AssetLayers';
        import {WorkbenchScope} from './src/workbench/shared';
        import {createWorkspaceClient} from './src/workspace/client';
        const screens=['first','second'].map(id=>({id,type:'Screen',title:id,reviewState:'approved'}));
        function App(){
          const [role,setRole]=useState('owner'),[projectId,setProjectId]=useState('p-test');
          const [bound,setBound]=useState(false);
          const client=useMemo(()=>createWorkspaceClient({projectId}),[projectId]);
          return <WorkbenchScope.Provider value={{client,project:{id:projectId},
            overview:{role,actorId:'alice'},params:new URLSearchParams(),navigate:()=>{},refresh:()=>{}}}>
            <button onClick={()=>setRole('developer')}>개발자 역할 보기</button>
            <button onClick={()=>setProjectId('p-other')}>다른 프로젝트 보기</button>
            <button onClick={()=>setBound(true)}>GUI 원본 연결 보기</button>
            <ScreenDerivation nodes={screens} generation="${generation}" onPublished={()=>window.adoptions=(window.adoptions||0)+1}/>
            <AssetLayers projectId={projectId} nodes={[]} edges={[]} onSelect={id=>window.selections.push(id)}
              node={{id:'atom',type:'Atom',title:'안내',revision:1,reviewState:'approved',
                sourceRefs:bound?[{sourceKind:'asset',sourceId:'image-source',sha256:'${resultHash}'}]:[],
                properties:{uxModel:{layers:{wireframe:{blocks:['lower-asset']},
                  gui:{snapshotHash:'${resultHash}',width:640,height:480},
                  code:{importPath:'@studio/approved-ui',exportName:'Notice',props:{}}}}}}}/>
          </WorkbenchScope.Provider>;
        }
        window.selections=[];
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
    const proposal = (id = 'good') => ({
      operation: 'design.derive', artifact: { id, name: id === 'good' ? '반복 안내' : '근거 부족', status: 'completed' },
      version: 3, resultHash, result: { approvalEffect: false,
        rejected: id === 'good' ? [] : [{ reason: 'independent-screen-support-required' }],
        proposals: [{ id: 'candidate', level: 'Molecule', fileSupport: 3, screenSupport: id === 'good' ? 2 : 1,
          screenBindings: [{ id: 'first', revision: 1, contentHash: generation }], identities: ['parts#Notice'],
          metadata: { title: '안내 구성', intent: '일관된 안내 구조' }, eligible: id === 'good' }],
        graph: { nodes: id === 'good' ? [{}] : [] }, coverage: { runtimeCompositionObserved: false, unresolved: [] },
        analysisEvidence: { inputHash: resultHash, execution: { backend: 'agentcore-code-interpreter' } } },
    });
    try {
      const context = await browser.newContext({ offline: true, viewport: { width: 390, height: 844 } });
      await context.route('**/*', async route => {
        const url = new URL(route.request().url()), method = route.request().method();
        const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8',
          body: '<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>' });
        if (url.pathname === '/app.js') return route.fulfill({
          contentType: 'application/javascript', body: bundle.outputFiles[0].text });
        const project = route.request().headers()['x-workspace-project'];
        assert(['p-test', 'p-other'].includes(project));
        const endpoint = url.pathname.replace(/^\/studio-api/, '');
        if (endpoint === '/designs/config') return send({ enabled: true, models: ['global.anthropic.claude-sonnet-5'] });
        if (endpoint === '/designs/inputs') return send({ inputs: [{ id: 'doc', title: '화면 원문', kind: 'document-pages' }] });
        if (endpoint === '/ontology/knowledge/derive-inputs') return send({ items: project === 'p-test' ?
          [{ analysisId: 'analysis-1', name: '코드 분석', collectionDecisionId: 'code-decision',
            backend: 'agentcore-code-interpreter' }] : [] });
        if (endpoint === '/ontology/knowledge' && method === 'GET') return send({
          items: project === 'p-test' ? [proposal(), proposal('bad'), { operation: 'design.guidelines',
            artifact: { id: 'guide', name: '다른 종류 후보', status: 'completed' } }] : [] });
        if (endpoint === '/ontology/knowledge/derive' && method === 'POST') {
          submissions.push({ kind: 'create', body: route.request().postDataJSON() });
          if (createFirst) { createFirst = false; return send({ error: '접수 재확인' }, 503); }
          return send({ artifact: proposal().artifact }, 202);
        }
        if (endpoint === '/ontology/knowledge/good/publish' && method === 'POST') {
          submissions.push({ kind: 'adopt', body: route.request().postDataJSON() });
          if (adoptFirst) { adoptFirst = false; return send({ error: '채택 재확인' }, 503); }
          return send({ approvalEffect: false }, 201);
        }
        if (endpoint === '/ontology/knowledge/good') return send(proposal());
        if (endpoint === '/ontology/knowledge/bad') return send(proposal('bad'));
        assert.fail(`Unexpected request: ${method} ${endpoint}`);
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('https://derivation.test/');
      const create = page.getByRole('button', { name: '반복 구성 후보 생성', exact: true });
      const adopt = page.getByRole('button', { name: '구성 근거 검토 후 정본 초안 연결', exact: true });
      assert(await create.isDisabled());
      assert.equal(await page.getByText('다른 종류 후보 · 완료', { exact: true }).count(), 0);
      await page.getByLabel('실행 근거가 보존된 코드 분석').selectOption('analysis-1');
      await page.getByRole('checkbox', { name: 'first', exact: true }).check();
      await page.getByRole('checkbox', { name: 'second', exact: true }).check();
      await page.getByRole('checkbox', { name: '화면 원문', exact: true }).check();
      await page.getByLabel('반복 구성 설명 모델').selectOption('global.anthropic.claude-sonnet-5');
      await create.click(); await page.getByText('접수 재확인', { exact: true }).waitFor();
      await create.click(); await page.getByText('정적 파일 3개 · 독립 승인 화면 2개', { exact: true }).waitFor();
      assert.deepEqual(submissions[0].body, submissions[1].body);
      assert.deepEqual(submissions[1].body.decisionIds, ['code-decision', 'doc']);
      assert.deepEqual(submissions[1].body.nodeIds, ['first', 'second']);
      assert.equal(submissions[1].body.analysisId, 'analysis-1');
      await adopt.click(); await page.getByText('채택 재확인', { exact: true }).waitFor();
      await adopt.click(); await page.waitForFunction(() => window.adoptions === 1);
      assert.deepEqual(submissions[2].body, submissions[3].body);
      assert.equal(submissions[3].body.resultHash, resultHash);
      await page.getByRole('button', { name: '근거 부족 · 완료', exact: true }).click();
      await page.getByText('정적 파일 3개 · 독립 승인 화면 1개', { exact: true }).waitFor();
      assert(await adopt.isDisabled());
      assert.equal(await page.getByRole('link', { name: '해시가 일치하는 등록 GUI 원본 보기' }).count(), 0);
      await page.getByText('해시가 일치하는 등록 원본이 연결되지 않았습니다.', { exact: false }).waitFor();
      await page.getByRole('button', { name: 'lower-asset · 연결 자산 열기', exact: true }).click();
      selections.push(...await page.evaluate(() => window.selections));
      assert.deepEqual(selections, ['lower-asset']);
      await page.getByRole('button', { name: 'GUI 원본 연결 보기', exact: true }).click();
      assert.equal(await page.getByRole('link', { name: '해시가 일치하는 등록 GUI 원본 보기' }).getAttribute('href'),
        '#/studio?projectId=p-test&step=assets&assetId=image-source');
      await page.getByRole('button', { name: '반복 안내 · 완료', exact: true }).click();
      await page.getByText('정적 파일 3개 · 독립 승인 화면 2개', { exact: true }).waitFor();
      await page.getByRole('button', { name: '개발자 역할 보기', exact: true }).click();
      assert(await create.isDisabled() && await adopt.isDisabled());
      await page.getByRole('button', { name: '다른 프로젝트 보기', exact: true }).click();
      await page.getByText('이 페이지에 현재 반입 승인과 실행 근거가 확인되는 코드 분석이 없습니다.', { exact: true }).waitFor();
      assert.equal(await page.getByText('안내 구성', { exact: true }).count(), 0);
      assert.equal(await page.getByRole('checkbox', { name: 'first', exact: true }).isChecked(), false);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
