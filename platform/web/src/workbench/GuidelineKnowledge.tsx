import { useEffect, useRef, useState } from 'react';
import { resource, listPages } from './client';
import { ActionState, Details, Field, LoadState, Section, useAction, useLoad, useWorkbench, label } from './shared';

type Artifact = { id: string; name?: string; status: string; jobId?: string; createdBy?: string };
type Proposal = { artifact: Artifact; version?: number; generation?: string; resultHash?: string;
  result?: { rejected: { reason: string }[]; approvalEffect: false; rules: {
    ruleId: string; statement: string; required: boolean; severity: string;
    citation: { sourceId: string; page: number; quote: string; derivativeHash: string };
  }[] } };

export default function GuidelineKnowledge({ target, generation, onPublished }: {
  target: { id: string; title: string; reviewState: string } | null;
  generation: string | null; onPublished: () => void;
}) {
  const { client, overview } = useWorkbench();
  const editable = ['owner', 'designer'].includes(overview.role);
  const [decisions, setDecisions] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [name, setName] = useState('guide-learning');
  const [selected, setSelected] = useState('');
  const [cursor, setCursor] = useState('');
  const pollUntil = useRef(0);
  const action = useAction();
  const config = useLoad(signal => client.get<{ enabled: boolean; models: string[] }>('/designs/config', signal), [client]);
  const inputs = useLoad(signal => listPages<{ id: string; title?: string; kind: string }>(
    client, '/designs/inputs', 'inputs', signal), [client]);
  const records = useLoad(signal => client.get<{ items: Proposal[]; cursor?: string }>(
    '/ontology/knowledge' + (cursor ? '?cursor=' + resource(cursor) : ''), signal), [client, cursor]);
  const detail = useLoad(signal => selected ? client.get<Proposal>(
    '/ontology/knowledge/' + resource(selected), signal) : Promise.resolve(null), [client, selected]);
  useEffect(() => {
    setSelected(''); setCursor(''); setDecisions([]); setModel(''); action.reset();
  }, [client]);
  useEffect(() => { pollUntil.current = Date.now() + 30 * 60 * 1000; }, [client, selected]);
  useEffect(() => {
    if (!selected || !detail.data || Date.now() >= pollUntil.current
      || !['queued', 'dispatched', 'running'].includes(detail.data.artifact.status)) return;
    const timer = window.setTimeout(() => detail.refresh(), 1500);
    return () => window.clearTimeout(timer);
  }, [selected, detail.data]);
  const result = detail.data?.result;
  return <Section title="가이드에서 인용 규칙 후보 만들기"
    description="반입 승인된 원문과 선택한 자산의 정확한 버전을 기준으로 후보를 만듭니다. AI는 규칙을 승인하지 않습니다.">
    <p>{target ? `선택 자산: ${target.title}` : '아래 자산 목록에서 승인된 자산을 선택하세요.'}</p>
    <LoadState state={inputs}>{inputs.data && <fieldset className="wb-field">
      <legend>지식화할 반입 승인 자료</legend>
      <div className="wb-source-list">{inputs.data.filter(row => row.kind !== 'prompt-text').map(row =>
        <label key={row.id}><input type="checkbox" disabled={action.busy || !editable}
          checked={decisions.includes(row.id)} onChange={event => setDecisions(old =>
            event.target.checked ? [...old, row.id] : old.filter(id => id !== row.id))} />{row.title || row.id}</label>)}</div>
    </fieldset>}</LoadState>
    <Field label="후보 묶음 이름"><input disabled={action.busy || !editable} value={name}
      maxLength={100} onChange={event => setName(event.target.value)} /></Field>
    <LoadState state={config}>{config.data && <Field label="가이드 추출 모델"><select disabled={action.busy || !editable}
      value={model} onChange={event => setModel(event.target.value)}><option value="">모델 선택</option>
      {config.data.models.map(id => <option key={id} value={id}>{id}</option>)}
    </select></Field>}</LoadState>
    <button disabled={action.busy || !editable || !config.data?.enabled || !model || !generation
      || !target || target.reviewState !== 'approved' || !decisions.length || decisions.length > 20
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(name)}
      onClick={() => {
        if (!target || !generation) return;
        const body = { name, model, decisionIds: decisions, nodeIds: [target.id], expectedGeneration: generation };
        void action.run(signal => client.post<{ artifact: Artifact }>('/ontology/knowledge/guidelines',
          { ...body, requestId: action.requestId(JSON.stringify(body)) }, signal), value => {
            setSelected(value.artifact.id); records.refresh();
          }, '지식화를 접수했습니다. 후보의 인용 근거를 확인하세요.');
      }}>가이드 인용 규칙 후보 생성</button>
    <ActionState action={action} />
    <LoadState state={records}>{records.data && <>
      <div className="wb-record-list">{records.data.items.map(row => <button key={row.artifact.id} className="wb-record"
        onClick={() => setSelected(row.artifact.id)}>{row.artifact.name || row.artifact.id} · {label(row.artifact.status)}</button>)}</div>
      <button disabled={!cursor} onClick={() => setCursor('')}>후보 첫 페이지</button>
      <button disabled={!records.data.cursor} onClick={() => setCursor(records.data?.cursor || '')}>후보 다음 페이지</button>
    </>}</LoadState>
    {selected && <LoadState state={detail}>{detail.data && <>
      <p>후보 상태: {label(detail.data.artifact.status)}</p>
      {detail.data.artifact.jobId && detail.data.artifact.createdBy === overview.actorId &&
        ['queued', 'dispatched', 'running'].includes(detail.data.artifact.status) &&
        <button disabled={action.busy} onClick={() => void action.run(signal => client.post(
          `/ontology/executions/${resource(detail.data!.artifact.jobId!)}/cancel`, {}, signal),
          () => detail.refresh(), '본인 작업 중지를 요청했습니다.')}>본인 지식화 작업 중지</button>}
      {result?.rules.map(rule => <article className="wb-record" key={rule.ruleId}>
        <strong>{rule.statement}</strong><p>{rule.required ? '필수 규칙 후보' : '권고 규칙 후보'} · {rule.severity}</p>
        <p>원문 {rule.citation.page}페이지: {rule.citation.quote}</p>
        <Details title="정확한 원문·추출 근거" value={rule.citation} />
      </article>)}
      {!!result?.rejected.length && <Details title="수정이 필요한 추출 결과" value={result.rejected} />}
      <p className="wb-muted">정본 연결은 UX의 후보 채택입니다. 업무 규칙의 최종 검토·승인은 기획자 또는 관리자가 별도로 기록합니다.</p>
      <button disabled={action.busy || !editable || !result || !result.rules.length || !!result.rejected.length
        || !detail.data.resultHash || !generation}
        onClick={() => {
          const current = detail.data!;
          const body = { version: current.version, resultHash: current.resultHash, expectedGeneration: generation };
          void action.run(signal => client.post(`/ontology/knowledge/${resource(selected)}/publish`,
            { ...body, requestId: action.requestId(selected + JSON.stringify(body)) }, signal), () => {
              records.refresh(); detail.refresh(); onPublished();
            }, '검토한 후보를 정본 초안에 연결했습니다. 정책 승인 단계는 별도로 진행하세요.');
        }}>인용 검토 후 정본 초안 연결</button>
    </>}</LoadState>}
  </Section>;
}
