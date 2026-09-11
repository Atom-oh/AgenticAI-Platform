import { useEffect, useRef, useState } from 'react';
import { messageOf, resource, workspaceClient } from './client';
import { useWorkspaceScope } from './WorkspaceScope';
import { can, ROLE_LABELS } from './project';
import { Notice } from './shared';
import ProductPlanner from './ProductPlanner';
import ReleasePanel from './ReleasePanel';
import ComponentCatalogPanel from './ComponentCatalogPanel';
import GuidelineHistory from './GuidelineHistory';
import type { Anchor, Asset, ComponentCatalogSummary, Discussion, Product, Project, Role, Selection } from './types';

export default function CollaborationSidebar({ products, product, onProduct, onProductSaved, selection, refresh, onProjectRefresh, assets, catalog }: {
  products: Product[]; product?: Product; onProduct: (id: string) => void; onProductSaved: (product: Product) => void;
  selection: Selection; refresh: () => void; onProjectRefresh: () => void;
  assets: Asset[]; catalog?: ComponentCatalogSummary;
}) {
  const { project, role } = useWorkspaceScope();
  const [tab, setTab] = useState<'plan' | 'discuss' | 'develop'>(project ? 'plan' : 'develop');
  const [releaseIds, setReleaseIds] = useState<Record<string, string>>({});
  const selectedArtifact = `${selection?.run.id || ''}:${selection?.round?.number || ''}:${selection?.round?.artifactSha256 || ''}`;
  // A selected historical run uses its own revision, never the newly published product revision.
  const anchor: Anchor = selection ? {
    ...(selection.run.productId ? { productId: selection.run.productId } : {}),
    ...(selection.run.guidelineId ? { guidelineId: selection.run.guidelineId } : {}),
    runId: selection.run.id, ...(selection.round ? { round: selection.round.number } : {}),
    ...(selection.round && selection.pageId ? { pageId: selection.pageId } : {}),
  } : product ? { productId: product.id, ...(product.publishedGuidelineId ? { guidelineId: product.publishedGuidelineId } : {}) } : {};
  return <aside className="ws-sidebar" aria-label="기획·디자인·개발 협업">
    <div className="ws-section-heading"><h2>{project ? '함께 검토하기' : '개발 산출물'}</h2><span>{role && ROLE_LABELS[role]}</span></div>
    {project && <label className="ws-field">공유 상품<select aria-label="공유 상품" value={product?.id || ''} onChange={event => onProduct(event.target.value)}>
      <option value="">상품 미선택 · 전체 이력</option>{products.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}
    <nav className="ws-sidebar-tabs" aria-label="협업 보기">
      {project && <><button aria-pressed={tab === 'plan'} onClick={() => setTab('plan')}>기획·지침</button>
        <button aria-pressed={tab === 'discuss'} onClick={() => setTab('discuss')}>의견</button></>}
      <button aria-pressed={tab === 'develop'} onClick={() => setTab('develop')}>개발·내보내기</button>
    </nav>
    {project && <div hidden={tab !== 'plan'}><ProductPlanner key={product?.id || 'new'} product={product} onSaved={onProductSaved} refresh={refresh} />
      {product && <GuidelineHistory key={product.id} product={product} assets={assets} />}</div>}
    {project && <div hidden={tab !== 'discuss'}><DiscussionPanel key={JSON.stringify(anchor)} anchor={anchor} product={product} selection={selection} /></div>}
    <div hidden={tab !== 'develop'}><ReleasePanel key={selectedArtifact}
      selection={selection} product={product} initialReleaseId={selection?.round?.releaseId || releaseIds[selectedArtifact]}
      onRelease={id => setReleaseIds(previous => previous[selectedArtifact] === id ? previous : { ...previous, [selectedArtifact]: id })} />
      <ComponentCatalogPanel summary={catalog} /></div>
    {project && can(role, 'members') && <details className="ws-member-details"><summary>참여자 관리</summary>
      <Members key={project.version} project={project} onSaved={onProjectRefresh} /></details>}
  </aside>;
}

function DiscussionPanel({ anchor, product, selection }: { anchor: Anchor; product?: Product; selection: Selection }) {
  const { client, project, role } = useWorkspaceScope();
  const [items, setItems] = useState<Discussion[]>([]);
  const [cursor, setCursor] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const request = useRef({ text: '', id: '' });
  const action = useRef<AbortController | null>(null);
  const query = new URLSearchParams(Object.entries(anchor).map(([key, value]) => [key, String(value)])).toString();
  const ready = !!(anchor.productId || anchor.guidelineId || anchor.runId);
  useEffect(() => {
    const abort = new AbortController(); setItems([]); setCursor(''); setError('');
    if (ready) client.get<{ comments: Discussion[]; cursor?: string }>(`/comments?${query}`, abort.signal)
      .then(value => { if (!abort.signal.aborted) { setItems(value.comments); setCursor(value.cursor || ''); } })
      .catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, query, ready, retry]);
  useEffect(() => () => action.current?.abort(), []);
  const send = async (more = false) => {
    if (busy || !ready || (!more && (!text.trim() || !can(role, 'discuss')))) return;
    const abort = new AbortController(); action.current?.abort(); action.current = abort; setBusy(true); setError('');
    try {
      if (more) {
        const value = await client.get<{ comments: Discussion[]; cursor?: string }>(`/comments?${query}&cursor=${resource(cursor)}`, abort.signal);
        if (!abort.signal.aborted) { setItems(previous => [...previous, ...value.comments.filter(item => !previous.some(existing => existing.id === item.id))]); setCursor(value.cursor || ''); }
      } else {
        if (request.current.text !== text.trim()) request.current = { text: text.trim(), id: crypto.randomUUID() };
        const { comment } = await client.post<{ comment: Discussion }>('/comments',
          { text: request.current.text, requestId: request.current.id, anchor }, abort.signal);
        if (!abort.signal.aborted) { setItems(previous => [...previous.filter(item => item.id !== comment.id), comment]); setText(''); request.current = { text: '', id: '' }; }
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <section className="ws-discussion"><h3>선택한 기준에 남기는 의견</h3>
    <p>{product?.title || '선택한 시안'}{selection?.round ? ` · 라운드 ${selection.round.number}` : ''}
      {selection?.pageId ? ` · 화면 ${(selection.round?.pageSources?.findIndex(page => page.pageId === selection.pageId) ?? 0) + 1}` : ''}</p>
    <p className="ws-muted">{anchor.guidelineId ? '이 시안에 연결된 게시 지침에 기록됩니다.' : '상품 또는 시안의 현재 선택 범위에 기록됩니다.'} 의견 작성은 시안 승인이 아닙니다.</p>
    {!ready ? <Notice>상품이나 시안을 선택하면 해당 맥락의 의견을 볼 수 있습니다.</Notice> : <>
      {error && <Notice error>{error} <button onClick={() => setRetry(value => value + 1)}>의견 다시 조회</button></Notice>}
      <ul className="ws-comments">{items.map(item => <li key={item.id}><strong>{project?.members[item.author]?.displayName || '참여자'}</strong>
        <p>{item.text}</p>{typeof item.createdAt === 'number' && <small>{new Date(item.createdAt < 1e12 ? item.createdAt * 1000 : item.createdAt).toLocaleString('ko-KR')}</small>}</li>)}</ul>
      {cursor && <button disabled={busy} onClick={() => void send(true)}>이전 의견 더 보기</button>}
      <label className="ws-field">의견 내용<textarea value={text} maxLength={4000} rows={4} onChange={event => setText(event.target.value)} /></label>
      <button disabled={busy || !text.trim() || !can(role, 'discuss')} onClick={() => void send()}>이 맥락에 의견 남기기</button>
      <details><summary>연결된 기록 식별자</summary><dl>{Object.entries(anchor).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></details>
    </>}
  </section>;
}

function Members({ project, onSaved }: { project: Project; onSaved: () => void }) {
  const [members, setMembers] = useState(project.members);
  const [query, setQuery] = useState('');
  const [people, setPeople] = useState<{ sub: string; displayName: string }[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  const act = async (save: boolean) => {
    if (busy) return;
    const abort = new AbortController(); operation.current?.abort(); operation.current = abort; setBusy(true); setError('');
    try {
      if (save) {
        await workspaceClient.put(`/projects/${resource(project.id)}/members`, { version: project.version, members }, abort.signal);
        if (!abort.signal.aborted) onSaved();
      } else {
        const value = await workspaceClient.get<{ people: typeof people }>(`/projects/${resource(project.id)}/people?q=${resource(query.trim())}`, abort.signal);
        if (!abort.signal.aborted) setPeople(value.people);
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <div className="ws-members"><p>기존 사용자를 찾아 역할을 지정합니다. 변경을 저장하면 접근 권한이 바뀝니다.</p>
    {error && <Notice error>{error}</Notice>}
    {Object.entries(members).map(([id, member]) => <div className="ws-member" key={id}>
      <label className="ws-field">{member.displayName}<select value={member.role} onChange={event => setMembers(previous => ({
        ...previous, [id]: { ...member, role: event.target.value as Role },
      }))}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <button onClick={() => setMembers(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => key !== id)))}>참여 제외</button>
    </div>)}
    <label className="ws-field">기존 사용자 검색<input value={query} onChange={event => setQuery(event.target.value)} /></label>
    <button disabled={busy || !query.trim()} onClick={() => void act(false)}>사용자 찾기</button>
    {people.filter(person => !Object.hasOwn(members, person.sub)).map(person => <button key={person.sub}
      onClick={() => setMembers(previous => ({ ...previous, [person.sub]: { displayName: person.displayName, role: 'designer' } }))}>{person.displayName} 추가</button>)}
    <button disabled={busy || !Object.values(members).some(member => member.role === 'owner')} onClick={() => void act(true)}>참여 권한 저장</button>
  </div>;
}
