import type { EditableContract, Role, Rule, UXState } from './types';

export const WORKFLOW_STEPS = [
  { id: 'define', label: '업무 정의', purpose: '기존 업무에서 무엇을 바꾸고 유지하나요?', output: '변경 요청·대상 화면·기준 시안' },
  { id: 'assets', label: '기준·자산', purpose: '화면 ID와 원문으로 변경 근거를 연결하세요.', output: '설계서·코드·가이드·원본 상태' },
  { id: 'design', label: '흐름·상태 설계', purpose: '화면별 변경점과 조건에 따른 이동을 확인하세요.', output: '변경 범위·상태·기준 승인' },
  { id: 'review', label: '시안·검수', purpose: '실제 화면에서 동작을 확인하고 수정하세요.', output: '검수 근거·시안 승인' },
  { id: 'handoff', label: '개발 전달', purpose: '승인한 소스를 재현하고 개발팀에 넘기세요.', output: 'React 소스·검증 보고서·Git 커밋' },
] as const;
export type WorkflowStep = typeof WORKFLOW_STEPS[number]['id'];
export const UX_STATES: Record<UXState, { label: string; prompt: string }> = {
  entry: { label: '첫 진입', prompt: '진입 조건과 처음 보여 줄 정보는 무엇인가요?' },
  input: { label: '입력·선택', prompt: '입력값과 선택 결과가 다음 화면으로 이어지나요?' },
  consent: { label: '동의', prompt: '필수·선택 동의와 진행 조건이 구분되나요?' },
  error: { label: '오류·수정', prompt: '언제 오류를 알리고, 어떻게 고치면 해제되나요?' },
  empty: { label: '결과 없음', prompt: '데이터가 없을 때 이유와 다음 행동을 안내하나요?' },
  loading: { label: '처리 중', prompt: '처리 상태와 중복 요청 방지 방법이 명확한가요?' },
  back: { label: '이전·재진입', prompt: '이전 화면에 돌아왔을 때 무엇을 유지하나요?' },
  cancel: { label: '취소·이탈', prompt: '계속하기와 취소 후 돌아갈 곳이 정해졌나요?' },
  complete: { label: '완료', prompt: '완료 정보, 다음 행동과 이전 화면 접근이 정해졌나요?' },
};
export const DESIGN_STARTERS: { label: string; brief: string; states: UXState[] }[] = [
  { label: '입력·신청 흐름', brief: '사용자가 필요한 정보를 입력하고, 동의와 확인을 거쳐 신청을 마치는 흐름을 설계합니다.',
    states: ['entry', 'input', 'consent', 'error', 'back', 'cancel', 'complete'] },
  { label: '조회·상세 흐름', brief: '사용자가 조건을 선택해 결과를 조회하고, 상세 정보를 확인하는 흐름을 설계합니다.',
    states: ['entry', 'input', 'loading', 'empty', 'error', 'back'] },
  { label: '안내·콘텐츠 화면', brief: '사용자가 핵심 안내를 이해하고, 다음 행동을 선택할 수 있는 화면을 설계합니다.',
    states: ['entry', 'complete'] },
];
export function stateRules(rules: Rule[], state: UXState) {
  return rules.filter(rule => rule.scenario === state);
}
export function stateCoverageIssues(contract: Pick<EditableContract, 'rules' | 'requiredStates'>): string[] {
  if (contract.requiredStates === undefined) return [];
  if (!Array.isArray(contract.requiredStates) || contract.requiredStates.length > Object.keys(UX_STATES).length ||
      new Set(contract.requiredStates).size !== contract.requiredStates.length ||
      contract.requiredStates.some(state => !Object.hasOwn(UX_STATES, state))) return ['검토할 UX 상태를 다시 선택하세요.'];
  return contract.requiredStates.filter(state => !stateRules(contract.rules, state).some(rule =>
    rule.required && rule.steps.some(step => step.action.startsWith('expect'))))
    .map(state => `${UX_STATES[state].label} 상태에 연결된 필수 검증 규칙을 작성하세요.`);
}
const safeId = (value: string | null) => value && /^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$/.test(value) ? value : '';
export function readWorkflowRoute(hash: string) {
  const params = new URLSearchParams(hash.split('?')[1] || '');
  const stage = params.get('step');
  return {
    invalid: ['projectId', 'project', 'productId', 'runId', 'assetId', 'contractId'].some(key => !!params.get(key) && !safeId(params.get(key))) ||
      (!!params.get('round') && !/^[1-5]$/.test(params.get('round')!)),
    projectId: safeId(params.get('projectId') || params.get('project')),
    productId: safeId(params.get('productId')), runId: safeId(params.get('runId')), assetId: safeId(params.get('assetId')),
    contractId: safeId(params.get('contractId')),
    round: /^[1-5]$/.test(params.get('round') || '') ? Number(params.get('round')) : undefined,
    step: WORKFLOW_STEPS.some(step => step.id === stage) ? stage as WorkflowStep : undefined,
  };
}
export function initialWorkflowStep(route: ReturnType<typeof readWorkflowRoute>, role: Role | null, legacy?: string): WorkflowStep {
  return route.step || (route.runId ? 'review' : route.assetId ? 'assets' : route.contractId ? 'design' :
    legacy === 'files' || legacy === 'guides' ? 'assets' : role === 'developer' ? 'handoff' : 'define');
}
export function workflowHash(hash: string, selection: {
  projectId: string; productId?: string; step?: WorkflowStep; runId?: string; round?: number; contractId?: string;
}) {
  const route = hash.split('?')[0] === '#/portal' ? '#/portal' : '#/studio';
  const params = new URLSearchParams();
  if (route === '#/portal') params.set('tab', 'guides');
  for (const [key, value] of Object.entries(selection)) if (value !== undefined && value !== '') params.set(key, String(value));
  const query = params.toString();
  return route + (query ? '?' + query : '');
}
