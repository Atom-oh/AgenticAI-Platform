'use strict';
// @atom/ui 스텁 런타임 — 접근성 게이트(a11y)용 정적 렌더링.
// 실제 디자인 시스템 구현이 아니라 각 컴포넌트가 내보내는 **시맨틱 HTML 골격**의 근사다.
// 결과 화면에는 "스텁 런타임(시맨틱 HTML 근사)"으로 표기된다 — 실제 컴포넌트의 픽셀·동작을 검증하지 않는다.
const React = require('react');
const h = React.createElement;

const alignStyle = (align) => (align ? { textAlign: align } : undefined);

const STUBS = {
  // Button → <button type="button">label</button>
  Button: (p) => h('button', { type: 'button', disabled: !!p.disabled, 'data-kind': p.kind || p.variant || undefined,
    'data-tone': p.tone, 'data-size': p.size }, p.label != null ? p.label : p.children),

  // DataTable → <table><caption>…</caption><thead><th scope=col>…  (caption 이 없으면 렌더하지 않는다 → 구조 검사에서 잡힌다)
  DataTable: (p) => {
    const columns = Array.isArray(p.columns) ? p.columns : [];
    const rows = Array.isArray(p.rows) ? p.rows : [];
    const rowKey = p.rowKey;
    return h('table', { 'data-component': 'DataTable' },
      p.caption ? h('caption', null, p.caption) : null,
      h('thead', null, h('tr', null, ...columns.map((c, i) =>
        h('th', { key: c.key || i, scope: 'col', style: alignStyle(c.align) }, c.header)))),
      h('tbody', null, rows.length
        ? rows.map((r, i) => h('tr', { key: rowKey && r[rowKey] != null ? String(r[rowKey]) : i },
            ...columns.map((c, j) => h('td', { key: c.key || j, style: alignStyle(c.align) },
              r[c.key] == null ? '-' : String(r[c.key])))))
        : h('tr', null, h('td', { colSpan: columns.length || 1 }, p.emptyText || '조회 결과가 없습니다.'))));
  },

  // Badge → <span role="status">label</span>
  Badge: (p) => h('span', { role: 'status', 'data-tone': p.tone }, p.label),

  // Card → <section aria-label=title><h2>title</h2>…</section>
  Card: (p) => h('section', { 'aria-label': p.title, 'data-component': 'Card' },
    h('h2', null, p.title),
    p.subtitle ? h('p', null, p.subtitle) : null,
    p.children,
    p.actions ? h('div', { 'data-slot': 'actions' }, p.actions) : null),

  // FormField → <label htmlFor>label</label> + children (+hint/error)
  FormField: (p) => h('div', { 'data-component': 'FormField' },
    h('label', { htmlFor: p.htmlFor }, p.label, p.required ? ' *' : null),
    p.children,
    p.hint ? h('p', { id: p.htmlFor ? p.htmlFor + '-hint' : undefined }, p.hint) : null,
    p.error ? h('p', { role: 'alert' }, p.error) : null),

  // Select → <select id name>…</select> (레이블은 FormField 의 htmlFor 로 연결된다)
  Select: (p) => h('select', { id: p.id, name: p.name, defaultValue: p.value, disabled: !!p.disabled,
    'aria-label': p.label || p.ariaLabel },
    p.placeholder ? h('option', { value: '' }, p.placeholder) : null,
    ...(Array.isArray(p.options) ? p.options : []).map((o, i) => h('option', { key: o.value != null ? o.value : i, value: o.value }, o.label))),

  // PageHeader → <header><nav aria-label=경로><ol>…</ol></nav><h1>title</h1><p>description</p></header>
  PageHeader: (p) => h('header', null,
    Array.isArray(p.breadcrumbs) && p.breadcrumbs.length
      ? h('nav', { 'aria-label': '경로' }, h('ol', null, ...p.breadcrumbs.map((b, i) => h('li', { key: i }, b)))) : null,
    h('h1', null, p.title),
    p.description ? h('p', null, p.description) : null,
    p.actions ? h('div', { 'data-slot': 'actions' }, p.actions) : null),

  // Alert → <div role="alert"><strong>title</strong><p>message</p></div>
  Alert: (p) => h('div', { role: 'alert', 'data-kind': p.kind },
    p.title ? h('strong', null, p.title) : null,
    h('p', null, p.message),
    p.children),
};

// 알려지지 않은 컴포넌트 → <div data-component="X">children</div>
const generic = (name) => {
  const C = (p) => h('div', { 'data-component': name },
    p.children != null ? p.children : (p.label != null ? p.label : (p.title != null ? p.title : null)));
  C.displayName = name;
  return C;
};

/** '@atom/ui' 또는 '@atom/ui/<module>' require 에 대응하는 모듈 객체 (모든 named export 를 스텁으로 해석). */
function stubModule(request) {
  const cache = {};
  return new Proxy({}, {
    get(_, prop) {
      if (prop === '__esModule') return true;
      if (typeof prop !== 'string') return undefined;
      if (!cache[prop]) cache[prop] = STUBS[prop] || generic(prop);
      return cache[prop];
    },
    has() { return true; },
  });
}

module.exports = { stubModule, STUBS, KNOWN: Object.keys(STUBS) };
