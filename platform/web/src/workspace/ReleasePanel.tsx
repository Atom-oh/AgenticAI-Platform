import { useEffect, useRef, useState } from 'react';
import { listAll, messageOf, resource } from './client';
import { useWorkspaceScope } from './WorkspaceScope';
import { BUILD_LABELS, buildDetails, buildPassed, can, currentGuideline, releaseChecksPassed, safeExternalUrl } from './project';
import { hasExactApproval } from './VerificationLoop';
import { JobProgress, Notice, useDownload } from './shared';
import { stateLabel } from './rules';
import type { BuildEvidence, Job, Product, Release, Selection } from './types';

export function BuildGates({ evidence, catalogHash }: { evidence?: BuildEvidence; catalogHash?: string }) {
  const allPassed = buildPassed(evidence, catalogHash);
  const details = buildDetails(evidence);
  return <section className="ws-build-gates" aria-label="React 빌드 검사"><h3>React 프로젝트 검사</h3>
    <p className={allPassed ? 'ws-good' : ''}>{allPassed ? '고정 컴포넌트·타입·빌드 검사 통과' : '전체 빌드 통과 근거 미확인'}</p>
    <dl>{Object.entries(BUILD_LABELS).map(([key, label]) => <div key={key}><dt>{label}</dt>
      <dd>{stateLabel(details?.gates?.[key as keyof typeof BUILD_LABELS]?.status)}</dd></div>)}</dl>
    <details><summary>컴포넌트·소스·번들 식별</summary><dl>
      <dt>컴포넌트</dt><dd>{evidence?.catalogHash || '미확인'}</dd>
      <dt>소스</dt><dd>{evidence?.sourceHash || '미확인'}</dd><dt>번들</dt><dd>{evidence?.bundleHash || '미확인'}</dd>
    </dl></details>
    {!!details?.diagnostics?.length && <Notice error><ul>{details.diagnostics.map((value, index) =>
      <li key={index}>{typeof value === 'string' ? value : typeof value === 'object' && value !== null && 'message' in value ? String(value.message) : '상세 빌드 근거를 확인하세요.'}</li>)}</ul></Notice>}
  </section>;
}

export default function ReleasePanel({ selection, product, initialReleaseId, onRelease }: {
  selection: Selection; product?: Product; initialReleaseId?: string; onRelease?: (id: string) => void;
}) {
  const { client, role, project } = useWorkspaceScope();
  const [connections, setConnections] = useState<{ id: string; label: string; repository?: string; visibility?: string; connectionHash: string }[] | null>(null);
  const [connectionId, setConnectionId] = useState('');
  const [publicConfirmed, setPublicConfirmed] = useState(false);
  const [release, setRelease] = useState<Release | null>(null);
  const [releaseId, setReleaseId] = useState(initialReleaseId || '');
  const [releases, setReleases] = useState<Release[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const [trackedProduct, setTrackedProduct] = useState<Product | null>(null);
  const [productChecked, setProductChecked] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const requestId = useRef('');
  const gitRequest = useRef({ key: '', id: '' });
  const downloading = useDownload();
  const run = selection?.run, round = selection?.round;
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { if (initialReleaseId) setReleaseId(initialReleaseId); }, [initialReleaseId]);
  useEffect(() => {
    const abort = new AbortController();
    if (run?.outputType === 'react' && round) listAll<Release>(client, 'releases', abort.signal).then(items => {
      if (abort.signal.aborted) return;
      const matching = items.filter(item => item.runId === run.id && item.round === round.number);
      setReleases(matching); setReleaseId(previous => previous || matching[0]?.id || '');
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, run?.id, run?.outputType, round?.number, retry]);
  useEffect(() => {
    const abort = new AbortController(); setConnections(null);
    client.get<{ connections: NonNullable<typeof connections> }>('/git-connections', abort.signal)
      .then(value => {
        if (abort.signal.aborted) return;
        if (!Array.isArray(value.connections)) throw new Error('Git 연결 설정을 확인하지 못했습니다.');
        setConnections(value.connections); setConnectionId(previous => value.connections.some(item => item.id === previous) ? previous : value.connections[0]?.id || '');
      }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, retry]);
  useEffect(() => {
    const abort = new AbortController(); setTrackedProduct(null); setProductChecked(false);
    if (project && run?.productId) client.get<{ product: Product }>(`/products/${resource(run.productId)}`, abort.signal)
      .then(value => { if (!abort.signal.aborted && value.product.id === run.productId) { setTrackedProduct(value.product); setProductChecked(true); } })
      .catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    else setProductChecked(true);
    return () => abort.abort();
  }, [client, project?.id, run?.productId, product?.publishedGuidelineId, retry]);
  useEffect(() => {
    const abort = new AbortController();
    setRelease(previous => previous?.id === releaseId ? previous : null);
    if (releaseId) client.get<{ release: Release }>(`/releases/${resource(releaseId)}`, abort.signal)
      .then(value => {
        if (abort.signal.aborted) return;
        if (value.release.id !== releaseId || value.release.runId !== run?.id || value.release.round !== round?.number) throw new Error('선택한 라운드의 릴리스가 아닙니다.');
        setRelease(value.release);
      }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, releaseId, retry, run?.id, round?.number]);
  useEffect(() => {
    if (release?.jobId && ['queued', 'running'].includes(release.status)) setJob(previous =>
      previous?.id === release.jobId ? previous : { id: release.jobId!, task: 'release', status: release.status as 'queued' | 'running' });
  }, [release?.id, release?.jobId, release?.status]);
  useEffect(() => {
    if (release?.git?.jobId && ['queued', 'running'].includes(release.git.status)) setJob(previous =>
      previous?.id === release.git?.jobId ? previous : { id: release.git!.jobId!, task: 'git', status: release.git!.status as 'queued' | 'running' });
  }, [release?.git?.jobId, release?.git?.status]);
  const selectedConnection = connections?.find(item => item.id === connectionId);
  const publicDestination = !selectedConnection?.visibility || ['public', 'unknown'].includes(selectedConnection.visibility);
  useEffect(() => setPublicConfirmed(false), [connectionId, selectedConnection?.connectionHash, releaseId]);
  const stale = run?.needsRevalidation === true || !!(project && run?.productId && (!productChecked ||
    !trackedProduct || !currentGuideline(trackedProduct, run)));
  const ready = !!run && !!round && run.outputType === 'react' && buildPassed(round, run.catalogHash) && hasExactApproval(run, round) && !stale;
  const matchingRelease = !!release && release.id === releaseId && release.runId === run?.id && release.round === round?.number &&
    release.sourceHash === round?.sourceHash && release.bundleHash === round?.bundleHash &&
    release.catalogHash === run?.catalogHash && release.contractHash === run?.contractHash &&
    (release.guidelineId ?? undefined) === (run?.guidelineId ?? undefined);
  const releasable = ready && matchingRelease && releaseChecksPassed(release) &&
    !!run && hasExactApproval({ ...run, approval: release?.approval }, round);
  const mutate = async (git = false) => {
    if (busy || !can(role, git ? 'export' : 'release') || !ready ||
        (git && (!releasable || !selectedConnection?.connectionHash || publicDestination && !publicConfirmed))) return;
    const abort = new AbortController(); controller.current?.abort(); controller.current = abort; setBusy(true); setError('');
    try {
      if (!requestId.current) requestId.current = crypto.randomUUID();
      const gitKey = `${releaseId}:${connectionId}:${selectedConnection?.connectionHash || ''}`;
      if (gitRequest.current.key !== gitKey) gitRequest.current = { key: gitKey, id: crypto.randomUUID() };
      const value = await client.post<{ release?: Release; job?: Job }>(git ? `/releases/${resource(release!.id)}/git` : '/releases',
        git ? { connectionId, connectionHash: selectedConnection!.connectionHash, confirmPublic: publicDestination ? publicConfirmed : undefined,
          requestId: gitRequest.current.id } : { runId: run!.id, round: round!.number, requestId: requestId.current }, abort.signal);
      if (abort.signal.aborted) return;
      if (value.release) {
        if (value.release.runId !== run?.id || value.release.round !== round?.number) throw new Error('릴리스의 원본 라운드를 확인하지 못했습니다.');
        setRelease(value.release); setReleaseId(value.release.id); onRelease?.(value.release.id);
      }
      if (value.job) setJob(value.job);
      if (!value.release && !value.job) throw new Error('릴리스 작업 정보를 확인하지 못했습니다.');
      setRetry(value => value + 1);
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <section className="ws-release"><h3>승인한 React 프로젝트</h3>
    {!run || !round ? <Notice>생성·검수 탭에서 시안과 라운드를 선택하세요.</Notice> : <>
      <p>선택한 라운드 {round.number} · {run.outputType === 'react' ? 'React 프로젝트' : 'HTML 프로토타입'}</p>
      {run.outputType === 'react' ? <BuildGates evidence={round} catalogHash={run.catalogHash} /> :
        <Notice>기존 HTML 이력은 프로토타입입니다. React 프로젝트 빌드·릴리스를 승인한 기록으로 사용할 수 없습니다.</Notice>}
      {!!round.pageSources?.length && <details><summary>화면과 React 소스 연결</summary><dl>
        {round.pageSources.map((page, index) => <div key={page.pageId}><dt>화면 {index + 1}</dt><dd>{page.pageId} · {page.path}</dd></div>)}
      </dl></details>}
      {stale && <Notice error>게시 지침과 이 시안의 기준이 다르거나 최신 연결을 확인하지 못했습니다. 새 기준으로 재검증해야 내보낼 수 있습니다.</Notice>}
      <p>{hasExactApproval(run, round) ? '이 라운드의 정확한 산출물 승인 기록 있음' : '이 라운드의 사람 승인 기록 미확인'}</p>
      <p className="ws-muted">AI를 다시 호출하지 않고 승인된 소스를 재빌드·검증합니다. 디자이너가 승인한 시작 화면과 다시 비교하며 허용 차이는 2%입니다.</p>
      {!can(role, 'release') && <Notice>디자인·개발·관리자가 승인된 산출물의 재빌드·릴리스를 준비할 수 있습니다.</Notice>}
      <button disabled={!ready || !can(role, 'release') || busy || !!job && ['queued', 'running'].includes(job.status)}
        onClick={() => void mutate()}>승인 소스 재빌드·릴리스 검사</button>
      {releases.length > 0 && <label className="ws-field">이 라운드의 저장된 릴리스<select aria-label="이 라운드의 저장된 릴리스"
        value={releaseId} disabled={busy} onChange={event => { setJob(null); setReleaseId(event.target.value); }}>
        {release && !releases.some(item => item.id === release.id) && <option value={release.id}>현재 릴리스 · {stateLabel(release.status)}</option>}
        {releases.map((item, index) => <option value={item.id} key={item.id}>릴리스 {index + 1} · {stateLabel(item.status)}</option>)}
      </select></label>}
      {job && <JobProgress job={job} label="릴리스·Git 작업" onComplete={value => {
        setJob(value); if (value.result?.releaseId) { setReleaseId(value.result.releaseId); onRelease?.(value.result.releaseId); } setRetry(previous => previous + 1);
      }} onFailure={value => { setJob(value); setRetry(previous => previous + 1); }} />}
      {release && <div className="ws-release-record"><h4>저장된 릴리스</h4><p>{stateLabel(release.status)}</p>
        <p>{releasable ? '선택한 승인 소스의 릴리스 검사 통과 기록 있음' : '선택한 승인 소스의 릴리스 통과 근거 미확인'}</p>
        {!matchingRelease && <Notice error>선택한 승인 소스와 릴리스 식별이 일치하지 않습니다.</Notice>}
        {release.error && <Notice error>{release.error}</Notice>}
        <p>재검증 동작: {stateLabel(release.verification?.functionalStatus)} · 접근성: {stateLabel(release.verification?.accessibility?.status)}</p>
        <p>승인 시작 화면 재비교: {stateLabel(release.verification?.visual?.status)}
          {typeof release.verification?.visual?.changedRatio === 'number' && ` · 차이 ${(release.verification.visual.changedRatio * 100).toFixed(2)}%`}</p>
        <div className="ws-actions">{(['source', 'dist', 'manifest', 'report'] as const).map(kind => {
          const flag = ({ source: release.hasSource, dist: release.hasDist, manifest: release.hasManifest, report: release.hasReport })[kind];
          return <button key={kind} disabled={!matchingRelease || flag !== true || downloading.busy}
            onClick={() => void downloading.download(`/releases/${resource(release.id)}/blob?kind=${kind}`, `react-${kind}.${kind === 'source' || kind === 'dist' ? 'zip' : 'json'}`)}>
            {{ source: 'React 소스 ZIP', dist: 'S3 배포용 번들 ZIP', manifest: '릴리스 목록', report: '재검증 근거' }[kind]}</button>;
        })}</div>
        <details><summary>릴리스·재검증 근거</summary><dl><dt>소스</dt><dd>{release.sourceHash || '미확인'}</dd>
          <dt>번들</dt><dd>{release.bundleHash || '미확인'}</dd></dl><pre className="ws-text">{release.build || release.verification ?
            JSON.stringify({ build: release.build, verification: release.verification }, null, 2) : '재검증 근거 미확인'}</pre></details>
      </div>}
    </>}
    <h4>실제 Git 기능 브랜치</h4>
    <p>{connections === null ? 'Git 연결 설정 미확인' : connections.length === 0 ? '설정된 Git 저장소가 없습니다. 연결 전에는 브랜치 내보내기를 할 수 없습니다.' : '등록된 저장소의 기능 브랜치로 내보냅니다.'}</p>
    {!!connections?.length && <label className="ws-field">등록된 Git 저장소<select aria-label="등록된 Git 저장소" value={connectionId} onChange={event => setConnectionId(event.target.value)}>
      {connections.map(item => <option key={item.id} value={item.id}>{item.label} · {item.visibility || '공개 범위 미확인'}</option>)}</select></label>}
    {selectedConnection && publicDestination && <label className="ws-check"><input type="checkbox" checked={publicConfirmed}
      disabled={busy} onChange={event => setPublicConfirmed(event.target.checked)} />대상 저장소의 공개 범위를 확인했고 이 승인본을 내보내겠습니다</label>}
    <button disabled={!releasable || !can(role, 'export') || !selectedConnection?.connectionHash || publicDestination && !publicConfirmed || busy || !!job && ['queued', 'running'].includes(job.status)}
      onClick={() => void mutate(true)}>기능 브랜치로 내보내기</button>
    {release?.git && <div><p>Git 작업 · {stateLabel(release.git.status)}</p>
      <dl>{release.git.branch && <><dt>실제 브랜치</dt><dd>{release.git.branch}</dd></>}
        {release.git.commitSha && <><dt>실제 커밋</dt><dd>{release.git.commitSha}</dd></>}</dl>
      {release.git.criteriaCurrent === false && <Notice error>커밋 기록은 보존되지만 현재 기획·승인 기준은 바뀌었습니다. 최신 기준으로 재검증하세요.</Notice>}
      {release.git.criteriaStatus === 'unavailable' && <Notice>커밋은 완료됐지만 최신 기준 확인을 다시 조회해야 합니다.</Notice>}
      {release.git.error && <Notice error>{release.git.error}</Notice>}
      {safeExternalUrl(release.git.commitUrl || undefined) && <a href={safeExternalUrl(release.git.commitUrl || undefined)} target="_blank" rel="noopener noreferrer">저장된 커밋 열기</a>}
      {safeExternalUrl(release.git.filesUrl || undefined) && <a href={safeExternalUrl(release.git.filesUrl || undefined)} target="_blank" rel="noopener noreferrer">저장된 파일 열기</a>}
    </div>}
    {(error || downloading.error) && <Notice error>{error || downloading.error}</Notice>}
    <button onClick={() => setRetry(value => value + 1)}>개발 기록 다시 조회</button>
  </section>;
}
