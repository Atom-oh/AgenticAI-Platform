const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { createHash } = require('node:crypto');
const { buildSync } = require('esbuild');
const web = path.resolve(__dirname, '..');
const names = ['Screen', 'Stack', 'Grid', 'Inline', 'Panel', 'Text', 'Button', 'Input',
  'Checkbox', 'Select', 'RadioGroup', 'Alert', 'Stepper', 'Summary', 'AssetImage'];
let rendered;
const renderer = () => rendered ||= require('../portal-react/build.cjs').buildRenderer({ write: false });
function source(file) {
  const built = buildSync({ entryPoints: [path.join(web, file)], bundle: true, write: false, platform: 'node', format: 'cjs' });
  const loaded = new Module(path.join(web, '__react-test.cjs'), module);
  loaded.filename = path.join(web, '__react-test.cjs');
  loaded.paths = Module._nodeModulePaths(web);
  loaded._compile(built.outputFiles[0].text, loaded.filename);
  return loaded.exports;
}

test('renderer bundles the real kit and catalog with exact HTML/script hashes and restrictive CSP', async () => {
  const { html, manifest, inputs } = await renderer();
  assert.deepEqual(manifest.catalog, require('../../react-kit/manifest.cjs').catalog());
  assert.deepEqual(manifest.catalog.components.map(c => c.name), names);
  assert.equal(manifest.schemaVersion, 1);
  assert.ok(inputs.some(file => file.endsWith('react-kit/ui/index.tsx')));
  assert.ok(inputs.some(file => file.endsWith('react-kit/ui/tokens.css')));
  assert.equal(manifest.renderer.sha256, createHash('sha256').update(html).digest('hex'));
  assert.equal(manifest.renderer.file, `react-${manifest.renderer.sha256}.html`);
  assert.equal(manifest.renderer.bytes, Buffer.byteLength(html));
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 1);
  assert.ok(html.includes(`script-src 'sha256-${createHash('sha256').update(scripts[0][1]).digest('base64')}'`));
  for (const directive of ['default-src', 'connect-src', 'frame-src', 'object-src', 'form-action', 'base-uri', 'worker-src']) {
    assert.ok(html.includes(`${directive} 'none'`));
  }
  assert.ok(html.includes("style-src 'unsafe-inline'"));
  assert.ok(html.includes('img-src data:'));
  assert.doesNotMatch(html, /<script[^>]+src=|<link\b|unsafe-eval|script-src 'unsafe-inline'/i);
});

test('manifest rejects traversal, external renderer URLs, unbounded metadata, wrong hashes and unknown components', async () => {
  const { validateManifest } = source('portal-react/client.ts');
  const { manifest } = await renderer();
  assert.equal(validateManifest(manifest).catalog.hash, manifest.catalog.hash);
  for (const mutate of [
    m => { m.schemaVersion = 2; },
    m => { m.renderer.file = '../mermaid/other.html'; },
    m => { m.renderer.file = 'https://external.invalid/react.html'; },
    m => { m.renderer.sha256 = '0'.repeat(64); },
    m => { m.renderer.bytes = 2_000_000; },
    m => { m.sources.file = '../private.json'; },
    m => { m.sources.archive.file = 'https://external.invalid/source.zip'; },
    m => { m.sources.archive.bytes = 1_000_000; },
    m => { delete m.sources; },
    m => { m.catalog.hash = 'unknown'; },
    m => { m.catalog.components[0].name = '__proto__'; },
    m => { m.catalog.components[0].description = 'x'.repeat(4000); },
    m => { m.catalog.components[0].props.children = { source: 'alert(1)' }; },
    m => { m.catalog.components.push(m.catalog.components[0]); },
  ]) {
    const bad = structuredClone(manifest);
    mutate(bad);
    assert.throws(() => validateManifest(bad));
  }
});

test('source ZIP preserves the exact complete kit and can compile in a consumer project', async t => {
  const fs = require('node:fs/promises');
  const { inflateRawSync } = require('node:zlib');
  const { build } = require('esbuild');
  const { manifest, sources, archive } = await renderer();
  const data = JSON.parse(sources);
  assert.equal(data.catalogHash, manifest.catalog.hash);
  assert.equal(createHash('sha256').update(archive).digest('hex'), manifest.sources.archive.sha256);
  const files = {};
  for (let offset = 0; archive.readUInt32LE(offset) === 0x04034b50;) {
    const size = archive.readUInt32LE(offset + 18), nameLength = archive.readUInt16LE(offset + 26);
    const start = offset + 30 + nameLength + archive.readUInt16LE(offset + 28);
    const name = archive.subarray(offset + 30, offset + 30 + nameLength).toString();
    files[name] = inflateRawSync(archive.subarray(start, start + size));
    offset = start + size;
  }
  assert.deepEqual(Object.keys(files).sort(), data.files.map(f => f.path).sort());
  for (const file of data.files) assert.equal(files[file.path].toString(), file.content);
  for (const file of manifest.catalog.files) {
    assert.equal(createHash('sha256').update(files[file.path]).digest('hex'), file.sha256);
    assert.deepEqual(files[file.path], await fs.readFile(path.join(web, '../react-kit', file.path)));
  }
  assert.equal(JSON.parse(files['package.json']).exports['.'], './ui/index.tsx');
  const temp = await fs.mkdtemp(path.join(require('node:os').tmpdir(), 'portal-source-test-'));
  t.after(() => fs.rm(temp, { recursive: true, force: true }));
  for (const [name, contents] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(temp, name)), { recursive: true });
    await fs.writeFile(path.join(temp, name), contents);
  }
  await fs.symlink(path.join(web, '../react-kit/node_modules'), path.join(temp, 'node_modules'), 'dir');
  const app = `import {Button} from './ui'; export default function Example(){return <Button label="확인" onClick={()=>{}}/>;}`;
  await fs.writeFile(path.join(temp, 'example.tsx'), app);
  const ts = require('typescript');
  const program = ts.createProgram([path.join(temp, 'example.tsx')], {
    strict: true, noEmit: true, skipLibCheck: true, jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, target: ts.ScriptTarget.ES2022,
  });
  assert.deepEqual(ts.getPreEmitDiagnostics(program).map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')), []);
  const bundle = await build({ absWorkingDir: temp, entryPoints: ['example.tsx'], outdir: path.join(temp, 'dist'),
    bundle: true, write: false, jsx: 'automatic' });
  assert.ok(bundle.outputFiles.some(file => file.path.endsWith('.css')));
  assert.ok(bundle.outputFiles.some(file => file.text.includes('data-studio-component')));
});

test('source downloads pin displayed revision, reject altered bytes and retry cleanly', async () => {
  const { createReactLoader } = source('portal-react/client.ts');
  const data = await renderer();
  const calls = []; let tamper = true;
  const loader = createReactLoader({ origin: 'https://portal.test', fetcher: async url => {
    calls.push(url);
    if (url.endsWith('index.json')) return new Response(JSON.stringify(data.manifest), { headers: { 'content-type': 'application/json' } });
    if (url.endsWith('.html')) return new Response(data.html, { headers: { 'content-type': 'text/html' } });
    if (url.endsWith('.zip')) return new Response(tamper ? Buffer.from('wrong archive') : data.archive, { headers: { 'content-type': 'application/zip' } });
    return new Response(tamper ? Buffer.from('{}') : data.sources, { headers: { 'content-type': 'application/json' } });
  } });
  const catalog = await loader.catalog();
  assert.equal(calls.length, 2, 'Sources are not fetched while browsing metadata');
  await assert.rejects(loader.archive('0'.repeat(64)), /catalog-mismatch/);
  await assert.rejects(loader.sources(catalog.hash), /integrity/);
  await assert.rejects(loader.archive(catalog.hash), /integrity/);
  tamper = false;
  const files = await loader.sources(catalog.hash);
  assert.ok(files.find(file => file.path === 'ui/index.tsx').content.includes('export function Button'));
  assert.deepEqual(Buffer.from(await loader.archive(catalog.hash)), data.archive);
});

test('source file hashes and catalog closure must agree even in a rehashed source manifest', async () => {
  const { createReactLoader } = source('portal-react/client.ts');
  const data = await renderer();
  for (const mutate of [
    f => { f[0].path = '../private.ts'; },
    f => { f[0].content = '<script>untrusted</script>'; },
    f => { f.push(f[0]); },
    f => { f.splice(f.findIndex(item => item.path === 'ui/internal.ts'), 1); },
    f => { f.find(item => item.path === 'ui/index.tsx').content += '\n// changed';
      const item = f.find(item => item.path === 'ui/index.tsx'); item.sha256 = createHash('sha256').update(item.content).digest('hex'); },
  ]) {
    const changed = JSON.parse(data.sources); mutate(changed.files);
    const bytes = Buffer.from(JSON.stringify(changed)), manifest = structuredClone(data.manifest);
    manifest.sources.sha256 = createHash('sha256').update(bytes).digest('hex');
    manifest.sources.file = `source-${manifest.sources.sha256}.json`; manifest.sources.bytes = bytes.length;
    const loader = createReactLoader({ origin: 'https://portal.test', fetcher: async url =>
      url.endsWith('index.json') ? new Response(JSON.stringify(manifest), { headers: { 'content-type': 'application/json' } }) :
        url.endsWith('.html') ? new Response(data.html, { headers: { 'content-type': 'text/html' } }) :
          new Response(bytes, { headers: { 'content-type': 'application/json' } }) });
    await assert.rejects(loader.sources(manifest.catalog.hash));
  }
});
test('shared loader fetches bounded same-origin bytes once and reuses the verified document', async () => {
  const { createReactLoader } = source('portal-react/client.ts');
  const { manifest, html } = await renderer();
  const calls = [];
  const loader = createReactLoader({ origin: 'https://portal.test', fetcher: async (url, options) => {
    calls.push(url);
    assert.equal(new URL(url).origin, 'https://portal.test');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    return url.endsWith('index.json')
      ? new Response(JSON.stringify(manifest), { headers: { 'content-type': 'application/json' } })
      : new Response(html, { headers: { 'content-type': 'text/html' } });
  } });
  const [a, b, catalog] = await Promise.all([loader.document(), loader.document(), loader.catalog()]);
  assert.equal(a.html, html);
  assert.equal(a, b);
  assert.equal(catalog.hash, manifest.catalog.hash);
  await loader.document();
  assert.deepEqual(calls, ['https://portal.test/portal-renderers/react/index.json',
    `https://portal.test/portal-renderers/react/${manifest.renderer.file}`]);
  assert.ok(Object.isFrozen(catalog) && Object.isFrozen(catalog.components));
});

test('loader rejects tampering and can retry without poisoning the shared promise', async () => {
  const { createReactLoader } = source('portal-react/client.ts');
  const { manifest, html } = await renderer();
  let tamper = true;
  const loader = createReactLoader({ origin: 'https://portal.test', fetcher: async url =>
    url.endsWith('index.json')
      ? new Response(JSON.stringify(manifest), { headers: { 'content-type': 'application/json' } })
      : new Response(tamper ? html.replace('<title>', '<TITLE>') : html, { headers: { 'content-type': 'text/html' } }),
  });
  await assert.rejects(loader.document(), /integrity/);
  tamper = false;
  assert.equal((await loader.document()).html, html);
});

test('catalog identity is exposed only with its verified document and rolls over atomically after failure', async () => {
  const { createReactLoader } = source('portal-react/client.ts');
  const { manifest, html } = await renderer();
  const previous = structuredClone(manifest);
  previous.catalog.version = '0.9.0';
  previous.catalog.hash = 'a'.repeat(64);
  let fresh = false;
  const loader = createReactLoader({ origin: 'https://portal.test', fetcher: async url =>
    url.endsWith('index.json')
      ? new Response(JSON.stringify(fresh ? manifest : previous), { headers: { 'content-type': 'application/json' } })
      : new Response(fresh ? html : html.replace('<title>', '<TITLE>'), { headers: { 'content-type': 'text/html' } }),
  });
  await assert.rejects(loader.catalog(), /integrity/);
  fresh = true;
  const catalog = await loader.catalog();
  const document = await loader.document();
  assert.equal(catalog, document.catalog);
  assert.equal(catalog.version, manifest.catalog.version);
  assert.equal(catalog.hash, manifest.catalog.hash);
});

test('fetch bounds apply to streaming bodies and redirects without trusting Content-Length', async () => {
  const { createReactLoader } = source('portal-react/client.ts');
  for (const response of [
    new Response('x'.repeat(100_000), { headers: { 'content-type': 'application/json' } }),
    new Response('{}', { headers: { 'content-type': 'application/json', 'content-length': '900000000' } }),
    new Response('{}', { headers: { 'content-type': 'text/html' } }),
    Object.defineProperty(new Response('{}', { headers: { 'content-type': 'application/json' } }), 'redirected', { value: true }),
  ]) {
    const loader = createReactLoader({ origin: 'https://portal.test', fetcher: async () => response });
    await assert.rejects(loader.catalog());
  }
});

test('opaque frame receipts bind source, origin, per-frame nonce, request ID and catalog hash', () => {
  const { newSession, responseType, CHANNEL } = source('portal-react/protocol.ts');
  const session = newSession();
  const frame = {};
  const hash = 'a'.repeat(64);
  const data = { channel: CHANNEL, type: 'rendered', ...session, catalogHash: hash };
  const event = { source: frame, origin: 'null', data };
  assert.equal(responseType(event, frame, session, hash), 'rendered');
  assert.notEqual(newSession().nonce, session.nonce);
  for (const bad of [
    { ...event, source: {} }, { ...event, origin: 'https://portal.test' },
    { ...event, data: { ...data, nonce: '0'.repeat(64) } },
    { ...event, data: { ...data, id: '0'.repeat(32) } },
    { ...event, data: { ...data, catalogHash: 'b'.repeat(64) } },
    { ...event, data: { ...data, channel: 'portal-diagram/v1' } },
  ]) assert.equal(responseType(bad, frame, session, hash), null);
});

test('usage snippets are static text for all actual exports and reject unknown metadata', () => {
  const { usageSnippet } = source('src/portal/reactCatalog.ts');
  for (const name of names) assert.match(usageSnippet(name), new RegExp(`<${name}\\b`));
  assert.throws(() => usageSnippet('@atom/ui/button'));
  assert.throws(() => usageSnippet('__proto__'));
  assert.throws(() => usageSnippet('<script>alert(1)</script>'));
});
