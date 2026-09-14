import type { ReactNode } from 'react';
import type { Job, Role } from './types';

const statuses: Record<string, string> = {
  active: '사용 중', archived: '보관됨', uploading: '전송 중', processing: '본문 추출 중',
  draft: '검토 전', in_review: '검토 요청됨', approved: '원문 승인됨', rejected: '보완 요청',
  failed: '처리 실패', pending: '추출 대기', complete: '본문 추출 완료', partial: '일부만 추출됨',
  unsupported: '추출할 수 없음', empty: '추출된 본문 없음', queued: '대기 중', running: '분석 중',
  needs_sources: '원문 등록 필요', needs_review: '담당자 검토 필요', stale: '원문 변경 · 재분석 필요',
  change_required: '수정 필요', unaffected: '영향 없음', completed: '처리 완료',
};
export function statusLabel(value: string) { return Object.hasOwn(statuses, value) ? statuses[value] : '상태 확인 필요'; }
const kinds: Record<string, string> = {
  regulation: '규정', policy: '업무 기준', report: '보고서', specification: '화면·기능 명세',
  notice: '공문·안내', guide: '가이드', reference: '참고 자료',
};
export function kindLabel(value: string) { return Object.hasOwn(kinds, value) ? kinds[value] : '기타 문서'; }
const roleLabels: Record<Role, string> = { owner: '소유자', planner: '기획 담당자', designer: '디자인 담당자', developer: '개발 담당자' };
export function roleLabel(value: string) { return Object.hasOwn(roleLabels, value) ? roleLabels[value as Role] : '역할 확인 필요'; }
export function Status({ value }: { value: string }) { return <span className="doc-chip" data-status={value}>{statusLabel(value)}</span>; }
export function Sample({ provenance }: { provenance: string }) {
  return provenance === 'synthetic_sample' ? <span className="doc-chip doc-sample">합성 예제 · 실제 내규 아님</span> : null;
}
export function dateLabel(value?: number) {
  if (!value || !Number.isFinite(value)) return '기록 없음';
  const date = new Date(value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '날짜 확인 필요' : date.toLocaleString('ko-KR');
}
export function Progress({ job, label }: { job: Job; label: string }) {
  const raw = typeof job.progress === 'number' ? job.progress : job.progress?.percent;
  const percent = typeof raw === 'number' && Number.isFinite(raw) ? Math.max(0, Math.min(raw, 100)) : undefined;
  return <div className="doc-progress" role="status">
    <span>{label} · {statusLabel(job.status)}{percent !== undefined ? ` · ${percent}%` : ''}</span>
    <progress max={100} value={percent} aria-label={label} />
    {typeof job.progress === 'object' && job.progress?.message && <p>{job.progress.message}</p>}
  </div>;
}
export function Details({ children, title = '기록 상세' }: { children: ReactNode; title?: string }) {
  return <details className="doc-details"><summary>{title}</summary><div>{children}</div></details>;
}
