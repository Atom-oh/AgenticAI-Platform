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

for (const scenario of ['personal-list-failed', 'shared-index-missing', 'personal-list-pending']) {
  test(`canonical document authority survives optional discovery: ${scenario}`, { timeout: 15000 }, async () => {
    const held = [];
    const shared = scenario === 'shared-index-missing';
    const ui = await openUI('documents/LibraryPage', {
      hash: shared ? '#/documents?projectId=team-a' : '#/documents',
      route: async ({ target, json, state }) => {
        if (shared) state.document.projectId = 'team-a';
        if (target !== '/projects') return false;
        if (scenario === 'personal-list-pending') held.push(() => json({ projects: [] }));
        else if (scenario === 'personal-list-failed') await json({ error: '합성 목록 장애', code: 'unavailable' }, 503);
        else await json({ projects: [] });
        return true;
      },
    });
    try {
      const { page } = ui;
      await page.getByRole('heading', { name: '문서 목록', exact: true }).waitFor();
      await page.getByRole('link', { name: '합성 담보 기준', exact: true }).waitFor();
      assert.equal(await page.getByLabel('문서함 범위', { exact: true }).inputValue(), shared ? 'team-a' : '');
      for (const release of held) await release();
      if (scenario === 'personal-list-failed') await page.getByRole('button', { name: '프로젝트 목록 다시 조회', exact: true }).waitFor();
      assert.deepEqual(ui.errors, []);
    } finally { await ui.browser.close(); }
  });
}

test('late optional discovery cannot restore a prior identity after logout', { timeout: 15000 }, async () => {
  const held = [];
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents', ignoreAbort: true,
    route: async ({ target, json }) => {
      if (target !== '/projects') return false;
      held.push(() => json({ projects: [{ id: 'team-secret', name: 'OLD_PRIVATE_PROJECT_NAME', members: {}, version: 1 }] }));
      return true;
    } });
  try {
    await ui.page.getByRole('heading', { name: '문서 목록', exact: true }).waitFor();
    await ui.page.evaluate(() => window.expire());
    for (const release of held) await release();
    await ui.page.getByText(/로그인이 필요하거나 만료되었습니다/).waitFor();
    assert.equal(await ui.page.getByText('OLD_PRIVATE_PROJECT_NAME', { exact: true }).count(), 0);
    assert.equal(await ui.page.getByRole('heading', { name: '문서 목록', exact: true }).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

for (const resourceDenied of [false, true]) {
  test(`shared pending analysis uses its authorized endpoint after raw-job denial (${resourceDenied})`, { timeout: 15000 }, async () => {
    let rawDenied = false;
    const ui = await openUI('S1', { hash: '#/s1?projectId=team-a&analysisId=ana-1',
      route: async ({ target, state, json }) => {
        state.analysis.projectId = 'team-a'; state.analysis.createdBy = 'teammate';
        state.document.projectId = 'team-a';
        if (target === '/jobs/job-1') { rawDenied = true; await json({ error: 'Raw job requester only', code: 'forbidden' }, 403); return true; }
        if (target === '/impact-analyses/ana-1') {
          if (rawDenied && resourceDenied) { await json({ error: '합성 원문 접근 권한이 회수되었습니다.', code: 'forbidden' }, 403); return true; }
          state.analysis.status = rawDenied ? 'needs_review' : 'running';
        }
        return false;
      } });
    try {
      if (resourceDenied) {
        await ui.page.getByText('합성 원문 접근 권한이 회수되었습니다.', { exact: false }).waitFor();
        assert.equal(await ui.page.getByRole('region', { name: '분석 결과', exact: true }).count(), 0);
      } else {
        await ui.page.getByRole('region', { name: '분석 결과', exact: true }).getByText('담당자 검토 필요', { exact: true }).waitFor();
        assert.equal(await ui.page.getByText('Raw job requester only', { exact: false }).count(), 0);
        await ui.page.getByRole('link', { name: /E1 · 근거 문단/ }).first().waitFor();
      }
      assert(rawDenied); assert.deepEqual(ui.errors, []);
    } finally { await ui.browser.close(); }
  });
}

test('shared processing source recovers via source reads after raw-job denial', { timeout: 15000 }, async () => {
  let denied = false;
  const ui = await openUI('documents/LibraryPage', {
    hash: '#/documents?projectId=team-a&documentId=doc-1&revisionId=doc-1--r1',
    route: async ({ target, state, json }) => {
      state.document.projectId = 'team-a'; state.revision.createdBy = 'teammate';
      state.revision.jobId = 'shared-extract';
      state.revision.status = denied ? 'draft' : 'processing';
      if (target === '/jobs/shared-extract') { denied = true; await json({ error: 'Raw job requester only', code: 'forbidden' }, 403); return true; }
      return false;
    },
  });
  try {
    await ui.page.getByRole('button', { name: '이 버전 검토 요청', exact: true }).waitFor();
    assert(denied); assert.equal(await ui.page.getByText('Raw job requester only', { exact: false }).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('explicit unlinked registration remains available when reference discovery fails', { timeout: 15000 }, async () => {
  const ui = await openUI('documents/LibraryPage', { hash: '#/documents?ref=DOC-missing', route: async ({ target, json }) => {
    if (target !== '/documents/references') return false;
    await json({ error: 'Synthetic catalog unavailable', code: 'unavailable' }, 503); return true;
  } });
  try {
    await ui.page.getByLabel('원문 파일', { exact: true }).setInputFiles({ name: 'synthetic.txt', mimeType: 'text/plain', buffer: Buffer.from('합성 원문') });
    await ui.page.getByRole('button', { name: '연결 없이 등록하기', exact: true }).click();
    assert.equal(await ui.page.getByRole('button', { name: '원문 전송', exact: true }).isEnabled(), true);
    assert.equal(await ui.page.getByLabel('관계 목록의 연결 대상', { exact: true }).inputValue(), '');
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('an unsupported graph reference or malformed model catalog cannot crash S1', { timeout: 15000 }, async () => {
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', route: async ({ state, target, json }) => {
    state.result.candidates.documents = [{ id: 'document:external', label: 'Document', name: 'Synthetic unsupported reference' }];
    if (target === '/config') { await json({ defaultModel: 'missing' }); return true; }
    return false;
  } });
  try {
    await ui.page.getByText('Synthetic unsupported reference', { exact: true }).first().waitFor();
    await ui.page.getByText('원문 연결 확인 필요', { exact: true }).waitFor();
    await ui.page.getByRole('link', { name: '새 분석 작성', exact: true }).click();
    await ui.page.getByText(/모델 목록 형식을 확인하지 못했습니다/).waitFor();
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});
