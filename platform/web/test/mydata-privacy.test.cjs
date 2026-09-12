const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const catalog = {
  type: 's2_privacy_models.done', processor: 'eks-sllm', defaultModel: 'qwen',
  models: [
    { id: 'qwen', label: 'Qwen3-8B', family: 'Qwen', modelId: 'Qwen/Qwen3-8B', revision: 'test-qwen-revision', available: true },
    { id: 'gemma', label: 'Gemma 4', family: 'Gemma', modelId: '', revision: '', available: false, reason: '엔드포인트와 리비전 미등록' },
    { id: 'deepseek', label: 'DeepSeek', family: 'DeepSeek', modelId: '', revision: '', available: false, reason: '엔드포인트 미등록' },
  ],
};
const receipt = {
  processor: 'eks-sllm', model: 'qwen', modelId: 'Qwen/Qwen3-8B', modelRevision: 'test-qwen-revision',
  method: 'redaction', entityCounts: [{ type: 'ACCOUNT', count: 1 }], total: 1,
  sourceChars: 85, outputChars: 74, ruleResidualCount: 0, independentNer: 'not-configured',
  latencyMs: 143, promptVersion: 'mydata-privacy-test-v1', status: 'pass',
  sanitizedPreview: '계좌 [ACCOUNT]의 잔액 1,250,000원, 금리 3.5%, 기간 12개월을 유지해 주세요.',
  // A UI receipt must never render debug fields, even if a producer mistakenly sends them.
  entities: [{ type: 'ACCOUNT', text: 'DEBUG_ENTITY_ORIGINAL' }], rawModelOutput: 'DEBUG_MODEL_OUTPUT',
};
let browser, bundle, css;

before(async () => {
  const result = await build({
    stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client'; import S2 from './src/S2';
      const root = createRoot(document.getElementById('root')); root.render(<S2/>); window.dispose = () => root.unmount();`,
      loader: 'tsx', resolveDir: root },
    bundle: true, write: false, jsx: 'automatic', format: 'iife', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{ name: 'mock-mydata-socket', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'mock' }));
      builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: `
        export const sock = {
          request(action, payload) {
            window.calls.push({ action, payload });
            return new Promise((resolve, reject) => { window.respondModels = resolve; window.rejectModels = reject; });
          },
          run(action, payload, onEvent) {
            window.calls.push({ action, payload });
            return new Promise((resolve, reject) => {
              window.emit = event => { onEvent(event); if (event.type === 's2.done') resolve(); };
              window.rejectRun = reject;
            });
          }
        };` }));
    } }],
  });
  bundle = result.outputFiles[0].text;
  // Use the real app styles without starting a listener or contacting the backend.
  const { createServer } = await import('vite');
  const server = await createServer({ root, server: { middlewareMode: true, hmr: false },
    optimizeDeps: { noDiscovery: true, include: [] }, appType: 'custom' });
  try { css = (await server.transformRequest('/src/index.css?direct')).code
    + '\n' + fs.readFileSync(path.join(root, 'src/mydata/privacy.css'), 'utf8'); }
  finally { await server.close(); }
  browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true, timeout: 30_000,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
});
after(async () => { await browser?.close(); });

async function openS2(t) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, offline: true });
  const page = await context.newPage();
  page.setDefaultTimeout(5_000);
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) errors.push(message.text()); });
  await context.routeWebSocket('**/*', socket => { external.push('websocket'); socket.close(); });
  await context.route('**/*', route => {
    if (route.request().url() !== 'http://offline.test/s2') { external.push(route.request().url()); return route.abort(); }
    return route.fulfill({ contentType: 'text/html', body:
      '<html lang="ko"><head><title>MyData privacy UI test</title></head><body><main style="padding:16px"><div id="root"></div></main></body></html>' });
  });
  await page.goto('http://offline.test/s2');
  await page.addStyleTag({ content: css });
  await page.evaluate(() => { window.calls = []; });
  await page.addScriptTag({ content: bundle });
  await page.getByRole('button', { name: '상담 실행', exact: true }).waitFor();
  assert.equal(page.url(), 'http://offline.test/s2');
  assert.equal(await page.title(), 'MyData privacy UI test');
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  t.after(async () => {
    try {
      assert.deepEqual(errors, [], 'browser console and runtime are clean');
      assert.deepEqual(external, [], 'UI stays offline with only the lib module mocked');
    } finally { await context.close(); }
  });
  return page;
}

async function respond(page, response = catalog) {
  await page.waitForFunction(() => typeof window.respondModels === 'function');
  await page.evaluate(value => window.respondModels(value), response);
}

async function ready(page, response = catalog) {
  await respond(page, response);
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === '상담 실행')?.disabled);
}

async function emit(page, event) {
  await page.evaluate(value => window.emit(value), event);
}

async function screenshot(page, name) {
  if (!process.env.WORKSPACE_QA_DIR) return;
  fs.mkdirSync(process.env.WORKSPACE_QA_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.WORKSPACE_QA_DIR, `mydata-${name}.png`), fullPage: true });
}

test('loading, unconfigured models and retry never permit an unprotected run', async t => {
  const page = await openS2(t);
  const run = page.getByRole('button', { name: '상담 실행', exact: true });
  assert.equal(await run.isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: '시나리오 S2', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: 'S5 차단 시연', exact: true }).isDisabled(), true);
  await page.getByText('프라이버시 모델 확인 중…', { exact: true }).waitFor();
  await page.getByLabel('상담 질문', { exact: true }).press('Enter');
  assert.deepEqual(await page.evaluate(() => window.calls), [{ action: 's2_privacy_models', payload: {} }]);
  await respond(page, { ...catalog, models: [], available: false, message: 'EKS 프라이버시 게이트웨이 미구성' });
  await page.getByText('EKS 프라이버시 게이트웨이 미구성', { exact: true }).waitFor();
  assert.equal(await run.isDisabled(), true);
  await page.getByRole('button', { name: '모델 상태 새로고침', exact: true }).click();
  await page.waitForFunction(() => window.calls.length === 2);
  await respond(page, { ...catalog, models: catalog.models.map(m => ({ ...m, available: false, reason: '현재 사용할 수 없음' })) });
  await page.getByRole('radio', { name: 'Qwen3-8B', exact: true }).waitFor();
  assert.equal(await page.getByRole('radio', { name: 'Qwen3-8B', exact: true }).isDisabled(), true);
  assert.equal(await run.isDisabled(), true);
  await page.getByRole('button', { name: '모델 상태 새로고침', exact: true }).click();
  await ready(page);
  assert.equal(await page.getByRole('radio', { name: 'Qwen3-8B', exact: true }).isChecked(), true);
  for (const name of ['Gemma 4', 'DeepSeek']) {
    assert.equal(await page.getByRole('radio', { name, exact: true }).isDisabled(), true);
  }
  assert.equal(await page.getByRole('group', { name: '필수 프라이버시 모델' }).getByRole('switch').count(), 0);
  await screenshot(page, 'ready-desktop');
});

test('model-list errors and malformed readiness fail closed and can recover', async t => {
  const page = await openS2(t);
  await page.waitForFunction(() => window.rejectModels);
  await page.evaluate(() => window.rejectModels(new Error('목록 연결 시간 초과')));
  await page.getByRole('alert').filter({ hasText: '목록 연결 시간 초과' }).waitFor();
  assert.equal(await page.getByRole('button', { name: '상담 실행', exact: true }).isDisabled(), true);
  for (const invalid of [
    { type: 'error', message: '지원하지 않는 액션' },
    { ...catalog, models: null },
    { ...catalog, processor: 'rules-only' },
    { ...catalog, models: [{ ...catalog.models[0], revision: '' }] },
    { ...catalog, available: false },
  ]) {
    await page.getByRole('button', { name: '모델 상태 새로고침', exact: true }).click();
    await respond(page, invalid);
    await page.waitForFunction(() => !document.body.textContent.includes('프라이버시 모델 확인 중…'));
    assert.equal(await page.getByRole('button', { name: '상담 실행', exact: true }).isDisabled(), true);
  }
  await page.getByRole('button', { name: '모델 상태 새로고침', exact: true }).click();
  await ready(page);
});

test('synthetic examples edit the query, and selected allowlist model stays separate from explanation route', async t => {
  const page = await openS2(t);
  // A second configured model exists only in this fixture to exercise selection.
  await ready(page, { ...catalog, models: [...catalog.models, {
    ...catalog.models[0], id: 'qwen-eval', label: 'Qwen 평가용 (테스트)', revision: 'test-eval-revision',
  }] });
  const query = page.getByLabel('상담 질문', { exact: true });
  for (const [name, expected] of [
    ['합성 예시: 이름·주소', /김하나.*서울.*가상로/],
    ['합성 예시: 전화·이메일', /010-0000-0000.*mydata@example\.invalid/],
    ['합성 예시: 계좌·금액 보존', /123-456789-01234.*1,250,000원.*3\.5%.*12개월/],
  ]) {
    await page.getByRole('button', { name, exact: true }).click();
    assert.match(await query.inputValue(), expected);
  }
  assert.equal(await page.evaluate(() => window.calls.filter(c => c.action === 's2').length), 0);
  await page.getByRole('radio', { name: 'Qwen 평가용 (테스트)', exact: true }).check();
  await page.getByRole('button', { name: /Gemma 4 · 설명용 Bedrock/ }).click();
  await page.getByRole('button', { name: '상담 실행', exact: true }).click();
  const request = await page.evaluate(() => window.calls.at(-1));
  assert.equal(request.action, 's2');
  assert.deepEqual(request.payload, { query: await query.inputValue(), route: 'gemma', semanticLayer: true, privacyModel: 'qwen-eval' });
  assert.equal(await page.getByRole('radio', { name: 'Qwen3-8B', exact: true }).isDisabled(), true);
  await emit(page, { type: 's2.done', blocked: true, blockedBy: 'privacy', message: '테스트 차단',
    privacy: { status: 'blocked', code: 'TEST_BLOCKED' } });
});

test('streamed input and payload receipts show actual evidence without debug originals', async t => {
  const page = await openS2(t);
  await ready(page);
  await page.getByRole('button', { name: '합성 예시: 계좌·금액 보존', exact: true }).click();
  await page.getByRole('button', { name: '상담 실행', exact: true }).click();
  await emit(page, { type: 's2.stage', step: 'privacy_input', ...receipt });
  const input = page.getByRole('region', { name: '질문 프라이버시 처리' });
  await input.getByText('처리 통과', { exact: true }).waitFor();
  for (const expected of ['eks-sllm', 'Qwen/Qwen3-8B', 'test-qwen-revision', 'ACCOUNT ×1', '143ms', 'mydata-privacy-test-v1', '미구성']) {
    assert.match(await input.innerText(), new RegExp(expected));
  }
  assert.match(await input.innerText(), /잔여 식별자\s*0건/);
  assert.match(await input.innerText(), /85자.*74자/);
  assert.equal(await input.getByText(receipt.sanitizedPreview, { exact: true }).isVisible(), true);
  await emit(page, { type: 's2.stage', step: 'privacy_payload', status: 'pass',
    processor: 'schema-and-independent-scan', method: 'structured-tokenization',
    modelInvoked: false, ruleResidualCount: 0, sourceChars: 470, detectors: ['rules', 'guardrail'] });
  const payload = page.getByRole('region', { name: '설명 자료 최종 개인정보 검사' });
  await payload.getByText('전체 전달 자료 검사 통과', { exact: true }).waitFor();
  assert.match(await payload.innerText(), /규칙 \+ Bedrock Guardrails/);
  await emit(page, { type: 's2.stage', step: 'lookup', values: { customer: '합성 고객', amount: '1,250,000원' } });
  await page.getByText('합성 고객', { exact: true }).waitFor();
  await emit(page, { type: 's2.token', t: '금액은 1,250,000원입니다.' });
  await emit(page, { type: 's2.done', blocked: false, elapsedMs: 300, traceId: 'synthetic-test' });
  await page.getByText('금액은 1,250,000원입니다.', { exact: true }).waitFor();
  const text = await page.locator('body').innerText();
  assert.doesNotMatch(text, /DEBUG_ENTITY_ORIGINAL|DEBUG_MODEL_OUTPUT/);
  assert.match(text, /새로 입력한.*식별자.*제거/);
  assert.match(text, /고객 응답에서만.*복원/);
  await screenshot(page, 'receipt-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await input.isVisible());
  const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
  assert.ok(geometry.scroll <= geometry.width + 1, `mobile overflow: ${JSON.stringify(geometry)}`);
  await screenshot(page, 'receipt-mobile');
});

test('privacy failure is distinct from Guardrails and a retry clears previous receipts', async t => {
  const page = await openS2(t);
  await ready(page);
  await page.getByRole('button', { name: '상담 실행', exact: true }).click();
  await emit(page, { type: 's2.stage', step: 'privacy_input', ...receipt });
  await emit(page, { type: 's2.done', blocked: true, blockedBy: 'privacy', message: '잔여 식별자 검사 실패',
    privacy: { status: 'blocked', code: 'RESIDUAL_PII', processor: 'eks-sllm', rawModelOutput: 'BLOCKED_DEBUG_OUTPUT' } });
  const alert = page.getByRole('alert').filter({ hasText: '프라이버시 처리 차단' });
  await alert.waitFor();
  assert.match(await alert.innerText(), /잔여 식별자 검사 실패.*RESIDUAL_PII/s);
  assert.doesNotMatch(await page.locator('body').innerText(), /Bedrock Guardrails 차단:|BLOCKED_DEBUG_OUTPUT/);
  await screenshot(page, 'blocked');
  await page.getByRole('button', { name: '상담 실행', exact: true }).click();
  assert.equal(await page.getByRole('region', { name: '질문 프라이버시 처리' }).count(), 0);
  assert.equal(await alert.count(), 0);
  await page.evaluate(() => window.rejectRun(new Error('스트림 연결 실패')));
  await page.getByRole('alert').filter({ hasText: '스트림 연결 실패' }).waitFor();
  assert.equal(await page.getByRole('button', { name: '상담 실행', exact: true }).isDisabled(), false);
});
