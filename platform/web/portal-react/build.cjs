const path = require('node:path');
const fs = require('node:fs/promises');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');
const { build } = require('esbuild');
const { catalog } = require('../../react-kit/manifest.cjs');
const kit = path.resolve(__dirname, '../../react-kit');
const kitRequire = createRequire(path.join(kit, 'package.json'));
const sha256 = data => createHash('sha256').update(data).digest('hex');

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
  const manifest = { schemaVersion: 1, catalog: descriptor, renderer: { file: `react-${hash}.html`, sha256: hash, bytes: Buffer.byteLength(html) } };
  if (manifest.renderer.bytes > 1_000_000 || Buffer.byteLength(JSON.stringify(manifest)) > 64_000) throw new Error('Renderer exceeds client limits');
  if (write) {
    const outdir = path.resolve(__dirname, '../public/portal-renderers/react');
    await fs.mkdir(outdir, { recursive: true });
    await fs.writeFile(path.join(outdir, manifest.renderer.file), html);
    const temporary = path.join(outdir, `index.${process.pid}.tmp`);
    await fs.writeFile(temporary, JSON.stringify(manifest, null, 2) + '\n');
    await fs.rename(temporary, path.join(outdir, 'index.json'));
  }
  return { html, manifest, inputs: Object.keys(result.metafile.inputs) };
}

module.exports = { buildRenderer };
if (require.main === module) buildRenderer()
  .then(({ manifest }) => console.log(`Portal React ${manifest.catalog.version}: ${manifest.renderer.file} (${manifest.renderer.bytes} bytes)`))
  .catch(error => { console.error(error); process.exitCode = 1; });
