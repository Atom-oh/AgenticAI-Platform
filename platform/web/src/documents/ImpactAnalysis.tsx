import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { resource, WorkspaceError } from '../workspace/client';
import { newRequest, sourceHref } from './client';
import { analysisHref, libraryHref, Notice, revisionHref, useDocumentQuery, useDocumentScope, usePrivateTask } from './DocumentScope';
import type { Analysis, AnalysisView, Candidate, DocumentRecord, Evidence, Job, Reference } from './types';
import { dateLabel, Details, Progress, roleLabel, Sample, Status } from './presentation';
import { watchAuthorizedResource, watchDocumentJob } from './useDocumentJob';

type ModelConfig = { models: { id: string; label: string }[]; defaultModel: string };
type ReferenceList = { references: Reference[]; backend: string };
const groups: Record<string, string> = { policyRules: '정책 규칙', products: '상품', screens: '화면',
  components: '컴포넌트', departments: '담당 부서', documents: '문서' };
// Stored JSON key order is alphabetical; it is not the review workflow order.
const candidateOrder = ['documents', 'products', 'screens', 'components', 'departments', 'policyRules'];
const groupLabel = (key: string) => Object.hasOwn(groups, key) ? groups[key] : '기타 영향 후보';
const outputPolicyLabels: Record<string, string> = {
  passed: '통과 · 내용 검토는 별도', failed: '실패 · AI 응답 사용 중단', not_run: '실행하지 않음',
};
const outputPolicyLabel = (value?: string) => value === undefined ? '기록 없음'
  : Object.hasOwn(outputPolicyLabels, value) ? outputPolicyLabels[value] : '상태 확인 필요';
const sourceCoverageLabels: Record<string, string> = {
  selected: '승인 원문 연결됨', unavailable: '승인 원문 사용 불가', not_checked: '원문 확인하지 않음',
};
const sourceCoverageReasons: Record<string, string> = {
  approved_source_unavailable: '연결·승인·접근 권한·원문 상태를 확인하세요. 접근할 수 없는 원본의 상세 정보는 제공하지 않습니다.',
  regulation_source_required: '규정 원문이 준비되지 않아 아직 확인하지 않았습니다.',
  source_limit: '이번 분석의 원문 수 한도로 확인하지 않았습니다.',
};

export default function ImpactAnalysis({ preset }: { preset: string }) {
  const { route, client } = useDocumentScope();
  const references = useDocumentQuery<ReferenceList>('/documents/references');
  const stored = useDocumentQuery<{ analyses: Analysis[]; cursor?: string }>('/impact-analyses');
  const pagination = usePrivateTask();
  const [more, setMore] = useState<Analysis[]>([]);
  const [cursor, setCursor] = useState<string>();
  const listGeneration = useRef(0);
  const resetPages = useCallback(() => {
    listGeneration.current += 1; pagination.cancel(); setMore([]); setCursor(undefined);
  }, [pagination.cancel]);
  const reloadStored = () => { resetPages(); stored.reload(); };
  useEffect(() => { resetPages(); setCursor(stored.data?.cursor); }, [stored.data, resetPages]);
  const analyses = [...stored.data?.analyses || [], ...more].filter((item, index, all) => all.findIndex(row => row.id === item.id) === index);
  return <div className="doc-stack">
    <Notice>현재 관계 목록은 공유 시연용입니다. 고객사의 실제 관계망으로 확인된 구성이 아닙니다. 관계로 찾은 항목은 영향 후보이며, 원문 근거와 담당자 검토가 필요합니다.</Notice>
    <div className="doc-grid doc-analysis-grid">
      <aside className="doc-panel doc-stack" aria-label="저장된 분석">
        <div className="doc-row doc-between"><h3>저장된 분석</h3><button onClick={reloadStored}>분석 목록 새로 조회</button></div>
        <a className="doc-button" href={analysisHref(undefined, route.projectId)}>새 분석 작성</a>
        <p className="doc-muted">현재 문서함에서 본인이 요청하고 접근 가능한 분석을 표시합니다.</p>
        {stored.busy && <p role="status">저장된 분석을 불러오고 있습니다…</p>}
        {stored.error && <Notice error>{stored.error}</Notice>}
        {stored.data && !analyses.length && <p>저장된 분석이 없습니다.</p>}
        <ul className="doc-list">{analyses.map(analysis => <li key={analysis.id} className="doc-stack">
          <SavedAnalysisLink analysis={analysis} />
          <div className="doc-row"><Status value={analysis.status} /><span className="doc-muted">{dateLabel(analysis.createdAt)}</span></div>
        </li>)}</ul>
        {cursor && <button disabled={pagination.busy} onClick={() => void pagination.run(async (signal, current) => {
          const generation = listGeneration.current;
          const page = await client.get<{ analyses: Analysis[]; cursor?: string }>('/impact-analyses?cursor=' + encodeURIComponent(cursor), signal);
          if (current() && generation === listGeneration.current) {
            setMore(rows => [...rows, ...page.analyses]); setCursor(page.cursor === cursor ? undefined : page.cursor);
          }
        })}>분석 더 보기</button>}
        {pagination.error && <Notice error>{pagination.error}</Notice>}
        <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId })}>내부 문서함 열기</a>
      </aside>
      <div className="doc-stack">
        {route.analysisId ? <StoredAnalysis /> : <AnalysisForm preset={preset} references={references.data?.references || []} />}
        {references.error && <Notice error>{references.error}<button onClick={references.reload}>규정 목록 다시 조회</button></Notice>}
        {references.data && <Details title="관계 목록 조회 정보"><p>조회 백엔드: {references.data.backend} · 공유 시연 관계 목록</p></Details>}
      </div>
    </div>
  </div>;
}

function AnalysisForm({ preset, references }: { preset: string; references: Reference[] }) {
  const { route, client } = useDocumentScope();
  const models = useDocumentQuery<ModelConfig>('/config');
  const availableModels = useMemo(() => Array.isArray(models.data?.models)
    ? models.data.models.filter(row => row && typeof row.id === 'string' && typeof row.label === 'string') : [], [models.data]);
  const malformedModels = models.data !== undefined && !Array.isArray(models.data.models);
  const [regulation, setRegulation] = useState('');
  const [query, setQuery] = useState(preset);
  const [model, setModel] = useState('');
  const readiness = useDocumentQuery<{ documents: DocumentRecord[]; cursor?: string }>('/documents' +
    (regulation ? '?q=' + encodeURIComponent(regulation) : ''));
  const task = usePrivateTask();
  const request = useRef<{ key: string; id: string }>();
  useEffect(() => {
    if (models.data) setModel(value => availableModels.some(item => item.id === value) ? value
      : availableModels.find(item => item.id === models.data!.defaultModel)?.id || availableModels[0]?.id || '');
  }, [models.data, availableModels]);
  const source = readiness.data?.documents.find(document =>
    document.graphRef === regulation && document.status === 'active' && document.approvedRevisionId);
  return <form className="doc-panel doc-stack" onSubmit={event => {
    event.preventDefault();
    if (!regulation || !query.trim() || !availableModels.some(item => item.id === model) || task.busy) return;
    const key = JSON.stringify([regulation, query.trim(), model]);
    if (request.current?.key !== key) request.current = { key, id: newRequest() };
    const requestId = request.current.id;
    void task.run(async (signal, current) => {
      const value = await client.post<{ analysis: Analysis; job: Job }>('/impact-analyses',
        { requestId, regulationRef: regulation, query: query.trim(), modelId: model }, signal);
      if (current()) {
        if ((value.analysis.projectId || undefined) !== route.projectId) throw new WorkspaceError('분석의 문서함 범위가 일치하지 않습니다.', 403);
        window.location.hash = analysisHref(value.analysis.id, route.projectId);
      }
    });
  }}>
    <h3>변경할 규정과 원문 확인</h3>
    <fieldset className="doc-stack" disabled={task.busy}>
      <label>분석할 규정<select aria-label="분석할 규정" required value={regulation} onChange={event => { setRegulation(event.target.value); request.current = undefined; }}>
        <option value="">규정을 선택하세요</option>
        {references.filter(ref => ref.label === 'Regulation').map(ref => <option key={ref.id} value={ref.id}>{ref.title}</option>)}
      </select></label>
      {regulation && <div className="doc-stack" aria-label="규정 원문 준비 상태">
        {readiness.busy ? <p role="status">승인된 원문 연결을 확인하고 있습니다…</p> : source
          ? <Notice>승인된 원문이 연결되어 있습니다. 분석 시작 시 버전과 접근 권한을 다시 확인합니다.
            <Sample provenance={source.provenance} /> <a href={revisionHref(source.id, source.approvedRevisionId!, route)}>규정 승인본 확인</a></Notice>
          : <Notice>현재 조회 목록에서 승인된 규정 원문을 찾지 못했습니다. 원문을 등록하고 검토·승인하세요.
            {readiness.data?.cursor && <> 목록의 다음 페이지에 연결 문서가 있을 수 있습니다.</>}
            <CandidateSourceLink candidateId={regulation} label="규정 원문 등록·찾기" />
            <p>승인된 규정 원문이 없으면 AI 내용 분석 없이 관계에 따른 영향 후보만 조회합니다.</p></Notice>}
        {readiness.error && <Notice error>{readiness.error}</Notice>}
      </div>}
      <label>변경 내용 또는 검토 질문<textarea aria-label="변경 내용 또는 검토 질문" required maxLength={2000} value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label>분석 모델<select aria-label="분석 모델" value={model} disabled={!availableModels.length} onChange={event => setModel(event.target.value)}>
        {!availableModels.length && <option value="">사용 가능한 모델 없음</option>}
        {availableModels.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}
      </select></label>
    </fieldset>
    {models.error && <Notice error>{models.error}<button type="button" onClick={models.reload}>모델 목록 다시 조회</button></Notice>}
    {malformedModels && <Notice error>모델 목록 형식을 확인하지 못했습니다. <button type="button" onClick={models.reload}>모델 목록 다시 조회</button></Notice>}
    {!models.busy && models.data && !availableModels.length && !malformedModels && <Notice>사용 가능한 모델이 설정되지 않았습니다. 담당자에게 구성을 확인하세요.</Notice>}
    {task.error && <Notice error>{task.error} 같은 입력으로 다시 시도하면 동일한 요청을 확인합니다.</Notice>}
    <div className="doc-row"><button className="doc-primary" type="submit" disabled={task.busy || !regulation || !query.trim() || !model}>
      {task.busy ? '분석 요청 중…' : '영향 분석 시작'}</button></div>
    <p className="doc-muted">원문 중 선택된 근거 문단으로 분석합니다. 결과는 자동 승인되지 않으며, 담당자의 판단을 별도로 기록합니다.</p>
  </form>;
}

function StoredAnalysis() {
  const { route, client } = useDocumentScope();
  const task = usePrivateTask(), decisionTask = usePrivateTask();
  const [view, setView] = useState<AnalysisView>();
  const [job, setJob] = useState<Job>();
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<Candidate>();
  const root = '/impact-analyses/' + resource(route.analysisId!);
  useEffect(() => {
    setView(undefined); setJob(undefined); setSelected(undefined); decisionTask.cancel();
    void task.run(async (signal, current) => {
      const load = async () => {
        const value = await client.get<AnalysisView>(root, signal);
        if (!current()) return;
        if (value.analysis.id !== route.analysisId || (value.analysis.projectId || undefined) !== route.projectId) {
          throw new WorkspaceError('저장된 분석의 문서함 또는 버전 연결을 확인하지 못했습니다.', 403);
        }
        setView(value);
        return value;
      };
      const value = await load();
      if (!value || !current()) return;
      if (['queued', 'running'].includes(value.analysis.status)) {
        let finished: Job;
        try {
          finished = await watchDocumentJob(client, value.analysis.jobId, signal, next => { if (current()) setJob(next); });
        } catch (error) {
          if (!(error instanceof WorkspaceError) || ![403, 404].includes(error.status)) throw error;
          if (!current()) return;
          setJob(undefined);
          await watchAuthorizedResource(load, value => ['queued', 'running'].includes(value.analysis.status), signal);
          return;
        }
        if (current()) {
          await load();
          if (current() && finished.status === 'failed') throw new WorkspaceError('분석 작업을 완료하지 못했습니다. 저장된 상태를 확인하고 새 분석을 요청하세요.');
        }
      }
    });
  }, [client, root, route, refresh, task.run, decisionTask.cancel]);
  const result = view?.result;
  const stale = !!view?.staleSources?.length || view?.analysis.status === 'stale';
  const canDecide = !!view?.canDecide && !stale && view.analysis.status === 'needs_review';
  const nodes = result ? Object.values(result.candidates).flat() : [];
  const findings = result?.findings || [];
  const candidateGroups = result ? [
    ...candidateOrder.filter(key => Object.hasOwn(result.candidates, key)),
    ...Object.keys(result.candidates).filter(key => !candidateOrder.includes(key)).sort(),
  ].map(key => [key, result.candidates[key]] as const) : [];
  return <section className="doc-stack" aria-label="분석 결과">
    <div className="doc-row doc-between"><h3>분석 결과</h3><button disabled={decisionTask.busy} onClick={() => { setView(undefined); setRefresh(value => value + 1); }}>분석 상태 다시 조회</button></div>
    {task.busy && !view && <Notice>저장된 분석을 불러오고 있습니다…</Notice>}
    {task.error && <Notice error>{task.error} <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId })}>원문과 권한 확인</a></Notice>}
    {decisionTask.error && <Notice error>{decisionTask.error} 표시된 분석을 다시 조회한 뒤 검토하세요.</Notice>}
    {view && <>
      <div className="doc-panel doc-stack"><h3>{result?.regulation.title || '요청한 규정 영향 분석'}</h3>
        <p className="doc-literal">{view.analysis.query}</p>
        <div className="doc-row"><Status value={stale ? 'stale' : view.analysis.status} /><span>분석 기록 버전 {view.analysis.version}</span></div>
        {job && <Progress job={job} label="영향 분석" />}
        {view.analysis.status === 'failed' && <Notice error>분석을 완료하지 못했습니다. 원문 상태와 접근 권한을 확인하고 새 분석을 요청하세요.</Notice>}
        {stale && <Notice error>분석에 사용한 원문이 변경되었습니다. 이 결과의 판단 저장은 중단됩니다. 최신 승인본으로 새 분석을 시작하세요.
          <a href={analysisHref(undefined, route.projectId)}>새 분석 작성</a></Notice>}
        {view.analysis.status === 'needs_sources' && <Notice>승인된 규정 원문이 필요합니다. 현재 결과는 관계에 따른 영향 후보입니다.
          <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId, ref: view.analysis.regulationRef })}>규정 원문 등록·찾기</a></Notice>}
        <Details title="분석 기록 정보"><p>분석 ID: <code>{view.analysis.id}</code></p><p>요청자: {view.analysis.createdBy} · {dateLabel(view.analysis.createdAt)}</p></Details>
      </div>
      {result && <>
        <section className="doc-panel doc-stack"><h3>검토 요약</h3><p className="doc-literal">{result.summary || '아직 작성된 검토 요약이 없습니다.'}</p>
          <Notice>인용 연결 확인은 내용의 타당성 검증이 아닙니다. AI 의견과 실제 적용 여부는 담당자가 원문을 대조하여 검토해야 합니다.</Notice>
          {result.verification.references === 'failed' && <Notice error>AI 응답의 인용 연결을 확인하지 못했습니다. 영향 후보와 원문을 직접 검토하거나 다시 분석하세요.</Notice>}
          {result.verification.outputPolicy === 'failed' && <Notice error>
            <strong>AI 응답 표현 검사 실패</strong>
            <p>허용되지 않은 주소·저장 경로나 자동 승인 표현으로 AI 요약과 의견이 제외되었습니다. 표시된 요약은 서버의 대체 안내입니다. 원문과 영향 후보를 직접 검토하거나 다시 분석하세요.</p>
          </Notice>}
          {findings.filter(finding => !nodes.some(node => node.id === finding.nodeId)).map((finding, index) => <div className="doc-stack" key={index}>
            <h4>{finding.nodeId === result.regulation.id ? '규정에 대한 검토 의견' : '대상 확인이 필요한 검토 의견'}</h4>
            <p className="doc-literal">{finding.reason}</p>
            <div className="doc-row">{finding.citationIds.map(id => {
              const evidence = result.evidence.find(item => item.id === id);
              return evidence ? <EvidenceLink key={id} evidence={evidence} /> : <span key={id}>근거 연결 확인 필요</span>;
            })}</div>
          </div>)}
          <div className="doc-stats">
            <div className="doc-stat"><strong>{result.coverage.linkedSources}</strong>연결된 승인 원문</div>
            <div className="doc-stat"><strong>{result.coverage.unavailableSources}</strong>사용하지 못한 원문</div>
            <div className="doc-stat"><strong>{result.coverage.evidenceParagraphs ?? result.evidence.length}</strong>선택된 근거 문단</div>
          </div>
          {result.coverage.uncheckedSources !== undefined && <p>확인하지 않은 원문: {result.coverage.uncheckedSources}개</p>}
          <p>관계 조회 백엔드: {result.coverage.graphBackend} · 공유 시연 관계 목록</p>
          {result.coverage.graphTraversalLimited && <Notice>관계 조회 한도에 도달했습니다. 표시된 개수는 조회된 범위이며 전체 영향 대상의 수가 아닙니다. 조회되지 않은 관계와 대상을 추가로 검토해야 합니다.</Notice>}
          {result.coverage.truncated && <Notice>원문의 일부 문단만 분석 범위에 포함되었습니다. 문서 전체를 검토한 결과가 아닙니다.</Notice>}
          {result.coverage.sourceLimitReached && <Notice>사용할 수 있는 원문 수의 상한에 도달했습니다. 제외된 자료를 별도로 검토하세요.</Notice>}
          {!!result.coverage.candidateContextsOmitted && <p>AI 입력에서 제외된 영향 후보: {result.coverage.candidateContextsOmitted}개</p>}
          {result.coverage.sourceResolution && <Details title="원문 연결·제외 내역">
            <p>사용 불가와 아직 확인하지 않은 원문을 구분합니다. 연결된 원문도 실제 AI 입력에는 선택된 근거 문단만 포함됩니다.</p>
            <ul className="doc-list">{result.coverage.sourceResolution.map((row, index) => {
              const label = Object.hasOwn(sourceCoverageLabels, row.status) ? sourceCoverageLabels[row.status] : '원문 상태 확인 필요';
              const reason = row.reason && Object.hasOwn(sourceCoverageReasons, row.reason) ? sourceCoverageReasons[row.reason] : '';
              let href: string | undefined;
              try { href = libraryHref({ projectId: route.projectId, analysisId: route.analysisId, ref: row.graphRef }); } catch { /* Unknown historical reference remains non-navigable. */ }
              return <li key={`${row.graphRef}:${index}`} className="doc-stack">
                <strong>{row.graphRef === result.regulation.id ? result.regulation.title : nodes.find(node => node.id === row.graphRef)?.name || row.graphRef}</strong>
                <p>{label}{reason && <> · {reason}</>}</p>
                {href && row.status !== 'selected' && <a href={href}>원문 연결·권한 확인</a>}
              </li>;
            })}</ul>
          </Details>}
          <Details title="분석 범위와 기술 정보">
            <p>가용 문단: {result.coverage.availableParagraphs ?? '기록 없음'} · 입력 문자: {result.coverage.contextCharacters ?? '기록 없음'}</p>
            <p>관계 조회 범위: {result.coverage.graphCountsExact === false ? '조회 한도 도달 · 전체 개수 미확인' : result.coverage.graphCountsExact === true ? '설정된 조회 한도 내 결과' : '한도 기록 없음'}</p>
            <p>원문 확인: {result.verification.sourceIntegrity === 'verified_at_analysis' ? '분석 당시 확인됨' : '확인되지 않음'}</p>
            <p>인용 연결: {result.verification.references === 'checked' ? '연결 확인됨 · 내용 검토는 별도' : '미확인'}</p>
            <p>AI 응답 표현 검사: {outputPolicyLabel(result.verification.outputPolicy)}</p>
            <p>모델 호출: {result.model.invoked ? '호출됨' : '호출하지 않음'}</p>
            {result.model.modelId && <p>사용 모델: <code>{result.model.modelId}</code></p>}
            {result.model.usage && <p>기록된 입력 / 출력 토큰: {result.model.usage.inputTokens ?? '없음'} / {result.model.usage.outputTokens ?? '없음'}</p>}
          </Details>
        </section>
        <section className="doc-panel doc-stack" aria-label="분석에 사용한 승인 원문"><h3>분석에 사용한 승인 원문</h3>
          {!result.sources.length && <p>연결된 승인 원문이 없습니다.</p>}
          <ul className="doc-list">{result.sources.map(source => <li key={source.revisionId}>
            <SourceLink source={source} /> <Sample provenance={source.provenance} />
            <p className="doc-muted">반입 {source.revision}차 · {source.versionLabel || '버전 이름 없음'}</p>
          </li>)}</ul>
        </section>
        <section className="doc-panel doc-stack" aria-label="영향 후보"><h3>영향 후보</h3>
          <p className="doc-muted">관계로 연결된 실제 조회 항목입니다. 수정 대상 확정은 담당자의 판단 기록을 확인하세요.</p>
          {candidateGroups.map(([key, candidates]) => <details key={key} open={key === 'documents'} className="doc-details">
            <summary>{groupLabel(key)} · {candidates.length}개 표시 / {Object.hasOwn(result.counts, key) ? result.counts[key] : candidates.length}개 조회
              {result.coverage.candidateOmissions && Object.hasOwn(result.coverage.candidateOmissions, key) && !!result.coverage.candidateOmissions[key] &&
                <span> · 목록에서 생략된 후보: {result.coverage.candidateOmissions[key]}개</span>}
            </summary>
            <div>
              {!candidates.length && <p>조회된 후보가 없습니다.</p>}
              <ul className="doc-list">{candidates.map(candidate => <li key={candidate.id} className="doc-stack">
                <div className="doc-row doc-between"><h4>{candidate.name}</h4>
                  <button disabled={!canDecide || decisionTask.busy} onClick={() => setSelected(candidate)}>{candidate.name} 검토</button></div>
                {findings.filter(finding => finding.nodeId === candidate.id).map((finding, index) => <div key={index} className="doc-stack">
                  <p className="doc-literal">{finding.reason}</p>
                  <div className="doc-row">{finding.citationIds.map(id => {
                    const evidence = result.evidence.find(item => item.id === id);
                    return evidence ? <EvidenceLink key={id} evidence={evidence} /> : <span key={id}>근거 연결 확인 필요</span>;
                  })}</div>
                </div>)}
                {!findings.some(finding => finding.nodeId === candidate.id) && <p>AI 내용 의견 없음 · 관계에 따른 후보입니다.</p>}
                {candidate.label === 'Document' && <div className="doc-row">
                  {result.sources.filter(source => source.graphRef === candidate.id).map(source => <SourceLink key={source.revisionId} source={source} />)}
                  {!result.sources.some(source => source.graphRef === candidate.id) && <>
                    <span>사용 가능한 승인 원문 없음</span><CandidateSourceLink candidateId={candidate.id} />
                  </>}
                </div>}
                <Details title="후보 식별 정보"><code>{candidate.id}</code></Details>
              </li>)}</ul>
            </div>
          </details>)}
        </section>
        <section className="doc-panel doc-stack"><h3>원문 근거 문단</h3>
          {!result.evidence.length && <p>원문 근거 문단이 없습니다. 승인된 원문을 연결해야 내용 기반 검토가 가능합니다.</p>}
          {result.evidence.map(evidence => <article className="doc-evidence" key={evidence.id}>
            <div className="doc-row"><EvidenceLink evidence={evidence} /><Sample provenance={evidence.provenance} /></div>
            <p className="doc-muted">{evidence.title} · 반입 {evidence.revision}차 · {evidence.versionLabel}
              {' · '}{evidence.page == null ? '쪽 정보 없음' : `${evidence.page}쪽`}</p>
            <blockquote>{evidence.quote}</blockquote>
          </article>)}
        </section>
        {selected && canDecide && <DecisionForm key={`${view.analysis.version}:${selected.id}`} candidate={selected} version={view.analysis.version}
          busy={decisionTask.busy} onSave={(decision, note) => void decisionTask.run(async (signal, current) => {
            try {
              await client.post(root + '/decisions', { version: view.analysis.version, nodeId: selected.id, decision, note }, signal);
              if (!current()) return;
              const latest = await client.get<AnalysisView>(root, signal);
              if (current()) {
                if (latest.analysis.id !== view.analysis.id || (latest.analysis.projectId || undefined) !== route.projectId) {
                  throw new WorkspaceError('저장된 판단의 분석 범위를 확인하지 못했습니다.', 403);
                }
                setView(latest); setSelected(undefined);
              }
            } catch (error) { if (current()) { setView(undefined); setSelected(undefined); } throw error; }
          })} />}
        {!canDecide && <Notice>{stale ? '원문이 바뀐 분석에는 판단을 저장할 수 없습니다.'
          : '원문 기반 분석이 검토 대기 상태일 때 소유자 또는 기획 담당자가 판단을 기록할 수 있습니다.'}</Notice>}
        <section className="doc-panel doc-stack"><h3>담당자 판단 기록</h3>
          {!view.decisions?.length && <p>아직 기록된 판단이 없습니다. 분석 결과는 승인이나 수정 확정을 의미하지 않습니다.</p>}
          {view.decisionsTruncated && <Notice>판단 이력 중 일부만 표시하고 있습니다.</Notice>}
          <ul className="doc-list">{view.decisions?.map(decision => <li key={decision.id} className="doc-stack">
            <div className="doc-row"><strong>{nodes.find(node => node.id === decision.nodeId)?.name || decision.nodeId}</strong><Status value={decision.decision} /></div>
            <p className="doc-literal">{decision.note}</p>
            <p className="doc-muted">{decision.actorId} · {roleLabel(decision.actorRole)} · {dateLabel(decision.createdAt)}</p>
          </li>)}</ul>
        </section>
      </>}
    </>}
  </section>;
}

function SourceLink({ source }: { source: NonNullable<AnalysisView['result']>['sources'][number] }) {
  const { route } = useDocumentScope();
  let href: string;
  try { href = revisionHref(source.documentId, source.revisionId, { ...route, textHash: source.textHash }); }
  catch { return <span>원문 버전 연결 확인 필요</span>; }
  return <a href={href}>{source.title} · 사용한 원문 버전</a>;
}
function CandidateSourceLink({ candidateId, label = '원문 등록·문서함 검색' }: { candidateId: string; label?: string }) {
  const { route } = useDocumentScope();
  try {
    return <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId, ref: candidateId })}>{label}</a>;
  } catch {
    return <span>원문 연결 확인 필요</span>;
  }
}
function SavedAnalysisLink({ analysis }: { analysis: Analysis }) {
  const { route } = useDocumentScope();
  try {
    return <a href={analysisHref(analysis.id, route.projectId)} aria-current={analysis.id === route.analysisId ? 'page' : undefined}>{analysis.query}</a>;
  } catch {
    return <span>{analysis.query} · 분석 연결 확인 필요</span>;
  }
}
function EvidenceLink({ evidence }: { evidence: Evidence }) {
  const { route } = useDocumentScope();
  let href: string;
  try { href = sourceHref(evidence, route); } catch { return <span>근거 연결 확인 필요</span>; }
  return <a href={href}>{evidence.id} · 근거 문단 {evidence.paragraphId} 보기</a>;
}
function DecisionForm({ candidate, version, busy, onSave }: { candidate: Candidate; version: number; busy: boolean;
  onSave: (decision: string, note: string) => void }) {
  const [decision, setDecision] = useState('needs_review');
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => { form.current?.scrollIntoView({ block: 'nearest' }); }, []);
  return <form ref={form} className="doc-panel doc-stack" onSubmit={event => { event.preventDefault();
    if (confirmed && note.trim() && !busy) onSave(decision, note.trim()); }}>
    <h3>{candidate.name} 검토 판단</h3><p>표시된 분석 기록 버전 {version}에 판단을 남깁니다.</p>
    <fieldset disabled={busy} className="doc-stack">
      <label>검토 판단<select aria-label="검토 판단" value={decision} onChange={event => setDecision(event.target.value)}>
        <option value="needs_review">추가 검토 필요</option><option value="change_required">수정 필요</option><option value="unaffected">영향 없음</option>
      </select></label>
      <label>판단 근거<textarea aria-label="판단 근거" required maxLength={4000} value={note} onChange={event => setNote(event.target.value)} /></label>
      <label className="doc-check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />표시된 분석 버전과 원문 근거를 확인했습니다</label>
    </fieldset>
    <button className="doc-primary" disabled={busy || !confirmed || !note.trim()} type="submit">검토 판단 저장</button>
  </form>;
}
