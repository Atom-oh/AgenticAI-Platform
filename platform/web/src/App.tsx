import { useEffect, useRef, useState } from 'react';
import { auth, login, openS1Socket, WsEvent } from './lib';
import GraphView, { GEdge, GNode } from './GraphView';
import Hub from './Hub';

const PRESET = '전세자금대출 담보 인정 규정이 개정되면 영향받는 상품·화면·담당부서·수정이 필요한 문서는 무엇인가?';

type Chunk = { id: string; score: number; text: string };
type Counts = Record<string, number>;

export default function App() {
  const [authed, setAuthed] = useState(false);
  const [route, setRoute] = useState(location.hash || '#/');
  const [studioAssets, setStudioAssets] = useState<number | null>(null);
  useEffect(() => {
    const h = () => setRoute(location.hash || '#/');
    window.addEventListener('hashchange', h);
    return () => window.removeEventListener('hashchange', h);
  }, []);
  useEffect(() => {
    if (!authed) return;
    // UI/UX 스튜디오 공개 자산 API — 실패해도 허브는 정상 동작
    fetch('https://d4zwmnh2s47e9.cloudfront.net/api/assets')
      .then(r => r.json()).then(d => setStudioAssets((d.assets || []).length))
      .catch(() => setStudioAssets(null));
  }, [authed]);
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return route.startsWith('#/s1') ? <S1 /> : <Hub studioAssets={studioAssets} />;
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
        <div className="text-xs text-slate-400 mt-1 mb-6">규정 영향 분석 · GraphRAG 데모 — 초대 계정 전용 (가입 없음)</div>
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
      </div>
    </div>
  );
}

function S1() {
  const [query, setQuery] = useState(PRESET);
  const [running, setRunning] = useState(false);
  const [backend, setBackend] = useState('');
  const [vChunks, setVChunks] = useState<Chunk[]>([]);
  const [vText, setVText] = useState('');
  const [vDone, setVDone] = useState(false);
  const [gSeed, setGSeed] = useState<any>(null);
  const [gConf, setGConf] = useState(0);
  const [gCounts, setGCounts] = useState<Counts | null>(null);
  const [gText, setGText] = useState('');
  const [gDone, setGDone] = useState<any>(null);
  const [graph, setGraph] = useState<{ nodes: GNode[]; edges: GEdge[] }>({ nodes: [], edges: [] });
  const [err, setErr] = useState('');
  const wsRef = useRef<WebSocket | null>(null);

  const run = async () => {
    if (running || !query.trim()) return;
    setRunning(true); setErr('');
    setVChunks([]); setVText(''); setVDone(false);
    setGSeed(null); setGConf(0); setGCounts(null); setGText(''); setGDone(null);
    setGraph({ nodes: [], edges: [] });
    let doneCount = 0;
    const onEvent = (e: WsEvent) => {
      switch (e.type) {
        case 'meta': setBackend(e.backend); break;
        case 'vector.chunks': setVChunks(e.chunks); break;
        case 'vector.token': setVText(t => t + e.t); break;
        case 'vector.done': setVDone(true); if (++doneCount === 2) finish(); break;
        case 'graph.meta':
          setGSeed(e.seed); setGConf(e.seedConfidence); setGCounts(e.counts);
          setGraph(e.graph); break;
        case 'graph.token': setGText(t => t + e.t); break;
        case 'graph.done': setGDone(e); if (++doneCount === 2) finish(); break;
        case 'error': setErr(e.message); break;
      }
    };
    const finish = () => { setRunning(false); wsRef.current?.close(); wsRef.current = null; };
    try {
      const ws = await openS1Socket(onEvent, () => setRunning(false));
      wsRef.current = ws;
      ws.send(JSON.stringify({ action: 's1', query }));
    } catch (e: any) { setErr(e.message); setRunning(false); }
  };

  return (
    <div className="max-w-[1400px] mx-auto p-5">
      <header className="flex items-center gap-3 mb-4">
        <a href="#/" className="chip hover:border-sky-500 text-slate-300">← 플랫폼 허브</a>
        <div className="text-lg font-bold">아톰은행 <span className="text-sky-400">규정 영향 분석</span></div>
        <span className="chip text-slate-400">S1 — Vector RAG vs GraphRAG</span>
        <div className="flex-1" />
        {backend && (
          <span className="chip" style={{ borderColor: backend === 'neptune' ? 'var(--cloud)' : 'var(--onprem)' }}>
            그래프 백엔드: <b>{backend === 'neptune' ? 'Amazon Neptune' : 'Local (개발용)'}</b>
          </span>
        )}
        <span className="chip text-slate-400">{auth.email}</span>
        <button className="chip hover:border-slate-500" onClick={() => location.reload()}>로그아웃</button>
      </header>

      <div className="panel p-3 mb-4 flex gap-2 items-center">
        <button className="chip whitespace-nowrap hover:border-sky-500 text-sky-300"
          onClick={() => setQuery(PRESET)}>시나리오 S1 질문</button>
        <input className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
          value={query} onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && run()} />
        <button onClick={run} disabled={running}
          className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
          {running ? '분석 중…' : '동시 실행'}
        </button>
      </div>
      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}

      <div className="grid grid-cols-2 gap-4 mb-4">
        {/* 좌: Vector RAG */}
        <section className="panel p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2.5 h-2.5 rounded-full bg-slate-400" />
            <b>Vector RAG</b>
            <span className="text-xs text-slate-500">하이브리드(BM25+dense) + 리랭커 — 약화 없음</span>
          </div>
          {vChunks.length > 0 && (
            <div className="mb-3 space-y-1">
              {vChunks.map(c => (
                <div key={c.id} className="text-xs text-slate-400 border border-slate-800 rounded-lg px-2 py-1">
                  <span className="text-slate-200 font-mono">{c.id}</span>
                  <span className="text-sky-400 ml-2">{c.score.toFixed(3)}</span>
                  <div className="truncate">{c.text}</div>
                </div>
              ))}
            </div>
          )}
          <div className="md text-slate-300">{vText}{running && !vDone && <span className="blink">▌</span>}</div>
          {vDone && <div className="mt-3 text-xs text-amber-400/90">
            → 관련 규정 청크는 정확히 찾았지만, 개정이 미치는 상품·화면·부서·문서의 <b>목록</b>은 답하지 못한다.</div>}
        </section>

        {/* 우: GraphRAG */}
        <section className="panel p-4 border-sky-900/60" style={{ borderColor: '#155e75' }}>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="w-2.5 h-2.5 rounded-full bg-sky-400" />
            <b>GraphRAG</b>
            {gSeed && <span className="chip text-xs">seed: <b className="font-mono">{gSeed.code}</b></span>}
            {gSeed && <span className="chip text-xs">seed 신뢰도 <b className={gConf > 0.6 ? 'text-emerald-400' : 'text-amber-400'}>{(gConf * 100).toFixed(0)}%</b></span>}
          </div>
          {gCounts && (
            <div className="flex gap-2 mb-3 flex-wrap">
              {Object.entries({ products: '상품', screens: '화면', departments: '부서', documents: '문서', components: '컴포넌트' })
                .map(([k, label]) => (
                  <span key={k} className="chip" style={{ borderColor: 'var(--cloud)' }}>
                    {label} <b className="text-sky-300">{gCounts[k]}</b>
                  </span>
                ))}
            </div>
          )}
          <div className="md text-slate-200">{gText}{running && !gDone && <span className="blink">▌</span>}</div>
          {gDone && !gDone.error && (
            <div className="mt-3 text-xs">
              {gDone.hallucinatedIds?.length
                ? <span className="text-rose-400">⚠ 근거 없는 ID 인용 {gDone.hallucinatedIds.length}건: {gDone.hallucinatedIds.join(', ')}</span>
                : <span className="text-emerald-400">✓ 근거 검증 통과 — 인용된 노드 ID 전부가 순회 결과에 존재</span>}
            </div>
          )}
          {gDone?.error && <div className="text-rose-400 text-sm">{gDone.error}</div>}
        </section>
      </div>

      <GraphView nodes={graph.nodes} edges={graph.edges} />
    </div>
  );
}
