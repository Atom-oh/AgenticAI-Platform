// Portal routing/integration coverage. React preview/catalog are explicit test
// doubles; the real React renderer's CSS, callbacks and CSP have separate tests.
// Portal, its CSS, DiagramPreview/Mermaid, and ImagePreview are shipped modules.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { compile } = require('@tailwindcss/node');
const { Scanner } = require('@tailwindcss/oxide');
const { chromium } = require('playwright');
const { buildRenderer } = require('../portal-renderer/build.cjs');

const web = path.resolve(__dirname, '..');
const ORIGIN = 'https://portal-integration.invalid';
const CATEGORIES = [
  ['Foundation', '기초 기준'], ['Components', '컴포넌트'], ['Patterns', 'UX 패턴'],
  ['Screens', '화면 · 이동'], ['Procedures', '사용자 흐름'], ['Policies', '업무 규칙'], ['UXWriting', 'UX 문구'],
];
const COMPONENT_NAMES = ['Button', 'Input', 'Checkbox', 'Select', 'RadioGroup', 'Alert', 'Stepper', 'Summary',
  'AssetImage', 'Screen', 'Panel', 'Stack', 'Grid', 'Inline', 'Text'];
const CATALOG = {
  id: 'studio-ui', version: '1.0.0', label: '명시적 테스트 카탈로그', hash: 'b'.repeat(64),
  components: COMPONENT_NAMES.map(name => ({
    name, description: `${name} 카탈로그 라우팅 테스트 항목`,
    props: { children: 'ReactNode', disabled: 'boolean' }, variationAxes: ['declared props'],
  })),
};
const IMAGE = 'data:image/svg+xml;base64,' + Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">' +
  '<rect width="320" height="180" fill="#eaf5ef"/><rect x="24" y="24" width="272" height="88" rx="8" fill="#fff"/>' +
  '<rect x="24" y="130" width="120" height="28" rx="5" fill="#008485"/></svg>',
).toString('base64');
const CARD_ROWS = [
  ['TERM-001', 'UXTerm', 'Foundation', '기초 확인 용어'],
  ['CMP-Button-v2', 'Component', 'Components', 'Button'],
  ['CMP-Input-v3', 'Component', 'Components', 'Input'],
  ['PAT-001', 'Pattern', 'Patterns', '본인 확인 패턴'],
  ['SCR-001', 'Screen', 'Screens', '약관 확인'],
  ['SCR-002', 'Screen', 'Screens', '계좌 확인'],
  ['PRC-000', 'Procedure', 'Procedures', '계좌 개설 절차'],
  ['PRC-001', 'Procedure', 'Procedures', '상담 접수 절차'],
  ['POL-000', 'PolicyRule', 'Policies', '한도 확인 정책'],
  ['TERM-002', 'UXTerm', 'UXWriting', '다음 단계 안내 문구'],
  ['TRM-0004', 'UXTerm', 'UXWriting', '전세대출 만기'],
];
const CARDS = CARD_ROWS.map(([id, label, category, name]) => ({
  id, label, category, name, status: 'APPROVED', version: id === 'CMP-Button-v2' ? 'v2' : '1',
  owner: '테스트 소유자', brief: `${name} 합성 자산`, related: {},
  computedBy: 'graph-traversal-fixture',
  ...(id === 'TRM-0004' ? { status: 'DRAFT', rawStatus: null, version: null, owner: null, termCategory: '여신' } : {}),
}));
const EMPTY = { kind: 'empty', reason: '표시할 구성·참조 관계가 없습니다.',
  note: '저장된 메타데이터를 확인하세요. 근거 없는 다이어그램은 만들지 않습니다.' };
const LEGACY = { kind: 'empty', reason: '실제 React 컴포넌트 코드가 연결되지 않았습니다.',
  note: '기존 @atom/ui 메타데이터는 실제 패키지 구현이 아닙니다. 다른 컴포넌트 패키지로 자동 대체하지 않습니다.' };

function card(id) {
  const result = CARDS.find(item => item.id === id);
  assert.ok(result, `Unknown fixture card: ${id}`);
  return structuredClone(result);
}
function detail(id) {
  const item = card(id);
  const result = {
    type: 'portal_detail', ok: true, ...item, props: { name: item.name },
    versionChain: [{ ...item, current: true }], neighbors: [], impactSupported: false,
    publishable: false, publishTarget: null, mapping: null, registry: null,
    backend: 'local', graphBackend: 'local', elapsedMs: 1, visual: { ...EMPTY },
  };
  if (item.label === 'Component') {
    result.visual = { ...LEGACY };
    result.props.package = '@atom/ui';
  }
  if (id === 'TRM-0004') {
    result.props = { termId: id, term: item.name, category: '여신',
      definition: "전세대출 업무 화면에서 '만기'을(를) 가리키는 표준 표기. 고객 안내 문구·버튼 명칭은 '전세대출 만기'으로 통일한다." };
    result.neighbors = [{ rel: 'USED_IN', direction: 'out', count: 2,
      nodes: [{ id: 'SCR-001', label: 'Screen', name: '약관 확인' }, { id: 'SCR-002', label: 'Screen', name: '계좌 확인' }] }];
  }
  if (id === 'SCR-001') {
    result.props.imageDataUrl = IMAGE;
    result.props.alt = '합성 약관 화면 원본';
  }
  if (id === 'SCR-002') result.props.previewImage = 'https://www.figma.com/file/never-request-this';
  if (item.label === 'Procedure') {
    // Matches portal_visual._procedure: ordered step IDs, exact Screen
    // asset links, and unresolved Step nodes; no invented workflow validation.
    const first = id === 'PRC-000' ? '약관 확인' : '상담 내용 확인';
    result.props.steps = [first, '계좌 확인', '담당자 검토'];
    result.visual = {
      kind: 'diagram', source: 'procedure-steps', title: item.name,
      nodes: [
        id === 'PRC-000' ? { id: 'step-1', label: first, type: 'Screen', assetId: 'SCR-001' }
          : { id: 'step-1', label: first, type: 'Step', missing: true },
        { id: 'step-2', label: '계좌 확인', type: 'Screen', assetId: 'SCR-002' },
        { id: 'step-3', label: '담당자 검토', type: 'Step', missing: true },
      ],
      edges: [
        { from: 'step-1', to: 'step-2', label: '다음 단계', kind: 'sequence' },
        { from: 'step-2', to: 'step-3', label: '다음 단계', kind: 'sequence' },
      ],
      note: 'Procedure.steps에 저장된 순서입니다. 화면은 INCLUDES 대상 중 이름이 정확히 하나와 일치할 때만 연결합니다.',
      truncated: { nodes: 0, edges: 0 },
    };
  }
  return result;
}
function listing(category) {
  const cards = CARDS.filter(item => item.category === category);
  return {
    type: 'portal_list', ok: true, category, cards, count: cards.length,
    categoryCounts: Object.fromEntries(CATEGORIES.map(([id]) => [id, CARDS.filter(item => item.category === id).length])),
    computedBy: 'graph-traversal-fixture', backend: 'local', graphBackend: 'local', elapsedMs: 1,
  };
}
function responseFor(action, payload) {
  if (action === 'portal_list') return listing(payload.category);
  if (action === 'portal_detail') return detail(payload.id);
  if (action === 'portal_sync') return {
    type: action, ok: true, id: payload.id, related: {}, registry: null,
    syncLabel: '그래프 재순회', backend: 'local', elapsedMs: 1, note: '로컬 테스트 응답',
  };
  // Opening the registry disclosure is read-only in this test transport.
  if (action === 'portal_registry_map') return { type: action, ok: true, rows: [], registryBackend: 'memory' };
  throw new Error(`Unexpected socket action: ${action}`);
}

const TRACKED = [
  'src/views/Portal.tsx', 'src/portal/portal.css', 'src/index.css',
  'src/portal/ImagePreview.tsx', 'src/portal/image-source.ts',
  'src/portal/DiagramPreview.tsx', 'src/portal/diagram.ts',
];
async function sourceDigest() {
  const contents = await Promise.all(TRACKED.map(file => fs.readFile(path.join(web, file))));
  return createHash('sha256').update(Buffer.concat(contents)).digest('hex');
}
let prepared;
function prepare() {
  return prepared ||= (async () => {
    const before = await sourceDigest();
    const plugin = {
      name: 'portal-integration-boundaries',
      setup(builder) {
        builder.onResolve({ filter: /^\.\.\/lib$/ }, args => args.importer.endsWith('/views/Portal.tsx')
          ? { path: 'sock', namespace: 'portal-test' } : null);
        builder.onResolve({ filter: /\/portal\/ReactComponentPreview$/ }, () =>
          ({ path: 'react-preview', namespace: 'portal-test' }));
        builder.onResolve({ filter: /\/portal\/reactCatalog$/ }, () =>
          ({ path: 'react-catalog', namespace: 'portal-test' }));
        builder.onLoad({ filter: /.*/, namespace: 'portal-test' }, ({ path: name }) => ({
          loader: 'tsx', resolveDir: web,
          contents: name === 'sock' ? `
export const sock={request:async(action,payload={})=>{
  const response=await fetch('/test-sock/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const value=await response.json(); window.portalCompleted.push({action,payload});
  return value;
}};` : name === 'react-catalog' ? `
export const loadReactCatalog=async()=>{const r=await fetch('/test-react-catalog.json');return r.json();};
export const usageSnippet=name=>'// Explicit Portal routing fixture: '+name;
` : `
import React from 'react';
export function ReactComponentPreview({name,compact=false}) {
  return <div data-portal-react-mock={name} data-compact={String(compact)}
    style={{minHeight:compact?130:240,padding:16,background:'#f3f8f5',overflowWrap:'anywhere'}}>
    <span>React 경계 테스트 대역: {name}</span>
  </div>;
}
export default ReactComponentPreview;`,
        }));
      },
    };
    const [app, renderer, indexCss] = await Promise.all([
      build({
        stdin: { loader: 'tsx', resolveDir: web, contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import Portal from './src/views/Portal';
window.portalCompleted=[];
createRoot(document.getElementById('portal-root')).render(<Portal/>);`,
        },
        bundle: true, write: false, format: 'iife', outdir: '/portal-integration-memory',
        plugins: [plugin], metafile: true, define: { 'process.env.NODE_ENV': '"production"' },
      }),
      buildRenderer({ write: false }),
      fs.readFile(path.join(web, 'src/index.css'), 'utf8'),
    ]);
    // Use the real Tailwind preflight/utilities and current Portal CSS, without
    // running a Vite build or writing build output into the parent's workspace.
    const compiler = await compile(indexCss, { base: path.join(web, 'src'), onDependency() {} });
    const scanner = new Scanner({ sources: [{ base: path.join(web, 'src'), pattern: '**/*.{tsx,ts}', negated: false }] });
    const css = compiler.build(scanner.scan()) + '\n' +
      app.outputFiles.filter(file => file.path.endsWith('.css')).map(file => file.text).join('\n');
    const script = app.outputFiles.find(file => file.path.endsWith('.js')).text;
    assert.equal(await sourceDigest(), before, 'Portal sources changed during test preparation; rerun after the owner finishes editing.');
    assert.ok(!Object.keys(app.metafile.inputs).some(file => /src\/lib\.ts$|portal-react\/runtime/.test(file)),
      'Real socket/auth and React runtime must stay outside this routing test.');
    return { script, css, renderer, sourceHash: before };
  })();
}

function requestKey(action, payload) { return `${action}:${payload.id || payload.category || ''}`; }
async function flushUi(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function mount(t) {
  const assets = await prepare();
  const browser = await chromium.launch({
    headless: true, executablePath: process.env.WORKSPACE_CHROMIUM,
    args: ['--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'],
  });
  const context = await browser.newContext({ offline: true, viewport: { width: 1920, height: 1080 } });
  const pageErrors = [], consoleErrors = [], external = [], unexpected = [], calls = [], holds = new Map();
  await context.addInitScript(() => {
    window.portalUnhandled = [];
    window.addEventListener('unhandledrejection', event => window.portalUnhandled.push(String(event.reason)));
  });
  await context.routeWebSocket('**/*', socket => { external.push(socket.url()); socket.close(); });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) { external.push(url.href); await route.abort(); return; }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><html lang="ko"><head><title>Portal integration fixture</title><link rel="stylesheet" href="/test-app.css"></head>' +
      '<body><main class="app-main"><div class="app-content"><div id="portal-root"></div></div></main><script src="/test-app.js"></script></body></html>' });
    if (url.pathname === '/test-app.js') return route.fulfill({ contentType: 'text/javascript', body: assets.script });
    if (url.pathname === '/test-app.css') return route.fulfill({ contentType: 'text/css', body: assets.css });
    if (url.pathname === '/test-react-catalog.json') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(CATALOG) });
    if (url.pathname === '/portal-renderers/mermaid/index.json') return route.fulfill({
      contentType: 'application/json', body: JSON.stringify(assets.renderer.manifest),
    });
    if (url.pathname === `/portal-renderers/mermaid/${assets.renderer.manifest.file}`) return route.fulfill({
      contentType: 'text/html', body: assets.renderer.html,
    });
    if (url.pathname.startsWith('/test-sock/')) {
      const action = url.pathname.slice('/test-sock/'.length);
      const payload = route.request().postDataJSON();
      calls.push({ action, payload });
      const key = requestKey(action, payload);
      let value;
      try {
        value = responseFor(action, payload);
        const held = holds.get(key);
        if (held) {
          holds.delete(key); held.received();
          value = await held.response;
        }
      } catch (error) {
        unexpected.push(error.message);
        value = { type: 'error', ok: false, error: 'Unexpected fixture request' };
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    }
    unexpected.push(url.pathname);
    await route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' || message.type() === 'warning') consoleErrors.push(message.text());
  });
  t.after(async () => {
    try {
      assert.deepEqual(pageErrors, []);
      assert.deepEqual(consoleErrors, []);
      assert.deepEqual(external, []);
      assert.deepEqual(unexpected, []);
      for (const frame of page.frames()) assert.deepEqual(await frame.evaluate(() => window.portalUnhandled || []), []);
      assert.equal(await page.locator('vite-error-overlay, nextjs-portal').count(), 0);
    } finally { await browser.close(); }
  });
  await page.goto(ORIGIN + '/#/portal');
  await page.getByRole('complementary', { name: 'Button React 컴포넌트 상세' }).waitFor();
  function hold(action, payload) {
    let arrived, release;
    const seen = new Promise(resolve => { arrived = resolve; });
    const response = new Promise(resolve => { release = resolve; });
    holds.set(requestKey(action, payload), { received: arrived, response });
    return {
      seen,
      async release(value = responseFor(action, payload)) {
        const completed = page.waitForResponse(response => {
          if (!response.url().endsWith('/test-sock/' + action)) return false;
          return requestKey(action, response.request().postDataJSON()) === requestKey(action, payload);
        });
        release(value);
        await (await completed).finished();
        await flushUi(page);
      },
    };
  }
  return { page, calls, hold, sourceHash: assets.sourceHash };
}
const navigation = page => page.getByRole('navigation', { name: '디자인 자산 유형' });
const library = page => page.getByRole('region', { name: '자산 목록' });
const assetCard = (page, id) => library(page).locator('.portal-card-grid > button').filter({ hasText: id });
async function category(page, name) {
  await navigation(page).getByRole('button', { name: new RegExp('^' + name) }).click();
}
async function openAsset(page, id) {
  await assetCard(page, id).click();
  const aside = page.getByRole('complementary', { name: `${card(id).name} 설계 자산 상세` });
  await aside.waitFor();
  return aside;
}

test('Korean category navigation and explicit React catalog route to separate details', { timeout: 60_000 }, async t => {
  const { page, calls } = await mount(t);
  assert.equal(await page.title(), 'Portal integration fixture');
  assert.equal(new URL(page.url()).origin, ORIGIN);
  await page.getByRole('heading', { name: '그림으로 확인하고, 직접 사용해 보세요.' }).waitFor();
  assert.equal(await navigation(page).getByRole('button').count(), CATEGORIES.length);
  for (const [, name] of CATEGORIES) assert.equal(await navigation(page).getByRole('button', { name: new RegExp('^' + name) }).count(), 1);
  assert.equal(await library(page).locator('.portal-code-card').count(), 15);
  assert.equal(await library(page).getByRole('button', { name: '실제 React', exact: true }).getAttribute('aria-pressed'), 'true');
  await library(page).getByRole('button', { name: /입력 필드.*Input/ }).click();
  const code = page.getByRole('complementary', { name: 'Input React 컴포넌트 상세' });
  await code.waitFor();
  assert.equal(await code.locator('[data-portal-react-mock="Input"][data-compact="false"]').count(), 1);
  assert.equal(await code.getByText('Input 카탈로그 라우팅 테스트 항목', { exact: true }).count(), 1);
  assert.match(page.url(), /component=Input$/);
  await code.getByRole('button', { name: '컴포넌트 상세 닫기' }).click();
  assert.equal(await page.getByRole('complementary').count(), 0);
  await library(page).getByRole('textbox', { name: '디자인 자산 검색' }).fill('Checkbox');
  assert.equal(await library(page).locator('.portal-code-card').count(), 1);
  for (const [id, name] of CATEGORIES.filter(([id]) => id !== 'Components')) {
    await category(page, name);
    const expected = CARDS.filter(item => item.category === id);
    await assetCard(page, expected[0].id).waitFor();
    assert.equal(await library(page).locator('.portal-card-grid > button').count(), expected.length);
    assert.equal(await navigation(page).getByRole('button', { name: new RegExp('^' + name) }).getAttribute('aria-current'), 'page');
    assert.equal(await page.getByRole('complementary').count(), 0);
  }
  assert.ok(calls.every(call => call.action === 'portal_list'));
});

test('legacy Component metadata stays code-unlinked until explicitly opening the same-name platform example', { timeout: 45_000 }, async t => {
  const { page, calls } = await mount(t);
  await library(page).getByRole('button', { name: /^설계 메타데이터/ }).click();
  const legacy = await openAsset(page, 'CMP-Button-v2');
  await legacy.getByRole('heading', { name: LEGACY.reason }).waitFor();
  await legacy.getByText(LEGACY.note, { exact: true }).waitFor();
  assert.equal(await legacy.locator('[data-portal-react-mock], iframe').count(), 0);
  await legacy.getByText('같은 이름의 플랫폼 예제이며, 이 설계 자산의 v2 구현으로 자동 연결되지 않습니다.', { exact: true }).waitFor();
  assert.match(page.url(), /id=CMP-Button-v2$/);
  await legacy.getByRole('button', { name: '플랫폼 Button 실행 예제 보기', exact: true }).click();
  const code = page.getByRole('complementary', { name: 'Button React 컴포넌트 상세' });
  await code.waitFor();
  assert.equal(await code.locator('[data-portal-react-mock="Button"][data-compact="false"]').count(), 1);
  assert.equal(await page.getByRole('heading', { name: LEGACY.reason }).count(), 0);
  await code.getByRole('button', { name: '같은 이름의 설계 자산 찾기' }).click();
  await assetCard(page, 'CMP-Button-v2').waitFor();
  assert.equal(await library(page).locator('.portal-card-grid > button').count(), 1);
  assert.equal(await page.getByRole('complementary').count(), 0);
  assert.equal(calls.filter(call => call.action === 'portal_detail').length, 1);
});

test('procedure detail renders real Mermaid and links to a Screen with a valid isolated image', { timeout: 60_000 }, async t => {
  const { page, calls } = await mount(t);
  await category(page, '사용자 흐름');
  let aside = await openAsset(page, 'PRC-000');
  await aside.locator('.portal-diagram[data-diagram-state="rendered"]').waitFor();
  const diagram = page.frameLocator('iframe[data-portal-diagram]');
  assert.deepEqual(await diagram.locator('.node .label text').allTextContents(), ['약관 확인', '계좌 확인', '담당자 검토']);
  await aside.getByText('출처와 다이어그램 소스', { exact: true }).click();
  await aside.getByText('procedure-steps', { exact: true }).waitFor();
  const links = aside.locator('[aria-label="다이어그램의 연결 자산"]');
  assert.equal(await links.getByRole('button').count(), 2);
  assert.equal(await links.getByRole('button', { name: '담당자 검토' }).count(), 0);
  await links.getByRole('button', { name: '약관 확인', exact: true }).click();
  aside = page.getByRole('complementary', { name: '약관 확인 설계 자산 상세' });
  await aside.waitFor();
  assert.equal(await page.locator('iframe[data-portal-diagram]').count(), 0);
  assert.equal(await navigation(page).getByRole('button', { name: /^화면 · 이동/ }).getAttribute('aria-current'), 'page');
  const image = page.frameLocator('iframe[title="약관 확인 반입 이미지"]').getByRole('img', { name: '합성 약관 화면 원본' });
  await image.waitFor();
  await image.evaluate(element => element.decode());
  await aside.getByRole('status').filter({ hasText: '이미지를 불러오는 중' }).waitFor({ state: 'hidden' });
  assert.equal(await aside.getByRole('alert').count(), 0);
  assert.equal(await image.evaluate(element => element.naturalWidth), 320);
  assert.equal(await image.evaluate(element => element.naturalHeight), 180);
  assert.equal(await image.getAttribute('src'), IMAGE);
  assert.equal(calls.filter(call => call.action === 'portal_detail').at(-1).payload.id, 'SCR-001');
  await aside.getByRole('button', { name: '설계 자산 상세 닫기' }).click();
  assert.equal(await page.getByRole('complementary').count(), 0);
  assert.equal(await page.locator('iframe').count(), 0);

  const externalImage = await openAsset(page, 'SCR-002');
  assert.equal(await externalImage.locator('iframe').count(), 0);
  await externalImage.getByRole('link', { name: 'Design Studio에서 원본 파일 반입하기' }).waitFor();
  assert.equal(await externalImage.getByRole('link', { name: 'Design Studio에서 원본 파일 반입하기' }).getAttribute('href'), '#/studio');
});

test('late category and detail responses cannot replace a newer selection or reopen closed details', { timeout: 60_000 }, async t => {
  const { page, hold } = await mount(t);
  await category(page, '화면 · 이동');
  await assetCard(page, 'SCR-001').waitFor();
  const slowList = hold('portal_list', { category: 'Procedures' });
  await category(page, '사용자 흐름'); await slowList.seen;
  assert.equal(await library(page).locator('.portal-card-grid > button').count(), 0,
    'The previous category cards must disappear before the new list arrives');
  await category(page, '화면 · 이동');
  let selected = await openAsset(page, 'SCR-002');
  await slowList.release();
  assert.equal(await selected.isVisible(), true);
  assert.equal(await assetCard(page, 'PRC-000').count(), 0);

  await category(page, '사용자 흐름');
  await assetCard(page, 'PRC-000').waitFor();
  const slowDetail = hold('portal_detail', { id: 'PRC-000' });
  await assetCard(page, 'PRC-000').click(); await slowDetail.seen;
  await category(page, '화면 · 이동');
  selected = await openAsset(page, 'SCR-002');
  await slowDetail.release();
  assert.equal(await selected.isVisible(), true);
  assert.match(page.url(), /id=SCR-002$/);
  assert.equal(await page.locator('iframe[data-portal-diagram]').count(), 0);

  // A selected detail is closed while an older request is still in flight.
  await category(page, '사용자 흐름');
  const older = hold('portal_detail', { id: 'PRC-000' });
  await assetCard(page, 'PRC-000').click(); await older.seen;
  const newer = await openAsset(page, 'PRC-001');
  await newer.getByRole('button', { name: '설계 자산 상세 닫기' }).click();
  await older.release();
  assert.equal(await page.getByRole('complementary').count(), 0);
  assert.equal(await page.locator('iframe').count(), 0);
});

test('late detail failures do not overwrite actual-code mode or the current design asset', { timeout: 45_000 }, async t => {
  const { page, hold } = await mount(t);
  await library(page).getByRole('button', { name: /^설계 메타데이터/ }).click();
  const lateFailure = hold('portal_detail', { id: 'CMP-Button-v2' });
  await assetCard(page, 'CMP-Button-v2').click(); await lateFailure.seen;
  await library(page).getByRole('button', { name: '실제 React', exact: true }).click();
  const code = page.getByRole('complementary', { name: 'Button React 컴포넌트 상세' });
  await code.waitFor();
  await lateFailure.release({ type: 'error', ok: false, error: '이전 요청 오류는 표시되면 안 됩니다' });
  assert.equal(await code.isVisible(), true);
  assert.equal(await page.getByRole('alert').count(), 0);
  await library(page).getByRole('button', { name: /^설계 메타데이터/ }).click();
  const lateSuccess = hold('portal_detail', { id: 'CMP-Button-v2' });
  await assetCard(page, 'CMP-Button-v2').click(); await lateSuccess.seen;
  const input = await openAsset(page, 'CMP-Input-v3');
  await lateSuccess.release();
  assert.equal(await input.isVisible(), true);
  assert.match(page.url(), /id=CMP-Input-v3$/);
});

test('Sync refetches the selected visual and ignores a late refresh after another asset is selected', { timeout: 60_000 }, async t => {
  const { page, calls, hold } = await mount(t);
  await category(page, '사용자 흐름');
  const aside = await openAsset(page, 'PRC-000');
  await aside.locator('.portal-diagram[data-diagram-state="rendered"]').waitFor();
  const oldFrame = await aside.locator('iframe[data-portal-diagram]').elementHandle();
  await aside.getByText('설계 속성 · 연결 · 발행 관리', { exact: true }).click();
  const refresh = hold('portal_detail', { id: 'PRC-000' });
  const start = calls.length;
  await aside.getByRole('button', { name: 'Sync (그래프 재순회)', exact: true }).click();
  await refresh.seen;
  assert.deepEqual(calls.slice(start).map(call => [call.action, call.payload.id]), [
    ['portal_sync', 'PRC-000'], ['portal_detail', 'PRC-000'],
  ]);
  const fresh = detail('PRC-000');
  fresh.version = '2';
  fresh.props.steps[0] = '변경된 약관 검토';
  fresh.visual.nodes[0] = { id: 'step-1', label: '변경된 약관 검토', type: 'Step', missing: true };
  await refresh.release(fresh);
  await aside.locator('.portal-diagram[data-diagram-state="rendered"]').waitFor();
  assert.equal(await oldFrame.evaluate(element => element.isConnected), false);
  assert.equal(await page.frameLocator('iframe[data-portal-diagram]').locator('.node .label text').first().textContent(), '변경된 약관 검토');
  await aside.getByText('Version 2', { exact: true }).waitFor();

  const lateRefresh = hold('portal_detail', { id: 'PRC-000' });
  await aside.getByRole('button', { name: 'Sync (그래프 재순회)', exact: true }).click();
  await lateRefresh.seen;
  await category(page, '화면 · 이동');
  const screen = await openAsset(page, 'SCR-002');
  await lateRefresh.release(fresh);
  assert.equal(await screen.isVisible(), true);
  assert.match(page.url(), /id=SCR-002$/);
  assert.equal(await page.locator('iframe[data-portal-diagram]').count(), 0);
  assert.equal(await page.getByRole('alert').count(), 0);
});

test('current Portal CSS fits wide desktop and phone layouts with catalog, diagram and image details', { timeout: 60_000 }, async t => {
  const { page, sourceHash } = await mount(t);
  for (const [width, height] of [[1920, 1080], [2560, 1440], [3440, 1440], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await category(page, '컴포넌트');
    await page.getByRole('complementary', { name: 'Button React 컴포넌트 상세' }).waitFor();
    await assertFits(page, width, 'catalog');
    await category(page, '사용자 흐름');
    const flow = await openAsset(page, 'PRC-000');
    await flow.locator('.portal-diagram[data-diagram-state="rendered"]').waitFor();
    await assertFits(page, width, 'diagram');
    await flow.getByRole('button', { name: '전체 화면으로 흐름 보기' }).click();
    const dialog = page.getByRole('dialog', { name: '계좌 개설 절차' });
    await dialog.waitFor();
    await dialog.locator('.portal-diagram[data-diagram-state="rendered"]').waitFor();
    const bounds = await dialog.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1, `Dialog overflow at ${width}`);
    await dialog.getByRole('button', { name: '전체 화면 닫기' }).click();
    assert.equal(await page.getByRole('dialog').count(), 0);
    await flow.locator('[aria-label="다이어그램의 연결 자산"]').getByRole('button', { name: '약관 확인', exact: true }).click();
    await page.getByRole('complementary', { name: '약관 확인 설계 자산 상세' }).waitFor();
    await page.frameLocator('iframe[title="약관 확인 반입 이미지"]').getByRole('img').evaluate(image => image.decode());
    await assertFits(page, width, 'image');
  }
  assert.equal(await sourceDigest(), sourceHash,
    'Portal source/CSS changed during QA. These results cover the starting snapshot; rerun this file after edits settle.');
});

async function assertFits(page, width, state) {
  await flushUi(page);
  const layout = await page.evaluate(() => {
    const rect = selector => {
      const r = document.querySelector(selector).getBoundingClientRect();
      return { x: r.x, right: r.right, width: r.width };
    };
    return {
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      page: rect('.portal-page'), nav: rect('.portal-nav'), library: rect('.portal-library'), detail: rect('.portal-detail'),
    };
  });
  assert.equal(layout.overflow, false, `${state} page overflow at ${width}`);
  for (const [name, box] of Object.entries(layout).filter(([name]) => name !== 'overflow')) {
    assert.ok(box.width > 0 && box.x >= -1 && box.right <= width + 1, `${state}/${name} outside viewport ${width}: ${JSON.stringify(box)}`);
  }
  if (width >= 1920) {
    assert.ok(layout.nav.right <= layout.library.x + 1, 'Desktop category rail must not overlap the library');
    assert.ok(layout.library.right <= layout.detail.x + 1, 'Desktop library must not overlap detail');
    assert.ok(layout.page.width >= width * 0.9, 'Portal should use the available desktop width');
  }
}

test('selecting lower cards reveals and focuses the active detail at desktop and stacked widths', { timeout: 60_000 }, async t => {
  const { page } = await mount(t);
  for (const width of [1920, 1000, 375]) {
    await page.setViewportSize({ width, height: 900 });
    const button = page.locator('.portal-code-open').filter({ hasText: 'Text' });
    await button.scrollIntoViewIfNeeded();
    await button.click();
    const detail = page.getByRole('complementary', { name: 'Text React 컴포넌트 상세' });
    await detail.waitFor();
    await flushUi(page);
    const state = await detail.evaluate(element => {
      const box = element.getBoundingClientRect();
      return { top: box.top, bottom: box.bottom, viewport: innerHeight,
        focused: element.contains(document.activeElement) || element === document.activeElement };
    });
    assert.ok(state.top >= -1 && state.top < state.viewport && state.bottom > 0,
      `Selected detail must be visible at ${width}: ${JSON.stringify(state)}`);
    assert.equal(state.focused, true);
    await detail.getByRole('button', { name: '컴포넌트 상세 닫기' }).click();
    await flushUi(page);
    assert.equal(await button.evaluate(element => element === document.activeElement), true);
  }
});

test('Escape closes fullscreen after the trusted diagram iframe receives keyboard focus', { timeout: 45_000 }, async t => {
  const { page } = await mount(t);
  await category(page, '사용자 흐름');
  await openAsset(page, 'PRC-000');
  const opener = page.getByRole('button', { name: '전체 화면으로 흐름 보기', exact: true });
  await opener.click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  const viewport = dialog.frameLocator('iframe[data-portal-diagram]').locator('#viewport');
  await viewport.waitFor();
  await viewport.focus();
  await page.keyboard.press('End');
  const fullscreenFrame = await (await dialog.locator('iframe[data-portal-diagram]').elementHandle()).contentFrame();
  assert(fullscreenFrame);
  assert.deepEqual(await fullscreenFrame.evaluate(() => window.portalUnhandled || []), []);
  const detached = page.waitForEvent('framedetached', {
    predicate: frame => frame === fullscreenFrame, timeout: 6000,
  });
  await page.keyboard.press('Escape');
  await detached;
  await page.waitForFunction(() => !document.querySelector('dialog[open]'), undefined, { timeout: 6000 });
  assert.equal(await opener.evaluate(element => element === document.activeElement), true);
});

test('UX terms show their registered explanation and usage instead of unsupported-preview and invented draft states', { timeout: 45_000 }, async t => {
  const { page } = await mount(t);
  await category(page, 'UX 문구');
  await page.getByRole('heading', { name: '용어와 사용 맥락을 확인하세요.' }).waitFor();
  const tile = assetCard(page, 'TRM-0004');
  assert.doesNotMatch(await tile.innerText(), /초안|v —|Owner/);
  const term = await openAsset(page, 'TRM-0004');
  await term.getByRole('heading', { name: '등록된 설명', exact: true }).waitFor();
  assert.equal(await term.getByText(detail('TRM-0004').props.definition, { exact: true }).first().isVisible(), true);
  assert.equal(await term.locator('.portal-unlinked, iframe').count(), 0);
  assert.doesNotMatch(await term.innerText(), /초안|Version —|Owner —|미리보기.*지원하지/);
  const usages = term.getByRole('region', { name: '연결된 사용 화면' });
  assert.equal(await usages.getByRole('button').count(), 2);
  await usages.getByRole('button', { name: /약관 확인/ }).click();
  await page.getByRole('complementary', { name: '약관 확인 설계 자산 상세' }).waitFor();
  await category(page, '기초 기준');
  await page.getByRole('heading', { name: '용어와 사용 맥락을 확인하세요.' }).waitFor();
  await category(page, '컴포넌트');
  await page.getByRole('heading', { name: '그림으로 확인하고, 직접 사용해 보세요.' }).waitFor();
});

test('term details distinguish missing descriptions and incomplete usage samples without fabricating content', { timeout: 45_000 }, async t => {
  const { page, hold } = await mount(t);
  await category(page, 'UX 문구');
  const pending = hold('portal_detail', { id: 'TRM-0004' });
  await assetCard(page, 'TRM-0004').click(); await pending.seen;
  const value = detail('TRM-0004');
  value.props.definition = '';
  value.rawStatus = 'APPROVED'; value.status = 'APPROVED';
  value.neighbors = [{ rel: 'USED_IN', direction: 'out', count: 20, nodes: [
    { id: 'SCR-001', label: 'Screen', name: '약관 확인' },
    { id: 'SCR-001', label: 'Screen', name: '약관 확인' },
    { id: 'CMP-Button-v2', label: 'Component', name: 'Button' },
  ] }, { rel: 'OTHER', direction: 'in', count: 1, nodes: [{ id: 'SCR-002', label: 'Screen', name: '계좌 확인' }] }];
  await pending.release(value);
  const term = page.getByRole('complementary', { name: '전세대출 만기 설계 자산 상세' });
  await term.getByText('등록된 설명이 없습니다.', { exact: true }).waitFor();
  assert.match(await term.innerText(), /승인/);
  const usages = term.getByRole('region', { name: '연결된 사용 화면' });
  assert.equal(await usages.getByRole('button').count(), 1);
  await usages.getByText('일부 연결만 표시됩니다.', { exact: false }).waitFor();
  await term.locator('.portal-technical > summary').click();
  assert.equal(await term.getByRole('button', { name: 'Publish', exact: true }).count(), 0);
  assert.equal(await term.getByText(/Publish 미구현/).count(), 0);
  await term.getByRole('button', { name: '용어 정보 새로고침', exact: true }).click();
  await term.getByText(detail('TRM-0004').props.definition, { exact: true }).first().waitFor();
});

test('term source text remains literal and fits the detail at desktop and stacked widths', { timeout: 45_000 }, async t => {
  const { page, hold } = await mount(t);
  await category(page, 'UX 문구');
  const pending = hold('portal_detail', { id: 'TRM-0004' });
  await assetCard(page, 'TRM-0004').click(); await pending.seen;
  const value = detail('TRM-0004');
  value.props.definition = '<img src=x onerror="window.termScriptRan=true"> 원문\n' + '아주긴등록용어'.repeat(80);
  value.neighbors = [];
  await pending.release(value);
  const term = page.getByRole('complementary', { name: '전세대출 만기 설계 자산 상세' });
  await term.getByText(value.props.definition, { exact: true }).first().waitFor();
  assert.equal(await term.locator('img').count(), 0);
  assert.equal(await page.evaluate(() => window.termScriptRan), undefined);
  await term.getByText('현재 조회 결과에 연결된 사용 화면이 없습니다.', { exact: true }).waitFor();
  for (const width of [1920, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await flushUi(page);
    await assertFits(page, width, 'term');
    const size = await term.evaluate(el => ({ client: el.clientWidth, scroll: el.scrollWidth }));
    assert.ok(size.scroll <= size.client + 1, 'Term text must wrap inside the detail');
  }
});
