import { useEffect, useRef, useState } from 'react';
import { useWorkspaceScope } from './WorkspaceScope';
import { messageOf, resource } from './client';
import { Notice } from './shared';

type Scope = { screenId: string; state?: string; targetId?: string; screenTitle?: string; targetTitle?: string };
type Event = { id: string; kind: 'approve' | 'edit' | 'reject'; actor: string; at: number; outcome?: string;
  reason?: string; scopes: Scope[] };
type Data = { artifactId: string; sourceHash: string; bundleHash: string; coverage: string; editRequests: number;
  rejectionCount: number; screenLabels: Record<string, string>; events: Event[];
  failureChecklist: { id: string; code: string; severity: string; rounds: number[]; resolved: boolean }[];
  rejectionCandidates: { eventId: string; queueLabel: string; nodes: { title: string }[] }[] };
const KIND = { approve: '승인', edit: '부분 수정 요청', reject: '반려' };
const OUTCOME: Record<string, string> = { submitted: '접수됨', succeeded: '검증 통과', needs_changes: '추가 수정 필요',
  failed: '실행 중단', cancelled: '취소됨', source_revoked: '원본 권한 회수됨', expired: '실행 만료됨' };
const STATE: Record<string, string> = { default: '기본', entry: '진입', input: '입력', consent: '동의',
  error: '오류', empty: '빈 상태', loading: '로딩', ineligible: '미자격', complete: '완료', back: '뒤로', cancel: '취소' };

export default function DesignInterventions({ designId, version, variant, onReviewed, onDirty }: {
  designId: string; version: number; variant: { artifactId: string; sourceHash: string; bundleHash: string };
  onReviewed: () => void; onDirty: (dirty: boolean) => void;
}) {
  const { client, role, project } = useWorkspaceScope();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Data | null>(null);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const action = useRef<AbortController | null>(null);
  const pending = useRef<{ reason: string; body: Record<string, unknown> } | null>(null);
  const path = `/designs/${resource(designId)}/artifacts/${resource(variant.artifactId)}/interventions`;
  useEffect(() => {
    const abort = new AbortController(); setData(null); setError('');
    if (open) client.get<Data>(path, abort.signal).then(value => {
      if (abort.signal.aborted) return;
      if (value.artifactId !== variant.artifactId || value.sourceHash !== variant.sourceHash || value.bundleHash !== variant.bundleHash) {
        setError('선택한 시안의 검토 기준이 변경되었습니다.'); return;
      }
      setData(value);
    }).catch(value => { if (!abort.signal.aborted) setError(messageOf(value)); });
    return () => abort.abort();
  }, [client, path, open, refresh, version, variant.artifactId, variant.sourceHash, variant.bundleHash]);
  useEffect(() => { onDirty(!!reason.trim() || busy); }, [reason, busy, onDirty]);
  useEffect(() => () => { action.current?.abort(); onDirty(false); }, [onDirty]);
  const reject = async () => {
    if (busy || !reason.trim() || !(role === 'owner' || role === 'designer')) return;
    const abort = new AbortController(); action.current?.abort(); action.current = abort;
    if (pending.current?.reason !== reason.trim()) pending.current = {
      reason: reason.trim(), body: { requestId: crypto.randomUUID(), version, artifactId: variant.artifactId,
        sourceHash: variant.sourceHash, bundleHash: variant.bundleHash, reason: reason.trim() },
    };
    setBusy(true); setError('');
    try {
      await client.post(`/designs/${resource(designId)}/reject`, pending.current.body, abort.signal);
      if (!abort.signal.aborted) { setReason(''); pending.current = null; setRefresh(value => value + 1); onReviewed(); }
    } catch (value) { if (!abort.signal.aborted) setError(messageOf(value)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <details className="ws-context-discussion" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>사람의 수정·반려와 개선 후보</summary>
    {error && <Notice error>{error} <button onClick={() => setRefresh(value => value + 1)}>개입 이력 다시 조회</button></Notice>}
    {data && <>
      {data.coverage !== 'complete' && <p className="ws-muted">계측 도입 이전의 개입은 모두 확인할 수 없습니다. 기록된 이력만 표시합니다.</p>}
      <p>부분 수정 요청 {data.editRequests}회 · 반려 {data.rejectionCount}회</p>
      <ul className="ws-comments">{data.events.map(event => <li key={event.id}>
        <strong>{KIND[event.kind]} · {project?.members[event.actor]?.displayName || '참여자'}</strong>
        {event.outcome && <p>{OUTCOME[event.outcome] || '결과 확인 필요'}</p>}
        {event.scopes.map((scope, index) => <p key={index}>{scope.screenTitle || data.screenLabels[scope.screenId] || '선택한 화면'}
          {scope.targetTitle ? ` · ${scope.targetTitle}` : ''}{scope.state ? ` · ${STATE[scope.state] || '선택한 상태'}` : ''}</p>)}
        {event.reason && <p>{event.reason}</p>}
      </li>)}</ul>
      {!!data.failureChecklist.length && <details><summary>자료·PRD 보강 체크리스트</summary>
        {data.failureChecklist.map(item => <p key={item.id}>{item.code} · {item.rounds.join(', ')}회차 · {item.resolved ? '마지막 회차에서 해결됨' : '확인 필요'}</p>)}
      </details>}
      {data.rejectionCandidates.map(candidate => <div key={candidate.eventId}>
        <h4>온톨로지 보강 후보 · {candidate.queueLabel}</h4>
        {candidate.nodes.map((node, index) => <p key={index}>{node.title}</p>)}
        <p className="ws-muted">업무 원본을 연결하고 사람이 검토해야 규칙으로 사용할 수 있습니다.</p>
      </div>)}
    </>}
    {(role === 'owner' || role === 'designer') && <>
      <label className="ws-field">시안 반려 사유<textarea value={reason} maxLength={1000} rows={3}
        onChange={event => setReason(event.target.value)} /></label>
      <button disabled={busy || !reason.trim() || !data} onClick={() => void reject()}>이 시안 반려·보강 후보 기록</button>
      {reason && <button disabled={busy} onClick={() => { setReason(''); pending.current = null; }}>반려 사유 작성 취소</button>}
    </>}
  </details>;
}
