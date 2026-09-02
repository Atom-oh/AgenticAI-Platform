'use strict';
// 린트 게이트 — ESLint 9 flat config (설정 파일 없음, 인라인 주석으로 규칙 우회 불가).
// 규칙: 미사용 변수(ts) · import 출처 제한(react / @atom/ui/*) · 네트워크·브라우저 저장소 API 금지(SPEC §12.10) · 색상 리터럴 인라인 style 금지
const path = require('path');
const { ESLint } = require('eslint');
const tsParser = require('@typescript-eslint/parser');
const tsPlugin = require('@typescript-eslint/eslint-plugin');

const ALLOWED_IMPORT = /^(react|react\/jsx-runtime|@atom\/ui(\/[a-z0-9][a-z0-9-]*)?)$/;
const COLOR_PROPS = new Set(['color', 'background', 'backgroundColor', 'borderColor', 'border', 'fill', 'stroke', 'outlineColor', 'boxShadow']);
const COLOR_LITERAL = /^(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|(white|black|red|blue|green|gray|grey|yellow|orange|purple|pink)\b)/;

const allowedImports = {
  meta: { type: 'problem', schema: [], messages: {
    forbidden: "허용되지 않은 import 출처 '{{source}}' — 'react' 와 '@atom/ui/…' 만 사용할 수 있습니다.",
    dynamic: '동적 import() 는 금지입니다.',
    require: 'require() 는 금지입니다.',
  } },
  create(ctx) {
    return {
      ImportDeclaration(node) {
        const s = String(node.source.value);
        if (!ALLOWED_IMPORT.test(s)) ctx.report({ node, messageId: 'forbidden', data: { source: s } });
      },
      ImportExpression(node) { ctx.report({ node, messageId: 'dynamic' }); },
      CallExpression(node) {
        if (node.callee.type === 'Identifier' && node.callee.name === 'require') ctx.report({ node, messageId: 'require' });
      },
    };
  },
};

const noColorLiteralsInStyle = {
  meta: { type: 'problem', schema: [], messages: {
    color: "인라인 style 의 '{{prop}}' 에 색상 리터럴 '{{value}}' — CSS 변수(var(--…))만 허용됩니다 (퍼블리싱 규약 P-7).",
  } },
  create(ctx) {
    return {
      JSXAttribute(node) {
        if (!node.name || node.name.name !== 'style' || !node.value || node.value.type !== 'JSXExpressionContainer') return;
        const obj = node.value.expression;
        if (!obj || obj.type !== 'ObjectExpression') return;
        for (const p of obj.properties) {
          if (p.type !== 'Property' || !p.key) continue;
          const key = p.key.type === 'Identifier' ? p.key.name : String(p.key.value);
          if (!COLOR_PROPS.has(key)) continue;
          const v = p.value;
          if (v.type === 'Literal' && typeof v.value === 'string' && COLOR_LITERAL.test(v.value.trim())) {
            ctx.report({ node: p, messageId: 'color', data: { prop: key, value: v.value } });
          }
        }
      },
    };
  },
};

const RESTRICTED_GLOBALS = [
  ['fetch', '네트워크 호출 금지 (SPEC §12.10) — 화면 코드는 데이터를 가져오지 않는다'],
  ['XMLHttpRequest', '네트워크 호출 금지 (SPEC §12.10)'],
  ['WebSocket', '네트워크 호출 금지 (SPEC §12.10)'],
  ['EventSource', '네트워크 호출 금지 (SPEC §12.10)'],
  ['localStorage', '브라우저 저장소에 데이터 저장 금지 (SPEC §12.10)'],
  ['sessionStorage', '브라우저 저장소에 데이터 저장 금지 (SPEC §12.10)'],
  ['indexedDB', '브라우저 저장소에 데이터 저장 금지 (SPEC §12.10)'],
  ['eval', 'eval 금지'],
].map(([name, message]) => ({ name, message }));

const RESTRICTED_PROPS = [];
for (const obj of ['window', 'globalThis', 'self']) {
  for (const prop of ['fetch', 'XMLHttpRequest', 'WebSocket', 'localStorage', 'sessionStorage', 'indexedDB', 'eval']) {
    RESTRICTED_PROPS.push({ object: obj, property: prop, message: `${obj}.${prop} 금지 (SPEC §12.10)` });
  }
}
RESTRICTED_PROPS.push({ object: 'navigator', property: 'sendBeacon', message: '네트워크 호출 금지 (SPEC §12.10)' });
RESTRICTED_PROPS.push({ object: 'document', property: 'cookie', message: '쿠키 접근 금지 (SPEC §12.10)' });

const GLOBALS = {};
for (const g of ['console', 'window', 'document', 'navigator', 'globalThis', 'self', 'fetch', 'XMLHttpRequest', 'WebSocket',
  'EventSource', 'localStorage', 'sessionStorage', 'indexedDB', 'Intl', 'Date', 'Math', 'JSON', 'Number', 'String', 'Array', 'Object']) GLOBALS[g] = 'readonly';

let _eslint = null;
function engine() {
  if (_eslint) return _eslint;
  _eslint = new ESLint({
    cwd: __dirname,
    overrideConfigFile: true,
    allowInlineConfig: false,
    overrideConfig: [{
      files: ['**/*.tsx', '**/*.ts'],
      languageOptions: {
        parser: tsParser,
        parserOptions: { ecmaVersion: 2022, sourceType: 'module', ecmaFeatures: { jsx: true } },
        globals: GLOBALS,
      },
      plugins: {
        '@typescript-eslint': tsPlugin,
        gates: { rules: { 'allowed-imports': allowedImports, 'no-color-literals-in-style': noColorLiteralsInStyle } },
      },
      rules: {
        'no-unused-vars': 'off',
        '@typescript-eslint/no-unused-vars': ['error', { args: 'after-used', ignoreRestSiblings: true, varsIgnorePattern: '^_', argsIgnorePattern: '^_' }],
        'no-undef': 'off',
        'gates/allowed-imports': 'error',
        'gates/no-color-literals-in-style': 'error',
        'no-restricted-globals': ['error', ...RESTRICTED_GLOBALS],
        'no-restricted-properties': ['error', ...RESTRICTED_PROPS],
        'no-restricted-syntax': ['error',
          { selector: "NewExpression[callee.name='Function']", message: 'new Function 금지' },
          { selector: "JSXAttribute[name.name='tabIndex'] > JSXExpressionContainer > Literal[value>0]", message: '양수 tabIndex 금지 (KWCAG 6.1.2 초점 순서)' },
          { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: 'dangerouslySetInnerHTML 금지' },
        ],
        'no-eval': 'error',
        'no-implied-eval': 'error',
        'no-debugger': 'error',
        'no-console': 'off',
        '@typescript-eslint/no-explicit-any': 'warn',
      },
    }],
  });
  return _eslint;
}

/** 반환 {ok, errors:[{line, ruleId, message}], warnings:[...], ms} */
async function lint(code, filename = 'Screen.tsx') {
  const t0 = Date.now();
  const results = await engine().lintText(code, { filePath: path.join(__dirname, '__virtual__', filename) });
  const msgs = (results[0] && results[0].messages) || [];
  const map = (m) => ({ line: m.line, column: m.column, ruleId: m.ruleId || (m.fatal ? 'parse-error' : null), message: m.message });
  const errors = msgs.filter((m) => m.severity === 2).map(map);
  const warnings = msgs.filter((m) => m.severity === 1).map(map);
  return { ok: errors.length === 0, errors, warnings, ms: Date.now() - t0 };
}

module.exports = { lint, ALLOWED_IMPORT };
