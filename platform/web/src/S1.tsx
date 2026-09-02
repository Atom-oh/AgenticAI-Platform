// S1 규정 영향 분석 — Vector RAG / GraphRAG 좌우 비교 + 순회 경로 시각화 (SPEC v2 §2 S1, 최우선 화면)
import { useState } from 'react';
import { sock } from './lib';
import GraphView, { GEdge, GNode } from './GraphView';

export const S1_PRESET = '전세자금대출 담보 인정 규정이 개정되면 영향받는 상품 · 화면 · 컴포넌트 · 담당부서 · 수정이 필요한 문서는?';

type Chunk = { id: string; score: number; text: string };
type Counts = Record<string, number>;

// 순회 경로 순서(§5-5): Regulation → PolicyRule → Product → Screen → Component → Department → Document
const COUNT_LABELS: [string, string][] = [
  ['policyRules', '정책규칙'], ['products', '상품'], ['screens', '화면'], ['components', '컴포넌트'],
  ['departments', '부서'], ['documents', '문서'],
];

function ModelFooter({ ev }: { ev: any }) {
  if (!ev || !ev.modelId) return null;   // 트레이스에 modelId 가 기록될 때만 표시 (추정 금지)
  const u = ev.usage || {};
  return (
    <div className="mt-2 text-[11px] text-slate-500">
      모델 ID <span className="font-mono text-sky-200">{ev.modelId}</span>
      {u.inputTokens != null && <> · 실측 토큰 {Number(u.inputTokens).toLocaleString()}/{Number(u.outputTokens || 0).toLocaleString()}</>}
      {ev.route && <> · 경로 {String(ev.route)}{ev.tier != null && ` (Tier ${ev.tier})`}</>}
    </div>
  );
}

export default function S1() {
  const [query, setQuery] = useState(S1_PRESET);
  const [running, setRunning] = useState(false);
  const [vChunks, setVChunks] = useState<Chunk[]>([]);
  const [vText, setVText] = useState('');
  const [vDone, setVDone] = useState<any>(null);
  const [vPlane, setVPlane] = useState<{ plane: string; label: string } | null>(null);
  const [vErr, setVErr] = useState('');
  const [gSeed, setGSeed] = useState<any>(null);
  const [gConf, setGConf] = useState(0);
  const [gCounts, setGCounts] = useState<Counts | null>(null);
  const [gText, setGText] = useState('');
  const [gDone, setGDone] = useState<any>(null);
  const [graph, setGraph] = useState<{ nodes: GNode[]; edges: GEdge[] }>({ nodes: [], edges: [] });
  const [err, setErr] = useState('');
  const [cached, setCached] = useState<string>('');

  const run = async () => {
    if (running || !query.trim()) return;
    setRunning(true); setErr('');
    setVChunks([]); setVText(''); setVDone(null); setVPlane(null); setVErr('');
    setGSeed(null); setGConf(0); setGCounts(null); setGText(''); setGDone(null);
    setGraph({ nodes: [], edges: [] }); setCached('');
    try {
      await sock.run('s1', { query }, (e) => {
        if (e.type === 'cache.replay') { // 폴백 (SPEC §8-5): 캐시 응답 — 배지로 표기, 상태 초기화
          setCached(e.reason || '캐시 응답'); setVChunks([]); setVText(''); setVDone(null);
          setGSeed(null); setGText(''); setGDone(null); return;
        }
        switch (e.type) {
          case 'vector.chunks': setVChunks(e.chunks); setVPlane({ plane: e.searchPlane, label: e.searchLabel }); break;
          case 'vector.token': setVText(t => t + e.t); break;
          case 'vector.done': setVDone(e); if (e.error) setVErr(e.error); break;
          case 'graph.meta':
            setGSeed(e.seed); setGConf(e.seedConfidence); setGCounts(e.counts);
            setGraph(e.graph); break;
          case 'graph.token': setGText(t => t + e.t); break;
          case 'graph.done': setGDone(e); break;
        }
      });
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  const vInVpc = vPlane?.plane === 'onprem' || vPlane?.plane === 'plane' || vPlane?.plane === 'vpc';
  return (
    <div>
      <div className="panel p-3 mb-4 flex gap-2 items-center">
        <button className="chip whitespace-nowrap hover:border-sky-500 text-sky-300"
          onClick={() => setQuery(S1_PRESET)}>시나리오 S1</button>
        <input className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
          value={query} onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && run()} />
        <button onClick={run} disabled={running}
          className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
          {running ? '분석 중…' : '동시 실행'}
        </button>
      </div>
      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}
      {cached && <div className="mb-3 text-xs"><span className="chip text-amber-300 border-amber-700">캐시 응답</span> <span className="text-slate-400">{cached} — Bedrock 실시간 응답이 아닙니다 (이전 실행 결과 재생)</span></div>}

      <div className="grid grid-cols-2 gap-4 mb-4">
        <section className="panel p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2.5 h-2.5 rounded-full bg-slate-400" />
            <b>Vector RAG</b>
            {vPlane && <span className="chip text-[10px] ml-2" style={{ borderColor: vInVpc ? 'var(--vpc)' : '#f43f5e', color: vInVpc ? 'var(--vpc)' : '#fda4af' }}
              title={vPlane.label}>{vInVpc ? 'VPC 내부 벡터 인덱스' : '로컬 인덱스 (개발용)'}</span>}
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
          {vErr && <div className="text-rose-300 text-xs border border-rose-900 rounded-lg p-2 mb-2">⚠ {vErr}</div>}
          <div className="md text-slate-300">{vText}{running && !vDone && <span className="blink">▌</span>}</div>
          {vDone && <div className="mt-3 text-xs text-amber-400/90">
            → 관련 규정 청크는 정확히 찾았지만, 개정이 미치는 상품 · 화면 · 컴포넌트 · 부서 · 문서의 <b>목록</b>은 답하지 못한다.</div>}
          <ModelFooter ev={vDone} />
        </section>

        <section className="panel p-4" style={{ borderColor: '#155e75' }}>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="w-2.5 h-2.5 rounded-full bg-sky-400" />
            <b>GraphRAG</b>
            {gSeed && <span className="chip text-xs">seed: <b className="font-mono">{gSeed.code}</b></span>}
            {gSeed && <span className="chip text-xs">seed 신뢰도 <b className={gConf > 0.6 ? 'text-emerald-400' : 'text-amber-400'}>{(gConf * 100).toFixed(0)}%</b></span>}
          </div>
          {gCounts && (
            <div className="flex gap-2 mb-3 flex-wrap">
              {COUNT_LABELS.filter(([k]) => gCounts[k] != null).map(([k, label]) => (
                <span key={k} className="chip" style={{ borderColor: 'var(--bedrock)' }}>
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
          <ModelFooter ev={gDone} />
        </section>
      </div>

      {graph.nodes.length > 0 && (
        <div className="text-xs text-slate-500 mb-1">노드 클릭: 이웃 강조 · 더블클릭: 온톨로지 탐색기에서 열기 · 순회 경로: Regulation → PolicyRule → Product → Screen → Component → Department → Document</div>
      )}
      <GraphView nodes={graph.nodes} edges={graph.edges}
        onOpen={(id) => {
          import('./Views').then(v => { v.setPendingExploreNode(id); location.hash = '#/explore'; });
        }} />
    </div>
  );
}
