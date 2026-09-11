import { useEffect, useRef, useState } from 'react';
import { messageOf, resource } from './client';
import { useWorkspaceScope } from './WorkspaceScope';
import { can } from './project';
import { guidelinePages } from './guidelinePages';
import { Notice } from './shared';
import type { Ontology, Product, Run } from './types';

type Draft = Pick<Product, 'title' | 'description' | 'conditions' | 'steps' | 'notices'>;
const fields = (value?: Product): Draft => value ? { title: value.title, description: value.description,
  conditions: value.conditions, steps: value.steps, notices: value.notices } : { title: '', description: '', conditions: [], steps: [], notices: [] };
const newId = () => 'item-' + crypto.randomUUID();
export default function ProductPlanner({ product, onSaved, refresh }: { product?: Product; onSaved: (product: Product) => void; refresh: () => void }) {
  const { client, role } = useWorkspaceScope();
  const [draft, setDraft] = useState<Draft>(() => fields(product));
  const [base, setBase] = useState(product);
  const [ontology, setOntology] = useState<Ontology | null>(null);
  const [affected, setAffected] = useState<Run[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const [revision, setRevision] = useState(0);
  const operation = useRef<AbortController | null>(null);
  const request = useRef({ fingerprint: '', id: '' });
  const dirty = JSON.stringify(draft) !== JSON.stringify(fields(base));
  const allowed = can(role, 'publish');
  useEffect(() => () => operation.current?.abort(), []);
  useEffect(() => {
    // New server data must not erase an unsaved planner draft.
    if (!dirty && product?.version !== base?.version) { setBase(product); setDraft(fields(product)); setChecked(false); }
  }, [product]);
  useEffect(() => {
    const abort = new AbortController(); setOntology(null); setAffected(null);
    if (!product?.publishedGuidelineId) return () => abort.abort();
    void Promise.all([
      client.get<{ ontology: Ontology }>(`/products/${resource(product.id)}/ontology?revision=${resource(product.publishedGuidelineId)}`, abort.signal),
      (async () => {
        const runs: Run[] = []; const seen = new Set<string>(); let cursor = '';
        do {
          const page = await client.get<{ affectedRuns: Run[]; cursor?: string }>(`/products/${resource(product.id)}/impact${cursor ? '?cursor=' + resource(cursor) : ''}`, abort.signal);
          runs.push(...page.affectedRuns); cursor = page.cursor || '';
          if (cursor && (seen.has(cursor) || seen.size > 100)) throw new Error('재검증 영향 목록을 모두 확인하지 못했습니다.');
          seen.add(cursor);
        } while (cursor);
        return runs;
      })(),
    ]).then(([value, runs]) => {
      if (abort.signal.aborted) return;
      if (value.ontology.guidelineId !== product.publishedGuidelineId || value.ontology.productId !== product.id) throw new Error('게시 지침의 연결 정보를 확인하지 못했습니다.');
      setOntology(value.ontology); setAffected(runs);
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, product?.id, product?.publishedGuidelineId, revision]);
  const save = async (publish = false) => {
    if (!allowed || busy || !draft.title.trim() || (publish && (!base || dirty || !checked))) return;
    const abort = new AbortController(); operation.current?.abort(); operation.current = abort; setBusy(true); setError('');
    try {
      const fingerprint = JSON.stringify(draft);
      if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
      const value = publish ? await client.post<{ product: Product }>(`/products/${resource(base!.id)}/publish`, { version: base!.version }, abort.signal) :
        base ? await client.put<{ product: Product }>(`/products/${resource(base.id)}`, { version: base.version, ...draft }, abort.signal) :
          await client.post<{ product: Product }>('/products', { ...draft, requestId: request.current.id }, abort.signal);
      if (!abort.signal.aborted) { setBase(value.product); setDraft(fields(value.product)); setChecked(false); onSaved(value.product); refresh(); setRevision(value => value + 1); }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <section className="ws-planner"><h3>상품 지침 {base ? '편집' : '만들기'}</h3>
    <p className="ws-muted">기획·관리자가 저장한 초안을 게시하면 버전이 고정된 지침과 연결 정보가 만들어집니다. 기존 승인 화면은 새 지침으로 다시 검증해야 합니다.</p>
    {!allowed && <Notice>기획·관리자만 상품 지침을 수정·게시할 수 있습니다.</Notice>}
    {error && <Notice error>{error}</Notice>}
    {product && base && product.version !== base.version && <Notice>다른 참여자의 변경이 있습니다. 내 초안은 유지했습니다.
      <button onClick={() => { setBase(product); setDraft(fields(product)); setChecked(false); }}>최신 지침으로 다시 열기</button></Notice>}
    <fieldset disabled={!allowed || busy} className="ws-editable">
      <label className="ws-field">상품 이름<input value={draft.title} maxLength={180} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="ws-field">상품 설명<textarea aria-label="상품 설명" rows={3} value={draft.description} maxLength={12000} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
      <h4>가입·업무 조건</h4>{draft.conditions.map((item, index) => <div className="ws-planning-card" key={item.id}>
        <label className="ws-field">조건 {index + 1}<textarea aria-label={`조건 ${index + 1}`} value={item.text} maxLength={4000} onChange={event => setDraft({
          ...draft, conditions: draft.conditions.map(value => value.id === item.id ? { ...value, text: event.target.value } : value),
        })} /></label><button onClick={() => setDraft({ ...draft, conditions: draft.conditions.filter(value => value.id !== item.id) })}>조건 삭제</button></div>)}
      <button onClick={() => setDraft({ ...draft, conditions: [...draft.conditions, { id: newId(), text: '' }] })}>조건 추가</button>
      <h4>진행 절차</h4>{draft.steps.map((item, index) => <div className="ws-planning-card" key={item.id}>
        <label className="ws-field">절차 {index + 1} 이름<input value={item.title} maxLength={180} onChange={event => setDraft({ ...draft,
          steps: draft.steps.map(value => value.id === item.id ? { ...value, title: event.target.value } : value) })} /></label>
        <label className="ws-field">절차 {index + 1} 설명<textarea aria-label={`절차 ${index + 1} 설명`} value={item.description} maxLength={4000} onChange={event => setDraft({ ...draft,
          steps: draft.steps.map(value => value.id === item.id ? { ...value, description: event.target.value } : value) })} /></label>
        <button onClick={() => setDraft({ ...draft, steps: draft.steps.filter(value => value.id !== item.id) })}>절차 삭제</button></div>)}
      <button onClick={() => setDraft({ ...draft, steps: [...draft.steps, { id: newId(), title: '', description: '' }] })}>절차 추가</button>
      <h4>필수 안내</h4>{draft.notices.map((item, index) => <div className="ws-planning-card" key={item.id}>
        <label className="ws-field">안내 {index + 1} 제목<input value={item.title} maxLength={180} onChange={event => setDraft({ ...draft,
          notices: draft.notices.map(value => value.id === item.id ? { ...value, title: event.target.value } : value) })} /></label>
        <label className="ws-field">안내 {index + 1} 내용<textarea aria-label={`안내 ${index + 1} 내용`} value={item.content} maxLength={4000} onChange={event => setDraft({ ...draft,
          notices: draft.notices.map(value => value.id === item.id ? { ...value, content: event.target.value } : value) })} /></label>
        <label className="ws-check"><input type="checkbox" checked={item.required} onChange={event => setDraft({ ...draft,
          notices: draft.notices.map(value => value.id === item.id ? { ...value, required: event.target.checked } : value) })} />반드시 표시</label>
        <button onClick={() => setDraft({ ...draft, notices: draft.notices.filter(value => value.id !== item.id) })}>안내 삭제</button></div>)}
      <button onClick={() => setDraft({ ...draft, notices: [...draft.notices, { id: newId(), title: '', content: '', required: true }] })}>안내 추가</button>
      <div className="ws-actions"><button disabled={!draft.title.trim() || (!dirty && !!base)} onClick={() => void save()}>상품 지침 저장</button></div>
      <label className="ws-check"><input type="checkbox" disabled={!base || dirty} checked={checked} onChange={event => setChecked(event.target.checked)} />이 초안을 새 기준으로 게시합니다</label>
      <button disabled={!base || dirty || !checked} onClick={() => void save(true)}>버전 고정·지침 게시</button>
    </fieldset>
    {product?.publishedGuidelineId && <section className="ws-published"><h4>게시 지침 {product.publishedRevision}차</h4>
      <p>{ontology ? `저장된 연결 정보 확인 · ${ontology.nodes.length}개 항목 / ${ontology.edges.length}개 연결` : '저장된 연결 정보 미확인'}</p>
      <p>{affected === null ? '재검증 영향 미확인' : `재검증이 필요한 시안 ${affected.length}개`}</p>
      {affected?.length ? <ul>{affected.map(run => <li key={run.id}>이전 지침 시안 · 재검증 필요 <small>{run.status}</small></li>)}</ul> : null}
      <button onClick={() => setRevision(value => value + 1)}>게시 기준·영향 다시 조회</button>
      {guidelinePages(ontology).map(page => <article className="ws-planning-card" key={page.pageId}><strong>{page.title}</strong>
        <p>{page.required === true ? '필수 안내 화면' : page.required === false ? '선택 안내 화면' : '필수 여부 미확인'}</p>
        <p className="ws-notice-copy">{page.content}</p>
        <details><summary>화면 연결 식별자</summary><code>{page.pageId}</code></details>
        <p className="ws-muted">규칙 편집에서 이 화면 이름을 선택하면 검사 대상이 연결됩니다. 게시 지침은 실행 화면 검증 결과가 아닙니다.</p>
      </article>)}
      {ontology && <details><summary>연결 항목과 개발 식별자</summary><ul>{ontology.nodes.map((node, index) => <li key={index}>
        {String(node.label || node.title || node.type || '지침 항목')} · {String(node.id || '')}</li>)}</ul>
        <dl><dt>지침</dt><dd>{ontology.guidelineId}</dd><dt>연결 정보 해시</dt><dd>{ontology.hash}</dd></dl></details>}
    </section>}
  </section>;
}
