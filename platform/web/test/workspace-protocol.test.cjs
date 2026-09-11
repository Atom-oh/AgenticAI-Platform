const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { buildSync } = require('esbuild');
const { webcrypto } = require('node:crypto');
globalThis.crypto ||= webcrypto;

function load(name) {
  const filename = path.join(__dirname, '../src/workspace/', name + '.ts');
  const built = buildSync({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs' });
  const compiled = new Module(filename);
  compiled._compile(built.outputFiles[0].text, filename);
  return compiled.exports;
}

test('HTTP requests use only the private same-origin API and access token', async () => {
  const { createWorkspaceClient } = load('client');
  const calls = [];
  const client = createWorkspaceClient({ token: () => 'offline-access', fetcher: async (url, init) => {
    calls.push({ url, init });
    return Response.json({ assets: [] });
  } });
  await client.get('/assets');
  assert.equal(calls[0].url, '/studio-api/assets');
  assert.equal(calls[0].init.headers.get('Authorization'), 'Bearer offline-access');
  assert.equal(calls[0].init.redirect, 'error');
  await assert.rejects(client.get('https://example.invalid/assets'));
  assert.equal(calls.length, 1);
});

test('missing authentication and HTTP failures remain explicit', async () => {
  const { createWorkspaceClient } = load('client');
  const client = createWorkspaceClient({ token: () => null, fetcher: async () => { throw Error('must not fetch'); } });
  await assert.rejects(client.get('/assets'), /로그인/);
  const conflict = createWorkspaceClient({ token: () => 'fixture', fetcher: async () =>
    Response.json({ error: '수정된 버전을 다시 확인하세요.', code: 'version_conflict' }, { status: 409 }) });
  await assert.rejects(conflict.post('/contracts/c/approve', { version: 1 }), error =>
    error.status === 409 && error.code === 'version_conflict');
});

test('common API codes produce actionable Korean errors while retaining status and code', async () => {
  const { createWorkspaceClient } = load('client');
  const cases = [
    ['unauthorized', 401, /로그인/],
    ['conflict', 409, /최신.*버전/],
    ['too-large', 413, /크기.*줄이|크기.*줄여/],
    ['unsupported-extension', 400, /지원.*파일 형식/],
    ['parts-missing', 409, /파일.*전송|전송.*파일/],
    ['asset-not-ready', 409, /파일.*보관|보관.*파일/],
    ['contract-unresolved', 409, /확인이 필요한/],
    ['contract-not-approved', 409, /규칙.*승인/],
    ['approval-evidence-required', 409, /라운드.*근거|근거.*라운드/],
    ['unavailable', 503, /진행 상태/],
  ];
  for (const [code, status, expected] of cases) {
    const client = createWorkspaceClient({ token: () => 'fixture',
      fetcher: async () => Response.json({ error: 'English service detail', code }, { status }) });
    await assert.rejects(client.get('/assets'), error => {
      assert.equal(error.status, status); assert.equal(error.code, code);
      assert.match(error.message, expected);
      assert(!error.message.includes('English service detail'));
      return true;
    });
  }
});

test('actionable Korean API messages take precedence over both code and HTTP fallback', async () => {
  const { createWorkspaceClient } = load('client');
  for (const [code, status, message] of [
    ['unauthorized', 401, '접속 권한이 변경되었습니다. 담당자에게 권한을 확인한 뒤 다시 로그인하세요.'],
    ['conflict', 409, '다른 창에서 규칙이 수정되었습니다. 버전 4를 다시 불러오세요.'],
    ['unavailable', 503, '시안 생성은 계속 진행 중입니다. 진행 상태를 먼저 확인하세요.'],
  ]) {
    const client = createWorkspaceClient({ token: () => 'fixture',
      fetcher: async () => Response.json({ error: message, code }, { status }) });
    await assert.rejects(client.get('/assets'), error => error.message === message && error.code === code);
  }
});

test('upload retry resumes the exact failed part without creating another asset', async () => {
  const { createWorkspaceClient, uploadFile } = load('client');
  const file = new File(['abcdefghij'], 'guide.txt', { type: 'text/plain' });
  const paths = []; let fail = true; let checkpoint;
  const client = createWorkspaceClient({ token: () => 'fixture', fetcher: async (url, init) => {
    paths.push(url);
    if (url === '/studio-api/assets') return Response.json({ asset: { id: 'a' }, chunkBytes: 4 });
    if (url.endsWith('/parts/1') && fail) { fail = false; return Response.json({ error: 'retry' }, { status: 503 }); }
    if (url.endsWith('/complete')) return Response.json({ asset: { id: 'a' }, job: { id: 'j', status: 'queued' } });
    assert(init.body instanceof Blob); return Response.json({ ok: true });
  } });
  const options = { purpose: 'guide', maxFileBytes: 50, extensions: ['txt'], onCheckpoint: value => { checkpoint = value; } };
  await assert.rejects(uploadFile(client, file, options));
  assert.equal(checkpoint.nextPart, 1);
  const result = await uploadFile(client, file, { ...options, checkpoint });
  assert.equal(result.job.id, 'j');
  assert.equal(paths.filter(p => p === '/studio-api/assets').length, 1);
  assert.deepEqual(paths.filter(p => p.includes('/parts/')), [
    '/studio-api/assets/a/parts/0', '/studio-api/assets/a/parts/1',
    '/studio-api/assets/a/parts/1', '/studio-api/assets/a/parts/2',
  ]);
});

test('private blob assembly verifies stable size/hash and rejects partial bytes', async () => {
  const { createWorkspaceClient, readPrivateBlob, sha256 } = load('client');
  const bytes = new TextEncoder().encode('파일🔒');
  const hash = await sha256(bytes.buffer);
  const make = corrupt => createWorkspaceClient({ token: () => 'fixture', fetcher: async url => {
    const offset = Number(new URL(url, 'http://offline.test').searchParams.get('offset'));
    const chunk = bytes.slice(offset, offset + 3);
    return new Response(chunk, { headers: {
      'X-Total-Size': String(bytes.length), 'X-Chunk-Size': String(chunk.length),
      'X-Content-Type': 'text/plain', 'X-SHA256': corrupt && offset ? '0'.repeat(64) : hash,
    } });
  } });
  assert.equal(await (await readPrivateBlob(make(false), '/assets/a/blob?kind=original')).blob.text(), '파일🔒');
  await assert.rejects(readPrivateBlob(make(true), '/assets/a/blob?kind=original'), /변경|일치/);
});

test('polling stops on failure and cancellation without inventing a completed result', async () => {
  const { pollJob } = load('client');
  let calls = 0;
  const client = { get: async () => ({ job: { id: 'j', status: ++calls === 1 ? 'running' : 'failed', error: '검수 실패' } }) };
  await assert.rejects(pollJob(client, 'j', { interval: 0 }), /검수 실패/);
  assert.equal(calls, 2);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(pollJob(client, 'j', { signal: controller.signal, interval: 0 }), { name: 'AbortError' });
  assert.equal(calls, 2);
});

test('editable Korean rules require assertions and unresolved requirements block approval', () => {
  const { contractProblems, newRule, newStep, roundApprovable } = load('rules');
  const rule = newRule();
  const contract = { title: '금액 확인', brief: '가입', assetIds: ['a'], viewport: { width: 390, height: 844 }, rules: [rule], unresolved: [] };
  assert.equal(contractProblems(contract).length, 0);
  assert(contractProblems({ ...contract, rules: [{ ...rule, steps: [newStep('click')] }] }).length > 0);
  assert(contractProblems({ ...contract, unresolved: ['확인 필요'] }).length > 0);
  assert.equal(roundApprovable({ number: 1, passed: false, artifactSha256: 'a' }), false);
  const complete = { number: 2, passed: true, artifactSha256: 'b'.repeat(64), blockingFindings: [],
    hasHtml: true, hasReport: true, hasScreenshot: true };
  assert.equal(roundApprovable(complete), true);
  assert.equal(roundApprovable({ ...complete, hasHtml: false }), false);
  assert.equal(roundApprovable({ ...complete, hasReport: undefined }), false);
  assert.equal(roundApprovable({ ...complete, hasScreenshot: false }), false);
  assert.equal(roundApprovable({ number: 2, passed: true, artifactSha256: 'b'.repeat(64) }), false);
});

test('OCR states are explicit and are not treated as proof of AI image inclusion', () => {
  const { ocrLabel } = load('rules');
  assert.equal(ocrLabel('complete'), '문자 인식 완료');
  assert.equal(ocrLabel('partial'), '일부만 문자 인식');
  assert.equal(ocrLabel('failed'), '문자 인식 실패');
  assert.equal(ocrLabel('unavailable'), '문자 인식 사용 불가');
  assert.equal(ocrLabel(undefined), '문자 인식 미확인');
});

test('style assertions preserve allowed properties and reject unsupported or empty values', () => {
  const { contractProblems, newStep, newRule, editable, STYLE_PROPERTIES } = load('rules');
  const style = { ...newStep('expectStyle'), property: 'backgroundColor', value: '#008485' };
  const rule = { ...newRule(), steps: [style] };
  const contract = { title: '버튼 스타일 확인', brief: '', assetIds: [], viewport: { width: 390, height: 844 }, rules: [rule], unresolved: [] };
  assert.deepEqual(contractProblems(contract), []);
  assert.deepEqual(editable(contract).rules[0].steps[0], style);
  assert.equal(STYLE_PROPERTIES.fontSize, '글자 크기');
  assert.equal(Object.keys(STYLE_PROPERTIES).length, 15);
  for (const step of [{ ...style, property: 'opacity' }, { ...style, value: '' }, { ...style, property: undefined }]) {
    assert(contractProblems({ ...contract, rules: [{ ...rule, steps: [step] }] }).length > 0);
  }
  assert.deepEqual(contractProblems({ ...contract, rules: [{ ...rule, steps: [{ ...style, property: 'fontSize', value: '16px' }] }] }), []);
});

test('approved original-HTML selector bindings survive edits without sharing mutable state', () => {
  const { blankContract, editable, contractProblems } = load('rules');
  const contract = { ...blankContract(['html']), bindings: { amount: '#amount', next: 'button.next' } };
  const copy = editable(contract);
  assert.deepEqual(copy.bindings, contract.bindings);
  copy.bindings.amount = '[name="amount"]';
  assert.equal(contract.bindings.amount, '#amount');
  assert.deepEqual(contractProblems(copy), []);
  for (const bindings of [
    { amount: '' }, { amount: 'xpath=//input' }, { amount: 'input >> text=next' },
    { amount: 'javascript:alert(1)' }, { amount: 'input\nbutton' }, { amount: 'a'.repeat(301) },
  ]) assert(contractProblems({ ...contract, bindings }).length > 0);
});

test('original HTML inspection accepts only stored, unarchived HTML selected in the contract', () => {
  const { importedHtmlAssets } = load('rules');
  const assets = [
    { id: 'html', name: 'screen.HTML', uploadStatus: 'stored' },
    { id: 'htm', name: 'screen.htm', uploadStatus: 'stored' },
    { id: 'css', name: 'style.css', uploadStatus: 'stored' },
    { id: 'pending', name: 'pending.html', uploadStatus: 'processing' },
    { id: 'archived', name: 'archived.html', uploadStatus: 'stored', archived: true },
    { id: 'outside', name: 'outside.html', uploadStatus: 'stored' },
  ];
  assert.deepEqual(importedHtmlAssets(assets, { assetIds: ['html', 'htm', 'css', 'pending', 'archived'] }).map(asset => asset.id), ['html', 'htm']);
  assert.deepEqual(importedHtmlAssets(assets, null), []);
});

test('primary HTML and image intake recommends editable purposes without interpreting FIG', () => {
  const { suggestedPurpose } = load('rules');
  assert.equal(suggestedPurpose('screen.HTML'), 'prototype');
  assert.equal(suggestedPurpose('reference.PNG'), 'reference');
  assert.equal(suggestedPurpose('reference.jpg'), 'reference');
  assert.equal(suggestedPurpose('icon.svg'), 'component');
  assert.equal(suggestedPurpose('source.fig'), 'archive');
  assert.equal(suggestedPurpose('guide.pdf'), 'guide');
  assert.equal(suggestedPurpose('SKILL.md'), 'skill');
  assert.equal(suggestedPurpose('styles.CSS'), 'guide');
});

test('CSS companions remain unchanged guide data and require config-advertised support', async () => {
  const { createWorkspaceClient, uploadFile, sha256 } = load('client');
  const { suggestedPurpose } = load('rules');
  const source = 'body { color: #008485; font-size: 16px; }';
  const file = new File([source], 'styles.css', { type: 'text/css' });
  const calls = [];
  const client = createWorkspaceClient({ token: () => 'fixture', fetcher: async (url, init) => {
    calls.push({ url, init });
    if (url === '/studio-api/assets') return Response.json({ asset: { id: 'css' }, chunkBytes: 2097152 });
    if (url.endsWith('/complete')) return Response.json({ asset: { id: 'css' }, job: { id: 'finalize-css' } });
    return Response.json({ ok: true });
  } });
  const options = { purpose: suggestedPurpose(file.name), maxFileBytes: 1024, extensions: ['html'] };
  await assert.rejects(uploadFile(client, file, options), /지원/);
  assert.equal(calls.length, 0);
  await uploadFile(client, file, { ...options, extensions: ['html', 'css'] });
  const metadata = JSON.parse(calls[0].init.body);
  assert.equal(metadata.purpose, 'guide');
  assert.equal(metadata.sha256, await sha256(await file.arrayBuffer()));
  assert.equal(await calls.find(call => call.url.endsWith('/parts/0')).init.body.text(), source);
});
