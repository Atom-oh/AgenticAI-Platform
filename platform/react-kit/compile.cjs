'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const ts = require('typescript');
const esbuild = require('esbuild');
const { catalog } = require('./manifest.cjs');
const { inspectSources, SOURCE_PATH } = require('./policy.cjs');
const { hashFiles, zip, sourceProject, TS_CONFIG } = require('./project.cjs');

const ROOT = __dirname;
const ASSET_SOURCE = 'src/logic/assets.ts';
const HTML_POLICY = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
const escapeHtml = value => String(value).replace(/[&<>"']/g, character =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
function preparedSources(input) {
  if (Object.prototype.hasOwnProperty.call(input.files || {}, ASSET_SOURCE)) {
    throw new Error(`${ASSET_SOURCE}는 서버가 제공하는 자산 연결 파일입니다.`);
  }
  const files = { ...input.files }, assets = input.assets || {};
  validateAssets(assets);
  if (Object.keys(assets).length) {
    const sorted = Object.fromEntries(Object.keys(assets).sort().map(key => [key, assets[key]]));
    files[ASSET_SOURCE] = `export const assets = Object.freeze(${JSON.stringify(sorted)} as const);\n`;
  }
  return files;
}
function validateAssets(assets) {
  if (!assets || typeof assets !== 'object' || Array.isArray(assets)) throw new Error('배포 이미지 맵이 필요합니다.');
  if (Object.keys(assets).length > 20) throw new Error('배포 자산은 최대 20개입니다.');
  let size = 0;
  for (const [id, uri] of Object.entries(assets)) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(id) || ['__proto__', 'constructor', 'prototype'].includes(id) || typeof uri !== 'string' ||
        !/^data:image\/(?:png|jpeg|svg\+xml);base64,[A-Za-z0-9+/=]+$/.test(uri)) throw new Error('로컬 이미지 자산만 연결할 수 있습니다.');
    size += Buffer.byteLength(uri);
  }
  if (size > 3 * 1024 * 1024) throw new Error('배포용 이미지 데이터가 3MiB를 초과했습니다.');
}
function validateAssetSource(source) {
  const match = /^export const assets = Object\.freeze\((.*) as const\);\n$/s.exec(source);
  if (!match) throw new Error('서버 자산 모듈에 실행 코드를 추가할 수 없습니다.');
  const assets = JSON.parse(match[1]);
  validateAssets(assets);
  if (match[1] !== JSON.stringify(assets)) throw new Error('자산 모듈 형식이 변경되었습니다.');
}
function typeDiagnostics(files, directory) {
  const config = ts.convertCompilerOptionsFromJson(TS_CONFIG.compilerOptions, directory);
  const program = ts.createProgram(Object.keys(files).map(name => path.join(directory, name)), config.options);
  const diagnostics = [...config.errors, ...ts.getPreEmitDiagnostics(program)].filter(value => value.category === ts.DiagnosticCategory.Error)
    .slice(0, 60).map(value => {
      const location = value.file && value.start !== undefined ? value.file.getLineAndCharacterOfPosition(value.start) : null;
      const file = value.file ? path.relative(directory, value.file.fileName).replace(/\\/g, '/') : 'typescript';
      return `${file}${location ? ':' + (location.line + 1) : ''} ${ts.flattenDiagnosticMessageText(value.messageText, '\n')}`;
    });
  const checker = program.getTypeChecker();
  const native = type => Boolean(type.flags & (ts.TypeFlags.StringLike | ts.TypeFlags.Any | ts.TypeFlags.Unknown)) ||
    (type.isUnion() && type.types.some(native));
  for (const name of Object.keys(files)) {
    const source = program.getSourceFile(path.join(directory, name));
    if (!source) continue;
    function visit(node) {
      if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && native(checker.getTypeAtLocation(node.tagName))) {
        const position = source.getLineAndCharacterOfPosition(node.getStart(source));
        diagnostics.push(`${name}:${position.line + 1} 문자열/any JSX 별칭으로 기준 컴포넌트를 우회할 수 없습니다.`);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  return diagnostics.slice(0, 60);
}
function preview(files) {
  let html = files['index.html'].toString();
  html = html.replace(`<meta http-equiv="Content-Security-Policy" content="${escapeHtml(HTML_POLICY)}">`, '');
  const style = files['assets/app.css']?.toString() || '';
  const script = files['assets/app.js'].toString().replace(/<\/script/gi, '<\\/script');
  return html.replace('<link rel="stylesheet" href="./assets/app.css">', `<style>${style}</style>`)
    .replace('<script src="./assets/app.js" defer></script>', `<script>${script}</script>`);
}
async function buildProject(input, options = {}) {
  const gates = Object.fromEntries(['policy', 'types', 'build', 'components'].map(name => [name, { status: 'not-run' }]));
  const result = { ok: false, gates, diagnostics: [] };
  let directory;
  try {
    const descriptor = catalog();
    result.catalogHash = descriptor.hash;
    if (input.expectedCatalogHash !== descriptor.hash) {
      gates.components.status = 'fail';
      result.diagnostics = ['승인한 React 컴포넌트 버전/해시가 현재 코드와 일치하지 않습니다.'];
      return result;
    }
    gates.components.status = 'pass';
    const generated = options.projectSources ? { ...input.files } : preparedSources(input);
    // Generated asset bytes are bounded separately, so count user code before adding the trusted module.
    const checked = { ...generated };
    if (checked[ASSET_SOURCE]) {
      validateAssetSource(checked[ASSET_SOURCE]);
      checked[ASSET_SOURCE] = 'export const assets = Object.freeze({} as const);';
    }
    const policy = inspectSources(checked, descriptor);
    gates.policy.status = policy.ok ? 'pass' : 'fail';
    result.pageSources = policy.pageSources;
    result.components = policy.components;
    if (!policy.ok) { result.diagnostics = policy.diagnostics; return result; }

    directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-react-build-'));
    fs.symlinkSync(path.join(ROOT, 'node_modules'), path.join(directory, 'node_modules'), 'dir');
    fs.symlinkSync(path.join(ROOT, 'ui'), path.join(directory, 'ui'), 'dir');
    for (const [name, contents] of Object.entries(generated)) {
      fs.mkdirSync(path.dirname(path.join(directory, name)), { recursive: true });
      fs.writeFileSync(path.join(directory, name), contents);
    }
    const diagnostics = typeDiagnostics(generated, directory);
    gates.types.status = diagnostics.length ? 'fail' : 'pass';
    if (diagnostics.length) { result.diagnostics = diagnostics; return result; }
    if (options.checkOnly) { result.ok = true; return result; }

    const entry = path.join(directory, '_studio-entry.tsx');
    fs.writeFileSync(entry, `import {createRoot} from 'react-dom/client';import App from './src/App';import './ui/tokens.css';createRoot(document.getElementById('root')!).render(<App/>);`);
    const output = await esbuild.build({
      entryPoints: [entry], outfile: path.join(directory, 'dist/assets/app.js'),
      absWorkingDir: directory, bundle: true, write: false, minify: true,
      format: 'iife', platform: 'browser', target: 'es2020', jsx: 'automatic',
      define: { 'process.env.NODE_ENV': '"production"' }, legalComments: 'none',
      alias: { '@studio/approved-ui': path.join(ROOT, 'ui/index.tsx') },
      loader: { '.woff2': 'dataurl', '.woff': 'dataurl', '.png': 'dataurl', '.svg': 'dataurl' },
      logLevel: 'silent',
    });
    const bundle = {};
    for (const file of output.outputFiles) {
      const name = path.relative(path.join(directory, 'dist'), file.path).replace(/\\/g, '/');
      if (!['assets/app.js', 'assets/app.css'].includes(name)) throw new Error('예상하지 않은 빌드 출력 경로입니다.');
      bundle[name] = Buffer.from(file.contents);
    }
    if (!bundle['assets/app.js'] || !bundle['assets/app.css']) throw new Error('React/CSS 빌드 출력이 누락되었습니다.');
    bundle['index.html'] = Buffer.from(`<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${escapeHtml(HTML_POLICY)}"><title>${escapeHtml(input.contract?.title || '검증된 React 화면')}</title><link rel="icon" href="data:,"><link rel="stylesheet" href="./assets/app.css"></head><body><div id="root"></div><script src="./assets/app.js" defer></script></body></html>`);
    bundle['THIRD_PARTY_NOTICES.txt'] = Buffer.concat(['react', 'react-dom'].flatMap(name => [
      Buffer.from(`\n${name} 18.3.1\n`), fs.readFileSync(path.join(ROOT, 'node_modules', name, 'LICENSE')),
    ]));
    gates.build.status = 'pass';
    result.bundleHash = hashFiles(bundle);
    result.files = Object.fromEntries(Object.entries(bundle).map(([name, bytes]) => [name, bytes.toString('base64')]));
    result.previewHtml = preview(bundle);
    if (!options.skipArchive) {
      const project = sourceProject(ROOT, generated, descriptor, input.contract);
      result.sourceHash = hashFiles(project);
      result.projectZipBase64 = zip(project).toString('base64');
      result.distZipBase64 = zip(bundle).toString('base64');
      result.sourceFiles = generated;
    }
    result.ok = true;
    return result;
  } catch (error) {
    if (gates.policy.status === 'not-run') gates.policy.status = 'fail';
    else if (gates.build.status === 'not-run') gates.build.status = 'fail';
    result.diagnostics = (error.errors?.map(item => item.text) || [String(error.message || 'React build failed')]).slice(0, 40);
    return result;
  } finally {
    if (directory) fs.rmSync(directory, { recursive: true, force: true });
  }
}

async function main() {
  const command = process.argv[2];
  if (command === '--build' || command === '--check') {
    const files = {};
    function visit(base) {
      for (const name of fs.readdirSync(base)) {
        const absolute = path.join(base, name);
        if (fs.lstatSync(absolute).isSymbolicLink()) throw new Error('Generated source cannot be a symlink');
        if (fs.statSync(absolute).isDirectory()) visit(absolute);
        else {
          const relative = path.relative(ROOT, absolute).replace(/\\/g, '/');
          if (!SOURCE_PATH.test(relative)) throw new Error('Unsupported source file: ' + relative);
          files[relative] = fs.readFileSync(absolute, 'utf8');
        }
      }
    }
    visit(path.join(ROOT, 'src'));
    const lock = JSON.parse(fs.readFileSync(path.join(ROOT, 'studio.lock.json'), 'utf8'));
    const contract = JSON.parse(fs.readFileSync(path.join(ROOT, 'test/contract.json'), 'utf8'));
    const result = await buildProject({ files, expectedCatalogHash: lock.catalogHash, contract },
      { projectSources: true, skipArchive: true, checkOnly: command === '--check' });
    if (!result.ok) { console.error(result.diagnostics.join('\n')); process.exitCode = 1; return; }
    if (command === '--build') {
      fs.rmSync(path.join(ROOT, 'dist'), { recursive: true, force: true });
      for (const [name, contents] of Object.entries(result.files)) {
        const destination = path.join(ROOT, 'dist', name);
        fs.mkdirSync(path.dirname(destination), { recursive: true }); fs.writeFileSync(destination, Buffer.from(contents, 'base64'));
      }
    }
    console.log(command === '--check' ? 'Types and component lock passed.' : 'Production build passed: ' + result.bundleHash);
    return;
  }
  if (!command || !process.argv[3]) throw new Error('Usage: node compile.cjs request.json output.json');
  const request = JSON.parse(fs.readFileSync(command, 'utf8'));
  fs.writeFileSync(process.argv[3], JSON.stringify(await buildProject(request)));
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { buildProject };
