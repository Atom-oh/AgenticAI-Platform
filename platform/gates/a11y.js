'use strict';
// KWCAG 접근성 게이트 — 생성 코드를 스텁 런타임으로 정적 렌더링 → jsdom 문서 → axe-core(wcag2a·wcag2aa) + KWCAG 구조 검사.
// 정직한 범위: 실제 컴포넌트 구현·브라우저 레이아웃이 없으므로 명도 대비 같은 레이아웃 의존 규칙은 '미판정(incomplete)'으로 남는다.
const vm = require('vm');
const React = require('react');
const ReactDOMServer = require('react-dom/server');
const { JSDOM, VirtualConsole } = require('jsdom');
const axeSource = require('axe-core').source;
const { stubModule } = require('./ui-stub');
const { kwcagFor, NOTE: KWCAG_NOTE } = require('./kwcag-map');

const NOTE = '스텁 런타임(시맨틱 HTML 근사) 정적 렌더링 → jsdom → axe-core wcag2a/wcag2aa + KWCAG 구조 검사. 명도 대비 등 레이아웃 의존 규칙은 미판정(incomplete).';

function shimRequire(req) {
  if (req === 'react') return React;
  if (req === 'react/jsx-runtime') return require('react/jsx-runtime');
  if (req === 'react/jsx-dev-runtime') return require('react/jsx-dev-runtime');
  if (req === '@atom/ui' || req.startsWith('@atom/ui/')) return stubModule(req);
  throw new Error(`허용되지 않은 모듈 require: ${req}`);
}

/** 트랜스파일된 CJS 를 격리 컨텍스트에서 평가해 default export(Screen) 를 얻는다. */
function loadScreen(js) {
  const mod = { exports: {} };
  const sandbox = { module: mod, exports: mod.exports, require: shimRequire, console: { log() {}, warn() {}, error() {}, info() {} } };
  vm.createContext(sandbox);
  const script = new vm.Script(js, { filename: 'Screen.js' });
  script.runInContext(sandbox, { timeout: 3000 });
  const Screen = mod.exports && (mod.exports.default || mod.exports.Screen);
  if (typeof Screen !== 'function') throw new Error('`export default function Screen()` 을 찾지 못했습니다.');
  return Screen;
}

function renderMarkup(Screen) {
  const origErr = console.error; const origWarn = console.warn; // 개발 모드 React 경고(prop 검증 등)는 결과에 섞지 않는다
  console.error = () => {}; console.warn = () => {};
  try {
    return ReactDOMServer.renderToStaticMarkup(React.createElement(Screen));
  } finally { console.error = origErr; console.warn = origWarn; }
}

function wrapDocument(markup) {
  return `<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>화면 생성 접근성 검사</title></head><body><main id="root">${markup}</main></body></html>`;
}

/** axe 가 다루지 않는 KWCAG 구조 항목 (표 caption · th scope · 양수 tabindex). */
function extraChecks(document) {
  const out = [];
  const tables = [...document.querySelectorAll('table')].filter((t) => !t.querySelector('caption'));
  if (tables.length) out.push({ id: 'kwcag-table-caption', impact: 'serious', source: 'kwcag-check',
    help: '데이터 표에 <caption>이 없습니다 (DataTable caption 필수)', kwcag: kwcagFor('kwcag-table-caption'),
    nodes: tables.slice(0, 5).map((t, i) => ({ target: `table[${i}]`, html: t.outerHTML.slice(0, 160) })) });
  const ths = [...document.querySelectorAll('th')].filter((th) => !th.hasAttribute('scope') && !th.hasAttribute('id'));
  if (ths.length) out.push({ id: 'kwcag-th-scope', impact: 'moderate', source: 'kwcag-check',
    help: '<th>에 scope(또는 id)가 없습니다', kwcag: kwcagFor('kwcag-th-scope'),
    nodes: ths.slice(0, 5).map((th, i) => ({ target: `th[${i}]`, html: th.outerHTML.slice(0, 160) })) });
  const tabs = [...document.querySelectorAll('[tabindex]')].filter((el) => parseInt(el.getAttribute('tabindex'), 10) > 0);
  if (tabs.length) out.push({ id: 'kwcag-positive-tabindex', impact: 'serious', source: 'kwcag-check',
    help: '양수 tabindex 는 초점 순서를 어지럽힙니다', kwcag: kwcagFor('kwcag-positive-tabindex'),
    nodes: tabs.slice(0, 5).map((el, i) => ({ target: `[tabindex][${i}]`, html: el.outerHTML.slice(0, 160) })) });
  return out;
}

async function runAxe(html) {
  // jsdom 은 canvas 미구현 오류(axe 색 대비 아이콘 검사)를 콘솔로 뿜는다 — 결과에 영향 없으므로 조용히 삼킨다
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: new VirtualConsole() });
  const { window } = dom;
  try {
    window.eval(axeSource);
    const results = await window.axe.run(window.document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
      resultTypes: ['violations', 'incomplete', 'passes'],
      elementRef: false,
    });
    const extra = extraChecks(window.document);
    return { results, extra };
  } finally {
    window.close();
  }
}

const mapNodes = (nodes) => (nodes || []).slice(0, 5).map((n) => ({
  target: Array.isArray(n.target) ? n.target.join(' ') : String(n.target || ''),
  html: String(n.html || '').slice(0, 160),
  summary: n.failureSummary ? String(n.failureSummary).slice(0, 240) : undefined,
}));

/** 반환 {ok, violations[], incomplete[], passes, markup, note, kwcagNote, ms} — 렌더 실패는 예외로 던진다. */
async function a11y(js) {
  const t0 = Date.now();
  const Screen = loadScreen(js);
  const markup = renderMarkup(Screen);
  const { results, extra } = await runAxe(wrapDocument(markup));
  const violations = results.violations.map((v) => ({
    id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl, source: 'axe', kwcag: kwcagFor(v.id),
    tags: (v.tags || []).filter((t) => /^wcag/.test(t)), nodes: mapNodes(v.nodes),
  })).concat(extra);
  const incomplete = results.incomplete.map((v) => ({ id: v.id, help: v.help, kwcag: kwcagFor(v.id), nodes: (v.nodes || []).length }));
  return {
    ok: violations.length === 0, violations, incomplete, passes: results.passes.length,
    markup, note: NOTE, kwcagNote: KWCAG_NOTE, engine: `axe-core ${results.testEngine ? results.testEngine.version : ''}`.trim(),
    ms: Date.now() - t0,
  };
}

module.exports = { a11y, loadScreen, renderMarkup, wrapDocument, extraChecks, NOTE };
