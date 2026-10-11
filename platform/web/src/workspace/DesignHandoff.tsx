import { useEffect, useRef, useState } from 'react';
import { useWorkspaceScope } from './WorkspaceScope';
import { messageOf, resource } from './client';
import { JobProgress, Notice } from './shared';
import type { Job } from './types';

type GitReceipt = { status: string; sourceHash: string; branch?: string; commitSha?: string; baseSha?: string;
  commitUrl?: string; criteriaCurrent?: boolean | null; criteriaStatus?: string; error?: string };
type Release = { id: string; version: number; status: string; sourceHash: string; bundleHash: string;
  approvalHash: string; git?: GitReceipt; frontendAcceptance?: { actor: string; sourceHash: string; bundleHash: string } };
type Connection = { id: string; label: string; repository: string; visibility: string; connectionHash: string };
const digest = async (bytes: Uint8Array) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes)))].map(v => v.toString(16).padStart(2, '0')).join('');
export default function DesignHandoff({ designId, releaseIds }: { designId: string; releaseIds: string[] }) {
  const { client, role } = useWorkspaceScope();
  const [release, setRelease] = useState<Release | null>(null);
  const [selected, setSelected] = useState('');
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connectionError, setConnectionError] = useState('');
  const current = useRef({ designId, selected }); current.current = { designId, selected };
  const [connectionId, setConnectionId] = useState('');
  const [confirmPublic, setConfirmPublic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const operation = useRef<AbortController | null>(null);
  const receiptRequest = useRef<AbortController | null>(null);
  const loadVersion = useRef(0);
  const pending = useRef<{ key: string; requestId: string } | null>(null);
  const allowed = role === 'owner' || role === 'developer';
  const load = async (id: string, signal?: AbortSignal) => {
    const version = ++loadVersion.current;
    const result = await client.get<{ release: Release }>(`/designs/${resource(designId)}/releases/${resource(id)}`, signal);
    if (!signal?.aborted && version === loadVersion.current && current.current.designId === designId && current.current.selected === id) {
      setRelease(result.release); return result.release;
    }
    return null;
  };
  useEffect(() => { if (!releaseIds.includes(selected)) setSelected(releaseIds.at(-1) || ''); }, [releaseIds.join(','), selected]);
  useEffect(() => {
    operation.current?.abort(); receiptRequest.current?.abort(); setBusy(false); setJob(null); setNote(''); setRelease(null); setError('');
    if (!selected) return;
    const abort = new AbortController();
    void load(selected, abort.signal).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [selected, designId, client]);
  useEffect(() => {
    const abort = new AbortController();
    void client.get<{ connections: Connection[] }>('/git-connections', abort.signal).then(value => {
      if (!abort.signal.aborted) { setConnections(value.connections); setConnectionError(''); }
    }).catch(reason => { if (!abort.signal.aborted) { setConnections([]); setConnectionError(messageOf(reason)); } });
    return () => abort.abort();
  }, [client]);
  useEffect(() => () => { operation.current?.abort(); receiptRequest.current?.abort(); }, []);
  useEffect(() => {
    if (!release?.git || !['queued', 'running'].includes(release.git.status)) return;
    const abort = new AbortController();
    const timer = setInterval(() => void load(release.id, abort.signal).catch(reason => {
      if (!abort.signal.aborted) setError(messageOf(reason));
    }), 2000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [release?.id, release?.git?.status, designId, client]);
  const refreshReceipt = () => {
    if (!selected) return;
    const abort = new AbortController(); receiptRequest.current?.abort(); receiptRequest.current = abort;
    void load(selected, abort.signal).then(value => {
      if (value?.git?.status === 'committed' && value.git.sourceHash === value.sourceHash && /^[a-f0-9]{40}$|^[a-f0-9]{64}$/.test(value.git.commitSha || '')) pending.current = null;
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
  };
  const download = async (kind: 'handoff' | 'source') => {
    if (!release || busy) return;
    const abort = new AbortController(); operation.current = abort; setBusy(true); setError('');
    try {
      const parts: Uint8Array[] = []; let offset = 0, expected = '', total = 0;
      do {
        const value = await client.get<{ data: string; offset: number; total: number; sha256: string; chunkHash: string; complete: boolean }>(
          `/designs/${resource(designId)}/releases/${resource(release.id)}?kind=${kind}&offset=${offset}`, abort.signal);
        if (value.offset !== offset || value.total > 40 * 1024 * 1024 || value.total <= 0 ||
            (offset && (expected !== value.sha256 || total !== value.total))) throw new Error('전달 파일 범위 또는 해시가 바뀌었습니다.');
        const bytes = Uint8Array.from(atob(value.data), c => c.charCodeAt(0));
        if (!bytes.length || bytes.length > 256 * 1024 || await digest(bytes) !== value.chunkHash) throw new Error('전달 조각의 무결성을 확인하지 못했습니다.');
        expected = value.sha256; total = value.total; offset += bytes.length; parts.push(bytes);
        if (value.complete !== (offset === total) || offset > total) throw new Error('전달 파일이 완전하지 않습니다.');
      } while (offset < total);
      const bytes = new Uint8Array(total); let at = 0; for (const part of parts) { bytes.set(part, at); at += part.length; }
      if (await digest(bytes) !== expected) throw new Error('전체 전달 파일 해시가 다릅니다.');
      if (abort.signal.aborted) return;
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }));
      const link = document.createElement('a'); link.href = url; link.download = `${kind}-${release.id}.zip`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000); setNote('모든 조각과 전체 해시를 확인한 ZIP을 저장했습니다.');
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  const accept = async () => {
    if (!release || !allowed || busy) return;
    setBusy(true); setError(''); const abort = new AbortController(); operation.current = abort;
    try {
      await client.post(`/designs/${resource(designId)}/releases/${resource(release.id)}/accept`, {
        version: release.version, sourceHash: release.sourceHash, bundleHash: release.bundleHash, note: '플랫폼 소스 패키지 검토 완료',
      }, abort.signal);
      if (abort.signal.aborted) return;
      await load(release.id, abort.signal); if (abort.signal.aborted) return; setNote('이 소스·화면 버전에 대한 개발자 확인을 기록했습니다.');
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  const connection = connections.find(item => item.id === connectionId);
  const requiresPublic = connection?.visibility !== 'private';
  const exportGit = async () => {
    if (!release || !allowed || !release.frontendAcceptance || !connection || busy || (requiresPublic && !confirmPublic)) return;
    setBusy(true); setError(''); const abort = new AbortController(); operation.current = abort;
    const key = JSON.stringify([release.id, connection.id, connection.connectionHash]);
    pending.current = pending.current?.key === key ? pending.current : { key, requestId: crypto.randomUUID() };
    try {
      const result = await client.post<{ job: Job }>(`/releases/${resource(release.id)}/git`, {
        requestId: pending.current.requestId, connectionId: connection.id, connectionHash: connection.connectionHash, confirmPublic,
      }, abort.signal);
      if (!abort.signal.aborted) {
        setJob(result.job); await load(release.id, abort.signal);
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  const git = release?.git;
  const verifiedCommit = !!git && git.status === 'committed' && git.sourceHash === release?.sourceHash && /^[a-f0-9]{40}$|^[a-f0-9]{64}$/.test(git.commitSha || '');
  let commitUrl: string | undefined;
  try { const url = new URL(git?.commitUrl || ''); if (url.protocol === 'https:' && !url.username && !url.password) commitUrl = url.href; } catch {}
  const gitLabel = !git ? '' : verifiedCommit ? 'Git 커밋 확인됨' : ({ queued: 'Git 전달 대기', running: 'Git 전달 중',
    failed: 'Git 전달 실패', 'delivered-unverified': 'Git 전달 결과 확인 필요' }[git.status] || 'Git 전달 상태 확인 필요');
  if (!releaseIds.length) return null;
  return <section aria-label="검증된 개발 전달" className="ws-section">
    <h3>검증된 개발 전달</h3><label className="ws-field">전달 버전<select value={selected} onChange={e => setSelected(e.target.value)}>
      {releaseIds.map((id, index) => <option key={id} value={id}>전달 {index + 1}</option>)}</select></label>
    {release && <><p>승인된 소스를 그대로 재빌드하고 화면·동작을 재검증했습니다.</p>
      <button disabled={busy} onClick={() => void download('handoff')}>전달 ZIP 다운로드</button>
      <button disabled={busy} onClick={() => void download('source')}>재현용 소스 ZIP</button>
      <p>플랫폼 컴포넌트 기준입니다. 고객 API·인증·실제 배포 연동 확인은 별도입니다.</p>
      <button disabled={busy || !allowed || !!release.frontendAcceptance} onClick={() => void accept()}>
        {release.frontendAcceptance ? '개발자 확인 완료' : '이 전달 버전 개발자 확인'}</button>
      <label className="ws-field">등록된 Git 전달 대상<select value={connectionId} onChange={e => { setConnectionId(e.target.value); setConfirmPublic(false); }}>
        <option value="">저장소 선택</option>{connections.map(item => <option key={item.id} value={item.id}>{item.label || item.repository}</option>)}</select></label>
      {connectionError && <Notice error>Git 연결 상태를 확인하지 못했습니다: {connectionError}</Notice>}
      {!connections.length && !connectionError && <p>관리자가 등록한 Git 연결이 없습니다. ZIP 전달은 사용할 수 있습니다.</p>}
      {connection && requiresPublic && <label><input type="checkbox" checked={confirmPublic} onChange={e => setConfirmPublic(e.target.checked)} />대상의 공개 범위를 확인했습니다.</label>}
      <button disabled={busy || !allowed || !release.frontendAcceptance || !connection || (requiresPublic && !confirmPublic)} onClick={() => void exportGit()}>등록된 저장소로 전달</button>
    </>}
    {git && <section aria-label="Git 전달 결과"><p role="status">{gitLabel}</p>
      {git.branch && <p>브랜치: <code>{git.branch}</code></p>}
      {git.commitSha && <p>커밋: <code>{git.commitSha}</code></p>}
      {commitUrl && <a href={commitUrl} target="_blank" rel="noopener noreferrer">기록된 Git 커밋 열기</a>}
      {git.baseSha && <p>기준 커밋: <code>{git.baseSha}</code></p>}
      {git.criteriaCurrent === false && <Notice error>커밋 이후 설계 기준이 변경되었습니다.</Notice>}
      {git.criteriaCurrent === null && <Notice>현재 설계 기준 상태를 확인 중입니다.</Notice>}
      {git.error && <Notice error>{git.error}</Notice>}
      <button onClick={refreshReceipt}>Git 결과 새로 조회</button>
    </section>}
    {job && <JobProgress job={job} label="Git 전달" onComplete={refreshReceipt} onFailure={refreshReceipt} />}
    {error && <Notice error>{error}</Notice>}{note && <Notice>{note}</Notice>}
  </section>;
}
