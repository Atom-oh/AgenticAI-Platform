const path = require('node:path');
const fs = require('node:fs/promises');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');
const { build } = require('esbuild');
const { catalog } = require('../../react-kit/manifest.cjs');
const { zip } = require('../../react-kit/project.cjs');
const kit = path.resolve(__dirname, '../../react-kit');
const kitRequire = createRequire(path.join(kit, 'package.json'));
const sha256 = data => createHash('sha256').update(data).digest('hex');

async function sourcePackage(descriptor) {
  const files = {};
  for (const file of descriptor.files) {
    const bytes = await fs.readFile(path.join(kit, file.path));
    if (sha256(bytes) !== file.sha256) throw new Error('Component sources changed during export');
    files[file.path] = bytes;
  }
  files['package.json'] = Buffer.from(JSON.stringify({
    name: '@studio/approved-ui', version: descriptor.version, private: true, type: 'module',
    exports: { '.': './ui/index.tsx' }, sideEffects: ['**/*.css'], peerDependencies: { react: '18.3.1' },
  }, null, 2) + '\n');
  files['README.md'] = Buffer.from([
    '# 플랫폼 기본 React 컴포넌트', '',
    `버전: ${descriptor.version}`, `소스 기준: ${descriptor.hash}`, '',
    'Portal에서 실행하는 실제 컴포넌트의 공통 구현, 타입, CSS입니다. 모든 컴포넌트가 같은 공통 파일을 사용하므로 전체 ui/ 폴더를 함께 제공합니다.',
    '생성한 업무 화면이나 고객 사내 패키지가 아니며, 고객 승인·업무 검증을 뜻하지 않습니다.', '',
    '## 기존 React 프로젝트에 추가', '',
    'React 18.3.1과 TSX·CSS를 처리하는 번들러가 필요합니다. npm에 게시된 패키지가 아니므로 원격 설치하지 마세요.',
    '압축을 풀고 ui/ 폴더를 프로젝트의 src/studio-ui/로 복사하세요. 공통 파일의 상대 경로를 유지하세요.',
    'index.tsx가 tokens.css를 함께 불러옵니다. 컴포넌트 하나만 복사하면 공통 타입·코드·스타일이 빠집니다.', '',
    '```tsx', "import { Button } from './studio-ui';", '',
    'export default function Example() {', '  return <Button label="확인" onClick={() => console.log("확인")} />;', '}', '```', '',
    '로컬 workspace 패키지로 등록하는 경우 포함된 package.json의 @studio/approved-ui 이름을 사용할 수 있습니다.',
    '독립 실행 앱이나 node_modules는 포함하지 않습니다. 생성한 전체 화면의 검증된 소스는 UX 설계 작업실의 개발 전달에서 받으세요.', '',
  ].join('\n'));
  const sources = Buffer.from(JSON.stringify({
    schemaVersion: 1, catalogHash: descriptor.hash,
    files: Object.keys(files).sort().map(name => ({ path: name, sha256: sha256(files[name]), content: files[name].toString('utf8') })),
  }));
  const archive = zip(files);
  const descriptorFor = (bytes, extension) => ({
    file: `source-${sha256(bytes)}.${extension}`, sha256: sha256(bytes), bytes: bytes.length,
  });
  return { sources, archive, descriptor: { ...descriptorFor(sources, 'json'), archive: descriptorFor(archive, 'zip') } };
}

async function buildRenderer({ write = true } = {}) {
  const descriptor = catalog();
  const alias = {};
  for (const name of ['react', 'react-dom']) {
    const packageFile = kitRequire.resolve(`${name}/package.json`);
    if (JSON.parse(await fs.readFile(packageFile, 'utf8')).version !== '18.3.1') throw new Error('Renderer requires kit React 18.3.1');
    // All imports, including kit hooks, must use one React installation.
    alias[name] = path.dirname(packageFile);
  }
  const result = await build({
    absWorkingDir: path.resolve(__dirname, '..'), entryPoints: [path.join(__dirname, 'runtime.tsx')],
    bundle: true, write: false, minify: true, jsx: 'automatic', format: 'iife', platform: 'browser',
    target: ['es2022'], outdir: '/portal-react-build', charset: 'utf8', legalComments: 'inline', alias,
    metafile: true, supported: { 'inline-script': true },
    define: { 'process.env.NODE_ENV': '"production"', __PORTAL_REACT_CATALOG_HASH__: JSON.stringify(descriptor.hash) },
  });
  if (result.outputFiles.length !== 2 || Object.values(result.metafile.outputs).some(output => output.imports.length)) {
    throw new Error('React renderer must contain exactly one script and one local stylesheet');
  }
  const script = result.outputFiles.find(file => file.path.endsWith('.js'))?.text;
  const css = result.outputFiles.find(file => file.path.endsWith('.css'))?.text;
  if (!script || !css || /<\/script/i.test(script) || /<\/style/i.test(css)) throw new Error('Unsafe renderer serialization');
  const scriptHash = createHash('sha256').update(script).digest('base64');
  const policy = ["default-src 'none'", `script-src 'sha256-${scriptHash}'`, "script-src-attr 'none'",
    "style-src 'unsafe-inline'", 'img-src data:', "connect-src 'none'", "frame-src 'none'", "object-src 'none'",
    "form-action 'none'", "base-uri 'none'", "font-src 'none'", "worker-src 'none'", "media-src 'none'"].join('; ');
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><title>React 컴포넌트 예시</title><style>${css}\n.preview-shell{min-width:0}.preview-knobs{border-top:1px solid var(--studio-ui-line);padding-top:16px}.preview-reset{display:flex;justify-content:flex-start}output{overflow-wrap:anywhere}</style></head><body><div id="root"></div><script>${script}</script></body></html>`;
  const hash = sha256(html);
  const source = await sourcePackage(descriptor);
  if (catalog().hash !== descriptor.hash) throw new Error('Component sources changed during renderer build');
  const manifest = { schemaVersion: 1, catalog: descriptor,
    renderer: { file: `react-${hash}.html`, sha256: hash, bytes: Buffer.byteLength(html) }, sources: source.descriptor };
  if (manifest.renderer.bytes > 1_000_000 || Buffer.byteLength(JSON.stringify(manifest)) > 64_000) throw new Error('Renderer exceeds client limits');
  if (source.sources.length > 256_000 || source.archive.length > 512_000) throw new Error('Source export exceeds client limits');
  if (write) {
    const outdir = path.resolve(__dirname, '../public/portal-renderers/react');
    await fs.mkdir(outdir, { recursive: true });
    await fs.writeFile(path.join(outdir, manifest.renderer.file), html);
    await fs.writeFile(path.join(outdir, source.descriptor.file), source.sources);
    await fs.writeFile(path.join(outdir, source.descriptor.archive.file), source.archive);
    const temporary = path.join(outdir, `index.${process.pid}.tmp`);
    await fs.writeFile(temporary, JSON.stringify(manifest, null, 2) + '\n');
    await fs.rename(temporary, path.join(outdir, 'index.json'));
  }
  return { html, manifest, sources: source.sources, archive: source.archive, inputs: Object.keys(result.metafile.inputs) };
}

module.exports = { buildRenderer };
if (require.main === module) buildRenderer()
  .then(({ manifest }) => console.log(`Portal React ${manifest.catalog.version}: ${manifest.renderer.file} (${manifest.renderer.bytes} bytes)`))
  .catch(error => { console.error(error); process.exitCode = 1; });
