import { useEffect, useState } from 'react';
import { listWorkbench, resource, waitForJob, wb } from './client';
import { ActionState, array, Details, Empty, Field, label, LoadState, Metrics, object, parseArray, Section, Status, text, useAction, useLoad, useWorkbench } from './shared';
import type { Batch, Job, JsonRecord, Source } from './types';

const SNAPSHOT_EXAMPLE = JSON.stringify([
  { id: 'fictional-pension-guide', title: '가상 연금 안내', revision: 'example-v1', kind: 'guide',
    content: '가상 데이터 기반 업무 예제입니다. 상담 금액은 저장된 계산 결과를 사용하고, 안내 문구 변경 시 연결된 화면과 검증 항목을 확인합니다.' },
], null, 2);
export function Sources() {
  const { client, navigate } = useWorkbench();
  const state = useLoad(signal => listWorkbench<Source>(client, '/sources', signal), [client]);
  const action = useAction();
  const [name, setName] = useState(''), [kind, setKind] = useState('snapshot'), [connectionId, setConnection] = useState('');
  const [description, setDescription] = useState('');
  return <div className="wb-two-column">
    <Section title="등록된 원본" description="원본 등록과 수집·색인 게시 상태를 구분합니다.">
      <LoadState state={state}>{state.data?.length ? state.data.map(source =>
        <article className="wb-artifact" key={source.id}><div className="wb-row"><h3>{source.name}</h3><Status value={source.status} /></div>
          <p>{label(source.kind)} · v{source.version}</p><p>{source.description}</p><Details title="수집 범위·연결 상태" value={source} />
          <button onClick={() => navigate('batches', { sourceId: source.id })}>이 원본 수집·배치 확인</button></article>) : <Empty>등록된 지식 원본이 없습니다.</Empty>}</LoadState>
    </Section>
    <Section title="지식 원본 등록" description="외부 원본은 운영자가 준비한 연결 ID를 사용합니다. 인증 정보는 입력하지 마세요.">
      <form onSubmit={event => { event.preventDefault(); void action.run(signal => {
        const fields = { name: name.trim(), kind, description, ...(kind === 'snapshot' ? {} : { connectionId: connectionId.trim() }) };
        return client.post<{ source: Source }>(wb('/sources'), { ...fields, requestId: action.requestId(JSON.stringify(fields)) }, signal);
      }, () => { state.refresh(); setName(''); setDescription(''); }); }}>
        <fieldset disabled={action.busy}><Field label="원본 이름"><input required maxLength={180} value={name} onChange={event => setName(event.target.value)} placeholder="예: 연금 업무 규정집" /></Field>
          <Field label="원본 유형"><select value={kind} onChange={event => setKind(event.target.value)}>
            <option value="snapshot">문서 스냅샷</option><option value="confluence">사내 설치형 Confluence</option><option value="git">Git 자산</option>
          </select></Field><Field label="원본 설명"><textarea rows={3} value={description} onChange={event => setDescription(event.target.value)} /></Field>
          {kind !== 'snapshot' && <><Field label="승인된 연결 ID" hint="서버에 등록된 연결 참조만 사용합니다. URL·비밀번호·토큰이 아닙니다.">
            <input required value={connectionId} onChange={event => setConnection(event.target.value)} /></Field>
            <p className="wb-muted">허용 공간·페이지·저장소·경로는 서버의 연결 프로필에서 관리합니다. 등록 후 원본의 수집 범위에서 확인하세요.</p></>}
          <button className="wb-primary">원본 등록</button></fieldset>
      </form><ActionState action={action} />
    </Section>
  </div>;
}

export function Batches() {
  const { client, params } = useWorkbench();
  const state = useLoad(signal => listWorkbench<Batch>(client, '/batches', signal), [client]);
  const sources = useLoad(signal => listWorkbench<Source>(client, '/sources', signal), [client]);
  const [sourceId, setSource] = useState(params.get('sourceId') || ''), [documents, setDocuments] = useState('[]');
  const action = useAction(), source = sources.data?.find(item => item.id === sourceId);
  const active = state.data?.some(batch => ['queued', 'running', 'pending'].includes(batch.status)) === true;
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(state.refresh, 2000);
    return () => clearTimeout(timer);
  }, [active, state.data]);
  return <div className="wb-stack"><Section title="수집 요청" description="승인된 스냅샷을 넣거나, 설정된 원본 수집기를 실행하세요.">
    <LoadState state={sources}>
      <form onSubmit={event => { event.preventDefault(); void action.run(async (signal, update) => {
        const fields = source?.kind === 'snapshot' ? { documents: parseArray(documents, '문서 스냅샷') } : {};
        const response = await client.post<{ batch: Batch; job: Job }>(wb(`/sources/${resource(sourceId)}/batches`),
          { ...fields, requestId: action.requestId(`${sourceId}:${JSON.stringify(fields)}`) }, signal);
        if (!response.batch?.id || !response.job?.id) throw new Error('배치 접수 기록이 없습니다. 목록에서 상태를 확인하세요.');
        state.refresh();
        await waitForJob(client, response.job.id, { signal, onUpdate: update });
        return client.get<{ batch: Batch }>(wb(`/batches/${resource(response.batch.id)}`), signal);
      }, state.refresh, '배치 작업이 완료되었습니다. 게시 상태와 처리 건수를 확인하세요.'); }}>
        <fieldset disabled={action.busy}><Field label="수집할 원본"><select required value={sourceId} onChange={event => setSource(event.target.value)}>
          <option value="">원본 선택</option>{sources.data?.map(item => <option key={item.id} value={item.id}>{item.name} · {label(item.kind)}</option>)}</select></Field>
          {source?.kind === 'snapshot' && <><div className="wb-row"><p>문서 배열에 원문·버전·권한·관계 근거를 입력하세요.</p>
            <button type="button" onClick={() => setDocuments(SNAPSHOT_EXAMPLE)}>가상 문서 예시 입력</button></div>
            <Field label="문서 스냅샷 JSON" hint="각 문서: id, title, content, revision. 선택 항목: kind, sourceUrl, allowedRoles, entities, relations.">
              <textarea className="wb-code-editor" rows={9} value={documents} onChange={event => setDocuments(event.target.value)} spellCheck={false} /></Field></>}
          {source && source.kind !== 'snapshot' && <p className="wb-muted">서버에 설정된 수집기가 준비되지 않았으면 요청이 차단됩니다.</p>}
          <button className="wb-primary" disabled={!source}>수집·색인 배치 시작</button></fieldset>
      </form>
    </LoadState><ActionState action={action} />
  </Section>
    <Section title="배치 실행 이력" description={active ? '진행 중인 배치 상태를 자동으로 조회합니다.' : '수집·추출·색인·게시 결과는 서버 기록을 기준으로 표시합니다.'}
      action={<button onClick={state.refresh}>상태 새로 조회</button>}>
      <LoadState state={state}>{state.data?.length ? state.data.map(batch =>
        <article className="wb-artifact" key={batch.id}><div className="wb-row"><div><h3>{sources.data?.find(source => source.id === batch.sourceId)?.name || batch.sourceId}</h3>
          <p className="wb-muted">배치 {batch.id}</p></div><Status value={batch.status} /></div>
          <Metrics values={batch.counts} />{batch.error && <p className="wb-error-text">{batch.error}</p>}
          <Details title="처리 단계·지식 버전·실패 근거" value={batch} /></article>) : <Empty>수집 배치 이력이 없습니다.</Empty>}</LoadState>
    </Section>
  </div>;
}

export function Tools() {
  const { client } = useWorkbench();
  const state = useLoad(signal => client.get<{ items: JsonRecord[]; endpoint?: string; operator: boolean }>(wb('/tools'), signal), [client]);
  const action = useAction();
  const [name, setName] = useState(''), [description, setDescription] = useState(''), [toolNames, setNames] = useState('');
  const [discovery, setDiscovery] = useState<JsonRecord | null>(null);
  const supported = array(object(discovery?.result).tools).map(item => object(item));
  return <div className="wb-two-column">
    <Section title="등록된 내부 도구" description="MCP 연결과 지식 수집·색인 상태는 별도입니다.">
      <LoadState state={state}>
        <p className="wb-muted">서빙 경로: {state.data?.endpoint || '응답에 제공 경로가 없습니다.'}</p>
        {state.data?.items.length ? state.data.items.map((tool, index) =>
          <article className="wb-artifact" key={text(tool.id, String(index))}><h3>{text(tool.name)}</h3><p>{text(tool.description, '')}</p>
            <Details title="허용 도구·등록 근거" value={tool} /></article>) : <Empty>아직 등록된 도구가 없습니다.</Empty>}
      </LoadState>
      <button disabled={action.busy} onClick={() => void action.run(signal => client.post<JsonRecord>(wb('/mcp'),
        { jsonrpc: '2.0', id: crypto.randomUUID(), method: 'tools/list', params: {} }, signal), result => {
        if (result.error) throw new Error(text(object(result.error).message, '도구 조회가 거부되었습니다.'));
        setDiscovery(result);
      }, '서버에서 제공하는 도구 목록을 조회했습니다.')}>제공 도구 조회</button>
      {supported.map(tool => <div className="wb-tool" key={text(tool.name)}><strong>{text(tool.name)}</strong><p>{text(tool.description, '')}</p></div>)}
      {discovery && <Details title="도구 조회 응답" value={discovery} />}
    </Section>
    <Section title="MCP 도구 등록" description="내부 API가 지원하는 작업만 등록할 수 있습니다. 도구 선언이 사용자 권한을 변경하지 않습니다.">
      <form onSubmit={event => { event.preventDefault(); const fields = { name, description, toolNames: toolNames.split(',').map(item => item.trim()).filter(Boolean) };
        void action.run(signal => client.post(wb('/tools'), { ...fields, requestId: action.requestId(JSON.stringify(fields)) }, signal), state.refresh); }}>
        <fieldset disabled={action.busy}><Field label="도구 묶음 이름"><input required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></Field>
          <Field label="도구 설명"><textarea required rows={3} value={description} onChange={event => setDescription(event.target.value)} /></Field>
          <Field label="등록할 도구 이름" hint="제공 도구 조회에서 확인한 이름을 쉼표로 구분하세요."><input required value={toolNames} onChange={event => setNames(event.target.value)} /></Field>
          {supported.length > 0 && <div className="wb-actions">{supported.map(tool => <button type="button" key={text(tool.name)} onClick={() =>
            setNames(value => Array.from(new Set([...value.split(',').map(item => item.trim()).filter(Boolean), text(tool.name)])).join(', '))}>{text(tool.name)} 추가</button>)}</div>}
          <button className="wb-primary">내부 도구 등록</button></fieldset>
      </form><ActionState action={action} />
    </Section>
  </div>;
}

export function Operations() {
  const { overview, navigate, refresh } = useWorkbench();
  return <div className="wb-stack"><Section title="운영 현황" description="현재 프로젝트 범위에서 서버가 집계한 작업입니다." action={<button onClick={refresh}>현황 새로 조회</button>}>
    <Metrics values={overview.stats} />
    {!!overview.statsIncomplete?.length && <p className="wb-muted">집계 범위 초과: {overview.statsIncomplete.map(label).join(' · ')}. 표시 건수는 전체가 아닙니다.</p>}
    <div className="wb-operation-links">{[
      ['sources', '지식 원본 등록', '사내 Confluence·Git·문서 스냅샷의 승인된 수집 범위'],
      ['batches', '수집·ETL 배치', '작업 진행 상태, 처리 건수와 게시된 지식 버전'],
      ['tools', 'MCP 도구 등록', '내부 검색·근거·영향 분석 작업의 제공 범위'],
    ].map(([view, title, description]) => <button key={view} onClick={() => navigate(view)}><strong>{title} →</strong><span>{description}</span></button>)}</div>
  </Section><Section title="연결·실행 준비 상태" description="설정 존재, 실제 실행, 검증 결과를 각각 확인하세요.">
    <div className="wb-readiness">{Object.entries(overview.readiness || {}).map(([key, value]) => {
      const details = object(value), status = typeof value === 'string' ? value : typeof value === 'boolean' ? value ? 'configured' : 'not-configured' : text(details.status, 'unknown');
      return <article key={key}><div className="wb-row"><h3>{label(key)}</h3><Status value={status} /></div>
        {typeof value === 'object' && <Details title="준비 상태 근거" value={value} />}</article>;
    })}</div><Details title="서버가 확인한 운영 기능 권한" value={overview.capabilities} />
  </Section></div>;
}
