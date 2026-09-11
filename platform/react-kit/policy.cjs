'use strict';
const path = require('node:path').posix;
const ts = require('typescript');

const SOURCE_PATH = /^src\/(?:App\.tsx|(?:pages\/[a-z][a-z0-9-]*\.tsx)|(?:logic\/[a-z][a-z0-9-]*\.ts))$/;
const HOOKS = new Set(['useState', 'useReducer', 'useMemo', 'useCallback']);
const FORBIDDEN = new Set([
  'eval', 'Function', 'Symbol', 'require', 'importScripts', 'window', 'document', 'globalThis', 'self', 'parent', 'top', 'frames',
  'navigator', 'location', 'history', 'localStorage', 'sessionStorage', 'indexedDB', 'caches', 'fetch', 'Request',
  'XMLHttpRequest', 'WebSocket', 'EventSource', 'Worker', 'SharedWorker', 'RTCPeerConnection', 'webkitRTCPeerConnection',
  'customElements', 'MutationObserver', 'Image', 'Audio', 'HTMLElement', 'postMessage', 'open', 'close',
  'process', 'global', 'Buffer', 'Deno', 'Bun',
]);
const MEMBERS = new Set(['constructor', 'prototype', '__proto__', 'innerHTML', 'outerHTML', 'ownerDocument', 'contentWindow']);
const JSX_PROPS = new Set(['style', 'className', 'ref', 'dangerouslySetInnerHTML', 'srcDoc']);

function inspectSources(files, descriptor) {
  const diagnostics = [], used = new Set(), pageSources = [], pageIds = new Set();
  const known = new Set((descriptor?.components || []).map(item => item.name));
  if (!files || typeof files !== 'object' || Array.isArray(files)) {
    return { ok: false, diagnostics: ['React 소스 파일 맵이 필요합니다.'], components: [], pageSources: [] };
  }
  const names = Object.keys(files);
  if (!names.includes('src/App.tsx')) diagnostics.push('src/App.tsx가 필요합니다.');
  if (names.length > 24) diagnostics.push('소스 파일은 최대 24개입니다.');
  let bytes = 0;
  for (const name of names) {
    if (!SOURCE_PATH.test(name) || name.includes('\\') || typeof files[name] !== 'string') {
      diagnostics.push(`허용되지 않은 소스 경로: ${name.slice(0, 180)}`); continue;
    }
    bytes += Buffer.byteLength(files[name]);
  }
  if (bytes > 128 * 1024) diagnostics.push('전체 생성 소스가 128KiB를 초과했습니다.');
  if (diagnostics.length) return { ok: false, diagnostics, components: [], pageSources: [] };

  for (const name of names.sort()) {
    const source = ts.createSourceFile(name, files[name], ts.ScriptTarget.ES2020, true,
      name.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const aliases = new Map(), locals = new Set();
    function problem(node, message) {
      const position = source.getLineAndCharacterOfPosition(node.getStart(source));
      diagnostics.push(`${name}:${position.line + 1}:${position.character + 1} ${message}`);
    }
    function localModule(module, node) {
      const resolved = path.normalize(path.join(path.dirname(name), module));
      if (!module.startsWith('.') || ![resolved, resolved + '.tsx', resolved + '.ts'].some(value => names.includes(value))) {
        problem(node, `생성 소스 안의 정적 모듈만 가져올 수 있습니다: ${module}`); return false;
      }
      return true;
    }
    for (const error of source.parseDiagnostics) {
      diagnostics.push(`${name}: ${ts.flattenDiagnosticMessageText(error.messageText, '\n')}`);
    }
    if (source.referencedFiles.length || source.typeReferenceDirectives.length || source.libReferenceDirectives.length) {
      diagnostics.push(`${name}: 외부 타입·파일 reference 지시문은 허용하지 않습니다.`);
    }
    // Record all local composition names before visiting JSX references.
    function bindings(node) {
      if ((ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) && node.name) locals.add(node.name.text);
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer &&
          (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) locals.add(node.name.text);
      ts.forEachChild(node, bindings);
    }
    bindings(source);
    for (const statement of source.statements) {
      if (!ts.isImportDeclaration(statement)) continue;
      const module = statement.moduleSpecifier.text;
      const clause = statement.importClause;
      if (!clause) { problem(statement, '부작용 import는 허용하지 않습니다.'); continue; }
      const bindings = clause.namedBindings;
      if (module === 'react' || module === '@studio/approved-ui') {
        if (clause.name || (bindings && ts.isNamespaceImport(bindings))) {
          problem(statement, 'React와 기준 컴포넌트는 명시적인 named import를 사용하세요.'); continue;
        }
        for (const item of bindings?.elements || []) {
          const imported = item.propertyName?.text || item.name.text;
          if (clause.isTypeOnly || item.isTypeOnly) continue;
          if (module === 'react') {
            if (!HOOKS.has(imported)) problem(item, `허용된 React 훅만 사용할 수 있습니다: ${imported}`);
          } else if (!known.has(imported)) problem(item, `기준 패키지에 없는 컴포넌트입니다: ${imported}`);
          else aliases.set(item.name.text, imported);
        }
      } else if (localModule(module, statement)) {
        if (clause.name) locals.add(clause.name.text);
        if (bindings && ts.isNamedImports(bindings)) for (const item of bindings.elements) locals.add(item.name.text);
        if (bindings && ts.isNamespaceImport(bindings)) problem(statement, '생성 모듈도 named/default import를 사용하세요.');
      }
    }
    function visit(node) {
      if (ts.isImportEqualsDeclaration(node)) problem(node, 'import=require 구문은 허용하지 않습니다.');
      if (ts.isImportTypeNode(node) || ts.isModuleDeclaration(node)) problem(node, '동적 타입 import·전역 모듈 재정의는 허용하지 않습니다.');
      if (node.kind === ts.SyntaxKind.AnyKeyword) problem(node, '컴포넌트 타입 검사를 우회하는 any는 허용하지 않습니다.');
      if (ts.isExportDeclaration(node) && node.moduleSpecifier) localModule(node.moduleSpecifier.text, node);
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) problem(node, '동적 import는 허용하지 않습니다.');
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && aliases.has(node.expression.text)) {
        problem(node, '기준 컴포넌트를 함수로 직접 호출하거나 반환 요소를 다시 작성할 수 없습니다. JSX로 조합하세요.');
      }
      if (ts.isPropertyAssignment(node) && node.name.getText(source).replace(/['"]/g, '') === '$$typeof') {
        problem(node, 'React 요소 객체를 직접 만들 수 없습니다.');
      }
      if (ts.isIdentifier(node) && FORBIDDEN.has(node.text)) {
        const parent = node.parent;
        const propertyName = (ts.isPropertyAssignment(parent) || ts.isPropertySignature(parent) ||
          ts.isPropertyAccessExpression(parent)) && parent.name === node;
        if (!propertyName) problem(node, `직접 DOM·네트워크·동적 실행 API는 사용할 수 없습니다: ${node.text}`);
      }
      if (ts.isPropertyAccessExpression(node) && MEMBERS.has(node.name.text)) problem(node, `허용되지 않은 객체 접근: ${node.name.text}`);
      if (ts.isElementAccessExpression(node) && node.argumentExpression &&
          ts.isStringLiteralLike(node.argumentExpression) && MEMBERS.has(node.argumentExpression.text)) {
        problem(node, `허용되지 않은 객체 접근: ${node.argumentExpression.text}`);
      }
      if (ts.isJsxSpreadAttribute(node)) problem(node, '컴포넌트 속성은 명시적으로 지정하세요. JSX spread는 허용하지 않습니다.');
      if (ts.isJsxAttribute(node) && JSX_PROPS.has(node.name.getText(source))) problem(node, '기준 컴포넌트의 코드·스타일을 덮어쓸 수 없습니다.');
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const tag = node.tagName.getText(source);
        const component = aliases.get(tag);
        if (/^[a-z]/.test(tag) || !/^[A-Z][A-Za-z0-9_]*$/.test(tag)) problem(node, 'UI는 기준 컴포넌트와 로컬 페이지 조합으로 구성하세요.');
        else if (!component && !locals.has(tag)) problem(node, `출처를 확인할 수 없는 JSX 컴포넌트: ${tag}`);
        if (component) used.add(component);
        if (component === 'Screen') {
          const field = node.attributes.properties.find(item => ts.isJsxAttribute(item) && item.name.getText(source) === 'pageId');
          const value = field?.initializer;
          if (!value || !ts.isStringLiteral(value) || !/^[a-z][a-z0-9-]{0,63}$/.test(value.text)) {
            problem(node, 'Screen의 pageId는 고정된 안전한 문자열이어야 합니다.');
          } else if (pageIds.has(value.text)) problem(node, `중복 pageId: ${value.text}`);
          else { pageIds.add(value.text); pageSources.push({ pageId: value.text, path: name }); }
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  if (!pageSources.length) diagnostics.push('실제 Screen 컴포넌트와 pageId가 필요합니다.');
  return { ok: diagnostics.length === 0, diagnostics: diagnostics.slice(0, 80), components: [...used].sort(), pageSources };
}

module.exports = { inspectSources, SOURCE_PATH };
