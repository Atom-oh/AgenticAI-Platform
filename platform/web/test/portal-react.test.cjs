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
