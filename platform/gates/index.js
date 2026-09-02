'use strict';
// F5 검증 게이트 실행기 — Node 20 Lambda handler + CLI(stdin JSON → stdout JSON).
// 입력  {code, filename:'Screen.tsx', components:[{name, version, module, exportName, propsSchema}], previousSnapshot?}
// 출력  {build:{ok,errors[]}, types:{ok,errors[]}, lint:{ok,errors[]}, a11y:{ok,violations[]}, visual:{ok,changed,note,snapshot}, ok}
// 각 게이트는 실제 도구(typescript / eslint / axe-core)를 실행한다. 실행할 수 없으면 ok:false 와 note 로 이유를 남긴다 — 통과를 흉내내지 않는다.
const { transpileBuild, checkTypes } = require('./typecheck');
const { lint } = require('./lint');
const { a11y } = require('./a11y');
const { snapshot, compare } = require('./visual');

const MAX_CODE = 200_000;

function errMessage(e) {
  return String((e && e.message) || e).slice(0, 400);
}

async function runGates(input) {
  const t0 = Date.now();
  const inp = input && typeof input === 'object' ? input : {};
  const code = String(inp.code || '').slice(0, MAX_CODE);
  const filename = String(inp.filename || 'Screen.tsx');
  const components = Array.isArray(inp.components) ? inp.components : [];
  const out = { filename, componentCount: components.length, node: process.version };

  // ① 빌드(구문·트랜스파일)
  const b = transpileBuild(code, filename);
  out.build = { ok: b.ok, errors: b.errors, ms: b.ms };

  // ② 타입 — 승인 propsSchema 로 합성한 선언에 대해 strict 검사
  if (b.ok) {
    try {
      const t = checkTypes(code, components, filename);
      out.types = { ok: t.ok, errors: t.errors, declaredModules: t.modules, skipped: t.skipped, ms: t.ms };
    } catch (e) {
      out.types = { ok: false, errors: [{ file: filename, message: `타입 검사기 예외: ${errMessage(e)}` }] };
    }
  } else {
    out.types = { ok: false, errors: [], note: '빌드(구문) 실패로 타입 검사를 실행하지 않았습니다.' };
  }

  // ③ 린트
  try {
    out.lint = await lint(code, filename);
  } catch (e) {
    out.lint = { ok: false, errors: [{ ruleId: 'lint-runner', message: `린트 실행 예외: ${errMessage(e)}` }], warnings: [] };
  }

  // ④ KWCAG 접근성 (axe-core) — 빌드 산출물(CJS) 을 스텁 런타임으로 렌더링
  let markup = '';
  if (b.ok) {
    try {
      const a = await a11y(b.js);
      markup = a.markup;
      out.a11y = { ok: a.ok, violations: a.violations, incomplete: a.incomplete, passes: a.passes,
        note: a.note, kwcagNote: a.kwcagNote, engine: a.engine, ms: a.ms };
    } catch (e) {
      out.a11y = { ok: false, violations: [], note: `렌더링 실패 — ${errMessage(e)}`, error: errMessage(e) };
    }
  } else {
    out.a11y = { ok: false, violations: [], note: '빌드 실패로 접근성 검사를 실행하지 않았습니다.' };
  }

  // ⑤ 시각 회귀(구조 스냅샷) — 픽셀 비교 아님
  out.visual = markup
    ? compare(snapshot(markup), inp.previousSnapshot)
    : { ok: false, changed: null, note: '렌더링 결과가 없어 구조 스냅샷을 만들지 못했습니다.' };

  out.ok = !!(out.build.ok && out.types.ok && out.lint.ok && out.a11y.ok && out.visual.ok);
  out.elapsedMs = Date.now() - t0;
  return out;
}

// Lambda 진입점 — 직접 invoke(JSON payload) 또는 {body: "<json>"} 둘 다 받는다
exports.handler = async (event) => {
  let input = event || {};
  if (input && typeof input.body === 'string') {
    try { input = JSON.parse(input.body); } catch (e) { return { ok: false, error: `잘못된 JSON: ${errMessage(e)}` }; }
  }
  return runGates(input);
};
exports.runGates = runGates;

// CLI 모드: node index.js < input.json
if (require.main === module) {
  const chunks = [];
  process.stdin.on('data', (c) => chunks.push(c));
  process.stdin.on('end', async () => {
    try {
      const input = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
      const result = await runGates(input);
      process.stdout.write(JSON.stringify(result));
      process.exit(0);
    } catch (e) {
      process.stderr.write(`gates: ${errMessage(e)}\n`);
      process.exit(1);
    }
  });
}
