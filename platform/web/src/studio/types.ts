// platform/web/src/studio/types.ts
export type Draft = { draftId: string; jobId: string; title: string; axis: string; outputType: string; productCode: string; productName: string;
  score: number; passed: boolean; rounds: number; bestRound: number; stopReason?: string; status: '검토중' | '승인됨' | '반려'; comment?: string;
  parentId?: string; url: string; createdAt: number; createdBy: string; model?: string };
export type Asset = { name: string; type: string; version: string; actor: string; updated_at: string; scope: string; asset_id?: string };
export type Product = { code: string; name: string; category: string; conditionCount: number; stepCount: number; hasPreferential: boolean };
export type SpecItem = { id: string; category: string; text: string; required: boolean; weight: number; check: 'llm' | 'text' | 'dom';
  expect?: Record<string, any> | null; source?: { nodeId: string; label: string } | null };
export type Spec = { productCode: string; productName: string; category: string; hasPreferential: boolean; outputType: string;
  conditions: { id: string; type: string; name: string }[]; steps: { screenId: string; name: string; entryCondition: string }[];
  terms: string[]; brandHex: string[]; items: SpecItem[] };
export type ReviewItem = SpecItem & { verdict: 'pass' | 'fail' | null; evidence: string; fix: string };
export type StageEvent = { type: 'studio.stage'; step: string; round?: number; score?: number; passed?: boolean; items?: ReviewItem[];
  requiredFailed?: string[]; undetermined?: string[]; reviewerError?: string | null; url?: string; failures?: any[]; [k: string]: any };
export type RoundResult = { round: number; score: number; passed: boolean; url: string; failures: { id: string; text: string; evidence: string; fix: string }[];
  undetermined: string[]; reviewerError?: string | null; elapsedMs: number };
export type DoneEvent = { type: 'studio.done'; jobId: string; draftId?: string | null; score: number; passed: boolean; rounds: number; maxRounds: number;
  passScore: number; stopReason: 'passed' | 'max_rounds' | 'time_cap' | 'error'; bestRound: number; url: string; items: ReviewItem[];
  history: RoundResult[]; usage: { inputTokens: number; outputTokens: number }; model: string; route?: string; elapsedMs: number; error?: string;
  itemsTruncated?: boolean; recovered?: boolean;
  backend?: string; graphBackend?: string; spec?: { productCode: string; productName: string; hasPreferential: boolean; stepCount: number } };
export type JobForm = { brief: string; productCode: string; outputType: string; axis: string; assetIds: string[]; agentId: string;
  maxRounds: number; passScore: number };

export const ASSET_TYPES = ['token', 'palette', 'icon-set', 'component', 'style-guide', 'skill', 'workflow', 'agent'];
export const TYPE_LABEL: Record<string, string> = { palette: '팔레트', token: '토큰', 'icon-set': '아이콘', component: '컴포넌트',
  'style-guide': '스타일가이드', skill: '스킬', workflow: '워크플로우', agent: '에이전트' };
export const OUTPUT_TYPES: [string, string][] = [['design', '디자인'], ['mockup', '목업'], ['wireframe', '와이어프레임'], ['ux-flow', 'UX 플로우']];
export const AXES = ['밀도', '강조', '흐름'];
export const BRIEF_PRESETS: { label: string; brief: string; productCode: string; outputType: string }[] = [
  { label: '축구사랑 적금 가입 플로우', brief: '아톰 축구사랑 적금 모바일 가입 플로우 — 축구클럽 회원 우대금리 인증 단계를 포함한 전체 흐름', productCode: 'PRD-DEP-001', outputType: 'ux-flow' },
  { label: '기본 적금 가입 플로우', brief: '아톰 기본 적금 모바일 가입 플로우 — 조건 없는 표준 5단계', productCode: 'PRD-DEP-002', outputType: 'ux-flow' },
  { label: '축구사랑 적금 상품안내', brief: '아톰 축구사랑 적금 상품안내 단일 화면 — 기본금리·우대금리 구분 표기, 가입하기 CTA', productCode: 'PRD-DEP-001', outputType: 'design' },
];
export const STEP_LABEL: Record<string, string> = {
  spec_build: '① DesignSpec — 온톨로지(상품 조건·절차·정책·용어) → 체크리스트',
  assets: '② 자산·스킬·승인 참고 시안 로드',
  generate: '③ 시안 생성 (Bedrock · 익명화 게이트 경유 · 토큰 스트리밍)',
  review: '④ 검수 — 결정적 검사(구조·문구) + 리뷰 에이전트 판정 → 점수',
  regenerate: '⑤ 수정 재생성 — 실패 항목·수정 지시 주입',
  publish: '⑥ 라운드 시안 저장',
};
export const aid = (a: Asset) => a.asset_id || `${a.type}:${a.name.trim().replace(/\s+/g, '-')}`;
