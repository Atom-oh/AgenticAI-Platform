import type { BuildEvidence, Product, Project, Release, Role } from './types';
export const ROLE_LABELS: Record<Role, string> = { owner: '관리자', planner: '기획', designer: '디자인', developer: '개발' };
export type Permission = 'upload' | 'discuss' | 'rules' | 'generate' | 'approve' | 'publish' | 'release' | 'export' | 'members';
export function roleFor(project: Project, actorId?: string): Role | null {
  if (!actorId || !Object.hasOwn(project.members || {}, actorId)) return null;
  const role = project.members[actorId]?.role;
  return Object.hasOwn(ROLE_LABELS, role) ? role : null;
}
export function can(role: Role | null, action: Permission): boolean {
  if (!role) return false;
  if (role === 'owner' || action === 'upload' || action === 'discuss') return true;
  return ({ rules: ['planner', 'designer'], generate: ['designer'], approve: ['designer'],
    publish: ['planner'], release: ['designer', 'developer'], export: ['developer'], members: [] } as Record<string, string[]>)[action]?.includes(role) === true;
}
export const BUILD_LABELS = { policy: '코드 사용 규칙', types: 'React 타입 검사', build: '프로젝트 빌드', components: '고정 컴포넌트' } as const;
export const buildDetails = (evidence?: BuildEvidence) => evidence?.build || evidence;
export function buildPassed(evidence?: BuildEvidence, catalogHash?: string): boolean {
  const build = buildDetails(evidence);
  return !!evidence && [evidence.sourceHash, evidence.bundleHash, evidence.catalogHash, catalogHash]
    .every(value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)) &&
    evidence.catalogHash === catalogHash && build?.ok !== false &&
    (!evidence.build || (build?.ok === true && build.sourceHash === evidence.sourceHash &&
      build.bundleHash === evidence.bundleHash && build.catalogHash === evidence.catalogHash)) &&
    Object.keys(BUILD_LABELS).every(key => build?.gates?.[key as keyof typeof BUILD_LABELS]?.status === 'pass');
}
export function releaseChecksPassed(release?: Release | null) {
  const visual = release?.verification?.visual;
  return !!release && release.status === 'ready' && buildPassed(release, release.catalogHash) &&
    release.verification?.functionalStatus === 'pass' && release.verification?.accessibility?.status === 'pass' &&
    visual?.status === 'pass' && visual.tolerance === 0.02 &&
    typeof visual.changedRatio === 'number' && Number.isFinite(visual.changedRatio) &&
    visual.changedRatio >= 0 && visual.changedRatio <= visual.tolerance;
}
export function generationRequest(mode: 'creative' | 'guided', variationCount: number) {
  if (mode === 'guided' && (!Number.isInteger(variationCount) || variationCount < 2 || variationCount > 5)) throw new Error('변형은 2~5개를 선택하세요.');
  return { mode, ...(mode === 'guided' ? { variationCount } : {}) };
}
export function currentGuideline(product: Product, snapshot: { productId?: string; guidelineId?: string; ontologyHash?: string }) {
  return snapshot.productId === product.id && !!product.publishedGuidelineId && snapshot.guidelineId === product.publishedGuidelineId &&
    typeof product.ontologyHash === 'string' && /^[a-f0-9]{64}$/i.test(product.ontologyHash) && snapshot.ontologyHash === product.ontologyHash;
}
export function safeExternalUrl(value?: string) {
  if (!value) return undefined;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined; } catch { return undefined; }
}
