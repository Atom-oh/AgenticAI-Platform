import { useEffect, useRef, useState } from 'react';
import { useWorkspaceScope } from './WorkspaceScope';
import { messageOf, resource } from './client';
import { Notice } from './shared';

type Golden = { id: string; case: Record<string, boolean>; mustShow: { screen: string; text: string }[];
  mustNotShow: { screen: string; text: string }[]; endsAt: string };
type Reference = { id: string; title: string; criteriaHash: string;
  baselineScore: { passed: string[]; failed: string[]; incomplete: string[]; blocked: boolean } };
type Comparison = { comparison: { improved: string[]; regressed: string[]; blocked: boolean };
  beforeScore: { rate: number | null }; afterScore: { rate: number | null }; approvalEffect: false };

export default function DesignReferencePanel({ designId, variant, flow, cases, canRegenerate, onRegenerate, onDirty }: {
  designId: string; variant: { artifactId: string; sourceHash: string; bundleHash: string };
  flow: { screens: string[]; terminals?: string[] }; cases: Record<string, boolean>[];
  canRegenerate: boolean; onRegenerate: () => void; onDirty: (value: boolean) => void;
}) {
  const { client, role } = useWorkspaceScope();
  const [open, setOpen] = useState(false);
  const [references, setReferences] = useState<Reference[]>([]);
  const [cursor, setCursor] = useState('');
  const [selected, setSelected] = useState('');
  const [title, setTitle] = useState('');
  const [caseIndex, setCaseIndex] = useState('0');
  const [screen, setScreen] = useState(flow.screens[0] || '');
  const [terminal, setTerminal] = useState(flow.terminals?.[0] || '');
  const [show, setShow] = useState('');
  const [hide, setHide] = useState('');
  const [golden, setGolden] = useState<Golden[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const request = useRef<{ key: string; body: Record<string, unknown> } | null>(null);
  const action = useRef<AbortController | null>(null);
  const editable = role === 'owner' || role === 'designer';
  const selectedReference = references.find(item => item.id === selected);
  useEffect(() => {
    const abort = new AbortController(); setReferences([]); setCursor(''); setError('');
    if (open) client.get<{ references: Reference[]; cursor?: string }>('/designs/reference-sets', abort.signal)
      .then(value => { if (!abort.signal.aborted) { setReferences(value.references); setCursor(value.cursor || ''); } })
      .catch(value => { if (!abort.signal.aborted) setError(messageOf(value)); });
    return () => abort.abort();
  }, [client, open, refresh]);
  useEffect(() => { onDirty(!!title.trim() || !!show.trim() || !!hide.trim() || !!golden.length || busy); },
    [title, show, hide, golden, busy, onDirty]);
  useEffect(() => () => { action.current?.abort(); onDirty(false); }, [onDirty]);
  const act = async (kind: 'create' | 'compare' | 'more') => {
    if (busy) return;
    const abort = new AbortController(); action.current?.abort(); action.current = abort;
    setBusy(true); setError('');
    try {
      if (kind === 'more') {
        const value = await client.get<{ references: Reference[]; cursor?: string }>(
          `/designs/reference-sets?cursor=${resource(cursor)}`, abort.signal);
        if (!abort.signal.aborted) {
          setReferences(previous => [...previous, ...value.references.filter(item => !previous.some(old => old.id === item.id))]);
          setCursor(value.cursor || '');
        }
        return;
      }
      const reference = references.find(item => item.id === selected);
      if (kind === 'compare' && !reference) throw new Error('비교할 기준 세트를 다시 선택하세요.');
      // The API accepts only these variant bindings; full variant rows carry run metadata.
      const binding = { artifactId: variant.artifactId, sourceHash: variant.sourceHash, bundleHash: variant.bundleHash };
      const data = kind === 'create' ? { title: title.trim(), golden, designId, ...binding } :
        { designId, ...binding, criteriaHash: reference?.criteriaHash };
      const path = kind === 'create' ? '/designs/reference-sets' : `/designs/reference-sets/${resource(selected)}/compare`;
      const key = JSON.stringify([path, data]);
      if (request.current?.key !== key) request.current = { key, body: { ...data, requestId: crypto.randomUUID() } };
      const value = await client.post<{ reference?: Reference; comparison?: Comparison }>(path, request.current.body, abort.signal);
      if (!abort.signal.aborted) {
        request.current = null;
        if (value.reference) { setSelected(value.reference.id); setTitle(''); setGolden([]); setRefresh(value => value + 1); }
        if (value.comparison) setComparison(value.comparison);
      }
    } catch (value) { if (!abort.signal.aborted) setError(messageOf(value)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <details className="ws-context-discussion" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>기준 화면 세트로 온톨로지 변경 비교</summary>
    <p className="ws-muted">사람이 정한 문구·조건·도착점은 고정됩니다. 실제 화면 관찰이 없거나 권한이 회수되면 비교할 수 없습니다.</p>
    {error && <Notice error>{error} <button onClick={() => setRefresh(value => value + 1)}>기준 다시 조회</button></Notice>}
    <label className="ws-field">고정한 기준 세트<select disabled={busy} value={selected} onChange={event => { setSelected(event.target.value); setComparison(null); }}>
      <option value="">기준 선택</option>{references.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
    </select></label>
    {cursor && <button disabled={busy} onClick={() => void act('more')}>기준 세트 더 보기</button>}
    <p className="ws-muted">온톨로지가 바뀌었다면 변경된 기준으로 새 설계를 만들고 PRD·흐름을 승인한 뒤 이 기준 세트와 비교하세요.</p>
    {editable && <button disabled={busy || !canRegenerate} onClick={onRegenerate}>현재 설계 기준으로 시안 재생성</button>}
    <button disabled={busy || !editable || !selectedReference} onClick={() => void act('compare')}>현재 시안을 같은 기준으로 비교</button>
    {comparison && <div role="status">
      <p>통과 비율: {comparison.beforeScore.rate === null ? '미측정' : `${Math.round(comparison.beforeScore.rate * 100)}%`} →
        {comparison.afterScore.rate === null ? '미측정' : `${Math.round(comparison.afterScore.rate * 100)}%`}</p>
      <p>개선 {comparison.comparison.improved.length}개 · 회귀 {comparison.comparison.regressed.length}개
        {comparison.comparison.blocked ? ' · 관찰이 부족하여 판단 보류' : ''}</p>
      <p className="ws-muted">비교 결과는 시안 승인이나 배포 승인이 아닙니다.</p>
    </div>}
    {editable && <details><summary>사람이 정한 독립 기준 저장</summary>
      <label className="ws-field">기준 세트 이름<input value={title} maxLength={160} onChange={event => setTitle(event.target.value)} /></label>
      <label className="ws-field">기준 조건 사례<select value={caseIndex} onChange={event => setCaseIndex(event.target.value)}>
        {cases.map((_, index) => <option key={index} value={String(index)}>조건 사례 {index + 1}</option>)}
      </select></label>
      <label className="ws-field">문구를 확인할 화면<select value={screen} onChange={event => setScreen(event.target.value)}>
        {flow.screens.map((id, index) => <option key={id} value={id}>화면 {index + 1}</option>)}
      </select></label>
      <label className="ws-field">반드시 보여야 할 문구<textarea value={show} maxLength={4000} rows={2} onChange={event => setShow(event.target.value)} /></label>
      <label className="ws-field">이 사례의 전체 경로에서 보이면 안 되는 문구<textarea value={hide} maxLength={4000} rows={2} onChange={event => setHide(event.target.value)} /></label>
      <label className="ws-field">기대 도착 화면<select value={terminal} onChange={event => setTerminal(event.target.value)}>
        {(flow.terminals || []).map(id => <option key={id} value={id}>화면 {flow.screens.indexOf(id) + 1}</option>)}
      </select></label>
      <button disabled={busy || !cases[Number(caseIndex)] || !screen || !terminal || golden.length >= 64}
        onClick={() => {
          setGolden(previous => [...previous, { id: `criterion-${previous.length + 1}`, case: cases[Number(caseIndex)],
            mustShow: show.trim() ? [{ screen, text: show.trim() }] : [],
            mustNotShow: hide.trim() ? [{ screen: '*', text: hide.trim() }] : [], endsAt: terminal }]);
          setShow(''); setHide('');
        }}>이 독립 기준 추가</button>
      <p>기록할 독립 기준 {golden.length}개</p>
      <ul className="ws-comments">{golden.map(item => <li key={item.id}>
        <p>도착: 화면 {flow.screens.indexOf(item.endsAt) + 1}</p>
        {item.mustShow.map((value, index) => <p key={index}>필수 문구: {value.text}</p>)}
        {item.mustNotShow.map((value, index) => <p key={index}>금지 문구: {value.text}</p>)}
      </li>)}</ul>
      <button disabled={busy || !title.trim() || !golden.length} onClick={() => void act('create')}>현재 관찰과 기준 세트 고정</button>
      {(golden.length > 0 || title) && <button disabled={busy} onClick={() => { setTitle(''); setGolden([]); setShow(''); setHide(''); request.current = null; }}>기준 작성 취소</button>}
    </details>}
  </details>;
}
