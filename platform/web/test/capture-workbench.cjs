#!/usr/bin/env node
/**
 * Manual integration QA and source screenshots, using the actual WorkspaceAPI.
 *
 * 1. Build platform/web with `npm ci && npm run build`.
 * 2. Run `python3 platform/tests/workbench_preview.py` from the repository root.
 * 3. Run `node platform/web/test/capture-workbench.cjs` from the repository root.
 *    Or use `node platform/web/test/capture-workbench.cjs --start-server` to
 *    keep an owned copy of the supplied loopback helper alive for this run.
 *    An isolated run can use:
 *    `PYTHON=/tmp/mydata-venv/bin/python node platform/web/test/capture-workbench.cjs --start-server --port 18766`
 *    --start-server requires an unused port and terminates only its own child.
 *
 * Browser plugin not available: uses the installed Playwright and optionally
 * WORKSPACE_CHROMIUM. Only Cognito InitiateAuth is fulfilled by this script.
 * No studio-api interception, fake API response, localStorage auth, DOM content
 * replacement, AWS call, or external model request is used.
 *
 * Each run creates a fresh private synthetic project. It never resets data.
 * Only the named screenshot/manifest outputs are replaced on subsequent runs.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { spawn } = require('node:child_process');
const os = require('node:os');
const { chromium } = require('playwright');

const repo = path.resolve(__dirname, '../../..');
let startServer = false, requestedPort;
for (let index = 2; index < process.argv.length; index++) {
  const argument = process.argv[index];
  if (argument === '--start-server') startServer = true;
  else if (argument === '--port') {
    const value = process.argv[++index];
    assert(/^\d+$/.test(value || '') && Number(value) >= 1 && Number(value) <= 65535, '--port must be between 1 and 65535');
    requestedPort = Number(value);
  } else throw new Error(`Unknown capture option: ${argument}`);
}
const base = new URL(process.env.WORKBENCH_PREVIEW_URL || 'http://127.0.0.1:8766');
if (requestedPort !== undefined) base.port = String(requestedPort);
assert.equal(base.hostname, '127.0.0.1', 'Capture only the explicitly synthetic loopback preview.');
assert.equal(base.protocol, 'http:');
assert.equal(base.username + base.password + base.search + base.hash, '');
const origin = base.origin;
const destination = path.resolve(process.env.WORKBENCH_CAPTURE_DIR || path.join(repo, 'videos/ontology-workbench/assets/screens'));
const token = 'synthetic-workbench-rehearsal';
const viewport = { width: 1440, height: 960 };
const evidence = {
  schemaVersion: 1, createdAt: new Date().toISOString(), origin,
  data: 'Synthetic project data; actual WorkspaceAPI and Worker; FakeS3/FakeDDB in the loopback-only test helper.',
  browser: { tool: 'Playwright', reason: 'Browser plugin not available', viewport, deviceScaleFactor: 2 },
  authentication: 'Only Cognito InitiateAuth is intercepted with the test-helper synthetic identity.',
  assertions: [], endpoints: [], endpointFailures: [], browserErrors: [], consoleErrors: [], screenshots: [], artifacts: {},
};
const safe = value => String(value).replaceAll(token, '[synthetic-token]').replaceAll('synthetic-id', '[synthetic-id]')
  .replace(/([?&](?:token|access_token|id_token|authorization)=)[^&\s"']+/gi, '$1[redacted]').slice(0, 1000);
const check = (name, condition, detail = {}) => {
  assert(condition, name);
  evidence.assertions.push({ name, passed: true, ...detail });
};
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function main() {
  await fs.mkdir(destination, { recursive: true });
  const staging = await fs.mkdtemp(path.join(os.tmpdir(), 'workbench-capture-'));
  let server, browser, context;
  try {
  if (startServer) {
    async function available() {
      try {
        const response = await fetch(origin + '/config.json', { signal: AbortSignal.timeout(1000) });
        const config = await response.json();
        assert.equal(config.cognitoClientId, 'synthetic-client', 'Expected the supplied synthetic helper.');
        return true;
      } catch (error) {
        if (error.code === 'ERR_ASSERTION') throw error;
        return false;
      }
    }
    assert.equal(await available(), false, 'The selected port is already serving a helper. Choose an unused --port, or omit --start-server to use it.');
      const python = process.env.PYTHON || 'python3';
      server = spawn(python, [path.join(repo, 'platform/tests/workbench_preview.py'), '--port', base.port || '80'], {
        cwd: repo, stdio: ['ignore', 'pipe', 'pipe'],
      });
      process.once('exit', () => { if (server?.exitCode === null) server.kill('SIGTERM'); });
      let startError = '';
      server.on('error', error => { startError = safe(error.message); });
      // Do not echo request traces or arbitrary subprocess data into evidence.
      server.stdout.on('data', () => {});
      server.stderr.on('data', () => {});
      let ready = false;
      for (let attempt = 0; attempt < 40; attempt++) {
        if (server.exitCode !== null || startError) throw new Error(startError || 'The synthetic helper exited before readiness.');
        if (await available()) { ready = true; break; }
        await new Promise(resolve => setTimeout(resolve, 250));
      }
      if (!ready) { server.kill('SIGTERM'); throw new Error('Synthetic helper startup timed out.'); }
      console.log('SERVER_READY owned loopback helper');
    evidence.server = { managedForThisRun: true, helper: 'platform/tests/workbench_preview.py', python, port: Number(base.port || 80) };
  }
  browser = await chromium.launch({
    executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking'],
  });
  context = await browser.newContext({ viewport, deviceScaleFactor: 2, locale: 'ko-KR', reducedMotion: 'reduce' });
  let authCalls = 0;
  await context.route('https://cognito-idp.ap-northeast-2.amazonaws.com/', async route => {
    const request = route.request();
    assert.equal(request.headers()['x-amz-target'], 'AWSCognitoIdentityProviderService.InitiateAuth', 'Only InitiateAuth may be intercepted.');
    assert.equal(request.method(), 'POST');
    authCalls++;
    await route.fulfill({
      status: 200, contentType: 'application/x-amz-json-1.1; charset=utf-8',
      headers: { 'access-control-allow-origin': origin },
      body: JSON.stringify({ AuthenticationResult: { AccessToken: token, IdToken: 'synthetic-id' } }),
    });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  let stage = 'login';
  page.on('pageerror', error => evidence.browserErrors.push({ stage, message: safe(error.message) }));
  page.on('console', message => {
    if (message.type() === 'error') evidence.consoleErrors.push({ stage, message: safe(message.text()) });
  });
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.origin !== origin || !url.pathname.startsWith('/studio-api/')) return;
    const event = { method: response.request().method(), path: url.pathname, status: response.status() };
    evidence.endpoints.push(event);
    if (response.status() >= 400) {
      evidence.endpointFailures.push({ ...event, stage });
      console.error(`API_FAILURE ${event.method} ${event.path} HTTP ${event.status} (stage ${stage})`);
    }
  });
  async function idle() {
    await page.waitForFunction(() => !document.querySelector('.wb-loading') &&
      ![...document.querySelectorAll('.workbench [role="status"]')].some(el => /서버에서 처리|백그라운드 작업/.test(el.textContent)));
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check(`${stage}: rendered without framework error`, await page.locator('vite-error-overlay').count() === 0 &&
      !await page.getByText('이 화면을 그리는 중 오류가 났습니다', { exact: true }).count());
  }
  async function read(apiPath, projectId) {
    const response = await page.request.get(origin + '/studio-api' + apiPath, {
      headers: { Authorization: `Bearer ${token}`, ...(projectId ? { 'X-Workspace-Project': projectId } : {}) },
    });
    const event = { method: 'GET', path: '/studio-api' + apiPath.split('?')[0], status: response.status(), readback: true };
    evidence.endpoints.push(event);
    if (!response.ok()) {
      evidence.endpointFailures.push({ ...event, stage });
      throw new Error(`Readback failed: GET ${event.path} HTTP ${event.status}`);
    }
    return response.json();
  }
  async function mutate(apiPath, action, method = 'POST') {
    const pending = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.origin === origin && url.pathname === '/studio-api' + apiPath && response.request().method() === method;
    });
    await action();
    const response = await pending;
    if (!response.ok()) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(`API rejected ${method} /studio-api${apiPath}: HTTP ${response.status()} code=${safe(payload.code || 'unknown')}`);
    }
    return response.json();
  }
  async function nav(view) {
    stage = view;
    await page.getByRole('navigation', { name: '업무 메뉴' }).getByRole('button', { name: {
      planning: '상품기획서', changes: '변경 요청 · 영향 분석', components: 'React 컴포넌트',
      knowledge: '규정집 · 위키 검색', skills: 'Skill 제작실', pension: '연금 상담', reports: '보고서 작업실',
      sources: '지식 원본 등록', batches: '수집 · ETL 배치',
    }[view], exact: true }).click();
    await page.waitForURL(url => url.hash.startsWith(`#/wb-${view}`));
    await page.locator('.wb-context-bar').waitFor();
    await idle();
  }
  async function focus(locator, offset = 85) {
    await locator.waitFor();
    await locator.evaluate((element, topGap) => {
      const main = document.querySelector('.app-main');
      main.scrollTo({ top: main.scrollTop + element.getBoundingClientRect().top - main.getBoundingClientRect().top - topGap, behavior: 'instant' });
    }, offset);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  async function capture(name, locator, offset = 85, fullViewport = false) {
    await idle();
    if (locator) await focus(locator, offset);
    const main = page.locator('.app-main');
    const bounds = fullViewport ? { ...page.viewportSize() } : await main.boundingBox();
    check(`${name}: meaningful rendered workbench`, (await page.locator('.workbench').innerText()).length > 300);
    const screenshotOptions = { path: path.join(staging, `${name}.png`), animations: 'disabled' };
    const bytes = fullViewport ? await page.screenshot({ ...screenshotOptions, fullPage: false }) : await main.screenshot(screenshotOptions);
    evidence.screenshots.push({ file: `${name}.png`, sha256: digest(bytes), cssWidth: bounds.width, cssHeight: bounds.height,
      pixelWidth: Math.round(bounds.width * 2), pixelHeight: Math.round(bounds.height * 2), route: new URL(page.url()).hash.split('?')[0], stage });
    console.log(`CAPTURE ${name}.png (${Math.round(bounds.width * 2)}×${Math.round(bounds.height * 2)})`);
  }
  async function assertMobile(name) {
    await page.setViewportSize({ width: 390, height: 844 });
    await idle();
    const widths = await page.evaluate(() => {
      const main = document.querySelector('.app-main'), workbench = document.querySelector('.workbench');
      return { viewport: innerWidth, document: document.documentElement.scrollWidth,
        mainClient: main.clientWidth, mainScroll: main.scrollWidth, workbenchClient: workbench.clientWidth, workbenchScroll: workbench.scrollWidth };
    });
    check(`${name}: mobile 390px has no horizontal page overflow`, widths.document <= widths.viewport &&
      widths.mainScroll <= widths.mainClient && widths.workbenchScroll <= widths.workbenchClient, { widths });
    await capture(`${name}-mobile`, page.locator('.wb-heading'));
    await page.setViewportSize(viewport);
  }
  try {
    await page.goto(origin + '/#/wb-planning', { waitUntil: 'domcontentloaded' });
    check('page identity', new URL(page.url()).origin === origin && (await page.title()).length > 0);
    await page.getByPlaceholder('이메일', { exact: true }).fill('rehearsal@example.invalid');
    await page.getByPlaceholder('비밀번호', { exact: true }).fill('local-synthetic-only');
    await page.getByRole('button', { name: '로그인', exact: true }).click();
    await page.getByLabel('프로젝트', { exact: true }).waitFor();
    check('only synthetic Cognito login is intercepted', authCalls >= 1 && authCalls <= 2);

    stage = 'planning';
    await page.getByText('새 프로젝트', { exact: true }).click();
    await page.getByLabel('프로젝트 이름', { exact: true }).fill('가상 모아저축 · 업무 연결 검토');
    const { project } = await mutate('/projects', () => page.getByRole('button', { name: '프로젝트 만들기', exact: true }).click());
    const projectId = project.id;
    evidence.artifacts.projectId = projectId;
    await page.waitForURL(url => new URLSearchParams(url.hash.split('?')[1]).get('projectId') === projectId);
    await page.locator('.wb-context-bar').waitFor();
    // Close the actual completed creation control before recording the sidebar.
    if (await page.locator('.wb-create-project[open]').count()) await page.locator('.wb-create-project > summary').click();
    const example = await mutate('/workbench/examples', () => page.getByRole('button', { name: '가상 예제 준비', exact: true }).click());
    await idle();
    await page.getByLabel('상품 이름', { exact: true }).waitFor();
    const product = (await read(`/products/${encodeURIComponent(example.productId)}`, projectId)).product;
    check('synthetic example persisted a populated product', product.conditions.length > 0 && product.notices.length > 0 &&
      await page.getByLabel('상품 이름', { exact: true }).inputValue() === product.title);
    evidence.artifacts.productId = product.id;
    await page.getByLabel('이 초안을 새 기준으로 게시합니다', { exact: true }).check();
    const published = await mutate(`/products/${product.id}/publish`, () => page.getByRole('button', { name: '버전 고정·지침 게시', exact: true }).click());
    check('product publication persisted an immutable guideline', !!published.product.publishedGuidelineId);
    await idle();
    await capture('planning', page.locator('.wb-two-column'));
    await page.locator('.app-main').evaluate(element => element.scrollTo({ top: 0, behavior: 'instant' }));
    const menu = page.getByRole('navigation', { name: '업무 메뉴' });
    check('role navigation displays planning, design and development work groups',
      await menu.locator('summary').filter({ hasText: /^기획자$/ }).isVisible() &&
      await menu.locator('summary').filter({ hasText: /^디자이너$/ }).isVisible() &&
      await menu.locator('summary').filter({ hasText: /^개발자$/ }).isVisible());
    await capture('roles', null, 85, true);

    stage = 'changes';
    await page.getByRole('button', { name: '이 상품의 변경 요청', exact: true }).click();
    await page.waitForURL(url => url.hash.startsWith('#/wb-changes'));
    await idle();
    const changeContext = new URLSearchParams(new URL(page.url()).hash.split('?')[1]);
    check('planning shortcut keeps product context without inventing a graph mapping',
      changeContext.get('productId') === product.id && !changeContext.has('targetId') &&
      await page.getByLabel('변경 대상', { exact: true }).inputValue() === '');
    const graph = await read('/workbench/dependencies', projectId);
    const target = graph.nodes.find(node => node.label === 'Product' &&
      (node.id === 'example-product' || node.sourceNodeId === 'example-product' || node.title === '가상 모아저축'));
    check('change target is obtained from the real dependency API', !!target && graph.edges.length > 0);
    await page.getByLabel('변경 제목', { exact: true }).fill('출금 확인 안내와 업무 기준 개정');
    await page.getByLabel('변경 대상', { exact: true }).selectOption({ label: `${target.title || target.id} · ${target.label}` });
    check('human-readable target selection uses the actual graph identity', await page.getByLabel('변경 대상', { exact: true }).inputValue() === target.id);
    await page.getByLabel('변경 유형', { exact: true }).selectOption('policy');
    await page.getByLabel('기준안', { exact: true }).fill('출금 금액을 확인한 뒤 요청합니다.');
    await page.getByLabel('제안안', { exact: true }).fill('출금 금액과 확인 안내를 함께 검토하고, 화면·API의 연결 근거를 확인합니다.');
    await page.getByLabel('변경 이유', { exact: true }).fill('가상 상품의 안내 기준을 정리하고 기획·디자인·개발 담당 작업을 함께 검토합니다.');
    const { change } = await mutate('/workbench/changes', () => page.getByRole('button', { name: '변경 요청 저장', exact: true }).click());
    check('saved change retains distinct product context and chosen graph target', change.productId === product.id && change.targetId === target.id);
    await page.getByRole('button', { name: '영향 분석 실행', exact: true }).waitFor();
    const analysis = await mutate(`/workbench/changes/${change.id}/analyze`, () => page.getByRole('button', { name: '영향 분석 실행', exact: true }).click());
    await page.getByRole('heading', { name: '영향 범위와 근거 경로', exact: true }).waitFor();
    check('saved impact has source-bound witness paths and actual work items', analysis.impact.items.length >= 3 &&
      analysis.impact.items.every(item => item.witnessPath.length > 0) && analysis.tasks.length === analysis.impact.items.length);
    check('impact produces planner, designer and developer worklists', ['planner', 'designer', 'developer'].every(role => analysis.tasks.some(task => task.role === role)));
    check('declared flow, guide, screen and API dependencies appear in actual analysis', ['Flow', 'Guideline', 'Screen', 'API'].every(kind =>
      graph.nodes.some(node => node.label === kind && analysis.impact.items.some(item => item.targetId === node.id))));
    const persistedImpact = await read(`/workbench/changes/${change.id}/impact`, projectId);
    check('impact readback retains exact saved analysis hash', persistedImpact.impact.impactHash === analysis.impact.impactHash);
    evidence.artifacts.changeId = change.id;
    evidence.artifacts.impact = { hash: analysis.impact.impactHash, targetId: target.id, taskCount: analysis.tasks.length,
      roles: [...new Set(analysis.tasks.map(task => task.role))], coverage: analysis.impact.coverage };
    await capture('changes-proposal', page.locator('.wb-two-column'), 75);
    await capture('changes', page.getByRole('heading', { name: '영향 범위와 근거 경로', exact: true }).locator('..'), 85);
    await page.getByRole('button', { name: '생성된 작업 확인', exact: true }).click();
    await page.locator('.wb-task').first().waitFor();
    check('development worklist renders saved task identities', await page.locator('.wb-task').count() === analysis.tasks.length);

    await nav('components');
    const { catalog } = await read('/components', projectId);
    check('component catalog is the real pinned platform package', catalog.components.length > 0 && /^[a-f0-9]{64}$/.test(catalog.hash));
    await page.getByLabel('컴포넌트 검색', { exact: true }).fill('Button');
    await page.locator('.wb-component').filter({ hasText: 'Button' }).waitFor();
    evidence.artifacts.componentCatalog = { id: catalog.id, version: catalog.version, hash: catalog.hash };
    await capture('components', page.locator('.wb-section'), 85);

    await nav('knowledge');
    const knowledge = await read('/workbench/knowledge', projectId);
    check('synthetic ETL published knowledge and labelled its actual backend', knowledge.items.length >= 2 && !!knowledge.generation && !!knowledge.backend);
    const document = knowledge.items.find(item => item.sourceDocumentId === 'fictional-withdrawal') || knowledge.items[0];
    await page.getByRole('button', { name: new RegExp(document.title) }).click();
    await page.locator('.wb-document .wb-prose').waitFor();
    const documentReadback = await read(`/workbench/knowledge/${document.id}`, projectId);
    check('knowledge document renders actual stored content and provenance', (await page.locator('.wb-document .wb-prose').innerText()) === documentReadback.document.content &&
      !!documentReadback.evidence.contentHash);
    evidence.artifacts.knowledge = { generation: knowledge.generation, backend: knowledge.backend, documentId: document.id };
    await capture('knowledge', page.locator('.wb-knowledge-layout'), 90);

    await nav('skills');
    const instructions = [
      'Review product changes against the pinned source revision.',
      '1. Read the saved impact and its witness paths.',
      '2. Separate planner, designer, and developer work items.',
      '3. Report confirmed, candidate, and unknown coverage separately.',
      '4. Preserve Korean product copy and deterministic financial values.',
      '5. Never treat missing evaluation evidence as approval.',
    ].join('\n');
    await page.getByLabel('Skill 식별 이름', { exact: true }).fill('product-impact-review');
    await page.getByLabel('Skill 제목', { exact: true }).fill('상품 변경 영향 검토');
    await page.getByLabel('Skill 설명', { exact: true }).fill('고정된 원본 근거로 역할별 영향과 미확인 범위를 정리합니다.');
    await page.getByLabel('Skill 지침 Markdown', { exact: true }).fill(instructions);
    await page.getByText('원본·도구·동작 예시 연결', { exact: true }).click();
    await page.getByLabel('원본 참조 JSON', { exact: true }).fill(JSON.stringify([documentReadback.evidence], null, 2));
    await page.getByLabel('사용 도구 이름', { exact: true }).fill('knowledge.read, impact.read');
    // This local helper deliberately has no model adapter. Empty behavior cases
    // preserve a real manual package check plus an explicit not-run evaluation.
    await page.getByLabel('동작 예시 JSON', { exact: true }).fill('[]');
    const { skill } = await mutate('/workbench/skills', () => page.getByRole('button', { name: 'Skill 저장', exact: true }).click());
    await page.getByRole('button', { name: '저장 버전 검증', exact: true }).waitFor();
    const validation = await mutate(`/workbench/skills/${skill.id}/validate`, () => page.getByRole('button', { name: '저장 버전 검증', exact: true }).click());
    await page.getByText(`저장된 버전 v${validation.skill.version}`, { exact: true }).waitFor();
    check('manual Skill format validation is real and behavior remains honestly not-run', validation.validation.package.status === 'passed' &&
      validation.validation.behavior.status === 'not-run' && validation.skill.status === 'PENDING_APPROVAL');
    const packageReply = await mutate(`/workbench/skills/${skill.id}/package`, () => page.getByRole('button', { name: '저장 패키지 확인', exact: true }).click(), 'GET');
    check('package endpoint returns exact saved Skill instructions and source binding', packageReply.files['SKILL.md'].includes(instructions) &&
      packageReply.contentHash === validation.skill.contentHash);
    evidence.artifacts.skill = { id: skill.id, version: validation.skill.version, contentHash: packageReply.contentHash,
      packageStatus: validation.validation.package.status, behaviorStatus: validation.validation.behavior.status, status: validation.skill.status };
    await capture('skills', page.locator('.wb-review-flow'), 150);

    await nav('pension');
    const pensionCatalog = await read('/workbench/pension/personas', projectId);
    check('feedback choices come from server-owned metadata', typeof pensionCatalog.feedbackComments?.clear === 'string');
    await page.getByRole('button', { name: /준비 상황을 점검하는 직장인/ }).click();
    const sessionCreated = await mutate('/workbench/pension/sessions', () => page.getByRole('button', { name: '이 페르소나로 상담 시작', exact: true }).click());
    const sessionId = sessionCreated.session.id;
    await page.getByLabel('월 납입액 (원)', { exact: true }).fill('700000');
    const calculated = await mutate(`/workbench/pension/sessions/${sessionId}/calculate`, () => page.getByRole('button', { name: '가정 적용·다시 계산', exact: true }).click());
    check('pension calculator persists exact submitted assumptions and deterministic facts',
      calculated.session.facts.assumptions.monthlyContribution === 700000 && Number.isSafeInteger(calculated.session.facts.projectedBalance));
    await page.getByRole('button', { name: /목표 생활비와 비교/ }).click();
    check('insight opens the matching planning topic', await page.getByLabel('상담 주제', { exact: true }).inputValue() === 'planning');
    const answered = await mutate(`/workbench/pension/sessions/${sessionId}/ask`, () => page.getByRole('button', { name: '상담 질문 보내기', exact: true }).click());
    await page.getByText('결정론적 기준 응답', { exact: true }).waitFor();
    check('pension response is persisted and labelled baseline without a model call', answered.answer.mode === 'baseline' &&
      answered.session.answers.some(answer => answer.id === answered.answer.id));
    await page.getByLabel('상담 만족도', { exact: true }).selectOption('4');
    await page.getByLabel('평가 의견', { exact: true }).selectOption('clear');
    const feedbackRequest = page.waitForRequest(request => new URL(request.url()).pathname === `/studio-api/workbench/pension/sessions/${sessionId}/feedback` && request.method() === 'POST');
    const rated = await mutate(`/workbench/pension/sessions/${sessionId}/feedback`, () => page.getByRole('button', { name: '평가 저장', exact: true }).click());
    const sentFeedback = (await feedbackRequest).postDataJSON();
    check('capture feedback sends a structured code without free text', sentFeedback.commentCode === 'clear' && !Object.hasOwn(sentFeedback, 'comment'));
    await page.getByText('평가 의견을 서버에 저장했습니다.', { exact: true }).waitFor();
    const savedSession = (await read(`/workbench/pension/sessions/${sessionId}`, projectId)).session;
    check('employee feedback persists against the actual answer and current session', savedSession.feedback.some(item =>
      item.id === rated.feedback.id && item.answerId === answered.answer.id && item.commentCode === 'clear' &&
      item.commentMode === 'structured' && item.comment === pensionCatalog.feedbackComments.clear));
    evidence.artifacts.pension = { sessionId, version: savedSession.version, factHash: savedSession.factHash,
      calculationVersion: savedSession.facts.calculationVersion, mode: answered.answer.mode, feedbackCount: savedSession.feedback.length };
    await capture('pension', page.getByRole('heading', { name: '연금 현황과 계산 결과', exact: true }).locator('..'), 100);
    await capture('pension-chat', page.locator('.wb-answer'), 110);

    stage = 'reports';
    await page.getByRole('button', { name: '상담 평가 보고서', exact: true }).click();
    await page.getByLabel('보고서 제목', { exact: true }).fill('가상 연금 상담 · 준비 수준과 직원 평가');
    await page.getByLabel('연결할 변경 요청', { exact: true }).selectOption('');
    const generated = await mutate('/workbench/reports', () => page.getByRole('button', { name: '근거 모아 보고서 생성', exact: true }).click());
    const report = generated.report;
    await page.getByText('저장된 보고서 원문', { exact: true }).waitFor();
    check('report contains saved pension evidence without fabricated sources', report.sourceRefs.some(item => item.kind === 'wb_pension' && item.id === sessionId) &&
      report.unresolved.length === 0);
    await page.getByLabel('문서 내용과 연결된 근거를 확인했습니다', { exact: true }).check();
    const approval = await mutate(`/workbench/reports/${report.id}/approve`, () => page.getByRole('button', { name: '이 보고서 버전 승인', exact: true }).click());
    check('report approval pins the exact content hash and version', approval.report.status === 'approved' &&
      approval.report.contentHash === report.contentHash && approval.report.version > report.version);
    const reportDocument = await read(`/workbench/reports/${report.id}/document`, projectId);
    check('approved report readback verifies document bytes', digest(Buffer.from(reportDocument.markdown)) === reportDocument.contentHash &&
      reportDocument.contentHash === approval.report.contentHash && reportDocument.status === 'approved');
    evidence.artifacts.report = { id: report.id, contentHash: report.contentHash, version: approval.report.version, status: approval.report.status };
    await capture('reports', page.locator('.wb-report-document'), 260);
    await assertMobile('reports');
    await nav('sources');
    const sources = await read('/workbench/sources', projectId);
    check('operator sources show the actual synthetic fixture source', sources.items.some(source => source.id === example.sourceId && source.status === 'ready'));
    await page.getByRole('heading', { name: '가상 출금 업무 자료', exact: true }).waitFor();
    await capture('sources', page.locator('.wb-two-column'), 85);
    await nav('batches');
    const batches = await read('/workbench/batches', projectId);
    const completedBatch = batches.items.find(batch => batch.sourceId === example.sourceId && batch.status === 'completed');
    check('operator batch shows actual completed ETL counts', !!completedBatch && completedBatch.counts.documents >= 2 &&
      completedBatch.counts.vectors >= 2 && completedBatch.counts.nodes > 0 && completedBatch.counts.edges > 0);
    evidence.artifacts.batch = { id: completedBatch.id, sourceId: completedBatch.sourceId, status: completedBatch.status,
      generation: completedBatch.generation, counts: completedBatch.counts };
    await capture('batches', page.getByRole('heading', { name: '배치 실행 이력', exact: true }).locator('..'), 100);
    await nav('knowledge');
    await assertMobile('knowledge');
    const overview = await read('/workbench/overview', projectId);
    evidence.artifacts.stats = overview.stats;
    check('overview keeps server-confirmed role and operator capability', overview.role === 'owner' && overview.operator === true);
    check('no API endpoints failed', evidence.endpointFailures.length === 0);
    check('no browser runtime errors', evidence.browserErrors.length === 0);
    check('no console errors', evidence.consoleErrors.length === 0);
    evidence.status = 'passed';
    console.log(`PASS ${evidence.assertions.length} assertions, ${evidence.screenshots.length} screenshots; real API mutations only.`);
  } catch (error) {
    evidence.status = 'failed';
    evidence.failure = { stage, message: safe(error.message) };
    await page.locator('.app-main').screenshot({ path: path.join(destination, 'capture-failure.png') }).catch(() => {});
    console.error(`CAPTURE_FAILED stage=${stage}: ${safe(error.message)}`);
    process.exitCode = 1;
  } finally {
    if (evidence.status === 'passed') {
      for (const image of evidence.screenshots) {
        const pending = path.join(destination, `.pending-${process.pid}-${image.file}`);
        await fs.copyFile(path.join(staging, image.file), pending);
        await fs.rename(pending, path.join(destination, image.file));
      }
      const pending = path.join(destination, `.pending-${process.pid}-evidence.json`);
      await fs.writeFile(pending, JSON.stringify(evidence, null, 2) + '\n');
      await fs.rename(pending, path.join(destination, 'capture-evidence.json'));
      await Promise.all(['failure.png', 'capture-failure.png', 'capture-failure.json'].map(file =>
        fs.rm(path.join(destination, file), { force: true })));
    } else {
      await fs.writeFile(path.join(destination, 'capture-failure.json'), JSON.stringify(evidence, null, 2) + '\n');
    }
  }
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    if (server && server.exitCode === null && server.signalCode === null) {
      const stopped = new Promise(resolve => { server.once('exit', resolve); server.once('error', resolve); });
      server.kill('SIGTERM');
      const deadline = setTimeout(() => {
        if (server.exitCode === null && server.signalCode === null) server.kill('SIGKILL');
      }, 2000);
      try { await stopped; } finally { clearTimeout(deadline); }
    }
    await fs.rm(staging, { recursive: true, force: true });
  }
}
if (require.main === module) main().catch(error => { console.error(safe(error.message)); process.exitCode = 1; });
