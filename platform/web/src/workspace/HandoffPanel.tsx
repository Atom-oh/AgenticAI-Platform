import { useEffect, useState } from 'react';
import { messageOf, resource } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { buildPassed } from './project';
import { hasExactApproval } from './VerificationLoop';
import ReleasePanel from './ReleasePanel';
import { Notice } from './shared';
import { UX_STATES } from './workflow';
import type { Contract, Product, Run, Selection } from './types';

export default function HandoffPanel({ runs, contracts, selection, product, initialRunId, initialRound, onReview, onSelection }: {
  runs: Run[]; contracts: Contract[]; selection: Selection; product?: Product; initialRunId?: string; initialRound?: number;
  onReview: (runId?: string, round?: number) => void;
  onSelection?: (selection: Selection) => void;
}) {
  const client = useWorkspaceClient();
  const [id, setId] = useState(initialRunId || selection?.run.id || '');
  const [loadedRun, setRun] = useState<Run | null>(null);
  const run = loadedRun?.id === id && (!product || loadedRun.productId === product.id) ? loadedRun : null;
  const [number, setNumber] = useState(initialRound || selection?.round?.number || 0);
  const [pageId, setPageId] = useState(selection?.pageId || '');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const reactRuns = runs.filter(item => item.outputType === 'react');
  const currentVersion = runs.find(item => item.id === id)?.version;
  useEffect(() => {
    if (initialRunId) {
      setId(initialRunId); setNumber(initialRound || 0);
      setPageId(selection?.run.id === initialRunId && (!initialRound || selection.round?.number === initialRound) ? selection.pageId || '' : '');
    }
  }, [initialRunId, initialRound]);
  useEffect(() => {
    const abort = new AbortController(); setRun(null); setError('');
    if (id) client.get<{ run: Run }>(`/runs/${resource(id)}`, abort.signal).then(value => {
      if (abort.signal.aborted) return;
      if (value.run.id !== id || value.run.outputType !== 'react' || (product && value.run.productId !== product.id))
        throw new Error('선택한 작업의 React 시안을 확인하지 못했습니다.');
      setRun(value.run);
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, id, product?.id, currentVersion, retry]);
  const round = number ? run?.rounds.find(item => item.number === number) :
    run?.rounds.find(item => item.number === run.approval?.round) || run?.rounds.find(item => item.number === run.bestRound);
  const approved = hasExactApproval(run, round);
  const selectedPage = round?.pageSources?.some(page => page.pageId === pageId) ? pageId : round?.pageSources?.[0]?.pageId;
  const selected: Selection = run ? { run, round, pageId: selectedPage } : null;
  useEffect(() => { onSelection?.(run ? { run, round, pageId: selectedPage } : null); }, [run, round, selectedPage, onSelection]);
  return <section className="ws-section ws-handoff">
    <div className="ws-section-heading"><div><h2>개발팀에 전달할 승인본</h2>
      <p>동일한 소스를 재빌드하고, 실행 코드와 확인 기준을 함께 넘깁니다.</p></div>
      <button onClick={() => onReview(id || undefined, number || round?.number)}>시안·검수로 돌아가기</button></div>
    {!reactRuns.length && !id && <div className="ws-empty"><h3>전달할 React 시안을 먼저 선택하세요</h3>
      <p>시안·검수에서 생성한 React 결과를 확인하고 디자이너가 해당 라운드를 승인합니다.</p></div>}
    <div className="ws-handoff-select">
      <label className="ws-field">전달할 React 시안<select aria-label="전달할 React 시안" value={id}
        onChange={event => { setId(event.target.value); setNumber(0); setPageId(''); }}><option value="">시안 선택</option>
        {id && !reactRuns.some(item => item.id === id) && <option value={id}>연결된 시안 확인 중</option>}
        {reactRuns.map(item => <option key={item.id} value={item.id}>{item.contract?.title || contracts.find(contract => contract.id === item.contractId)?.title || item.id}
          {item.approval ? ' · 승인 기록 있음' : ' · 미승인'}</option>)}</select></label>
      {run && <label className="ws-field">전달할 라운드<select aria-label="전달할 라운드" value={number || round?.number || ''}
        onChange={event => { setNumber(Number(event.target.value)); setPageId(''); }}>
        {number > 0 && !round && <option value={number}>라운드 {number} · 찾을 수 없음</option>}{run.rounds.map(item =>
          <option key={item.number} value={item.number}>라운드 {item.number}{hasExactApproval(run, item) ? ' · 정확한 승인 기록 있음' : ' · 승인 확인 필요'}</option>)}</select></label>}
    </div>
    {error && <Notice error>{error} <button onClick={() => setRetry(value => value + 1)}>전달 대상 다시 조회</button></Notice>}
    {run && number > 0 && !round && <Notice error>연결된 라운드를 찾지 못했습니다. 다른 승인본으로 자동 전환하지 않습니다.</Notice>}
    {run && <div className="ws-handoff-criteria">
      <article><span>고정된 기준</span><strong>규칙 v{run.contractVersion}</strong><p>{run.contract?.title || '저장된 규칙 기준'}</p></article>
      <article><span>React 빌드</span><strong>{buildPassed(round, run.catalogHash) ? '빌드 근거 있음' : '빌드 근거 미확인'}</strong>
        <p>{run.contract?.requiredStates?.map(state => UX_STATES[state]?.label || state).join(' · ') || '상태별 분류 기록 없음'}</p></article>
      <article><span>UX 승인</span><strong>{approved ? '선택한 라운드 승인됨' : '승인 확인 필요'}</strong>
        <p>{run.needsRevalidation ? '기준 변경 · 재검증 필요' : '릴리스 검사는 아래에서 별도로 수행합니다.'}</p></article>
    </div>}
    {run?.contract?.changeRequest && <section className="ws-handoff-delta"><h3>변경 요청과 개발 인수 범위</h3>
      <p>{run.contract.changeRequest.channel || '채널 미정'} · {run.contract.changeRequest.requester || '담당 미정'}
        {run.contract.changeRequest.dueDate ? ` · 목표 ${run.contract.changeRequest.dueDate}` : ''}</p>
      <p>유지할 내용: {run.contract.changeRequest.preserve || '별도 기록 없음'}</p>
      {run.contract.changeRequest.screens.map(screen => <article key={screen.id}><strong>{screen.title}</strong>
        <p>{screen.instruction || '변경 설명 미기록'}</p><small>UIUX {screen.uiuxId || '미연결'} · 개발 {screen.developerId || '미연결'} · 지식 {screen.canonicalId || '미연결'}</small></article>)}
      <h4>실제 생성 소스 변경</h4>
      {round?.fileChanges?.length ? <ul>{round.fileChanges.map(file => <li key={file.path}>
        {({ added: '추가', modified: '수정', deleted: '삭제' } as Record<string, string>)[file.change] || file.change} · {file.path}
      </li>)}</ul> : <p>이 라운드의 소스 변경 근거가 없습니다.</p>}
      <p>플랫폼 React 패키지 기준입니다. 고객 SDK·API·Native 연동과 개발팀 인수는 확인되지 않았습니다.</p>
      <p>재빌드 후 릴리스 목록에는 기준 소스, 전체 전달 파일의 변경·해시, ID 매핑과 원문 참조가 포함됩니다.</p>
    </section>}
    <ReleasePanel key={`${id}:${round?.number || ''}:${round?.artifactSha256 || ''}`} selection={selected} product={product} />
    <section className="ws-handoff-package"><h3>인수할 내용과 개발팀의 다음 작업</h3>
      <div><article><strong>React 소스 ZIP</strong><p>화면·상태 로직, 사용한 UI 코드·토큰, 고정 의존성과 업무 테스트가 포함됩니다.</p></article>
        <article><strong>검증 보고서·배포 번들</strong><p>실제 통과 범위와 승인 기준을 확인합니다. 시작 화면 재비교는 모든 상태의 시각 검증을 뜻하지 않습니다.</p></article>
        <article><strong>개발팀 통합</strong><p>고객 컴포넌트, API·인증·라우팅을 연결하고 팀의 CI와 코드 리뷰를 진행합니다. Git 내보내기는 기능 브랜치·커밋까지입니다.</p></article></div>
    </section>
  </section>;
}
