import { useEffect, useState } from 'react';
import { resource, listPages, waitForJob } from './client';
import { ActionState, Details, Empty, Field, label, LoadState, Section, useAction, useLoad, useWorkbench } from './shared';
import { DependencyGraph } from './Knowledge';
import type { Asset } from '../workspace/types';

type SourceRef = { sourceKind: string; sourceId: string; revision: string; sha256: string; audienceRevision: string };
type Node = { id: string; type: string; title: string; revision: number; reviewState: string; provenance: string;
  sourceRefs: SourceRef[]; properties?: Record<string, unknown> };
type Edge = { id: string; type: string; src: { id: string }; dst: { id: string }; sourceRefs: SourceRef[]; provenance: string };
type Graph = { nodes: Node[]; edges: Edge[]; generation: string | null; cursor?: string | null; coverage: unknown; backend: string };
type Impact = { items: { nodeId: string; title: string; type: string; evidenceKind: string; witnessPath: string[];
  witnessEdges?: unknown[]; sourceRefs?: SourceRef[]; staleWitness?: boolean }[];
  generation: string; coverage: unknown };
type AnalysisArtifact = { id: string; status: string; name?: string; jobId?: string; requestId?: string;
  coverage?: unknown; execution?: { backend?: string; [key: string]: unknown } };
const HASH = /^[a-f0-9]{64}$/;
function completeReceipt(artifact: AnalysisArtifact) {
  const execution = artifact.execution;
  const coverage = artifact.coverage as { complete?: unknown; truncated?: unknown } | undefined;
  if (artifact.status !== 'completed' || !coverage || typeof coverage !== 'object' || Array.isArray(coverage) ||
      typeof coverage.complete !== 'boolean' || typeof coverage.truncated !== 'boolean' ||
      !execution || execution.sourceExecuted !== false ||
      typeof execution.inputHash !== 'string' || !HASH.test(execution.inputHash)) return false;
  if (execution.backend === 'local-offline')
    return ['analyzerCodeHash', 'dependencyLockHash'].every(key =>
      typeof execution[key] === 'string' && HASH.test(execution[key] as string));
  if (execution.backend === 'agentcore-code-interpreter')
    return typeof execution.toolArchiveHash === 'string' && HASH.test(execution.toolArchiveHash) &&
      ['interpreterId', 'sessionId', 'nodeVersion', 'architecture', 'region'].every(key =>
        typeof execution[key] === 'string' && (execution[key] as string).length > 0);
  return false;
}
const LEVELS = ['Foundation', 'Atom', 'Molecule', 'Organism', 'Pattern', 'PageTemplate', 'Screen', 'Procedure'];
const STATUS: Record<string, string> = { candidate: '검토 후보', reviewed: '검토 완료', approved: '승인', rejected: '반려', deprecated: '폐기' };
const KIND: Record<string, string> = { Foundation: '기초 자산', Atom: '기본 요소', Molecule: '소규모 조합', Organism: '업무 영역',
  Pattern: '재사용 패턴', PageTemplate: '페이지 틀', Screen: '화면', Procedure: '업무 흐름', CodeFile: '코드 파일',
  CodeSymbol: '코드 기호', Component: '분류 전 컴포넌트', Product: '상품', PolicyRule: '업무 규칙', Team: '담당 팀',
  API: 'API', Test: '테스트', Asset: '파일 자산', Document: '문서', Skill: '스킬' };

export default function OntologyView() {
  const { client, project, overview, navigate, params } = useWorkbench();
  const [cursor, setCursor] = useState(''), [history, setHistory] = useState<string[]>([]);
  const [kind, setKind] = useState(''), [selected, setSelected] = useState<Node | null>(null);
  const [reason, setReason] = useState(''), [impact, setImpact] = useState<Impact | null>(null);
  const [collectionName, setCollectionName] = useState('source-collection');
  const [files, setFiles] = useState<Record<string, string>>({});
  const [receipt, setReceipt] = useState<AnalysisArtifact | null>(null);
  const [analysisCursor, setAnalysisCursor] = useState('');
  const [analysisReload, setAnalysisReload] = useState(0), [requestEpoch, setRequestEpoch] = useState(() => crypto.randomUUID());
  const analysisId = params.get('analysisId') || '';
  const operation = useAction();
  const analysis = useAction();
  const query = cursor ? '?cursor=' + resource(cursor) : '';
  const state = useLoad(signal => client.get<Graph>('/ontology' + query, signal), [client, query]);
  const configuration = useLoad(signal => client.get<{ analyzerConfigured: boolean }>('/ontology/schema', signal), [client]);
  const assets = useLoad(signal => listPages<Asset>(client, '/assets', 'assets', signal), [client]);
  const accepted = useLoad(signal => client.get<{ items: AnalysisArtifact[]; cursor?: string }>(
    '/ontology/analyses?limit=20' + (analysisCursor ? '&cursor=' + resource(analysisCursor) : ''), signal), [client, analysisCursor]);
  useEffect(() => {
    setReceipt(null);
    if (!analysisId) return;
    analysis.cancel();
    void analysis.run(async (signal, update) => {
      const path = `/ontology/analyses/${resource(analysisId)}`;
      let result = await client.get<{ artifact: AnalysisArtifact }>(path, signal);
      signal.throwIfAborted();
      if (['queued', 'processing', 'running'].includes(result.artifact.status) && result.artifact.jobId) {
        setReceipt({ ...result.artifact, execution: undefined });
        try { await waitForJob(client, result.artifact.jobId, { signal, onUpdate: update }); }
        catch (error) {
          signal.throwIfAborted();
          const failed = await client.get<{ artifact: AnalysisArtifact }>(path, signal);
          signal.throwIfAborted();
          setReceipt({ ...failed.artifact, execution: undefined });
          throw error;
        }
        result = await client.get<{ artifact: AnalysisArtifact }>(path, signal);
      }
      if (result.artifact.id !== analysisId || !completeReceipt(result.artifact)) {
        signal.throwIfAborted();
        setReceipt({ id: analysisId, status: ['failed', 'cancelled'].includes(result.artifact.status) ? result.artifact.status : 'unverified' });
        throw new Error('분석 완료 기록과 실행 근거를 확인하지 못했습니다. 저장된 작업 상태를 확인하세요.');
      }
      return result.artifact;
    }, value => { setReceipt(value); state.refresh(); accepted.refresh(); }, '저장된 분석의 실행 근거를 조회했습니다.');
    return () => analysis.cancel();
  }, [client, analysisId, analysisReload]);
  const choose = (node: Node | null) => {
    if (operation.busy) operation.cancel();
    setSelected(node); setImpact(null); setReason('');
  };
  useEffect(() => { choose(null); }, [state.data?.generation, cursor]);
  const nodes = (state.data?.nodes || []).filter(node => !kind || node.type === kind);
  const ids = new Set(nodes.map(node => node.id));
  const diagram = { nodes: nodes.map(node => ({ id: node.id, label: KIND[node.type] || node.type, title: node.title })),
    edges: (state.data?.edges || []).filter(edge => ids.has(edge.src.id) && ids.has(edge.dst.id)).map(edge =>
      ({ src: edge.src.id, dst: edge.dst.id, rel: edge.type, sourceRef: edge.sourceRefs, provenance: edge.provenance })) };
  const roles = selected && ['Product', 'PolicyRule', 'Procedure'].includes(selected.type) ? ['owner', 'planner'] :
    selected && ['CodeFile', 'CodeSymbol', 'Test', 'API', 'Skill'].includes(selected.type) ? ['owner', 'developer'] :
      selected?.type === 'Team' ? ['owner'] : ['owner', 'designer'];
  const canReview = roles.includes(overview.role);
  const review = (decision: string) => {
    if (!selected || !state.data?.generation) return;
    const target = selected;
    void operation.run(signal => client.post(`/ontology/nodes/${resource(target.id)}/review`, {
      requestId: operation.requestId([target.id, target.revision, decision, reason].join(':')),
      expectedGeneration: state.data!.generation, revision: target.revision, decision, reason,
    }, signal), () => { setSelected(null); setImpact(null); setCursor(''); setHistory([]); state.refresh(); },
    '선택한 버전의 검토 결정을 기록했습니다.');
  };
  return <div className="wb-stack">
    <Section title="자산·상품·규정·코드의 연결" description="워크스페이스 프로젝트 온톨로지를 정본으로 조회합니다. 그림의 연결은 원본 근거와 검토 상태를 함께 확인하세요."
      action={<button onClick={() => navigate('studio', { step: 'assets', tool: undefined })}>디자인 자산 등록</button>}>
      <p className="wb-muted">{LEVELS.map(level => KIND[level]).join(' → ')}</p>
      <p className="wb-muted">8단계는 디자인과 업무 흐름의 분류입니다. 상품·규정·코드는 이 단계들을 가로질러 연결됩니다.</p>
      <div className="wb-toolbar"><Field label="현재 페이지의 자산 유형"><select value={kind} onChange={event => { setKind(event.target.value); choose(null); }}>
        <option value="">전체 유형</option>{Object.keys(KIND).map(value =>
          <option key={value} value={value}>{KIND[value]} · {value}</option>)}
      </select></Field><button onClick={() => { setCursor(''); setHistory([]); choose(null); state.refresh(); }}>온톨로지 새로 조회</button></div>
      <LoadState state={state}>{state.data && <>
        <DependencyGraph graph={diagram} onSelect={id => choose(nodes.find(node => node.id === id) || null)} />
        <div className="wb-record-list">{nodes.map(node => <button className={`wb-record${selected?.id === node.id ? ' is-selected' : ''}`}
          key={node.id} aria-pressed={selected?.id === node.id} onClick={() => choose(node)}>
          <strong>{node.title}</strong><span>{KIND[node.type] || node.type} · {STATUS[node.reviewState] || '미확인'} · 버전 {node.revision}</span>
        </button>)}</div>
        {!nodes.length && <Empty>조회 가능한 정본 항목이 없습니다. 상품 기준을 게시하거나 원본과 매핑을 등록하세요.</Empty>}
        <div className="wb-actions"><button disabled={!history.length} onClick={() => { setCursor(history.at(-1) || ''); setHistory(items => items.slice(0, -1)); }}>이전 페이지</button>
          <button disabled={!state.data.cursor} onClick={() => { setHistory(items => [...items, cursor]); setCursor(state.data?.cursor || ''); }}>다음 페이지</button></div>
        <Details title="정본 버전·조회 범위" value={{ generation: state.data.generation, coverage: state.data.coverage, backend: state.data.backend }} />
      </>}</LoadState>
      <LoadState state={configuration}>{configuration.data && <p className="wb-muted">코드 분석 실행 환경: {configuration.data.analyzerConfigured ? '설정됨 · 실행 근거는 작업별 확인' : '미구성 · 등록만으로 코드 분석 완료를 뜻하지 않습니다.'}</p>}</LoadState>
    </Section>
    <Section title="등록한 원본에서 연결 추출" description="HTML·React·스타일·이미지를 같은 소스 묶음으로 선택하고 원래 상대 경로를 지정하세요. 원본 코드는 실행하지 않습니다.">
      <Field label="소스 묶음 ID"><input disabled={analysis.busy} value={collectionName} onChange={event => setCollectionName(event.target.value)}
        maxLength={100} placeholder="example-ui" /></Field>
      <LoadState state={assets}>{assets.data && <div className="wb-record-list">
        {assets.data.filter(asset => !asset.archived && !asset.system && asset.uploadStatus === 'stored' &&
          /\.(tsx?|jsx?|css|scss|json|html?|png|jpe?g|svg)$/i.test(asset.name)).map(asset =>
          <div className="wb-artifact" key={asset.id}><label><input type="checkbox" disabled={analysis.busy} checked={Object.hasOwn(files, asset.id)}
            onChange={event => setFiles(previous => { const next = { ...previous }; if (event.target.checked) next[asset.id] = asset.name; else delete next[asset.id]; return next; })} />
            {asset.name}</label>
            {Object.hasOwn(files, asset.id) && <Field label={`${asset.name} 상대 경로`}><input disabled={analysis.busy} value={files[asset.id]}
              onChange={event => setFiles(previous => ({ ...previous, [asset.id]: event.target.value }))} maxLength={500} /></Field>}
          </div>)}
      </div>}</LoadState>
      <button disabled={analysis.busy || Boolean(analysisId) || accepted.loading || Boolean(accepted.error) ||
        accepted.data?.items.some(item => item.name === collectionName && ['queued', 'processing', 'running'].includes(item.status)) ||
        !configuration.data?.analyzerConfigured || !Object.keys(files).length ||
        Object.keys(files).length > 50 || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(collectionName)}
        onClick={() => void analysis.run(async signal => {
          setReceipt(null);
          const payload = { name: collectionName, files: Object.entries(files).map(([assetId, path]) => ({ assetId, path })),
            expectedGeneration: state.data?.generation ?? null };
          return await client.post<{ artifact: AnalysisArtifact; job: { id: string } }>('/ontology/analyses', {
            ...payload, requestId: analysis.requestId(requestEpoch + JSON.stringify(payload)),
          }, signal);
        }, result => { accepted.refresh(); navigate('ontology', { analysisId: result.artifact.id });
          setCursor(''); setHistory([]); }, '분석을 접수했습니다. 저장된 작업에서 이어서 확인하세요.')}>
        원본 분석·매핑 후보 등록
      </button>
      {analysisId && <button onClick={() => {
        analysis.cancel(); setRequestEpoch(crypto.randomUUID()); navigate('ontology', { analysisId: undefined });
      }}>
        새 분석 준비</button>}
      <ActionState action={analysis} />
      {receipt && <section aria-label="원본 분석 실행 근거">
        <p>분석 작업 상태: {receipt.status === 'processing' ? '분석 중' : label(receipt.status)}</p>
        {receipt.execution && <p>실제 분석 환경: {receipt.execution.backend === 'local-offline' ? '로컬 테스트 실행' :
          receipt.execution.backend === 'agentcore-code-interpreter' ? 'AgentCore Code Interpreter' : '확인되지 않은 실행 환경'}</p>}
        <Details title="분석 작업·실행 증거" value={{ artifactId: receipt.id, requestId: receipt.requestId, execution: receipt.execution }} />
        <Details title="분석 범위·미해결 항목" value={receipt.coverage} />
      </section>}
    </Section>
    <Section title="접수된 원본 분석" description="화면을 떠나도 접수된 분석은 남습니다. 기존 작업을 열면 새 분석 요청 없이 상태와 실행 근거를 확인합니다."
      action={<button onClick={accepted.refresh}>분석 작업 새로고침</button>}>
      <LoadState state={accepted}>{accepted.data && <>
        <div className="wb-record-list">{accepted.data.items.map(item => <button key={item.id} className="wb-record"
          onClick={() => {
            analysis.cancel();
            if (analysisId === item.id) setAnalysisReload(value => value + 1);
            else navigate('ontology', { analysisId: item.id });
          }}>
          <strong>{item.name || item.id}</strong><span>{label(item.status)} · 저장된 분석 열기</span>
        </button>)}</div>
        {!accepted.data.items.length && <Empty>이 페이지에 조회 가능한 원본 분석이 없습니다.</Empty>}
        <div className="wb-actions"><button disabled={!analysisCursor} onClick={() => setAnalysisCursor('')}>분석 첫 페이지</button>
          <button disabled={!accepted.data.cursor} onClick={() => setAnalysisCursor(accepted.data?.cursor || '')}>분석 다음 페이지</button></div>
      </>}</LoadState>
    </Section>
    {selected && <Section title={selected.title} description={`${KIND[selected.type] || selected.type} · ${STATUS[selected.reviewState]} · 정확한 원본 버전과 연결을 검토합니다.`}>
      <Details title="원본 근거·코드 매핑" value={{ id: selected.id, sourceRefs: selected.sourceRefs, properties: selected.properties, provenance: selected.provenance }} />
      <div className="wb-actions">{selected.sourceRefs.filter(ref => ref.sourceKind === 'asset').map(ref =>
        <a key={ref.sourceId} className="wb-link" href={`#/studio?projectId=${resource(project.id)}&step=assets&assetId=${resource(ref.sourceId)}`}>등록 원본 보기</a>)}
        <button disabled={operation.busy || !state.data?.generation} onClick={() => {
          const target = selected;
          void operation.run(signal => client.post<Impact>('/ontology/impact', {
            changeId: crypto.randomUUID(), kind: selected.type === 'PolicyRule' ? 'rule' :
              selected.type === 'Product' ? 'product-condition' : selected.type === 'Procedure' ? 'procedure' :
              ['CodeFile', 'CodeSymbol', 'API', 'Test', 'Skill'].includes(selected.type) ? 'code' : 'asset',
            nodeIds: [target.id], expectedGeneration: state.data!.generation,
          }, signal), value => setImpact(value), '현재 권한 범위에서 영향 경로를 조회했습니다.');
        }}>이 항목 변경 영향 보기</button></div>
      <Field label="검토 근거"><textarea value={reason} maxLength={2000} onChange={event => setReason(event.target.value)} placeholder="원본에서 확인한 내용과 판단 근거" /></Field>
      <div className="wb-actions">
        <button disabled={!canReview || !reason.trim() || operation.busy || selected.reviewState === 'deprecated'} onClick={() => review('reviewed')}>검토 완료 기록</button>
        <button disabled={!canReview || !reason.trim() || operation.busy || selected.reviewState !== 'reviewed'} onClick={() => review('approved')}>이 버전 승인</button>
        <button disabled={!canReview || !reason.trim() || operation.busy || selected.reviewState === 'deprecated'} onClick={() => review('rejected')}>반려</button>
      </div>
      <p className="wb-muted">온톨로지 매핑 승인은 생성한 React 화면의 동작 검수·퍼블리싱 승인을 대신하지 않습니다.</p>
      <ActionState action={operation} />
      {impact && <section><h3>변경 영향 경로</h3>
        {impact.items.map(item => <article className="wb-artifact" key={item.nodeId}><strong>{item.title}</strong>
          <p>{item.evidenceKind === 'candidate' ? '추정 연결 · 확인 필요' : item.evidenceKind === 'approved-declared' ? '승인된 선언 관계' :
            item.evidenceKind === 'observed-structural' ? '확인된 구조 참조' : '근거 확인 필요'}</p>
          <Details title="영향 경로의 식별자" value={item.witnessPath} />
          <Details title="경로 전체의 관계·원본 근거" value={{ edges: item.witnessEdges, sources: item.sourceRefs,
            staleWitness: item.staleWitness }} /></article>)}
        {!impact.items.length && <Empty>확인 가능한 영향 경로가 없습니다. 미매핑·권한 제한을 확인해야 하며, 영향이 없다는 판정은 아닙니다.</Empty>}
        <Details title="영향 분석 범위와 미확인 항목" value={impact.coverage} />
      </section>}
    </Section>}
  </div>;
}
