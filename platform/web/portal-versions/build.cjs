const path = require('node:path');
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');
const { build } = require('esbuild');
const library = path.resolve(__dirname, '../../component-library');
const kitRequire = createRequire(path.resolve(__dirname, '../../react-kit/package.json'));
const sha256 = data => createHash('sha256').update(data).digest('hex');

function sourceCatalog() {
  const data = JSON.parse(fs.readFileSync(path.join(library, 'catalog.json'), 'utf8'));
  if (data.schemaVersion !== 1 || data.package !== '@atom/portal-components') throw new Error('Invalid version catalog');
  const read = name => {
    const file = path.join(library, name);
    if (!/^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+$/.test(name) || name.split('/').includes('..') ||
        fs.realpathSync(file) !== file || fs.statSync(file).size > 100_000) throw new Error('Invalid component source');
    return fs.readFileSync(file, 'utf8');
  };
  const components = data.components.map(c => {
    const files = [...new Set([c.entry, ...data.sharedFiles])].sort().map(name => {
      const content = read(name);
      return { path: name, sha256: sha256(content), content };
    });
    const identity = {
      id: c.id, version: c.version, package: data.package, exportName: c.exportName,
      files: files.map(({ path, sha256 }) => ({ path, sha256 })),
    };
    return { ...c, sourceHash: sha256(JSON.stringify(identity)), files };
  });
  const catalog = { package: data.package, components };
  return { ...catalog, hash: sha256(JSON.stringify(catalog)) };
}

async function buildRenderer({ write = true } = {}) {
  const catalog = sourceCatalog();
  const alias = {};
  for (const name of ['react', 'react-dom']) {
    const file = kitRequire.resolve(`${name}/package.json`);
    if (JSON.parse(fs.readFileSync(file, 'utf8')).version !== '18.3.1') throw new Error('React 18.3.1 required');
    alias[name] = path.dirname(file);
  }
  const result = await build({
    absWorkingDir: path.resolve(__dirname, '..'), entryPoints: [path.join(__dirname, 'runtime.tsx')],
    bundle: true, write: false, minify: true, jsx: 'automatic', format: 'iife', platform: 'browser',
    target: ['es2022'], outdir: '/portal-versions-build', charset: 'utf8', legalComments: 'inline', alias,
    metafile: true, supported: { 'inline-script': true },
    define: {
      'process.env.NODE_ENV': '"production"', __VERSION_CATALOG_HASH__: JSON.stringify(catalog.hash),
      __VERSION_IDENTITIES__: JSON.stringify(catalog.components.map(({ id, version, sourceHash }) => ({ id, version, sourceHash }))),
    },
  });
  const inputs = Object.keys(result.metafile.inputs);
  for (const c of catalog.components) {
    if (!inputs.some(file => file.endsWith(`component-library/${c.entry}`))) throw new Error(`Missing compiled implementation: ${c.id}`);
  }
  if (result.outputFiles.length !== 2 || Object.values(result.metafile.outputs).some(output => output.imports.length)) {
    throw new Error('Version renderer must contain one script and one stylesheet');
  }
  const script = result.outputFiles.find(file => file.path.endsWith('.js'))?.text;
  const css = result.outputFiles.find(file => file.path.endsWith('.css'))?.text;
  if (!script || !css || /<\/script/i.test(script) || /<\/style/i.test(css)) throw new Error('Unsafe renderer serialization');
  const scriptHash = createHash('sha256').update(script).digest('base64');
  const policy = ["default-src 'none'", `script-src 'sha256-${scriptHash}'`, "script-src-attr 'none'",
    "style-src 'unsafe-inline'", "img-src data:", "connect-src 'none'", "frame-src 'none'", "object-src 'none'",
    "form-action 'none'", "base-uri 'none'", "font-src 'none'", "worker-src 'none'", "media-src 'none'"].join('; ');
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><title>버전별 React 컴포넌트</title><style>${css}\nbody{margin:0;background:#fff;color:#16382c;font:14px/1.6 system-ui,sans-serif}*{box-sizing:border-box}.version-example{padding:20px;min-width:0}button,input,select{font:inherit}output{display:block;margin-top:12px;overflow-wrap:anywhere}</style></head><body><div id="root"></div><script>${script}</script></body></html>`;
  const hash = sha256(html);
  const manifest = { schemaVersion: 1, catalog, renderer: { file: `versions-${hash}.html`, sha256: hash, bytes: Buffer.byteLength(html) } };
  if (manifest.renderer.bytes > 1_000_000 || Buffer.byteLength(JSON.stringify(manifest)) > 800_000) throw new Error('Version renderer exceeds client limits');
  if (write) {
    const dir = path.resolve(__dirname, '../public/portal-renderers/versions');
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, manifest.renderer.file), html);
    const temp = path.join(dir, `index.${process.pid}.tmp`);
    fs.writeFileSync(temp, JSON.stringify(manifest));
    fs.renameSync(temp, path.join(dir, 'index.json'));
  }
  return { html, manifest, inputs };
}
module.exports = { sourceCatalog, buildRenderer };
if (require.main === module) buildRenderer()
  .then(({ manifest }) => console.log(`Portal versions: ${manifest.catalog.components.length} implementations, ${manifest.renderer.bytes} bytes`))
  .catch(error => { console.error(error); process.exitCode = 1; });
