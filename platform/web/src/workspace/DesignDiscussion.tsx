import { useEffect, useMemo, useRef, useState } from 'react';
import { useWorkspaceScope } from './WorkspaceScope';
import { messageOf, resource } from './client';
import { Notice } from './shared';

type Anchor = { kind: 'prd' | 'flow-node' | 'rule'; id: string; sha256: string } |
  { kind: 'screen'; id: string; artifactId: string; sourceHash: string; bundleHash: string };
type Comment = { id: string; author: string; text: string; createdAt: number };
type Flow = { flow: { screens: string[] }; contract: { rules: { id: string; title: string }[] } | null };
type Variant = { artifactId: string; sourceHash: string; bundleHash: string };

export default function DesignDiscussion({ designId, prdHash, flowHash, flow, variant, screenIds, onDirty }: {
  designId: string; prdHash?: string | null; flowHash?: string | null; flow?: Flow | null;
  variant?: Variant; screenIds: string[]; onDirty: (dirty: boolean) => void;
}) {
  const { client, project } = useWorkspaceScope();
  const choices = useMemo(() => {
    const result: { label: string; anchor: Anchor }[] = [];
    if (prdHash) result.push({ label: 'PRD', anchor: { kind: 'prd', id: 'prd', sha256: prdHash } });
    if (flow && flowHash) {
      flow.flow.screens.forEach((id, index) => result.push({
        label: `흐름 노드 ${index + 1}`, anchor: { kind: 'flow-node', id, sha256: flowHash },
      }));
      flow.contract?.rules.forEach(rule => result.push({
        label: `확인 기준 · ${rule.title}`, anchor: { kind: 'rule', id: rule.id, sha256: flowHash },
      }));
    }
    if (variant) screenIds.forEach((id, index) => result.push({
      label: `선택한 시안 · 화면 ${index + 1}`, anchor: { kind: 'screen', id, ...variant },
    }));
    return result.map(item => ({ ...item, key: JSON.stringify(item.anchor) }));
  }, [prdHash, flowHash, flow, variant, screenIds]);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState('');
  const key = choices.some(item => item.key === selected) ? selected : choices[0]?.key || '';
  const [comments, setComments] = useState<Comment[]>([]);
  const [cursor, setCursor] = useState('');
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const request = useRef<{ text: string; id: string } | null>(null);
  const action = useRef<AbortController | null>(null);
  const base = `/designs/${resource(designId)}/comments`;
  useEffect(() => {
    const abort = new AbortController();
    setComments([]); setCursor(''); setError('');
    if (open && key) client.get<{ comments: Comment[]; cursor?: string }>(
      `${base}?${new URLSearchParams({ anchor: key })}`, abort.signal,
    ).then(value => {
      if (!abort.signal.aborted) { setComments(value.comments); setCursor(value.cursor || ''); }
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, base, key, open, refresh]);
  useEffect(() => { onDirty(!!text.trim() || busy); }, [text, busy, onDirty]);
  useEffect(() => () => { action.current?.abort(); onDirty(false); }, [onDirty]);
  const send = async (more = false) => {
    if (busy || !key || (!more && !text.trim())) return;
    const abort = new AbortController(); action.current?.abort(); action.current = abort;
    setBusy(true); setError('');
    try {
      if (more) {
        const value = await client.get<{ comments: Comment[]; cursor?: string }>(
          `${base}?${new URLSearchParams({ anchor: key, cursor })}`, abort.signal,
        );
        if (!abort.signal.aborted) {
          setComments(previous => [...previous, ...value.comments.filter(
            item => !previous.some(existing => existing.id === item.id))]);
          setCursor(value.cursor || '');
        }
      } else {
        if (request.current?.text !== text.trim()) request.current = { text: text.trim(), id: crypto.randomUUID() };
        const value = await client.post<{ comment: Comment }>(base, {
          requestId: request.current.id, text: request.current.text, anchor: JSON.parse(key),
        }, abort.signal);
        if (!abort.signal.aborted) {
          setComments(previous => [...previous.filter(item => item.id !== value.comment.id), value.comment]);
          setText(''); request.current = null;
        }
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  return <details className="ws-context-discussion" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>PRD·흐름·화면에 의견 남기기</summary>
    <p className="ws-muted">선택한 근거의 정확한 버전에 기록됩니다. 의견 작성은 설계 승인이 아닙니다.</p>
    {!key ? <Notice>PRD나 흐름, 실제 시안을 준비하면 해당 근거에 의견을 남길 수 있습니다.</Notice> : <>
      <label className="ws-field">의견을 연결할 근거<select value={key} disabled={busy || !!text.trim()}
        onChange={event => setSelected(event.target.value)}>
        {choices.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
      </select></label>
      {error && <Notice error>{error} <button onClick={() => setRefresh(value => value + 1)}>설계 의견 다시 조회</button></Notice>}
      <ul className="ws-comments">{comments.map(item => <li key={item.id}>
        <strong>{project?.members[item.author]?.displayName || '참여자'}</strong><p>{item.text}</p>
        <small>{new Date(item.createdAt < 1e12 ? item.createdAt * 1000 : item.createdAt).toLocaleString('ko-KR')}</small>
      </li>)}</ul>
      {cursor && <button disabled={busy} onClick={() => void send(true)}>설계 의견 더 보기</button>}
      <label className="ws-field">설계 의견 내용<textarea value={text} rows={4} maxLength={4000}
        onChange={event => setText(event.target.value)} /></label>
      <button disabled={busy || !text.trim()} onClick={() => void send()}>선택한 근거에 의견 남기기</button>
      {text && <button disabled={busy} onClick={() => { setText(''); request.current = null; }}>의견 작성 취소</button>}
    </>}
  </details>;
}
