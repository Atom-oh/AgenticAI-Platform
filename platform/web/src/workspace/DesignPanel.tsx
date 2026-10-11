import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useWorkspaceScope } from './WorkspaceScope';
import { messageOf, resource } from './client';
import { Notice, previewFrameDocument } from './shared';
import { readPreviewSelection } from './previewSelection';
import DesignHandoff from './DesignHandoff';
import FlowDiagram from './FlowDiagram';
import DesignComparison from './DesignComparison';
import type { WorkspaceConfig } from './types';

type Variant = { artifactId: string; jobId: string; variant: number; passed: boolean; sourceHash: string; bundleHash: string;
  verification: { verdict: string; findings?: { code: string; severity: string; message?: string }[] } };
type Design = { id: string; title: string; version: number; revision: number; status: string; model: string;
  activeJobId?: string | null; unknownOutcome?: boolean; variants: Variant[]; approvals: { prd?: unknown; flow?: unknown;
    ux?: { artifactId: string } }; releases?: { id: string; sourceHash: string; bundleHash: string }[] };
type Input = { id: string; sourceId: string; kind: string; pageCount: number };
type Doc = { id: string; title: string; approvedRevisionId?: string };
type Procedure = { id: string; title: string; type: string; reviewState: string };
type Flow = { flow: { screens: string[]; transitions: { id: string; src: string; dst: string; when?: string | null }[] };
  issues: { code?: string; message?: string }[]; contract: { rules: { id: string; title: string }[] } | null };
type Detail = { design: Design; prd?: { prd: Record<string, unknown>; issues: { field: string; code: string }[] }; flow?: Flow; flowHash?: string };
const PHASE: Record<string, string> = { draft: '자료 선택 완료', executing: '생성·검증 중', 'prd-review': 'PRD 확인 필요',
  'flow-review': '흐름 확인 필요', ready: '시안 생성 가능', review: '시안 검토', approved: '시안 승인됨', released: '개발 전달 준비됨',
  interrupted: '실행 중단 · 결과 확인 필요', cancelled: '취소됨' };
function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, v]) => [k, sorted(v)]));
  return value;
}
async function hash(value: unknown) {
  const result = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(sorted(value))));
  return [...new Uint8Array(result)].map(value => value.toString(16).padStart(2, '0')).join('');
}

export default function DesignPanel({ config, onDirty }: { config: WorkspaceConfig; onDirty: (value: boolean) => void }) {
  const { client, role } = useWorkspaceScope();
  const [enabled, setEnabled] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [designs, setDesigns] = useState<Design[]>([]);
  const [inputs, setInputs] = useState<Input[]>([]);
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [procedures, setProcedures] = useState<Procedure[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [title, setTitle] = useState('');
  const [procedure, setProcedure] = useState('');
  const [decisions, setDecisions] = useState<string[]>([]);
  const [model, setModel] = useState(config.defaultModel);
  const [prd, setPrd] = useState('');
  const [variantId, setVariantId] = useState('');
  const [preview, setPreview] = useState('');
  const [findings, setFindings] = useState<Variant['verification'] | null>(null);
  const [instruction, setInstruction] = useState('');
  const [instructionBinding, setInstructionBinding] = useState<{ id: string; text: string; designId: string; artifactId: string } | null>(null);
  const instructionId = instructionBinding?.id || '';
  const selection = useRef({ designId: selected, artifactId: variantId, instruction });
  selection.current = { designId: selected, artifactId: variantId, instruction };
  const previewRequest = useRef<AbortController | null>(null);
  const [viewed, setViewed] = useState<{ designId: string; artifactId: string; sourceHash: string; bundleHash: string } | null>(null);
  const [targets, setTargets] = useState<{ screenId: string; state: string; targetId: string; testId: string; label: string }[]>([]);
  const [target, setTarget] = useState('');
  const [selectMode, setSelectMode] = useState(false);
  const iframe = useRef<HTMLIFrameElement | null>(null);
  const selectionNonce = useMemo(() => crypto.randomUUID(), [viewed, preview]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const request = useRef<{ key: string; body: Record<string, unknown> } | null>(null);
  const operation = useRef<AbortController | null>(null);
  const plan = role === 'owner' || role === 'planner';
  const designRole = role === 'owner' || role === 'designer';
  const developer = role === 'owner' || role === 'developer';
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [settings, records, docs, graph] = await Promise.all([
      client.get<{ enabled: boolean; models: string[] }>('/designs/config', signal),
      (async () => {
        const designs: Design[] = []; let cursor: string | undefined; const seen = new Set<string>();
        do {
          const page = await client.get<{ designs: Design[]; cursor?: string }>(`/designs${cursor ? '?cursor=' + encodeURIComponent(cursor) : ''}`, signal);
          designs.push(...page.designs); cursor = page.cursor;
          if (cursor && seen.has(cursor)) throw new Error('설계 목록 페이지가 반복되었습니다. 새로 조회하세요.');
          if (cursor) seen.add(cursor);
        } while (cursor && !signal?.aborted);
        return { designs };
      })(),
      client.get<{ documents: Doc[] }>('/documents', signal),
      client.get<{ nodes: Procedure[] }>('/ontology', signal),
    ]);
    if (signal?.aborted) return;
    setEnabled(settings.enabled); setModels(settings.models); setDesigns(records.designs);
    setDocuments(docs.documents); setProcedures(graph.nodes.filter(n => n.type === 'Procedure' && n.reviewState === 'approved'));
    const all: Input[] = [];
    let cursor: string | undefined;
    do {
      const page = await client.get<{ inputs: Input[]; cursor?: string }>(`/designs/inputs${cursor ? '?cursor=' + encodeURIComponent(cursor) : ''}`, signal);
      all.push(...page.inputs); cursor = page.cursor;
    } while (cursor && all.length < 100 && !signal?.aborted);
    if (!signal?.aborted) setInputs(all);
  }, [client]);
  useEffect(() => {
    const abort = new AbortController();
    void refresh(abort.signal).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [refresh]);
  const load = useCallback(async (id: string, signal?: AbortSignal) => {
    const value = await client.get<Detail>(`/designs/${resource(id)}`, signal);
    if (!signal?.aborted) {
      setDetail(value); setDesigns(items => [value.design, ...items.filter(item => item.id !== id)]);
      setPrd(JSON.stringify(value.prd?.prd || {}, null, 2));
    }
  }, [client]);
  useEffect(() => {
    previewRequest.current?.abort(); operation.current?.abort(); setBusy(false);
    setDetail(null); setPreview(''); setViewed(null); setVariantId(''); setFindings(null); setTargets([]); setTarget(''); setAcknowledge(false);
    setInstruction(''); setInstructionBinding(null);
    if (!selected) return;
    const abort = new AbortController();
    void load(selected, abort.signal).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [selected, load]);
  useEffect(() => {
    if (!detail?.design.activeJobId) return;
    const abort = new AbortController();
    const timer = window.setInterval(() => void load(detail.design.id, abort.signal).catch(reason => {
      if (!abort.signal.aborted) setError(messageOf(reason));
    }), 2500);
    return () => { clearInterval(timer); abort.abort(); };
  }, [detail?.design.id, detail?.design.activeJobId, load]);
  useEffect(() => {
    const dirty = !!instruction.trim() || (!!detail?.prd && prd !== JSON.stringify(detail.prd.prd, null, 2));
    onDirty(dirty || busy);
  }, [instruction, prd, detail?.prd, busy, onDirty]);
  useEffect(() => () => { operation.current?.abort(); previewRequest.current?.abort(); onDirty(false); }, [onDirty]);
  const act = async (path: string, body: Record<string, unknown>, idempotent = false) => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    const abort = new AbortController(); operation.current?.abort(); operation.current = abort;
    if (detail?.design.unknownOutcome && /\/(extract|generate|edit|release)$/.test(path)) {
      if (!acknowledge) { setError('이전 실행의 추가 비용 가능성을 먼저 확인하세요.'); setBusy(false); return; }
      body = { ...body, acknowledgeUnknownOutcome: true };
    }
    const subject = { ...selection.current };
    const key = JSON.stringify([path, body]);
    if (idempotent) {
      request.current = request.current?.key === key ? request.current : { key, body: { ...body, requestId: crypto.randomUUID() } };
      body = request.current.body;
    }
    try {
      const result = await client.post<{ design?: Design; decision?: { id?: string; status: string; blocking?: string[] } }>(path, body, abort.signal);
      if (abort.signal.aborted) return;
      request.current = null;
      if (result.design) { setSelected(result.design.id); await load(result.design.id, abort.signal); }
      if (result.decision) {
        if (result.decision.id && path === '/designs/instructions') {
          setInstructionBinding({ id: result.decision.id, text: String(body.text), designId: subject.designId, artifactId: subject.artifactId });
        }
        setNotice(result.decision.status === 'admitted' ? '자료 사용 승인이 완료되었습니다.' :
          result.decision.status === 'blocked' ? `자료 사용이 차단되었습니다: ${(result.decision.blocking || []).join(', ')}` : '지정된 검토자의 자료 사용 승인을 기다리고 있습니다.');
        await refresh(abort.signal);
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  };
  const show = async (id: string) => {
    if (!detail) return;
    const subject = detail.design;
    const expected = subject.variants.find(item => item.artifactId === id);
    if (!expected) return;
    const controller = new AbortController(); previewRequest.current?.abort(); previewRequest.current = controller;
    setError(''); setPreview(''); setViewed(null); setFindings(null); setVariantId(id); setTargets([]); setTarget('');
    setInstructionBinding(null); setInstruction('');
    try {
      const value = await client.get<{ previewHtml: string; verification: Variant['verification']; targets: typeof targets; sourceHash: string; bundleHash: string }>(
        `/designs/${resource(subject.id)}/artifacts/${resource(id)}`, controller.signal);
      if (controller.signal.aborted || previewRequest.current !== controller || selection.current.designId !== subject.id) return;
      if (value.sourceHash !== expected.sourceHash || value.bundleHash !== expected.bundleHash ||
          (expected.passed && value.verification.verdict !== 'pass')) throw new Error('선택한 시안과 표시할 검증 근거가 다릅니다.');
      setPreview(value.previewHtml); setFindings(value.verification); setTargets(value.targets);
      setViewed({ designId: subject.id, artifactId: id, sourceHash: value.sourceHash, bundleHash: value.bundleHash });
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
  };
  const current = detail?.design;
  const active = busy || !!current?.activeJobId;
  const selectedVariant = current?.variants.find(v => v.artifactId === variantId);
  const viewMatches = !!viewed && viewed.designId === current?.id && viewed.artifactId === selectedVariant?.artifactId &&
    viewed.sourceHash === selectedVariant?.sourceHash && viewed.bundleHash === selectedVariant?.bundleHash;
  const approvedInstruction = !!instructionBinding && instructionBinding.text === instruction &&
    instructionBinding.designId === current?.id && instructionBinding.artifactId === selectedVariant?.artifactId &&
    inputs.some(item => item.id === instructionId && item.kind === 'prompt-text');
  const syncSelection = () => iframe.current?.contentWindow?.postMessage({ type: 'studio-select-mode', channel: selectionNonce, enabled: selectMode }, '*');
  useEffect(() => {
    syncSelection();
    const receive = (event: MessageEvent) => {
      if (!selectMode || !viewMatches || event.source !== iframe.current?.contentWindow || event.origin !== 'null' ||
          event.data?.type !== 'studio-element' || event.data.channel !== selectionNonce) return;
      const selected = readPreviewSelection(event.data.selection);
      if (!selected) return;
      const index = targets.findIndex(item => selected.selector === `[data-testid="${CSS.escape(item.testId)}"]` || selected.selector === '#' + CSS.escape(item.testId));
      if (index >= 0) setTarget(String(index));
    };
    window.addEventListener('message', receive); return () => window.removeEventListener('message', receive);
  }, [selectMode, selectionNonce, targets, viewMatches]);
  const shownPreview = useMemo(() => preview ? previewFrameDocument(preview, true, selectionNonce) : '', [preview, selectionNonce]);
  return <section className="ws-procedure-design" aria-label="절차 기반 설계">
    <div className="ws-design-toolbar"><label className="ws-field">진행 중인 설계<select value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}>
      <option value="">새 설계</option>{designs.map(item => <option key={item.id} value={item.id}>{item.title} · {PHASE[item.status] || item.status}</option>)}
    </select></label><button onClick={() => void refresh().catch(reason => setError(messageOf(reason)))}>새로 조회</button></div>
    {error && <Notice error>{error}</Notice>}{notice && <Notice>{notice}</Notice>}
    {!enabled && <Notice>이 작업 공간의 실제 설계 실행은 아직 활성화되지 않았습니다. 자료와 승인 흐름을 먼저 준비할 수 있습니다.</Notice>}
    <div className="ws-design-canvas-grid"><aside className="ws-design-control">
      {!current ? <>
        <h3>무엇을 설계할까요?</h3>
        <label className="ws-field">설계 이름<input value={title} maxLength={120} onChange={e => setTitle(e.target.value)} /></label>
        <label className="ws-field">검토된 업무 절차<select value={procedure} onChange={e => setProcedure(e.target.value)}>
          <option value="">절차 선택</option>{procedures.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select></label>
        <label className="ws-field">사용 모델<select value={model} onChange={e => setModel(e.target.value)}>{config.models.map(item =>
          <option key={item.id} value={item.id}>{item.label}{models.includes(item.id) ? '' : ' · 실행 미활성'}</option>)}</select></label>
        <h4>AI 사용이 승인된 자료</h4>{inputs.filter(item => item.kind !== 'prompt-text').map(item => <label className="ws-design-input" key={item.id}>
          <input type="checkbox" checked={decisions.includes(item.id)} onChange={e => setDecisions(old => e.target.checked ? [...old, item.id] : old.filter(id => id !== item.id))} />
          {documents.find(doc => doc.id === item.sourceId)?.title || `승인 자료 ${item.id.slice(-8)}`} · {item.pageCount}쪽
        </label>)}
        {!inputs.length && <p>승인된 문서를 선택하고 AI 사용 검토를 요청하세요.</p>}
        <details><summary>문서의 AI 사용 검토 요청</summary>{documents.filter(doc => doc.approvedRevisionId).map(doc => <div key={doc.id}>
          <span>{doc.title}</span><button disabled={busy} onClick={() => void act('/designs/admissions', { documentId: doc.id })}>검토 요청</button>
        </div>)}<p>문서 승인과 AI 사용 승인은 별개입니다. 지정된 검토자가 비민감 자료 여부를 확인합니다.</p></details>
        <button className="ws-primary" disabled={busy || !(plan || designRole) || !title.trim() || !procedure || !decisions.length}
          onClick={() => void act('/designs', { title: title.trim(), procedureId: procedure, decisionIds: decisions, model }, true)}>설계 시작</button>
      </> : <>
        <h3>{current.title}</h3><p role="status">{PHASE[current.status] || current.status}</p>
        {current.unknownOutcome && <label><input type="checkbox" checked={acknowledge} onChange={e => setAcknowledge(e.target.checked)} />이전 실행 여부가 확정되지 않았으며, 재실행 시 추가 비용이 발생할 수 있음을 확인했습니다.</label>}
        {current.activeJobId && <button disabled={busy} onClick={() => void act(`/designs/${resource(current.id)}/cancel`, {})}>실행 중단</button>}
        {!detail?.prd && <button disabled={active || !enabled || !(plan || designRole)} onClick={() => void act(`/designs/${resource(current.id)}/extract`, { version: current.version }, true)}>문서에서 PRD 정리</button>}
        {detail?.prd && <details open={!current.approvals.prd}><summary>PRD · 원문 인용과 수치 확인</summary>
          {detail.prd.issues.map((issue, index) => <p key={index}>{issue.field}: {issue.code}</p>)}
          <textarea aria-label="PRD와 원문 인용" rows={15} value={prd} onChange={e => setPrd(e.target.value)} disabled={!plan || active} />
          <button disabled={!plan || active || ['approved', 'released'].includes(current.status)} onClick={() => void (async () => {
            try { const value: unknown = JSON.parse(prd); await act(`/designs/${resource(current.id)}/confirm-prd`, { version: current.version, prd: value, hash: await hash(value) }); }
            catch (reason) { setError(messageOf(reason)); }
          })()}>이 PRD 확정</button>
        </details>}
        {detail?.flow && <details open={!current.approvals.flow}><summary>흐름과 필수 확인 기준</summary>
          <FlowDiagram key={`${current.id}:${detail.flowHash}`} screens={detail.flow.flow.screens.map((id, index) => ({ id, title: `화면 ${index + 1} · ${id}` }))}
            transitions={detail.flow.flow.transitions.map(item => ({ id: item.id, from: item.src, to: item.dst, condition: item.when }))} />
          {detail.flow.contract?.rules.map(rule => <p key={rule.id}>✓ {rule.title}</p>)}
          {detail.flow.issues.map((issue, index) => <p key={index}>{issue.code || issue.message}</p>)}
          <button disabled={!designRole || active || ['approved', 'released'].includes(current.status) || !!detail.flow.issues.length} onClick={() => void act(`/designs/${resource(current.id)}/approve-flow`, { version: current.version, hash: detail.flowHash })}>이 흐름과 확인 기준 승인</button>
        </details>}
        {current.approvals.flow && <button className="ws-primary" disabled={!designRole || active || !enabled || !['ready', 'review', 'interrupted'].includes(current.status) || current.variants.length >= 5}
          onClick={() => void act(`/designs/${resource(current.id)}/generate`, { version: current.version, variant: current.variants.length, maxRounds: 3 }, true)}>
          {current.variants.length ? '다른 시안 만들기' : '시안 만들기'}</button>}
        <div className="ws-design-variants">{current.variants.map((item, index) => <button key={item.artifactId} aria-pressed={variantId === item.artifactId}
          onClick={() => void show(item.artifactId)}>시안 {index + 1} · {item.passed ? '검증 통과' : '수정 필요'}</button>)}</div>
        {selectedVariant && <>
          <button disabled={!designRole || active || !selectedVariant.passed || !viewMatches} onClick={() => void act(`/designs/${resource(current.id)}/approve`, {
            version: current.version, artifactId: selectedVariant.artifactId, sourceHash: selectedVariant.sourceHash, bundleHash: selectedVariant.bundleHash })}>이 시안 승인</button>
          <details><summary>선택한 부분 수정</summary><label className="ws-field">수정 대상<select value={target} onChange={e => setTarget(e.target.value)}>
            <option value="">요소 선택</option>{targets.map((item, index) => <option key={index} value={String(index)}>{item.label}</option>)}</select></label>
            <textarea aria-label="수정 지시문" value={instruction} maxLength={4000} onChange={e => { setInstruction(e.target.value); setInstructionBinding(null); }} placeholder="선택한 요소에서 바꿀 점을 적으세요." />
            <button disabled={active || !designRole || !instruction.trim()} onClick={() => void act('/designs/instructions', { text: instruction })}>수정 지시문 사용 검토 요청</button>
            {instructionId && <p>{approvedInstruction ? '수정 지시문 승인됨' : '지정된 검토자의 승인이 필요합니다. 승인 후 새로 조회하세요.'}</p>}
            <button disabled={active || !designRole || target === '' || !approvedInstruction || !enabled || !viewMatches} onClick={() => void act(`/designs/${resource(current.id)}/edit`, {
              version: current.version, baseArtifactId: selectedVariant.artifactId, instructionDecisionId: instructionId,
              screenId: targets[Number(target)].screenId, state: targets[Number(target)].state, targetId: targets[Number(target)].targetId, variant: selectedVariant.variant, maxRounds: 3 }, true)}>선택 영역 수정·재검증</button>
          </details>
          {current.approvals.ux?.artifactId === selectedVariant.artifactId && <button disabled={active || !(designRole || developer) || !enabled}
            onClick={() => void act(`/designs/${resource(current.id)}/release`, { version: current.version, baseArtifactId: selectedVariant.artifactId }, true)}>승인 소스 재빌드·전달 준비</button>}
        </>}
        <DesignHandoff designId={current.id} releaseIds={(current.releases || []).map(item => item.id)} />
      </>}
    </aside><main className="ws-design-preview">
      {preview && <button aria-pressed={selectMode} onClick={() => setSelectMode(value => !value)}>{selectMode ? '선택 모드 종료 · 화면 동작 확인' : '화면에서 수정 요소 선택'}</button>}
      {preview ? <iframe ref={iframe} title="선택한 시안 미리보기" sandbox="allow-scripts" referrerPolicy="no-referrer" onLoad={syncSelection} srcDoc={shownPreview} /> : <div className="ws-design-empty">
        <h3>시안을 보며 설계하세요</h3><p>자료와 PRD, 흐름을 확인하면 이곳에서 실제 React 시안을 검토하고 수정할 수 있습니다.</p>
      </div>}
      {findings && <div><strong>{findings.verdict === 'pass' ? '검증 통과' : '확인이 필요합니다'}</strong>
        {(findings.findings || []).map((item, index) => <p key={index}>{item.severity} · {item.message || item.code}</p>)}</div>}
    </main></div>
    {!!current?.variants.length && <DesignComparison designId={current.id} variants={current.variants} selected={variantId} onSelect={id => void show(id)} />}
  </section>;
}
