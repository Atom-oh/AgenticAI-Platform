const { test } = require('node:test');
const assert = require('node:assert/strict');
const { openUI } = require('./s1-documents.test.cjs');

for (const type of ['documents', 'analyses']) {
  test(`refresh fences a late ${type} page even when cancellation is ignored`, { timeout: 15000 }, async () => {
    let revoked = false;
    const held = [];
    const isDocument = type === 'documents';
    const endpoint = isDocument ? '/documents' : '/impact-analyses';
    const sentinel = isDocument ? 'REVOKED_METADATA_SENTINEL' : 'REVOKED_QUERY_SENTINEL';
    const ui = await openUI(isDocument ? 'documents/LibraryPage' : 'S1', {
      hash: isDocument ? '#/documents' : '#/s1', ignoreAbort: true,
      route: async ({ target, method, url, json, state }) => {
        if (target !== endpoint || method !== 'GET') return false;
        if (url.searchParams.has('cursor')) {
          const row = isDocument ? { ...state.document, id: 'secret-document', title: sentinel }
            : { ...state.analysis, id: 'secret-analysis', query: sentinel };
          held.push(() => json({ [type]: [row] }));
        } else await json({ [type]: revoked ? [] : [isDocument ? state.document : state.analysis],
          ...(revoked ? {} : { cursor: 'next-page' }) });
        return true;
      },
    });
    try {
      const { page } = ui;
      await page.getByRole('button', { name: isDocument ? '문서 더 보기' : '분석 더 보기', exact: true }).click();
      while (!held.length) await page.waitForTimeout(20);
      revoked = true;
      await page.getByRole('button', { name: isDocument ? '목록 새로 조회' : '분석 목록 새로 조회', exact: true }).click();
      await page.getByText(isDocument ? /등록된 문서가 없습니다/ : '저장된 분석이 없습니다.', { exact: false }).waitFor();
      const received = page.waitForResponse(response => new URL(response.url()).searchParams.get('cursor') === 'next-page');
      for (const release of held) await release();
      await received;
      await page.waitForTimeout(100);
      assert.equal(await page.getByText(sentinel, { exact: true }).count(), 0);
      assert.equal(await page.getByRole('button', { name: isDocument ? '문서 더 보기' : '분석 더 보기', exact: true }).count(), 0);
      assert.deepEqual(ui.errors, []);
    } finally { await ui.browser.close(); }
  });
}

for (const explicitEmpty of [false, true]) {
  test(`delayed references ${explicitEmpty ? 'respect an explicit empty selection' : 'retain the missing-source target'}`, { timeout: 15000 }, async () => {
    const held = [];
    let registration;
    const ui = await openUI('documents/LibraryPage', {
      hash: '#/documents?ref=DOC-missing&analysisId=ana-1',
      route: async ({ target, method, body, json }) => {
        if (target === '/documents/references') {
          held.push(() => json({ references: [
            { id: 'REG-1', label: 'Regulation', title: '합성 기준' },
            { id: 'DOC-missing', label: 'Document', title: '누락된 합성 원문' },
          ], backend: 'local' }));
          return true;
        }
        if (target === '/documents' && method === 'POST') {
          registration = body;
          await json({ error: '합성 요청 본문 확인 완료', code: 'unavailable' }, 503);
          return true;
        }
        return false;
      },
    });
    try {
      const { page } = ui;
      const select = page.getByLabel('관계 목록의 연결 대상', { exact: true });
      await select.waitFor();
      while (!held.length) await page.waitForTimeout(20);
      if (explicitEmpty) await select.selectOption('');
      await page.getByLabel('원문 파일', { exact: true }).setInputFiles({
        name: 'synthetic.txt', mimeType: 'text/plain', buffer: Buffer.from('합성 원문입니다.'),
      });
      const submit = page.getByRole('button', { name: '원문 전송', exact: true });
      assert.equal(await submit.isDisabled(), !explicitEmpty);
      assert.equal(ui.calls.filter(call => call.target === '/documents' && call.method === 'POST').length, 0);
      for (const release of held) await release();
      await select.locator('option[value="DOC-missing"]').waitFor({ state: 'attached' });
      assert.equal(await select.inputValue(), explicitEmpty ? '' : 'DOC-missing');
      await submit.click();
      await page.getByText('합성 요청 본문 확인 완료', { exact: false }).waitFor();
      assert.equal(registration.graphRef, explicitEmpty ? undefined : 'DOC-missing');
      assert.deepEqual(ui.errors, []);
    } finally { await ui.browser.close(); }
  });
}

test('source refresh restarts the activity history at its first page', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', {
    hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1',
    route: async ({ target, url, json }) => {
      if (target !== '/documents/doc-1/activity') return false;
      const older = url.searchParams.has('cursor');
      await json({ events: [{ id: older ? 'old' : 'new', action: 'approved',
        actorId: older ? 'OLD_ACTIVITY_SENTINEL' : 'NEW_ACTIVITY_SENTINEL', createdAt: 1 }],
        ...(older ? {} : { cursor: 'older-page' }) });
      return true;
    },
  });
  try {
    const { page } = ui;
    await page.getByRole('button', { name: '활동 이력 조회', exact: true }).click();
    await page.getByText(/NEW_ACTIVITY_SENTINEL/).waitFor();
    await page.getByRole('button', { name: '원문 다시 조회', exact: true }).click();
    await page.getByRole('button', { name: '활동 이력 조회', exact: true }).click();
    await page.getByRole('heading', { name: '문서 활동 이력', exact: true }).waitFor();
    const activityCalls = ui.calls.filter(call => call.target.endsWith('/activity'));
    assert.equal(activityCalls.length, 2);
    assert.deepEqual(activityCalls.map(call => call.query), [{}, {}]);
    await page.getByText(/NEW_ACTIVITY_SENTINEL/).waitFor();
    assert.equal(await page.getByText(/OLD_ACTIVITY_SENTINEL/).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('an analysis return parameter never certifies the selected document as its source', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', {
    hash: '#/documents?documentId=doc-1&revisionId=doc-1--r1&analysisId=unrelated-analysis',
    route: async ({ state }) => {
      state.document.approvedRevisionId = 'doc-1--r2';
      return false;
    },
  });
  try {
    await ui.page.getByText('합성 문단: 담당자가 변경 범위를 검토합니다.', { exact: true }).waitFor();
    assert.equal(await ui.page.getByText(/분석 당시의 원문 버전입니다/).count(), 0);
    await ui.page.getByText(/분석에 사용된 버전은 분석 결과의 근거 링크에서 확인/).waitFor();
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});
