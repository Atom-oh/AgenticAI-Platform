// 프로세스 생성 (검수 루프) — 상품명세서 → PRD → 가입 프로세스(스텝별 화면) 생성 → 리뷰·테스트 → 리포트.
// 백엔드: handlers/design.py (design_catalog·design_preview·design_flow·design_runs·design_run·design_review).
import { useEffect, useMemo, useState } from 'react';
import { auth, sock } from '../lib';

type SpecSummary = {
  id: string; productName: string; productType: string; category: string; shape: string; baseRate: number;
  conditions: number; inputConditions: number; partners: string[]; notices: number; record?: string;
};
type ChecklistSummary = { id: string; title: string; appliesTo: Record<string, string>; items: number };
type PrdStep = { id: string; title: string; required?: string[]; branch?: { when: string; after: string } };
type Prd = { title: string; steps: PrdStep[]; transitions: { from: string; to: string; trigger: string; when?: string }[]; branchSteps?: string[] };
type ChecklistItem = { id: string; text: string; method: string; target: string; source: string; set: string };
type Stage = { step: string; status?: string; attempt?: number; [k: string]: any };
type ReportItem = { id: string; text: string; source: string; method: string; verdict: string; evidence: string; attempt: number; severity?: string };
type Report = { items: ReportItem[]; score: { pass: number; fail: number; incomplete: number; total: number }; openItems: string[]; history?: any[] };
type StepOut = { id: string; title: string; url: string };
type Run = { runId: string; productName?: string; shape?: string; status?: string; ok?: boolean; attempts?: number; regenerated?: boolean;
  score?: Report['score']; steps?: StepOut[]; branchSteps?: string[]; createdAt?: number };

const OUTPUT_TYPES = [['design', '디자인'], ['mockup', '목업'], ['wireframe', '와이어프레임'], ['uxflow', 'UX 플로우']] as const;
const VERDICT = {
  pass: { t: '통과', c: 'text-emerald-600 border-emerald-300 bg-emerald-50' },
  fail: { t: '실패', c: 'text-[#E90061] border-rose-300 bg-rose-50' },
  incomplete: { t: '미판정', c: 'text-slate-500 border-slate-300 bg-slate-50' },
} as const;
const STEP_LABEL: Record<string, string> = {
  gate: '경계 게이트', prd: 'PRD 도출', checklist: '체크리스트 확정', generate: '화면 생성',
  review: '검수(리뷰)', test: '테스트 게이트', regenerate: '재생성', report: '리포트',
};

export default function ProcessStudio() {
  const canWrite = !!auth.studioToken;
  const [specs, setSpecs] = useState<SpecSummary[]>([]);
  const [checklists, setChecklists] = useState<ChecklistSummary[]>([]);
  const [source, setSource] = useState('');
  const [runtimeBadge, setRuntimeBadge] = useState('');
  const [testBadge, setTestBadge] = useState('');
  const [specId, setSpecId] = useState('');
  const [outputType, setOutputType] = useState('design');
  const [prd, setPrd] = useState<Prd | null>(null);
  const [preview, setPreview] = useState<{ items: ChecklistItem[]; counts: any } | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{ runId: string; report: Report; steps: StepOut[]; prd: Prd; ok: boolean; attempts: number; usage?: any; modelId?: string; runtime?: string } | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [err, setErr] = useState('');
  const [viewStep, setViewStep] = useState<StepOut | null>(null);

  const spec = useMemo(() => specs.find(s => s.id === specId), [specs, specId]);
  const loadRuns = () => sock.request('design_runs').then(e => setRuns(e.runs || [])).catch(() => {});

  useEffect(() => {
    sock.request('design_catalog').then(e => {
      setSpecs(e.productSpecs || []); setChecklists(e.checklists || []); setSource(e.source || '');
      setRuntimeBadge(e.runtimeBadge || ''); setTestBadge(e.testBadge || '');
      if ((e.productSpecs || []).length) setSpecId(e.productSpecs.find((s: SpecSummary) => s.shape === '반정형')?.id || e.productSpecs[0].id);
    }).catch(err2 => setErr(String(err2?.message || err2)));
    loadRuns();
  }, []);

  useEffect(() => {
    if (!specId) return;
    setPrd(null); setPreview(null);
    sock.request('design_preview', { productSpecId: specId }).then(e => {
      if (e.error) { setErr(e.error); return; }
      setErr(''); setPrd(e.prd); setPreview({ items: e.checklist || [], counts: e.counts || {} });
    }).catch(() => {});
  }, [specId]);

  const run = async () => {
    if (!canWrite || running || !specId) return;
    setRunning(true); setErr(''); setStages([]); setResult(null); setViewStep(null);
    try {
      await sock.run('design_flow', { studioToken: auth.studioToken, productSpecId: specId, outputType }, (e: any) => {
        if (e.type === 'design.stage') {
          setStages(prev => [...prev, { step: e.step, ...e }]);
        } else if (e.type === 'design.done') {
          if (e.error) { setErr(e.error + (e.missing ? ` (필드: ${e.missing.join(', ')})` : '')); }
          else {
            setResult({ runId: e.runId, report: e.report, steps: e.steps || [], prd: e.prd, ok: e.ok, attempts: e.attempts, usage: e.usage, modelId: e.modelId, runtime: e.runtime });
            setViewStep((e.steps || [])[0] || null);
          }
        }
      });
    } catch (e2: any) { setErr(String(e2?.message || e2)); }
    setRunning(false); loadRuns();
  };

  const decide = async (runId: string, decision: 'approve' | 'reject') => {
    await sock.request('design_review', { studioToken: auth.studioToken, runId, decision }).catch(() => {});
    loadRuns();
  };

  return (
    <div>
      <div className="panel p-5 mb-4" style={{ background: 'linear-gradient(105deg,#eaf5f4 0%,#ffffff 55%,#faf7ef 100%)' }}>
        <div className="text-lg font-bold text-[#0b4f4b]">프로세스 생성 — 상품명세서 검수 루프</div>
        <div className="text-sm text-slate-600 mt-1">
          상품명세서에서 <b className="text-[#008485]">PRD</b>를 도출하고 가입 프로세스 화면을 생성한 뒤,
          <b className="text-amber-700"> 리뷰·테스트 에이전트</b>가 체크리스트(기본 + 명세서 파생)로 검수합니다.
          실패 시 <b>최대 1회</b> 재생성합니다.
        </div>
        <div className="flex flex-wrap gap-2 mt-2 text-[11px]">
          {source && <span className="chip text-slate-500">자산 출처: {source === 'registry' ? 'Registry(APPROVED)' : '시드 폴백'}</span>}
          {runtimeBadge && <span className="chip text-teal-700 border-teal-300">{runtimeBadge}</span>}
          {testBadge && <span className="chip text-amber-700 border-amber-300">{testBadge}</span>}
        </div>
      </div>

      <div className="grid grid-cols-[1fr_1.2fr] gap-5">
        {/* 좌: 입력 + PRD 미리보기 */}
        <div className="panel p-5">
          <div className="text-sm font-bold text-slate-800 mb-2">① 상품명세서</div>
          <div className="flex flex-col gap-1 mb-3">
            {specs.map(s => (
              <button key={s.id} onClick={() => setSpecId(s.id)}
                className={`text-left px-3 py-2 rounded-xl border text-sm ${specId === s.id ? 'border-teal-400 bg-teal-50' : 'border-slate-200 hover:border-teal-300'}`}>
                <div className="flex items-center gap-2">
                  <b className="text-slate-800">{s.productName}</b>
                  <span className="chip text-[10px] text-slate-500">{s.shape}</span>
                  <span className="text-[11px] text-slate-400 ml-auto">기본 {s.baseRate}%</span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1">
                  우대 {s.conditions}건{s.inputConditions > 0 && <span className="text-amber-700"> · 증빙 입력 {s.inputConditions}</span>}
                  {s.partners.length > 0 && <span className="text-teal-700"> · 제휴 {s.partners.join(', ')}</span>}
                </div>
              </button>
            ))}
          </div>

          <div className="text-sm font-bold text-slate-800 mb-2">② 출력 유형</div>
          <div className="flex gap-2 mb-3">
            {OUTPUT_TYPES.map(([id, label]) => (
              <button key={id} onClick={() => setOutputType(id)}
                className={`chip text-xs ${outputType === id ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500 hover:border-teal-300'}`}>{label}</button>
            ))}
          </div>

          {prd && (
            <>
              <div className="text-sm font-bold text-slate-800 mb-2">③ PRD 미리보기 <span className="text-[11px] font-normal text-slate-400">— 명세서에서 도출된 스텝·분기</span></div>
              <div className="flex flex-wrap items-center gap-1 mb-2">
                {prd.steps.map((st, i) => (
                  <span key={st.id} className="flex items-center gap-1">
                    <span className={`chip text-[11px] ${st.branch ? 'text-amber-700 border-amber-300 bg-amber-50' : 'text-slate-600 border-slate-200'}`}
                      title={st.branch ? `분기: ${st.branch.when}` : (st.required || []).join(' · ')}>
                      {st.title}{st.branch && ' ⎘'}
                    </span>
                    {i < prd.steps.length - 1 && <span className="text-slate-300">→</span>}
                  </span>
                ))}
              </div>
              {(prd.branchSteps?.length ?? 0) > 0 &&
                <div className="text-[11px] text-amber-700 mb-2">⎘ 분기 스텝: 상품 특성(증빙 입력형 우대조건)에서 자동 삽입됨</div>}
            </>
          )}
          {preview && (
            <div className="text-[12px] text-slate-600 mb-3">
              체크리스트 {preview.counts.items}항목 —
              <span className="text-slate-500"> 기본 {preview.counts.base}</span> ·
              <span className="text-amber-700"> 명세서 파생 {preview.counts.derived}</span> ·
              <span className="text-slate-400"> LLM 판정 {preview.counts.llm}</span>
            </div>
          )}

          <button onClick={run} disabled={!canWrite || running || !specId}
            className="w-full py-2.5 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-bold text-sm disabled:opacity-40">
            {running ? '실행 중… (생성 → 검수 → 재생성)' : '▶ 프로세스 생성 + 검수 실행'}
          </button>
          {!canWrite && <div className="text-xs text-slate-400 mt-2">이 계정은 조회 전용입니다</div>}
          {err && <div className="text-[#E90061] text-xs mt-2">{err}</div>}
        </div>

        {/* 우: 루프 타임라인 + 결과 */}
        <div className="panel p-5">
          <div className="text-sm font-bold text-slate-800 mb-2">루프 진행</div>
          {stages.length === 0 && !result && <div className="text-slate-400 text-sm">실행하면 단계별 진행이 여기에 표시됩니다.</div>}
          <ol className="space-y-1 mb-3">
            {stages.filter(s => s.status !== 'start' || s.step === 'generate').map((s, i) => (
              <li key={i} className="text-[13px] flex items-center gap-2">
                <span className={`chip text-[10px] ${s.step === 'regenerate' ? 'text-amber-700 border-amber-300' : s.step === 'report' ? 'text-teal-700 border-teal-300' : 'text-slate-500'}`}>
                  {STEP_LABEL[s.step] || s.step}{s.attempt ? ` ·${s.attempt}차` : ''}
                </span>
                <span className="text-slate-500 text-[12px]">{stageDetail(s)}</span>
              </li>
            ))}
          </ol>

          {result && (
            <ReviewReport result={result} viewStep={viewStep} setViewStep={setViewStep}
              canWrite={canWrite} onDecide={decide} />
          )}
        </div>
      </div>

      {viewStep && result && (
        <div className="panel p-3 mt-4">
          <div className="flex items-center gap-2 mb-2">
            <div className="text-sm font-bold text-slate-800">{viewStep.title}</div>
            {result.steps.map(st => (
              <button key={st.id} onClick={() => setViewStep(st)}
                className={`chip text-[11px] ${viewStep.id === st.id ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{st.title}</button>
            ))}
            <a href={viewStep.url} target="_blank" rel="noopener" className="text-[11px] text-[#008485] ml-auto">원본 크게 보기 ↗</a>
          </div>
          <iframe key={viewStep.url} src={viewStep.url} title={viewStep.title} sandbox="allow-same-origin"
            className="w-full rounded-lg border border-slate-200" style={{ height: 520, background: '#fff' }} />
        </div>
      )}

      {/* 과거 실행 */}
      <div className="panel p-5 mt-4">
        <div className="text-sm font-bold text-slate-800 mb-2">최근 실행</div>
        {runs.length === 0 && <div className="text-slate-400 text-sm">아직 실행 기록이 없습니다.</div>}
        <div className="grid grid-cols-3 gap-3">
          {runs.map(r => (
            <div key={r.runId} className="panel p-3">
              <div className="flex items-center gap-2">
                <b className="text-sm text-slate-800 truncate">{r.productName}</b>
                <span className="chip text-[10px] text-slate-500 ml-auto">{r.shape}</span>
              </div>
              <div className="flex items-center gap-1 mt-1 text-[11px]">
                <span className={`chip ${r.ok ? 'text-emerald-600 border-emerald-300' : 'text-[#E90061] border-rose-300'}`}>{r.ok ? '검수 통과' : '잔여 있음'}</span>
                {r.regenerated && <span className="chip text-amber-700 border-amber-300">재생성 1회</span>}
                {r.score && <span className="text-slate-500">✓{r.score.pass} ✗{r.score.fail} ?{r.score.incomplete}</span>}
              </div>
              <div className="flex items-center gap-1 mt-1 text-[11px]">
                <span className={`chip ${r.status === '승인됨' ? 'text-emerald-600 border-emerald-300' : r.status === '반려' ? 'text-[#E90061] border-rose-300' : 'text-amber-700 border-amber-300'}`}>{r.status || '검토중'}</span>
                {canWrite && r.status !== '승인됨' && (
                  <span className="ml-auto flex gap-1">
                    <button onClick={() => decide(r.runId, 'approve')} className="chip text-[11px] text-[#008485] border-teal-300 hover:bg-teal-50">승인</button>
                    <button onClick={() => decide(r.runId, 'reject')} className="chip text-[11px] text-[#E90061] border-rose-300 hover:bg-rose-50">반려</button>
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function stageDetail(s: Stage): string {
  if (s.step === 'prd' && s.steps) return `스텝 ${s.steps.length}개 · 분기 ${(s.branchSteps || []).length}`;
  if (s.step === 'checklist') return `${s.total}항목 (기본 ${s.base} · 파생 ${s.derived})`;
  if (s.step === 'generate' && s.status === 'done') return `화면 ${(s.steps || []).length}장 생성`;
  if (s.step === 'generate' && s.status === 'start') return '생성 중…';
  if (s.step === 'review' && s.status === 'done') return `통과 ${s.pass} · 실패 ${s.fail} · 미판정 ${s.incomplete}`;
  if (s.step === 'regenerate') return `실패 ${(s.reasons || []).length}건 반영해 재생성`;
  if (s.step === 'report') return s.ok ? '검수 통과' : `잔여 ${(s.open || []).length}건`;
  if (s.step === 'gate') return '경계 계측 · PII 스캔 통과';
  return s.status || '';
}

function ReviewReport({ result, viewStep, setViewStep, canWrite, onDecide }: {
  result: { runId: string; report: Report; steps: StepOut[]; attempts: number; ok: boolean; usage?: any; modelId?: string; runtime?: string };
  viewStep: StepOut | null; setViewStep: (s: StepOut) => void; canWrite: boolean; onDecide: (id: string, d: 'approve' | 'reject') => void;
}) {
  const { report } = result;
  const [tab, setTab] = useState<'all' | 'fail' | 'derived'>('fail');
  const items = report.items.filter(i =>
    tab === 'all' ? true : tab === 'fail' ? i.verdict === 'fail' : i.source === 'derived');
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className={`chip text-xs ${result.ok ? 'text-emerald-600 border-emerald-300 bg-emerald-50' : 'text-[#E90061] border-rose-300 bg-rose-50'}`}>
          {result.ok ? '검수 통과' : `잔여 ${report.openItems.length}건`}
        </span>
        <span className="chip text-xs text-slate-500">시도 {result.attempts}회{result.attempts > 1 ? ' (재생성)' : ''}</span>
        <span className="text-[12px] text-slate-500">✓{report.score.pass} ✗{report.score.fail} ?{report.score.incomplete} / {report.score.total}</span>
      </div>
      <div className="flex gap-2 mb-2">
        {([['fail', '실패만'], ['derived', '명세서 파생'], ['all', '전체']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`chip text-[11px] ${tab === id ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{label}</button>
        ))}
      </div>
      <div className="max-h-72 overflow-auto space-y-1">
        {items.length === 0 && <div className="text-slate-400 text-sm">해당 항목이 없습니다.</div>}
        {items.map(it => {
          const v = VERDICT[it.verdict as keyof typeof VERDICT] || VERDICT.incomplete;
          return (
            <div key={it.id} className="border border-slate-100 rounded-lg p-2">
              <div className="flex items-start gap-2">
                <span className={`chip text-[10px] shrink-0 ${v.c}`}>{v.t}</span>
                <div>
                  <div className="text-[12px] text-slate-700">{it.text}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {it.source === 'derived' ? '명세서 파생' : it.source === 'gate' ? '테스트 게이트' : '기본'} · {it.method}
                    {it.evidence && <span className="text-slate-500"> — {it.evidence}</span>}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {canWrite && (
        <div className="flex gap-2 mt-3">
          <button onClick={() => onDecide(result.runId, 'approve')} className="flex-1 py-1.5 rounded-lg text-xs font-semibold bg-[#008485] text-white hover:bg-[#0a6b6c]">✓ 승인 (마스터)</button>
          <button onClick={() => onDecide(result.runId, 'reject')} className="flex-1 py-1.5 rounded-lg text-xs font-semibold border border-rose-300 text-[#E90061] hover:bg-rose-50">반려</button>
        </div>
      )}
      <div className="text-[10px] text-slate-400 mt-2">
        {result.runtime} · {result.modelId?.split('.').pop()}
        {result.usage?.inputTokens != null && <span> · 토큰 in {result.usage.inputTokens}/out {result.usage.outputTokens}</span>}
        <span> · 승인 시 운영 GitLab 푸시(데모: Registry 상태 반영)</span>
      </div>
    </div>
  );
}
