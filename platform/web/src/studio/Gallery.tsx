// platform/web/src/studio/Gallery.tsx
import { useState } from 'react';
import { sock } from '../lib';
import { openExplorer } from './nav';
import { Checklist, ScoreBadge } from './RoundTimeline';
import { Draft, ReviewItem } from './types';

const STOP: Record<string, string> = { passed: '통과', max_rounds: '상한 도달', time_cap: '13분 상한', error: '오류' };

export default function Gallery({ drafts, canWrite, reload, onEdit }: { drafts: Draft[]; canWrite: boolean; reload: () => void; onEdit: (d: Draft) => void }) {
  const [filter, setFilter] = useState<'전체' | '검토중' | '승인됨' | '반려'>('전체');
  const [busy, setBusy] = useState('');
  const [comment, setComment] = useState<Record<string, string>>({});
  const [report, setReport] = useState<{ d: Draft; rounds: any[]; items: ReviewItem[] } | null>(null);
  const list = drafts.filter(d => filter === '전체' || d.status === filter);
  const decide = async (d: Draft, decision: 'approve' | 'reject') => {
    setBusy(d.draftId);
    await sock.request('studio_feedback', { draftId: d.draftId, decision, comment: comment[d.draftId] || '' }).catch(() => {});
    setBusy(''); reload();
  };
  const openReport = async (d: Draft) => {
    const j = await sock.request('studio_jobs', { jobId: d.jobId }).catch(() => ({} as any));
    const rounds = (j.job?.rounds || []) as any[];
    const best = rounds.find(r => r.round === d.bestRound && Array.isArray(r.failures));
    setReport({ d, rounds, items: (best?.failures || []) as ReviewItem[] });
  };
  const color = (s: string) => s === '승인됨' ? 'text-emerald-600 border-emerald-300 bg-emerald-50' : s === '반려' ? 'text-[#E90061] border-rose-300 bg-rose-50' : 'text-amber-700 border-amber-300 bg-amber-50';
  return (
    <div>
      <div className="flex gap-2 mb-3 items-center">
        {(['전체', '검토중', '승인됨', '반려'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)} className={`chip ${filter === f ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{f}</button>
        ))}
        <span className="text-xs text-slate-400 ml-2">승인된 시안(최근 2건)은 다음 생성의 few-shot 레퍼런스로 주입됩니다 · 점수 = 상품 명세 체크리스트 가중 통과율</span>
      </div>
      <div className="grid grid-cols-3 gap-5">
        {list.map(d => (
          <div key={d.draftId} className="panel overflow-hidden group hover:shadow-lg transition-shadow">
            <div className="relative h-72 overflow-hidden bg-slate-50 border-b border-slate-100">
              <iframe src={d.url} title={d.title} sandbox="allow-same-origin" className="pointer-events-none origin-top-left" style={{ width: '200%', height: '200%', transform: 'scale(0.5)' }} />
              <a href={d.url} target="_blank" rel="noopener" className="absolute inset-0 flex items-end justify-end p-2 opacity-0 group-hover:opacity-100 transition-opacity" style={{ background: 'linear-gradient(transparent 65%, rgba(11,47,43,.45))' }}>
                <span className="text-white text-xs bg-[#008485] px-3 py-1.5 rounded-lg">원본 크게 보기 ↗</span></a>
              <div className="absolute top-2 left-2"><ScoreBadge score={d.score} passed={d.passed} /></div>
            </div>
            <div className="p-4">
              <div className="text-sm font-bold text-slate-800 truncate" title={d.title}>{d.title}</div>
              <div className="text-[11px] text-slate-500 mt-0.5">{d.productName} · {d.outputType} · {d.rounds}라운드 (최고 R{d.bestRound}) · {STOP[d.stopReason || ''] || ''}</div>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <span className={`chip text-[11px] ${color(d.status)}`}>{d.status}</span>
                <span className="chip text-[11px] text-slate-500">{d.axis}</span>
                {d.parentId && <span className="chip text-[11px] text-slate-500">수정본</span>}
                {d.status === '승인됨' && <span className="chip text-[11px] text-teal-700 border-teal-300">few-shot 반영</span>}
                <button onClick={() => openReport(d)} className="chip text-[11px] text-slate-600 ml-auto">검수 리포트</button>
                <button onClick={() => onEdit(d)} className="chip text-[11px] text-teal-700 border-teal-300">편집 →</button>
              </div>
              {d.comment && <div className="text-[11px] text-slate-500 mt-1">코멘트: {d.comment}</div>}
              {canWrite && d.status === '검토중' && (
                <div className="mt-3 space-y-2">
                  <input className="w-full px-2 py-1 rounded-lg border border-slate-200 text-xs" placeholder="반려·승인 코멘트 (선택)" value={comment[d.draftId] || ''} onChange={e => setComment({ ...comment, [d.draftId]: e.target.value })} />
                  <div className="flex gap-2">
                    <button disabled={busy === d.draftId} onClick={() => decide(d, 'approve')} className="flex-1 py-1.5 rounded-lg text-xs font-semibold bg-[#008485] text-white hover:bg-[#0a6b6c]">✓ 승인</button>
                    <button disabled={busy === d.draftId} onClick={() => decide(d, 'reject')} className="flex-1 py-1.5 rounded-lg text-xs font-semibold border border-rose-300 text-[#E90061] hover:bg-rose-50">반려</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        {list.length === 0 && <div className="text-slate-400 text-sm col-span-3">시안이 없습니다 — 플레이그라운드에서 루프를 실행해 보세요.</div>}
      </div>
      {report && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setReport(null)}>
          <div className="bg-white rounded-2xl p-5 w-[720px] max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-3"><div className="text-sm font-bold">{report.d.title} — 검수 리포트</div><ScoreBadge score={report.d.score} passed={report.d.passed} />
              <button className="chip ml-auto" onClick={() => setReport(null)}>닫기</button></div>
            <div className="text-xs text-slate-500 mb-3">라운드별 점수: {report.rounds.filter(r => r.score !== undefined).map(r => `R${r.round}=${r.score}`).join(' · ') || '기록 없음'}</div>
            <div className="text-xs font-bold text-slate-700 mb-1">최고 점수 라운드의 미충족·미판정 항목</div>
            {report.items.length ? <Checklist items={report.items} onSource={openExplorer} /> : <div className="text-xs text-emerald-700">미충족 항목 없음</div>}
          </div>
        </div>
      )}
    </div>
  );
}
