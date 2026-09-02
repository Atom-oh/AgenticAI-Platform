'use strict';
// propsSchema(JSON Schema) → TypeScript 모듈 선언 합성.
// 승인(APPROVED) 컴포넌트만 선언되므로: 미승인 컴포넌트 import → "Cannot find module", 스키마 밖 props → 타입 오류.

const SAFE_MODULE = /^@?[A-Za-z0-9_][A-Za-z0-9_./-]*$/;
const SAFE_IDENT = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

function paren(t) {
  return /\s[|&]\s/.test(t) && !(t.startsWith('(') && t.endsWith(')')) ? `(${t})` : t;
}

function safeKey(k) {
  return SAFE_IDENT.test(k) ? k : JSON.stringify(k);
}

/** JSON Schema(또는 'string'/'fn'/'node' 축약) → TS 타입 문자열 */
function tsType(schema, depth = 0) {
  if (schema == null) return 'unknown';
  if (typeof schema === 'string') {
    const s = schema.toLowerCase();
    if (s === 'fn' || s === 'function') return '(...args: any[]) => void';
    if (s === 'node' || s === 'reactnode' || s === 'element') return 'React.ReactNode';
    if (s === 'integer') return 'number';
    if (['string', 'number', 'boolean', 'any', 'unknown', 'null'].includes(s)) return s;
    return 'unknown';
  }
  if (typeof schema !== 'object') return 'unknown';
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (Array.isArray(schema.enum) && schema.enum.length) return schema.enum.map((v) => JSON.stringify(v)).join(' | ');
  const union = schema.oneOf || schema.anyOf;
  if (Array.isArray(union) && union.length) return union.map((s) => paren(tsType(s, depth + 1))).join(' | ');
  if (Array.isArray(schema.allOf) && schema.allOf.length) return schema.allOf.map((s) => paren(tsType(s, depth + 1))).join(' & ');
  const t = schema.type;
  if (Array.isArray(t)) return t.map((x) => tsType({ ...schema, type: x }, depth)).join(' | ');
  switch (t) {
    case 'string': return 'string';
    case 'number': case 'integer': return 'number';
    case 'boolean': return 'boolean';
    case 'null': return 'null';
    case 'function': case 'fn': return '(...args: any[]) => void';
    case 'node': case 'reactnode': case 'element': return 'React.ReactNode';
    case 'array': return `Array<${tsType(schema.items, depth + 1)}>`;
    case 'object': {
      const props = schema.properties || {};
      const keys = Object.keys(props);
      if (!keys.length) return schema.additionalProperties === false ? 'Record<string, never>' : 'Record<string, unknown>';
      if (depth > 6) return 'Record<string, unknown>';
      const req = new Set(Array.isArray(schema.required) ? schema.required : []);
      const pad = '  '.repeat(depth + 1);
      const lines = keys.map((k) => `${pad}${safeKey(k)}${req.has(k) ? '' : '?'}: ${propType(k, props[k], depth + 1)};`);
      return `{\n${lines.join('\n')}\n${'  '.repeat(depth)}}`;
    }
    default:
      if (schema.properties) return tsType({ ...schema, type: 'object' }, depth);
      if (schema.items) return tsType({ ...schema, type: 'array' }, depth);
      return 'unknown';
  }
}

function propType(name, schema, depth) {
  if (name === 'children') return 'React.ReactNode';
  return tsType(schema, depth);
}

/** 컴포넌트 목록 → `declare module` 선언 문자열. 모듈당 1개 블록, 같은 모듈의 중복 exportName 은 첫 것만. */
function synthesize(components) {
  const byModule = new Map();
  const skipped = [];
  for (const c of components || []) {
    const mod = c.module || '@atom/ui';
    const exportName = c.exportName || c.name;
    if (!SAFE_MODULE.test(mod) || !SAFE_IDENT.test(exportName || '')) {
      skipped.push({ name: c.name, version: c.version, reason: '모듈 경로/export 이름이 안전하지 않아 선언에서 제외' });
      continue;
    }
    if (!byModule.has(mod)) byModule.set(mod, new Map());
    const m = byModule.get(mod);
    if (!m.has(exportName)) m.set(exportName, c);
  }
  const parts = [];
  for (const [mod, comps] of byModule) {
    const body = [];
    for (const [exportName, c] of comps) {
      const schema = c.propsSchema && typeof c.propsSchema === 'object' ? c.propsSchema : { type: 'object', properties: {} };
      const propsType = tsType({ ...schema, type: 'object' }, 1);
      body.push(`  /** ${c.name}@${c.version} — Registry APPROVED */`);
      body.push(`  export type ${exportName}Props = ${propsType};`);
      body.push(`  export const ${exportName}: (props: ${exportName}Props) => React.ReactElement | null;`);
    }
    parts.push(`declare module '${mod}' {\n  import * as React from 'react';\n${body.join('\n')}\n}`);
  }
  if (!byModule.has('@atom/ui') && byModule.size) {
    const re = [];
    for (const [mod, comps] of byModule) for (const exportName of comps.keys()) re.push(`  export { ${exportName} } from '${mod}';`);
    parts.push(`declare module '@atom/ui' {\n${re.join('\n')}\n}`);
  }
  return { dts: parts.join('\n\n') + '\n', modules: [...byModule.keys()], skipped };
}

module.exports = { tsType, synthesize };
