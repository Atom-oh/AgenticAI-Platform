const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { buildSync } = require('esbuild');
const root = path.resolve(__dirname, '../..');
const hash = value => createHash('sha256').update(value).digest('hex');
let prepared;
async function assets() {
  assert.ok(fs.existsSync(path.join(root, 'web/portal-versions/build.cjs')), 'exact-version renderer is missing');
  return prepared ||= require('../portal-versions/build.cjs').buildRenderer({ write: false });
}
function source(file) {
  const code = buildSync({ entryPoints: [path.join(root, 'web', file)], bundle: true, write: false, platform: 'node', format: 'cjs' }).outputFiles[0].text;
  const target = { exports: {} };
  new Function('module', 'exports', 'require', code)(target, target.exports, require);
  return target.exports;
}

test('every named seed version has compiled source, a distinct export and an exact API binding', async () => {
  const { manifest, inputs } = await assets();
  const expected = { Button: 3, Input: 3, Table: 2, Modal: 2, DatePicker: 2, Select: 2, Card: 2, Tabs: 2, Stepper: 1, FileUpload: 1, Badge: 1, Toast: 1 };
  const ids = Object.entries(expected).flatMap(([name, count]) => Array.from({ length: count }, (_, i) => `CMP-${name}-v${i + 1}`)).sort();
  assert.deepEqual(manifest.catalog.components.map(c => c.id).sort(), ids);
  assert.equal(new Set(manifest.catalog.components.map(c => c.entry)).size, 22);
  const api = JSON.parse(execFileSync('python3', ['-c',
    'import json,runpy;print(json.dumps(runpy.run_path("api/handlers/component_sources.py")["catalog"]()))'], { cwd: root }).toString());
  for (const c of manifest.catalog.components) {
    assert.equal(c.sourceHash, api[c.id].sourceHash);
    assert.ok(inputs.some(file => file.endsWith(`component-library/${c.entry}`)), c.entry);
    assert.ok(c.files.some(file => file.path === c.entry));
    for (const file of c.files) {
      assert.equal(file.content, fs.readFileSync(path.join(root, 'component-library', file.path), 'utf8'));
      assert.equal(file.sha256, hash(file.content));
    }
  }
});

test('version renderer has exact content and script hashes, with no external runtime imports', async () => {
  const { html, manifest } = await assets();
  assert.equal(manifest.renderer.sha256, hash(html));
  assert.equal(manifest.renderer.bytes, Buffer.byteLength(html));
  const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
  assert.ok(html.includes(`script-src 'sha256-${createHash('sha256').update(script).digest('base64')}'`));
  for (const directive of ["connect-src 'none'", "frame-src 'none'", "form-action 'none'", "default-src 'none'"]) assert.ok(html.includes(directive));
});

test('manifest validation rejects wrong version identities, source paths, duplicates and oversized contents', async () => {
  const { manifest } = await assets();
  const { validateManifest } = source('portal-versions/client.ts');
  assert.equal(validateManifest(manifest).catalog.components.length, 22);
  for (const edit of [
    m => { m.catalog.components[0].version = '99.0.0'; },
    m => { m.catalog.components[0].files[0].path = '../other.tsx'; },
    m => { m.catalog.components[0].files[0].content = 'x'.repeat(100_001); },
    m => { m.catalog.components.push(m.catalog.components[0]); },
    m => { m.renderer.file = 'https://external.invalid/renderer.html'; },
    m => { m.catalog.package = '@studio/approved-ui'; },
  ]) {
    const changed = structuredClone(manifest); edit(changed);
    assert.throws(() => validateManifest(changed));
  }
});

test('loader verifies source bytes and rejects source or renderer tampering', async () => {
  const { manifest, html } = await assets();
  const { createVersionLoader } = source('portal-versions/client.ts');
  for (const kind of ['source', 'html', 'valid']) {
    const changed = structuredClone(manifest);
    if (kind === 'source') changed.catalog.components[0].files[0].content += '\n// changed';
    const fetcher = async url => new Response(url.endsWith('.json') ? JSON.stringify(changed) : html + (kind === 'html' ? ' ' : ''), {
      headers: { 'content-type': url.endsWith('.json') ? 'application/json' : 'text/html' },
    });
    const loader = createVersionLoader({ fetcher, origin: 'https://portal.test' });
    if (kind === 'valid') assert.equal((await loader.document()).catalog.components.length, 22);
    else await assert.rejects(loader.document());
  }
});

test('exact API identity must match source package, version, export and hash', async () => {
  const { manifest } = await assets();
  const { matchBinding } = source('portal-versions/client.ts');
  const c = manifest.catalog.components.find(c => c.id === 'CMP-Select-v2');
  const binding = { ...c, package: manifest.catalog.package, kind: 'react-component' };
  assert.equal(matchBinding(manifest.catalog, binding).id, c.id);
  for (const field of ['id', 'version', 'package', 'exportName', 'sourceHash', 'name']) {
    assert.throws(() => matchBinding(manifest.catalog, { ...binding, [field]: 'wrong' }));
  }
});

test('explicit retry refreshes a cached deployment snapshot without mutating the old document', async () => {
  const { manifest, html } = await assets();
  const { createVersionLoader } = source('portal-versions/client.ts');
  let currentHtml = html, calls = 0;
  const fetcher = async url => {
    calls++;
    const m = structuredClone(manifest);
    m.renderer = { sha256: hash(currentHtml), file: `versions-${hash(currentHtml)}.html`, bytes: Buffer.byteLength(currentHtml) };
    return new Response(url.endsWith('.json') ? JSON.stringify(m) : currentHtml, {
      headers: { 'content-type': url.endsWith('.json') ? 'application/json' : 'text/html' },
    });
  };
  const loader = createVersionLoader({ fetcher, origin: 'https://portal.test' });
  const first = await loader.document();
  currentHtml += '\n';
  assert.equal(await loader.document(), first);
  assert.equal(typeof loader.reset, 'function', 'Retry must be able to discard a stale deployment snapshot');
  loader.reset();
  const second = await loader.document();
  assert.equal(first.html, html);
  assert.equal(second.html, html + '\n');
  assert.equal(calls, 4);
});
