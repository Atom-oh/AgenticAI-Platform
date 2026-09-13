// UX Asset Portal (SPEC v2 §8-2) — 좌측 7 카테고리 · 자산 카드(ID · Status · Version · Owner · Related) · 상세(Version History ·
// Related 분해 · Publish / Sync · 영향 분석). Related 카운트와 영향 범위는 전부 그래프 순회 결과다 (§12.8 하드코딩 금지).
// 색 규칙(§8-4): 온톨로지(VPC 내부 Neptune) 데이터 = var(--vpc) 앰버, Registry(클라우드 메타데이터) = var(--bedrock) 시안.
// Registry 데이터가 보이는 곳에는 'Tier 0/1 전용' 배지(§11-4). 브라우저 스토리지는 쓰지 않는다 (§12.12).
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import GraphView, { GEdge, GNode } from '../GraphView';
import { sock, WsEvent } from '../lib';
import DiagramPreview from '../portal/DiagramPreview';
import type { PortalDiagram } from '../portal/diagram';
import ReactComponentPreview from '../portal/ReactComponentPreview';
import { loadReactCatalog, usageSnippet, type ReactCatalog } from '../portal/reactCatalog';
import ImagePreview from '../portal/ImagePreview';
import { imageSource } from '../portal/image-source';
import '../portal/portal.css';

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
  visual?: PortalDiagram | { kind: 'empty'; reason: string; note: string };
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
  { id: 'Foundation', label: '기초 기준', sub: 'Foundation · 공통 용어' },
  { id: 'Components', label: '컴포넌트', sub: 'React · 설계 연결' },
  { id: 'Patterns', label: 'UX 패턴', sub: 'Pattern · 구성 관계' },
  { id: 'Screens', label: '화면 · 이동', sub: 'Screen · 앞뒤 화면' },
  { id: 'Procedures', label: '사용자 흐름', sub: 'Procedure · 단계 순서' },
  { id: 'Policies', label: '업무 규칙', sub: 'Policy · 적용 대상' },
  { id: 'UXWriting', label: 'UX 문구', sub: 'UX Writing · 공통 표현' },
];
const PRESETS = ['CMP-Button-v2', 'CMP-Input-v3', 'PAT-001', 'POL-000', 'SCR-001', 'PRC-000'];
const COMPONENT_LABELS: Record<string, string> = {
  Button: '버튼', Input: '입력 필드', Checkbox: '체크박스', Select: '선택 목록', RadioGroup: '단일 선택',
  Alert: '안내 메시지', Stepper: '진행 단계', Summary: '정보 요약', AssetImage: '이미지',
  Screen: '화면 틀', Panel: '영역 카드', Stack: '세로 배치', Grid: '격자 배치', Inline: '가로 배치', Text: '텍스트',
};
const COMPONENT_ORDER = ['Button', 'Input', 'Checkbox', 'Select', 'RadioGroup', 'Alert', 'Stepper', 'Summary', 'AssetImage', 'Screen', 'Panel', 'Stack', 'Grid', 'Inline', 'Text'];
const COMPONENT_HELP: Record<string, string> = {
  Button: '버튼을 눌러 보고, 표현과 비활성 상태를 바꿔 보세요.',
  Input: '값을 입력하고 안내 문구와 비활성 상태가 어떻게 보이는지 확인하세요.',
  Checkbox: '체크 상태와 비활성 상태를 직접 바꿔 보세요.',
  Select: '목록에서 항목을 선택하면 선택한 값이 반영됩니다.',
  RadioGroup: '여러 항목 중 하나를 선택하는 모습을 확인하세요.',
  Alert: '안내·완료·주의·오류 메시지의 표현을 비교하세요.',
  Stepper: '단계를 이동하며 현재 단계 표시를 확인하세요.',
  Summary: '항목 이름과 값을 묶어 보여 주는 요약 영역입니다.',
  AssetImage: '이미지의 크기와 배치를 확인하는 예제입니다.',
  Screen: '제목과 콘텐츠 영역이 포함된 페이지 틀을 확인하세요.',
  Panel: '연관된 내용을 묶는 영역의 표현을 비교하세요.',
  Stack: '요소 사이 간격을 바꾸며 세로 배치를 확인하세요.',
  Grid: '열 수와 간격을 바꾸며 격자 배치를 확인하세요.',
  Inline: '요소의 가로 정렬과 간격을 확인하세요.',
  Text: '텍스트의 크기와 표현을 비교하세요.',
};
type CodeComponent = ReactCatalog['components'][number];
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

function CodeCard({ component, catalog, active, onOpen }: { component: CodeComponent; catalog: ReactCatalog; active: boolean; onOpen: () => void }) {
  return <article className={`portal-code-card${active ? ' is-selected' : ''}`}>
    <div className="portal-code-thumbnail" aria-hidden="true" ref={element => { if (element) element.inert = true; }}>
      <ReactComponentPreview name={component.name} compact />
    </div>
    <button type="button" className="portal-code-open" onClick={onOpen} aria-pressed={active}>
      <span>{COMPONENT_LABELS[component.name] || component.name}</span>
      <span className="portal-code-name">{component.name}</span>
      <span className="portal-code-origin">{catalog.id} · v{catalog.version} · 플랫폼 샘플</span>
      <span className="portal-open-hint">직접 조작하기 <span aria-hidden="true">↗</span></span>
    </button>
  </article>;
}

function CodeDetail({ component, catalog, onClose, onFindDesign }: {
  component: CodeComponent; catalog: ReactCatalog; onClose: () => void; onFindDesign: () => void;
}) {
  return <aside className="portal-detail" aria-label={`${component.name} React 컴포넌트 상세`}>
    <div className="panel portal-detail-inner">
      <div className="portal-detail-heading">
        <div><p className="portal-eyebrow">실제 React 코드 · {catalog.id} v{catalog.version}</p>
          <h2>{COMPONENT_LABELS[component.name] || component.name} <span>{component.name}</span></h2></div>
        <button type="button" className="chip" aria-label="컴포넌트 상세 닫기" onClick={onClose}>닫기</button>
      </div>
      <p className="portal-muted">{COMPONENT_HELP[component.name] || component.description}</p>
      <ReactComponentPreview key={component.name} name={component.name} />
      <p className="portal-preview-note">플랫폼에 포함된 원본 React 코드로 실행합니다. 이 예제의 조작은 고객 승인이나 업무 흐름 검증을 뜻하지 않습니다.</p>
      <button type="button" className="portal-secondary" onClick={onFindDesign}>같은 이름의 설계 자산 찾기</button>
      <details className="portal-technical">
        <summary>개발 속성 · 코드 기준 확인</summary>
        <p>{component.description}</p>
        <p>구현 위치: <code>platform/react-kit/ui/index.tsx</code></p>
        <dl><dt>패키지</dt><dd>{catalog.label} · {catalog.id} · {catalog.version}</dd><dt>소스 기준 해시</dt><dd className="portal-hash">{catalog.hash}</dd></dl>
        <table><tbody>{Object.entries(component.props).map(([key, value]) =>
          <tr key={key}><th>{key}</th><td>{value}</td></tr>)}</tbody></table>
        <p>허용된 변화: {component.variationAxes.join(' · ') || '명시된 속성 범위 내에서만 변경'}</p>
        <details><summary>사용 예시 (TSX)</summary><pre className="portal-code-example">{usageSnippet(component.name)}</pre></details>
      </details>
    </div>
  </aside>;
}

function FlowPreview({ visual, name }: { visual: PortalDiagram; name: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (expanded && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [expanded]);
  const labels = new Map(visual.nodes.map(node => [node.id, node.label]));
  return <section className="portal-flow-preview">
    <p className="portal-flow-hint">{visual.nodes.length}{visual.source === 'procedure-steps' ? '단계' : '개 항목'} · 그림 안에서 스크롤하거나 전체 화면으로 볼 수 있습니다.</p>
    <DiagramPreview visual={visual} title={name} />
    <button type="button" className="portal-secondary" onClick={() => setExpanded(true)}>전체 화면으로 흐름 보기</button>
    <details className="portal-flow-outline"><summary>순서·연결을 텍스트로 보기</summary>
      <ul>{visual.nodes.map(node => <li key={node.id}>{node.label}{node.missing ? ' · 참조 미확인' : ''}</li>)}</ul>
      {visual.edges.length > 0 && <ul>{visual.edges.map((edge, index) => <li key={index}>
        {labels.get(edge.from)} → {labels.get(edge.to)} · {edge.label || (edge.kind === 'sequence' ? '순서' : '참조')}
      </li>)}</ul>}
    </details>
    {expanded && <dialog className="portal-wide-dialog" ref={dialog} aria-labelledby={titleId} onClose={() => setExpanded(false)}>
      <header><h2 id={titleId}>{name}</h2><button type="button" className="portal-secondary" onClick={() => dialog.current?.close()}>전체 화면 닫기</button></header>
      <p className="portal-flow-hint">그림 안에서 스크롤해 나머지 항목을 확인하거나, 화면에 맞춤을 선택하세요.</p>
      <DiagramPreview visual={visual} title={name} large />
    </dialog>}
  </section>;
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
  const requested = useRef(false);
  const rows: MapRow[] = m?.rows || [];
  const mcpCreated: number = m?.mcpSeed?.created ?? 0;
  return (
    <details className="panel p-3 mt-4" onToggle={event => {
      if (event.currentTarget.open && !requested.current) { requested.current = true; onReload(); }
    }}>
      <summary className="cursor-pointer text-sm font-semibold flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: 'var(--bedrock)' }} />
        Registry 매핑 (SPEC v2 §7 등록 대상) <TierBadge />
        <span className="text-xs text-slate-500 font-normal">저장소 {m?.registryBackend === 'dynamodb' ? 'DynamoDB' : m?.registryBackend === 'memory' ? '인메모리(개발용)' : '…'}</span>
        {mcpCreated > 0 && <span className="text-xs text-amber-700 font-normal">· §7 MCP 서버 레코드 {mcpCreated}건 시드됨</span>}
        {m?.bootstrapped && <span className="text-xs text-amber-700 font-normal">· 빈 레지스트리에 기준선 {m.bootstrapped.created}건 시드됨</span>}
      </summary>
      {err && <div className="text-xs text-[#E90061] mt-2">{err} <button className="chip ml-2" onClick={onReload}>다시</button></div>}
      <div className="portal-table-scroll"><table className="w-full text-xs mt-3">
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
      </table></div>
      {!m && !err && <div className="text-xs text-slate-400 mt-2">불러오는 중…</div>}
    </details>
  );
}

/* ---------------- 상세 패널 ---------------- */
function DetailPanel({ d, busy, publishRes, syncRes, onClose, onOpen, onImpact, onPublish, onSync, onCode, codeAvailable }: {
  d: Detail; busy: string; publishRes: WsEvent | null; syncRes: WsEvent | null; onClose: () => void; onOpen: (id: string) => void;
  onImpact: () => void; onPublish: () => void; onSync: () => void;
  onCode: (name: string) => void; codeAvailable: boolean;
}) {
  const props = d.props || {};
  const propRows = Object.entries(props).filter(([k]) => !['propsSchema', 'steps', 'prevScreens', 'nextScreens', 'sections'].includes(k));
  const jsonRows = Object.entries(props).filter(([k]) => ['propsSchema', 'steps', 'sections'].includes(k));
  const related = sortedRelated(syncRes?.ok ? syncRes.related : d.related);
  const maxRel = Math.max(1, ...related.map(([, v]) => v));
  const reg = syncRes?.ok && syncRes.registry ? (syncRes.registry as RegistryInfo) : d.registry;
  const pubErr = errOf(publishRes);
  return (
    <aside className="portal-detail" aria-label={`${d.name} 설계 자산 상세`}>
      <div className="panel portal-detail-inner" style={{ borderTop: '2px solid var(--vpc)' }}>
        <div className="flex items-start gap-2 mb-2">
          <div className="min-w-0">
            <div className="font-mono text-xs text-slate-400">{d.id} <span className="text-slate-400">· {LABEL_KO[d.label] || d.label}</span></div>
            <div className="text-base font-bold leading-snug break-words">{d.name}</div>
          </div>
          <button className="chip ml-auto hover:border-slate-400 shrink-0" aria-label="설계 자산 상세 닫기" onClick={onClose}>닫기</button>
        </div>
        <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
          <span className="text-slate-500">설계 등록 상태</span>
          <StatusChip s={d.status} raw={d.rawStatus} />
          <span className="chip text-[10px]" title={d.version ? '' : '온톨로지에 버전 속성 없음'}>Version {d.version || '—'}</span>
          <span className="chip text-[10px]">Owner {d.owner || '—'}</span>
          {d.alsoIn && <span className="text-[10px] text-slate-500">Foundation · UX Writing 양쪽에 표시</span>}
        </div>

        <ImagePreview props={props} name={d.name} />
        {d.label === 'Screen' && !imageSource(props, d.name) &&
          <p className="portal-preview-note">화면 원본 이미지가 아직 연결되지 않았습니다. 아래 다이어그램은 등록된 화면 이동 관계입니다.</p>}
        {d.visual?.kind === 'diagram' ? <FlowPreview visual={d.visual} name={d.name} /> :
          !imageSource(props, d.name) && <section className="portal-unlinked" aria-label="미리보기 연결 상태">
            <h3>{d.visual?.kind === 'empty' ? d.visual.reason : '이 자산의 미리보기를 아직 확인하지 못했습니다.'}</h3>
            <p>{d.visual?.kind === 'empty' ? d.visual.note : '자산의 그림·순서 데이터가 없거나 이전 API 응답입니다. 속성만으로 화면을 임의 생성하지 않습니다.'}</p>
            {d.label === 'Component' && codeAvailable && <button type="button" className="portal-secondary" onClick={() => onCode(d.name)}>
              플랫폼 {d.name} 실행 예제 보기
            </button>}
            {d.label === 'Component' && codeAvailable && <p>같은 이름의 플랫폼 예제이며, 이 설계 자산의 {d.version || '원본'} 구현으로 자동 연결되지 않습니다.</p>}
            {d.label === 'Screen' && <a className="portal-link" href="#/studio">Design Studio에서 원본 파일 반입하기</a>}
          </section>}
        {d.visual?.kind === 'diagram' && d.visual.nodes.some(node => node.assetId && !node.missing && node.assetId !== d.id) &&
          <div className="portal-flow-links" aria-label="다이어그램의 연결 자산">
            <span>연결 자산 열기</span>{d.visual.nodes.filter(node => node.assetId && !node.missing && node.assetId !== d.id).map(node =>
              <button type="button" key={node.id} onClick={() => onOpen(node.assetId!)}>{node.label}</button>)}
          </div>}

        <details className="portal-technical">
        <summary>설계 속성 · 연결 · 발행 관리</summary>
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
              <td className="py-0.5 break-words">{typeof v === 'string' && v.startsWith('data:image/')
                ? `반입 이미지 데이터 URL (${v.length.toLocaleString()}자)` : typeof v === 'object' ? JSON.stringify(v) : String(v)}</td></tr>
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
        </details>
      </div>
    </aside>
  );
}

/* ---------------- Main portal ---------------- */
export default function Portal() {
  const initialId = idFromHash();
  const [cat, setCat] = useState('Components');
  const [componentView, setComponentView] = useState<'react' | 'ontology'>(initialId ? 'ontology' : 'react');
  const [codeName, setCodeName] = useState<string | null>(initialId ? null : new URLSearchParams(location.hash.split('?')[1]).get('component') || 'Button');
  const [catalog, setCatalog] = useState<ReactCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [catalogRetry, setCatalogRetry] = useState(0);
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
  const catRef = useRef(cat); catRef.current = cat;
  const countsRef = useRef<Record<string, number> | null>(null);
  const listRequest = useRef(0), detailRequest = useRef(0);
  const selectedId = useRef<string | null>(null);
  const showCode = cat === 'Components' && componentView === 'react';
  const currentCode = catalog?.components.find(item => item.name === codeName);
  const changeCategory = useCallback((category: string) => {
    if (category !== catRef.current) {
      ++listRequest.current;
      catRef.current = category;
      setCards([]); setListMeta(null); setLoading(true); setErr('');
      setCat(category);
    }
  }, []);

  useEffect(() => {
    let active = true; setCatalogError('');
    loadReactCatalog().then(value => { if (active) setCatalog(value); })
      .catch(() => { if (active) setCatalogError('React 컴포넌트 기준을 불러오지 못했습니다. 다시 시도하세요.'); });
    return () => { active = false; };
  }, [catalogRetry]);

  const loadCards = useCallback(async (category: string) => {
    const request = ++listRequest.current; setLoading(true); setErr('');
    try {
      const e = await sock.request('portal_list', { category, withCounts: countsRef.current === null });
      if (request !== listRequest.current || category !== catRef.current) return;
      const error = errOf(e); if (error) throw new Error(error);
      if (e.categoryCounts) countsRef.current = e.categoryCounts;
      setCards(e.cards || []); setListMeta({ ...e, categoryCounts: e.categoryCounts || countsRef.current });
    } catch (error: any) {
      if (request === listRequest.current) { setErr(error.message); setCards([]); }
    } finally { if (request === listRequest.current) setLoading(false); }
  }, []);
  const loadRegMap = useCallback(async () => {
    setRegErr('');
    try {
      const e = await sock.request('portal_registry_map', {});
      const error = errOf(e); if (error) throw new Error(error);
      setRegMap(e);
    } catch (error: any) { setRegErr(error.message); }
  }, []);
  const clearSelection = useCallback(() => {
    ++detailRequest.current; selectedId.current = null;
    setDetail(null); setCodeName(null); setDetailErr(''); setBusy('');
    setPublishRes(null); setSyncRes(null); setImpact(null); setImpactErr('');
  }, []);
  const openCode = useCallback((name: string) => {
    clearSelection(); changeCategory('Components'); setComponentView('react'); setCodeName(name); setQ('');
    history.replaceState(null, '', '#/portal?component=' + encodeURIComponent(name));
  }, [clearSelection, changeCategory]);
  const openDetail = useCallback(async (id: string) => {
    clearSelection(); const request = ++detailRequest.current; selectedId.current = id;
    setComponentView('ontology'); setBusy('detail');
    history.replaceState(null, '', '#/portal?id=' + encodeURIComponent(id));
    try {
      const e = await sock.request('portal_detail', { id });
      if (request !== detailRequest.current) return;
      const error = errOf(e); if (error) throw new Error(error);
      const result = e as unknown as Detail;
      setDetail(result);
      if (result.category && result.category !== catRef.current) { setQ(''); changeCategory(result.category); }
    } catch (error: any) { if (request === detailRequest.current) setDetailErr(error.message); }
    finally { if (request === detailRequest.current) setBusy(''); }
  }, [clearSelection, changeCategory]);

  useEffect(() => { void loadCards(cat); }, [cat, loadCards]);
  useEffect(() => {
    if (idFromHash()) void openDetail(idFromHash()!);
    const changed = () => {
      const id = idFromHash();
      const component = new URLSearchParams(location.hash.split('?')[1]).get('component');
      if (id) void openDetail(id); else if (component) openCode(component);
    };
    window.addEventListener('hashchange', changed);
    return () => { window.removeEventListener('hashchange', changed); ++detailRequest.current; ++listRequest.current; };
  }, [openDetail, openCode]);

  const chooseCategory = (category: string) => {
    clearSelection(); changeCategory(category); setQ('');
    if (category === 'Components') { setComponentView('react'); setCodeName('Button'); }
    history.replaceState(null, '', '#/portal');
  };
  const runImpact = async () => {
    if (!detail) return; const id = detail.id, request = detailRequest.current;
    setBusy('impact'); setImpactErr('');
    try {
      const e = await sock.request('portal_impact', { id });
      if (request !== detailRequest.current) return;
      const error = errOf(e); if (error) throw new Error(error);
      setImpact(e as unknown as Impact);
    } catch (error: any) { if (request === detailRequest.current) { setImpactErr(error.message); setImpact(null); } }
    finally { if (request === detailRequest.current) setBusy(''); }
  };
  const runPublish = async () => {
    if (!detail) return; const id = detail.id, request = detailRequest.current;
    setBusy('publish');
    try {
      const e = await sock.request('portal_publish', { id });
      if (request !== detailRequest.current) return;
      setPublishRes(e); if (e.ok) void loadRegMap();
    } catch (error: any) { if (request === detailRequest.current) setPublishRes({ type: 'error', message: error.message }); }
    finally { if (request === detailRequest.current) setBusy(''); }
  };
  const runSync = async () => {
    if (!detail) return; const id = detail.id, request = detailRequest.current;
    setBusy('sync');
    try {
      const e = await sock.request('portal_sync', { id });
      if (request !== detailRequest.current) return;
      if (e.ok) {
        const fresh = await sock.request('portal_detail', { id });
        if (request !== detailRequest.current) return;
        const error = errOf(fresh); if (error) throw new Error(error);
        setDetail(fresh as unknown as Detail);
        setSyncRes({ ...e, related: fresh.related, registry: fresh.registry });
        setCards(current => current.map(card => card.id === id ? { ...card, related: fresh.related } : card));
      } else setSyncRes(e);
    } catch (error: any) { if (request === detailRequest.current) setSyncRes({ type: 'error', message: error.message }); }
    finally { if (request === detailRequest.current) setBusy(''); }
  };

  const shown = useMemo(() => {
    const query = q.trim().toLowerCase();
    return cards.filter(card => !query || [card.id, card.name, card.brief, card.owner, card.rawStatus, card.status, card.meta?.purpose]
      .filter(Boolean).join(' ').toLowerCase().includes(query));
  }, [cards, q]);
  const shownCode = useMemo(() => [...(catalog?.components || [])]
    .filter(item => [item.name, COMPONENT_LABELS[item.name], item.description].join(' ').toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a, b) => COMPONENT_ORDER.indexOf(a.name) - COMPONENT_ORDER.indexOf(b.name)), [catalog, q]);
  const catInfo = CATS.find(item => item.id === cat)!;
  const counts: Record<string, number> = listMeta?.categoryCounts || countsRef.current || {};
  const hasDetail = showCode ? !!codeName : !!detail || busy === 'detail' || !!detailErr;

  return <div className="portal-page">
    <header className="portal-intro">
      <div><p className="portal-eyebrow">DESIGN ASSET LIBRARY</p><h2>그림으로 확인하고, 직접 사용해 보세요.</h2>
        <p>컴포넌트는 실제 React로, 사용자 흐름과 설계 관계는 다이어그램으로 확인합니다.</p></div>
      <a href="#/studio" className="portal-secondary">파일 반입 · Design Studio</a>
    </header>
    <div className={`portal-layout${hasDetail ? ' has-detail' : ''}`}>
      <nav className="portal-nav panel" aria-label="디자인 자산 유형">
        {CATS.map(item => <button type="button" key={item.id} onClick={() => chooseCategory(item.id)}
          aria-current={cat === item.id ? 'page' : undefined}>
          <span className="portal-nav-label">{item.label}<small>{item.id === 'Components' && showCode ? catalog?.components.length ?? '…' : counts[item.id] ?? '…'}</small></span>
          <span className="portal-nav-sub">{item.sub}</span>
        </button>)}
        <p className="portal-nav-note">플랫폼 코드와 설계 메타데이터의 기준·상태를 구분해서 표시합니다.</p>
      </nav>
      <section className="portal-library" aria-label="자산 목록">
        <div className="panel portal-library-toolbar">
          <div><h3>{catInfo.label}</h3><p>{showCode ? '플랫폼에 포함된 React 구현' : catInfo.sub}</p></div>
          <label className="portal-search"><span className="sr-only">디자인 자산 검색</span>
            <input placeholder="이름 · 설명 · ID 검색" value={q} onChange={event => setQ(event.target.value)} /></label>
          <span className="portal-count">{showCode ? catalog ? `${shownCode.length}개` : '불러오는 중…' : loading ? '불러오는 중…' : `${shown.length}개`}</span>
          {cat === 'Components' && <div className="portal-view-switch" role="group" aria-label="컴포넌트 보기 방식">
            <button type="button" aria-pressed={showCode} onClick={() => { clearSelection(); setComponentView('react'); setCodeName('Button'); setQ(''); }}>실제 React</button>
            <button type="button" aria-pressed={!showCode} onClick={() => { clearSelection(); setComponentView('ontology'); setQ(''); }}>설계 메타데이터 <span>{counts.Components ?? '…'}</span></button>
          </div>}
        </div>
        {showCode ? <>
          <p className="portal-source-note">{catalog ? `${catalog.label} · ${catalog.version}` : 'React 기준 조회 중'} · 고객 사내 패키지의 승인을 뜻하지 않습니다.</p>
          {catalogError && <div className="portal-error" role="alert">{catalogError} <button type="button" onClick={() => setCatalogRetry(value => value + 1)}>다시 조회</button></div>}
          <div className="portal-card-grid">{shownCode.map(component => <CodeCard key={component.name} component={component} catalog={catalog!}
            active={codeName === component.name} onOpen={() => openCode(component.name)} />)}</div>
          {catalog && !shownCode.length && <p className="portal-empty">검색 결과가 없습니다.</p>}
        </> : <>
          <p className="portal-source-note">등록된 온톨로지 · {listMeta?.backend === 'neptune' ? 'Neptune' : 'Local 개발 데이터'}
            {cat === 'Components' && ' · 코드가 연결되지 않은 항목도 포함됩니다.'}</p>
          {listMeta?.note && <p className="portal-source-note">{listMeta.note}</p>}
          {err && <div className="portal-error" role="alert">{err} <button type="button" onClick={() => loadCards(cat)}>다시 조회</button></div>}
          {impactErr && <p className="portal-error" role="alert">{impactErr}</p>}
          {impact && <ImpactPanel im={impact} onOpen={openDetail} onClose={() => setImpact(null)} />}
          <div className="portal-card-grid">{shown.map(card => <AssetCard key={card.id} c={card} active={detail?.id === card.id} onOpen={openDetail} />)}</div>
          {!loading && !shown.length && <p className="portal-empty">{cards.length ? '검색 결과가 없습니다.' : '등록된 자산이 없습니다.'}</p>}
        </>}
        <details className="portal-presets"><summary>설계 자산 바로가기</summary><div>{PRESETS.map(id =>
          <button type="button" key={id} onClick={() => openDetail(id)}>{id}</button>)}</div></details>
        <RegistryMap m={regMap} err={regErr} onReload={loadRegMap} />
      </section>
      {showCode && currentCode && catalog && <CodeDetail key={currentCode.name} component={currentCode} catalog={catalog}
        onClose={clearSelection} onFindDesign={() => { clearSelection(); setComponentView('ontology'); setQ(currentCode.name); }} />}
      {showCode && codeName && catalog && !currentCode && <aside className="portal-detail panel portal-error" role="alert">이 React 컴포넌트는 현재 패키지에 없습니다.</aside>}
      {showCode && codeName && !catalog && <aside className="portal-detail panel portal-detail-placeholder" role="status">
        {catalogError || '실제 React 컴포넌트를 준비하고 있습니다…'}</aside>}
      {!showCode && detail && <DetailPanel key={detail.id} d={detail} busy={busy} publishRes={publishRes} syncRes={syncRes}
        onClose={clearSelection} onOpen={openDetail} onImpact={runImpact} onPublish={runPublish} onSync={runSync}
        onCode={openCode} codeAvailable={!!catalog?.components.some(item => item.name === detail.name)} />}
      {!showCode && !detail && hasDetail && <aside className="portal-detail panel portal-detail-placeholder" aria-live="polite">
        {detailErr ? <p role="alert">상세 조회 실패: {detailErr}</p> : <p role="status">선택한 자산의 미리보기를 불러오는 중…</p>}
      </aside>}
    </div>
  </div>;
}
