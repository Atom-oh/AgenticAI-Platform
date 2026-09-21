import { useEffect, useRef, useState } from 'react';
import { messageOf, resource } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { Notice } from './shared';
import { UX_STATES } from './workflow';
import type { ChangeRequest, EditableContract, GuideRef, Run, UXState } from './types';

export const emptyRequest = (): ChangeRequest => ({
  kind: 'new', channel: '', requester: '', dueDate: '', baselineNote: '', preserve: '',
  allowedFiles: [], screens: [], transitions: [],
});
const KINDS = { page: '화면', 'bottom-sheet': '바텀시트', popup: '팝업', tab: '탭', slot: '슬롯' };
const CHANGES = { add: '추가', modify: '수정', keep: '유지', remove: '삭제' };
const nextId = (prefix: string) => prefix + '-' + crypto.randomUUID().slice(0, 8);
const sameRef = (left: GuideRef, right: GuideRef) => left.assetId === right.assetId && left.sourceId === right.sourceId &&
  left.page === right.page && left.sourceSha256 === right.sourceSha256 && left.textSha256 === right.textSha256;
export function requestIssues(draft: EditableContract): string[] {
  const request = draft.changeRequest;
  if (!request) return [];
  const issues: string[] = [];
  if (!request.screens.length) issues.push('변경 대상 화면·슬롯을 하나 이상 정하세요.');
  for (const screen of request.screens) {
    if (screen.sourceRefs?.some(ref => !draft.guideRefs?.some(selected => selected.assetId === ref.assetId &&
      selected.sourceId === ref.sourceId && selected.page === ref.page && selected.sourceSha256 === ref.sourceSha256 && selected.textSha256 === ref.textSha256)))
      issues.push(`${screen.title}: 원문 연결을 현재 선택한 페이지와 다시 대조하세요.`);
    if (screen.change === 'remove') {
      if (!draft.rules.some(rule => rule.required && rule.screenId === screen.id &&
        rule.steps.some(step => step.action === 'expectVisible' && step.target === screen.id && step.value === false) &&
        rule.steps.some(step => step.action === 'expectVisible' && step.target !== screen.id && step.value === true)))
        issues.push(`${screen.title}: 유지되는 화면과 삭제 대상의 미표시를 확인하는 필수 규칙이 필요합니다.`);
      continue;
    }
    if (!screen.states.length) issues.push(`${screen.title}: 확인할 상태를 선택하세요.`);
    for (const state of screen.states) if (!draft.rules.some(rule => rule.required && rule.screenId === screen.id &&
      rule.scenario === state && rule.steps.some(step => step.action === 'expectVisible' && step.target === screen.id && step.value === true)))
      issues.push(`${screen.title} · ${UX_STATES[state].label}: 화면 표시를 포함한 필수 규칙이 필요합니다.`);
  }
  for (const link of request.transitions) {
    if (request.screens.some(screen => screen.change === 'remove' && [link.from, link.to].includes(screen.id))) {
      issues.push(`${link.id}: 삭제 대상 대신 이동할 화면으로 연결을 수정하세요.`); continue;
    }
    if (!link.action || !link.condition || !link.retention) issues.push(`${link.id}: 이동 행동·조건·값 유지 기준을 정하세요.`);
    if (!draft.rules.some(rule => rule.required && rule.transitionId === link.id && rule.steps.some((start, i) =>
      start.action === 'expectVisible' && start.target === link.from && start.value === true &&
      rule.steps.some((end, j) => j > i && end.action === 'expectVisible' && end.target === link.to && end.value === true &&
        rule.steps.slice(i + 1, j).some(step => ['click', 'press', 'select', 'check', 'fill'].includes(step.action))))))
      issues.push(`${link.id}: 출발 화면 → 조작 → 도착 화면 확인이 필요합니다.`);
  }
  if (request.baseline && !request.allowedFiles.length) issues.push('기준 시안에서 변경할 파일을 선택하세요.');
  return issues;
}

export default function ChangeRequestPanel({ draft, runs = [], disabled, stage, onChange, onPending }: {
  draft: EditableContract; runs?: Run[]; disabled: boolean; stage: 'define' | 'design';
  onChange: (draft: EditableContract) => void;
  onPending?: (pending: boolean) => void;
}) {
  const client = useWorkspaceClient();
  const request = draft.changeRequest || emptyRequest();
  const [error, setError] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [newFile, setNewFile] = useState('');
  const operation = useRef<AbortController | null>(null);
  const pendingCallback = useRef(onPending); pendingCallback.current = onPending;
  const latestDraft = useRef(draft); latestDraft.current = draft;
  useEffect(() => () => { operation.current?.abort(); pendingCallback.current?.(false); }, []);
  const update = (change: Partial<ChangeRequest>) => onChange({ ...draft, changeRequest: { ...request, ...change } });
  const eligible = runs.filter(run => run.outputType === 'react' && run.approval && !run.needsRevalidation);
  const chooseBaseline = async (id: string, refreshOnly = false) => {
    operation.current?.abort();
    if (!id) { update({ baseline: undefined, allowedFiles: [] }); setFiles([]); setLoading(false); setError(''); pendingCallback.current?.(false); return; }
    const abort = new AbortController(); operation.current = abort; setLoading(true); setError('');
    pendingCallback.current?.(true);
    const expected = request.baseline;
    try {
      const value = await client.get<{ baseline: ChangeRequest['baseline']; files: string[] }>(
        `/runs/${resource(id)}/baseline${refreshOnly && expected ? `?round=${expected.round}` : ''}`, abort.signal);
      if (!abort.signal.aborted) {
        if (refreshOnly && (!expected || value.baseline?.sourceHash !== expected.sourceHash || value.baseline?.round !== expected.round))
          throw new Error('고정한 기준 시안의 현재 승인을 확인하지 못했습니다. 업무 정의에서 기준을 다시 선택하세요.');
        const latest = latestDraft.current;
        const current = latest.changeRequest || emptyRequest();
        if (current.baseline?.sourceHash !== value.baseline?.sourceHash || current.baseline?.runId !== value.baseline?.runId)
          onChange({ ...latest, changeRequest: { ...current, kind: 'change', baseline: value.baseline, allowedFiles: [] } });
        setFiles(value.files);
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) { setLoading(false); pendingCallback.current?.(false); } }
  };
  useEffect(() => {
    if (request.baseline) void chooseBaseline(request.baseline.runId, true);
  }, [request.baseline?.runId, request.baseline?.round, request.baseline?.sourceHash]);
  const addScreen = (kind: keyof typeof KINDS = 'page') => {
    const id = nextId(kind === 'page' ? 'screen' : kind);
    update({ screens: [...request.screens, { id, kind, title: `${KINDS[kind]} ${request.screens.length + 1}`,
      change: 'add', instruction: '', uiuxId: '', developerId: '', canonicalId: '', sourceNote: '', states: ['entry'] }] });
  };
  const screenChange = (id: string, value: Partial<ChangeRequest['screens'][number]>) =>
    update({ screens: request.screens.map(screen => screen.id === id ? { ...screen, ...value } : screen) });
  const counts = Object.entries(KINDS).map(([kind, label]) => `${label} ${request.screens.filter(screen => screen.kind === kind).length}`);
  return <section className="ws-change-request" aria-label="업무 변경 요청">
    {stage === 'define' && <>
      <div className="ws-request-intro"><h3>어떤 업무를 바꾸나요?</h3><p>기존 화면에서 바뀌는 부분과 유지할 부분을 먼저 정하세요. 원문과 세부 검사는 다음 단계에서 연결합니다.</p></div>
      <fieldset disabled={disabled || loading} className="ws-request-fields">
        <label className="ws-field">요청 유형<select value={request.kind} onChange={event => update({ kind: event.target.value as ChangeRequest['kind'] })}>
          <option value="new">신규 화면</option><option value="change">기존 업무 변경</option><option value="fix">오류·사용성 수정</option></select></label>
        <label className="ws-field">적용 채널<input maxLength={120} value={request.channel} placeholder="예: 모바일 앱"
          onChange={event => update({ channel: event.target.value })} /></label>
        <label className="ws-field">요청 담당<input maxLength={120} value={request.requester} placeholder="담당자 또는 업무 부서"
          onChange={event => update({ requester: event.target.value })} /></label>
        <label className="ws-field">목표일<input type="date" value={request.dueDate} onChange={event => update({ dueDate: event.target.value })} /></label>
        <label className="ws-field ws-request-wide">참고할 기존 업무·화면<textarea rows={2} maxLength={2000} value={request.baselineNote}
          placeholder="어떤 기존 흐름을 따르는지, 설계서와 원본의 어느 부분인지 적어 주세요."
          onChange={event => update({ baselineNote: event.target.value })} /></label>
        <label className="ws-field ws-request-wide">그대로 유지할 내용<textarea rows={2} maxLength={2000} value={request.preserve}
          placeholder="예: 가입 순서는 유지하고 상품 안내와 배너만 수정"
          onChange={event => update({ preserve: event.target.value })} /></label>
      </fieldset>
      <details className="ws-baseline-picker"><summary>승인된 React 시안에서 변경하기</summary>
        <p>같은 작업 공간의 승인 소스를 기준으로 고정합니다. 지정하지 않은 소스 파일의 변경은 차단합니다.</p>
        <label className="ws-field">기준 시안<select aria-label="기준 시안" disabled={disabled || loading} value={request.baseline?.runId || ''}
          onChange={event => void chooseBaseline(event.target.value)}><option value="">기준 소스 없음 · 자료 참고로 설계</option>
          {request.baseline && !eligible.some(run => run.id === request.baseline!.runId) && <option value={request.baseline.runId}>저장된 기준 · 현재 승인 재확인 필요</option>}
          {eligible.map(run => <option key={run.id} value={run.id}>{run.contract?.title || run.id} · 승인 라운드 {run.approval?.round}</option>)}</select></label>
        {request.baseline && <><p>기준 라운드 {request.baseline.round} · 소스 {request.baseline.sourceHash.slice(0, 12)}</p>
          <button disabled={disabled || loading} onClick={() => void chooseBaseline(request.baseline!.runId, true)}>기준 파일 다시 조회</button></>}
      </details>
    </>}
    {error && <Notice error>{error}</Notice>}
    <div className="ws-section-heading"><div><h3>이번에 다룰 화면과 변경점</h3><p>{counts.join(' · ')}</p></div>
      <button disabled={disabled || request.screens.length >= 20} onClick={() => addScreen()}>화면 추가</button></div>
    {!request.screens.length && <div className="ws-request-empty">
      <p>전체 업무를 다시 만들 필요 없이, 변경할 화면·팝업·슬롯을 추가하세요.</p>
      <button disabled={disabled} onClick={() => {
        const screens: ChangeRequest['screens'] = ['상품 안내', '신청 안내', '상품 배너'].map((title, index) => ({
          id: nextId(index === 2 ? 'slot' : 'screen'), title, kind: index === 2 ? 'slot' : 'page', change: 'modify',
          instruction: '', uiuxId: '', developerId: '', canonicalId: '', sourceNote: '', states: ['entry'],
        }));
        update({ kind: 'change', screens });
      }}>두 화면·한 슬롯으로 범위 잡기</button>
      <button disabled={disabled} onClick={() => addScreen('bottom-sheet')}>바텀시트부터 추가</button>
    </div>}
    <div className="ws-scope-cards">{request.screens.map(screen => <article key={screen.id} className={`ws-scope-card change-${screen.change}`}>
      <fieldset disabled={disabled}>
        <div className="ws-request-fields">
          <label className="ws-field">대상 이름<input value={screen.title} maxLength={180} onChange={event => screenChange(screen.id, { title: event.target.value })} /></label>
          <label className="ws-field">대상 유형<select value={screen.kind} onChange={event => screenChange(screen.id, { kind: event.target.value as typeof screen.kind })}>
            {Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="ws-field">변경 범위<select value={screen.change} onChange={event => screenChange(screen.id, { change: event.target.value as typeof screen.change })}>
            {Object.entries(CHANGES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </div>
        <label className="ws-field">무엇을 바꾸거나 유지하나요?<textarea rows={2} maxLength={1000} value={screen.instruction}
          onChange={event => screenChange(screen.id, { instruction: event.target.value })} /></label>
        {screen.change === 'remove' ? <p>유지되는 화면이 표시되고, 이 대상은 더 이상 표시되지 않는지 검증합니다.</p> :
        <details open={stage === 'design'}><summary>이 화면에서 확인할 상태</summary>
          <div className="ws-scope-states">{(Object.entries(UX_STATES) as [UXState, typeof UX_STATES[UXState]][]).map(([state, info]) =>
            <label className="ws-check" key={state}><input type="checkbox" checked={screen.states.includes(state)}
              onChange={() => screenChange(screen.id, { states: screen.states.includes(state) ? screen.states.filter(s => s !== state) : [...screen.states, state] })} />{info.label}</label>)}</div>
        </details>}
        <details><summary>원본 화면 ID·출처 연결</summary><p>다른 이름 공간의 ID는 별도로 기록합니다. 원본의 ‘완료’ 표시는 승인으로 사용하지 않습니다.</p>
          <div className="ws-request-fields">{([['uiuxId', 'UIUX 화면 ID'], ['developerId', '개발 화면 ID'], ['canonicalId', '지식 저장소 ID']] as const).map(([key, label]) =>
            <label key={key} className="ws-field">{label}<input maxLength={128} value={screen[key]} onChange={event => screenChange(screen.id, { [key]: event.target.value })} /></label>)}</div>
          <label className="ws-field">ID 매핑·원본 버전 확인 근거<textarea rows={2} maxLength={1000} value={screen.sourceNote}
            onChange={event => screenChange(screen.id, { sourceNote: event.target.value })} /></label>
          {!!draft.guideRefs?.length && <div><p>이 화면과 연결할 선택 원문</p>{draft.guideRefs.map(ref => {
            const key = `${ref.assetId}:${ref.sourceId}:${ref.page}:${ref.sourceSha256}:${ref.textSha256}`;
            const selected = screen.sourceRefs?.some(item => sameRef(item, ref));
            return <label className="ws-check" key={key}><input type="checkbox" checked={!!selected} onChange={() =>
              screenChange(screen.id, { sourceRefs: selected ? screen.sourceRefs!.filter(item => !sameRef(item, ref)) :
                [...(screen.sourceRefs || []), ref] })} />{ref.sourceId} · {ref.page}페이지 · {ref.sourceSha256.slice(0, 8)}</label>;
          })}</div>}
          {screen.sourceRefs?.filter(ref => !draft.guideRefs?.some(current => current.assetId === ref.assetId &&
            current.sourceId === ref.sourceId && current.page === ref.page && current.sourceSha256 === ref.sourceSha256 &&
            current.textSha256 === ref.textSha256)).map(ref => <div className="ws-notice" key={`${ref.assetId}:${ref.sourceId}:${ref.page}`}>
              선택에서 빠진 원문: {ref.sourceId} · {ref.page}페이지
              <button onClick={() => screenChange(screen.id, { sourceRefs: screen.sourceRefs!.filter(item => item !== ref) })}>이전 원문 연결 제외</button>
            </div>)}
          <small>검사 연결 ID: {screen.id} · 선택한 원문 페이지는 기준·자산에서 연결합니다.</small>
        </details>
        <button onClick={() => {
          const transitions = request.transitions.filter(link => link.from !== screen.id && link.to !== screen.id);
          onChange({ ...draft, changeRequest: { ...request, screens: request.screens.filter(s => s.id !== screen.id), transitions },
            rules: draft.rules.map(rule => ({ ...rule, ...(rule.screenId === screen.id ? { screenId: undefined } : {}),
              ...(rule.transitionId && !transitions.some(link => link.id === rule.transitionId) ? { transitionId: undefined } : {}) })) });
        }}>대상 삭제</button>
      </fieldset>
    </article>)}</div>
    {stage === 'design' && request.screens.length > 0 && <section className="ws-journey">
      <div className="ws-section-heading"><div><h3>고객이 이동하는 흐름</h3><p>조건에 따른 분기와 돌아올 때 유지할 값을 함께 확인합니다.</p></div>
        <button disabled={disabled || request.screens.length < 2 || request.transitions.length >= 30} onClick={() => update({
          transitions: [...request.transitions, { id: nextId('link'), from: request.screens[0].id, to: request.screens[1].id,
            action: '', condition: '', retention: '' }],
        })}>화면 연결 추가</button></div>
      {request.transitions.map(link => <fieldset disabled={disabled} key={link.id} className="ws-transition">
        <div className="ws-transition-path">{(['from', 'to'] as const).map((key, index) => <label className="ws-field" key={key}>
          {index ? '→ 도착 화면' : '출발 화면'}<select value={link[key]} onChange={event => update({ transitions: request.transitions.map(item =>
            item.id === link.id ? { ...item, [key]: event.target.value } : item) })}>{request.screens.map(screen =>
              <option key={screen.id} value={screen.id}>{screen.title}</option>)}</select></label>)}</div>
        {([['action', '사용자가 하는 행동'], ['condition', '이동 조건·예외'], ['retention', '유지하거나 초기화할 값']] as const).map(([key, label]) =>
          <label className="ws-field" key={key}>{label}<input maxLength={1000} value={link[key]} onChange={event => update({
            transitions: request.transitions.map(item => item.id === link.id ? { ...item, [key]: event.target.value } : item),
          })} /></label>)}
        <button onClick={() => onChange({ ...draft, changeRequest: { ...request, transitions: request.transitions.filter(item => item.id !== link.id) },
          rules: draft.rules.map(rule => rule.transitionId === link.id ? { ...rule, transitionId: undefined } : rule) })}>연결 삭제</button>
      </fieldset>)}
    </section>}
    {request.baseline && <details open className="ws-file-scope"><summary>변경을 허용할 소스 파일</summary>
      <p>선택한 경로만 추가·수정·삭제할 수 있습니다. 다른 기준 파일은 바이트 단위로 유지합니다.</p>
      {stage === 'design' && <button disabled={disabled || loading} onClick={() => void chooseBaseline(request.baseline!.runId, true)}>기준 파일 다시 조회</button>}
      {[...new Set([...files, ...request.allowedFiles])].sort().map(path => <label key={path} className="ws-check"><input type="checkbox"
        disabled={disabled || loading} checked={request.allowedFiles.includes(path)} onChange={() => update({ allowedFiles: request.allowedFiles.includes(path) ?
          request.allowedFiles.filter(item => item !== path) : [...request.allowedFiles, path] })} />{path}</label>)}
      <label className="ws-field">추가할 화면·로직 파일<input disabled={disabled || loading} value={newFile} placeholder="src/pages/product-detail.tsx" onChange={event => setNewFile(event.target.value)} /></label>
      <button disabled={disabled || loading || !/^src\/(?:App\.tsx|pages\/[a-z][a-z0-9-]*\.tsx|logic\/[a-z][a-z0-9-]*\.ts)$/.test(newFile) || newFile === 'src/logic/assets.ts'}
        onClick={() => { update({ allowedFiles: [...new Set([...request.allowedFiles, newFile])] }); setNewFile(''); }}>변경 경로 추가</button>
    </details>}
  </section>;
}
