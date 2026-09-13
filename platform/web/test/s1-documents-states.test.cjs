const { test } = require('node:test');
const assert = require('node:assert/strict');
const { openUI } = require('./s1-documents.test.cjs');

test('source coverage separates unavailable from unchecked originals and discloses graph limits', { timeout: 20000 }, async () => {
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', route: async ({ state }) => {
    state.analysis.status = 'needs_sources'; state.canDecide = false;
    state.result = { ...state.result, sources: [], evidence: [], findings: [],
      model: { invoked: false }, coverage: { ...state.result.coverage,
        linkedSources: 0, unavailableSources: 1, uncheckedSources: 1,
        graphTraversalLimited: true, graphCountsExact: false,
        sourceResolution: [
          { graphRef: 'REG-1', status: 'unavailable', reason: 'approved_source_unavailable' },
          { graphRef: 'DOC-missing', status: 'not_checked', reason: 'regulation_source_required' },
        ] } };
    return false;
  } });
  try {
    const { page } = ui;
    await page.getByText('확인하지 않은 원문: 1개', { exact: true }).waitFor();
    await page.getByText(/관계 조회 한도에 도달했습니다/).waitFor();
    await page.getByText('원문 연결·제외 내역', { exact: true }).click();
    await page.getByText(/승인 원문 사용 불가 · 연결·승인·접근 권한·원문 상태/).waitFor();
    await page.getByText(/원문 확인하지 않음 · 규정 원문이 준비되지 않아/).waitFor();
    const links = page.getByRole('link', { name: '원문 연결·권한 확인', exact: true });
    assert.equal(await links.count(), 2);
    const href = await links.first().getAttribute('href');
    assert.match(href, /ref=REG-1/); assert(!href.includes('documentId='));
    await page.getByText('분석 범위와 기술 정보', { exact: true }).click();
    await page.getByText('관계 조회 범위: 조회 한도 도달 · 전체 개수 미확인', { exact: true }).waitFor();
    await ui.capture('s1-source-coverage');
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('unfamiliar historical source-coverage values do not become an approval', { timeout: 15000 }, async () => {
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', route: async ({ state }) => {
    state.result.coverage.sourceResolution = [{ graphRef: 'REG-1', status: 'constructor', reason: '__proto__' }];
    return false;
  } });
  try {
    await ui.page.getByText('원문 연결·제외 내역', { exact: true }).click();
    await ui.page.getByText('원문 상태 확인 필요', { exact: true }).waitFor();
    assert.equal(await ui.page.getByText('승인 원문 연결됨', { exact: true }).count(), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('checked citations still surface output-policy failure while source quote URLs remain literal', { timeout: 20000 }, async () => {
  const fallback = 'AI 응답에 허용되지 않은 출처 주소 또는 자동 판정 표현이 포함되어 본문을 표시하지 않았습니다. 원문과 영향 후보를 직접 검토하거나 다시 분석하세요.';
  const quote = '합성 원문에 기록된 주소: https://source.example.invalid/policy 및 s3://synthetic-source/policy.txt';
  const ui = await openUI('router', { hash: '#/s1?analysisId=ana-1', route: async ({ target, state, json }) => {
    state.result = { ...state.result, summary: fallback, findings: [],
      verification: { ...state.result.verification, references: 'checked', outputPolicy: 'failed' },
      evidence: [{ ...state.result.evidence[0], quote }] };
    if (target === '/documents/doc-1/revisions/doc-1--r1') {
      await json({ document: state.document, revision: state.revision, totalParagraphs: 1,
        paragraphs: [{ id: 'p000002', text: quote, page: 1, sha256: 'a'.repeat(64) }] });
      return true;
    }
    return false;
  } });
  try {
    const { page } = ui;
    const failure = page.getByRole('alert').filter({ hasText: 'AI 응답 표현 검사 실패' });
    await failure.waitFor();
    assert.equal(await page.getByText(fallback, { exact: true }).isVisible(), true);
    assert.equal(await page.getByRole('region', { name: '분석 결과', exact: true }).getByText('담당자 검토 필요', { exact: true }).isVisible(), true);
    assert.equal(await page.getByText(/안내 변경 여부를 확인하세요/).count(), 0);
    assert.equal(await page.locator('blockquote').getByText(quote, { exact: true }).isVisible(), true);
    assert.equal(await page.locator('a[href^="https:"], a[href^="s3:"]').count(), 0);
    await page.getByText('분석 범위와 기술 정보', { exact: true }).click();
    await page.getByText('인용 연결: 연결 확인됨 · 내용 검토는 별도', { exact: true }).waitFor();
    await page.getByText('AI 응답 표현 검사: 실패 · AI 응답 사용 중단', { exact: true }).waitFor();
    await page.getByText(/^상품 · /).click();
    assert.equal(await page.getByRole('button', { name: '합성 상품 검토', exact: true }).isEnabled(), true);
    assert.equal(await page.getByRole('button', { name: '합성 담보 기준 검토', exact: true }).count(), 0);
    assert.equal(ui.calls.filter(call => call.target.endsWith('/decisions')).length, 0);
    await ui.capture('s1-output-policy-failed-desktop');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await failure.isVisible(), true);
    assert.equal(await page.locator('main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    await ui.capture('s1-output-policy-failed-narrow');
    await page.getByRole('link', { name: /E1.*근거 문단/ }).click();
    await page.locator('.doc-paragraph[aria-current=true]').getByText(quote, { exact: true }).waitFor();
    assert.equal(await page.locator('a[href^="https:"], a[href^="s3:"]').count(), 0);
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('optional output-policy status distinguishes passed, not run, absent and unfamiliar values', { timeout: 15000 }, async () => {
  let policy = 'passed';
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', route: async ({ state }) => {
    state.result.verification = { ...state.result.verification, outputPolicy: policy };
    return false;
  } });
  try {
    const { page } = ui;
    for (const [value, label] of [
      ['passed', '통과 · 내용 검토는 별도'],
      ['controlled', '고정된 검토 안내 · AI 자유 서술 제외'],
      ['not_run', '실행하지 않음'],
      [undefined, '기록 없음'],
      ['constructor', '상태 확인 필요'],
    ]) {
      policy = value;
      await page.getByRole('button', { name: '분석 상태 다시 조회', exact: true }).click();
      await page.getByText('분석 범위와 기술 정보', { exact: true }).click();
      await page.getByText((value === 'controlled' ? '검토 안내 방식: ' : 'AI 응답 표현 검사: ') + label, { exact: true }).waitFor();
      if (value === 'controlled') await page.getByText(/AI는 검토 대상과 근거 문단의 연결을 제안합니다/).waitFor();
      assert.equal(await page.getByRole('alert').count(), 0);
      assert.equal(await page.getByRole('region', { name: '분석 결과', exact: true }).getByText('담당자 검토 필요', { exact: true }).isVisible(), true);
    }
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('canonicalized catalog with 77 components keeps eight documents and source links ahead of technical groups', { timeout: 20000 }, async () => {
  const candidates = {
    components: Array.from({ length: 77 }, (_, index) => ({ id: `CMP-${index + 1}`, label: 'Component', name: `합성 컴포넌트 ${index + 1}` })),
    departments: [{ id: 'DEPT-1', label: 'Department', name: '합성 담당 부서' }],
    documents: Array.from({ length: 8 }, (_, index) => ({ id: `DOC-${index + 1}`, label: 'Document', name: `합성 문서 ${index + 1}` })),
    policyRules: [{ id: 'POL-1', label: 'PolicyRule', name: '합성 정책 규칙' }],
    products: [{ id: 'PROD-1', label: 'Product', name: '합성 상품' }],
    screens: [{ id: 'SCR-1', label: 'Screen', name: '합성 화면' }],
  };
  const ui = await openUI('router', { hash: '#/s1?analysisId=ana-1', route: async ({ target, state, json }) => {
    if (target !== '/impact-analyses/ana-1') return false;
    const source = { ...state.result.sources[0], graphRef: 'DOC-1', title: '합성 문서 1 원본' };
    const result = { ...state.result,
      // Match stored canonical JSON: alphabetical keys, deliberately not business order.
      candidates: Object.fromEntries(Object.keys(candidates).sort().map(key => [key, candidates[key]])),
      counts: { components: 80, departments: 1, documents: 8, policyRules: 1, products: 1, screens: 1 },
      sources: [source],
      coverage: { ...state.result.coverage, unavailableSources: 7, sourceLimitReached: true,
        candidateContextsOmitted: 102, candidateOmissions: { components: 3 } },
    };
    await json({ analysis: state.analysis, result, decisions: [], decisionsTruncated: false, staleSources: [], canDecide: true });
    return true;
  } });
  try {
    const { page } = ui;
    const catalog = page.getByRole('region', { name: '영향 후보', exact: true });
    await catalog.getByRole('heading', { name: '영향 후보', exact: true }).waitFor();
    const sections = catalog.locator(':scope > details');
    assert.deepEqual(await sections.locator(':scope > summary').evaluateAll(items => items.map(item => item.textContent.split(' · ')[0])),
      ['문서', '상품', '화면', '컴포넌트', '담당 부서', '정책 규칙']);
    assert.deepEqual(await sections.evaluateAll(items => items.map(item => item.open)), [true, false, false, false, false, false]);
    const documents = sections.nth(0);
    assert.equal(await documents.getByRole('button', { name: /^합성 문서 \d 검토$/ }).count(), 8);
    assert.equal(await documents.getByRole('button', { name: '합성 문서 8 검토', exact: true }).isVisible(), true);
    const firstDocument = documents.getByRole('link', { name: '합성 문서 1 원본 · 사용한 원문 버전', exact: true });
    assert.equal(await firstDocument.isVisible(), true);
    assert.equal(await page.getByRole('button', { name: '합성 컴포넌트 1 검토', exact: true }).isVisible(), false);
    const sources = page.getByRole('region', { name: '분석에 사용한 승인 원문', exact: true });
    const sourceLink = sources.getByRole('link', { name: '합성 문서 1 원본 · 사용한 원문 버전', exact: true });
    assert.equal(await sourceLink.isVisible(), true);
    assert((await sourceLink.boundingBox()).y < (await catalog.boundingBox()).y);
    assert.equal(await sources.getByText('합성 예제 · 실제 내규 아님', { exact: true }).isVisible(), true);
    for (const text of [/현재 관계 목록은 공유 시연용/, /원문의 일부 문단만 분석 범위/, /원문 수의 상한에 도달/, /AI 입력에서 제외된 영향 후보: 102개/]) {
      assert.equal(await page.getByText(text).isVisible(), true);
    }
    assert.equal(await page.getByRole('region', { name: '분석 결과', exact: true }).getByText('담당자 검토 필요', { exact: true }).isVisible(), true);
    const components = sections.nth(3);
    assert.match(await components.locator(':scope > summary').innerText(), /77개 표시 \/ 80개 조회.*생략된 후보: 3개/);
    await ui.capture('s1-large-catalog-desktop');
    // Open the original before ever opening or traversing the component group.
    const href = await sourceLink.getAttribute('href');
    assert.match(href, /revisionId=doc-1--r1/);
    assert.match(href, /textHash=a{64}/);
    assert.match(href, /analysisId=ana-1/);
    await sourceLink.click();
    await page.getByRole('button', { name: '원본 내려받기', exact: true }).waitFor();
    assert.equal(await page.getByLabel('원문 버전', { exact: true }).inputValue(), 'doc-1--r1');
    await page.getByRole('link', { name: '분석 결과로 돌아가기', exact: true }).click();
    await catalog.getByRole('heading', { name: '영향 후보', exact: true }).waitFor();
    // Keyboard opening exposes every retained component; no client-side candidate loss.
    await components.locator(':scope > summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await components.getByRole('button', { name: /^합성 컴포넌트 \d+ 검토$/ }).count(), 77);
    assert.equal(await components.getByRole('button', { name: '합성 컴포넌트 77 검토', exact: true }).isVisible(), true);
    await components.locator(':scope > summary').click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await firstDocument.isVisible(), true);
    assert.equal(await page.getByRole('button', { name: '합성 컴포넌트 1 검토', exact: true }).isVisible(), false);
    assert.equal(await page.locator('main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    await ui.capture('s1-large-catalog-narrow');
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('private job polling reaches needs_sources without claiming a model call or inventing an original', { timeout: 20000 }, async () => {
  let polls = 0;
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', route: async ({ target, json, state }) => {
    if (target === '/impact-analyses/ana-1' && polls < 2) {
      await json({ analysis: { ...state.analysis, status: 'queued' }, decisions: [], staleSources: [], canDecide: false, decisionsTruncated: false });
      return true;
    }
    if (target === '/jobs/job-1') {
      polls++;
      if (polls >= 2) {
        state.analysis.status = 'needs_sources'; state.canDecide = false;
        state.result = { ...state.result, sources: [], evidence: [], findings: [], model: { invoked: false },
          summary: '합성 규정 원문을 먼저 연결하세요.', verification: { sourceIntegrity: 'not_checked', references: 'not_run', semantic: 'requires_human_review' } };
      }
      await json({ job: { id: 'job-1', status: polls >= 2 ? 'completed' : 'running', progress: polls >= 2 ? 100 : { percent: 35, message: '원문 연결 확인 중' } } });
      return true;
    }
    return false;
  } });
  try {
    const { page } = ui;
    await page.getByText('원문 연결 확인 중', { exact: true }).waitFor();
    await page.getByText('합성 규정 원문을 먼저 연결하세요.', { exact: true }).waitFor();
    assert(polls >= 2);
    await page.getByText(/^상품 · /).click();
    assert.equal(await page.getByRole('button', { name: '합성 상품 검토', exact: true }).isDisabled(), true);
    const href = await page.getByRole('link', { name: '규정 원문 등록·찾기', exact: true }).getAttribute('href');
    assert.match(href, /ref=REG-1/);
    assert.match(href, /analysisId=ana-1/);
    assert.equal(await page.getByRole('link', { name: /E1.*근거 문단/ }).count(), 0);
    await page.getByText('분석 범위와 기술 정보', { exact: true }).click();
    await page.getByText('모델 호출: 호출하지 않음', { exact: true }).waitFor();
    await page.getByRole('link', { name: '원문 등록·문서함 검색', exact: true }).waitFor();
    assert.equal(await page.locator('a[href^="https:"]').count(), 0);
    assert.deepEqual(ui.errors, []); assert.deepEqual(ui.external, []);
  } finally { await ui.browser.close(); }
});

test('stale analysis disables decisions even if a stale capability response says they are allowed', { timeout: 15000 }, async () => {
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', state: { staleSources: ['doc-1'], canDecide: true } });
  try {
    await ui.page.getByText(/분석에 사용한 원문이 변경되었습니다/).waitFor();
    await ui.page.getByText(/^상품 · /).click();
    assert.equal(await ui.page.getByRole('button', { name: '합성 상품 검토', exact: true }).isDisabled(), true);
    assert.equal(await ui.page.getByLabel('판단 근거', { exact: true }).count(), 0);
    assert.equal(ui.calls.filter(call => call.method === 'POST').length, 0);
    await ui.capture('s1-stale');
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('a decision CAS conflict removes the displayed result and requires a fresh explicit review', { timeout: 15000 }, async () => {
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', route: async ({ target, method, json, state }) => {
    if (method === 'POST' && target.endsWith('/decisions')) {
      assert.equal(state.analysis.version, 7);
      state.analysis.version++; state.staleSources = ['doc-1'];
      await json({ code: 'conflict', error: '합성 동시 수정 충돌' }, 409); return true;
    }
    return false;
  } });
  try {
    const { page } = ui;
    await page.getByText(/^상품 · /).click();
    await page.getByRole('button', { name: '합성 상품 검토', exact: true }).click();
    await page.getByLabel('판단 근거', { exact: true }).fill('합성 검토 판단');
    await page.getByLabel('표시된 분석 버전과 원문 근거를 확인했습니다', { exact: true }).check();
    await page.getByRole('button', { name: '검토 판단 저장', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '합성 동시 수정 충돌' }).waitFor();
    assert.equal(await page.getByText('원문을 대조하여 변경 여부를 검토하세요.', { exact: true }).count(), 0);
    assert.equal(await page.getByLabel('판단 근거', { exact: true }).count(), 0);
    await page.getByRole('button', { name: '분석 상태 다시 조회', exact: true }).click();
    await page.getByText(/분석에 사용한 원문이 변경되었습니다/).waitFor();
    assert.equal(ui.calls.filter(call => call.target.endsWith('/decisions')).length, 1);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});

test('an ignored-abort analysis response cannot restore private output after logout', { timeout: 15000 }, async () => {
  const held = [];
  const ui = await openUI('S1', { hash: '#/s1?analysisId=ana-1', ignoreAbort: true, route: async ({ target, json, state }) => {
    if (target === '/impact-analyses/ana-1') {
      held.push(() => json({ analysis: state.analysis, result: state.result, decisions: [], canDecide: true, staleSources: [], decisionsTruncated: false }));
      return true;
    }
    return false;
  } });
  try {
    while (!held.length) await ui.page.waitForTimeout(20);
    await ui.page.evaluate(() => window.expire());
    await ui.page.getByText(/로그인이 필요하거나 만료되었습니다/).waitFor();
    for (const release of held) await release();
    await ui.page.waitForTimeout(100);
    assert.equal(await ui.page.getByText('원문을 대조하여 변경 여부를 검토하세요.', { exact: true }).count(), 0);
    assert.equal(await ui.page.getByRole('link', { name: /E1.*근거 문단/ }).count(), 0);
    assert.equal(await ui.page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.deepEqual(ui.errors, []);
  } finally { await ui.browser.close(); }
});
