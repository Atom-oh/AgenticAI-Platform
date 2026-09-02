'use strict';
// 빌드(구문·트랜스파일) + 타입(tsc --noEmit strict) 게이트.
// 가상 파일(Screen.tsx, 합성 @atom/ui 선언)을 서비스하는 CompilerHost — lib.*.d.ts 와 @types/react 는 node_modules 에서 읽는다.
const path = require('path');
const ts = require('typescript');
const { synthesize } = require('./dts');

const GATES_DIR = __dirname;
const VIRTUAL_DIR = path.join(GATES_DIR, '__virtual__');
const libCache = new Map(); // 웜 인보크 재사용 (lib/@types 소스파일 파싱 비용 절감)

const OPTIONS = {
  strict: true,
  noEmit: true,
  jsx: ts.JsxEmit.ReactJSX,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  target: ts.ScriptTarget.ES2020,
  types: ['react'],
  typeRoots: [path.join(GATES_DIR, 'node_modules', '@types')],
  skipLibCheck: true,
  esModuleInterop: true,
  allowSyntheticDefaultImports: true,
  isolatedModules: true,
  forceConsistentCasingInFileNames: true,
  noUnusedLocals: false,
  noUnusedParameters: false,
  noImplicitAny: true,
};

function fmt(diags) {
  return diags.map((d) => {
    let line;
    if (d.file && typeof d.start === 'number') line = d.file.getLineAndCharacterOfPosition(d.start).line + 1;
    return {
      file: d.file ? path.basename(d.file.fileName) : '',
      line,
      code: d.code,
      message: ts.flattenDiagnosticMessageText(d.messageText, '\n'),
    };
  });
}

/** 빌드 게이트: transpileModule(구문 진단) — 성공 시 CJS 출력(js)을 a11y 게이트가 재사용한다. */
function transpileBuild(code, filename = 'Screen.tsx') {
  const t0 = Date.now();
  let out;
  try {
    out = ts.transpileModule(code, {
      fileName: filename, reportDiagnostics: true,
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
    });
  } catch (e) {
    return { ok: false, errors: [{ file: filename, message: `트랜스파일 예외: ${e.message}` }], js: '', ms: Date.now() - t0 };
  }
  const errors = fmt(out.diagnostics || []);
  // transpileModule 은 일부 구문 오류를 놓칠 수 있어 SourceFile 파서 진단도 합친다
  const sf = ts.createSourceFile(filename, code, ts.ScriptTarget.ES2020, true, ts.ScriptKind.TSX);
  const parseDiags = sf.parseDiagnostics || [];
  for (const d of fmt(parseDiags)) if (!errors.some((e) => e.line === d.line && e.message === d.message)) errors.push(d);
  if (!code.trim()) errors.push({ file: filename, message: '코드가 비어 있습니다.' });
  return { ok: errors.length === 0, errors, js: out.outputText || '', ms: Date.now() - t0 };
}

/** 타입 게이트: 가상 Screen.tsx + 합성 선언으로 createProgram → 의미 진단. */
function checkTypes(code, components, filename = 'Screen.tsx') {
  const t0 = Date.now();
  const screenPath = path.join(VIRTUAL_DIR, filename);
  const dtsPath = path.join(VIRTUAL_DIR, 'atom-ui.d.ts');
  const synth = synthesize(components || []);
  const virtual = new Map([[screenPath, code], [dtsPath, synth.dts]]);

  const host = ts.createCompilerHost(OPTIONS, true);
  const origFileExists = host.fileExists.bind(host);
  const origReadFile = host.readFile.bind(host);
  const origGetSourceFile = host.getSourceFile.bind(host);
  host.fileExists = (f) => virtual.has(f) || origFileExists(f);
  host.readFile = (f) => (virtual.has(f) ? virtual.get(f) : origReadFile(f));
  host.getSourceFile = (f, lang, onError, shouldCreate) => {
    if (virtual.has(f)) return ts.createSourceFile(f, virtual.get(f), lang, true);
    if (f.includes('node_modules')) {
      if (!libCache.has(f)) libCache.set(f, origGetSourceFile(f, lang, onError, shouldCreate));
      return libCache.get(f);
    }
    return origGetSourceFile(f, lang, onError, shouldCreate);
  };
  host.getCurrentDirectory = () => GATES_DIR;
  host.writeFile = () => {};

  const program = ts.createProgram({ rootNames: [screenPath, dtsPath], options: OPTIONS, host });
  const sf = program.getSourceFile(screenPath);
  const dsf = program.getSourceFile(dtsPath);
  const diags = [
    ...program.getOptionsDiagnostics(),
    ...program.getGlobalDiagnostics(),
    ...(sf ? program.getSyntacticDiagnostics(sf) : []),
    ...(sf ? program.getSemanticDiagnostics(sf) : []),
    ...(dsf ? program.getSemanticDiagnostics(dsf) : []), // 스키마 합성 자체의 문제도 숨기지 않는다
  ];
  const errors = fmt(diags.filter((d) => d.category === ts.DiagnosticCategory.Error));
  return { ok: errors.length === 0, errors, modules: synth.modules, skipped: synth.skipped, dts: synth.dts, ms: Date.now() - t0 };
}

module.exports = { transpileBuild, checkTypes, OPTIONS };
