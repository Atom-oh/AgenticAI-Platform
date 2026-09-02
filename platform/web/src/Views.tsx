// 보조 뷰 모음: 대시보드 / 온톨로지 탐색기 / 에이전트 / Single Boundary 뷰(S4) / Guardrails 로그(S5) / 임베드
// (Registry·화면생성·보고서·Portal 은 views/). 색 규칙(SPEC v2 §8-4): VPC 내부 = var(--vpc) 앰버, Bedrock = var(--bedrock) 시안.
import { useEffect, useState } from 'react';
import { auth, sock, WsEvent } from './lib';
import GraphView from './GraphView';

/* ---------------- 공용 라벨 (전 화면 동일 규칙) ---------------- */
const ROSE = '#f43f5e';
export const planeColor = (p?: string) =>
  p === 'bridge' || p === 'direct' ? 'var(--vpc)' : p === 'cloud' ? 'var(--bedrock)' : ROSE;
/** 셸이 백엔드 planeLabel 문자열을 그대로 쓰지 않고 모드 값으로 표기한다 (용어 규칙 §12.13). */
export const planeLabel = (p?: string) =>
  p === 'bridge' ? 'VPC 내부 플레인 (격리 서브넷 ECS · 브리지 경유)'
    : p === 'direct' ? 'VPC 내부 플레인 (격리 서브넷 ECS · 직접)'
      : p === 'local' ? '로컬 폴백 (개발용 — VPC 분리 아님)'
        : p === 'cloud' ? 'Bedrock' : 'VPC 내부 플레인 미연결';
const planeCell = (p?: string) =>
  p === 'bridge' ? 'VPC 내부 · 브리지' : p === 'direct' ? 'VPC 내부 · 직접'
    : p === 'cloud' ? 'Bedrock' : p === 'local' ? '로컬 폴백' : p || '—';
/** 모델 ID 접두어로 추론 라우팅을 읽는다 — 트레이스에 기록된 실제 modelId 기준 (SPEC §4). */
export function routeOfModel(id: string): { infer: string; tier: string; gemma: boolean } {
  if (/gemma/i.test(id)) return { infer: '추론: us-west-2 직접 호출 (bedrock-mantle)', tier: 'Tier 2 · Gemma (데모 대체)', gemma: true };
  if (id.startsWith('global.')) return { infer: '추론: global 라우팅', tier: 'Tier 0/1 · Claude', gemma: false };
  if (id.startsWith('apac.')) return { infer: '추론: APAC 교차 리전 라우팅', tier: 'Tier 0/1 · Claude', gemma: false };
  if (/^(us|eu)\./.test(id)) return { infer: '추론: 교차 리전 라우팅', tier: 'Tier 0/1', gemma: false };
  return { infer: '추론: 단일 리전', tier: 'Tier 0/1', gemma: false };
}
const num = (v: any) => (v === null || v === undefined || v === '' ? null : Number(v));
const fmt = (v: any) => { const n = num(v); return n === null || Number.isNaN(n) ? '—' : n.toLocaleString(); };
const asList = (v: any): any[] => (Array.isArray(v) ? v : []);   // DynamoDB Decimal→문자열 등 배열이 아닌 값 방어 (S4 백지화 원인)
const fieldsOf = (i: any): string[] => {
  const raw = asList(i.boundaryFields).length ? asList(i.boundaryFields) : asList(i.maskedFields);
  return raw.map((f: any) => (typeof f === 'string' ? f : f?.field || f?.name || String(f)));
};
const isGate = (i: any) => i.blockedBy === 'gate' || !!i.gateRejected;
const blockReason = (i: any) =>
  (asList(i.topics).length ? asList(i.topics).join(', ') : '') || i.blockReason || i.reason || (isGate(i) ? '게이트 거부' : 'Guardrails');

/* ---------------- 대시보드 ---------------- */
export function Dashboard({ go }: { go: (v: string) => void }) {
  const [d, setD] = useState<WsEvent | null>(null);
  const [traces, setTraces] = useState<WsEvent | null>(null);
  useEffect(() => {
    sock.request('hub', { idToken: auth.idToken }).then(setD).catch(() => {});
    sock.request('traces').then(setTraces).catch(() => {});
  }, []);
  const tile = (label: string, v: any, view?: string, accent = 'var(--bedrock)', sub?: string, small = false) => (
    <button onClick={() => view && go(view)} className="panel p-4 text-left hover:border-slate-500"
      style={{ borderTop: `2px solid ${accent}` }}>
      <div className={small ? 'text-sm font-bold break-all leading-snug' : 'text-2xl font-bold'}>{v ?? '…'}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
      <div className="text-xs text-slate-400 mt-1">{label}</div>
    </button>
  );
  const r = traces?.retained;
  const retainedOk = r && r.source === 'plane-health';
  const separated = d?.plane === 'bridge' || d?.plane === 'direct';
  const route = d ? (d.llmRoute === 'gemma' ? 'Tier 2 · Gemma (데모 대체)' : 'Tier 0/1 · Claude global') : undefined;
  return (
    <div>
      <div className="grid grid-cols-4 gap-3 mb-4">
        {tile('온톨로지 노드 (합성)', d?.graphNodes?.toLocaleString(), 'explore')}
        {tile('Registry 레코드 (승인/전체)', d && `${d.registryApproved}/${d.registry}`, 'registry', 'var(--vpc)')}
        {tile('플랫폼 서피스 (AgentCore Registry)', d && `${d.surfacesApproved}/${d.surfaces}`, 'registry')}
        {tile('에이전트 (컨트롤룸)', d && `${d.agentsApproved}/${d.agents}`, 'agents')}
      </div>
      <div className="grid grid-cols-5 gap-3 mb-6">
        {tile('VPC 내부에 남은 항목 — 인덱스 청크 · 감사 원문 (플레인 실측)',
          traces ? (retainedOk ? `${fmt(r.vectorChunks)} · ${fmt(r.auditRecords)}건` : '—') : '…',
          'boundary', retainedOk ? 'var(--vpc)' : ROSE,
          traces ? (retainedOk ? `저장소 ${String(r.store || '').toUpperCase()} · 온톨로지 ${fmt(r.ontologyNodes)} 노드` : '플레인 미연결 — 실측 불가') : undefined)}
        {tile('Single Boundary 통과 요청 · 실측 토큰(out)',
          traces ? `${traces.requests}건 · ${fmt(traces.tokensOutTotal)}tok` : '…', 'boundary', 'var(--bedrock)',
          traces ? `식별자 탐지 ${traces.piiOutboundTotal}건 — 독립 스캔` : undefined)}
        {tile('LLM 경로 · 모델 ID', route, 'boundary', d?.llmRoute === 'gemma' ? ROSE : 'var(--bedrock)',
          d ? (d.genModel || '모델 ID 미확인') : undefined, true)}
        {tile('Guardrails 차단 / 게이트 거부 / 캐시 응답',
          traces ? `${traces.guardrailBlocked ?? traces.blocked} / ${traces.gateRejected ?? 0} / ${traces.cached}` : '…', 'guardrails', ROSE)}
        {tile('그래프 · VPC 내부 플레인',
          d ? `${d.backend === 'neptune' ? 'Neptune' : 'Local(개발)'} · ${separated ? '연결됨' : '로컬 폴백'}` : '…', 'boundary',
          separated ? 'var(--vpc)' : ROSE)}
      </div>
      <div className="text-sm text-slate-400 mb-2 font-semibold">15분 시연 순서</div>
      <div className="grid grid-cols-4 gap-3">
        {[['s1', 'S1 규정 영향 분석', 'GraphRAG의 존재 이유 (4분)'],
          ['s2', 'S2 마이데이터 상담', '숫자는 LLM이 만들지 않는다 (4분)'],
          ['screengen', 'S3 화면 생성 + Registry', '승인 상태가 결과를 바꾼다 (3분)'],
          ['portal', 'UX Asset Portal (P1 지원)', 'AI-Readable 화면/에셋 관리 — Related 카운트는 그래프 실쿼리'],
          ['boundary', 'S4 Single Boundary 뷰', '경계 통과 실측 (2분)'],
          ['guardrails', 'S5 Guardrails', '차단 시연 (2분)'],
          ['report', 'F7 보고서 Reader/Writer', '인젝션 무력화 (보너스)']].map(([v, t, s]) => (
            <button key={v} onClick={() => go(v)} className="panel p-3 text-left hover:border-sky-600">
              <div className="font-semibold text-sm">{t}</div>
              <div className="text-xs text-slate-500 mt-1">{s}</div>
            </button>
          ))}
      </div>
    </div>
  );
}

/* ---------------- 온톨로지 탐색기 ---------------- */
export let pendingExploreNode = '';
export function setPendingExploreNode(id: string) { pendingExploreNode = id; }

export function Explore() {
  const [center, setCenter] = useState(pendingExploreNode || 'REG-LN-001');
  const [data, setData] = useState<WsEvent | null>(null);
  const load = (id: string) => {
    setCenter(id);
    sock.request('explore', { nodeId: id }).then(setData).catch(() => {});
  };
  useEffect(() => {
    load(pendingExploreNode || 'REG-LN-001');
    pendingExploreNode = '';
  }, []);
  return (
    <div>
      <div className="panel p-3 mb-3 flex gap-2 items-center">
        <span className="text-xs text-slate-500">노드 ID</span>
        <input className="px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono w-56"
          value={center} onChange={e => setCenter(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && load(center)} />
        <button className="chip hover:border-sky-500" onClick={() => load(center)}>탐색</button>
        {['REG-LN-001', 'PRD-LN-001', 'CMP-Button-v2', 'D-LNP'].map(p => (
          <button key={p} className="chip text-xs font-mono hover:border-slate-500" onClick={() => load(p)}>{p}</button>
        ))}
      </div>
      {data?.props && (
        <div className="panel p-3 mb-3 text-xs text-slate-300 flex gap-4 flex-wrap">
          {Object.entries(data.props).slice(0, 8).map(([k, v]) => (
            <span key={k}><span className="text-slate-500">{k}</span> {String(v).slice(0, 60)}</span>
          ))}
        </div>
      )}
      <div className="text-xs text-slate-500 mb-2">노드를 클릭하면 그 노드를 중심으로 이웃 관계를 다시 펼칩니다.</div>
      {data?.graph && <GraphView nodes={data.graph.nodes} edges={data.graph.edges}
        onSelect={(id) => load(id)} />}
    </div>
  );
}

/* ---------------- 에이전트 (컨트롤룸 프록시 — 거버넌스는 컨트롤룸이 시행) ---------------- */
export function Agents() {
  const [agents, setAgents] = useState<any[]>([]);
  const [sel, setSel] = useState<any>(null);
  const [msg, setMsg] = useState('');
  const [log, setLog] = useState<{ who: string; text: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID().replace(/-/g, ''));
  useEffect(() => {
    sock.request('agents', { idToken: auth.idToken })
      .then(e => setAgents(e.agents || [])).catch(() => {});
  }, []);
  const send = async () => {
    if (!sel || !msg.trim() || busy) return;
    const m = msg; setMsg(''); setBusy(true);
    setLog(l => [...l, { who: 'me', text: m }]);
    try {
      const r = await sock.request('chat', { idToken: auth.idToken, agentId: sel.id, message: m, sessionId });
      setLog(l => [...l, { who: 'ai', text: r.reply || r.error || '(응답 없음)' }]);
    } catch (e: any) { setLog(l => [...l, { who: 'ai', text: '오류: ' + e.message }]); }
    setBusy(false);
  };
  return (
    <div className="grid grid-cols-[320px_1fr] gap-4">
      <div className="space-y-2">
        <div className="text-xs text-slate-500 mb-1">컨트롤룸 카탈로그 — 예산·감사·승인은 컨트롤룸 백엔드가 그대로 시행</div>
        {agents.map(a => (
          <button key={a.id} onClick={() => { setSel(a); setLog([]); }}
            className={`panel p-3 w-full text-left hover:border-slate-500 ${sel?.id === a.id ? 'border-sky-600' : ''}`}>
            <div className="text-sm font-semibold">{a.name}
              <span className={`chip text-[10px] ml-2 ${a.status === 'APPROVED' ? 'text-emerald-400' : 'text-amber-400'}`}>{a.status}</span></div>
            <div className="text-xs text-slate-500 truncate">{a.description}</div>
          </button>
        ))}
      </div>
      <div className="panel p-4 flex flex-col min-h-[420px]">
        {!sel ? <div className="text-slate-500 text-sm m-auto">왼쪽에서 에이전트를 선택하세요</div> : (
          <>
            <div className="text-sm font-semibold mb-2">{sel.name} <span className="text-xs text-slate-500">Tier {sel.riskTier} · {sel.team}</span></div>
            <div className="flex-1 overflow-y-auto space-y-2 mb-3">
              {log.map((l, i) => (
                <div key={i} className={`md text-sm p-2 rounded-lg max-w-[85%] ${l.who === 'me' ? 'bg-sky-950/60 ml-auto' : 'bg-slate-900'}`}>{l.text}</div>
              ))}
              {busy && <div className="text-slate-500 text-sm">답변 생성 중…</div>}
            </div>
            <div className="flex gap-2">
              <input className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
                value={msg} onChange={e => setMsg(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()}
                placeholder="메시지…" />
              <button onClick={send} disabled={busy} className="px-4 rounded-lg bg-sky-500/90 text-slate-950 font-semibold text-sm disabled:opacity-40">전송</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ---------------- Single Boundary 뷰 (S4) & Guardrails 로그 (S5) ---------------- */
/** 경계 통과 애니메이션 — 최근 트레이스 1건 = 점 1개. VPC 내부(앰버) → 익명화 게이트 → Bedrock(시안). 순수 CSS (index.css .bx-*). */
function BoundaryFlow({ items }: { items: any[] }) {
  const recent = items.slice(0, 14).reverse();
  return (
    <div className="panel px-4 py-3 mb-4">
      <div className="flex items-center justify-between text-[11px] mb-1.5">
        <span className="font-semibold" style={{ color: 'var(--vpc)' }}>■ VPC 내부 (프라이빗 서브넷 · 원장 · 인덱스 · 온톨로지)</span>
        <span className="text-slate-400">익명화 게이트 — 유일한 통과 지점</span>
        <span className="font-semibold" style={{ color: 'var(--bedrock)' }}>Bedrock (global 프로파일) ■</span>
      </div>
      <div className="bx-track" aria-label="경계 통과 흐름">
        {recent.map((i, idx) => (
          <span key={i.traceId || idx}
            className={`bx-dot ${i.blocked ? 'gate' : i.cached ? 'stay' : ''}`}
            style={{ ['--bx-delay' as any]: `${idx * 0.45}s`, ['--bx-dur' as any]: `${4 + Math.min(recent.length, 14) * 0.45}s` }}
            title={`${i.traceId} · ${i.scenario} · ${i.blocked ? '경계에서 차단/거부' : i.cached ? '캐시 응답 — 경계 통과 없음' : `통과 ${fmt(i.tokensIn)}/${fmt(i.tokensOut)} tok`}`} />
        ))}
        {recent.length === 0 && <span className="absolute inset-0 text-[10px] text-slate-600 flex items-center justify-center">기록 없음</span>}
      </div>
      <div className="text-[10px] text-slate-500 mt-1.5">
        점 1개 = 트레이스 1건 (최근 {recent.length}건). 시안으로 바뀌는 지점이 경계 — <span className="text-rose-400">붉게 멈춘 점</span>은 Guardrails 차단·게이트 거부,
        {' '}앰버로 머무는 점은 캐시 응답(Bedrock 미호출). 모양은 애니메이션이지만 건수·색은 트레이스 실측이다.
      </div>
    </div>
  );
}

export function TwoPlane({ blockedOnly }: { blockedOnly?: boolean }) {
  const [data, setData] = useState<WsEvent | null>(null);
  const [err, setErr] = useState('');
  const reload = () => { setErr(''); sock.request('traces').then(setData).catch(e => setErr(e.message)); };
  useEffect(() => { reload(); }, []);
  const all: any[] = data?.items || [];
  const items = all.filter((i: any) => !blockedOnly || i.blocked);
  const r = data?.retained;
  const retainedOk = !!r && r.source === 'plane-health';
  const tokensIn = data?.tokensInTotal ?? all.reduce((s, i) => s + (num(i.tokensIn) || 0), 0);
  const fields = Array.from(new Set(all.flatMap(fieldsOf)));
  const models: { modelId: string; count: number }[] = data?.models || [];
  const gateRejected = data?.gateRejected ?? all.filter(isGate).length;
  const guardrailBlocked = data?.guardrailBlocked ?? all.filter(i => i.blocked && !isGate(i)).length;
  const blockedRows = all.filter(i => i.blocked);
  const infers = Array.from(new Set(models.map(m => routeOfModel(m.modelId).infer)));
  const storageOk = data?.plane === 'bridge' || data?.plane === 'direct';
  const cfgInfer = data?.genModel ? routeOfModel(String(data.genModel)).infer : null;

  const Tile = ({ n, title, children, accent = 'var(--line)' }: { n: string; title: string; children: any; accent?: string }) => (
    <div className="panel px-4 py-3 min-w-0" style={{ borderTop: `2px solid ${accent}` }}>
      <div className="text-[11px] text-slate-400 mb-1"><span className="text-slate-500 mr-1">{n}</span>{title}</div>
      {children}
    </div>
  );

  return (
    <div>
      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}
      {!blockedOnly && (
        <>
          <div className="grid grid-cols-5 gap-3 mb-3">
            <Tile n="①" title="VPC 내부에 남은 항목 (플레인 실측)" accent={retainedOk ? 'var(--vpc)' : ROSE}>
              {!data ? <div className="text-xl font-bold">…</div> : (
                <>
                  <div className="text-sm leading-6">
                    <div>인덱스 청크 <b className={retainedOk ? 'text-amber-300' : 'text-rose-400'}>{retainedOk ? fmt(r.vectorChunks) : '—'}</b></div>
                    <div>원장·감사 원문 <b className={retainedOk ? 'text-amber-300' : 'text-rose-400'}>{retainedOk ? `${fmt(r.auditRecords)}건` : '—'}</b>
                      {retainedOk && r.ledgerRows != null && <span className="text-slate-500"> · 원장 행 {fmt(r.ledgerRows)}</span>}</div>
                    <div>온톨로지 노드 <b className={r?.ontologyBackend === 'neptune' ? 'text-amber-300' : 'text-slate-300'}>{fmt(r?.ontologyNodes)}</b>
                      {r && r.ontologyBackend !== 'neptune' && <span className="text-rose-400 text-[10px] ml-1">Local(개발용) — VPC 내부 아님</span>}</div>
                  </div>
                  {retainedOk
                    ? <div className="text-[10px] text-slate-500 mt-1">저장소 {String(r.store || '').toUpperCase()} · 원문은 VPC 내부에만 (클라우드 트레이스는 해시·길이)</div>
                    : <div className="text-[10px] text-rose-400 mt-1">플레인 미연결 — 실측 불가{r?.reason ? ` (${r.reason})` : ''}</div>}
                </>
              )}
            </Tile>
            <Tile n="②" title="경계를 넘은 토큰 (Bedrock 실측 usage)" accent="var(--bedrock)">
              <div className="text-xl font-bold text-sky-300">{data ? `${fmt(tokensIn)} in · ${fmt(data.tokensOutTotal)} out` : '…'}</div>
              <div className="flex flex-wrap gap-1 mt-1">
                {fields.length ? fields.map(f => <span key={f} className="chip text-[10px] text-amber-300" style={{ borderColor: 'var(--vpc)' }}>{f}</span>)
                  : <span className="text-[10px] text-slate-500">전달된 필드 기록 없음</span>}
              </div>
              {data && <div className={`text-[10px] mt-1 ${Number(data.piiOutboundTotal) === 0 ? 'text-slate-500' : 'text-rose-400'}`}>
                식별자 탐지 {data.piiOutboundTotal}건 — 독립 스캔(규칙 + Guardrails PII)</div>}
            </Tile>
            <Tile n="③" title="사용한 모델 ID (트레이스 기록)" accent="var(--bedrock)">
              {!data ? <div className="text-xl font-bold">…</div> : models.length === 0
                ? <div className="text-xs text-slate-500">기록 없음{data.genModel && <> — 설정값 <span className="font-mono text-slate-400">{data.genModel}</span> (실측 아님)</>}</div>
                : <div className="space-y-0.5">{models.map(m => (
                  <div key={m.modelId} className="text-xs flex items-center gap-2">
                    <span className={`font-mono break-all ${routeOfModel(m.modelId).gemma ? 'text-rose-300' : 'text-sky-200'}`}>{m.modelId}</span>
                    <span className="chip text-[10px] ml-auto">{m.count}건</span>
                  </div>))}</div>}
            </Tile>
            <Tile n="④" title="배지 — 데이터 소재 / 추론 라우팅" accent={storageOk ? 'var(--vpc)' : ROSE}>
              {!data ? <div className="text-xl font-bold">…</div> : (
                <div className="flex flex-col gap-1">
                  <span className="chip text-[11px] font-semibold" style={{ borderColor: storageOk ? 'var(--vpc)' : ROSE, color: storageOk ? '#fcd34d' : '#fda4af' }}>
                    {storageOk ? '저장: 서울 리전 (VPC 프라이빗 서브넷)' : '저장: 로컬 폴백 — VPC 아님'}</span>
                  {(infers.length ? infers : cfgInfer ? [cfgInfer] : ['추론: 기록 없음']).map(s => (
                    <span key={s} className="chip text-[11px] font-semibold"
                      style={{ borderColor: /us-west-2/.test(s) ? ROSE : 'var(--bedrock)', color: /us-west-2/.test(s) ? '#fda4af' : '#7dd3fc' }}>{s}
                      {!infers.length && cfgInfer && <span className="font-normal text-slate-500">(설정값)</span>}</span>
                  ))}
                  <span className="text-[10px] text-slate-500">서울은 global 프로파일만 지원 — 프롬프트는 국외로 이동할 수 있어 익명화가 필수 (§4-1)</span>
                </div>
              )}
            </Tile>
            <Tile n="⑤" title="Guardrails 차단 / 게이트 거부" accent={ROSE}>
              <div className="text-xl font-bold">{data ? <><span className="text-rose-300">{guardrailBlocked}</span><span className="text-slate-500 text-sm"> 건 · </span><span className="text-rose-300">{gateRejected}</span><span className="text-slate-500 text-sm"> 건</span></> : '…'}</div>
              <div className="text-[10px] text-slate-400 mt-1 max-h-14 overflow-y-auto">
                {blockedRows.length === 0 ? <span className="text-slate-500">차단·거부 없음</span>
                  : blockedRows.slice(0, 5).map(i => <div key={i.traceId} className="truncate"><span className="font-mono text-slate-500">{String(i.traceId).slice(0, 8)}</span> {isGate(i) ? '게이트 거부' : 'Guardrails'} — {blockReason(i)}</div>)}
              </div>
            </Tile>
          </div>
          <BoundaryFlow items={all} />
          <div className="flex items-center gap-3 mb-3 text-xs text-slate-500">
            <span>모든 값은 요청마다 기록된 트레이스와 플레인 헬스의 실측 합이다 — 하드코딩 없음(§3-2). 질문·프롬프트 원문은 클라우드에 저장하지 않는다(해시·길이만).</span>
            <span className="ml-auto whitespace-nowrap">현재 플레인: <b style={{ color: planeColor(data?.plane) }}>{data ? planeLabel(data.plane) : '…'}</b></span>
            <button className="chip hover:border-sky-500" onClick={reload}>새로고침</button>
          </div>
        </>
      )}
      {blockedOnly && (
        <div className="flex items-center gap-3 mb-3">
          <div className="panel px-5 py-3" style={{ borderTop: `2px solid ${ROSE}` }}>
            <span className="text-xs text-slate-400">Guardrails 차단 / 게이트 거부 (실측)</span>
            <div className="text-2xl font-bold text-rose-300">{data ? `${guardrailBlocked}건 / ${gateRejected}건` : '…'}</div>
          </div>
          <div className="text-xs text-slate-500 max-w-xl">Bedrock Guardrails 실물 판정(투자권유 토픽·PII·비속어)과 익명화 게이트 거부만 표시한다. 시뮬레이션 없음 — S2 화면의 "S5 차단 시연"으로 기록을 만든다.</div>
          <button className="chip ml-auto hover:border-sky-500" onClick={reload}>새로고침</button>
        </div>
      )}
      <div className="panel overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-xs text-slate-500 border-b border-slate-800">
            <th className="text-left p-3">traceId</th><th className="text-left p-3">시나리오</th>
            <th className="text-left p-3">질문(해시·길이)</th><th className="text-left p-3">위치</th>
            <th className="text-left p-3">모델 ID</th><th className="text-left p-3">경로(Tier)</th>
            <th className="text-left p-3">전달 필드</th><th className="text-left p-3">토큰 in/out</th>
            <th className="text-left p-3">Guardrails/게이트</th><th className="text-left p-3">응답</th></tr></thead>
          <tbody>{items.map((i: any) => {
            const mid = i.modelId ? String(i.modelId) : '';
            const ro = mid ? routeOfModel(mid) : null;
            const tier = i.tier != null ? `Tier ${i.tier}` : ro?.tier.split(' · ')[0];
            const route = i.route ? String(i.route) : ro ? (ro.gemma ? 'Gemma' : 'Claude') : '';
            const f = fieldsOf(i);
            return (
              <tr key={i.traceId} className="border-b border-slate-900 align-top">
                <td className="p-3 font-mono text-xs text-slate-500">{i.traceId}</td>
                <td className="p-3 text-xs">{i.scenario}</td>
                <td className="p-3 text-xs text-slate-400 font-mono whitespace-nowrap">{i.queryHash} · {i.queryLen}자</td>
                <td className="p-3 text-xs whitespace-nowrap" style={{ color: planeColor(i.plane) }}>{planeCell(i.plane)}</td>
                <td className={`p-3 text-xs font-mono break-all ${ro?.gemma ? 'text-rose-300' : 'text-sky-200'}`}>{mid || <span className="text-slate-600">미기록</span>}</td>
                <td className="p-3 text-xs whitespace-nowrap">{tier || route ? `${tier || ''}${tier && route ? ' · ' : ''}${route}` : <span className="text-slate-600">—</span>}</td>
                <td className="p-3 text-xs text-amber-300">{f.join(', ') || '—'}</td>
                <td className="p-3 text-xs whitespace-nowrap">{fmt(i.tokensIn)} / {fmt(i.tokensOut)}</td>
                <td className={`p-3 text-xs font-semibold ${i.blocked ? 'text-rose-400' : 'text-emerald-400'}`}>
                  {i.blocked ? `${isGate(i) ? '게이트 거부' : '차단'} (${blockReason(i)})` : (i.guardrailOut || 'NONE')}</td>
                <td className="p-3 text-xs">{i.cached ? <span className="chip text-[10px] text-amber-300 border-amber-700">캐시 응답</span> : <span className="text-slate-500">실시간</span>}</td>
              </tr>
            );
          })}</tbody>
        </table>
        {items.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">
          {blockedOnly ? '차단 기록 없음 — S2 화면의 "S5 차단 시연" 버튼으로 Guardrails 차단을 만들면 여기에 쌓입니다.'
            : '기록 없음 — S1/S2/S3/F7을 실행하면 여기에 계측이 쌓입니다.'}</div>}
      </div>
    </div>
  );
}
export { TwoPlane as BoundaryView };

/* ---------------- 임베드 (스튜디오/가이드북) ---------------- */
export function Frame({ src, note }: { src: string; note: string }) {
  return (
    <div className="h-[calc(100vh-120px)] flex flex-col">
      <div className="text-xs text-slate-500 mb-2">{note} · <a className="text-sky-400" href={src} target="_blank" rel="noopener">새 탭에서 열기 ↗</a></div>
      <iframe src={src} className="flex-1 w-full rounded-xl border border-slate-800 bg-white" />
    </div>
  );
}
