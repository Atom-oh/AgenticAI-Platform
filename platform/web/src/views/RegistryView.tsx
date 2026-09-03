// F4 Agent Registry — 카탈로그 · 승인 워크플로우 · 버전 체인 · 하이브리드 검색 · S3 반전 시연 패널 (SPEC §6.1 화면 6)
// Registry 는 클라우드 메타데이터(--cloud). Consumer API 는 APPROVED 만 돌려준다 — 이 제약이 S3 의 전부다.
import { useCallback, useEffect, useState } from 'react';
import { sock, WsEvent } from '../lib';

type Rec = {
  name: string; recordVersion: string; recordType: string; subtype?: string; status: string;
  description?: string; owner?: string; tags?: string[]; payload?: Record<string, any>;
  createdAt?: number; updatedAt?: number; updatedBy?: string; allowedTargets?: string[]; hasEmbedding?: boolean;
};
type Audit = { actor: string; from: string; to: string; reason: string; ts: number; transition?: string; forced?: boolean };
type ChainItem = { recordVersion: string; status: string; supersededBy?: string | null; inChain: boolean; current: boolean };
type Hit = {
  record: Rec; score: number; match: 'hybrid' | 'keyword' | 'dense';
  keywordRank?: number | null; denseRank?: number | null; keywordScore?: number | null; denseScore?: number | null;
};
type Detail = { record: Rec; audit: Audit[]; versionChain: ChainItem[] };

const TYPES = ['ALL', 'MCP', 'AGENT', 'SKILL', 'CUSTOM'];
const STATUSES = ['ALL', 'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'DEPRECATED'];
const STATUS_KO: Record<string, string> = {
  DRAFT: '초안', PENDING_APPROVAL: '승인 대기', APPROVED: '승인', REJECTED: '반려', DEPRECATED: '폐기',
};
const TRANSITION_KO: Record<string, string> = {
  PENDING_APPROVAL: '승인 요청', APPROVED: '승인', REJECTED: '반려', DEPRECATED: '폐기(Deprecate)', DRAFT: 'DRAFT 복귀',
};
const REASON_REQUIRED = ['REJECTED', 'DEPRECATED'];
const SEARCH_PRESETS = ['여신 심사 결과 표', '규정 개정 영향 분석', '버튼 컴포넌트', '퍼블리싱 규약 스킬', '마이데이터 우대금리'];

const statusColor = (s: string) =>
  s === 'APPROVED' ? 'text-emerald-600' : s === 'PENDING_APPROVAL' ? 'text-amber-700'
    : s === 'DEPRECATED' ? 'text-[#E90061]' : s === 'REJECTED' ? 'text-[#E90061]' : 'text-slate-400';
const statusBorder = (s: string) =>
  s === 'APPROVED' ? 'border-emerald-700' : s === 'PENDING_APPROVAL' ? 'border-amber-400'
    : s === 'DEPRECATED' || s === 'REJECTED' ? 'border-rose-800' : 'border-slate-300';
const fmtTs = (ms?: number) => (ms ? new Date(ms).toLocaleString('ko-KR', { hour12: false }) : '—');
const recKey = (r: { name: string; recordVersion: string }) => `${r.name}@${r.recordVersion}`;
const errOf = (e: WsEvent | null | undefined) => (e && (e.type === 'error' || e.ok === false)) ? (e.error || e.message || '오류') : '';

function StatusChip({ s }: { s: string }) {
  return <span className={`chip text-[10px] font-semibold ${statusColor(s)} ${statusBorder(s)}`}>{STATUS_KO[s] || s}</span>;
}

/* ---------------- S3 시연 패널 (상단 고정) ---------------- */
function DemoPanel({ records, consumer, busy, onTransition, onRefresh }: {
  records: Rec[]; consumer: Rec[] | null; busy: string;
  onTransition: (name: string, version: string, to: string, reason: string) => Promise<void>;
  onRefresh: () => void;
}) {
  const v2 = records.find(r => r.name === 'Button' && r.recordVersion === 'v2');
  const v3 = records.find(r => r.name === 'Button' && r.recordVersion === 'v3');
  const canDeprecate = v2?.status === 'APPROVED';
  const canApprove = v3?.status === 'PENDING_APPROVAL';
  const reversed = v2?.status === 'DEPRECATED' && v3?.status === 'APPROVED';
  return (
    <div className="panel p-4 mb-4" style={{ borderTop: '2px solid var(--cloud)' }}>
      <div className="flex items-center gap-3 mb-3">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--cloud)' }} />
        <b className="text-sm">S3 시연 패널 — 승인 상태가 생성 결과를 바꾼다</b>
        <span className="text-xs text-slate-500">Registry(클라우드 · 메타데이터만) → Consumer API(APPROVED 만) → 화면 생성 에이전트</span>
        <button className="chip ml-auto hover:border-teal-500" onClick={onRefresh}>새로고침</button>
      </div>
      <div className="grid grid-cols-[1fr_1fr_1.4fr] gap-3">
        <div className="rounded-lg border border-slate-200 p-3">
          <div className="text-xs text-slate-500 mb-1">현재 상태</div>
          <div className="text-sm flex items-center gap-2 mb-1"><span className="font-mono">Button v2</span>{v2 ? <StatusChip s={v2.status} /> : <span className="text-slate-400 text-xs">로딩…</span>}</div>
          <div className="text-sm flex items-center gap-2"><span className="font-mono">Button v3</span>{v3 ? <StatusChip s={v3.status} /> : <span className="text-slate-400 text-xs">로딩…</span>}</div>
          <div className="text-[11px] text-slate-500 mt-2">{reversed
            ? <span className="text-emerald-600">반전 완료 — 에이전트는 이제 Button v3 만 본다</span>
            : '기준선: v2 승인 · v3 승인 대기 (리셋 버튼으로 복원)'}</div>
        </div>
        <div className="rounded-lg border border-slate-200 p-3 flex flex-col gap-2">
          <div className="text-xs text-slate-500">원클릭 반전 (사유가 감사 이벤트에 남는다)</div>
          <button disabled={!canDeprecate || !!busy}
            onClick={() => onTransition('Button', 'v2', 'DEPRECATED', 'v3 승인')}
            className="px-3 py-2 rounded-lg text-sm font-semibold bg-rose-500/90 hover:bg-rose-400 text-white disabled:opacity-30 text-left">
            {busy === 'Button@v2' ? '전이 중…' : '① Button v2 → DEPRECATED'} <span className="font-normal text-xs">(사유: v3 승인)</span>
          </button>
          <button disabled={!canApprove || !!busy}
            onClick={() => onTransition('Button', 'v3', 'APPROVED', 'S3 시연 — v2 대체')}
            className="px-3 py-2 rounded-lg text-sm font-semibold bg-emerald-500/90 hover:bg-emerald-400 text-white disabled:opacity-30 text-left">
            {busy === 'Button@v3' ? '전이 중…' : '② Button v3 → APPROVED'}
          </button>
          <a href="#/screengen" className="chip justify-center hover:border-teal-500 text-teal-700 mt-auto">③ 재생성하러 가기 → 화면 생성</a>
        </div>
        <div className="rounded-lg border border-slate-200 p-3">
          <div className="text-xs text-slate-500 mb-1 flex items-center gap-2">
            Consumer API 미리보기 — <span className="text-emerald-600">에이전트가 보는 것</span> (COMPONENT · APPROVED 만)
            {consumer && <span className="chip text-[10px] ml-auto">{consumer.length}건</span>}
          </div>
          {!consumer ? <div className="text-xs text-slate-400">로딩…</div> : (
            <div className="flex flex-wrap gap-1 max-h-[92px] overflow-y-auto">
              {consumer.map(r => (
                <span key={recKey(r)} className={`chip text-[10px] font-mono ${r.name === 'Button' ? 'text-emerald-700 border-emerald-700' : ''}`}>
                  {r.name} {r.recordVersion}</span>
              ))}
            </div>
          )}
          <div className="text-[11px] text-slate-500 mt-2">registry.api.list_approved(subtype="COMPONENT") 의 결과 그대로 — 벡터 검색 아님, 정확 조회.</div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- 상세 드로어 ---------------- */
function Drawer({ detail, busy, err, onClose, onTransition, onOpenVersion }: {
  detail: Detail; busy: string; err: string; onClose: () => void;
  onTransition: (name: string, version: string, to: string, reason: string) => Promise<void>;
  onOpenVersion: (name: string, version: string) => void;
}) {
  const r = detail.record;
  const [reason, setReason] = useState('');
  useEffect(() => { setReason(''); }, [r.name, r.recordVersion, r.status]);
  const allowed = r.allowedTargets || [];
  const targets = STATUSES.filter(s => s !== 'ALL' && s !== r.status);
  const payload = r.payload || {};
  const { propsSchema, ...restPayload } = payload;
  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-30" onClick={onClose} />
      <aside className="fixed top-0 right-0 h-screen w-[560px] max-w-[95vw] z-40 overflow-y-auto border-l border-slate-200 p-5"
        style={{ background: 'var(--panel)' }} role="dialog" aria-label={`${r.name} ${r.recordVersion} 상세`}>
        <div className="flex items-start gap-3 mb-3">
          <div>
            <div className="text-lg font-bold font-mono">{r.name} <span className="text-teal-700">{r.recordVersion}</span></div>
            <div className="text-xs text-slate-500 mt-0.5">{r.recordType}{r.subtype ? ` / ${r.subtype}` : ''} · 소유 {r.owner || '—'} · 갱신 {fmtTs(r.updatedAt)} · by {r.updatedBy || '—'}</div>
          </div>
          <StatusChip s={r.status} />
          <button className="chip ml-auto hover:border-slate-400" onClick={onClose}>닫기 ✕</button>
        </div>
        <p className="text-sm text-slate-700 whitespace-pre-wrap mb-3">{r.description || <span className="text-slate-400">설명 없음</span>}</p>
        {r.tags && r.tags.length > 0 && <div className="mb-4 flex flex-wrap gap-1">{r.tags.map(t => <span key={t} className="chip text-[10px]">{t}</span>)}</div>}

        {/* 상태 전이 */}
        <section className="rounded-lg border border-slate-200 p-3 mb-4">
          <div className="text-xs text-slate-500 mb-2">상태 전이 — 현재 <b className={statusColor(r.status)}>{STATUS_KO[r.status]}</b>에서 가능한 전이만 활성화 (그 외는 서버가 400 으로 거부)</div>
          <input className="w-full mb-2 px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-sm"
            placeholder="사유 (반려·폐기는 필수 — 감사 이벤트에 기록)" value={reason} onChange={e => setReason(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            {targets.map(t => {
              const ok = allowed.includes(t);
              const needReason = REASON_REQUIRED.includes(t) && !reason.trim();
              const disabled = !ok || needReason || !!busy;
              return (
                <button key={t} disabled={disabled} title={!ok ? `${r.status} → ${t} 는 허용되지 않는 전이` : needReason ? '사유 필수' : ''}
                  onClick={() => onTransition(r.name, r.recordVersion, t, reason)}
                  className={`chip text-xs ${ok ? `${statusColor(t)} ${statusBorder(t)} hover:brightness-125` : 'text-slate-400 line-through'} disabled:cursor-not-allowed`}>
                  → {TRANSITION_KO[t]}{ok && needReason && <span className="text-[10px] text-amber-700 no-underline">(사유 필수)</span>}
                </button>
              );
            })}
            {allowed.length === 0 && <span className="text-xs text-slate-500">종료 상태 — 더 이상 전이할 수 없습니다.</span>}
          </div>
          {busy === recKey(r) && <div className="text-xs text-teal-700 mt-2">전이 중…</div>}
          {err && <div className="text-xs text-[#E90061] mt-2">{err}</div>}
        </section>

        {/* 버전 체인 */}
        <section className="mb-4">
          <div className="text-xs text-slate-500 mb-2">버전 체인 (payload.supersededBy 양방향 추적)</div>
          <div className="flex items-center flex-wrap gap-1">
            {detail.versionChain.map((c, i) => (
              <span key={c.recordVersion} className="flex items-center gap-1">
                {i > 0 && <span className="text-slate-400 text-xs">{c.inChain ? '→' : '·'}</span>}
                <button onClick={() => onOpenVersion(r.name, c.recordVersion)}
                  className={`chip text-xs font-mono ${statusBorder(c.status)} ${c.current ? 'bg-slate-800' : 'hover:border-slate-400'}`}>
                  {c.recordVersion} <span className={`text-[10px] ${statusColor(c.status)}`}>{STATUS_KO[c.status] || c.status}</span>
                </button>
              </span>
            ))}
            {detail.versionChain.length === 0 && <span className="text-xs text-slate-400">버전 정보 없음</span>}
          </div>
        </section>

        {/* payload */}
        <section className="mb-4">
          <div className="text-xs text-slate-500 mb-1">payload{propsSchema ? '.propsSchema (화면 생성 에이전트가 받는 정확한 스키마)' : ''}</div>
          {propsSchema && <pre className="bg-slate-50 rounded p-2 text-[11px] overflow-x-auto max-h-72">{JSON.stringify(propsSchema, null, 2)}</pre>}
          {Object.keys(restPayload).length > 0 && (
            <details className="mt-1"><summary className="cursor-pointer text-xs text-slate-400">payload 나머지 필드 ({Object.keys(restPayload).join(', ')})</summary>
              <pre className="bg-slate-50 rounded p-2 text-[11px] overflow-x-auto max-h-60 mt-1">{JSON.stringify(restPayload, null, 2)}</pre></details>
          )}
          {!propsSchema && Object.keys(restPayload).length === 0 && <div className="text-xs text-slate-400">payload 없음</div>}
        </section>

        {/* 감사 이벤트 */}
        <section>
          <div className="text-xs text-slate-500 mb-1">감사 이벤트 ({detail.audit.length}) — 최신순 · DynamoDB audit# 항목</div>
          <div className="space-y-1">
            {detail.audit.map((a, i) => (
              <div key={i} className="text-xs rounded border border-slate-200 p-2 flex flex-wrap gap-x-2 gap-y-0.5">
                <span className="text-slate-500 font-mono">{fmtTs(a.ts)}</span>
                <span><span className={statusColor(a.from)}>{a.from ? STATUS_KO[a.from] || a.from : '(생성)'}</span> → <span className={statusColor(a.to)}>{STATUS_KO[a.to] || a.to}</span></span>
                <span className="text-slate-400">{a.transition}{a.forced && <span className="text-amber-700 ml-1">강제 기록</span>}</span>
                <span className="text-slate-500">by {a.actor}</span>
                {a.reason && <span className="w-full text-slate-700">사유: {a.reason}</span>}
              </div>
            ))}
            {detail.audit.length === 0 && <div className="text-xs text-slate-400">감사 이벤트 없음</div>}
          </div>
        </section>
      </aside>
    </>
  );
}

/* ---------------- 신규 레코드 폼 ---------------- */
function CreateForm({ onCreated }: { onCreated: (r: Rec) => void }) {
  const [f, setF] = useState({ name: '', recordVersion: 'v1', recordType: 'AGENT', subtype: '', description: '', owner: '', tags: '' });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF(s => ({ ...s, [k]: e.target.value }));
  const submit = async () => {
    setBusy(true); setMsg('');
    try {
      const e = await sock.request('registry_create', { record: { ...f, tags: f.tags.split(',').map(t => t.trim()).filter(Boolean), payload: {} } });
      const er = errOf(e);
      if (er) setMsg(er); else { setMsg(`등록됨 (DRAFT): ${e.record.name} ${e.record.recordVersion}`); onCreated(e.record); }
    } catch (ex: any) { setMsg(ex.message); }
    setBusy(false);
  };
  const cls = 'px-2 py-1.5 rounded-lg bg-white border border-slate-300 text-xs';
  return (
    <details className="panel p-3 mt-4">
      <summary className="cursor-pointer text-sm font-semibold">새 레코드 등록 (DRAFT 로 시작 → 승인 요청 → 승인)</summary>
      <div className="grid grid-cols-6 gap-2 mt-3">
        <input className={cls} placeholder="name (영문/숫자/._-)" value={f.name} onChange={set('name')} />
        <input className={cls} placeholder="recordVersion (v1)" value={f.recordVersion} onChange={set('recordVersion')} />
        <select className={cls} value={f.recordType} onChange={set('recordType')}>{['MCP', 'AGENT', 'SKILL', 'CUSTOM'].map(t => <option key={t}>{t}</option>)}</select>
        <input className={cls} placeholder="subtype (CUSTOM 은 필수, 예: COMPONENT)" value={f.subtype} onChange={set('subtype')} />
        <input className={cls} placeholder="owner" value={f.owner} onChange={set('owner')} />
        <input className={cls} placeholder="tags (쉼표 구분)" value={f.tags} onChange={set('tags')} />
        <input className={`${cls} col-span-5`} placeholder="description (한국어)" value={f.description} onChange={set('description')} />
        <button onClick={submit} disabled={busy || !f.name}
          className="px-3 py-1.5 rounded-lg bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-xs disabled:opacity-40">{busy ? '등록 중…' : '등록'}</button>
      </div>
      {msg && <div className={`text-xs mt-2 ${msg.startsWith('등록됨') ? 'text-emerald-600' : 'text-[#E90061]'}`}>{msg}</div>}
      <div className="text-[11px] text-slate-500 mt-2">payload 편집 UI 는 미구현 — 등록 후 payload 는 비어 있다. 컴포넌트 propsSchema 는 시드 기준선에서만 제공된다.</div>
    </details>
  );
}

/* ---------------- 메인 뷰 ---------------- */
export default function RegistryView() {
  const [type, setType] = useState('ALL');
  const [status, setStatus] = useState('ALL');
  const [records, setRecords] = useState<Rec[]>([]);
  const [allRecords, setAllRecords] = useState<Rec[]>([]); // 필터 무관 전체 (S3 패널용)
  const [counts, setCounts] = useState<any>(null);
  const [meta, setMeta] = useState<{ backend?: string; embeddingsEnabled?: boolean; bootstrapped?: any }>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');
  const [search, setSearch] = useState<WsEvent | null>(null);
  const [searching, setSearching] = useState(false);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailErr, setDetailErr] = useState('');
  const [busy, setBusy] = useState('');
  const [consumer, setConsumer] = useState<Rec[] | null>(null);
  const [surfaces, setSurfaces] = useState<WsEvent | null>(null);
  const [toast, setToast] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const e = await sock.request('registry_list', { type: type === 'ALL' ? undefined : type, status: status === 'ALL' ? undefined : status });
      const er = errOf(e); if (er) throw new Error(er);
      setRecords(e.records || []); setCounts(e.counts);
      setMeta({ backend: e.backend, embeddingsEnabled: e.embeddingsEnabled, bootstrapped: e.bootstrapped });
      if (type === 'ALL' && status === 'ALL') setAllRecords(e.records || []);
      else { const a = await sock.request('registry_list', {}); setAllRecords(a.records || []); }
    } catch (ex: any) { setErr(ex.message); }
    setLoading(false);
  }, [type, status]);
  const loadConsumer = useCallback(() => {
    sock.request('registry_consumer', { subtype: 'COMPONENT' }).then(e => setConsumer(e.records || [])).catch(() => setConsumer([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    loadConsumer();
    sock.request('surfaces').then(setSurfaces).catch(() => setSurfaces({ type: 'surfaces', records: [], error: '조회 실패' }));
  }, [loadConsumer]);

  const open = async (name: string, version: string) => {
    setDetailErr('');
    try {
      const e = await sock.request('registry_get', { name, version });
      const er = errOf(e); if (er) { setErr(er); return; }
      setDetail({ record: e.record, audit: e.audit || [], versionChain: e.versionChain || [] });
    } catch (ex: any) { setErr(ex.message); }
  };

  const doTransition = async (name: string, version: string, to: string, reason: string) => {
    const k = `${name}@${version}`;
    setBusy(k); setDetailErr(''); setToast('');
    try {
      const e = await sock.request('registry_transition', { name, version, to, reason });
      const er = errOf(e);
      if (er) { setDetailErr(`${e.code ? e.code + ' · ' : ''}${er}`); if (!detail) setToast(`전이 거부: ${er}`); }
      else {
        setToast(`${name} ${version}: ${STATUS_KO[e.audit.from]} → ${STATUS_KO[e.audit.to]} (감사 기록됨)`);
        if (detail && recKey(detail.record) === k) setDetail({ record: e.record, audit: e.auditTrail || [], versionChain: detail.versionChain.map(c => c.recordVersion === version ? { ...c, status: e.record.status } : c) });
        await load(); loadConsumer();
        if (search) runSearch(search.q);
      }
    } catch (ex: any) { setDetailErr(ex.message); }
    setBusy('');
  };

  const runSearch = async (qq?: string) => {
    const query = (qq ?? q).trim();
    if (!query) { setSearch(null); return; }
    if (qq !== undefined) setQ(qq);
    setSearching(true);
    try {
      const e = await sock.request('registry_search', { q: query, type: type === 'ALL' ? undefined : type });
      const er = errOf(e); if (er) throw new Error(er);
      setSearch(e);
    } catch (ex: any) { setErr(ex.message); }
    setSearching(false);
  };

  const hits: Hit[] = search?.hits || [];
  const rows: Rec[] = search ? hits.map(h => h.record) : records;
  const hitOf = (r: Rec) => hits.find(h => recKey(h.record) === recKey(r));

  return (
    <div>
      <div className="text-xs text-slate-500 mb-3">
        사내 AI 자산(MCP · AGENT · SKILL · CUSTOM/COMPONENT)의 버전·승인 관리. 전이는 DRAFT → 승인 대기 → 승인 → 폐기 (반려 → DRAFT 복귀)만 허용되고 매 전이가 감사 이벤트로 남는다.
        저장소: <span style={{ color: 'var(--cloud)' }}>{meta.backend === 'dynamodb' ? 'DynamoDB (REGISTRY_TABLE)' : meta.backend === 'memory' ? '인메모리 (개발용 — 영속 아님)' : '…'}</span>
        {meta.bootstrapped && <span className="text-amber-700 ml-2">· 빈 레지스트리에 기준선 {meta.bootstrapped.created}건을 시드했습니다</span>}
      </div>

      <DemoPanel records={allRecords} consumer={consumer} busy={busy} onTransition={doTransition}
        onRefresh={() => { load(); loadConsumer(); }} />
      {toast && <div className="mb-3 text-xs text-emerald-700">{toast}</div>}

      {/* 필터 + 검색 */}
      <div className="panel p-3 mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500">타입</span>
        {TYPES.map(t => (
          <button key={t} onClick={() => setType(t)} className={`chip text-xs ${type === t ? 'border-teal-500 text-teal-700' : 'hover:border-slate-400'}`}>
            {t}{counts?.byType && t !== 'ALL' ? <span className="text-slate-500">{counts.byType[t] ?? 0}</span> : t === 'ALL' && counts ? <span className="text-slate-500">{counts.total}</span> : null}
          </button>
        ))}
        <span className="text-xs text-slate-500 ml-3">상태</span>
        {STATUSES.map(s => (
          <button key={s} onClick={() => setStatus(s)} className={`chip text-xs ${status === s ? 'border-teal-500 text-teal-700' : `hover:border-slate-400 ${s !== 'ALL' ? statusColor(s) : ''}`}`}>
            {s === 'ALL' ? '전체' : STATUS_KO[s]}{counts?.byStatus && s !== 'ALL' && <span className="text-slate-500">{counts.byStatus[s] ?? 0}</span>}
          </button>
        ))}
        <div className="w-full flex items-center gap-2 mt-1">
          <input className="flex-1 px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-sm"
            placeholder="하이브리드 검색 — 키워드(한국어 부분일치) + 임베딩(Titan) → RRF 융합" value={q}
            onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && runSearch()} />
          <button onClick={() => runSearch()} disabled={searching}
            className="px-4 py-1.5 rounded-lg bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm disabled:opacity-40">{searching ? '검색 중…' : '검색'}</button>
          {search && <button className="chip hover:border-slate-400" onClick={() => { setSearch(null); setQ(''); }}>목록으로</button>}
        </div>
        <div className="w-full flex flex-wrap items-center gap-1 text-xs">
          <span className="text-slate-500">프리셋</span>
          {SEARCH_PRESETS.map(p => <button key={p} className="chip text-[11px] hover:border-teal-500" onClick={() => runSearch(p)}>{p}</button>)}
          <span className="ml-auto text-slate-500">
            {meta.embeddingsEnabled === false && <span className="text-amber-700">임베딩 미사용 (REGISTRY_EMBED=0) — 키워드만</span>}
            {search && <> · 결과 {hits.length}건 · {search.dense ? <span className="text-emerald-600">키워드 + 임베딩 융합</span> : <span className="text-amber-700">{search.note || '키워드만'}</span>}</>}
          </span>
        </div>
      </div>
      {err && <div className="text-[#E90061] text-sm mb-3">{err}</div>}

      {/* 테이블 */}
      <div className="panel overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="text-xs text-slate-500 border-b border-slate-200">
            {search && <th className="text-left p-3">점수 · 매칭</th>}
            <th className="text-left p-3">이름</th><th className="text-left p-3">버전</th>
            <th className="text-left p-3">타입 / 서브타입</th><th className="text-left p-3">상태</th>
            <th className="text-left p-3">소유</th><th className="text-left p-3">설명</th><th className="text-left p-3">갱신</th></tr></thead>
          <tbody>{rows.map(r => {
            const h = search ? hitOf(r) : undefined;
            return (
              <tr key={recKey(r)} className={`border-b border-slate-100 hover:bg-slate-100 cursor-pointer ${detail && recKey(detail.record) === recKey(r) ? 'bg-white' : ''}`}
                onClick={() => open(r.name, r.recordVersion)}>
                {search && <td className="p-3 text-xs whitespace-nowrap">
                  <span className="font-mono text-slate-700">{h?.score.toFixed(4)}</span>
                  <span className={`chip text-[10px] ml-1 ${h?.match === 'hybrid' ? 'text-emerald-700 border-emerald-300' : h?.match === 'dense' ? 'text-teal-700 border-teal-300' : 'text-slate-700'}`}
                    title={`키워드 순위 ${h?.keywordRank ?? '—'} (${h?.keywordScore ?? '—'}) · 임베딩 순위 ${h?.denseRank ?? '—'} (cos ${h?.denseScore ?? '—'})`}>
                    {h?.match === 'hybrid' ? '키워드+임베딩' : h?.match === 'dense' ? '임베딩' : '키워드'}</span></td>}
                <td className="p-3 font-mono text-xs">{r.name}</td>
                <td className="p-3 font-mono text-xs text-teal-700">{r.recordVersion}</td>
                <td className="p-3 text-xs">{r.recordType}{r.subtype && <span className="text-slate-500"> / {r.subtype}</span>}</td>
                <td className={`p-3 text-xs font-semibold ${statusColor(r.status)}`}>{STATUS_KO[r.status] || r.status}</td>
                <td className="p-3 text-xs text-slate-400">{r.owner || '—'}</td>
                <td className="p-3 text-xs text-slate-400 max-w-[360px] truncate" title={r.description}>{r.description}</td>
                <td className="p-3 text-xs text-slate-500 whitespace-nowrap">{fmtTs(r.updatedAt)}</td>
              </tr>
            );
          })}</tbody>
        </table>
        {loading && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">불러오는 중…</div>}
        {!loading && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">{search ? '검색 결과 없음' : '레코드 없음'}</div>}
      </div>

      <CreateForm onCreated={() => load()} />

      {/* AgentCore Agent Registry 서피스 (읽기 전용) */}
      <div className="panel p-4 mt-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--cloud)' }} />
          <b className="text-sm">AgentCore Agent Registry (us-east-1) 서피스 — 읽기 전용</b>
          <span className="text-xs text-slate-500">플랫폼 서피스(컨트롤룸·스튜디오 등) 등록 레코드. 승인·폐기는 CloudTrail 에 감사된다. 위 F4 레지스트리(서울 · DynamoDB)와는 별개 시스템.</span>
        </div>
        {!surfaces ? <div className="text-xs text-slate-400">로딩…</div> : surfaces.error ? <div className="text-xs text-[#E90061]">조회 실패: {surfaces.error}</div> : (
          <table className="w-full text-sm">
            <thead><tr className="text-xs text-slate-500 border-b border-slate-200">
              <th className="text-left p-2">이름</th><th className="text-left p-2">타입</th><th className="text-left p-2">상태</th><th className="text-left p-2">설명</th><th className="text-left p-2">갱신</th></tr></thead>
            <tbody>{(surfaces.records || []).map((s: any) => (
              <tr key={s.name} className="border-b border-slate-100">
                <td className="p-2 font-mono text-xs">{s.name}</td><td className="p-2 text-xs">{s.type}</td>
                <td className={`p-2 text-xs font-semibold ${statusColor(s.status)}`}>{s.status}</td>
                <td className="p-2 text-xs text-slate-400">{s.description}</td><td className="p-2 text-xs text-slate-500">{s.updatedAt}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {surfaces && !surfaces.error && (surfaces.records || []).length === 0 && <div className="text-xs text-slate-500 p-2">등록된 서피스 레코드가 없거나 조회 권한이 없습니다.</div>}
      </div>

      {detail && <Drawer detail={detail} busy={busy} err={detailErr} onClose={() => setDetail(null)}
        onTransition={doTransition} onOpenVersion={open} />}
    </div>
  );
}
