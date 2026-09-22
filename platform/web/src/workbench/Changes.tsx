import { useState } from 'react';
import type { Release, Run } from '../workspace/types';
import { listPages, listWorkbench, resource, wb } from './client';
import { ActionState, array, Details, Empty, Field, label, LoadState, object, parseArray, Section, Status, text, useAction, useLoad, useWorkbench } from './shared';
import type { Change, Graph, JsonRecord, Task } from './types';

export function Changes() {
  const { client, params, navigate } = useWorkbench();
  const state = useLoad(signal => listWorkbench<Change>(client, '/changes', signal), [client]);
  const graph = useLoad(signal => client.get<Graph>(wb('/dependencies'), signal), [client]);
  const [selected, setSelected] = useState(params.get('changeId') || '');
  const [title, setTitle] = useState(''), [target, setTarget] = useState(params.get('targetId') || '');
  const targetNode = !graph.loading && !graph.error ? graph.data?.nodes.find(node => node.id === target) : undefined;
  const [changeType, setChangeType] = useState('update'), [before, setBefore] = useState(''), [after, setAfter] = useState(''), [reason, setReason] = useState('');
  const [analysis, setAnalysis] = useState<JsonRecord | null>(null);
  const action = useAction();
  const change = state.data?.find(item => item.id === selected);
  const savedImpact = useLoad(signal => change?.impactHash ?
    client.get<{ impact: JsonRecord }>(wb(`/changes/${resource(change.id)}/impact`), signal) : Promise.resolve(null), [client, change?.id, change?.impactHash]);
  const impact = analysis?.impact || savedImpact.data?.impact || change?.impact;
  const impactData = object(impact);
  const impacts = array(impactData.items || impactData.impacts || impactData.affected);
  return <div className="wb-stack"><div className="wb-two-column">
    <Section title="변경 요청" description="기준안과 제안안을 저장한 뒤 영향 분석을 실행합니다.">
      <LoadState state={state}><div className="wb-record-list">{state.data?.map(item =>
        <button className={`wb-record${item.id === selected ? ' is-selected' : ''}`} aria-pressed={item.id === selected} key={item.id}
          onClick={() => { setSelected(item.id); setAnalysis(null); navigate('changes', { changeId: item.id, targetId: item.targetId }); }}>
          <strong>{item.title}</strong><span>v{item.version} · {label(item.status || 'draft')}</span></button>)}</div>
        {!state.data?.length && <Empty>아직 변경 요청이 없습니다. 변경하려는 기준을 작성하세요.</Empty>}</LoadState>
      <button onClick={() => { setSelected(''); setAnalysis(null); }}>새 변경 작성</button>
    </Section>
    <Section title={change ? change.title : '새 변경 작성'} description="연결 정보가 없는 범위는 영향 없음으로 판단하지 않습니다.">
      {change ? <>
        <div className="wb-diff"><div><h3>기준안</h3><pre>{typeof change.before === 'string' ? change.before : JSON.stringify(change.before, null, 2)}</pre></div>
          <div><h3>제안안</h3><pre>{typeof change.after === 'string' ? change.after : JSON.stringify(change.after, null, 2)}</pre></div></div>
        <p><strong>변경 이유</strong> · {change.reason}</p>
        <button className="wb-primary" disabled={action.busy} onClick={() => void action.run(signal =>
          client.post<{ change: Change; impact: JsonRecord; tasks: Task[] }>(wb(`/changes/${resource(change.id)}/analyze`), { version: change.version }, signal),
        result => { setAnalysis(result); state.refresh(); }, '영향 분석 결과를 조회했습니다.')}>영향 분석 실행</button>
      </> : <form onSubmit={event => { event.preventDefault(); if (!targetNode) return;
        const fields = { title, targetId: targetNode.id, changeType, before, after, reason, ...(params.get('productId') ? { productId: params.get('productId') } : {}) };
        void action.run(signal => client.post<{ change: Change }>(wb('/changes'), { ...fields, requestId: action.requestId(JSON.stringify(fields)) }, signal),
          result => { setSelected(result.change.id); navigate('changes', { changeId: result.change.id, targetId: result.change.targetId }); state.refresh(); }); }}>
        <fieldset disabled={action.busy}>
          <Field label="변경 제목"><input required maxLength={180} value={title} onChange={event => setTitle(event.target.value)} placeholder="예: 연금 안내 문구와 가입 조건 개정" /></Field>
          <div className="wb-form-grid"><Field label="변경 대상" hint={`연결 정보에서 확인한 변경 대상을 선택하세요. ${target && !targetNode ?
            '전달된 대상은 현재 그래프에서 확인되지 않았습니다.' : '상품 맥락은 유지되며 변경 대상은 자동 연결하지 않습니다.'}`}>
            <select required value={targetNode?.id || ''} disabled={graph.loading || !!graph.error || !graph.data?.nodes.length}
              onChange={event => setTarget(event.target.value)}>
              <option value="">변경 대상 선택</option>{graph.data?.nodes.map(node => <option key={node.id} value={node.id}>{node.title || node.id} · {node.label}</option>)}
            </select></Field>
            <Field label="변경 유형"><select value={changeType} onChange={event => setChangeType(event.target.value)}>
              <option value="update">기존 내용 변경</option><option value="add">새 기준 추가</option><option value="remove">기준 삭제</option>
              <option value="deprecate">사용 중단</option><option value="policy">정책·규칙 개정</option>
            </select></Field></div>
          <div className="wb-form-grid"><Field label="기준안"><textarea required rows={4} value={before} onChange={event => setBefore(event.target.value)} /></Field>
            <Field label="제안안"><textarea required rows={4} value={after} onChange={event => setAfter(event.target.value)} /></Field></div>
          <Field label="변경 이유"><textarea required rows={2} value={reason} onChange={event => setReason(event.target.value)} /></Field>
          {graph.error && <LoadState state={graph}>{null}</LoadState>}
          {!graph.loading && !graph.error && !graph.data?.nodes.length && <p className="wb-muted">선택할 연결 항목이 없습니다. 지식 원본을 수집하거나 가상 예제를 준비한 뒤 다시 조회하세요.</p>}
          <button className="wb-primary" disabled={!title.trim() || !targetNode}>변경 요청 저장</button>
        </fieldset>
      </form>}
      <ActionState action={action} />
      {savedImpact.error && <LoadState state={savedImpact}>{null}</LoadState>}
    </Section>
  </div>
    {impact && <Section title="영향 범위와 근거 경로" description="확인된 영향·검토 후보·알 수 없는 범위를 구분합니다.">
      {impacts.length ? <div className="wb-table-wrap"><table><thead><tr><th>영향 대상</th><th>담당 역할</th><th>근거</th><th>신뢰 구분</th><th>다음 작업</th></tr></thead>
        <tbody>{impacts.map((entry, index) => { const item = object(entry), node = object(item.target);
          const targetId = text(item.targetId || node.id, '');
          return <tr key={targetId || index}><td>{text(item.title || node.title || item.targetId)}</td><td>{label(text(item.role))}</td>
            <td>{text(item.reason)}<Details title="연결 경로·원본 버전" value={{ path: item.witnessPath || item.path,
              sourceRef: item.sourceRef, sourceRefs: item.sourceRefs, sourceRevision: item.sourceRevision,
              evidenceKind: item.evidenceKind, targetEvidence: item.targetEvidence,
              evidenceStates: item.evidenceStates, staleWitness: item.staleWitness }} /></td>
            <td><Status value={text(item.confidence || item.confidenceClass, 'unknown')} /></td>
            <td><button onClick={() => navigate(item.role === 'developer' ? 'development' : item.role === 'designer' ? 'studio' : 'planning', {
              changeId: selected, targetId, impactHash: text(impactData.hash || impactData.impactHash, ''),
            })}>관련 작업</button></td></tr>;
        })}</tbody></table></div> : <Empty>표시할 영향 항목이 없습니다. 아래 범위 근거와 미확인 연결을 함께 확인하세요.</Empty>}
      <Details title="분석 범위·미해결 연결·기준" value={impact} />
      <div className="wb-actions"><button onClick={() => navigate('development', { changeId: selected })}>생성된 작업 확인</button>
        <button onClick={() => navigate('reports', { changeId: selected })}>영향 보고서 만들기</button></div>
    </Section>}
  </div>;
}

export function TaskList({ tasks, refresh }: { tasks: Task[]; refresh: () => void }) {
  return <div className="wb-task-list">{tasks.map(task => <TaskRow key={`${task.id}:${task.version}`} task={task} refresh={refresh} />)}
    {!tasks.length && <Empty>현재 조건에 해당하는 작업이 없습니다.</Empty>}</div>;
}
function TaskRow({ task, refresh }: { task: Task; refresh: () => void }) {
  const { client, navigate, project, overview } = useWorkbench();
  const action = useAction();
  const [status, setStatus] = useState(task.status), [evidence, setEvidence] = useState(JSON.stringify(task.evidenceRefs || [], null, 2));
  const [assignee, setAssignee] = useState(task.assigneeSub || '');
  const allowed = overview.role === 'owner' || overview.role === task.role;
  return <article className="wb-task"><div className="wb-row"><div><span className="wb-eyebrow">{label(task.role)} · v{task.version}</span><h3>{task.title}</h3></div><Status value={task.status} /></div>
    <p className="wb-muted">대상 {task.targetId} · 변경 {task.changeId}</p>
    {task.evidenceKind && <Details title="영향 근거의 관찰·검토 상태" value={{ classification: task.evidenceKind,
      target: task.targetEvidence, states: task.evidenceStates, staleWitness: task.staleWitness }} />}
    <div className="wb-actions"><button onClick={() => navigate(task.role === 'designer' ? 'studio' : task.role === 'planner' ? 'planning' : 'components',
      { changeId: task.changeId, targetId: task.targetId, impactHash: task.impactHash })}>대상 작업 열기</button>
      <button onClick={() => navigate('changes', { changeId: task.changeId, targetId: task.targetId })}>변경 근거 확인</button></div>
    <details><summary>작업 상태·완료 근거 제출</summary><form onSubmit={event => { event.preventDefault(); void action.run(signal =>
      client.put(wb(`/tasks/${resource(task.id)}`), { version: task.version, status, assigneeSub: assignee || null, evidenceRefs: parseArray(evidence, '완료 근거') }, signal), refresh); }}>
      {!allowed && <p className="wb-muted">해당 역할의 참여자 또는 프로젝트 관리자만 상태를 변경할 수 있습니다.</p>}
      <fieldset disabled={action.busy || !allowed}><Field label={`${task.title} 담당자`}><select value={assignee} onChange={event => setAssignee(event.target.value)}>
        <option value="">미배정</option>{Object.entries(project.members).filter(([, member]) => member.role === 'owner' || member.role === task.role).map(([id, member]) =>
          <option key={id} value={id}>{member.displayName || id}</option>)}</select></Field>
      <Field label={`${task.title} 상태`}><select value={status} onChange={event => setStatus(event.target.value)}>
        <option value="open">대기</option><option value="in-progress">진행 중</option><option value="blocked">차단됨</option><option value="done">완료</option>
      </select></Field><Field label={`${task.title} 완료 근거 JSON`} hint="완료에는 해당 작업과 연결된 실제 검증 근거가 필요합니다."><textarea rows={3} value={evidence} onChange={event => setEvidence(event.target.value)} /></Field>
      {task.sourceRefs?.length ? <button type="button" onClick={() => setEvidence(JSON.stringify(task.sourceRefs, null, 2))}>이 작업의 원본 근거 입력</button> : null}
      <button disabled={action.busy || !allowed}>작업 상태 저장</button></fieldset></form></details><ActionState action={action} /></article>;
}

export function Development() {
  const { client, params, navigate, overview } = useWorkbench();
  const [role, setRole] = useState(''), [status, setStatus] = useState('');
  const query = new URLSearchParams({ ...(role ? { role } : {}), ...(status ? { status } : {}) }).toString();
  const tasks = useLoad(signal => listWorkbench<Task>(client, '/tasks' + (query ? '?' + query : ''), signal), [client, query]);
  const runs = useLoad(async signal => {
    const [runs, releases] = await Promise.all([listPages<Run>(client, '/runs', 'runs', signal), listPages<Release>(client, '/releases', 'releases', signal)]);
    return { runs, releases };
  }, [client]);
  const filtered = (tasks.data || []).filter(task => !params.get('changeId') || task.changeId === params.get('changeId'));
  return <div className="wb-stack"><Section title="담당 작업" description={`현재 역할: ${label(overview.role)} · 상태 변경은 서버가 권한과 근거를 확인합니다.`}>
    <div className="wb-toolbar"><Field label="담당 역할"><select value={role} onChange={event => setRole(event.target.value)}><option value="">모든 역할</option>
      <option value="planner">기획자</option><option value="designer">디자이너</option><option value="developer">개발자</option></select></Field>
      <Field label="작업 상태"><select value={status} onChange={event => setStatus(event.target.value)}><option value="">모든 상태</option>
        <option value="open">대기</option><option value="in-progress">진행 중</option><option value="blocked">차단됨</option><option value="done">완료</option></select></Field>
      <button onClick={tasks.refresh}>작업 새로 조회</button></div>
    <LoadState state={tasks}><TaskList tasks={filtered} refresh={tasks.refresh} /></LoadState></Section>
    <Section title="React 검증·개발 전달" description="검증된 빌드와 승인, 릴리스 결과를 함께 확인하세요." action={<button onClick={() => navigate('studio')}>스튜디오에서 검증·릴리스</button>}>
      <LoadState state={runs}>{runs.data?.runs.filter(run => run.outputType === 'react').map(run =>
        <article className="wb-artifact" key={run.id}><div className="wb-row"><h3>{run.contract?.title || run.id}</h3><Status value={run.status} /></div>
          <p>소스 라운드 {run.bestRound || '미선택'} · {run.approval ? '승인 기록 있음' : '미승인'}</p>
          <Details title="빌드·기능 검사·릴리스 근거" value={{ rounds: run.rounds, approval: run.approval, releases: runs.data?.releases.filter(release => release.runId === run.id) }} />
          <button onClick={() => navigate('studio', { runId: run.id })}>이 실행의 검증 확인</button></article>)}
        {!runs.data?.runs.some(run => run.outputType === 'react') && <Empty>저장된 React 실행이 없습니다.</Empty>}</LoadState>
    </Section></div>;
}
