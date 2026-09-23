import { useEffect, useRef, useState } from 'react';
import { useWorkspaceScope } from './WorkspaceScope';
import { messageOf } from './client';
import { can } from './project';
import { JobProgress, Notice } from './shared';
import type { GuideRef, Job, Product } from './types';

export default function CanvasRequest({ model, available, assetIds, guideRefs, product, onReady, onDirty }: {
  model: string; available: boolean; assetIds: string[]; guideRefs: GuideRef[]; product?: Product;
  onReady: (id: string) => void; onDirty: (dirty: boolean) => void;
}) {
  const { client, role, project } = useWorkspaceScope();
  const [brief, setBrief] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const action = useRef<AbortController | null>(null);
  const pending = useRef<{ key: string; payload: Record<string, unknown> } | null>(null);
  const locked = useRef(false);
  const allowed = can(role, 'rules') && (!project || !!product?.publishedGuidelineId);
  useEffect(() => { onDirty(!!brief.trim() || busy); }, [brief, busy, onDirty]);
  useEffect(() => () => action.current?.abort(), []);
  const prepare = async () => {
    if (!allowed || !available || !brief.trim() || locked.current) return;
    locked.current = true; setBusy(true); setError(''); setJob(null);
    const controller = new AbortController(); action.current?.abort(); action.current = controller;
    const fields = { brief: brief.trim(), model, assetIds, ...(guideRefs.length ? { guideRefs } : {}),
      ...(product ? { productId: product.id, guidelineId: product.publishedGuidelineId } : {}) };
    const key = JSON.stringify(fields);
    const payload = pending.current?.key === key ? pending.current.payload : { ...fields, requestId: crypto.randomUUID() };
    pending.current = { key, payload };
    try {
      const result = await client.post<{ job: Job }>('/contracts/propose', payload, controller.signal);
      if (controller.signal.aborted) return;
      if (!result.job?.id) throw new Error('요청 접수 기록을 확인하지 못했습니다. 다시 시도하세요.');
      setJob(result.job);
    } catch (reason) {
      if (!controller.signal.aborted) { setError(messageOf(reason)); setBusy(false); locked.current = false; }
    }
  };
  return <div className="ws-canvas-request">
    <label className="ws-field">무엇을 만들까요?
      <textarea rows={4} maxLength={4000} value={brief} disabled={busy || !can(role, 'rules')}
        placeholder="예: 환전 신청 화면. 받을 금액을 먼저 보여주고 우대 쿠폰을 강조해 주세요."
        onChange={event => setBrief(event.target.value)} /></label>
    {brief.trim() && <><button className="ws-primary" disabled={!allowed || !available || busy}
      onClick={() => void prepare()}>{busy ? '확인 기준 준비 중…' : '이 요청으로 시작하기'}</button>
      <p className="ws-muted">AI가 제안한 확인 기준을 검토·승인하면 시안을 만듭니다.</p></>}
    {!allowed && project && <p className="ws-muted">게시된 상품 지침을 선택한 뒤 시작하세요.</p>}
    {error && <Notice error>{error}</Notice>}
    {job && <JobProgress job={job} label="요청에서 확인 기준 준비" onComplete={result => {
      const id = result.result?.contractId;
      locked.current = false; setBusy(false); setJob(null); pending.current = null;
      if (!id) { setError('완료된 요청의 확인 기준을 찾지 못했습니다. 다시 시도하세요.'); return; }
      setBrief(''); onReady(id);
    }} onFailure={() => { locked.current = false; setBusy(false); pending.current = null; }} />}
  </div>;
}
