// S2 마이데이터 상담 — 단계별 펼침 패널. 숫자는 계산엔진만 만든다.
import { useState } from 'react';
import { sock } from './lib';

export const S2_PRESET = '제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?';
export const S5_PRESET = '어떤 상품이 제일 돈 많이 벌어요?';

type Stage = { step: string; [k: string]: any };

const STEP_LABEL: Record<string, string> = {
  guardrail_in: '① 입력 가드레일 (Bedrock Guardrails 실물)',
  semantic: '② Semantic Layer 지표 해석',
  lookup: '③ 정확 조회 — 조회된 원본 값 (온프렘)',
  calc: '④ 결정론적 계산엔진 — 계산 내역',
  mask: '⑤ 마스킹/토큰화 게이트 → 경계 통과 페이로드',
};

function StagePanel({ s }: { s: Stage }) {
  const [open, setOpen] = useState(s.step === 'calc' || s.step === 'lookup');
  const onprem = ['semantic', 'lookup', 'calc', 'mask'].includes(s.step);
  return (
    <div className="panel p-3 mb-2" style={{ borderLeft: `3px solid ${onprem ? 'var(--onprem)' : 'var(--cloud)'}` }}>
      <button className="w-full text-left text-sm font-semibold flex items-center gap-2"
        onClick={() => setOpen(o => !o)}>
        <span className="text-slate-500">{open ? '▾' : '▸'}</span>
        {STEP_LABEL[s.step] || s.step}
        <span className="chip text-[10px] ml-auto" style={{ borderColor: onprem ? 'var(--onprem)' : 'var(--cloud)' }}>
          {onprem ? '온프렘 플레인' : '클라우드'}
        </span>
      </button>
      {open && <div className="mt-2 text-xs text-slate-300">
        {s.step === 'guardrail_in' && (
          <div>판정: <b className={s.result.action === 'NONE' ? 'text-emerald-400' : 'text-rose-400'}>{s.result.action}</b>
            {s.result.topics?.length > 0 && <> · 토픽: {s.result.topics.join(', ')}</>}</div>
        )}
        {s.step === 'semantic' && (
          <div>지표 <b className="text-sky-300">{s.metric.name}</b> ({s.metric.unit}, 소관 {s.metric.ownerDept})
            <pre className="mt-1 bg-slate-950 rounded p-2 overflow-x-auto">{s.metric.sql}</pre></div>
        )}
        {s.step === 'lookup' && (
          <table className="w-full">{Object.entries(s.values as Record<string, string>).map(([k, v]) => (
            <tr key={k}><td className="text-slate-500 pr-3 py-0.5 whitespace-nowrap">{k}</td><td>{v}</td></tr>
          ))}</table>
        )}
        {s.step === 'calc' && ['rate', 'limit'].map(key => (
          <div key={key} className="mb-2">
            <b className="text-amber-300">{s[key].name}</b> = <b>{key === 'limit' ? Number(s[key].value).toLocaleString() + '원' : s[key].value + '%'}</b>
            <table className="w-full mt-1">{s[key].steps.map((st: any, i: number) => (
              <tr key={i}><td className="text-slate-500 pr-2 py-0.5">{st.label}</td>
                <td className="font-mono text-slate-400 pr-2">{st.formula}</td>
                <td className="font-mono">{st.value}</td></tr>
            ))}</table>
          </div>
        ))}
        {s.step === 'mask' && (
          <div>
            <div className="mb-1">마스킹된 필드: {s.maskedFields.map((f: any) => (
              <span key={f.token} className="chip text-[10px] mr-1" style={{ borderColor: 'var(--onprem)' }}>{f.field} → {f.token}</span>
            ))}</div>
            <div className="mb-1">경계 통과 개인식별자(실측 스캔): <b className={s.piiOutbound === 0 ? 'text-emerald-400' : 'text-rose-400'}>{s.piiOutbound}건</b></div>
            <details><summary className="cursor-pointer text-slate-400">Bedrock에 실제 전달된 페이로드 보기</summary>
              <pre className="mt-1 bg-slate-950 rounded p-2 overflow-x-auto whitespace-pre-wrap">{s.maskedPayload}</pre></details>
          </div>
        )}
      </div>}
    </div>
  );
}

export default function S2() {
  const [query, setQuery] = useState(S2_PRESET);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<Stage[]>([]);
  const [text, setText] = useState('');
  const [done, setDone] = useState<any>(null);
  const [err, setErr] = useState('');

  const run = async (q?: string) => {
    const qq = q ?? query;
    if (running || !qq.trim()) return;
    if (q) setQuery(q);
    setRunning(true); setErr(''); setStages([]); setText(''); setDone(null);
    try {
      await sock.run('s2', { query: qq }, (e) => {
        if (e.type === 's2.stage') setStages(s => [...s, e as unknown as Stage]);
        if (e.type === 's2.token') setText(t => t + e.t);
        if (e.type === 's2.done') setDone(e);
      });
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  return (
    <div>
      <div className="panel p-3 mb-4 flex gap-2 items-center">
        <button className="chip whitespace-nowrap hover:border-sky-500 text-sky-300" onClick={() => run(S2_PRESET)}>시나리오 S2</button>
        <button className="chip whitespace-nowrap hover:border-rose-500 text-rose-300" onClick={() => run(S5_PRESET)}>S5 차단 시연</button>
        <input className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
          value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()} />
        <button onClick={() => run()} disabled={running}
          className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
          {running ? '상담 중…' : '상담 실행'}
        </button>
      </div>
      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}

      <div className="grid grid-cols-[1fr_1fr] gap-4">
        <div>
          <div className="text-xs text-slate-500 mb-2">파이프라인 단계 (펼쳐보기) — <span style={{ color: 'var(--onprem)' }}>■ 온프렘</span> / <span style={{ color: 'var(--cloud)' }}>■ 클라우드</span></div>
          {stages.map((s, i) => <StagePanel key={i} s={s} />)}
        </div>
        <div className="panel p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--cloud)' }} />
            <b>⑥ LLM 설명 (숫자는 계산엔진 확정값만)</b>
          </div>
          {done?.blocked
            ? <div className="text-rose-300 text-sm border border-rose-900 rounded-lg p-3">
                🛡 Bedrock Guardrails 차단: {done.message}</div>
            : <div className="md text-slate-200">{text}{running && !done && <span className="blink">▌</span>}</div>}
          {done && !done.blocked && (
            <div className="mt-3 text-xs space-y-1">
              <div>출력 가드레일: <b className={done.guardrailOut.action === 'NONE' ? 'text-emerald-400' : 'text-rose-400'}>{done.guardrailOut.action}</b></div>
              <div>{done.inventedNumbers?.length
                ? <span className="text-rose-400">⚠ 계산엔진에 없는 수치 발견: {done.inventedNumbers.join(', ')}</span>
                : <span className="text-emerald-400">✓ 수치 검증 통과 — 모든 숫자가 계산엔진 출력과 일치</span>}</div>
              <div className="text-slate-500">traceId {done.traceId} · {done.elapsedMs}ms — Two-Plane 뷰에 계측 기록됨</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
