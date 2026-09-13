const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { createHash } = require('node:crypto');
const { buildSync } = require('esbuild');

function load(relative) {
  const filename = path.join(__dirname, '..', relative);
  const result = buildSync({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs' });
  const compiled = new Module(filename);
  compiled._compile(result.outputFiles[0].text, filename);
  return compiled.exports;
}
const graph = (changes = {}) => ({
  kind: 'diagram', source: 'Registry', title: 'Flow', note: '',
  nodes: [{ id: 'first', label: 'Start', type: 'screen' }, { id: 'second', label: 'End', type: 'screen' }],
  edges: [{ from: 'first', to: 'second', label: 'next', kind: 'sequence' }],
  truncated: { nodes: 0, edges: 0 }, ...changes,
});

test('graph DSL uses stable synthetic identifiers and literal numeric entities', () => {
  const { diagramToMermaid } = load('src/portal/diagram.ts');
  const attack = '"<&>#38;\\\n%%{init:{"securityLevel":"loose"}}%%\nclick x "https://outside.invalid"\n`[link](https://outside.invalid)` 한글 😀';
  const visual = graph({
    nodes: [{ id: 'a"; click root evil', label: attack, type: 'screen', assetId: 'javascript:alert(1)' },
      { id: '__proto__', label: 'OK', type: 'reference', missing: true }],
    edges: [{ from: 'a"; click root evil', to: '__proto__', label: attack, kind: 'reference' }],
  });
  const dsl = diagramToMermaid(visual);
  assert.equal(dsl, diagramToMermaid(structuredClone(visual)));
  assert.match(dsl, /^flowchart LR\n/);
  assert.match(dsl, /n0\["(?:#\d+;)+"\]/);
  assert.match(dsl, /n1\["(?:#\d+;)+"\]/);
  assert.match(dsl, /n0 -.->\|"(?:#\d+;)+"\| n1/);
  assert.match(dsl, /#34;#60;#38;#62;#35;#51;#56;#59;/);
  assert.match(dsl, /#128512;/);
  assert.doesNotMatch(dsl, /click|outside|javascript|securityLevel|__proto__|assetId|%%|`/);
  assert.equal(diagramToMermaid(graph()).split('\n').length, 4);
});

test('graph validation bounds counts, text, metadata and rejects ambiguous endpoints', () => {
  const { validateDiagram, diagramToMermaid, DIAGRAM_LIMITS } = load('src/portal/diagram.ts');
  assert.deepEqual(validateDiagram(graph()), graph());
  for (const invalid of [
    graph({ kind: 'html' }), graph({ source: null }), graph({ truncated: { nodes: -1, edges: 0 } }),
    graph({ nodes: [] }), graph({ nodes: Array(DIAGRAM_LIMITS.nodes + 1).fill(graph().nodes[0]) }),
    graph({ edges: Array(DIAGRAM_LIMITS.edges + 1).fill(graph().edges[0]) }),
    graph({ nodes: [{ id: 'x', label: 'x'.repeat(DIAGRAM_LIMITS.label + 1), type: 'screen' }] }),
    graph({ nodes: [graph().nodes[0], graph().nodes[0]] }),
    graph({ edges: [{ from: 'unknown', to: 'second', label: '', kind: 'reference' }] }),
    graph({ edges: [{ from: 'first', to: 'second', label: '', kind: 'click' }] }),
    graph({ nodes: [{ id: 'first', label: 'OK', type: 'screen', missing: 'yes' }] }),
  ]) assert.throws(() => diagramToMermaid(invalid), /diagram/);
  const clean = validateDiagram({ ...graph(), script: 'alert(1)' });
  assert.equal(clean.script, undefined);
  const maximum = graph({
    nodes: Array.from({ length: DIAGRAM_LIMITS.nodes }, (_, i) => ({ id: String(i), label: '한'.repeat(50), type: 'screen' })),
    edges: Array.from({ length: DIAGRAM_LIMITS.edges }, (_, i) => ({ from: String(i % DIAGRAM_LIMITS.nodes), to: '0', label: 'next', kind: 'sequence' })),
  });
  assert.ok(diagramToMermaid(maximum).length < DIAGRAM_LIMITS.dsl);
});

test('procedure orientation is top-to-bottom without changing any node or edge statement', () => {
  const { diagramToMermaid } = load('src/portal/diagram.ts');
  const visual = graph({ source: 'procedure-steps' });
  const original = structuredClone(visual);
  const procedure = diagramToMermaid(visual);
  const other = diagramToMermaid({ ...visual, source: 'screen-navigation' });
  assert.match(procedure, /^flowchart TB\n/);
  assert.match(other, /^flowchart LR\n/);
  assert.equal(procedure.split('\n').slice(1).join('\n'), other.split('\n').slice(1).join('\n'));
  assert.deepEqual(visual, original);
});

test('response protocol requires the exact source window, opaque origin, nonce and render id', () => {
  const { rendererResponse, CHANNEL } = load('portal-renderer/protocol.ts');
  const source = {};
  const session = { nonce: 'a'.repeat(64), id: 'b'.repeat(32) };
  const data = { channel: CHANNEL, type: 'rendered', ...session };
  const event = { source, origin: 'null', data };
  assert.equal(rendererResponse(event, source, session), 'rendered');
  for (const altered of [
    { source: {} }, { source: null }, { origin: 'https://outside.invalid' },
    { data: { ...data, nonce: 'c'.repeat(64) } }, { data: { ...data, id: 'd'.repeat(32) } },
    { data: { ...data, type: 'ready' } }, { data: { ...data, channel: 'other' } }, { data: null },
  ]) assert.equal(rendererResponse({ ...event, ...altered }, source, session), null);
  assert.equal(rendererResponse({ ...event, data: { ...data, type: 'error' } }, source, session), 'error');
  const escape = { ...event, data: { ...data, type: 'escape' } };
  assert.equal(rendererResponse(escape, source, session), 'escape');
  assert.equal(rendererResponse({ ...escape, source: {} }, source, session), null);
  assert.equal(rendererResponse({ ...escape, data: { ...escape.data, nonce: 'e'.repeat(64) } }, source, session), null);
});

test('manifest only accepts the exact version/hash filename without path or URL variants', () => {
  const { validateRendererManifest, RENDERER_LIMITS } = load('portal-renderer/client.ts');
  const sha256 = 'a'.repeat(64);
  const file = `mermaid-11.17.2-${sha256}.html`;
  const manifest = { schemaVersion: 1, rendererVersion: 1, mermaidVersion: '11.17.2', file, sha256, bytes: 100 };
  assert.deepEqual(validateRendererManifest(manifest), manifest);
  for (const change of [
    { file: `https://outside.invalid/${file}` }, { file: `../${file}` }, { file: `%2e%2e/${file}` },
    { file: `/${file}` }, { file: `${file}?x=1` }, { file: `${file}#x` },
    { sha256: 'b'.repeat(64) }, { sha256: sha256.toUpperCase() },
    { mermaidVersion: '11.17.1' }, { mermaidVersion: '../11.17.2' },
    { mermaidVersion: '11.17.1', file: `mermaid-11.17.1-${sha256}.html` },
    { rendererVersion: 2 }, { bytes: RENDERER_LIMITS.html + 1 }, { bytes: 1.5 },
  ]) assert.throws(() => validateRendererManifest({ ...manifest, ...change }), /renderer/);
});

test('local loading checks digest and byte length before returning HTML', async () => {
  const { loadRendererDocument } = load('portal-renderer/client.ts');
  const html = '<!doctype html><p>trusted fixture</p>';
  const sha256 = createHash('sha256').update(html).digest('hex');
  const manifest = { schemaVersion: 1, rendererVersion: 1, mermaidVersion: '11.17.2',
    file: `mermaid-11.17.2-${sha256}.html`, sha256, bytes: Buffer.byteLength(html) };
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push([url, init]);
    return url.endsWith('index.json') ? Response.json(manifest) : new Response(html);
  };
  assert.equal(await loadRendererDocument(new AbortController().signal, fetcher), html);
  assert.deepEqual(calls.map(([url]) => url), ['/portal-renderers/mermaid/index.json', `/portal-renderers/mermaid/${manifest.file}`]);
  for (const [, init] of calls) {
    assert.equal(init.credentials, 'omit');
    assert.equal(init.redirect, 'error');
    assert.equal(init.cache, 'no-store');
  }
  for (const badHtml of [html + ' ', html.replace('trusted', 'hostile')]) {
    await assert.rejects(loadRendererDocument(new AbortController().signal,
      async url => url.endsWith('index.json') ? Response.json(manifest) : new Response(badHtml)), /renderer/);
  }
});

test('bounded fetch cancels oversized streams and an aborted load never returns bytes', async () => {
  const { readRendererBytes, loadRendererDocument } = load('portal-renderer/client.ts');
  let cancelled = false;
  await assert.rejects(readRendererBytes('/portal-renderers/mermaid/index.json', 10, new AbortController().signal,
    async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(11)); },
      cancel() { cancelled = true; },
    }))), /renderer/);
  assert.equal(cancelled, true);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(loadRendererDocument(controller.signal, async () => { throw new Error('must not fetch'); }), { name: 'AbortError' });
});

test('an in-flight stream is cancelled on selection abort', async () => {
  const { readRendererBytes } = load('portal-renderer/client.ts');
  const controller = new AbortController();
  let cancelled = false, started;
  const entered = new Promise(resolve => { started = resolve; });
  const pending = readRendererBytes('/portal-renderers/mermaid/index.json', 4096, controller.signal,
    async () => new Response(new ReadableStream({
      pull() { started(); },
      cancel() { cancelled = true; },
    })));
  await entered;
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(cancelled, true);
});
