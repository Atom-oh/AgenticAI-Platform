// platform/web/src/studio/Playground.tsx
// 플레이그라운드 — 브리프·상품·자산·라운드 상한 → studio_run(에이전틱 루프) 스트리밍; 라이브 캔버스, 요소 선택 → refine, 라운드 타임라인, 체크리스트.
import { useEffect, useMemo, useRef, useState } from 'react';
import { sock } from '../lib';
import { openExplorer } from './nav';
import SpecPanel from './SpecPanel';
import { Checklist, RoundStrip, ScoreBadge, StageList } from './RoundTimeline';
import { AXES, BRIEF_PRESETS, Asset, DoneEvent, Draft, JobForm, OUTPUT_TYPES, Product, ReviewItem, RoundResult, Spec, StageEvent, TYPE_LABEL, aid } from './types';

type Sel = { selector: string; html: string; label: string } | null;

function cssPath(el: Element, doc: Document): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur !== doc.body && cur.nodeType === 1) {
    let seg = cur.tagName.toLowerCase();
    if (cur.id) { parts.unshift(`#${cur.id}`); break; }
    const parent: Element | null = cur.parentElement;
    if (parent) {
      const same = [...parent.children].filter(c => c.tagName === cur!.tagName);
      if (same.length > 1) seg += `:nth-of-type(${same.indexOf(cur) + 1})`;
    }
    parts.unshift(seg);
    cur = parent;
  }
  return parts.join(' > ');
}

export default function Playground({ assets, products, canWrite, initialDraft, onDone }: { assets: Asset[]; products: Product[]; canWrite: boolean; initialDraft?: Draft | null; onDone: () => void }) {
  const p0 = BRIEF_PRESETS[0];
  const [form, setForm] = useState<JobForm>({ brief: p0.brief, productCode: p0.productCode, outputType: p0.outputType, axis: '흐름', assetIds: [], agentId: '', maxRounds: 3, passScore: 85 });
  const [spec, setSpec] = useState<Spec | null>(null);
  const [specErr, setSpecErr] = useState('');
  const [specLoading, setSpecLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageEvent[]>([]);
  const [tokens, setTokens] = useState(0);
  const [history, setHistory] = useState<RoundResult[]>([]);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [done, setDone] = useState<DoneEvent | null>(null);
  const [canvasUrl, setCanvasUrl] = useState(initialDraft?.url || '');
  const [baseDraftId, setBaseDraftId] = useState(initialDraft?.draftId || '');
  const [pickedRound, setPickedRound] = useState<number | undefined>();
  const [mobile, setMobile] = useState(true);
  const [selectMode, setSelectMode] = useState(false);
  const [sel, setSel] = useState<Sel>(null);
  const [refineText, setRefineText] = useState('');
  const [turns, setTurns] = useState<{ role: 'user' | 'agent'; text: string }[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [msg, setMsg] = useState('');
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const selectModeRef = useRef(selectMode);
  useEffect(() => { selectModeRef.current = selectMode; }, [selectMode]);

  useEffect(() => {
    if (!form.productCode) { setSpec(null); return; }
    setSpecLoading(true); setSpecErr('');
    sock.request('studio_spec', { productCode: form.productCode, outputType: form.outputType })
      .then(e => { if (e.error) { setSpecErr(e.error); setSpec(null); } else setSpec(e.spec); })
      .catch(e => setSpecErr(String(e))).finally(() => setSpecLoading(false));
  }, [form.productCode, form.outputType]);

  useEffect(() => {
    if (!running) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 500);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (initialDraft) { setCanvasUrl(initialDraft.url); setBaseDraftId(initialDraft.draftId); setForm(f => ({ ...f, productCode: initialDraft.productCode, outputType: initialDraft.outputType })); }
  }, [initialDraft]);

  const groups = useMemo(() => {
    const g: Record<string, Asset[]> = {};
    for (const a of assets) (g[a.type] = g[a.type] || []).push(a);
    return g;
  }, [assets]);
  const agents = groups['agent'] || [];

  const wireSelection = () => {
    const iframe = iframeRef.current; if (!iframe) return;
    let doc: Document | null = null;
    try { doc = iframe.contentDocument; } catch { return; }
    if (!doc?.body) return;
    if (doc.body.dataset.wired === '1') return;
    doc.body.dataset.wired = '1';
    let hovered: HTMLElement | null = null; let outline = '';
    doc.addEventListener('mouseover', (e) => {
      if (!selectModeRef.current) return;
      if (hovered) hovered.style.outline = outline;
      hovered = e.target as HTMLElement; outline = hovered.style.outline; hovered.style.outline = '2px dashed #008485';
    }, true);
    doc.addEventListener('click', (e) => {
      if (!selectModeRef.current) return;
      e.preventDefault(); e.stopPropagation();
      doc!.querySelectorAll('[data-picked]').forEach(n => { (n as HTMLElement).style.outline = ''; n.removeAttribute('data-picked'); });
      const el = e.target as HTMLElement;
      const clean = el.outerHTML.slice(0, 4000);
      el.setAttribute('data-picked', '1'); el.style.outline = '3px solid #008485';
      const label = el.tagName.toLowerCase() + (el.textContent ? ` — "${el.textContent.trim().slice(0, 30)}"` : '');
      setSel({ selector: cssPath(el, doc!), html: clean, label });
    }, true);
  };
  const onEvent = (e: any) => {
    if (e.type === 'studio.stage') {
      setStages(s => [...s, e]);
      if (e.step === 'review') setItems(e.items || []);
      if (e.step === 'publish' && e.url) { setCanvasUrl(e.url); setPickedRound(e.round); setHistory(h => [...h.filter(x => x.round !== e.round), { round: e.round, score: e.score, passed: false, url: e.url, failures: [], undetermined: [], elapsedMs: 0 }]); }
    } else if (e.type === 'studio.token') {
      setTokens(t => t + (e.t?.length || 0));
    } else if (e.type === 'studio.done') {
      setDone(e as DoneEvent);
      setHistory(e.history || []);
      setItems(e.items || []);
      if (e.url) { setCanvasUrl(e.url); setPickedRound(e.bestRound); setBaseDraftId(e.draftId || ''); }
      setTurns(t => [...t, { role: 'agent', text: e.error ? `오류: ${e.error}` : `${e.rounds}라운드 · 최고 점수 ${e.score} (라운드 ${e.bestRound}) · ${({ passed: '통과', max_rounds: '라운드 상한 도달', time_cap: '시간 상한(13분)으로 중단', error: '오류' } as any)[e.stopReason]}` }]);
    }
  };

  const run = async (payload: Record<string, any>) => {
    if (!canWrite) return;
    setRunning(true); setStages([]); setTokens(0); setDone(null); setItems([]); setMsg(''); setSel(null);
    try { await sock.run('studio_run', payload, onEvent); }
    catch (e: any) { setMsg('오류: ' + (e?.message || e)); }
    finally { setRunning(false); onDone(); }
  };
  const generate = () => { setTurns(t => [...t, { role: 'user', text: form.brief }]); setHistory([]); run({ ...form, mode: 'generate' }); };
  const refine = () => {
    if (!baseDraftId || !refineText.trim()) { setMsg('수정 지시를 입력하세요 (원본 시안이 캔버스에 있어야 합니다)'); return; }
    setTurns(t => [...t, { role: 'user', text: `[수정${sel ? ' · ' + sel.label : ''}] ${refineText}` }]);
    run({ mode: 'refine', baseDraftId, selector: sel?.selector || '', elementHtml: sel?.html || '', instruction: refineText, productCode: form.productCode, outputType: form.outputType, axis: form.axis, assetIds: form.assetIds, agentId: form.agentId, maxRounds: 1, passScore: form.passScore, brief: '' });
    setRefineText('');
  };
  const toggleAsset = (id: string) => setForm(f => ({ ...f, assetIds: f.assetIds.includes(id) ? f.assetIds.filter(x => x !== id) : [...f.assetIds, id] }));
  const pickRound = (r: RoundResult) => { setPickedRound(r.round); if (r.url) setCanvasUrl(r.url); };
  const product = products.find(p => p.code === form.productCode);

  return (
    <div className="grid grid-cols-[1fr_1.35fr] gap-5">
      {/* 좌: 설정 */}
      <div className="space-y-4">
        <div className="panel p-5">
          <div className="text-sm font-bold text-slate-800 mb-2">① 브리프</div>
          <div className="flex gap-2 mb-2 flex-wrap">
            {BRIEF_PRESETS.map(p => (
              <button key={p.label} onClick={() => setForm(f => ({ ...f, brief: p.brief, productCode: p.productCode, outputType: p.outputType }))}
                className={`chip text-xs ${form.brief === p.brief ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500 hover:border-teal-300'}`}>{p.label}</button>
            ))}
          </div>
          <textarea className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-20" value={form.brief} onChange={e => setForm({ ...form, brief: e.target.value })} />

          <div className="text-sm font-bold text-slate-800 mt-4 mb-2">② 상품 (명세 출처 — 온톨로지 Product)</div>
          <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.productCode} onChange={e => setForm({ ...form, productCode: e.target.value })}>
            <option value="">상품 선택</option>
            {products.map(p => <option key={p.code} value={p.code}>{p.name} · {p.category} · 조건 {p.conditionCount}{p.hasPreferential ? ' · 우대' : ''}</option>)}
          </select>
          {product && <div className="mt-1 text-[11px] text-slate-500">{product.stepCount}단계 절차 · 조건 {product.conditionCount}개{product.hasPreferential && <span className="ml-2 chip text-[10px] text-amber-700 border-amber-300 bg-amber-50">우대 조건 → 조건 입력 스텝 검수</span>}</div>}

          <div className="grid grid-cols-2 gap-3 mt-4">
            <div>
              <div className="text-sm font-bold text-slate-800 mb-1">③ 출력 유형</div>
              <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.outputType} onChange={e => setForm({ ...form, outputType: e.target.value })}>
                {OUTPUT_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div>
              <div className="text-sm font-bold text-slate-800 mb-1">축</div>
              <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.axis} onChange={e => setForm({ ...form, axis: e.target.value })}>
                {AXES.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>

          <div className="text-sm font-bold text-slate-800 mt-4 mb-2">④ 자산 (조건이 됩니다)</div>
          {Object.entries(groups).filter(([t]) => t !== 'agent').map(([t, list]) => (
            <div key={t} className="mb-1"><span className="text-[11px] text-slate-400 mr-2">{TYPE_LABEL[t] || t}</span>
              {list.map(a => <button key={aid(a)} onClick={() => toggleAsset(aid(a))} className={`chip text-xs mr-1 mb-1 ${form.assetIds.includes(aid(a)) ? 'text-teal-800 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{a.name}</button>)}
            </div>
          ))}
          {agents.length > 0 && (
            <select className="w-full mt-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.agentId} onChange={e => setForm({ ...form, agentId: e.target.value })}>
              <option value="">에이전트 프리셋 없음</option>
              {agents.map(a => <option key={aid(a)} value={aid(a)}>{a.name}</option>)}
            </select>
          )}

          <div className="grid grid-cols-2 gap-3 mt-4">
            <label className="text-sm"><div className="font-bold text-slate-800 mb-1">⑤ 라운드 상한 <b className="text-[#008485]">{form.maxRounds}</b></div>
              <input type="range" min={1} max={20} value={form.maxRounds} onChange={e => setForm({ ...form, maxRounds: +e.target.value })} className="w-full" />
              <div className="text-[11px] text-slate-400">기본 3 · 최대 20 · 워커 13분 상한</div></label>
            <label className="text-sm"><div className="font-bold text-slate-800 mb-1">통과 점수 <b className="text-[#008485]">{form.passScore}</b></div>
              <input type="range" min={50} max={100} value={form.passScore} onChange={e => setForm({ ...form, passScore: +e.target.value })} className="w-full" />
              <div className="text-[11px] text-slate-400">필수 항목 전부 통과 + 점수 이상이면 종료</div></label>
          </div>

          <button onClick={generate} disabled={!canWrite || running || !form.productCode}
            className="w-full mt-4 py-2.5 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-bold text-sm disabled:opacity-40">
            {running ? `루프 실행 중… ${elapsed}s · ${tokens.toLocaleString()}자 수신` : '✨ 생성 → 명세 검수 → 수정 루프 시작'}
          </button>
          {!canWrite && <div className="text-xs text-slate-400 mt-2">이 계정은 조회 전용입니다</div>}
          {msg && <div className="text-[#E90061] text-xs mt-2">{msg}</div>}
        </div>

        <div className="panel p-4"><div className="text-sm font-bold text-slate-800 mb-2">검수 체크리스트 미리보기</div>
          <SpecPanel spec={spec} loading={specLoading} error={specErr} /></div>

        <div className="panel p-4">
          <div className="text-sm font-bold text-slate-800 mb-2">진행</div>
          {stages.length ? <StageList stages={stages} running={running} /> : <div className="text-xs text-slate-400">아직 실행 전</div>}
          {done && <div className="mt-3 flex items-center gap-2 text-xs"><ScoreBadge score={done.score} passed={done.passed} size="lg" />
            <div className="text-slate-600">{done.rounds}/{done.maxRounds} 라운드 · 최고 점수 라운드 {done.bestRound} 선택 · {done.stopReason === 'time_cap' ? '13분 상한으로 중단' : done.stopReason === 'max_rounds' ? '상한 도달' : done.stopReason === 'passed' ? '통과' : '오류'}<br />
              토큰 in {done.usage?.inputTokens} / out {done.usage?.outputTokens} · {done.model} · {Math.round(done.elapsedMs / 1000)}s</div></div>}
        </div>
      </div>

      {/* 우: 캔버스 + 체크리스트 */}
      <div className="space-y-4">
        <div className="panel p-3">
          <div className="flex items-center gap-2 mb-2 text-xs">
            <RoundStrip history={history} best={done?.bestRound || 0} onPick={pickRound} picked={pickedRound} />
            <span className="ml-auto" />
            <button onClick={() => setMobile(m => !m)} className="chip">{mobile ? '📱 모바일' : '🖥 전체 폭'}</button>
            <button onClick={() => setSelectMode(s => !s)} className={`chip ${selectMode ? 'text-teal-700 border-teal-400 bg-teal-50' : ''}`} disabled={!canvasUrl}>{selectMode ? '요소 선택 중' : '요소 선택'}</button>
            {canvasUrl && <a href={canvasUrl} target="_blank" rel="noopener" className="chip text-teal-700">새 탭 ↗</a>}
          </div>
          <div className={`mx-auto bg-slate-100 rounded-xl overflow-hidden border border-slate-200 ${mobile ? 'w-[420px]' : 'w-full'}`} style={{ height: 720 }}>
            {canvasUrl
              ? <iframe ref={iframeRef} key={canvasUrl} src={canvasUrl} title="canvas" className="w-full h-full bg-white" onLoad={wireSelection} />
              : <div className="h-full flex items-center justify-center text-sm text-slate-400">생성하면 라운드마다 시안이 여기 갱신됩니다</div>}
          </div>
          <div className="mt-2 text-[11px] text-slate-500">{sel ? `선택: ${sel.label}` : '선택된 요소 없음 — 전체 수정으로 적용됩니다'}</div>
          <div className="flex gap-2 mt-2">
            <input className="flex-1 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" placeholder="수정 지시 (예: 우대금리 문구를 더 크게)" value={refineText} onChange={e => setRefineText(e.target.value)} onKeyDown={e => e.key === 'Enter' && refine()} />
            <button onClick={refine} disabled={!canWrite || running || !baseDraftId} className="px-4 py-2 rounded-xl bg-white border border-teal-400 text-teal-700 text-sm font-semibold disabled:opacity-40">수정 (1라운드 검수)</button>
          </div>
          {turns.length > 0 && <div className="mt-2 max-h-28 overflow-auto text-xs space-y-1">{turns.map((t, i) => <div key={i} className={t.role === 'user' ? 'text-slate-700' : 'text-teal-700'}>{t.role === 'user' ? '👤 ' : '🤖 '}{t.text}</div>)}</div>}
        </div>
        <div className="panel p-4">
          <div className="flex items-center gap-2 mb-2"><div className="text-sm font-bold text-slate-800">검수 결과</div>
            <span className="chip text-[10px] text-slate-500">구조·문구·흐름 검수 — 픽셀 비교 미구현</span></div>
          {items.length ? <Checklist items={items} onSource={openExplorer} /> : <div className="text-xs text-slate-400">라운드가 끝나면 항목별 판정이 표시됩니다</div>}
        </div>
      </div>
    </div>
  );
}
