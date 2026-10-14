import { useEffect, useRef, useState } from 'react';
import { listPages, resource } from './client';
import { ActionState, Details, Empty, Field, label, LoadState, Section, useAction, useLoad, useWorkbench } from './shared';

type Screen = { id: string; title: string; reviewState: string; type: string; tombstone?: boolean };
type Input = { analysisId: string; name: string; collectionDecisionId: string; backend: string };
type Artifact = { id: string; name?: string; status: string; jobId?: string; createdBy?: string };
type Proposal = { artifact: Artifact; operation: string; version?: number; resultHash?: string;
  result?: { proposals: { id: string; level: string; fileSupport: number; screenSupport: number;
    screenBindings: unknown[]; identities: string[]; metadata?: { title: string; intent: string }; eligible: boolean }[];
    rejected: { reason: string }[]; coverage: unknown; analysisEvidence: unknown; graph: { nodes: unknown[] } } };

export default function ScreenDerivation({ nodes, generation, onPublished }: {
  nodes: Screen[]; generation: string | null; onPublished: () => void;
}) {
  const { client, overview } = useWorkbench();
  const editable = ['owner', 'designer'].includes(overview.role);
  const [inputId, setInputId] = useState(''), [decisions, setDecisions] = useState<string[]>([]);
  const [screenIds, setScreenIds] = useState<string[]>([]), [model, setModel] = useState('');
  const [name, setName] = useState('screen-composition'), [selected, setSelected] = useState('');
  const [inputCursor, setInputCursor] = useState(''), [cursor, setCursor] = useState('');
  const pollUntil = useRef(0), action = useAction();
  const config = useLoad(signal => client.get<{ enabled: boolean; models: string[] }>('/designs/config', signal), [client]);
  const inputs = useLoad(signal => client.get<{ items: Input[]; cursor?: string }>(
    '/ontology/knowledge/derive-inputs' + (inputCursor ? '?cursor=' + resource(inputCursor) : ''), signal), [client, inputCursor]);
  const documents = useLoad(signal => listPages<{ id: string; title?: string; kind: string }>(
    client, '/designs/inputs', 'inputs', signal), [client]);
  const records = useLoad(signal => client.get<{ items: Proposal[]; cursor?: string }>(
    '/ontology/knowledge' + (cursor ? '?cursor=' + resource(cursor) : ''), signal), [client, cursor]);
  const detail = useLoad(signal => selected ? client.get<Proposal>('/ontology/knowledge/' + resource(selected), signal)
    : Promise.resolve(null), [client, selected]);
  useEffect(() => {
    setInputId(''); setDecisions([]); setScreenIds([]); setModel(''); setSelected(''); setInputCursor(''); setCursor('');
    action.reset();
  }, [client]);
  useEffect(() => { setScreenIds([]); action.reset(); }, [generation]);
  useEffect(() => { pollUntil.current = Date.now() + 30 * 60 * 1000; action.reset(); }, [client, selected]);
  useEffect(() => {
    if (!selected || !detail.data || Date.now() >= pollUntil.current ||
      !['queued', 'dispatched', 'running'].includes(detail.data.artifact.status)) return;
    const timer = window.setTimeout(detail.refresh, 1500);
    return () => window.clearTimeout(timer);
  }, [selected, detail.data]);
  const input = inputs.data?.items.find(row => row.analysisId === inputId);
  const screens = nodes.filter(node => node.type === 'Screen' && node.reviewState === 'approved' && !node.tombstone);
  const result = detail.data?.operation === 'design.derive' ? detail.data.result : undefined;
  return <Section title="반복 화면 구성에서 재사용 후보 만들기"
    description="검증된 정적 코드 분석과 승인된 화면 매핑으로 반복 구성을 찾습니다. 모델은 제목과 의도만 제안하며 후보를 승인하지 않습니다.">
    <LoadState state={inputs}>{inputs.data && <>
      <Field label="실행 근거가 보존된 코드 분석"><select disabled={!editable || action.busy}
        value={inputId} onChange={event => setInputId(event.target.value)}><option value="">분석 선택</option>
        {inputs.data.items.map(row => <option key={row.analysisId} value={row.analysisId}>
          {row.name} · {row.backend === 'agentcore-code-interpreter' ? 'AgentCore Code Interpreter' : '실행 환경 미확인'}</option>)}</select></Field>
      {!inputs.data.items.length && <Empty>이 페이지에 현재 반입 승인과 실행 근거가 확인되는 코드 분석이 없습니다.</Empty>}
      <button disabled={!inputCursor || action.busy} onClick={() => { setInputId(''); setInputCursor(''); }}>분석 기준 첫 페이지</button>
      <button disabled={!inputs.data.cursor || action.busy} onClick={() => {
        setInputId(''); setInputCursor(inputs.data?.cursor || '');
      }}>분석 기준 다음 페이지</button>
    </>}</LoadState>
    <fieldset className="wb-field"><legend>현재 페이지에서 승인된 화면 선택</legend>
      <div className="wb-source-list">{screens.map(screen => <label key={screen.id}>
        <input type="checkbox" disabled={!editable || action.busy} checked={screenIds.includes(screen.id)}
          onChange={event => setScreenIds(old => event.target.checked ? [...old, screen.id] : old.filter(id => id !== screen.id))} />
        {screen.title}</label>)}</div>
      {!screens.length && <p className="wb-muted">승인된 화면과 독립적으로 승인된 코드의 구현 연결이 필요합니다.</p>}
    </fieldset>
    <LoadState state={documents}>{documents.data && <fieldset className="wb-field">
      <legend>선택 화면에 연결된 추가 반입 승인 원문</legend>
      <div className="wb-source-list">{documents.data.filter(row => row.kind === 'document-pages').map(row =>
        <label key={row.id}><input type="checkbox" disabled={!editable || action.busy} checked={decisions.includes(row.id)}
          onChange={event => setDecisions(old => event.target.checked ? [...old, row.id] : old.filter(id => id !== row.id))} />
          {row.title || row.id}</label>)}</div>
    </fieldset>}</LoadState>
    <Field label="반복 구성 후보 묶음 이름"><input value={name} disabled={!editable || action.busy}
      maxLength={100} onChange={event => setName(event.target.value)} /></Field>
    <LoadState state={config}>{config.data && <Field label="반복 구성 설명 모델">
      <select value={model} disabled={!editable || action.busy} onChange={event => setModel(event.target.value)}>
        <option value="">모델 선택</option>{config.data.models.map(id => <option key={id} value={id}>{id}</option>)}
      </select></Field>}</LoadState>
    <button disabled={!editable || action.busy || !input || inputs.loading || !!inputs.error || !generation ||
      !config.data?.enabled || !model || !screenIds.length || screenIds.length > 20 || decisions.length > 19 ||
      screenIds.some(id => !screens.some(screen => screen.id === id)) || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(name)}
      onClick={() => {
        if (!input || !generation) return;
        const body = { name, analysisId: input.analysisId, decisionIds: [input.collectionDecisionId, ...decisions],
          nodeIds: screenIds, model, expectedGeneration: generation };
        void action.run(signal => client.post<{ artifact: Artifact }>('/ontology/knowledge/derive',
          { ...body, requestId: action.requestId(JSON.stringify(body)) }, signal), value => {
            setSelected(value.artifact.id); records.refresh();
          }, '반복 구성 분석을 접수했습니다. 파일 반복과 독립 화면 근거를 구분해 검토하세요.');
      }}>반복 구성 후보 생성</button>
    <ActionState action={action} />
    <LoadState state={records}>{records.data && <>
      <div className="wb-record-list">{records.data.items.filter(row => row.operation === 'design.derive').map(row =>
        <button key={row.artifact.id} className="wb-record" onClick={() => setSelected(row.artifact.id)}>
          {row.artifact.name || row.artifact.id} · {label(row.artifact.status)}</button>)}</div>
      <button disabled={!cursor} onClick={() => setCursor('')}>반복 후보 첫 페이지</button>
      <button disabled={!records.data.cursor} onClick={() => setCursor(records.data?.cursor || '')}>반복 후보 다음 페이지</button>
    </>}</LoadState>
    {selected && <LoadState state={detail}>{detail.data && <>
      <p>반복 구성 작업 상태: {label(detail.data.artifact.status)}</p>
      {detail.data.artifact.jobId && detail.data.artifact.createdBy === overview.actorId &&
        ['queued', 'dispatched', 'running'].includes(detail.data.artifact.status) &&
        <button disabled={action.busy} onClick={() => void action.run(signal => client.post(
          `/ontology/executions/${resource(detail.data!.artifact.jobId!)}/cancel`, {}, signal),
          () => detail.refresh(), '본인 반복 구성 작업 중지를 요청했습니다.')}>본인 반복 구성 작업 중지</button>}
      {result?.proposals.map(item => <article className="wb-artifact" key={item.id}>
        <strong>{item.metadata?.title || item.id}</strong>
        <p>{item.level} · {item.eligible ? '근거 검증 통과 · 채택 전' : '수정이 필요한 후보'}</p>
        <p>정적 파일 {item.fileSupport}개 · 독립 승인 화면 {item.screenSupport}개</p>
        <p>{item.metadata?.intent}</p>
        <Details title="구성 요소·정확한 화면 근거" value={{ components: item.identities, screenBindings: item.screenBindings }} />
      </article>)}
      {result && <><Details title="미매핑·분석 범위" value={result.coverage} />
        <Details title="원본 분석의 실행 증거" value={result.analysisEvidence} /></>}
      {!!result?.rejected.length && <Details title="채택을 막는 근거 부족·수정 항목" value={result.rejected} />}
      <p className="wb-muted">정적 참조가 반복된 결과입니다. 실제 실행 화면과 컴포넌트 속성 인터페이스는 별도로 검수하세요. 채택해도 검토 후보로 남습니다.</p>
      <button disabled={!editable || action.busy || !result || !result.graph.nodes.length || !!result.rejected.length ||
        !detail.data.resultHash || !generation}
        onClick={() => {
          const current = detail.data!;
          const body = { version: current.version, resultHash: current.resultHash, expectedGeneration: generation };
          void action.run(signal => client.post(`/ontology/knowledge/${resource(selected)}/publish`,
            { ...body, requestId: action.requestId(selected + JSON.stringify(body)) }, signal), () => {
              records.refresh(); detail.refresh(); onPublished();
            }, '검토한 반복 구성 후보를 정본 초안에 연결했습니다. 자산 승인은 별도로 기록하세요.');
        }}>구성 근거 검토 후 정본 초안 연결</button>
    </>}</LoadState>}
  </Section>;
}
