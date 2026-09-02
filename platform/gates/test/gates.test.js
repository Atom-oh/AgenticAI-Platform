'use strict';
// 게이트 실행기 테스트 (node:test) — 실제 typescript/eslint/axe-core 를 돌린다 (네트워크 없음)
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { runGates } = require('../index');
const { synthesize } = require('../dts');
const { snapshot, compare } = require('../visual');
const { COMPONENTS, VALID_SCREEN, V3_PROPS_SCREEN, IMG_NO_ALT_SCREEN, FETCH_SCREEN } = require('./helpers');

test('(a) Button@v2 props 만 쓰는 정상 화면은 build/types/lint/a11y 를 통과한다', async () => {
  const r = await runGates({ code: VALID_SCREEN, filename: 'Screen.tsx', components: COMPONENTS });
  assert.equal(r.build.ok, true, JSON.stringify(r.build.errors));
  assert.equal(r.types.ok, true, JSON.stringify(r.types.errors, null, 1));
  assert.equal(r.lint.ok, true, JSON.stringify(r.lint.errors));
  assert.equal(r.a11y.ok, true, JSON.stringify(r.a11y.violations, null, 1));
  assert.equal(r.visual.ok, true);
  assert.equal(r.visual.changed, null); // 기준선 없음
  assert.ok(r.visual.snapshot && r.visual.snapshot.hash);
  assert.ok(r.visual.snapshot.tagCounts.table === 1 && r.visual.snapshot.tagCounts.caption === 1);
  assert.equal(r.ok, true);
  assert.ok(r.types.declaredModules.includes('@atom/ui/button'));
});

test('(b) v2 스키마만 승인된 상태에서 Button v3 props(variant/tone/size) 를 쓰면 types 가 명확한 메시지로 실패한다', async () => {
  const r = await runGates({ code: V3_PROPS_SCREEN, components: COMPONENTS });
  assert.equal(r.build.ok, true);
  assert.equal(r.types.ok, false);
  const msgs = r.types.errors.map((e) => e.message).join('\n');
  assert.match(msgs, /variant/);
  assert.match(msgs, /ButtonProps/);
  assert.ok(r.types.errors.every((e) => e.file === 'Screen.tsx' && e.line >= 1));
  assert.equal(r.ok, false);
});

test('(c) alt 없는 raw <img> 는 a11y(image-alt → KWCAG 5.1.1) 로 실패한다', async () => {
  const r = await runGates({ code: IMG_NO_ALT_SCREEN, components: COMPONENTS });
  assert.equal(r.build.ok, true);
  assert.equal(r.types.ok, true, JSON.stringify(r.types.errors));
  assert.equal(r.a11y.ok, false);
  const v = r.a11y.violations.find((x) => x.id === 'image-alt');
  assert.ok(v, JSON.stringify(r.a11y.violations));
  assert.match(v.kwcag, /5\.1\.1/);
  assert.ok(v.nodes.length >= 1 && /img/.test(v.nodes[0].html));
  assert.equal(r.ok, false);
});

test('(d) fetch() 사용은 lint(no-restricted-globals) 로 실패한다', async () => {
  const r = await runGates({ code: FETCH_SCREEN, components: COMPONENTS });
  assert.equal(r.lint.ok, false);
  const e = r.lint.errors.find((x) => x.ruleId === 'no-restricted-globals');
  assert.ok(e, JSON.stringify(r.lint.errors));
  assert.match(e.message, /12\.10/);
  assert.equal(r.ok, false);
});

test('(e) 승인 목록에 없는 모듈 import 는 types 에서 Cannot find module 로 실패한다 (Deprecated 컴포넌트 시나리오)', async () => {
  const code = `// registry: LegacyPanel@v1
import { LegacyPanel } from '@atom/ui/legacy-panel';
export default function Screen() { return <LegacyPanel title="x" />; }
`;
  const r = await runGates({ code, components: COMPONENTS });
  assert.equal(r.types.ok, false);
  assert.match(r.types.errors.map((e) => e.message).join('\n'), /Cannot find module '@atom\/ui\/legacy-panel'/);
});

test('(f) 서드파티 import · localStorage · 색상 리터럴 style 은 lint 로 실패한다', async () => {
  const code = `// registry: Button@v2
import { Button } from '@atom/ui/button';
import dayjs from 'dayjs';
export default function Screen() {
  const saved = localStorage.getItem('x');
  return <div style={{ color: '#ff0000' }}><Button label={String(saved) + dayjs().format()} /></div>;
}
`;
  const r = await runGates({ code, components: COMPONENTS });
  assert.equal(r.lint.ok, false);
  const ids = r.lint.errors.map((e) => e.ruleId);
  assert.ok(ids.includes('gates/allowed-imports'), ids.join(','));
  assert.ok(ids.includes('no-restricted-globals'), ids.join(','));
  assert.ok(ids.includes('gates/no-color-literals-in-style'), ids.join(','));
});

test('(g) caption 없는 raw <table> 은 KWCAG 구조 검사(kwcag-table-caption) 로 잡힌다', async () => {
  const code = `// registry: PageHeader@v1
import { PageHeader } from '@atom/ui/page-header';
export default function Screen() {
  return (<div><PageHeader title="표" /><table><thead><tr><th>이름</th></tr></thead><tbody><tr><td>값</td></tr></tbody></table></div>);
}
`;
  const r = await runGates({ code, components: COMPONENTS });
  assert.equal(r.a11y.ok, false);
  const ids = r.a11y.violations.map((v) => v.id);
  assert.ok(ids.includes('kwcag-table-caption'), ids.join(','));
  assert.ok(ids.includes('kwcag-th-scope'), ids.join(','));
});

test('(h) 구문 오류는 build 실패 + types/a11y 미실행(note) 로 보고된다', async () => {
  const r = await runGates({ code: '// registry: Button@v2\nimport { Button } from "@atom/ui/button";\nexport default function Screen() { return <Button label="x" ; }\n', components: COMPONENTS });
  assert.equal(r.build.ok, false);
  assert.ok(r.build.errors.length >= 1 && r.build.errors[0].line >= 1);
  assert.equal(r.types.ok, false);
  assert.match(r.types.note, /빌드/);
  assert.equal(r.a11y.ok, false);
  assert.equal(r.ok, false);
});

test('(i) visual: 같은 마크업은 changed=false, 바뀐 마크업은 changed=true 와 태그 diff', () => {
  const a = snapshot('<div><button>조회</button></div>');
  const same = compare(snapshot('<div>\n  <button>조회</button>\n</div>'), a);
  assert.equal(same.changed, false);
  const diff = compare(snapshot('<div><button>조회</button><button>초기화</button><table></table></div>'), a);
  assert.equal(diff.changed, true);
  assert.deepEqual(diff.diff.button, { before: 1, after: 2 });
  assert.deepEqual(diff.diff.table, { before: 0, after: 1 });
  assert.match(diff.note, /픽셀/);
});

test('(j) dts 합성: enum → 리터럴 유니온, function → (...args)=>void, children → ReactNode, required 구분', () => {
  const { dts, modules } = synthesize(COMPONENTS);
  assert.ok(modules.includes('@atom/ui/card'));
  assert.match(dts, /kind\?: "primary" \| "secondary" \| "ghost";/);
  assert.match(dts, /onClick\?: \(\.\.\.args: any\[\]\) => void;/);
  assert.match(dts, /children\?: React\.ReactNode;/);
  assert.match(dts, /label: string;/);
  assert.match(dts, /declare module '@atom\/ui' \{/);
});

test('(k) CLI 모드: stdin JSON → stdout JSON', () => {
  const p = spawnSync(process.execPath, [path.join(__dirname, '..', 'index.js')], {
    input: JSON.stringify({ code: FETCH_SCREEN, components: COMPONENTS }), encoding: 'utf8', timeout: 120000,
  });
  assert.equal(p.status, 0, p.stderr);
  const out = JSON.parse(p.stdout);
  assert.equal(out.lint.ok, false);
  assert.equal(out.build.ok, true);
});

test('(l) previousSnapshot 이 주어지면 visual 이 변경 여부를 판정한다', async () => {
  const first = await runGates({ code: VALID_SCREEN, components: COMPONENTS });
  const second = await runGates({ code: VALID_SCREEN, components: COMPONENTS, previousSnapshot: first.visual.snapshot });
  assert.equal(second.visual.changed, false);
  assert.equal(second.visual.baseline, true);
  const third = await runGates({ code: V3_PROPS_SCREEN, components: COMPONENTS, previousSnapshot: first.visual.snapshot });
  assert.equal(third.visual.changed, true);
});
