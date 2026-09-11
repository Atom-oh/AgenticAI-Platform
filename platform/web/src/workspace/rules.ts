import type { Action, Asset, EditableContract, Purpose, Round, Rule, Run, Step, StyleProperty } from './types';

export const PURPOSES = {
  prototype: 'HTML 화면 시안', reference: '화면 기준 이미지', component: '컴포넌트·아이콘 자산',
  guide: '업무·스타일 가이드', skill: '작업 지침·스킬', token: '디자인 토큰', archive: '원본 보관',
} as const;
export function suggestedPurpose(name: string): Purpose {
  const extension = name.split('.').pop()?.toLowerCase();
  if (extension === 'html' || extension === 'htm') return 'prototype';
  if (extension === 'css') return 'guide';
  if (['png', 'jpg', 'jpeg'].includes(extension || '')) return 'reference';
  if (extension === 'svg') return 'component';
  if (extension === 'fig') return 'archive';
  if (/^skill\.m(?:d|arkdown)$/i.test(name)) return 'skill';
  return 'guide';
}
export const ACTIONS: Record<Action, string> = {
  fill: '값 입력', click: '누르기', check: '체크 변경', select: '항목 선택', press: '키 누르기',
  expectText: '문구 확인', expectValue: '입력값 확인', expectVisible: '표시 여부 확인',
  expectEnabled: '사용 가능 여부 확인', expectChecked: '체크 여부 확인', expectStyle: '스타일 일치',
};
export const STYLE_PROPERTIES: Record<StyleProperty, string> = {
  color: '글자 색', backgroundColor: '배경 색', fontSize: '글자 크기', fontWeight: '글자 굵기', fontFamily: '글꼴',
  borderRadius: '모서리 둥글기', padding: '안쪽 여백', margin: '바깥 여백', gap: '요소 간격',
  minHeight: '최소 높이', height: '높이', width: '너비', borderColor: '테두리 색', borderWidth: '테두리 두께', display: '표시 방식',
};
export const BOOLEAN_ACTIONS: Action[] = ['check', 'expectVisible', 'expectEnabled', 'expectChecked'];
export const KEYS = ['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'];
export function newStep(action: Action = 'expectVisible'): Step {
  return { action, target: `field_${crypto.randomUUID().replace(/-/g, '').slice(0, 10)}`, targetLabel: '확인할 화면 요소',
    ...(action === 'click' ? {} : { value: BOOLEAN_ACTIONS.includes(action) ? true : action === 'press' ? 'Enter' : '' }),
    ...(action === 'expectText' ? { match: 'contains' as const } : {}),
    ...(action === 'expectStyle' ? { property: 'color' as const } : {}) };
}
export function newRule(): Rule {
  return { id: `R_${crypto.randomUUID().slice(0, 8)}`, title: '화면 요소가 표시된다', required: true, source: { kind: 'manual' }, steps: [newStep()] };
}
export function contractProblems(contract: EditableContract): string[] {
  const problems: string[] = [];
  if (!contract.title.trim()) problems.push('규칙 묶음의 이름을 입력하세요.');
  if (contract.brief.length > 4000) problems.push('만들 화면 설명은 4,000자 이내로 입력하세요.');
  if (!contract.rules.length || contract.rules.length > 20) problems.push('규칙은 1~20개가 필요합니다.');
  if (contract.assetIds.length > 20) problems.push('파일은 최대 20개까지 선택하세요.');
  if (contract.unresolved.some(value => value.trim())) problems.push('확인이 필요한 내용을 모두 해결해야 승인할 수 있습니다.');
  if (!Number.isInteger(contract.viewport.width) || !Number.isInteger(contract.viewport.height) ||
      contract.viewport.width < 320 || contract.viewport.width > 1920 || contract.viewport.height < 480 || contract.viewport.height > 2160) {
    problems.push('화면 너비는 320~1920, 높이는 480~2160 사이의 정수로 입력하세요.');
  }
  if (!contract.rules.some(rule => rule.required)) problems.push('필수 확인 규칙이 최소 한 개 필요합니다.');
  if (contract.bindings !== undefined) {
    if (!contract.bindings || typeof contract.bindings !== 'object' || Array.isArray(contract.bindings) ||
        Object.keys(contract.bindings).length > 100) problems.push('원본 HTML 요소 연결은 최대 100개까지 지정하세요.');
    else for (const [target, selector] of Object.entries(contract.bindings)) {
      if (!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(target) || typeof selector !== 'string' || !selector.trim() ||
          selector.length > 300 || selector.includes('>>') || /[\r\n\0]/.test(selector) || /^(xpath=|text=|javascript:)/.test(selector)) {
        problems.push('고급 설정의 원본 HTML 요소 연결에는 300자 이내의 CSS 선택자를 입력하세요. XPath·실행식·선택자 연결은 사용할 수 없습니다.');
      }
    }
  }
  for (const [index, rule] of contract.rules.entries()) {
    const name = `규칙 ${index + 1}`;
    if (!rule.title.trim()) problems.push(`${name}: 규칙 이름을 입력하세요.`);
    if (!rule.steps.length || rule.steps.length > 20) problems.push(`${name}: 단계는 1~20개가 필요합니다.`);
    if (!rule.steps.some(step => step.action.startsWith('expect'))) problems.push(`${name}: 결과를 확인하는 단계를 추가하세요.`);
    for (const step of rule.steps) {
      if (!(step.action in ACTIONS) || !/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(step.target)) problems.push(`${name}: 고급 설정의 요소 식별자를 확인하세요.`);
      if (!step.targetLabel.trim()) problems.push(`${name}: 화면 요소 이름을 입력하세요.`);
      if (BOOLEAN_ACTIONS.includes(step.action) && typeof step.value !== 'boolean') problems.push(`${name}: 여부를 선택하세요.`);
      if (!BOOLEAN_ACTIONS.includes(step.action) && step.action !== 'click' && typeof step.value !== 'string') problems.push(`${name}: 확인할 값을 입력하세요.`);
      if (step.action === 'expectText' && !step.value) problems.push(`${name}: 확인할 문구를 입력하세요.`);
      if (step.action === 'expectText' && step.normalizeWhitespace !== undefined && typeof step.normalizeWhitespace !== 'boolean')
        problems.push(`${name}: 공백 정규화 여부는 예 또는 아니요로 선택하세요.`);
      if (step.action === 'expectStyle' && (!step.property || !Object.hasOwn(STYLE_PROPERTIES, step.property) ||
          typeof step.value !== 'string' || !step.value.trim())) problems.push(`${name}: 스타일 속성과 기대값을 입력하세요.`);
      if (step.action === 'press' && !KEYS.includes(String(step.value))) problems.push(`${name}: 지원하는 키를 선택하세요.`);
    }
    if (rule.source.kind === 'explicit' && (!rule.source.quote?.trim() || !contract.assetIds.includes(rule.source.assetId || ''))) {
      problems.push(`${name}: 선택한 파일과 원문 근거를 확인하세요.`);
    }
  }
  return [...new Set(problems)];
}
export const roundApprovable = (round?: Round) => round?.passed === true && /^[a-f0-9]{64}$/i.test(round.artifactSha256) &&
  round.hasHtml === true && round.hasReport === true && round.hasScreenshot === true &&
  Array.isArray(round.blockingFindings) && round.blockingFindings.length === 0;
export function ocrLabel(status?: string): string {
  return ({ complete: '문자 인식 완료', partial: '일부만 문자 인식', failed: '문자 인식 실패',
    unavailable: '문자 인식 사용 불가', pending: '문자 인식 대기 중' } as Record<string, string>)[status || ''] || '문자 인식 미확인';
}
export function editable(contract: EditableContract): EditableContract {
  return { schemaVersion: 1, title: contract.title, brief: contract.brief, assetIds: [...contract.assetIds],
    ...Object.fromEntries(['projectId', 'productId', 'guidelineId', 'guidelineAssetId', 'ontologyHash', 'catalogHash']
      .filter(key => contract[key as keyof EditableContract] !== undefined).map(key => [key, contract[key as keyof EditableContract]])),
    viewport: { ...contract.viewport }, rules: structuredClone(contract.rules), unresolved: [...(contract.unresolved || [])],
    ...(contract.bindings !== undefined ? { bindings: { ...contract.bindings } } : {}) };
}
export const manualAssets = (assets: Asset[]) => assets.filter(asset => !asset.system && !asset.archived);
export function applyManualAssets(draft: EditableContract, assets: Asset[], selected: string[]): EditableContract {
  const allowed = new Set(manualAssets(assets).map(asset => asset.id));
  const fixed = draft.assetIds.filter(id => id === draft.guidelineAssetId || assets.some(asset => asset.id === id && asset.system));
  if (draft.guidelineAssetId) fixed.push(draft.guidelineAssetId);
  return { ...draft, assetIds: [...new Set([...selected.filter(id => allowed.has(id)), ...fixed])] };
}
export function importedHtmlAssets(assets: Asset[], contract?: Pick<EditableContract, 'assetIds'> | null): Asset[] {
  return assets.filter(asset => contract?.assetIds.includes(asset.id) && asset.uploadStatus === 'stored' &&
    !asset.system && !asset.archived && /\.html?$/i.test(asset.name));
}
export const isOriginalHtmlCheck = (run: Pick<Run, 'mode' | 'model'>) => run.mode === 'verify' || run.model === 'no-inference';
export function blankContract(assetIds: string[] = [], brief = ''): EditableContract {
  return { schemaVersion: 1, title: '새 화면의 확인 기준', brief, assetIds, viewport: { width: 390, height: 844 }, rules: [newRule()], unresolved: [] };
}
export function stateLabel(state?: string): string {
  return ({ queued: '대기 중', running: '진행 중', completed: '완료', needs_changes: '수정 필요', failed: '실패',
    'review-required': '화면 변형 검토 필요', committed: '커밋 완료',
    ready: '준비 완료', rebuilding: '재빌드 중', exporting: '내보내는 중', unavailable: '사용할 수 없음', needs_revalidation: '재검증 필요',
    passed: '통과', pass: '통과', fail: '실패', verified: '검증됨', not_run: '미검증', not_tested: '미검증',
    not_verified: '미검증', unverified: '미검증', pending: '미검증', incomplete: '미판정',
    'not-run': '미검증', not_compared: '비교하지 않음', skipped: '검사 생략', unsupported: '미지원', approved: '승인됨', draft: '작성 중',
    uploading: '전송 중', processing: '파일 확인 중', stored: '보관 완료', complete: '해석 완료', partial: '일부 해석',
  } as Record<string, string>)[state || ''] || '미확인';
}
