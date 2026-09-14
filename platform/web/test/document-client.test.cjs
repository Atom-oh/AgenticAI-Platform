const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { buildSync } = require('esbuild');
const { createHash, webcrypto } = require('node:crypto');
globalThis.crypto ||= webcrypto;

function load() {
  const filename = path.join(__dirname, '../src/documents/client.ts');
  const built = buildSync({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs' });
  const compiled = new Module(filename);
  compiled._compile(built.outputFiles[0].text, filename);
  return compiled.exports;
}

test('evidence URLs bind collection, exact revision, paragraph and hash without credentials', () => {
  const { sourceHref } = load();
  const source = { documentId: 'd-abc', revisionId: 'd-abc--r000001', paragraphId: 'p000003', textHash: 'a'.repeat(64) };
  const href = sourceHref(source, { projectId: 'team-1', analysisId: 'ana-1' });
  assert(href.startsWith('#/documents?'));
  const params = new URLSearchParams(href.split('?')[1]);
  assert.equal(params.get('revisionId'), source.revisionId);
  assert.equal(params.get('paragraph'), 'p000003');
  assert.equal(params.get('textHash'), source.textHash);
  assert.equal(params.get('projectId'), 'team-1');
  assert.equal(params.get('analysisId'), 'ana-1');
  assert(!/token|authorization|signature/i.test(href));
  for (const changed of [{ documentId: 'https://other.invalid' }, { revisionId: 'd-other--r000001' },
    { textHash: '' }, { paragraphId: 'javascript:alert(1)' }]) {
    assert.throws(() => sourceHref({ ...source, ...changed }));
  }
});

test('retry cannot attach the same file to changed document metadata', async () => {
  const { uploadDocument } = load();
  const calls = []; let fail = true, checkpoint;
  const client = {
    post: async (url, body) => {
      calls.push({ url, body });
      return url.endsWith('/complete') ? { document: { id: 'd-1' }, revision: { id: 'd-1--r1' }, job: { id: 'j', status: 'queued' } }
        : { document: { id: 'd-1' }, revision: { id: 'd-1--r1' }, chunkBytes: 2 };
    },
    part: async (url, bytes) => {
      calls.push({ url, size: bytes.size });
      if (url.endsWith('/1') && fail) { fail = false; throw Error('temporary upload failure'); }
    },
  };
  const file = new File(['abc'], 'rule.txt');
  const input = { requestId: 'upload-1', title: '규정', kind: 'regulation', graphRef: 'REG-1' };
  const config = { maxFileBytes: 50, extensions: ['txt'] };
  await assert.rejects(uploadDocument(client, file, input, config, { onCheckpoint: v => { checkpoint = v; } }));
  assert.equal(checkpoint.nextPart, 1);
  const before = calls.length;
  await assert.rejects(uploadDocument(client, file, { ...input, graphRef: 'REG-2' }, config, { checkpoint }), /조건/);
  assert.equal(calls.length, before);
  await uploadDocument(client, file, input, config, { checkpoint });
  assert.equal(calls.filter(c => c.url === '/documents').length, 1);
  assert(calls.at(-1).url.endsWith('/complete'));
});

test('sample originals cannot be relabelled by client revision uploads', async () => {
  const { uploadDocument } = load();
  let calls = 0;
  await assert.rejects(uploadDocument({ post: async () => { calls++; } }, new File(['x'], 'source.txt'),
    { requestId: 'r', title: 'x', kind: 'reference', document: { id: 'd-1', provenance: 'synthetic_sample' } },
    { maxFileBytes: 50, extensions: ['txt'] }), /합성 예제/);
  assert.equal(calls, 0);
});

test('download verifies selected revision identity as well as transport integrity', async () => {
  const { downloadOriginal } = load();
  const bytes = new TextEncoder().encode('source bytes');
  const hash = createHash('sha256').update(bytes).digest('hex');
  const client = { get: async url => {
    assert(url.startsWith('/documents/d-1/revisions/d-1--r1/blob?kind=original&offset='));
    return { response: new Response(bytes, { headers: {
      'X-Total-Size': String(bytes.length), 'X-Chunk-Size': String(bytes.length),
      'X-SHA256': hash, 'X-Content-Type': 'application/octet-stream',
    } }), bytes: bytes.buffer };
  } };
  const revision = { id: 'd-1--r1', sha256: hash, size: bytes.length };
  assert.equal(await (await downloadOriginal(client, 'd-1', revision)).text(), 'source bytes');
  await assert.rejects(downloadOriginal(client, 'd-1', { ...revision, sha256: 'a'.repeat(64) }), /버전/);
});
