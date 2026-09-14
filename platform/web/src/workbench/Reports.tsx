import { useState } from 'react';
import { listWorkbench, resource, wb } from './client';
import { ActionState, array, Details, Empty, Field, label, LoadState, object, Section, Status, text, useAction, useLoad, useWorkbench } from './shared';
import type { Change, Knowledge, Report } from './types';

const REPORT_TYPES = ['change-impact', 'pension-evaluation', 'management', 'underwriting', 'regulation'];
export default function Reports() {
  const { client, params, navigate } = useWorkbench();
  const state = useLoad(signal => listWorkbench<Report>(client, '/reports', signal), [client]);
  const changes = useLoad(signal => listWorkbench<Change>(client, '/changes', signal), [client]);
  const knowledge = useLoad(signal => client.get<{ items: Knowledge[]; cursor?: string }>(wb('/knowledge?limit=100'), signal), [client]);
  const [selected, setSelected] = useState(params.get('reportId') || ''), [current, setCurrent] = useState<Report | null>(null);
  const [title, setTitle] = useState(''), [type, setType] = useState(params.get('sessionId') ? 'pension-evaluation' : 'change-impact');
  const [changeId, setChangeId] = useState(params.get('changeId') || ''), [sessionId, setSessionId] = useState(params.get('sessionId') || '');
  const [evidenceIds, setEvidence] = useState<string[]>([]);
  const action = useAction();
  const report = current?.id === selected ? current : state.data?.find(item => item.id === selected);
  function saved(value: Report) { setCurrent(value); setSelected(value.id); navigate('reports', { reportId: value.id }); state.refresh(); }
  return <div className="wb-two-column">
    <Section title="보고서 목록" description="보고서는 원본 근거와 승인한 내용을 함께 보관합니다." action={<button onClick={() => { setSelected(''); setCurrent(null); navigate('reports', { reportId: undefined }); }}>새 보고서</button>}>
      <LoadState state={state}>{state.data?.length ? <div className="wb-record-list">{state.data.map(item => <button key={item.id}
        className={`wb-record${selected === item.id ? ' is-selected' : ''}`} aria-pressed={selected === item.id}
        onClick={() => { setSelected(item.id); setCurrent(null); navigate('reports', { reportId: item.id }); }}>
        <strong>{item.title}</strong><span>{label(item.type)} · <Status value={item.status} /></span></button>)}</div> : <Empty>아직 만든 보고서가 없습니다.</Empty>}</LoadState>
    </Section>
    <Section title={report ? report.title : '근거 기반 보고서 만들기'} description="자료가 부족한 항목은 미해결 상태로 남깁니다. 승인과 외부 발행은 별도입니다.">
      {report ? <ReportDetail key={`${report.id}:${report.version}`} report={report} onSaved={saved} /> : selected ?
        <LoadState state={state}><Empty>선택한 보고서를 찾지 못했습니다.</Empty></LoadState> :
        <form onSubmit={event => { event.preventDefault(); const fields = { type, title: title.trim(), evidenceIds, ...(changeId ? { changeId } : {}), ...(sessionId ? { sessionId } : {}) };
          void action.run(signal => client.post<{ report: Report }>(wb('/reports'), { ...fields, requestId: action.requestId(JSON.stringify(fields)) }, signal), result => saved(result.report)); }}>
          <fieldset disabled={action.busy}><Field label="보고서 제목"><input required maxLength={160} value={title} onChange={event => setTitle(event.target.value)} placeholder="예: 연금 안내 변경 영향 검토" /></Field>
            <Field label="보고서 유형"><select value={type} onChange={event => setType(event.target.value)}>{REPORT_TYPES.map(value => <option value={value} key={value}>{label(value)}</option>)}</select></Field>
            <LoadState state={changes}><Field label="연결할 변경 요청"><select value={changeId} onChange={event => setChangeId(event.target.value)}><option value="">연결하지 않음</option>
              {changeId && !changes.data?.some(item => item.id === changeId) && <option value={changeId}>{changeId} · 현재 목록에서 미확인</option>}
              {changes.data?.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select></Field></LoadState>
            <Field label="상담 세션 ID" hint="연금 상담에서 보고서로 이동하면 저장된 세션이 자동 연결됩니다."><input value={sessionId} onChange={event => setSessionId(event.target.value)} /></Field>
            <details><summary>게시 지식 근거 선택 · 최대 10개</summary><LoadState state={knowledge}>
              {knowledge.data?.items.map(item => <label className="wb-check" key={item.id}><input type="checkbox" checked={evidenceIds.includes(item.id)}
                disabled={!evidenceIds.includes(item.id) && evidenceIds.length >= 10} onChange={event => setEvidence(ids =>
                  event.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id))} />{item.title}</label>)}
              {!knowledge.data?.items.length && <p className="wb-muted">조회 가능한 게시 지식이 없습니다.</p>}
              {knowledge.data?.cursor && <p className="wb-muted">처음 100개 문서만 표시합니다. 원하는 문서가 없으면 규정집·위키에서 검색하세요.</p>}
            </LoadState></details>
            <button className="wb-primary">근거 모아 보고서 생성</button></fieldset>
        </form>}
      <ActionState action={action} />
    </Section>
  </div>;
}
function ReportDetail({ report, onSaved }: { report: Report; onSaved: (report: Report) => void }) {
  const { client } = useWorkbench(), action = useAction();
  const [checked, setChecked] = useState(false);
  const document = useLoad(signal => client.get<{ markdown: string; contentHash: string; status: string }>(wb(`/reports/${resource(report.id)}/document`), signal), [client, report.id, report.contentHash]);
  const matches = document.data?.contentHash === report.contentHash && !!report.contentHash;
  return <>
    <div className="wb-row"><Status value={report.status} /><span>v{report.version} · {label(report.type)}</span></div>
    <div className="wb-evidence"><h3>보고서 근거</h3>{array(report.sourceRefs || report.evidence).length ? <ul>{array(report.sourceRefs || report.evidence).map((item, index) => {
      const evidence = object(item); return <li key={index}>{text(evidence.title || evidence.type || evidence.id || item)}<Details title="근거 참조·버전" value={item} /></li>;
    })}</ul> : <p className="wb-muted">응답에 별도 근거 목록이 없습니다. 원문과 생성 근거를 확인하세요.</p>}
      {array(report.unresolved).length > 0 && <div className="wb-notice"><strong>미해결 항목 {array(report.unresolved).length}개</strong>
        <ul>{array(report.unresolved).map((item, index) => <li key={index}>{text(object(item).message || object(item).reason || item)}</li>)}</ul></div>}
    </div>
    <LoadState state={document}>{document.data && <>
      {!matches && <p className="wb-error-text" role="alert">보고서 목록과 문서의 콘텐츠 해시가 다릅니다. 최신 버전을 다시 조회하세요.</p>}
      <article className="wb-report-document"><span className="wb-eyebrow">저장된 보고서 원문</span><pre>{document.data.markdown}</pre></article>
      <button disabled={!matches} onClick={() => {
        const url = URL.createObjectURL(new Blob([document.data!.markdown], { type: 'text/markdown;charset=utf-8' }));
        const link = window.document.createElement('a'); link.href = url; link.download = `report-${report.id.replace(/[^a-zA-Z0-9_-]/g, '_')}.md`;
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }}>저장된 문서 다운로드</button>
    </>}</LoadState>
    <Details title="보고서 버전·생성 근거" value={report} />
    <label className="wb-check"><input type="checkbox" checked={checked} disabled={!matches || action.busy} onChange={event => setChecked(event.target.checked)} />
      문서 내용과 연결된 근거를 확인했습니다</label>
    <button className="wb-primary" disabled={!checked || !matches || action.busy || ['approved', 'APPROVED'].includes(report.status)}
      onClick={() => void action.run(signal => client.post<{ report: Report }>(wb(`/reports/${resource(report.id)}/approve`),
        { version: report.version, contentHash: report.contentHash }, signal), result => onSaved(result.report), '해당 문서 버전이 승인되었습니다.')}>이 보고서 버전 승인</button>
    <ActionState action={action} />
  </>;
}
