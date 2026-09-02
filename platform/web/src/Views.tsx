// 보조 뷰 모음: 대시보드 / 온톨로지 탐색기 / 에이전트 / Registry / Two-Plane / Guardrails 로그 / 임베드
import { useEffect, useState } from 'react';
import { auth, sock, WsEvent } from './lib';
import GraphView from './GraphView';

/* ---------------- 대시보드 ---------------- */
export function Dashboard({ go }: { go: (v: string) => void }) {
  const [d, setD] = useState<WsEvent | null>(null);
  const [traces, setTraces] = useState<WsEvent | null>(null);
  useEffect(() => {
    sock.request('hub', { idToken: auth.idToken }).then(setD).catch(() => {});
    sock.request('traces').then(setTraces).catch(() => {});
  }, []);
  const tile = (label: string, v: any, view?: string, accent = 'var(--cloud)') => (
    <button onClick={() => view && go(view)} className="panel p-4 text-left hover:border-slate-500"
      style={{ borderTop: `2px solid ${accent}` }}>
      <div className="text-2xl font-bold">{v ?? '…'}</div>
      <div className="text-xs text-slate-400 mt-1">{label}</div>
    </button>
  );
  return (
    <div>
      <div className="grid grid-cols-4 gap-3 mb-4">
        {tile('온톨로지 노드 (합성)', d?.graphNodes?.toLocaleString(), 'explore')}
        {tile('Registry 레코드 (승인)', d && `${d.registryApproved}/${d.registry}`, 'registry', 'var(--onprem)')}
        {tile('에이전트 (컨트롤룸)', d && `${d.agentsApproved}/${d.agents}`, 'agents')}
        {tile('디자인 자산 (스튜디오)', d?.assets, 'studio', '#a78bfa')}
      </div>
      <div className="grid grid-cols-4 gap-3 mb-6">
        {tile('개인신용정보 반출 (실측)', traces ? `${traces.piiOutboundTotal}건` : '…', 'twoplane',
          traces?.piiOutboundTotal === 0 ? 'var(--ok)' : '#f43f5e')}
        {tile('경계 통과 요청', traces?.items?.length, 'twoplane', 'var(--onprem)')}
        {tile('Guardrails 차단', traces?.items?.filter((i: any) => i.blocked).length, 'guardrails', '#f43f5e')}
        {tile('그래프 백엔드', d?.backend === 'neptune' ? 'Neptune' : 'Local(개발)', undefined, 'var(--cloud)')}
      </div>
      <div className="text-sm text-slate-400 mb-2 font-semibold">15분 시연 순서</div>
      <div className="grid grid-cols-5 gap-3">
        {[['s1', 'S1 규정 영향 분석', 'GraphRAG의 존재 이유 (4분)'],
          ['s2', 'S2 마이데이터 상담', '숫자는 LLM이 만들지 않는다 (4분)'],
          ['registry', 'S3 Registry', '자산 승인 거버넌스 (3분)'],
          ['twoplane', 'S4 Two-Plane 뷰', '경계 통과 실측 (2분)'],
          ['guardrails', 'S5 Guardrails', '차단 시연 (2분)']].map(([v, t, s]) => (
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

/* ---------------- Agent Registry ---------------- */
export function RegistryView() {
  const [recs, setRecs] = useState<any[]>([]);
  useEffect(() => { sock.request('registry').then(e => setRecs(e.records || [])).catch(() => {}); }, []);
  const color = (s: string) => s === 'APPROVED' ? 'text-emerald-400' : s === 'PENDING_APPROVAL' ? 'text-amber-400' : s === 'DEPRECATED' ? 'text-rose-400' : 'text-slate-400';
  return (
    <div>
      <div className="text-xs text-slate-500 mb-3">AgentCore Agent Registry (us-east-1) — 승인·폐기가 CloudTrail에 감사 기록된다. Consumer는 APPROVED만 사용한다.
        컴포넌트 Deprecate → 화면 생성 반전 시연(S3)은 Phase 4에서 구현 예정.</div>
      <div className="panel overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="text-xs text-slate-500 border-b border-slate-800">
            <th className="text-left p-3">이름</th><th className="text-left p-3">타입</th>
            <th className="text-left p-3">상태</th><th className="text-left p-3">설명</th>
            <th className="text-left p-3">갱신</th></tr></thead>
          <tbody>{recs.map(r => (
            <tr key={r.name} className="border-b border-slate-900">
              <td className="p-3 font-mono text-xs">{r.name}</td>
              <td className="p-3 text-xs">{r.type}</td>
              <td className={`p-3 text-xs font-semibold ${color(r.status)}`}>{r.status}</td>
              <td className="p-3 text-xs text-slate-400">{r.description}</td>
              <td className="p-3 text-xs text-slate-500">{r.updatedAt}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------- Two-Plane (S4) & Guardrails 로그 (S5) ---------------- */
export function TwoPlane({ blockedOnly }: { blockedOnly?: boolean }) {
  const [data, setData] = useState<WsEvent | null>(null);
  const reload = () => sock.request('traces').then(setData).catch(() => {});
  useEffect(() => { reload(); }, []);
  const items = (data?.items || []).filter((i: any) => !blockedOnly || i.blocked);
  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <div className="panel px-5 py-3" style={{ borderColor: 'var(--ok)' }}>
          <span className="text-xs text-slate-400">개인신용정보 반출 (경계 페이로드 실측 스캔)</span>
          <div className={`text-2xl font-bold ${data?.piiOutboundTotal === 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {data ? `${data.piiOutboundTotal}건` : '…'}</div>
        </div>
        <div className="text-xs text-slate-500 max-w-md">
          하드코딩이 아니다 — 매 요청의 경계 통과 페이로드를 정규식 스캔해 DynamoDB에 기록한 값의 합.
          정확 조회·계산·마스킹·감사 원문은 <b style={{ color: 'var(--onprem)' }}>고객 데이터 플레인(PII VPC — 인터넷 차단 격리 서브넷의 ECS·RDS)</b>이
          수행한다. 원칙: PII는 VPC 안에 두되, <b>Bedrock 등 모델 호출로 나가는 페이로드는 익명화가 필수</b> — 이 표가 그 실측 기록이다.</div>
        <button className="chip ml-auto hover:border-sky-500" onClick={reload}>새로고침</button>
      </div>
      <div className="panel overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="text-xs text-slate-500 border-b border-slate-800">
            <th className="text-left p-3">traceId</th><th className="text-left p-3">시나리오</th>
            <th className="text-left p-3">질문</th><th className="text-left p-3">마스킹 필드</th>
            <th className="text-left p-3">PII 반출</th><th className="text-left p-3">경계 토큰(추정)</th>
            <th className="text-left p-3">Guardrails</th></tr></thead>
          <tbody>{items.map((i: any) => (
            <tr key={i.connId} className="border-b border-slate-900">
              <td className="p-3 font-mono text-xs text-slate-500">{i.traceId}</td>
              <td className="p-3 text-xs">{i.scenario}</td>
              <td className="p-3 text-xs text-slate-300 max-w-[260px] truncate">{i.query}</td>
              <td className="p-3 text-xs text-amber-300">{(i.maskedFields || []).join(', ') || '—'}</td>
              <td className={`p-3 text-xs font-semibold ${Number(i.piiOutbound) === 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{i.piiOutbound}건</td>
              <td className="p-3 text-xs">{i.tokensOut}</td>
              <td className={`p-3 text-xs font-semibold ${i.blocked ? 'text-rose-400' : 'text-emerald-400'}`}>
                {i.blocked ? `차단 (${(i.topics || []).join(',')})` : (i.guardrailOut || 'NONE')}</td>
            </tr>
          ))}</tbody>
        </table>
        {items.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">기록 없음 — S1/S2를 실행하면 여기에 계측이 쌓입니다.</div>}
      </div>
    </div>
  );
}

/* ---------------- 임베드 (스튜디오/가이드북) ---------------- */
export function Frame({ src, note }: { src: string; note: string }) {
  return (
    <div className="h-[calc(100vh-120px)] flex flex-col">
      <div className="text-xs text-slate-500 mb-2">{note} · <a className="text-sky-400" href={src} target="_blank" rel="noopener">새 탭에서 열기 ↗</a></div>
      <iframe src={src} className="flex-1 w-full rounded-xl border border-slate-800 bg-white" />
    </div>
  );
}
