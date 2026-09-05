// platform/web/src/studio/RoundTimeline.tsx
import { ReviewItem, RoundResult, StageEvent, STEP_LABEL } from './types';

export function ScoreBadge({ score, passed, size = 'md' }: { score: number; passed?: boolean; size?: 'sm' | 'md' | 'lg' }) {
  const cls = passed ? 'bg-emerald-50 text-emerald-700 border-emerald-300' : score >= 70 ? 'bg-amber-50 text-amber-700 border-amber-300' : 'bg-rose-50 text-[#E90061] border-rose-300';
  const sz = size === 'lg' ? 'text-2xl px-4 py-1.5' : size === 'sm' ? 'text-[11px] px-2 py-0.5' : 'text-sm px-3 py-1';
  return <span className={`inline-flex items-center gap-1 rounded-full border font-bold ${cls} ${sz}`}>{score}<span className="font-normal opacity-70">/100</span></span>;
}

export function StageList({ stages, running }: { stages: StageEvent[]; running: boolean }) {
  return (
    <ol className="space-y-1.5 text-xs">
      {stages.map((s, i) => (
        <li key={i} className="flex items-start gap-2">
          <span className={`mt-0.5 w-2 h-2 rounded-full ${i === stages.length - 1 && running ? 'bg-amber-400 blink' : 'bg-[#008485]'}`} />
          <div className="flex-1">
            <div className="text-slate-700 font-semibold">{STEP_LABEL[s.step] || s.step}{s.round ? ` · 라운드 ${s.round}` : ''}</div>
            {s.step === 'review' && <div className="text-slate-500">점수 {s.score} · 필수 미충족 {(s.requiredFailed || []).length} · 미판정 {(s.undetermined || []).length}{s.reviewerError ? ` · 리뷰어 오류: ${s.reviewerError}` : ''}</div>}
            {s.step === 'spec_build' && <div className="text-slate-500">항목 {s.items}개 (필수 {s.required}) · {s.hasPreferential ? '우대 조건 있음 → 조건 스텝 필수' : '우대 조건 없음'}</div>}
            {s.step === 'regenerate' && <div className="text-slate-500">실패 {(s.failures || []).length}건 주입</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function RoundStrip({ history, best, onPick, picked }: { history: RoundResult[]; best: number; onPick: (r: RoundResult) => void; picked?: number }) {
  if (!history.length) return null;
  return (
    <div className="flex gap-2 flex-wrap">
      {history.map(h => (
        <button key={h.round} onClick={() => onPick(h)}
          className={`chip text-xs flex items-center gap-1.5 ${picked === h.round ? 'border-teal-500 bg-teal-50' : ''}`}>
          R{h.round} <ScoreBadge score={h.score} passed={h.passed} size="sm" />
          {h.round === best && <span className="text-[10px] text-teal-700 font-bold">최고</span>}
        </button>
      ))}
    </div>
  );
}

export function Checklist({ items, onSource }: { items: ReviewItem[]; onSource?: (nodeId: string) => void }) {
  const icon = (v: ReviewItem['verdict']) => v === 'pass' ? <span className="text-emerald-600 font-bold">✓</span> : v === 'fail' ? <span className="text-[#E90061] font-bold">✗</span> : <span className="text-slate-400 font-bold">—</span>;
  const groups: Record<string, ReviewItem[]> = {};
  for (const it of items) (groups[it.category] = groups[it.category] || []).push(it);
  return (
    <div className="space-y-3 text-xs">
      {Object.entries(groups).map(([cat, list]) => (
        <div key={cat}>
          <div className="text-[11px] font-bold text-slate-500 mb-1">{cat} · {list.filter(i => i.verdict === 'pass').length}/{list.length}</div>
          {list.map(it => (
            <div key={it.id} className="flex items-start gap-2 py-1 border-b border-slate-100">
              <span className="w-4">{icon(it.verdict)}</span>
              <div className="flex-1">
                <div className={it.required ? 'text-slate-800 font-semibold' : 'text-slate-700'}>{it.text}
                  {it.required && <span className="ml-1 text-[10px] text-rose-500">필수</span>}
                  <span className="ml-1 text-[10px] text-slate-400">w{it.weight} · {it.check}</span></div>
                {it.evidence && <div className="text-slate-500">근거: {it.evidence}</div>}
                {it.verdict !== 'pass' && it.fix && <div className="text-amber-700">수정: {it.fix}</div>}
                {it.verdict === null && <div className="text-slate-400">미판정 — 통과로 세지 않음</div>}
              </div>
              {it.source && <button onClick={() => onSource?.(it.source!.nodeId)} className="chip text-[10px] text-slate-500" title="온톨로지 출처 노드">{it.source.label}</button>}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
