import { Component, Suspense, lazy, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { auth, login } from './lib';
import S1 from './S1';
import LibraryPage from './documents/LibraryPage';
import S2 from './S2';
import Studio from './Studio';
import { Agents, Dashboard, Explore, Frame, TwoPlane } from './Views';
import RegistryView from './views/RegistryView';
import ScreenGen from './views/ScreenGen';
import Report from './views/Report';
import Portal from './views/Portal';
import AgentBuilder from './views/AgentBuilder';
import { loadConfig, sock, WsEvent } from './lib';
import { MENU_GROUPS, WORKBENCH_TITLES, WORKBENCH_VIEWS } from './navigation';
import type { WorkbenchView, Overview } from './workbench/types';

const Workbench = lazy(() => import('./workbench/Workbench'));
// 구 해시 호환 — 북마크·문서의 #/twoplane 은 Single Boundary 뷰로 연다
const ALIAS: Record<string, string> = { twoplane: 'boundary' };
// Query parameters identify an asset inside a view, not a different app menu.
const resolveView = () => { const h = location.hash.replace('#/', '').split('?')[0] || 'wb-planning'; return ALIAS[h] || h; };

const TITLE: Record<string, string> = {
  home: '플랫폼 대시보드', s1: '규정 영향 분석 — 원문 근거 · 변경 검토',
  documents: '내부 문서함 — 원문 · 버전 · 검토',
  s2: '마이데이터 상담 — 숫자는 LLM이 만들지 않는다', explore: '온톨로지 탐색기',
  registry: 'Agent Registry — 자산 승인 거버넌스', screengen: '화면 생성 — 승인된 컴포넌트만, 실검증 게이트',
  portal: 'UX Asset Portal — AI-Readable 화면 · 에셋 관리 (P1)',
  report: '보고서 생성 — Reader/Writer 권한 분리', boundary: 'Single Boundary 뷰 — 경계 통과 실측',
  guardrails: 'Guardrails 차단 이력', agents: '에이전트 빌더 — AgentCore Harness · Strands 런타임 · Gateway 도구 · Registry 승인',
  controlroom: '컨트롤룸 (레거시 데모 프록시 — RBAC·예산·감사는 컨트롤룸 백엔드가 시행)',
  studio: 'UI/UX 디자인 스튜디오', guide: '가이드북',
};

/* §11 데모 대체 표기 — 운영 구성을 대체한 지점을 화면에서 숨기지 않는다 (필수 배지, SPEC v2 §11 · §16) */
function DemoLegend({ route, onClose }: { route: WsEvent | null; onClose: () => void }) {
  const llm = route?.llmRoute;
  const rows: { no: string; title: string; prod: string; demo: string; note?: string; noteOk?: boolean }[] = [
    { no: '11-1', title: 'PII 추론 경로',
      prod: 'IDC GPU + vLLM (EKS Hybrid Nodes)',
      demo: 'Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체',
      note: llm == null ? '현재 LLM_ROUTE 확인 중…'
        : llm === 'gemma' ? '현재 LLM_ROUTE=gemma — Tier 2 대체 경로 사용 중 (us-west-2 직접 호출)'
          : `현재 LLM_ROUTE=${llm} — Tier 2(Gemma) 경로 미사용, Claude global 프로파일만 호출`,
      noteOk: llm !== 'gemma' },
    { no: '11-2', title: '익명화 게이트',
      prod: '가명처리 · 식별자 토큰화 · 재식별',
      demo: '합성데이터 가명 생성 + 규칙 기반 토큰화 구현 — ML 가명처리 · 재식별 볼트 미구현',
      note: '게이트는 경계 통과 지점으로 실제 존재하며 모든 Bedrock 호출의 페이로드를 여기서 계측한다', noteOk: true },
    { no: '11-3', title: '계정계 원장',
      prod: '계정계 원장을 Direct Connect 로 조회',
      demo: 'VPC 프라이빗 서브넷 내 RDS 합성 원장 (실계정 · 실상품명 미사용)' },
  ];
  return (
    <div className="absolute right-6 top-14 z-20 panel p-4 w-[560px] shadow-2xl text-xs" style={{ borderColor: '#fbbf24' }}>
      <div className="flex items-center gap-2 mb-3">
        <b className="text-sm text-amber-700">데모 대체 표기 (SPEC §11)</b>
        <span className="text-slate-400">운영 아키텍처를 대체한 지점 — 숨기지 않는다</span>
        <button className="chip ml-auto hover:border-slate-400" onClick={onClose}>닫기</button>
      </div>
      <div className="space-y-3">
        {rows.map(r => (
          <div key={r.no} className="rounded-lg border border-slate-200 p-3">
            <div className="font-semibold text-slate-800 mb-1"><span className="text-slate-500 mr-2">{r.no}</span>{r.title}</div>
            <div className="grid grid-cols-[44px_1fr] gap-x-2 gap-y-0.5">
              <span className="text-slate-500">운영</span><span className="text-slate-700">{r.prod}</span>
              <span className="text-slate-500">데모</span><span className="text-amber-700">{r.demo}</span>
            </div>
            {r.note && <div className={`mt-1 ${r.noteOk ? 'text-slate-400' : 'text-[#E90061]'}`}>{r.note}</div>}
          </div>
        ))}
        <div className="text-slate-500">11-4 AgentCore insights · Evaluations · Policy 는 <b className="text-slate-700">Tier 0/1 전용</b> — Tier 2 워크로드 경로에서 사용하지 않는다.
          {' '}그래프 백엔드 · 플레인 연결 · 캐시 응답 여부는 각 화면과 좌측 하단에 항상 표시된다.</div>
      </div>
    </div>
  );
}

/* 화면 단위 에러 바운더리 — 한 뷰의 렌더 오류가 앱 전체(내비 포함)를 백지로 만들지 않게 한다 (시연 안전장치) */
class ViewErrorBoundary extends Component<{ viewKey: string; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: any) { return { error: String(e?.message || e) }; }
  componentDidUpdate(prev: { viewKey: string }) { if (prev.viewKey !== this.props.viewKey && this.state.error) this.setState({ error: null }); }
  render() {
    if (this.state.error) return (
      <div className="panel p-5 text-sm">
        <div className="text-[#E90061] font-semibold mb-1">이 화면을 그리는 중 오류가 났습니다</div>
        <pre className="text-xs text-slate-400 whitespace-pre-wrap">{this.state.error}</pre>
        <button className="chip mt-3 hover:border-teal-500" onClick={() => this.setState({ error: null })}>다시 시도</button>
      </div>);
    return this.props.children;
  }
}

export default function App() {
  const [authed, setAuthed] = useState(false);
  const [view, setView] = useState(resolveView());
  useEffect(() => {
    const h = () => setView(resolveView());
    window.addEventListener('hashchange', h);
    return () => window.removeEventListener('hashchange', h);
  }, []);
  const go = (v: string) => {
    const current = new URLSearchParams(location.hash.split('?')[1] || '');
    const projectId = current.get('projectId') || current.get('project');
    const next = new URLSearchParams();
    if (projectId && (v.startsWith('wb-') || ['studio', 'portal', 'documents', 's1'].includes(v)))
      next.set('projectId', projectId);
    if (['studio', 'portal'].includes(v)) {
      for (const key of ['productId', 'contractId']) if (current.get(key)) next.set(key, current.get(key)!);
    }
    const query = next.toString();
    location.hash = '#/' + v + (query ? '?' + query : '');
  };
  const [cfg, setCfg] = useState<{ graphBackend?: string; planeDeployed?: boolean } | null>(null);
  const [route, setRoute] = useState<WsEvent | null>(null);   // traces(limit 1, 플레인 호출 없음) → llmRoute · genModel · plane
  const [legend, setLegend] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [focus, setFocus] = useState('all');
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => { loadConfig().then(c => setCfg(c)).catch(() => {}); }, []);
  useEffect(() => {
    if (!authed || WORKBENCH_VIEWS.has(view)) return;
    sock.request('traces', { limit: 1, withRetained: false }).then(setRoute).catch(() => {});
  }, [authed, view]);
  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  const routeLine = !route ? '…'
    : route.llmRoute === 'gemma' ? 'Tier 2 Gemma · us-west-2 직접 호출 (데모 대체)'
      : 'Tier 0/1 Claude global';

  return (
    <div className="flex h-full">
      {menuOpen && <button aria-label="메뉴 닫기" className="fixed inset-0 z-30 bg-slate-900/30 md:hidden" onClick={() => setMenuOpen(false)} />}
      <nav aria-label="업무 메뉴" className={`${menuOpen ? 'flex' : 'hidden'} md:flex fixed md:static inset-y-0 left-0 z-40 bg-white w-64 shrink-0 border-r border-slate-200 p-3 flex-col gap-1 overflow-y-auto`}>
        <div className="px-2 py-3">
          <div className="font-bold tracking-tight">아톰은행 <span className="text-[#008485]">Agentic AI</span></div>
          <div className="text-[10px] text-slate-500">ONE PLATFORM · SINGLE BOUNDARY</div>
        </div>
        <label className="px-2 text-xs text-slate-600">자주 쓰는 작업 영역
          <select aria-label="자주 쓰는 작업 영역" value={focus} onChange={e => setFocus(e.target.value)}
            className="mt-1 mb-2 w-full border border-slate-300 rounded-lg bg-white p-2">
            <option value="all">전체 업무</option>
            {MENU_GROUPS.filter(g => !['reference', 'common', 'operator'].includes(g.id)).map(g =>
              <option key={g.id} value={g.id}>{g.label}</option>)}
          </select>
        </label>
        {MENU_GROUPS.filter(group => !group.operator || overview?.operator).filter(group =>
          focus === 'all' || ['common', 'operator', 'reference', focus].includes(group.id)).map(group => (
          <details key={group.id} open={group.id !== 'reference' || group.items.some(item => item.id === view)} className="mb-1">
            <summary className="cursor-pointer px-2 py-2 text-xs font-semibold text-slate-500">{group.label}</summary>
            {group.items.map(n => <button key={n.id} onClick={() => { go(n.id); setMenuOpen(false); }}
              aria-current={view === n.id ? 'page' : undefined}
              className={`w-full px-3 py-2 rounded-lg text-sm text-left ${view === n.id ? 'bg-teal-50 font-semibold text-teal-900' : 'text-slate-600 hover:bg-slate-100'}`}>
              {n.label}
            </button>)}
          </details>
        ))}
        <div className="mt-auto px-2 py-2 text-[10px] text-slate-500 space-y-1">
          {WORKBENCH_VIEWS.has(view) ? <>
            <div>작업실 지식: <b className="text-teal-700">{overview ? '비공개 Vector·Graph 파일' : '프로젝트 선택 후 확인'}</b></div>
            <div>원본 연결과 게시 상태는 소스별로 확인합니다.</div>
            <div>연금 시나리오: 합성 자료 · 실계좌 미연동</div>
          </> : <>
          <div>그래프 백엔드: <b className={cfg?.graphBackend === 'neptune' ? 'text-teal-700' : 'text-amber-700'}>
            {cfg?.graphBackend === 'neptune' ? 'Neptune Serverless' : 'Local (개발용 인메모리)'}</b></div>
          <div>VPC 내부 플레인: <b className={cfg?.planeDeployed ? 'text-amber-700' : 'text-[#E90061]'}>
            {cfg?.planeDeployed ? '격리 VPC · ECS+RDS+Neptune (연결됨)' : '미연결 — 로컬 폴백'}</b></div>
          <div>추론 경로: <b className={route?.llmRoute === 'gemma' ? 'text-[#E90061]' : 'text-teal-700'}>{routeLine}</b>
            {route?.genModel && <div className="font-mono text-slate-400 break-all">{route.genModel}</div>}</div>
          <div className="text-slate-400">합성데이터 · 실계정 미사용</div>
          </>}
        </div>
      </nav>
      <main className="app-main flex-1 overflow-y-auto relative">
        <div className="app-toolbar flex items-center gap-3 py-4 border-b border-slate-200/70 sticky top-0 backdrop-blur z-10" style={{ background: 'rgba(255,255,255,.88)' }}>
          <button className="chip md:hidden" aria-label="업무 메뉴 열기" onClick={() => setMenuOpen(true)}>메뉴</button>
          <h1 className="font-bold">{WORKBENCH_TITLES[view] || TITLE[view] || ''}</h1>
          <div className="flex-1" />
          {!WORKBENCH_VIEWS.has(view) && <button className={`chip hover:border-amber-500 ${legend ? 'text-amber-200 border-amber-500' : 'text-amber-700'}`}
            title="SPEC §11 — 운영 구성을 대체한 지점 (필수 표기)" onClick={() => setLegend(l => !l)}>⚠ 데모 대체 표기</button>
          }
          <span className="chip text-slate-400">{auth.email}</span>
          <button className="chip hover:border-slate-400" onClick={() => { auth.logout(); location.reload(); }}>로그아웃</button>
        </div>
        {legend && !WORKBENCH_VIEWS.has(view) && <DemoLegend route={route} onClose={() => setLegend(false)} />}
        <div className="app-content">
          <ViewErrorBoundary viewKey={view}>
          {WORKBENCH_VIEWS.has(view) && <Suspense fallback={<p role="status" className="p-6 text-slate-500">작업실을 여는 중…</p>}>
            <Workbench view={view.slice(3) as WorkbenchView} onOverview={setOverview}
              onNavigate={target => { location.hash = '#/' + (target.startsWith('wb-') || target.startsWith('studio') || target.startsWith('portal') ? target : 'wb-' + target); }} />
          </Suspense>}
          {view === 'home' && <Dashboard go={go} />}
          {view === 's1' && <S1 />}
          {view === 'documents' && <LibraryPage />}
          {view === 's2' && <S2 />}
          {view === 'explore' && <Explore />}
          {view === 'registry' && <RegistryView />}
          {view === 'screengen' && <ScreenGen />}
          {view === 'portal' && <Portal />}
          {view === 'report' && <Report />}
          {view === 'boundary' && <TwoPlane />}
          {view === 'guardrails' && <TwoPlane blockedOnly />}
          {view === 'agents' && <AgentBuilder />}
          {view === 'controlroom' && <Agents />}
          {view === 'studio' && <Studio />}
          {view === 'guide' && <Frame src="https://www.atomai.click/AgenticAI-Platform/14-demo/ontology-workbench" note="업무별 작업실과 지식·영향 분석 — 4분 설명 영상" />}
          </ViewErrorBoundary>
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
        <div className="text-2xl font-bold tracking-tight">아톰은행 <span className="text-[#008485]">Agentic AI</span></div>
        <div className="text-xs text-slate-400 mt-1 mb-6">종합 Agentic AI 플랫폼 — 초대 계정 전용 (가입 없음)</div>
        <input className="w-full mb-2 px-3 py-2 rounded-lg bg-white border border-slate-300 text-sm"
          value={email} onChange={e => setEmail(e.target.value)} placeholder="이메일" />
        <input className="w-full mb-3 px-3 py-2 rounded-lg bg-white border border-slate-300 text-sm"
          type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="비밀번호"
          onKeyDown={e => e.key === 'Enter' && submit()} />
        {err && <div className="text-[#E90061] text-xs mb-2">{err}</div>}
        <button onClick={submit} disabled={busy}
          className="w-full py-2 rounded-lg bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm disabled:opacity-50">
          {busy ? '확인 중…' : '로그인'}
        </button>
        <div className="text-[11px] text-slate-500 mt-3">초대 계정 전용 · 비밀번호는 시연 운영자에게 (Secrets Manager 관리)</div>
      </div>
    </div>
  );
}
