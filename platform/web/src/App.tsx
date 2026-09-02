import { useEffect, useState } from 'react';
import { auth, login } from './lib';
import S1 from './S1';
import S2 from './S2';
import { Agents, Dashboard, Explore, Frame, RegistryView, TwoPlane } from './Views';

const NAV = [
  { id: 'home', ic: '◈', label: '대시보드' },
  { id: 's1', ic: '⧉', label: '규정 영향 분석', tag: 'S1' },
  { id: 's2', ic: '💬', label: '마이데이터 상담', tag: 'S2' },
  { id: 'explore', ic: '🕸', label: '온톨로지 탐색기' },
  { id: 'registry', ic: '🗂', label: 'Agent Registry', tag: 'S3' },
  { id: 'twoplane', ic: '⇄', label: 'Two-Plane 뷰', tag: 'S4' },
  { id: 'guardrails', ic: '🛡', label: 'Guardrails 로그', tag: 'S5' },
  { id: 'agents', ic: '🤖', label: '에이전트' },
  { id: 'studio', ic: '🎨', label: '디자인 스튜디오' },
  { id: 'guide', ic: '📖', label: '가이드북' },
];

const TITLE: Record<string, string> = {
  home: '플랫폼 대시보드', s1: '규정 영향 분석 — Vector RAG vs GraphRAG',
  s2: '마이데이터 상담 — 숫자는 LLM이 만들지 않는다', explore: '온톨로지 탐색기',
  registry: 'Agent Registry — 자산 승인 거버넌스', twoplane: 'Two-Plane 경계 뷰',
  guardrails: 'Guardrails 차단 이력', agents: '에이전트 카탈로그 · 채팅',
  studio: 'UI/UX 디자인 스튜디오', guide: '가이드북',
};

export default function App() {
  const [authed, setAuthed] = useState(false);
  const [view, setView] = useState(location.hash.replace('#/', '') || 'home');
  useEffect(() => {
    const h = () => setView(location.hash.replace('#/', '') || 'home');
    window.addEventListener('hashchange', h);
    return () => window.removeEventListener('hashchange', h);
  }, []);
  const go = (v: string) => { location.hash = '#/' + v; };
  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  return (
    <div className="flex h-full">
      <nav className="w-56 shrink-0 border-r border-slate-800 p-3 flex flex-col gap-1 overflow-y-auto">
        <div className="px-2 py-3">
          <div className="font-bold tracking-tight">아톰은행 <span className="text-sky-400">Agentic AI</span></div>
          <div className="text-[10px] text-slate-500">ONE PLATFORM · ALL SURFACES</div>
        </div>
        {NAV.map(n => (
          <button key={n.id} onClick={() => go(n.id)}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left
              ${view === n.id ? 'bg-sky-950/70 text-sky-200' : 'text-slate-400 hover:bg-slate-900'}`}>
            <span className="w-5 text-center">{n.ic}</span>{n.label}
            {n.tag && <span className="chip text-[9px] ml-auto">{n.tag}</span>}
          </button>
        ))}
        <div className="mt-auto px-2 py-2 text-[10px] text-slate-600">
          그래프 백엔드: Local(개발) → Neptune<br />합성데이터 · 실계정 미사용
        </div>
      </nav>
      <main className="flex-1 overflow-y-auto">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-slate-800/70 sticky top-0 backdrop-blur z-10" style={{ background: 'rgba(11,15,20,.85)' }}>
          <h1 className="font-bold">{TITLE[view] || ''}</h1>
          <div className="flex-1" />
          <span className="chip text-slate-400">{auth.email}</span>
          <button className="chip hover:border-slate-500" onClick={() => { auth.logout(); location.reload(); }}>로그아웃</button>
        </div>
        <div className="p-6 max-w-[1400px]">
          {view === 'home' && <Dashboard go={go} />}
          {view === 's1' && <S1 />}
          {view === 's2' && <S2 />}
          {view === 'explore' && <Explore />}
          {view === 'registry' && <RegistryView />}
          {view === 'twoplane' && <TwoPlane />}
          {view === 'guardrails' && <TwoPlane blockedOnly />}
          {view === 'agents' && <Agents />}
          {view === 'studio' && <Frame src="https://d4zwmnh2s47e9.cloudfront.net/" note="UI/UX 스튜디오 — 같은 플랫폼의 디자이너 서피스 (임베드)" />}
          {view === 'guide' && <Frame src="https://www.atomai.click/AgenticAI-Platform/" note="Agentic AI 플랫폼 엔지니어링 가이드북 (임베드)" />}
        </div>
      </main>
    </div>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState('demo@atomai.click');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true); setErr('');
    try { await login(email, pw); onDone(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="h-full flex items-center justify-center">
      <div className="panel p-8 w-96">
        <div className="text-2xl font-bold tracking-tight">아톰은행 <span className="text-sky-400">Agentic AI</span></div>
        <div className="text-xs text-slate-400 mt-1 mb-6">종합 Agentic AI 플랫폼 — 초대 계정 전용 (가입 없음)</div>
        <input className="w-full mb-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
          value={email} onChange={e => setEmail(e.target.value)} placeholder="이메일" />
        <input className="w-full mb-3 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
          type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="비밀번호"
          onKeyDown={e => e.key === 'Enter' && submit()} />
        {err && <div className="text-rose-400 text-xs mb-2">{err}</div>}
        <button onClick={submit} disabled={busy}
          className="w-full py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-50">
          {busy ? '확인 중…' : '로그인'}
        </button>
        <div className="text-[11px] text-slate-500 mt-3">demo: demo@atomai.click / !234Qwer</div>
      </div>
    </div>
  );
}
