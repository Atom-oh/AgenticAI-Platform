import { useState } from 'react';
import { safeExternalUrl } from '../workspace/project';
import { resource, wb } from './client';
import { Details, Empty, Field, LoadState, Section, Status, text, useLoad, useWorkbench } from './shared';
import type { Graph, Knowledge } from './types';

export default function KnowledgeView() {
  const { client, params, navigate } = useWorkbench();
  const [query, setQuery] = useState(''), [submitted, setSubmitted] = useState('');
  const [kind, setKind] = useState(''), [cursor, setCursor] = useState(''), [history, setHistory] = useState<string[]>([]);
  const [selected, setSelected] = useState('');
  const target = params.get('targetId') || '';
  const search = new URLSearchParams({ ...(submitted ? { q: submitted } : {}), ...(kind ? { kind } : {}), ...(cursor ? { cursor } : {}) }).toString();
  const state = useLoad(signal => client.get<{ items: Knowledge[]; generation?: string; coverage?: unknown; backend?: unknown; cursor?: string }>(
    wb('/knowledge') + (search ? '?' + search : ''), signal), [client, search]);
  const graph = useLoad(signal => client.get<Graph>(wb('/dependencies') + (target ? '?targetId=' + resource(target) : ''), signal), [client, target]);
  return <div className="wb-stack"><Section title="근거로 찾는 지식" description="권한이 있는 원본과 실제 게시된 지식 버전만 조회합니다.">
    <form className="wb-search" onSubmit={event => { event.preventDefault(); setSubmitted(query.trim()); setCursor(''); setHistory([]); setSelected(''); }}>
      <Field label="지식 검색"><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="가입 조건, 연금 안내, 컴포넌트…" /></Field>
      <Field label="지식 유형"><select value={kind} onChange={event => { setKind(event.target.value); setCursor(''); setHistory([]); setSelected(''); }}>
        <option value="">전체 유형</option><option value="guide">업무 가이드</option><option value="regulation">규정</option>
        <option value="component">컴포넌트</option><option value="product">상품</option><option value="wiki">위키</option>
      </select></Field><button className="wb-primary">검색</button></form>
    <LoadState state={state}>{state.data && <>
      <div className="wb-row"><p className="wb-muted">현재 페이지 {state.data.items.length}개 · 게시 버전 {state.data.generation || '없음'}</p>
        <Details title="검색 제공 방식·범위" value={{ backend: state.data.backend, coverage: state.data.coverage }} /></div>
      <div className="wb-knowledge-layout"><div className="wb-record-list">{state.data.items.map(item =>
        <button className={`wb-record${selected === item.id ? ' is-selected' : ''}`} key={item.id} aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>
          <strong>{item.title}</strong><span>{item.kind || '문서'} · 원본 {item.revision || '버전 미확인'}</span>
          {(item.snippet || item.content) && <p>{(item.snippet || item.content || '').slice(0, 180)}</p>}</button>)}
        {!state.data.items.length && <Empty>조회 가능한 지식이 없습니다. 검색어를 바꾸거나 지식 수집 상태를 확인하세요.</Empty>}
        <div className="wb-actions"><button disabled={!history.length} onClick={() => { setCursor(history.at(-1) || ''); setHistory(items => items.slice(0, -1)); setSelected(''); }}>이전 페이지</button>
          <button disabled={!state.data.cursor || state.data.cursor === cursor || history.includes(state.data.cursor)} onClick={() => {
            setHistory(items => [...items, cursor]); setCursor(state.data?.cursor || ''); setSelected('');
          }}>다음 페이지</button></div></div>
        {selected ? <DocumentDetail key={selected} id={selected} /> : <div className="wb-document-placeholder">문서를 선택하면 원문과 출처 근거가 표시됩니다.</div>}
      </div>
    </>}</LoadState>
  </Section>
    <Section title="지식과 업무의 연결" description="확인한 연결을 따라 관련 기준과 작업으로 이동하세요."
      action={target ? <button onClick={() => navigate('knowledge', { targetId: undefined })}>전체 연결 보기</button> : undefined}>
      <LoadState state={graph}>{graph.data && <>
        <DependencyGraph graph={graph.data} onSelect={id => navigate('knowledge', { targetId: id })} />
        <Details title="연결 범위·지식 버전" value={{ coverage: graph.data.coverage, generation: graph.data.generation }} />
      </>}</LoadState>
    </Section>
  </div>;
}
function DocumentDetail({ id }: { id: string }) {
  const { client } = useWorkbench();
  const state = useLoad(signal => client.get<{ document: Knowledge; evidence: unknown; generation: string }>(wb(`/knowledge/${resource(id)}`), signal), [client, id]);
  const doc = state.data?.document;
  const url = safeExternalUrl(doc?.sourceUrl);
  return <div className="wb-document"><LoadState state={state}>{doc && <>
    <span className="wb-eyebrow">원본에 연결된 지식</span><h3>{doc.title}</h3><p className="wb-prose">{doc.content || doc.snippet || '본문이 응답에 포함되지 않았습니다.'}</p>
    <dl className="wb-facts"><div><dt>원본 버전</dt><dd>{doc.revision || '미확인'}</dd></div><div><dt>게시 버전</dt><dd>{state.data?.generation}</dd></div></dl>
    {url && <a href={url} target="_blank" rel="noreferrer">허용된 원본 열기 ↗</a>}
    <Details title="출처·권한·추출 근거" value={state.data?.evidence} />
  </>}</LoadState></div>;
}
export function DependencyGraph({ graph, onSelect }: { graph: Graph; onSelect: (id: string) => void }) {
  const nodes = graph.nodes.slice(0, 18), positions = new Map(nodes.map((node, index) => [node.id, { x: 28 + (index % 3) * 300, y: 36 + Math.floor(index / 3) * 130 }]));
  const height = Math.max(190, Math.ceil(nodes.length / 3) * 130 + 25);
  return !nodes.length ? <Empty>아직 게시된 연결 정보가 없습니다. 연결이 없다는 사실은 영향이 없다는 뜻이 아닙니다.</Empty> : <>
    <div className="wb-graph"><svg viewBox={`0 0 900 ${height}`} role="img" aria-label={`지식 관계도: ${graph.nodes.length}개 항목, ${graph.edges.length}개 연결`}>
      <defs><marker id="wb-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#739992" /></marker></defs>
      {graph.edges.map((edge, index) => { const src = positions.get(edge.src), dst = positions.get(edge.dst); return src && dst ?
        <path key={index} d={`M${src.x + 120},${src.y + 44} C${src.x + 175},${src.y + 88} ${dst.x + 60},${dst.y - 28} ${dst.x + 120},${dst.y}`}
          stroke="#99b7b0" strokeWidth="1.5" fill="none" markerEnd="url(#wb-arrow)" /> : null; })}
      {nodes.map(node => { const pos = positions.get(node.id)!; return <g key={node.id} transform={`translate(${pos.x},${pos.y})`}
        role="button" tabIndex={0} aria-label={`${node.title || node.id} 연결 보기`} onClick={() => onSelect(node.id)}
        onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.id); } }}>
        <rect width="240" height="72" rx="10" fill="#fff" stroke="#8bb9ad" /><text x="15" y="24" fill="#55756d" fontSize="12">{node.label}</text>
        <text x="15" y="49" fill="#174d42" fontSize="15" fontWeight="650">{(node.title || node.id).slice(0, 23)}</text>
        <title>{node.title || node.id}</title></g>; })}
    </svg></div>
    {graph.nodes.length > nodes.length && <p className="wb-muted">관계도는 앞 {nodes.length}개 항목만 표시합니다. 아래 목록에서 응답에 포함된 전체 연결을 확인하세요.</p>}
    <details className="wb-details"><summary>연결 목록 {graph.edges.length}개와 출처</summary>
      <div className="wb-table-wrap"><table><thead><tr><th>시작 항목</th><th>관계</th><th>대상</th><th>근거</th></tr></thead><tbody>
        {graph.edges.map((edge, index) => <tr key={index}><td>{edge.src}</td><td>{edge.rel}</td><td>{edge.dst}</td>
          <td>{text(edge.provenance)}<Details title="원본 참조" value={edge.sourceRef} /></td></tr>)}
      </tbody></table></div></details></>;
}
