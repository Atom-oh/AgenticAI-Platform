// UX Asset Portal (SPEC v2 §8-2) — 좌측 7 카테고리 · 자산 카드(ID · Status · Version · Owner · Related) · 상세(Version History ·
// Related 분해 · Publish / Sync · 영향 분석). Related 카운트와 영향 범위는 전부 그래프 순회 결과다 (§12.8 하드코딩 금지).
// 색 규칙(§8-4): 온톨로지(VPC 내부 Neptune) 데이터 = var(--vpc) 앰버, Registry(클라우드 메타데이터) = var(--bedrock) 시안.
// Registry 데이터가 보이는 곳에는 'Tier 0/1 전용' 배지(§11-4). 브라우저 스토리지는 쓰지 않는다 (§12.12).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import GraphView, { GEdge, GNode } from '../GraphView';
import { sock, WsEvent } from '../lib';

type Status = 'APPROVED' | 'DRAFT' | 'DEPRECATED';
type Related = Record<string, number>;
type ScreenMeta = { metaId: string; purpose?: string | null; entryCondition?: string | null; prevScreens: string[]; nextScreens: string[] };
type Card = {
  id: string; label: string; category: string | null; name: string; status: Status; rawStatus?: string | null;
  version?: string | null; owner?: string | null; brief?: string; related: Related; computedBy: string;
  termCategory?: string | null; meta?: ScreenMeta | null;
};
type ChainItem = { id: string; label: string; name: string; version?: string | null; status: string; rawStatus?: string | null; current: boolean };
type NeighborGroup = { rel: string; direction: 'in' | 'out'; count: number; nodes: GNode[] };
type Mapping = { key: string; asset: string; recordType: string; subtype?: string | null; origin: string; current: string; deviation: boolean; note?: string | null };
type RegistryInfo = { available: boolean; record: any | null; name?: string; recordVersion?: string; tier: string; backend?: string; error?: string; note?: string };
type Detail = Card & {
  props: Record<string, any>; versionChain: ChainItem[]; neighbors: NeighborGroup[]; impactSupported: boolean;
  publishable: boolean; publishTarget: { recordType: string; subtype: string } | null; mapping: Mapping | null;
  registry: RegistryInfo | null; backend: string; alsoIn?: string[]; elapsedMs?: number;
};
type ScreenNode = GNode & { channel?: string | null };
type Impact = {
  id: string; label: string; name: string; counts: Record<string, number>; screens: ScreenNode[]; patterns: GNode[];
  policyRules: GNode[]; departments: GNode[]; products: GNode[]; procedures: GNode[]; components: GNode[]; regulations: GNode[];
  graph: { nodes: GNode[]; edges: GEdge[]; truncated: { nodes: number; edges: number } }; pathEdges: number; traversal: string;
  backend: string; elapsedMs: number;
};
type MapRow = Mapping & { records: { name: string; recordVersion: string; status: string | null; recordType?: string | null; subtype?: string | null; found: boolean; payloadFlags: Record<string, any> }[] };

const CATS: { id: string; label: string; sub: string }[] = [
  { id: 'Foundation', label: 'Foundation', sub: '공통 용어 (대체 표시)' },
  { id: 'Components', label: 'Components', sub: 'Component Library' },
  { id: 'Patterns', label: 'Patterns', sub: 'Pattern Library' },
  { id: 'Screens', label: 'Screens', sub: 'Screen + Metadata' },
  { id: 'Procedures', label: 'Procedures', sub: 'Procedure Library' },
  { id: 'Policies', label: 'Policies', sub: 'Policy Rule' },
  { id: 'UXWriting', label: 'UX Writing', sub: 'UX Dictionary' },
];
const PRESETS = ['CMP-Button-v2', 'CMP-Input-v3', 'PAT-001', 'POL-000', 'SCR-001', 'PRC-000'];
const DEFAULT_ID = 'CMP-Button-v2';
const LABEL_KO: Record<string, string> = {
  Component: '컴포넌트', Pattern: '패턴', Screen: '화면', Procedure: '절차', PolicyRule: '정책규칙', UXTerm: 'UX 용어',
  ScreenMeta: '화면 메타', Product: '상품', Department: '부서', Regulation: '규정', Document: '문서', Template: '템플릿', Condition: '조건',
};
const REL_LABEL: Record<string, string> = {
  Screen: 'Screens', Pattern: 'Patterns', PolicyRule: 'Policies', Component: 'Components', Product: 'Products',
  Department: 'Departments', Procedure: 'Procedures', UXTerm: 'Terms', ScreenMeta: 'Meta', Regulation: 'Regulations',
  Template: 'Templates', Document: 'Documents',
};
const REL_ORDER = ['Screen', 'Pattern', 'PolicyRule', 'Component', 'Product', 'Department', 'Procedure', 'Regulation', 'UXTerm', 'ScreenMeta'];
const STATUS_KO: Record<string, string> = { APPROVED: '승인', DRAFT: '초안', DEPRECATED: '폐기', PENDING_APPROVAL: '승인 대기', REJECTED: '반려' };
const IMPACT_TITLE: Record<string, string> = {
  Component: '이 컴포넌트를 변경하면 영향받는 화면', Pattern: '이 패턴을 변경하면 영향받는 화면', PolicyRule: '이 정책규칙을 변경하면 영향받는 화면',
};

const statusColor = (s: string) =>
  s === 'APPROVED' ? 'text-emerald-600 border-emerald-700' : s === 'DEPRECATED' || s === 'REJECTED' ? 'text-[#E90061] border-rose-800'
    : s === 'PENDING_APPROVAL' ? 'text-amber-700 border-amber-400' : 'text-slate-700 border-slate-600';
const errOf = (e: WsEvent | null | undefined) => (e && (e.type === 'error' || e.ok === false)) ? (e.error || e.message || '오류') : '';
const idFromHash = (): string | null => {
  const h = location.hash; const q = h.indexOf('?');
  if (q < 0) return null;
  return new URLSearchParams(h.slice(q + 1)).get('id');
};
const sortedRelated = (rel: Related) =>
  Object.entries(rel || {}).sort((a, b) => {
    const ia = REL_ORDER.indexOf(a[0]), ib = REL_ORDER.indexOf(b[0]);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || b[1] - a[1];
  });

function StatusChip({ s, raw }: { s: string; raw?: string | null }) {
  return <span className={`chip text-[10px] font-semibold ${statusColor(s)}`}
    title={raw && raw !== s ? `원본 상태 ${raw} → ${s}` : s}>{STATUS_KO[s] || s}</span>;
}
function TierBadge({ text = 'Registry · Tier 0/1 전용' }: { text?: string }) {
  return <span className="chip text-[10px] text-amber-700 border-amber-400"
    title="SPEC §11-4 — Registry(AgentCore 미러 포함)는 Tier 0/1 워크로드 전용. Tier 2(PII 추론) 경로에는 쓰지 않는다">{text}</span>;
}
function RelatedChips({ rel, max = 4 }: { rel: Related; max?: number }) {
  const items = sortedRelated(rel);
  if (items.length === 0) return <span className="text-[11px] text-slate-400" title="그래프 순회 결과 — 이웃 없음">관계 없음</span>;
  const shown = items.slice(0, max);
  return (
    <span className="text-[11px] text-slate-700" title="그래프 순회 결과 (store.related_counts — 양방향 이웃을 라벨별로 센 값, 하드코딩 없음)">
      {shown.map(([k, v], i) => <span key={k}>{i > 0 && <span className="text-slate-400"> · </span>}<b>{v}</b> {REL_LABEL[k] || k}</span>)}
      {items.length > max && <span className="text-slate-500"> +{items.length - max}</span>}
    </span>
  );
}

/* ---------------- 카드 ---------------- */
function AssetCard({ c, active, onOpen }: { c: Card; active: boolean; onOpen: (id: string) => void }) {
  return (
    <button onClick={() => onOpen(c.id)}
      className={`panel p-3 text-left hover:border-amber-500/60 flex flex-col gap-1 ${active ? 'border-amber-500' : ''}`}
      style={{ borderTop: '2px solid var(--vpc)' }}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-slate-400">{c.id}</span>
        <StatusChip s={c.status} raw={c.rawStatus} />
        <span className="ml-auto text-[11px] text-slate-500" title={c.version ? `Version ${c.version}` : '온톨로지에 버전 속성 없음'}>
          {c.version ? `v${c.version}` : 'v —'}</span>
      </div>
      <div className="text-sm font-semibold truncate" title={c.name}>{c.name}</div>
      <div className="text-[11px] text-slate-500 truncate" title={c.brief}>{c.brief || ' '}</div>
      <div className="text-[11px] text-slate-500">Owner <span className="text-slate-700">{c.owner || '—'}</span></div>
      <RelatedChips rel={c.related} />
    </button>
  );
}

/* ---------------- 영향 분석 패널 ---------------- */
function ImpactPanel({ im, onOpen, onClose }: { im: Impact; onOpen: (id: string) => void; onClose: () => void }) {
  const tiles: [string, string][] = [['screens', 'Screens'], ['patterns', 'Patterns'], ['policyRules', 'Policies'],
    ['components', 'Components'], ['products', 'Products'], ['departments', 'Departments'], ['procedures', 'Procedures']];
  const [sel, setSel] = useState<string>('');
  const selNode = im.graph.nodes.find(n => n.id === sel);
  return (
    <div className="panel p-4 mb-4" style={{ borderTop: '2px solid var(--vpc)' }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--vpc)' }} />
        <b className="text-sm">{IMPACT_TITLE[im.label] || '영향 분석'} — <span className="font-mono">{im.id}</span> {im.name}</b>
        <span className="chip text-[10px] text-slate-400" title={im.traversal}>그래프 순회 결과 · {im.backend === 'neptune' ? 'Neptune' : 'Local(개발)'} · {im.elapsedMs}ms</span>
        <button className="chip ml-auto hover:border-slate-400" onClick={onClose}>닫기</button>
      </div>
      <div className="grid grid-cols-7 gap-2 mb-3">
        {tiles.map(([k, l]) => (
          <div key={k} className={`rounded-lg border p-2 text-center ${k === 'screens' ? 'border-amber-600' : 'border-slate-200'}`}>
            <div className="text-xl font-bold">{im.counts[k] ?? 0}</div>
            <div className="text-[10px] text-slate-500">{l}</div>
          </div>
        ))}
      </div>
      <div className="text-xs text-slate-400 mb-1">영향 화면 {im.screens.length}건 — 클릭하면 화면 상세로</div>
      <div className="flex flex-wrap gap-1 mb-3 max-h-[88px] overflow-y-auto">
        {im.screens.map(s => (
          <button key={s.id} className="chip text-[10px] hover:border-emerald-500" onClick={() => onOpen(s.id)} title={s.id}>
            <span className="font-mono text-slate-500">{s.id}</span> {s.name}{s.channel && <span className="text-slate-400">· {s.channel}</span>}
          </button>
        ))}
      </div>
      {im.regulations.length > 0 && (
        <div className="text-xs text-slate-400 mb-2">파생 규정: {im.regulations.map(r => <span key={r.id} className="chip text-[10px] ml-1 font-mono">{r.id}</span>)}</div>
      )}
      <GraphView nodes={im.graph.nodes} edges={im.graph.edges} onSelect={setSel} onOpen={onOpen} />
      <div className="text-[11px] text-slate-500 mt-2 flex flex-wrap gap-x-3">
        <span>경로 엣지 {im.pathEdges}건 중 {im.graph.edges.length}건 표시 · 노드 {im.graph.nodes.length}개</span>
        {(im.graph.truncated.nodes > 0 || im.graph.truncated.edges > 0) &&
          <span className="text-amber-700">시각화 상한으로 노드 {im.graph.truncated.nodes} · 엣지 {im.graph.truncated.edges}건 생략 (카운트는 전체 기준)</span>}
        {selNode && <span>선택: <span className="font-mono">{selNode.id}</span> {selNode.name} ({LABEL_KO[selNode.label] || selNode.label}) — 이웃 강조 · 더블클릭으로 상세</span>}
        <span className="text-slate-400">순회: {im.traversal}</span>
      </div>
    </div>
  );
}

/* ---------------- §7 등록 대상 매핑 ---------------- */
function RegistryMap({ m, err, onReload }: { m: WsEvent | null; err: string; onReload: () => void }) {
  const rows: MapRow[] = m?.rows || [];
  const mcpCreated: number = m?.mcpSeed?.created ?? 0;
  return (
    <details className="panel p-3 mt-4">
      <summary className="cursor-pointer text-sm font-semibold flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: 'var(--bedrock)' }} />
        Registry 매핑 (SPEC v2 §7 등록 대상) <TierBadge />
        <span className="text-xs text-slate-500 font-normal">저장소 {m?.registryBackend === 'dynamodb' ? 'DynamoDB' : m?.registryBackend === 'memory' ? '인메모리(개발용)' : '…'}</span>
        {mcpCreated > 0 && <span className="text-xs text-amber-700 font-normal">· §7 MCP 서버 레코드 {mcpCreated}건 시드됨</span>}
        {m?.bootstrapped && <span className="text-xs text-amber-700 font-normal">· 빈 레지스트리에 기준선 {m.bootstrapped.created}건 시드됨</span>}
      </summary>
      {err && <div className="text-xs text-[#E90061] mt-2">{err} <button className="chip ml-2" onClick={onReload}>다시</button></div>}
      <table className="w-full text-xs mt-3">
        <thead><tr className="text-slate-500 border-b border-slate-200">
          <th className="text-left p-2">사내 자산</th><th className="text-left p-2">§7 recordType</th><th className="text-left p-2">원본</th>
          <th className="text-left p-2">현재 구현</th><th className="text-left p-2">Registry 레코드</th></tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.key} className="border-b border-slate-100 align-top">
            <td className="p-2 text-slate-800">{r.asset}</td>
            <td className="p-2 font-mono">{r.recordType}{r.subtype ? <span className="text-slate-500">/{r.subtype}</span> : ''}</td>
            <td className="p-2 text-slate-400">{r.origin}</td>
            <td className={`p-2 ${r.deviation ? 'text-amber-700' : 'text-slate-700'}`} title={r.note || ''}>
              {r.deviation && <span className="chip text-[10px] text-amber-700 border-amber-400 mr-1">명세와 다름</span>}{r.current}
              {r.note && <div className="text-[10px] text-slate-500 mt-0.5">{r.note}</div>}
            </td>
            <td className="p-2">
              {r.records.length === 0 && <span className="text-slate-400">{r.key === 'pattern' || r.key === 'screen_spec' ? '발행 레코드 없음 (Portal Publish 로 생성)' : '레코드 없음'}</span>}
              <div className="flex flex-wrap gap-1">{r.records.map(x => (
                <span key={x.name + x.recordVersion} className={`chip text-[10px] font-mono ${x.found ? statusColor(x.status || '') : 'text-[#E90061] border-rose-800'}`}
                  title={Object.entries(x.payloadFlags || {}).map(([k, v]) => `${k}=${String(v)}`).join(' · ') || ''}>
                  {x.name} {x.recordVersion} <span className="font-sans">{x.found ? (STATUS_KO[x.status || ''] || x.status) : '없음'}</span>
                  {x.payloadFlags?.deployed === false && <span className="font-sans text-amber-700">미배포</span>}
                  {x.payloadFlags?.connected === false && <span className="font-sans text-amber-700">미연결</span>}
                </span>
              ))}</div>
            </td>
          </tr>
        ))}</tbody>
      </table>
      {!m && !err && <div className="text-xs text-slate-400 mt-2">불러오는 중…</div>}
    </details>
  );
}

/* ---------------- 상세 패널 ---------------- */
function DetailPanel({ d, busy, publishRes, syncRes, onClose, onOpen, onImpact, onPublish, onSync }: {
  d: Detail; busy: string; publishRes: WsEvent | null; syncRes: WsEvent | null; onClose: () => void; onOpen: (id: string) => void;
  onImpact: () => void; onPublish: () => void; onSync: () => void;
}) {
  const props = d.props || {};
  const propRows = Object.entries(props).filter(([k]) => !['propsSchema', 'steps', 'prevScreens', 'nextScreens', 'sections'].includes(k));
  const jsonRows = Object.entries(props).filter(([k]) => ['propsSchema', 'steps', 'sections'].includes(k));
  const related = sortedRelated(syncRes?.ok ? syncRes.related : d.related);
  const maxRel = Math.max(1, ...related.map(([, v]) => v));
  const reg = syncRes?.ok && syncRes.registry ? (syncRes.registry as RegistryInfo) : d.registry;
  const pubErr = errOf(publishRes);
  return (
    <aside className="w-[400px] shrink-0">
      <div className="panel p-4 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto" style={{ borderTop: '2px solid var(--vpc)' }}>
        <div className="flex items-start gap-2 mb-2">
          <div className="min-w-0">
            <div className="font-mono text-xs text-slate-400">{d.id} <span className="text-slate-400">· {LABEL_KO[d.label] || d.label}</span></div>
            <div className="text-base font-bold leading-snug break-words">{d.name}</div>
          </div>
          <button className="chip ml-auto hover:border-slate-400 shrink-0" onClick={onClose}>✕</button>
        </div>
        <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
          <StatusChip s={d.status} raw={d.rawStatus} />
          <span className="chip text-[10px]" title={d.version ? '' : '온톨로지에 버전 속성 없음'}>Version {d.version || '—'}</span>
          <span className="chip text-[10px]">Owner {d.owner || '—'}</span>
          {d.alsoIn && <span className="text-[10px] text-slate-500">Foundation · UX Writing 양쪽에 표시</span>}
        </div>

        {/* 액션 */}
        <div className="flex flex-wrap gap-2 mb-3">
          {d.impactSupported && (
            <button onClick={onImpact} disabled={!!busy}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500/90 hover:bg-amber-400 text-white disabled:opacity-40">
              {busy === 'impact' ? '순회 중…' : IMPACT_TITLE[d.label]}
            </button>
          )}
          <button onClick={onPublish} disabled={!!busy || !d.publishable}
            title={d.publishable ? `Registry 로 발행 — ${d.publishTarget?.recordType}/${d.publishTarget?.subtype} (DRAFT)` : 'Publish 미구현 — §7 등록 대상 매핑에 없는 자산 유형'}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-[#008485] hover:bg-[#0a6b6c] text-white disabled:opacity-40">
            {busy === 'publish' ? '발행 중…' : 'Publish'}
          </button>
          <button onClick={onSync} disabled={!!busy} title="그래프 재순회 + Registry 재조회 (외부 원본 Git · Figma 동기화는 미구현)"
            className="chip text-xs hover:border-amber-500">{busy === 'sync' ? '재순회 중…' : 'Sync (그래프 재순회)'}</button>
        </div>
        {!d.publishable && <div className="text-[11px] text-slate-500 mb-3">Publish <b className="text-amber-700">미구현</b> — §7 등록 대상 매핑에 없는 자산 유형({LABEL_KO[d.label] || d.label}). Component · Pattern · Screen 만 발행한다.</div>}
        {syncRes && (
          <div className={`text-[11px] mb-3 ${syncRes.ok ? 'text-emerald-700' : 'text-[#E90061]'}`}>
            {syncRes.ok ? `${syncRes.syncLabel} 완료 · ${syncRes.backend === 'neptune' ? 'Neptune' : 'Local'} · ${syncRes.elapsedMs}ms` : errOf(syncRes)}
            {syncRes.ok && <div className="text-slate-500">{syncRes.note}</div>}
          </div>
        )}
        {publishRes && (
          <section className="rounded-lg border border-teal-900 p-3 mb-3 text-xs" style={{ borderColor: pubErr ? '#9f1239' : undefined }}>
            <div className="flex items-center gap-2 mb-1"><b>Publish 결과</b> <TierBadge /></div>
            {pubErr ? <div className="text-[#E90061]">{pubErr}</div> : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono">{publishRes.record?.name} {publishRes.record?.recordVersion}</span>
                  <span className="text-slate-500">{publishRes.record?.recordType}/{publishRes.record?.subtype}</span>
                  <StatusChip s={publishRes.record?.status} />
                  <span className={`chip text-[10px] ${publishRes.action === 'created' ? 'text-emerald-700 border-emerald-700' : 'text-slate-700'}`}>{publishRes.action === 'created' ? '새로 생성 (DRAFT)' : '기존 레코드'}</span>
                </div>
                <div className="text-slate-500 mt-1">{publishRes.note} · 저장소 {publishRes.registryBackend === 'dynamodb' ? 'DynamoDB' : '인메모리(개발용)'}</div>
                <a href="#/registry" className="text-teal-700 hover:underline">Agent Registry 화면에서 승인 요청 →</a>
              </>
            )}
          </section>
        )}

        {/* Related 분해 */}
        <section className="mb-3">
          <div className="text-xs text-slate-500 mb-1 flex items-center gap-2">Related 분해
            <span className="chip text-[10px]" title="store.related_counts(id) — 양방향 이웃을 라벨별 distinct 로 센 값">그래프 순회 결과</span>
            <span className="text-slate-400">{d.backend === 'neptune' ? 'Neptune' : 'Local(개발)'}</span></div>
          {related.length === 0 && <div className="text-xs text-slate-400">이웃 없음</div>}
          <div className="space-y-1">{related.map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 text-xs">
              <span className="w-24 text-slate-400">{REL_LABEL[k] || k}</span>
              <div className="flex-1 h-2 rounded bg-white overflow-hidden"><div className="h-full" style={{ width: `${Math.round(100 * v / maxRel)}%`, background: 'var(--vpc)' }} /></div>
              <span className="w-8 text-right font-mono">{v}</span>
            </div>
          ))}</div>
        </section>

        {/* Version History */}
        <section className="mb-3">
          <div className="text-xs text-slate-500 mb-1">Version History <span className="text-slate-400">(SUPERSEDED_BY 사슬 · 과거 → 최신)</span></div>
          {d.versionChain.length <= 1
            ? <div className="text-xs text-slate-400">{d.label === 'Component' ? '단일 버전 — 대체 관계 없음' : '버전 사슬 없음 (이 자산 유형에는 SUPERSEDED_BY 관계가 없다)'}</div>
            : <div className="flex flex-wrap items-center gap-1">{d.versionChain.map((c, i) => (
              <span key={c.id} className="flex items-center gap-1">
                {i > 0 && <span className="text-slate-400 text-xs">→</span>}
                <button onClick={() => onOpen(c.id)} className={`chip text-[11px] font-mono ${statusColor(c.status)} ${c.current ? 'bg-slate-800' : 'hover:brightness-125'}`}
                  title={`${c.id} · ${c.rawStatus || c.status}`}>v{c.version || '?'} <span className="font-sans">{STATUS_KO[c.status] || c.status}</span></button>
              </span>
            ))}</div>}
        </section>

        {/* Registry (Component) */}
        {d.label === 'Component' && (
          <section className="rounded-lg border p-3 mb-3 text-xs" style={{ borderColor: 'rgba(56,189,248,.35)' }}>
            <div className="flex items-center gap-2 mb-1"><b>Registry 레코드</b> <TierBadge /></div>
            {!reg || !reg.available ? <div className="text-[#E90061]">{reg?.error || 'Registry 미연결'}</div>
              : reg.record ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono">{reg.record.name} {reg.record.recordVersion}</span>
                  <span className="text-slate-500">{reg.record.recordType}/{reg.record.subtype}</span>
                  <StatusChip s={reg.record.status} />
                  {reg.record.payload?.supersededBy && <span className="text-slate-500">→ {reg.record.payload.supersededBy}</span>}
                  <span className="text-slate-400">{reg.backend === 'dynamodb' ? 'DynamoDB' : '인메모리'}</span>
                </div>
              ) : <div className="text-slate-400">레코드 없음 — <span className="font-mono">{reg.name} {reg.recordVersion}</span> (Publish 로 DRAFT 생성 가능)</div>}
            {d.mapping && <div className="text-[10px] text-amber-700 mt-1" title={d.mapping.note || ''}>§7 매핑: {d.mapping.recordType} — 현재 {d.mapping.current} {d.mapping.deviation && '(명세와 다름 — 숨기지 않음)'}</div>}
          </section>
        )}

        {/* 화면 메타 */}
        {d.meta && (
          <section className="mb-3 text-xs">
            <div className="text-slate-500 mb-1">Screen Metadata <span className="font-mono text-slate-400">{d.meta.metaId}</span> (DESCRIBES)</div>
            <div className="text-slate-700 mb-1">{d.meta.purpose}</div>
            <div className="text-slate-400 mb-1">진입 조건: {d.meta.entryCondition || '—'}</div>
            <div className="flex flex-wrap gap-1 items-center">
              <span className="text-slate-500">이전</span>{d.meta.prevScreens.map(s => <button key={s} className="chip text-[10px] font-mono hover:border-emerald-500" onClick={() => onOpen(s)}>{s}</button>)}
              <span className="text-slate-500 ml-2">다음</span>{d.meta.nextScreens.map(s => <button key={s} className="chip text-[10px] font-mono hover:border-emerald-500" onClick={() => onOpen(s)}>{s}</button>)}
            </div>
          </section>
        )}

        {/* 속성 */}
        <section className="mb-3">
          <div className="text-xs text-slate-500 mb-1">속성</div>
          <table className="w-full text-xs">{propRows.map(([k, v]) => (
            <tr key={k} className="border-b border-slate-100 align-top"><td className="text-slate-500 pr-2 py-0.5 whitespace-nowrap">{k}</td>
              <td className="py-0.5 break-words">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td></tr>
          ))}</table>
          {jsonRows.map(([k, v]) => (
            <details key={k} className="mt-1"><summary className="cursor-pointer text-xs text-slate-400">{k}{Array.isArray(v) ? ` (${v.length})` : ''}</summary>
              {Array.isArray(v) && v.every(x => typeof x === 'string')
                ? <ol className="list-decimal ml-5 text-xs text-slate-700 mt-1">{(v as string[]).map((s, i) => <li key={i}>{s}</li>)}</ol>
                : <pre className="bg-slate-50 rounded p-2 text-[11px] overflow-x-auto max-h-48 mt-1">{JSON.stringify(v, null, 2)}</pre>}
            </details>
          ))}
        </section>

        {/* 이웃 표본 */}
        <section>
          <div className="text-xs text-slate-500 mb-1">관계 이웃 (표본 · 관계별 최대 8)</div>
          <div className="space-y-1.5">{d.neighbors.map(g => (
            <div key={g.rel + g.direction} className="text-xs">
              <span className="font-mono text-slate-400">{g.direction === 'out' ? '→' : '←'} {g.rel}</span> <span className="text-slate-400">{g.count}건</span>
              <div className="flex flex-wrap gap-1 mt-0.5">{g.nodes.map(n => (
                <button key={n.id} className="chip text-[10px] hover:border-slate-400" onClick={() => onOpen(n.id)} title={`${n.id} · ${LABEL_KO[n.label] || n.label}`}>
                  <span className="font-mono text-slate-500">{n.id}</span> {n.name.length > 18 ? n.name.slice(0, 17) + '…' : n.name}</button>
              ))}{g.count > g.nodes.length && <span className="text-slate-400 text-[10px]">+{g.count - g.nodes.length}</span>}</div>
            </div>
          ))}</div>
        </section>
      </div>
    </aside>
  );
}

/* ---------------- 메인 뷰 ---------------- */
export default function Portal() {
  const [cat, setCat] = useState('Components');
  const [cards, setCards] = useState<Card[]>([]);
  const [listMeta, setListMeta] = useState<WsEvent | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailErr, setDetailErr] = useState('');
  const [busy, setBusy] = useState('');
  const [impact, setImpact] = useState<Impact | null>(null);
  const [impactErr, setImpactErr] = useState('');
  const [publishRes, setPublishRes] = useState<WsEvent | null>(null);
  const [syncRes, setSyncRes] = useState<WsEvent | null>(null);
  const [regMap, setRegMap] = useState<WsEvent | null>(null);
  const [regErr, setRegErr] = useState('');
  const catRef = useRef(cat);
  catRef.current = cat;
  const countsRef = useRef<Record<string, number> | null>(null);   // 레일 배지 — 첫 로드 뒤에는 서버 재계산을 생략 (Neptune 질의 절약)

  const loadCards = useCallback(async (c: string) => {
    setLoading(true); setErr('');
    try {
      const e = await sock.request('portal_list', { category: c, withCounts: countsRef.current === null });
      const er = errOf(e); if (er) throw new Error(er);
      if (e.categoryCounts) countsRef.current = e.categoryCounts;
      setCards(e.cards || []); setListMeta({ ...e, categoryCounts: e.categoryCounts || countsRef.current });
    } catch (ex: any) { setErr(ex.message); setCards([]); }
    setLoading(false);
  }, []);
  const loadRegMap = useCallback(async () => {
    setRegErr('');
    try {
      const e = await sock.request('portal_registry_map', {});
      const er = errOf(e); if (er) throw new Error(er);
      setRegMap(e);
    } catch (ex: any) { setRegErr(ex.message); }
  }, []);
  const openDetail = useCallback(async (id: string) => {
    setDetailErr(''); setBusy('detail'); setPublishRes(null); setSyncRes(null);
    try {
      const e = await sock.request('portal_detail', { id });
      const er = errOf(e); if (er) throw new Error(er);
      const d = e as unknown as Detail;
      setDetail(d);
      if (d.category && d.category !== catRef.current) setCat(d.category);
    } catch (ex: any) { setDetailErr(ex.message); }
    setBusy('');
  }, []);

  useEffect(() => { loadCards(cat); }, [cat, loadCards]);
  useEffect(() => {
    loadRegMap();
    openDetail(idFromHash() || DEFAULT_ID);   // 프리셋: #/portal?id=… 또는 기본 CMP-Button-v2
    const h = () => { const id = idFromHash(); if (id) openDetail(id); };
    window.addEventListener('hashchange', h);
    return () => window.removeEventListener('hashchange', h);
  }, [loadRegMap, openDetail]);

  const runImpact = async () => {
    if (!detail) return;
    setBusy('impact'); setImpactErr('');
    try {
      const e = await sock.request('portal_impact', { id: detail.id });
      const er = errOf(e); if (er) throw new Error(er);
      setImpact(e as unknown as Impact);
    } catch (ex: any) { setImpactErr(ex.message); setImpact(null); }
    setBusy('');
  };
  const runPublish = async () => {
    if (!detail) return;
    setBusy('publish');
    try {
      const e = await sock.request('portal_publish', { id: detail.id });
      setPublishRes(e);
      if (e.ok) loadRegMap();
    } catch (ex: any) { setPublishRes({ type: 'error', message: ex.message }); }
    setBusy('');
  };
  const runSync = async () => {
    if (!detail) return;
    setBusy('sync');
    try {
      const e = await sock.request('portal_sync', { id: detail.id });
      setSyncRes(e);
      if (e.ok) setCards(cs => cs.map(c => c.id === e.id ? { ...c, related: e.related } : c));
    } catch (ex: any) { setSyncRes({ type: 'error', message: ex.message }); }
    setBusy('');
  };

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return cards;
    return cards.filter(c => [c.id, c.name, c.brief, c.owner, c.rawStatus, c.status, c.meta?.purpose]
      .filter(Boolean).join(' ').toLowerCase().includes(needle));
  }, [cards, q]);
  const catInfo = CATS.find(c => c.id === cat)!;
  const counts: Record<string, number> = listMeta?.categoryCounts || {};

  return (
    <div>
      <div className="text-xs text-slate-500 mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>고객 PoC 요건 6종 라이브러리(Component · Pattern · Procedure · Policy Rule · UX Dictionary · Screen Metadata)를 온톨로지에서 직접 읽는다.</span>
        <span className="chip text-[10px]" style={{ borderColor: 'var(--vpc)', color: 'var(--vpc)' }}>
          온톨로지 · {listMeta ? (listMeta.backend === 'neptune' ? 'Neptune Serverless (VPC 내부)' : 'Local 인메모리 (개발용)') : '…'}</span>
        <span className="chip text-[10px]" title="§12.8 — Related 카운트·영향 범위는 그래프 순회 결과. 하드코딩된 수치 없음">Related = 그래프 순회 결과</span>
        <span className="text-slate-400">프리셋</span>
        {PRESETS.map(p => <button key={p} className="chip text-[10px] font-mono hover:border-amber-500" onClick={() => openDetail(p)}>{p}</button>)}
      </div>

      <div className="flex gap-4 items-start">
        {/* 좌측 카테고리 */}
        <nav className="w-44 shrink-0 panel p-2 sticky top-20">
          {CATS.map(c => (
            <button key={c.id} onClick={() => { setCat(c.id); setQ(''); }}
              className={`w-full text-left px-3 py-2 rounded-lg mb-0.5 ${cat === c.id ? 'bg-amber-950/50 text-amber-200' : 'text-slate-400 hover:bg-slate-100'}`}>
              <div className="flex items-center text-sm"><span>{c.label}</span>
                <span className="ml-auto text-[10px] text-slate-500 font-mono">{counts[c.id] ?? '…'}</span></div>
              <div className="text-[10px] text-slate-400">{c.sub}</div>
            </button>
          ))}
          <div className="text-[10px] text-slate-400 px-3 pt-2 border-t border-slate-200 mt-1">
            Foundation 은 <b className="text-amber-700">미구현</b> — 디자인 토큰 노드가 없어 UX Dictionary 공통 용어로 대체 표시.
          </div>
        </nav>

        {/* 중앙: 검색 + 영향 분석 + 카드 */}
        <section className="flex-1 min-w-0">
          <div className="panel p-3 mb-3 flex flex-wrap items-center gap-2">
            <div>
              <div className="text-sm font-semibold">{catInfo.label} <span className="text-slate-500 font-normal text-xs">— {listMeta?.library || catInfo.sub} · 라벨 <span className="font-mono">{listMeta?.label || ''}</span></span></div>
              {listMeta?.note && <div className="text-[11px] text-amber-700 mt-0.5">{listMeta.note}</div>}
            </div>
            <input className="ml-auto w-72 px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-sm"
              placeholder="검색 (ID · 이름 · 소유 · 상태 — 클라이언트 필터)" value={q} onChange={e => setQ(e.target.value)} />
            <span className="text-xs text-slate-500">{loading ? '불러오는 중…' : `${shown.length}/${cards.length}건`}{listMeta && ` · ${listMeta.elapsedMs}ms`}</span>
          </div>
          {err && <div className="text-[#E90061] text-sm mb-3">{err}</div>}
          {detailErr && <div className="text-[#E90061] text-xs mb-3">상세 조회 실패: {detailErr}</div>}
          {impactErr && <div className="text-[#E90061] text-xs mb-3">영향 분석 실패: {impactErr}</div>}
          {impact && <ImpactPanel im={impact} onOpen={openDetail} onClose={() => setImpact(null)} />}
          <div className={`grid gap-3 ${detail ? 'grid-cols-2' : 'grid-cols-3'}`}>
            {shown.map(c => <AssetCard key={c.id} c={c} active={detail?.id === c.id} onOpen={openDetail} />)}
          </div>
          {!loading && shown.length === 0 && <div className="panel p-6 text-sm text-slate-500 text-center">{cards.length === 0 ? '자산 없음' : '검색 결과 없음'}</div>}
          <RegistryMap m={regMap} err={regErr} onReload={loadRegMap} />
        </section>

        {detail && <DetailPanel d={detail} busy={busy} publishRes={publishRes} syncRes={syncRes} onClose={() => setDetail(null)}
          onOpen={openDetail} onImpact={runImpact} onPublish={runPublish} onSync={runSync} />}
      </div>
    </div>
  );
}
