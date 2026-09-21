const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { chromium } = require('playwright');

test('guideline page selection reaches AI proposal, exact citations survive editing, and scope changes clear private state',
  { timeout: 60000 }, async () => {
    const root = path.resolve(__dirname, '..');
    const hash = value => createHash('sha256').update(value).digest('hex');
    const source = { id: 'interaction', name: '합성 화면 동작 가이드.pdf', sha256: hash('source'),
      category: 'interaction', pageCount: 10, extractedPages: 10, nonemptyPages: 9, truncatedPages: 1,
      originalStatus: 'local-only', reviewStatus: 'unreviewed' };
    const pages = Array.from({ length: 10 }, (_, index) => {
      const text = index === 0 ? '' : `합성 가이드 ${index + 1}: 뒤로 이동하면 입력 상태를 유지합니다.`;
      return { sourceId: source.id, sourceName: source.name, sourceSha256: source.sha256, page: index + 1,
        category: source.category, text, textSha256: hash(text), truncated: index === 2 };
    });
    const asset = { id: 'pack', name: '합성 UX 가이드.json', version: 2, importRevision: 1, purpose: 'guide',
      uploadStatus: 'stored', parseStatus: 'complete', size: 100, sha256: hash('pack'), guidelineSources: [source] };
    const project = { id: 'project-b', name: '다른 프로젝트', version: 1,
      members: { actor: { role: 'owner', displayName: '합성 사용자' } } };
    let contract, failure = true;
    const calls = [], errors = [], external = [];
    const bundle = await build({
      stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';
        import Workspace from './src/workspace/Workspace';
        const root=createRoot(document.getElementById('root'));root.render(<Workspace initialStep="guides"/>);
        window.dispose=()=>root.unmount();`, resolveDir: root, loader: 'tsx' },
      bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
      define: { 'process.env.NODE_ENV': '"development"' }, plugins: [{ name: 'auth', setup(builder) {
        builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'offline' }));
        builder.onLoad({ filter: /.*/, namespace: 'offline' }, () => ({ contents: 'export const auth={token:"offline"};' }));
      } }],
    });
    const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
      args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, offline: true });
      await context.routeWebSocket('**/*', socket => socket.close());
      await context.route('**/*', async route => {
        const url = new URL(route.request().url()), method = route.request().method();
        const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
        if (url.hostname !== 'guidelines.invalid') { external.push(url.href); return route.abort(); }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html',
          body: '<html lang="ko"><head><title>합성 UX 가이드 검증</title></head><body><div id="root"></div></body></html>' });
        assert.equal(route.request().headers().authorization, 'Bearer offline');
        const target = url.pathname.replace('/studio-api', '');
        const scope = route.request().headers()['x-workspace-project'] || 'personal';
        const body = method === 'GET' ? null : route.request().postDataJSON();
        calls.push({ target, method, scope, body });
        if (target === '/config') return json({ actorId: 'actor', models: [{ id: 'astra', label: 'Astra' }],
          defaultModel: 'astra', maxFileBytes: 52428800, chunkBytes: 2097152, extensions: ['json', 'pdf', 'md'] });
        if (target === '/projects') return json({ projects: [project] });
        if (target === '/projects/project-b') return json({ project });
        if (target === '/assets') return json({ assets: scope === 'personal' ? [asset] : [] });
        if (target === '/contracts' && method === 'GET') return json({ contracts: scope === 'personal' && contract ? [contract] : [] });
        if (target === '/runs') return json({ runs: [] });
        if (target === '/batches') return json({ batches: [] });
        if (target === '/comments') return json({ comments: [] });
        if (target === '/git-connections') return json({ connections: [] });
        if (target === '/products') return json({ products: [] });
        if (target === '/assets/pack/guidelines') {
          assert.equal(scope, 'personal');
          const filtered = pages.filter(page => page.text.includes(url.searchParams.get('q') || ''));
          const start = Number(url.searchParams.get('cursor') || 0);
          return json({ sources: [source], pages: filtered.slice(start, start + 8), total: filtered.length,
            cursor: start + 8 < filtered.length ? String(start + 8) : null });
        }
        if (target === '/contracts/propose') {
          if (failure) { failure = false; return json({ error: '합성 재시도 안내', code: 'unavailable' }, 503); }
          const ref = body.guideRefs[0];
          contract = { id: 'contract', version: 1, status: 'draft', title: '상태 보존 확인', brief: body.brief,
            assetIds: body.assetIds, guideRefs: body.guideRefs, requiredStates: body.requiredStates, viewport: { width: 390, height: 844 }, unresolved: [],
            rules: [{ id: 'R1', title: '입력 상태 보존', required: true, scenario: 'back',
              source: { kind: 'explicit', assetId: ref.assetId, sourceId: ref.sourceId, page: ref.page, quote: pages[ref.page - 1].text },
              steps: [{ action: 'expectText', target: 'summary', targetLabel: '확인 내용', value: '합성 입력값', match: 'contains' }] }] };
          return json({ job: { id: 'proposal', task: 'propose', status: 'queued' } }, 202);
        }
        if (target === '/jobs/proposal') return json({ job: { id: 'proposal', task: 'propose', status: 'completed', result: { contractId: 'contract' } } });
        if (target === '/contracts/contract' && method === 'GET') return json({ contract });
        if (target === '/contracts/contract' && method === 'PUT') {
          contract = { ...contract, ...body, version: contract.version + 1, status: 'draft' };
          return json({ contract });
        }
        if (target === '/contracts/contract/approve') {
          assert(contract.requiredStates.every(state => contract.rules.some(rule => rule.required && rule.scenario === state)));
          contract = { ...contract, version: contract.version + 1, status: 'approved', hash: hash('approved-states') };
          return json({ contract });
        }
        throw new Error(`Unexpected route ${method} ${target}`);
      });
      const page = await context.newPage();
      page.setDefaultTimeout(8000);
      page.on('pageerror', error => errors.push(String(error)));
      page.on('dialog', dialog => dialog.accept());
      await page.goto('https://guidelines.invalid/');
      await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'src/workspace/workspace.css'), 'utf8') });
      await page.addScriptTag({ content: bundle.outputFiles[0].text });
      const capture = async name => {
        if (!process.env.WORKSPACE_QA_DIR) return;
        fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, name + '.png'), fullPage: true });
      };
      await page.getByRole('heading', { name: '고객 가이드 · AI UX 기준', exact: true }).waitFor();
      await page.getByRole('button', { name: '1 업무 정의', exact: true }).click();
      await page.getByLabel('사용자 목적·완료 조건').fill('입력 후 이전 화면으로 돌아와도 값을 유지하세요.');
      await page.getByLabel('오류·수정 상태 포함').check();
      await page.getByLabel('이전·재진입 상태 포함').check();
      await capture('workflow-definition');
      await page.getByRole('button', { name: '기준·자산 선택으로', exact: true }).click();
      await page.goBack();
      await page.getByLabel('사용자 목적·완료 조건').waitFor();
      assert.equal(await page.getByLabel('사용자 목적·완료 조건').inputValue(), '입력 후 이전 화면으로 돌아와도 값을 유지하세요.');
      await page.goForward();
      await page.getByRole('heading', { name: '고객 가이드 · AI UX 기준', exact: true }).waitFor();
      const library = page.locator('.ws-guide-library');
      await library.getByLabel(`${source.name} 2페이지 적용`).waitFor();
      assert.equal(await library.getByLabel(`${source.name} 1페이지 적용`).isDisabled(), true);
      assert.equal(await library.getByLabel(`${source.name} 3페이지 적용`).isDisabled(), true);
      await library.getByLabel(`${source.name} 2페이지 적용`).check();
      await library.getByRole('button', { name: '다음 페이지', exact: true }).click();
      await library.getByLabel(`${source.name} 10페이지 적용`).check();
      await library.getByLabel('원문 검색', { exact: true }).fill('입력 상태');
      await library.getByRole('button', { name: '검색', exact: true }).click();
      await library.getByLabel(`${source.name} 2페이지 적용`).waitFor();
      assert.equal(await library.getByLabel(`${source.name} 2페이지 적용`).isChecked(), true);
      for (const width of [390, 900, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(await library.evaluate(element => element.scrollWidth > element.clientWidth), false);
      }
      if (process.env.WORKSPACE_QA_DIR) {
        fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, 'guideline-library-synthetic.png'), fullPage: true });
      }
      await library.getByRole('button', { name: '선택한 2페이지로 규칙 만들기', exact: true }).click();
      await page.getByLabel('만들 화면 설명', { exact: true }).fill('입력 후 이전 화면으로 돌아와도 값을 유지하세요.');
      const propose = page.getByRole('button', { name: '선택한 파일로 규칙 제안받기', exact: true });
      await propose.click();
      await page.getByText('합성 재시도 안내', { exact: true }).waitFor();
      await propose.click();
      await page.getByText('AI 제안은 아직 승인되지 않았습니다.', { exact: false }).waitFor();
      await page.getByText('오류·수정 상태에 연결된 필수 검증 규칙을 작성하세요.', { exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '버전 1 규칙 승인', exact: true }).isDisabled(), true);
      await capture('workflow-state-gap');
      const requests = calls.filter(call => call.target === '/contracts/propose');
      assert.equal(requests.length, 2);
      assert.equal(requests[0].body.requestId, requests[1].body.requestId);
      assert.deepEqual(requests[1].body.assetIds, ['pack']);
      assert.deepEqual(requests[1].body.guideRefs.map(ref => ref.page), [2, 10]);
      assert.equal(requests[1].body.guideRefs[0].sourceSha256, source.sha256);
      assert.deepEqual(requests[1].body.requiredStates, ['error', 'back']);
      assert.equal(await page.getByLabel(/^가이드 원본·페이지/).inputValue(), 'interaction:2');
      await page.getByLabel('규칙 1', { exact: true }).fill('뒤로 이동 시 입력 상태 확인');
      await page.getByLabel('상태별 규칙', { exact: true }).selectOption('error');
      await page.getByRole('button', { name: '규칙 추가', exact: true }).click();
      await page.getByRole('button', { name: '규칙 저장', exact: true }).click();
      await page.getByText('규칙을 저장했습니다.', { exact: false }).waitFor();
      assert.deepEqual(contract.guideRefs.map(ref => ref.page), [2, 10]);
      assert.deepEqual(contract.requiredStates, ['error', 'back']);
      await page.getByLabel('이 버전의 근거와 모든 확인 단계를 검토했습니다', { exact: true }).check();
      await page.getByRole('button', { name: '버전 2 규칙 승인', exact: true }).click();
      await page.getByRole('button', { name: '승인 기준으로 시안·검수', exact: true }).click();
      await page.getByLabel('사용할 규칙', { exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '4 시안·검수', exact: true }).getAttribute('aria-current'), 'step');
      await page.getByRole('button', { name: '5 개발 전달', exact: true }).click();
      await page.getByRole('heading', { name: '개발팀에 전달할 승인본', exact: true }).waitFor();
      await capture('workflow-handoff-empty');
      await page.getByRole('button', { name: '1 업무 정의', exact: true }).click();
      assert.equal(await page.getByLabel('사용자 목적·완료 조건').inputValue(), '입력 후 이전 화면으로 돌아와도 값을 유지하세요.');
      assert.equal(await page.getByLabel('오류·수정 상태 포함').isChecked(), true);
      await page.getByLabel('작업 공간', { exact: true }).selectOption('project-b');
      await page.getByRole('button', { name: '2 기준·자산', exact: true }).click();
      await page.getByText('아직 반입한 고객 가이드가 없습니다', { exact: true }).waitFor();
      assert.equal(await page.getByText(source.name, { exact: true }).count(), 0);
      assert.equal(await page.getByRole('button', { name: '선택한 0페이지로 규칙 만들기', exact: true }).isDisabled(), true);
      assert(calls.some(call => call.target === '/assets' && call.scope === 'project-b'));
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      await page.evaluate(() => window.dispose());
    } finally { await browser.close(); }
  });
