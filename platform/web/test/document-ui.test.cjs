const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { openUI } = require('./s1-documents.test.cjs');

test('upload retry, extraction, review, original download, new revision, ACL and archive use displayed versions', { timeout: 40000 }, async () => {
  let failPart = true, originals = Buffer.from('합성 문서\n담당자가 검토합니다.'), revisions = [], partUploads = [];
  const hash = bytes => createHash('sha256').update(bytes).digest('hex');
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?register=1', route: async args => {
    const { target, method, body, state, json, route } = args;
    if (target === '/documents' && method === 'POST' || target === '/documents/doc-1/revisions' && method === 'POST') {
      if (target.endsWith('/revisions')) assert.equal(body.version, state.document.version);
      state.document = { ...state.document, provenance: 'uploaded', title: body.title || state.document.title,
        version: state.document.version + 1, latestRevisionId: 'doc-1--r' + (revisions.length + 1),
        approvedRevisionId: revisions.length ? state.document.approvedRevisionId : null };
      state.revision = { ...state.revision, id: state.document.latestRevisionId, revision: revisions.length + 1,
        name: body.name, sha256: body.sha256, size: body.size, versionLabel: body.versionLabel, version: 1,
        status: 'uploading', parseStatus: 'pending', textHash: null, reviewedBy: undefined, reviewedAt: undefined, reviewNote: undefined };
      revisions.push(state.revision);
      await json({ document: state.document, revision: state.revision, chunkBytes: 12 }, 201); return true;
    }
    if (/\/parts\//.test(target)) {
      if (target.endsWith('/1') && failPart) { failPart = false; await json({ error: '합성 전송 재시도', code: 'unavailable' }, 503); }
      else { partUploads.push(target); await json({ revision: state.revision.id, index: Number(target.split('/').at(-1)), sha256: hash('part') }); }
      return true;
    }
    if (target.endsWith('/complete')) {
      state.revision = { ...state.revision, status: 'processing', jobId: 'extract-1', version: state.revision.version + 1 };
      await json({ document: state.document, revision: state.revision, job: { id: 'extract-1', status: 'queued', progress: 0 } }, 202); return true;
    }
    if (target === '/jobs/extract-1') {
      state.revision = { ...state.revision, status: 'draft', parseStatus: 'complete', textHash: hash('projection-' + state.revision.id),
        version: state.revision.version + 1 };
      revisions[revisions.length - 1] = state.revision;
      await json({ job: { id: 'extract-1', status: 'completed', progress: 100 } }); return true;
    }
    if (target === '/documents/doc-1') {
      await json({ document: state.document, revisions: revisions.map(row => row.id === state.revision.id ? state.revision : row),
        capabilities: { read: true, edit: state.document.status === 'active', review: state.document.status === 'active', manage: state.document.status === 'active' } }); return true;
    }
    if (target.endsWith('/submit')) {
      assert.equal(body.version, state.revision.version);
      assert.equal(state.revision.status, 'draft');
      state.revision = { ...state.revision, status: 'in_review', version: state.revision.version + 1 };
      await json({ document: state.document, revision: state.revision }); return true;
    }
    if (target.endsWith('/review')) {
      assert.equal(body.version, state.revision.version); assert.equal(body.decision, 'approved');
      assert.equal(state.revision.status, 'in_review');
      state.revision = { ...state.revision, version: state.revision.version + 1, status: 'approved',
        reviewedBy: 'owner-reviewer', reviewedAt: 1789300000000, reviewNote: body.note };
      state.document = { ...state.document, version: state.document.version + 1, approvedRevisionId: state.revision.id };
      revisions[revisions.length - 1] = state.revision;
      await json({ document: state.document, revision: state.revision }); return true;
    }
    if (target.endsWith('/permissions')) {
      assert.equal(body.version, state.document.version);
      assert.deepEqual(body.readRoles, ['owner']);
      state.document = { ...state.document, version: state.document.version + 1, readRoles: body.readRoles };
      await json({ document: state.document }); return true;
    }
    if (target.endsWith('/archive')) {
      assert.equal(body.version, state.document.version);
      state.document = { ...state.document, version: state.document.version + 1, status: 'archived' };
      await json({ document: state.document }); return true;
    }
    if (target.endsWith('/blob')) {
      await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: originals,
        headers: { 'X-Total-Size': String(originals.length), 'X-Chunk-Size': String(originals.length), 'X-SHA256': hash(originals),
          'X-Content-Type': 'text/html' } }); return true;
    }
    if (/^\/documents\/doc-1\/revisions\/doc-1--r\d+$/.test(target)) {
      const selected = target.endsWith(state.revision.id) ? state.revision : revisions.find(row => target.endsWith(row.id));
      await json({ document: state.document, revision: selected, totalParagraphs: 1,
        paragraphs: selected.textHash ? [{ id: 'p000001', text: '<script>window.bad=true</script>\n담당자가 확인합니다.', page: 1, sha256: hash('paragraph') }] : [] }); return true;
    }
    return false;
  } });
  const { page, calls } = ui;
  try {
    await page.getByLabel('원문 파일', { exact: true }).setInputFiles({ name: 'synthetic.txt', mimeType: 'text/plain', buffer: originals });
    await page.getByLabel('문서 제목', { exact: true }).fill('등록한 합성 검토 자료');
    await page.getByLabel('관계 목록의 연결 대상', { exact: true }).selectOption('REG-1');
    await page.getByLabel('버전 이름', { exact: true }).fill('초안 1');
    await page.getByRole('button', { name: '원문 전송', exact: true }).click();
    await page.getByRole('button', { name: '전송 다시 시도', exact: true }).waitFor();
    assert.equal(await page.getByLabel('문서 제목', { exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '전송 다시 시도', exact: true }).click();
    await page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).waitFor();
    assert.equal(calls.filter(call => call.target === '/documents' && call.method === 'POST').length, 1);
    assert.equal(partUploads.filter(target => target.endsWith('/0')).length, 1);
    assert.equal(calls.filter(call => call.target.endsWith('/review')).length, 0);
    assert.equal(await page.evaluate(() => window.bad), undefined);
    assert.equal(await page.locator('iframe').count(), 0);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '원본 내려받기', exact: true }).click();
    assert.equal((await download).suggestedFilename(), 'synthetic.txt');
    await page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).click();
    await page.getByLabel('원문 검토 의견', { exact: true }).fill('추출된 합성 본문과 원본을 확인했습니다.');
    assert.equal(await page.getByRole('button', { name: '이 버전 원문 승인', exact: true }).isDisabled(), true);
    await page.getByLabel('이 원문 버전과 추출된 본문을 확인했습니다', { exact: true }).check();
    await page.getByRole('button', { name: '이 버전 원문 승인', exact: true }).click();
    await page.getByText(/검토자 owner-reviewer/).waitFor();
    await ui.capture('library-reviewed-desktop');
    await page.getByRole('link', { name: '이 문단 링크', exact: true }).click();
    assert.match(page.url(), /textHash=/);
    await page.getByRole('button', { name: '새 버전 등록', exact: true }).click();
    originals = Buffer.from('합성 문서 두 번째 원본');
    await page.getByLabel('원문 파일', { exact: true }).setInputFiles({ name: 'second.txt', mimeType: 'text/plain', buffer: originals });
    await page.getByLabel('버전 이름', { exact: true }).fill('초안 2');
    await page.getByRole('button', { name: '새 버전 전송', exact: true }).click();
    await page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).waitFor();
    assert.match(page.url(), /revisionId=doc-1--r2/);
    assert(!page.url().includes('textHash='), 'A new upload must not inherit the previous source hash');
    assert.equal(await page.getByLabel('원문 버전', { exact: true }).locator('option').count(), 2);
    await page.getByText('읽기 권한 관리', { exact: true }).click();
    await page.getByLabel('기획 담당자', { exact: true }).uncheck();
    assert.equal(await page.getByLabel('소유자', { exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '읽기 권한 저장', exact: true }).click();
    await page.getByText('문서 보관 처리', { exact: true }).first().click();
    await page.getByLabel('새 분석에서 사용을 중단하고 이력을 보관합니다', { exact: true }).check();
    await page.getByRole('button', { name: '문서 보관 처리', exact: true }).click();
    await page.getByText(/보관 처리된 문서입니다/).waitFor();
    await page.getByLabel('문서 목록', { exact: true }).getByText('보관됨', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    await ui.capture('library-archived-narrow');
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('project evidence opens the exact historical paragraph and returns to the recoverable analysis URL', { timeout: 20000 }, async () => {
  const ui = await openUI('router', { hash: '#/s1?projectId=team-a&analysisId=ana-1',
    route: async ({ state }) => { state.document.projectId = 'team-a'; state.analysis.projectId = 'team-a'; return false; } });
  try {
    const { page } = ui;
    await page.getByRole('link', { name: /E1.*근거 문단/ }).first().click();
    await page.locator('.doc-paragraph[aria-current=true]').waitFor();
    assert.match(await page.locator('.doc-paragraph[aria-current=true]').innerText(), /p000002/);
    assert.equal(await page.getByLabel('원문 버전', { exact: true }).inputValue(), 'doc-1--r1');
    assert.equal(await page.getByRole('button', { name: '새 버전 등록', exact: true }).count(), 0);
    assert.match(page.url(), /projectId=team-a/);
    assert.match(page.url(), /analysisId=ana-1/);
    assert.equal(await page.evaluate(() => window.bad), undefined);
    await ui.capture('library-evidence-desktop');
    await page.getByRole('link', { name: '분석 결과로 돌아가기', exact: true }).click();
    await page.getByText('원문을 대조하여 변경 여부를 검토하세요.', { exact: true }).waitFor();
    await ui.reload();
    await page.getByText('원문을 대조하여 변경 여부를 검토하세요.', { exact: true }).waitFor();
    assert.match(page.url(), /analysisId=ana-1/);
    assert(ui.calls.filter(call => call.target.startsWith('/impact-analyses/') || call.target.startsWith('/documents/doc-1'))
      .every(call => call.scope === 'team-a'));
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('exact source hash mismatch and missing revision never show latest content', { timeout: 20000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1&textHash=' + 'c'.repeat(64) });
  try {
    const { page } = ui;
    await page.getByRole('alert').filter({ hasText: '해시' }).waitFor();
    assert.equal(await page.locator('.doc-paragraph').count(), 0);
    assert.equal(await page.getByRole('button', { name: '원본 내려받기', exact: true }).count(), 0);
    await page.evaluate(() => { location.hash = '#/documents?documentId=doc-1&revisionId=doc-1--missing'; });
    await page.getByText(/지정된 원문 버전을 찾을 수 없습니다/).waitFor();
    assert.equal(await page.locator('.doc-paragraph').count(), 0);
    assert.equal(ui.calls.filter(call => call.target.endsWith('/doc-1--r2')).length, 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('partial extraction cannot be submitted and unfamiliar statuses remain renderable', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1',
    route: async ({ state }) => { state.revision.parseStatus = 'partial'; state.revision.status = 'draft'; state.document.status = 'constructor'; return false; } });
  try {
    await ui.page.getByText(/일부 본문만 추출되었습니다/).waitFor();
    assert.equal(await ui.page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).count(), 0);
    await ui.page.getByLabel('원문과 검토', { exact: true }).getByText('상태 확인 필요', { exact: true }).waitFor();
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('collection switches discard late private responses even when fetch ignores AbortSignal', { timeout: 15000 }, async () => {
  const held = [];
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1', ignoreAbort: true,
    route: async ({ target, scope, json, state }) => {
      if (target === '/documents/doc-1/revisions/doc-1--r1' && scope === 'personal') {
        held.push(() => json({ document: state.document, revision: state.revision, totalParagraphs: 1,
          paragraphs: [{ id: 'p000001', text: '늦은 개인 원문은 표시하면 안 됨', page: 1, sha256: 'a'.repeat(64) }] })); return true;
      }
      if (target === '/documents' && scope === 'team-a') { await json({ documents: [] }); return true; }
      return false;
    } });
  try {
    const { page } = ui;
    await page.waitForFunction(() => document.querySelector('.doc-scope select option[value="team-a"]'));
    while (!held.length) await page.waitForTimeout(20);
    await page.getByLabel('문서함 범위', { exact: true }).selectOption('team-a');
    await page.getByText(/등록된 문서가 없습니다/).waitFor();
    for (const release of held) await release();
    await page.waitForTimeout(100);
    assert.equal(await page.getByText('늦은 개인 원문은 표시하면 안 됨', { exact: true }).count(), 0);
    assert.equal(await page.locator('.doc-paragraph').count(), 0);
    assert.equal(await page.getByRole('link', { name: '합성 담보 기준', exact: true }).count(), 0);
    await page.evaluate(() => window.expire());
    await page.getByText(/로그인이 필요하거나 만료되었습니다/).waitFor();
    assert.equal(await page.getByLabel('문서함 범위', { exact: true }).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('source access denial clears all private content and offers a scope recovery action', { timeout: 15000 }, async () => {
  let denied = false;
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1',
    route: async ({ target, json }) => {
      if (denied && target === '/documents/doc-1') { await json({ code: 'forbidden', error: '문서 읽기 권한이 회수되었습니다.' }, 403); return true; }
      return false;
    } });
  try {
    await ui.page.getByText('합성 문단: 담당자가 변경 범위를 검토합니다.', { exact: true }).waitFor();
    denied = true;
    await ui.page.getByRole('button', { name: '원문 다시 조회', exact: true }).click();
    await ui.page.getByText('문서 읽기 권한이 회수되었습니다.', { exact: false }).waitFor();
    assert.equal(await ui.page.locator('.doc-paragraph').count(), 0);
    assert.equal(await ui.page.getByRole('button', { name: '원본 내려받기', exact: true }).count(), 0);
    await ui.page.getByRole('button', { name: '참여 권한 다시 조회', exact: true }).waitFor();
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('failed paragraph pagination removes already rendered source text', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1',
    route: async ({ target, url, json, state }) => {
      if (target === '/documents/doc-1/revisions/doc-1--r1') {
        if (url.searchParams.has('cursor')) await json({ code: 'source-integrity', error: '합성 추출본이 변경되었습니다.' }, 409);
        else await json({ document: state.document, revision: state.revision, cursor: 'next', totalParagraphs: 2,
          paragraphs: [{ id: 'p000001', text: '추가 조회 실패 후 지워야 하는 본문', page: 1, sha256: 'a'.repeat(64) }] });
        return true;
      }
      return false;
    } });
  try {
    await ui.page.getByText('추가 조회 실패 후 지워야 하는 본문', { exact: true }).waitFor();
    await ui.page.getByRole('button', { name: '다음 문단 더 보기', exact: true }).click();
    await ui.page.getByRole('alert').waitFor();
    assert.equal(await ui.page.locator('.doc-paragraph').count(), 0);
    assert.equal(await ui.page.getByRole('button', { name: '원본 내려받기', exact: true }).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('changing collection clears an in-flight upload and its retry checkpoint', { timeout: 15000 }, async () => {
  const held = [];
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?register=1', ignoreAbort: true,
    route: async ({ target, method, json, state, scope }) => {
      if (target === '/documents' && method === 'POST') {
        await json({ document: { ...state.document, provenance: 'uploaded' }, revision: state.revision, chunkBytes: 2097152 }, 201); return true;
      }
      if (target.endsWith('/parts/0')) { held.push(() => json({})); return true; }
      if (target === '/documents' && scope === 'team-a') { await json({ documents: [] }); return true; }
      return false;
    } });
  try {
    const { page } = ui;
    await page.getByLabel('원문 파일', { exact: true }).setInputFiles({ name: 'synthetic.txt', mimeType: 'text/plain', buffer: Buffer.from('합성 원문') });
    await page.getByRole('button', { name: '원문 전송', exact: true }).click();
    while (!held.length) await page.waitForTimeout(20);
    await page.getByLabel('문서함 범위', { exact: true }).selectOption('team-a');
    await page.getByRole('button', { name: '새 문서 등록', exact: true }).click();
    for (const release of held) await release();
    assert.equal(await page.getByLabel('원문 파일', { exact: true }).inputValue(), '');
    assert.equal(await page.getByRole('button', { name: '전송 다시 시도', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '원문 전송', exact: true }).isDisabled(), true);
    assert.equal(ui.calls.filter(call => call.target.endsWith('/complete')).length, 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('unsupported upload is rejected before registration and failed extraction remains actionable', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?register=1', route: async ({ state }) => {
    state.revision.status = 'failed'; state.revision.parseStatus = 'failed'; state.revision.paragraphCount = 0;
    return false;
  } });
  try {
    const { page } = ui;
    await page.getByLabel('원문 파일', { exact: true }).setInputFiles({ name: 'synthetic.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('fake') });
    await page.getByRole('button', { name: '원문 전송', exact: true }).click();
    await page.getByText(/지원하는 형식과 크기의 원문 파일/).waitFor();
    assert.equal(ui.calls.filter(call => call.target === '/documents' && call.method === 'POST').length, 0);
    await page.evaluate(() => { location.hash = '#/documents?documentId=doc-1&revisionId=doc-1--r1'; });
    await page.getByText(/본문 추출에 실패했습니다/).waitFor();
    assert.equal(await page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});
