const { test } = require('node:test');
const assert = require('node:assert/strict');
const { openUI } = require('./s1-documents.test.cjs');

const catalog = () => ({
  schemaVersion: 1, notice: '동작 확인을 위한 합성 자료이며 실제 은행 내규가 아닙니다.',
  samples: [
    { graphRef: 'REG-1', title: '합성 담보 기준', kind: 'regulation', name: 'sample.txt',
      versionLabel: '기준 1', sha256: 'b'.repeat(64),
      sections: [{ title: '검토 항목', text: '상품 조건과 담보 설명을 대조하는 규정 예제입니다.' },
        { title: '확인 원칙', text: '원문을 읽고 담당자가 수정 여부를 결정합니다.' }] },
    { graphRef: 'DOC-2', title: '[합성 예제] 상품 심의 보고', kind: 'report', name: 'report.md',
      versionLabel: '기준 1', sha256: 'c'.repeat(64),
      sections: [{ title: '검토 항목', text: '상품 심의에서 조건 차이를 확인하는 보고서입니다.' },
        { title: '확인 원칙', text: '심의가 자동 완료되는 예제가 아닙니다.' }] },
  ],
});
const sourceHash = '#/documents?documentId=doc-1&revisionId=doc-1--r1&paragraph=p000002&textHash=' + 'a'.repeat(64);

test('sample catalog explains each template and previews content without installing it', { timeout: 20000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents',
    route: async ({ target, method, json }) => {
      if (target === '/documents/samples' && method === 'GET') { await json(catalog()); return true; }
      return false;
    } });
  try {
    const panel = ui.page.getByRole('region', { name: '합성 예제 구성', exact: true });
    await panel.getByRole('heading', { name: '합성 문서 2종 살펴보기', exact: true }).waitFor();
    await panel.getByText('상품 조건과 담보 설명을 대조하는 규정 예제입니다.', { exact: true }).first().waitFor();
    const report = panel.getByRole('article').filter({ hasText: '[합성 예제] 상품 심의 보고' });
    assert.equal(await report.getByText('보고서', { exact: true }).count(), 1);
    await report.getByText('포함 내용 보기', { exact: true }).click();
    await report.getByText('심의가 자동 완료되는 예제가 아닙니다.', { exact: true }).waitFor();
    assert.equal(ui.calls.filter(call => call.method !== 'GET').length, 0);
    for (const width of [390, 1920, 2560, 3440]) {
      await ui.page.setViewportSize({ width, height: 1080 });
      assert.equal(await ui.page.locator('main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    }
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('exact evidence carries document identity and only a hash-matched template explanation', { timeout: 20000 }, async () => {
  let wrongHash = false;
  const ui = await openUI('documents/LibraryPage', { hash: sourceHash,
    route: async ({ target, method, json }) => {
      if (target === '/documents/samples' && method === 'GET') {
        const response = catalog();
        if (wrongHash) {
          response.samples[0].sha256 = 'd'.repeat(64);
          response.samples[0].sections[0].text = '다른 원본 버전의 안내';
        }
        await json(response); return true;
      }
      return false;
    } });
  try {
    const reader = ui.page.getByRole('region', { name: '원문과 검토', exact: true });
    await reader.getByRole('region', { name: '이 문서 안내', exact: true }).waitFor();
    await reader.getByRole('region', { name: '이 문서 안내', exact: true })
      .getByText('상품 조건과 담보 설명을 대조하는 규정 예제입니다.', { exact: true }).waitFor();
    const selected = reader.locator('.doc-paragraph[aria-current=true]');
    await selected.waitFor();
    assert.match(await selected.locator('.doc-source-identity').innerText(), /합성 담보 기준.*규정.*기준 1/s);
    assert.match(await selected.locator('.doc-source-identity').innerText(), /상품 조건과 담보 설명을 대조/);
    assert.equal(await selected.locator('.doc-literal').textContent(), '합성 문단: 담당자가 변경 범위를 검토합니다.');
    assert.equal(await ui.page.evaluate(() => window.bad), undefined);
    wrongHash = true; await ui.reload();
    await ui.page.locator('.doc-paragraph[aria-current=true]').waitFor();
    assert.equal(await reader.getByRole('region', { name: '이 문서 안내', exact: true }).count(), 0);
    assert.equal(await reader.getByText('다른 원본 버전의 안내', { exact: true }).count(), 0);
    assert.equal(await ui.page.locator('.doc-paragraph[aria-current=true] .doc-literal').textContent(),
      '합성 문단: 담당자가 변경 범위를 검토합니다.');
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('uploaded originals are not presented as templates even when reference and bytes match', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: sourceHash,
    route: async ({ target, method, json, state }) => {
      state.document.provenance = 'uploaded';
      if (target === '/documents/samples' && method === 'GET') { await json(catalog()); return true; }
      return false;
    } });
  try {
    await ui.page.locator('.doc-paragraph[aria-current=true]').waitFor();
    assert.equal(await ui.page.getByRole('region', { name: '이 문서 안내', exact: true }).count(), 0);
    assert.equal(await ui.page.getByText('상품 조건과 담보 설명을 대조하는 규정 예제입니다.', { exact: true }).count(), 0);
    assert.match(await ui.page.locator('.doc-source-identity').innerText(), /합성 담보 기준.*기준 1/s);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('unreadable catalog can be retried without claiming documents were installed', { timeout: 15000 }, async () => {
  let malformed = true;
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents',
    route: async ({ target, method, json }) => {
      if (target === '/documents/samples' && method === 'GET') {
        await json(malformed ? { schemaVersion: 1, notice: '예제', samples: [null] } : catalog()); return true;
      }
      return false;
    } });
  try {
    await ui.page.getByRole('alert').filter({ hasText: '합성 예제 구성을 확인하지 못했습니다.' }).waitFor();
    assert.equal(await ui.page.getByRole('button', { name: /새로 등록/ }).count(), 0);
    malformed = false;
    await ui.page.getByRole('button', { name: '예제 다시 조회', exact: true }).click();
    await ui.page.getByRole('heading', { name: '합성 문서 2종 살펴보기', exact: true }).waitFor();
    assert.equal(ui.calls.filter(call => call.method !== 'GET').length, 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('members can inspect templates without an install button and catalog navigation preserves project context', { timeout: 20000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: sourceHash + '&projectId=team-a&analysisId=ana-1',
    route: async ({ target, method, json, state }) => {
      state.document.projectId = 'team-a';
      if (target === '/documents/config') {
        await json({ roles: ['owner', 'planner', 'designer', 'developer'], maxFileBytes: 20971520,
          extensions: ['md', 'txt'], role: 'designer', actorId: 'actor', projectId: 'team-a' }); return true;
      }
      if (target === '/documents/samples' && method === 'GET') { await json(catalog()); return true; }
      return false;
    } });
  try {
    await ui.page.getByRole('link', { name: '합성 예제 구성 보기', exact: true }).first().click();
    const panel = ui.page.getByRole('region', { name: '합성 예제 구성', exact: true });
    await panel.getByRole('heading', { name: '합성 문서 2종 살펴보기', exact: true }).waitFor();
    assert.match(ui.page.url(), /projectId=team-a/); assert.match(ui.page.url(), /analysisId=ana-1/);
    assert(!ui.page.url().includes('documentId='));
    assert.equal(await panel.getByRole('button', { name: /새로 등록/ }).count(), 0);
    assert(ui.calls.filter(call => call.target === '/documents/samples').every(call => call.scope === 'team-a'));
    assert.equal(ui.calls.filter(call => call.method !== 'GET').length, 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});
