// platform/web/src/studio/Playground.tsx
// 플레이그라운드 — 브리프·상품·자산·라운드 상한 → studio_run(에이전틱 루프) 스트리밍; 라이브 캔버스, 요소 선택 → refine, 라운드 타임라인, 체크리스트.
import { useEffect, useMemo, useRef, useState } from 'react';
import { sock } from '../lib';
import { openExplorer } from './nav';
import SpecPanel from './SpecPanel';
import ModelSelect, { ModelOption } from './ModelSelect';
import { loadRoundReport } from './reports';
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

export default function Playground({ assets, products, models, defaultModel, canWrite, initialDraft, onDone }: {
  assets: Asset[]; products: Product[]; models: ModelOption[]; defaultModel: string; canWrite: boolean; initialDraft?: Draft | null; onDone: () => void;
}) {
  const p0 = BRIEF_PRESETS[0];
  const [form, setForm] = useState<JobForm>({ brief: p0.brief, productCode: p0.productCode, outputType: p0.outputType, axis: '흐름', assetIds: [], agentId: '', maxRounds: 3, passScore: 85, model: initialDraft?.model || defaultModel });
  const [spec, setSpec] = useState<Spec | null>(null);
  const [specErr, setSpecErr] = useState('');
  const [specLoading, setSpecLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageEvent[]>([]);
  const [tokens, setTokens] = useState(0);
  const [history, setHistory] = useState<RoundResult[]>([]);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [reportNote, setReportNote] = useState('');
  const roundItems = useRef(new Map<number, ReviewItem[]>());
  const selectedReportKey = useRef('');
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
  const jobIdRef = useRef('');          // studio_run ack 의 jobId — 연결이 끊겼을 때 기록으로 복구하는 열쇠
  const lastEventAt = useRef(Date.now());
  const runningRef = useRef(false);
  const recoveredRef = useRef(false);   // 기록 복구로 이미 마감했다 — 뒤늦은 타임아웃 오류·중복 onDone 을 막는다
  const modelReady = models.some(model => model.id === form.model);
  useEffect(() => {
    if (defaultModel) setForm(previous => previous.model ? previous : { ...previous, model: defaultModel });
  }, [defaultModel]);

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
    let cancelled = false;
    if (initialDraft) {
      setCanvasUrl(initialDraft.url); setBaseDraftId(initialDraft.draftId); setPickedRound(initialDraft.bestRound);
      setSel(null); setSelectMode(false);
      roundItems.current.clear(); jobIdRef.current = initialDraft.jobId;
      selectedReportKey.current = `${initialDraft.jobId}:${initialDraft.bestRound}`;
      setItems([]); setReportNote('검수표를 불러오는 중입니다.');
      setForm(f => ({ ...f, productCode: initialDraft.productCode, outputType: initialDraft.outputType, model: initialDraft.model || f.model }));
      sock.request('studio_jobs', { jobId: initialDraft.jobId, summaryOnly: true }).then(response => {
        if (cancelled || jobIdRef.current !== initialDraft.jobId) return;
        const rounds = response.job?.rounds || [];
        setHistory(rounds);
        const selected = rounds.find((round: RoundResult) => round.round === initialDraft.bestRound);
        if (selected) void pickRound(selected);
        else setReportNote('이전 시안의 라운드 기록을 찾지 못했습니다.');
      }).catch(() => { if (!cancelled) setReportNote('검수표를 불러오지 못했습니다.'); });
    }
    return () => { cancelled = true; };
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
  const finishFromJob = (j: any) => {
    if (recoveredRef.current) return;
    runningRef.current = false; recoveredRef.current = true;
    const rounds = Array.isArray(j.rounds) ? j.rounds : [];
    const best = rounds.find((r: any) => r.round === j.bestRound) || [...rounds].reverse().find((r: any) => r.url);
    setDone({ type: 'studio.done', jobId: j.jobId, draftId: j.draftId ?? null, score: j.score ?? 0, passed: !!j.passed,
      rounds: rounds.length || Number(j.rounds) || 0, maxRounds: j.maxRounds ?? form.maxRounds, passScore: j.passScore ?? form.passScore,
      stopReason: j.stopReason ?? 'error', bestRound: j.bestRound ?? (best?.round || 0), url: best?.url || '', items: [],
      history: rounds, usage: { inputTokens: 0, outputTokens: 0 }, model: j.model || '', elapsedMs: 0, error: j.error, recovered: true } as DoneEvent);
    if (rounds.length) setHistory(rounds);
    if (best?.url) { setCanvasUrl(best.url); setPickedRound(best.round); }
    setItems([]); setReportNote('복구한 라운드를 선택하면 해당 검수표를 조회합니다.');
    if (j.draftId) setBaseDraftId(j.draftId);
    setTurns(t => [...t, { role: 'agent', text: '연결이 끊겨 기록에서 복구했습니다' }]);
    setRunning(false);
    onDone();
  };

  // 무응답 감시 — .done 프레임을 못 받아도(커넥션 종료·프레임 유실) 잡 기록으로 결과를 복구한다.
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => {
      if (Date.now() - lastEventAt.current <= 240_000) return;
      const jid = jobIdRef.current;
      if (!jid) return;
      sock.request('studio_jobs', { jobId: jid, summaryOnly: true })
        .then(r => { const j = r.job; if (runningRef.current && j && (j.status === 'done' || j.status === 'failed')) finishFromJob(j); })
        .catch(() => {}); // 다음 주기에 재시도
    }, 20_000);
    return () => clearInterval(id);
  }, [running]);

  const onEvent = (e: any) => {
    lastEventAt.current = Date.now();
    if (e.type === 'studio_run') { jobIdRef.current = e.jobId || ''; return; }
    if (e.type === 'studio.stage') {
      setStages(s => [...s, e]);
      if (e.step === 'review') roundItems.current.set(e.round, e.items || []);
      if (e.step === 'publish' && e.url) {
        setCanvasUrl(e.url); setPickedRound(e.round);
        selectedReportKey.current = `${jobIdRef.current}:${e.round}`;
        setItems(roundItems.current.get(e.round) || []); setReportNote('');
        setHistory(h => [...h.filter(x => x.round !== e.round), { round: e.round, score: e.score, passed: false, url: e.url, failures: [], undetermined: [], elapsedMs: 0 }]);
      }
    } else if (e.type === 'studio.token') {
      setTokens(t => t + (e.t?.length || 0));
    } else if (e.type === 'studio.done') {
      setDone(e as DoneEvent);
      setHistory(e.history || []);
      setItems(e.itemsTruncated ? roundItems.current.get(e.bestRound) || [] : e.items || []);
      selectedReportKey.current = `${e.jobId}:${e.bestRound}`; setReportNote('');
      if (e.url) { setCanvasUrl(e.url); setPickedRound(e.bestRound); setBaseDraftId(e.draftId || ''); }
      setTurns(t => [...t, { role: 'agent', text: e.error ? `오류: ${e.error}` : `${e.rounds}라운드 · 선택 시안 ${e.score}점 (라운드 ${e.bestRound}) · ${({ passed: '정적 검수 통과 · 동작 미검증', max_rounds: '라운드 상한 도달', time_cap: '시간 상한(13분)으로 중단', error: '오류' } as any)[e.stopReason]}` }]);
    }
  };

  const run = async (payload: Record<string, any>) => {
    if (!canWrite || !modelReady || runningRef.current) return;
    jobIdRef.current = ''; lastEventAt.current = Date.now(); runningRef.current = true; recoveredRef.current = false;
    roundItems.current.clear(); selectedReportKey.current = ''; setReportNote('');
    setRunning(true); setStages([]); setTokens(0); setDone(null); setItems([]); setMsg(''); setSel(null);
    try { await sock.run('studio_run', payload, onEvent, 300_000); }
    catch (e: any) { if (!recoveredRef.current) setMsg('오류: ' + (e?.message || e)); }
    finally { runningRef.current = false; setRunning(false); if (!recoveredRef.current) onDone(); }
  };
  const generate = () => { setTurns(t => [...t, { role: 'user', text: form.brief }]); setHistory([]); run({ ...form, mode: 'generate' }); };
  const refine = () => {
    if (!canWrite || runningRef.current || !modelReady) return;
    if (!baseDraftId || !refineText.trim()) { setMsg('수정 지시를 입력하세요 (원본 시안이 캔버스에 있어야 합니다)'); return; }
    setTurns(t => [...t, { role: 'user', text: `[수정${sel ? ' · ' + sel.label : ''}] ${refineText}` }]);
    run({ mode: 'refine', baseDraftId, baseRound: pickedRound || 0, model: form.model,
      selector: sel?.selector || '', elementHtml: sel?.html || '', instruction: refineText, productCode: form.productCode, outputType: form.outputType, axis: form.axis, assetIds: form.assetIds, agentId: form.agentId, maxRounds: 1, passScore: form.passScore, brief: '' });
    setRefineText('');
  };
  const toggleAsset = (id: string) => setForm(f => ({ ...f, assetIds: f.assetIds.includes(id) ? f.assetIds.filter(x => x !== id) : [...f.assetIds, id] }));
  const pickRound = async (r: RoundResult) => {
    setSel(null); setSelectMode(false); setPickedRound(r.round); if (r.url) setCanvasUrl(r.url);
    const jobId = jobIdRef.current || initialDraft?.jobId || done?.jobId || '';
    const key = `${jobId}:${r.round}`;
    selectedReportKey.current = key;
    setItems([]); setReportNote('선택한 라운드의 검수표를 불러오는 중입니다.');
    const cached = roundItems.current.get(r.round);
    if (cached) { setItems(cached); setReportNote(''); return; }
    try {
      const report = await loadRoundReport(jobId, r.round);
      if (selectedReportKey.current !== key) return;
      setItems(report.items);
      setReportNote(report.itemsComplete ? '' : '이전 기록에는 미충족·미판정 항목만 저장되어 있습니다. 전체 검수표는 재검수 후 제공됩니다.');
      if (report.itemsComplete) roundItems.current.set(r.round, report.items);
    } catch {
      if (selectedReportKey.current === key) setReportNote('선택한 라운드의 검수표를 불러오지 못했습니다. 다시 선택해 주세요.');
    }
  };
  const product = products.find(p => p.code === form.productCode);

  return (
    <div className="studio-layout">
      {/* 좌: 설정 */}
      <div className="studio-controls">
        <div className="studio-settings panel p-5">
          <div className="text-sm font-bold text-slate-800 mb-2">① 만들 화면 설명</div>
          <div className="flex gap-2 mb-2 flex-wrap">
            {BRIEF_PRESETS.map(p => (
              <button key={p.label} onClick={() => setForm(f => ({ ...f, brief: p.brief, productCode: p.productCode, outputType: p.outputType }))}
                className={`chip text-xs ${form.brief === p.brief ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500 hover:border-teal-300'}`}>{p.label}</button>
            ))}
          </div>
          <textarea className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-20" value={form.brief} onChange={e => setForm({ ...form, brief: e.target.value })} />

          <div className="text-sm font-bold text-slate-800 mt-4 mb-2">② 상품과 업무 기준</div>
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
              <div className="text-sm font-bold text-slate-800 mb-1">디자인 방향</div>
              <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.axis} onChange={e => setForm({ ...form, axis: e.target.value })}>
                {AXES.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>
          <ModelSelect id="studio-model" value={form.model} models={models}
            onChange={model => setForm(previous => ({ ...previous, model }))} disabled={running} />

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

          <details className="mt-4">
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">고급 설정 · 반복 횟수와 정적 검수 점수</summary>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <label className="text-sm"><div className="font-bold text-slate-800 mb-1">⑤ 라운드 상한 <b className="text-[#008485]">{form.maxRounds}</b></div>
              <input type="range" min={1} max={20} value={form.maxRounds} onChange={e => setForm({ ...form, maxRounds: +e.target.value })} className="w-full" />
              <div className="text-[11px] text-slate-400">기본 3 · 최대 20 · 워커 13분 상한</div></label>
            <label className="text-sm"><div className="font-bold text-slate-800 mb-1">통과 점수 <b className="text-[#008485]">{form.passScore}</b></div>
              <input type="range" min={50} max={100} value={form.passScore} onChange={e => setForm({ ...form, passScore: +e.target.value })} className="w-full" />
              <div className="text-[11px] text-slate-400">필수 항목 통과 + 미판정 없음 + 기준 점수 이상</div></label>
          </div>
          </details>

          <button onClick={generate} disabled={!canWrite || running || !form.productCode || !modelReady || specLoading || !!specErr}
            className="w-full mt-4 py-2.5 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-bold text-sm disabled:opacity-40">
            {running ? `시안 생성·검수 중… ${elapsed}s` : '시안 만들고 정적 검수하기'}
          </button>
          {!canWrite && <div className="text-xs text-slate-400 mt-2">이 계정은 조회 전용입니다</div>}
          {msg && <div className="text-[#E90061] text-xs mt-2">{msg}</div>}
        </div>

        <div className="studio-reference">
        <div className="panel p-4"><div className="text-sm font-bold text-slate-800 mb-2">검수 체크리스트 미리보기</div>
          <SpecPanel spec={spec} loading={specLoading} error={specErr} /></div>

        <div className="panel p-4">
          <div className="text-sm font-bold text-slate-800 mb-2">진행</div>
          {stages.length ? <StageList stages={stages} running={running} /> : <div className="text-xs text-slate-400">아직 실행 전</div>}
          {done && <div className="mt-3 flex items-center gap-2 text-xs"><ScoreBadge score={done.score} passed={done.passed} size="lg" />
            <div className="text-slate-600">{done.rounds}/{done.maxRounds} 라운드 · 라운드 {done.bestRound} 선택 · {done.stopReason === 'time_cap' ? '13분 상한으로 중단' : done.stopReason === 'max_rounds' ? '상한 도달' : done.stopReason === 'passed' ? '정적 검수 통과 · 동작 미검증' : '오류'}<br />
              토큰 in {done.usage?.inputTokens} / out {done.usage?.outputTokens} · {done.model} · {Math.round(done.elapsedMs / 1000)}s</div></div>}
        </div>
        </div>
      </div>

      <div className="studio-canvas panel p-3">
          <div className="studio-preview-toolbar flex items-center gap-2 mb-2 text-xs">
            <RoundStrip history={history} best={done?.bestRound || 0} onPick={pickRound} picked={pickedRound} disabled={running} />
            <span className="ml-auto" />
            <button onClick={() => setMobile(m => !m)} className="chip">{mobile ? '📱 모바일' : '🖥 전체 폭'}</button>
            <button onClick={() => setSelectMode(s => !s)} className={`chip ${selectMode ? 'text-teal-700 border-teal-400 bg-teal-50' : ''}`} disabled={!canvasUrl}>{selectMode ? '요소 선택 중' : '요소 선택'}</button>
            {canvasUrl && <a href={canvasUrl} target="_blank" rel="noopener noreferrer" className="chip text-teal-700">새 탭 ↗</a>}
          </div>
          <div className={`studio-preview-frame mx-auto bg-slate-100 rounded-xl overflow-hidden border border-slate-200 ${mobile ? 'is-mobile' : ''}`}>
            {canvasUrl
              // sandbox: allow-same-origin 만 — 시안 안의 스크립트는 실행하지 않고(allow-scripts 없음),
              // 같은 출처는 유지되므로 부모에서 contentDocument 로 요소 선택 배선(wireSelection)은 계속 동작한다.
              ? <iframe ref={iframeRef} key={canvasUrl} src={canvasUrl} title="canvas" sandbox="allow-same-origin" className="w-full h-full bg-white" onLoad={wireSelection} />
              : <div className="h-full flex items-center justify-center text-sm text-slate-400">생성하면 라운드마다 시안이 여기 갱신됩니다</div>}
          </div>
          <div className="mt-2 text-[11px] text-slate-500">{sel ? `선택: ${sel.label}` : '선택된 요소 없음 — 전체 수정으로 적용됩니다'}</div>
          <div className="studio-refine-controls flex gap-2 mt-2">
            <input className="flex-1 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" placeholder="수정 지시 (예: 우대금리 문구를 더 크게)" value={refineText} onChange={e => setRefineText(e.target.value)} onKeyDown={e => e.key === 'Enter' && refine()} />
            <button onClick={refine} disabled={!canWrite || running || !baseDraftId || !modelReady} className="px-4 py-2 rounded-xl bg-white border border-teal-400 text-teal-700 text-sm font-semibold disabled:opacity-40">보고 있는 시안 수정·검수</button>
          </div>
          {turns.length > 0 && <div className="mt-2 max-h-28 overflow-auto text-xs space-y-1">{turns.map((t, i) => <div key={i} className={t.role === 'user' ? 'text-slate-700' : 'text-teal-700'}>{t.role === 'user' ? '👤 ' : '🤖 '}{t.text}</div>)}</div>}
        </div>
        <div className="studio-review panel p-4">
          <div className="studio-review-heading flex items-center gap-2 mb-2"><div className="text-sm font-bold text-slate-800">검수 결과</div>
            <span className="chip text-[10px] text-slate-500">구조·문구·흐름 검수 — 픽셀 비교 미구현</span></div>
          {reportNote && <p role="status" className="text-xs text-amber-800 mb-2">{reportNote}</p>}
          {items.length ? <Checklist items={items} onSource={openExplorer} />
            : done?.itemsTruncated ? <span className="chip text-[10px] text-amber-700 border-amber-300 bg-amber-50">항목 목록이 프레임 한도로 생략됨 — 갤러리의 검수 리포트에서 확인</span>
              : <div className="text-xs text-slate-400">라운드가 끝나면 항목별 판정이 표시됩니다</div>}
        </div>
    </div>
  );
}
