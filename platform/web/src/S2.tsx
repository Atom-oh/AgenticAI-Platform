// S2 마이데이터 상담 — 단계별 펼침 패널. 숫자는 계산엔진만 만든다 (SPEC v2 §2 S2 · §6 · §8-3 · §11 · §12.4).
// 색 규칙(§8-4): VPC 내부 = var(--onprem)(앰버) · Bedrock = var(--cloud)(시안). 경계(익명화 게이트)는 두 영역의 유일한 접점.
// 배지 문구의 출처는 서버 engine/gate.py(route_info · gate_info) 하나다 — 아래 *_FALLBACK 은 실행 전 표시용 동일 문구이며
// 실행하면 서버가 보낸 'route' / 'mask' 스테이지 값으로 대체된다 (§11 배지는 항상 보여야 한다 — 툴팁 금지).
import { useState } from 'react';
import type { ReactNode } from 'react';
import { sock } from './lib';

export const S2_PRESET = '제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?';
export const S5_PRESET = '어떤 상품이 제일 돈 많이 벌어요?';

type Stage = { step: string; plane?: string; [k: string]: any };
type Route = 'claude' | 'gemma';
type Badge = { title: string; prod: string; demo: string; region?: string; substituted?: boolean; implemented?: boolean };
type Boundary = { chars?: number; estTokens?: number; fieldsPassed?: string[]; piiRules?: { count?: number; hits?: any[] } } | null | undefined;

const VPC = 'var(--onprem)';     // VPC 내부 (앰버) — 별칭 --vpc 와 같은 색
const BEDROCK = 'var(--cloud)';  // Bedrock (시안)
const OK = 'var(--ok)';
const ROSE = '#f43f5e';

const ROUTES: { id: Route; label: string; sub: string }[] = [
  { id: 'claude', label: 'Tier 0/1 · Claude (global)', sub: 'bedrock-runtime Converse · 소스 리전 ap-northeast-2 · global 프로파일' },
  { id: 'gemma', label: 'Tier 2 · PII 경로 (데모: Gemma @ us-west-2)', sub: 'bedrock-mantle OpenAI 호환 · us-west-2 직접 호출 — GPU 미구성 대체' },
];

// 실행 전 표시용 — engine/gate.py route_info() 와 같은 문구 (실행 시 서버 값으로 교체)
const ROUTE_FALLBACK: Record<Route, { badge: Badge; modelId: string; tier: string; region: string; regionBadge: string }> = {
  claude: {
    badge: { title: '추론 경로', prod: 'Bedrock Claude (global 프로파일) · bedrock-runtime Converse · 소스 리전 ap-northeast-2',
      demo: '운영과 동일 — Bedrock Claude (global 프로파일)', substituted: false },
    modelId: 'global.anthropic.claude-sonnet-5', tier: '0/1', region: 'ap-northeast-2', regionBadge: '저장: 서울 리전 / 추론: global 라우팅',
  },
  gemma: {
    badge: { title: '추론 경로', prod: 'IDC GPU + vLLM (EKS Hybrid Nodes)',
      demo: 'Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체', substituted: true },
    modelId: 'google.gemma-4-31b', tier: '2', region: 'us-west-2', regionBadge: '저장: 서울 리전 / 추론: us-west-2 직접 호출',
  },
};
// 실행 전 표시용 — engine/gate.py gate_info() 와 같은 문구 (§16 확인 결과: 규칙 기반 토큰화 구현, ML 가명처리·재식별 볼트 미구현)
const GATE_FALLBACK: Badge = { title: '익명화 게이트', prod: '가명처리 · 토큰화 · 재식별',
  demo: '합성데이터 가명 생성 + 규칙 기반 토큰화 (ML 가명처리·재식별 볼트 미구현)' };

const STEP_LABEL: Record<string, string> = {
  route: '⓪ 추론 경로 — 어떤 모델 · 리전으로 나가는가 (§11-1)',
  guardrail_in: '① 입력 가드레일 (Bedrock Guardrails 실물)',
  semantic: '② Semantic Layer — 지표 정의 (VPC 내부)',
  lookup: '③ 정확 조회 — 조회된 원본 값 (VPC 내부)',
  calc: '④ 결정론적 계산엔진 — 계산 내역 (VPC 내부)',
  mask: '⑤ 익명화 게이트 — 규칙 기반 토큰화 · 경계 계측 (§11-2)',
  semantic_check: '⑧ Semantic 검증 — 모델이 말한 전월실적 vs 계산엔진 (출력 검증기)',
};
const OPEN_BY_DEFAULT = new Set(['route', 'lookup', 'calc', 'mask', 'semantic_check']);

const fmtKrw = (v: any) => (v === null || v === undefined || v === '' ? '—' : `${Number(v).toLocaleString()}원`);
const planeOf = (s: Stage): 'cloud' | 'onprem' | 'boundary' =>
  s.plane === 'cloud' || s.plane === 'onprem' || s.plane === 'boundary' ? s.plane
    : s.step === 'mask' ? 'boundary' : ['semantic', 'lookup', 'calc', 'semantic_check'].includes(s.step) ? 'onprem' : 'cloud';
const PLANE_UI = {
  cloud: { color: BEDROCK, chip: 'Bedrock' },
  onprem: { color: VPC, chip: 'VPC 내부' },
  boundary: { color: VPC, chip: '경계 — 유일한 통과 지점' },
};

/* ---------------- §11 배지 카드 (항상 표시) ---------------- */
function BadgeCard({ no, b, source, live, accent, children }: {
  no: string; b: Badge; source: string; live: boolean; accent: string; children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-800 p-3 text-xs" style={{ borderTop: `2px solid ${accent}` }}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-slate-500">§{no}</span>
        <b className="text-slate-200">{b.title}</b>
        <span className={`chip text-[10px] ml-auto ${live ? 'text-emerald-300 border-emerald-800' : 'text-slate-400'}`}>{source}</span>
      </div>
      <div className="grid grid-cols-[36px_1fr] gap-x-2 gap-y-0.5">
        <span className="text-slate-500">운영</span><span className="text-slate-300">{b.prod}</span>
        <span className="text-slate-500">데모</span>
        <span className={b.substituted === false ? 'text-emerald-300' : 'text-amber-300'}>{b.demo}</span>
      </div>
      {children}
    </div>
  );
}

function Switch({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled} onClick={() => onChange(!on)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-40 ${on ? 'bg-emerald-500' : 'bg-rose-600'}`}>
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  );
}

/* ---------------- Semantic 검증 패널 (§6 데모 포인트) ---------------- */
function SemanticCheck({ s }: { s: Stage }) {
  const mismatch = !!s.mismatch;
  const stated = s.modelValue !== null && s.modelValue !== undefined;
  const on = s.semanticLayer !== false;
  return (
    <div>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="chip text-[10px]" style={{ borderColor: on ? OK : ROSE, color: on ? '#6ee7b7' : '#fda4af' }}>Semantic Layer {on ? 'ON' : 'OFF'}</span>
        {mismatch
          ? <span className="chip text-[10px] font-bold text-white bg-rose-600 border-rose-400">조용히 틀림</span>
          : stated
            ? <span className="chip text-[10px] text-emerald-300 border-emerald-700">일치 — 모델 제시값 = 계산엔진 값</span>
            : <span className="chip text-[10px] text-slate-400">모델이 수치를 제시하지 않음</span>}
        {s.period && <span className="text-slate-500">직전월 {s.period.prevMonth} ({s.period.prevFrom} ~ {s.period.prevTo}) · 기준일 {s.period.refDate}</span>}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg border p-2" style={{ borderColor: VPC }}>
          <div className="text-[10px] text-slate-500">계산엔진 값 — 전월실적{s.definition ? ` (${s.definition})` : ''}</div>
          <div className="text-lg font-bold text-amber-200">{fmtKrw(s.engineValue)}</div>
          <div className="text-[10px] text-slate-500">VPC 내부 · Semantic Layer 정의로 계산</div>
        </div>
        <div className="rounded-lg border p-2" style={{ borderColor: mismatch ? ROSE : BEDROCK }}>
          <div className="text-[10px] text-slate-500">모델이 말한 전월실적 — 회신에서 파싱 (만들어내지 않음)</div>
          <div className={`text-lg font-bold ${mismatch ? 'text-rose-300' : stated ? 'text-sky-200' : 'text-slate-500'}`}>{stated ? fmtKrw(s.modelValue) : '—'}</div>
          <div className="text-[10px] text-slate-500">{on ? '정의 + 계산엔진 값을 받은 상태' : '정의 없이 거래내역 원본만 받은 상태 (안티패턴 시연)'}</div>
        </div>
      </div>
      {s.note && <div className={`mt-2 ${mismatch ? 'text-rose-300' : 'text-slate-400'}`}>{s.note}</div>}
      {Array.isArray(s.candidates) && s.candidates.length > 1 && (
        <div className="text-[10px] text-slate-500 mt-1">회신 안 '전월실적' 근처 수치 후보: {s.candidates.map((c: any) => fmtKrw(c)).join(' · ')}</div>
      )}
    </div>
  );
}

/* ---------------- 단계 패널 ---------------- */
function StagePanel({ s, boundary }: { s: Stage; boundary: Boundary }) {
  const interesting = s.step === 'guardrail_in' && s.result?.action && s.result.action !== 'NONE';
  const [open, setOpen] = useState<boolean>(OPEN_BY_DEFAULT.has(s.step) || !!interesting);
  const plane = planeOf(s);
  const ui = PLANE_UI[plane];
  const period = s.metricPeriod || {};
  return (
    <div className="panel p-3 mb-2" style={{ borderLeft: `3px solid ${ui.color}` }}>
      <button className="w-full text-left text-sm font-semibold flex items-center gap-2" onClick={() => setOpen(o => !o)}>
        <span className="text-slate-500">{open ? '▾' : '▸'}</span>
        <span>{STEP_LABEL[s.step] || s.step}</span>
        {s.cached && <span className="chip text-[10px] text-amber-300 border-amber-700">캐시 응답</span>}
        <span className="chip text-[10px] ml-auto whitespace-nowrap"
          style={{ borderColor: plane === 'boundary' ? BEDROCK : ui.color, color: ui.color }}>{ui.chip}</span>
      </button>
      {open && <div className="mt-2 text-xs text-slate-300">
        {s.step === 'route' && (
          <div className="space-y-1">
            <div>모델 ID <span className="font-mono text-sky-200">{s.modelId}</span> · Tier <b>{s.tier}</b>
              {s.endpoint && <> · 엔드포인트 <span className="font-mono">{s.endpoint}</span></>}
              {s.region && <> · 호출 리전 <span className="font-mono">{s.region}</span></>}</div>
            <div><span style={{ color: BEDROCK }}>{s.badge?.region || `저장: ${s.storageLabel || '서울 리전'} / 추론: ${s.inferenceRoutingLabel || s.inferenceRouting || '—'}`}</span>
              {s.inferenceRoutingLabel && <span className="text-slate-500"> — {s.inferenceRoutingLabel}</span>}</div>
            <div className="text-slate-500">Semantic Layer {s.semanticLayer === false ? <b className="text-rose-300">OFF (안티패턴 시연)</b> : <b className="text-emerald-300">ON</b>}
              {' '}· 이 요청의 페이로드는 아래 ⑤ 게이트를 지난 뒤에만 이 경로로 나간다</div>
            {s.badge?.implemented === false && <div className="text-rose-300">이 경로는 미구현 — 데모에서는 사용할 수 없다</div>}
          </div>
        )}
        {s.step === 'guardrail_in' && s.result && (
          <div>판정: <b className={s.result.action === 'NONE' ? 'text-emerald-400' : 'text-rose-400'}>{s.result.action}</b>
            {s.result.topics?.length > 0 && <> · 토픽: {s.result.topics.join(', ')}</>}
            {s.result.pii?.length > 0 && <> · PII: {s.result.pii.map((p: any) => p.type).join(', ')}</>}</div>
        )}
        {s.step === 'semantic' && (
          <div>
            {s.metric
              ? <div>지표 <b className="text-sky-300">{s.metric.name}</b> ({s.metric.unit}, 소관 {s.metric.ownerDept})
                <pre className="mt-1 bg-slate-950 rounded p-2 overflow-x-auto">{s.metric.sql}</pre></div>
              : <div className="text-amber-300">{s.note}</div>}
            <div className="mt-2 border-t border-slate-800 pt-2">
              {s.semanticLayer !== false
                ? (s.prevMonthMetric
                  ? <div><b className="text-amber-300">{s.prevMonthMetric.name}</b> 정의 (Semantic Layer): <b>{s.prevMonthMetric.description}</b>
                    <span className="text-slate-500"> · 소관 {s.prevMonthMetric.ownerDept} · 단위 {s.prevMonthMetric.unit}</span>
                    <div className="text-slate-500 mt-0.5">이 정의와 계산엔진 값이 프롬프트에 들어간다 — LLM 은 인용만 한다 (§12.4)</div></div>
                  : <div className="text-amber-300">전월실적 정의를 Semantic Layer 에서 찾지 못했습니다</div>)
                : <div className="text-rose-300">{s.toggleNote || "Semantic Layer OFF — '전월실적' 정의를 모델에 제공하지 않는다 (안티패턴 시연)"}</div>}
            </div>
          </div>
        )}
        {s.step === 'lookup' && (
          <div>
            <table className="w-full"><tbody>{Object.entries((s.values || {}) as Record<string, string>).map(([k, v]) => (
              <tr key={k}><td className="text-slate-500 pr-3 py-0.5 whitespace-nowrap">{k}</td><td>{v}</td></tr>
            ))}</tbody></table>
            {s.source && <div className="text-[10px] text-slate-500 mt-1">출처: {s.source}</div>}
            {Array.isArray(s.txnSample) && (
              <div className="mt-2 border-t border-slate-800 pt-2">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <b className="text-slate-200">합성 카드 거래내역</b>
                  <span className="text-slate-500">직전월 {period.prevMonth || '—'} 전체 + 당월 {period.currentMonth || '—'} (기준일 {period.refDate || '—'}) · {s.txnSample.length}건 · 가맹점명은 가상(§9)</span>
                  {s.metricByEngine && <>
                    <span className="chip text-[10px]" style={{ borderColor: VPC }}>전월실적 <b className="text-amber-200 ml-1">{fmtKrw(s.metricByEngine['전월실적'])}</b></span>
                    <span className="chip text-[10px]" style={{ borderColor: VPC }}>당월실적 <b className="text-amber-200 ml-1">{fmtKrw(s.metricByEngine['당월실적'])}</b></span>
                  </>}
                </div>
                <div className="max-h-48 overflow-y-auto rounded border border-slate-800">
                  <table className="w-full text-[11px]">
                    <thead className="text-slate-500 sticky top-0 bg-slate-900"><tr>
                      <th className="text-left px-2 py-0.5">일자</th><th className="text-left px-2">가맹점</th><th className="text-right px-2">금액</th><th className="text-right px-2">상태</th>
                    </tr></thead>
                    <tbody>{s.txnSample.map((t: any, i: number) => {
                      const cancelled = t.status === 'CANCELLED';
                      return (
                        <tr key={i} className={cancelled ? 'text-slate-500' : ''}>
                          <td className="px-2 py-0.5 font-mono">{t.date}</td><td className="px-2">{t.merchant}</td>
                          <td className={`px-2 text-right font-mono ${cancelled ? 'line-through' : ''}`}>{Number(t.amountKrw).toLocaleString()}</td>
                          <td className={`px-2 text-right ${cancelled ? 'text-rose-400' : 'text-emerald-400'}`}>{cancelled ? '취소' : '승인'}</td>
                        </tr>
                      );
                    })}</tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
        {s.step === 'calc' && (
          <div>
            {['rate', 'limit'].filter(k => s[k]).map(key => (
              <div key={key} className="mb-2">
                <b className="text-amber-300">{s[key].name}</b> = <b>{key === 'limit' ? Number(s[key].value).toLocaleString() + '원' : s[key].value + '%'}</b>
                <table className="w-full mt-1"><tbody>{(s[key].steps || []).map((st: any, i: number) => (
                  <tr key={i}><td className="text-slate-500 pr-2 py-0.5">{st.label}</td>
                    <td className="font-mono text-slate-400 pr-2">{st.formula}</td>
                    <td className="font-mono">{st.value}</td></tr>
                ))}</tbody></table>
              </div>
            ))}
            <div className="border-t border-slate-800 pt-2">
              <b className="text-amber-300">Semantic Layer 지표 (계산엔진 산출)</b>
              {s.metricByEngine
                ? <table className="w-full mt-1"><tbody>{Object.entries(s.metricByEngine as Record<string, number>).map(([k, v]) => (
                  <tr key={k}><td className="text-slate-500 pr-2 py-0.5 whitespace-nowrap">{k}</td>
                    <td className="text-slate-400 pr-2">{s.metricDefinitions?.[k] || ''}</td>
                    <td className="font-mono text-right whitespace-nowrap">{fmtKrw(v)}</td></tr>
                ))}</tbody></table>
                : <div className="text-rose-300 mt-1">미제공 — VPC 내부 플레인이 metricByEngine 을 돌려주지 않았습니다 (구버전 플레인 · 재배포 필요)</div>}
            </div>
          </div>
        )}
        {s.step === 'mask' && (
          <div>
            <div className="mb-1">토큰화된 필드: {(s.maskedFields || []).map((f: any) => (
              <span key={f.token} className="chip text-[10px] mr-1" style={{ borderColor: VPC }}>{f.field} → <span className="font-mono">{f.token}</span></span>
            ))}{(s.maskedFields || []).length === 0 && <span className="text-slate-500">없음</span>}</div>
            <div className="mb-2">경계 통과 개인식별자 — 독립 스캔({(s.piiDetectors || []).join(' + ')}):{' '}
              <b className={s.piiOutbound === 0 ? 'text-emerald-400' : 'text-rose-400'}>{s.piiOutbound}건</b>
              {s.piiHits?.length > 0 && <span className="text-rose-300 ml-2">{s.piiHits.map((h: any) => h.type).join(', ')}</span>}
              {s.badge?.refuseTypes?.length > 0 && <span className="text-slate-500 ml-2">게이트 차단 유형: {s.badge.refuseTypes.join(' · ')}</span>}
            </div>
            {s.badge && (
              <div className="mb-2">
                <BadgeCard no="11-2" b={s.badge} source="서버 게이트 값" live accent={VPC}>
                  {s.badge.detector && <div className="text-[10px] text-slate-500 mt-1">탐지기: {s.badge.detector}</div>}
                </BadgeCard>
              </div>
            )}
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <b className="text-slate-200">경계를 넘은 실제 페이로드</b>
              <span className="chip text-[10px]" style={{ borderColor: BEDROCK, color: BEDROCK }}>Bedrock 입력 (user)</span>
              {boundary
                ? <>
                  <span className="chip text-[10px]">{Number(boundary.chars || 0).toLocaleString()}자 · ≈{Number(boundary.estTokens || 0).toLocaleString()} 토큰 (게이트 실측)</span>
                  <span className="chip text-[10px]" style={{ borderColor: boundary.piiRules?.count ? ROSE : OK }}>게이트 규칙 스캔 {boundary.piiRules?.count ?? 0}건</span>
                </>
                : <span className="text-[10px] text-slate-500">게이트 계측 대기 중…</span>}
            </div>
            {boundary && Array.isArray(boundary.fieldsPassed) && (
              <div className="mb-1">전달 필드 ({boundary.fieldsPassed.length}): {boundary.fieldsPassed.map(f => (
                <span key={f} className="chip text-[10px] mr-1 mb-1" style={{ borderColor: BEDROCK }}>{f}</span>
              ))}{boundary.fieldsPassed.length === 0 && <span className="text-slate-500">없음</span>}</div>
            )}
            <pre className="bg-slate-950 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-72 overflow-y-auto">{s.maskedPayload}</pre>
            {s.systemPromptChars != null && <div className="text-[10px] text-slate-500 mt-1">시스템 프롬프트 {Number(s.systemPromptChars).toLocaleString()}자는 별도 블록으로 함께 전달된다 (게이트 계측에 포함)</div>}
          </div>
        )}
        {s.step === 'semantic_check' && <SemanticCheck s={s} />}
      </div>}
    </div>
  );
}

/* ---------------- 화면 ---------------- */
export default function S2() {
  const [query, setQuery] = useState(S2_PRESET);
  const [route, setRoute] = useState<Route>('claude');
  const [semanticOn, setSemanticOn] = useState(true);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<Stage[]>([]);
  const [text, setText] = useState('');
  const [done, setDone] = useState<any>(null);
  const [err, setErr] = useState('');
  const [cached, setCached] = useState('');
  const [routeInfo, setRouteInfo] = useState<Stage | null>(null);   // 마지막 'route' 스테이지 (서버 게이트 값)
  const [gateBadge, setGateBadge] = useState<Badge | null>(null);    // 마지막 'mask' 스테이지 배지 (서버 게이트 값)

  const run = async (q?: string) => {
    const qq = q ?? query;
    if (running || !qq.trim()) return;
    if (q) setQuery(q);
    setRunning(true); setErr(''); setStages([]); setText(''); setDone(null); setCached(''); setRouteInfo(null);
    try {
      await sock.run('s2', { query: qq, route, semanticLayer: semanticOn }, (e) => {
        if (e.type === 'cache.replay') { setCached(e.reason || '캐시 응답'); setStages([]); setText(''); setDone(null); return; }
        if (e.type === 's2.stage') {
          const s = e as unknown as Stage;
          setStages(prev => [...prev, s]);
          if (s.step === 'route') setRouteInfo(s);
          if (s.step === 'mask' && s.badge) setGateBadge(s.badge as Badge);
        }
        if (e.type === 's2.token') setText(t => t + e.t);
        if (e.type === 's2.done') setDone(e);
      });
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  const fb = ROUTE_FALLBACK[route];
  const live = routeInfo && routeInfo.route === route ? routeInfo : null;     // 선택 경로와 다른 이전 실행 값은 쓰지 않는다
  const routeBadge: Badge = live?.badge || fb.badge;
  const boundary: Boundary = done?.boundary || null;
  const chk = done?.semanticCheck;

  return (
    <div>
      <div className="panel p-3 mb-3 flex flex-col gap-2">
        <div className="flex gap-2 items-center">
          <button className="chip whitespace-nowrap hover:border-sky-500 text-sky-300" onClick={() => run(S2_PRESET)}>시나리오 S2</button>
          <button className="chip whitespace-nowrap hover:border-rose-500 text-rose-300" onClick={() => run(S5_PRESET)}>S5 차단 시연</button>
          <input className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
            value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()} />
          <button onClick={() => run()} disabled={running}
            className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
            {running ? '상담 중…' : '상담 실행'}
          </button>
        </div>
        <div className="flex items-center gap-3 flex-wrap text-xs">
          <span className="text-slate-500">추론 경로</span>
          {ROUTES.map(r => (
            <button key={r.id} disabled={running} onClick={() => setRoute(r.id)} title={r.sub}
              className={`chip whitespace-nowrap disabled:opacity-50 ${route === r.id
                ? (r.id === 'gemma' ? 'border-rose-500 text-rose-200 bg-rose-950/40' : 'border-sky-500 text-sky-200 bg-sky-950/40')
                : 'text-slate-400 hover:border-slate-500'}`}>
              {route === r.id ? '● ' : '○ '}{r.label}
            </button>
          ))}
          <span className="mx-1 text-slate-700">|</span>
          <span className="text-slate-500">Semantic Layer</span>
          <Switch on={semanticOn} onChange={setSemanticOn} disabled={running} />
          <span className={semanticOn ? 'text-emerald-300' : 'text-rose-300'}>
            {semanticOn ? 'ON — 전월실적 정의 + 계산엔진 값을 모델에 제공' : "OFF — 정의 없이 거래내역 원본만 제공, 모델이 '전월실적'을 스스로 계산 (안티패턴 시연)"}
          </span>
          <span className="ml-auto text-slate-500">
            <span style={{ color: VPC }}>■ VPC 내부</span> (정확 조회 · 계산 · 토큰화 · 재식별) / <span style={{ color: BEDROCK }}>■ Bedrock</span> (익명화 게이트 통과 후)
          </span>
        </div>
      </div>

      {/* §11 배지 — 항상 표시 (툴팁 아님). 실행 전에는 기본 문구, 실행하면 서버 게이트 값 */}
      <div className="grid grid-cols-2 gap-3 mb-3">
        <BadgeCard no="11-1" b={routeBadge} source={live ? '서버 게이트 값' : '기본 문구 — 실행 시 서버 값으로 갱신'} live={!!live} accent={route === 'gemma' ? ROSE : BEDROCK}>
          <div className="mt-1 text-[11px] text-slate-400">
            모델 ID <span className="font-mono text-sky-200">{live?.modelId || fb.modelId}</span> · Tier {live?.tier || fb.tier}
            {' '}· 호출 리전 <span className="font-mono">{live?.region || fb.region}</span>
            {' '}· <span style={{ color: BEDROCK }}>{live?.badge?.region || fb.regionBadge}</span>
          </div>
          {route === 'claude' && (
            <div className="mt-1 text-[11px] text-slate-500">Tier 2 PII 추론 경로는 이 요청에서 미사용 — 선택하면 운영(IDC GPU + vLLM, EKS Hybrid Nodes) 대신 데모 대체(Bedrock Gemma 4 31B @ us-west-2, GPU 미구성)로 나간다.</div>
          )}
          {route === 'gemma' && (
            <div className="mt-1 text-[11px] text-rose-300">이 경로는 IDC GPU 가 아니다 — us-west-2 의 Bedrock Gemma 로 직접 호출된다 (교차 리전 추론 미지원).</div>
          )}
        </BadgeCard>
        <BadgeCard no="11-2" b={gateBadge || GATE_FALLBACK} source={gateBadge ? '서버 게이트 값' : '기본 문구 — 실행 시 서버 값으로 갱신'} live={!!gateBadge} accent={VPC}>
          <div className="mt-1 text-[11px] text-slate-500">게이트는 경계 통과 지점으로 실제 존재한다 — 모든 모델 호출 페이로드를 여기서 실측(문자 · 추정 토큰 · 전달 필드 · 규칙 기반 PII)하고, 식별자가 남아 있으면 Bedrock 에 보내지 않는다.</div>
        </BadgeCard>
      </div>

      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}
      {cached && <div className="mb-3 text-xs"><span className="chip text-amber-300 border-amber-700">캐시 응답</span> <span className="text-slate-400">{cached} — 실시간 응답이 아닙니다 (이전 실행 결과 재생)</span></div>}

      <div className="grid grid-cols-[1fr_1fr] gap-4">
        <div>
          <div className="text-xs text-slate-500 mb-2">파이프라인 단계 (펼쳐보기) — 왼쪽 띠 색: <span style={{ color: VPC }}>■ VPC 내부</span> / <span style={{ color: BEDROCK }}>■ Bedrock</span></div>
          {stages.length === 0 && !running && <div className="text-xs text-slate-600 panel p-3">실행하면 ⓪ 추론 경로부터 ⑧ Semantic 검증까지 단계가 여기에 쌓인다.</div>}
          {stages.map((s, i) => <StagePanel key={i} s={s} boundary={boundary} />)}
          {running && <div className="text-xs text-slate-500 mt-1"><span className="blink">▌</span> 진행 중…</div>}
        </div>
        <div className="panel p-4">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: BEDROCK }} />
            <b>⑥ LLM 설명 (숫자는 계산엔진 확정값만) → ⑦ 출력 가드레일 → 재식별</b>
            <span className="chip text-[10px] ml-auto" style={{ borderColor: route === 'gemma' ? ROSE : BEDROCK }}>
              {ROUTES.find(r => r.id === route)?.label}
            </span>
          </div>
          {done?.blocked
            ? (done.gateRefused
              ? <div className="text-rose-300 text-sm border border-rose-900 rounded-lg p-3">
                <div className="font-semibold">⛔ 익명화 게이트 차단 — 페이로드가 Bedrock 에 전달되지 않았습니다</div>
                <div className="text-xs mt-1 text-rose-200">{done.message}</div>
                {Array.isArray(done.hits) && done.hits.length > 0 && (
                  <div className="text-xs mt-1">식별자 유형: {done.hits.map((h: any) => `${h.type} ×${h.count}`).join(', ')} <span className="text-slate-500">(값은 기록하지 않음)</span></div>
                )}
                <div className="text-[10px] text-slate-500 mt-1">경로 {done.route} · 모델 {done.modelId} 로 나갈 예정이었음 · Single Boundary 뷰에 '게이트 거부'로 기록</div>
              </div>
              : <div className="text-rose-300 text-sm border border-rose-900 rounded-lg p-3">
                🛡 Bedrock Guardrails 차단: {done.message}
                {done.topics?.length > 0 && <div className="text-xs mt-1 text-rose-200">토픽: {done.topics.join(', ')}</div>}
              </div>)
            : <div className="md text-slate-200">{text}{running && !done && <span className="blink">▌</span>}</div>}
          {done && !done.blocked && (
            <div className="mt-3 text-xs space-y-1">
              {done.guardrailOut && <div>출력 가드레일: <b className={done.guardrailOut.action === 'NONE' ? 'text-emerald-400' : 'text-rose-400'}>{done.guardrailOut.action}</b>
                {done.guardrailOut.grounding?.length > 0 && <span className="text-slate-400 ml-2">근거 점수 {done.guardrailOut.grounding.map((g: any) => `${g.type} ${g.score}`).join(' · ')}</span>}</div>}
              <div className="text-slate-400">플레인: <span style={{ color: done.plane === 'bridge' || done.plane === 'direct' ? VPC : ROSE }}>{done.planeLabel}</span>
                {done.usage && <> · 실측 토큰 {Number(done.usage.inputTokens || 0).toLocaleString()}/{Number(done.usage.outputTokens || 0).toLocaleString()}</>}</div>
              <div>{done.inventedNumbers?.length
                ? <span className="text-rose-400">⚠ 계산엔진에 없는 수치 발견: {done.inventedNumbers.join(', ')}</span>
                : <span className="text-emerald-400">✓ 수치 검증 통과 — 모든 숫자가 계산엔진 출력(또는 제공된 원장 값)과 일치</span>}</div>
              {chk && (
                <div>Semantic 검증 (전월실적): {chk.mismatch
                  ? <span className="text-rose-300">모델 {fmtKrw(chk.modelValue)} ≠ 계산엔진 {fmtKrw(chk.engineValue)} — <b className="chip text-[10px] text-white bg-rose-600 border-rose-400">조용히 틀림</b></span>
                  : chk.modelValue != null
                    ? <span className="text-emerald-400">모델 {fmtKrw(chk.modelValue)} = 계산엔진 {fmtKrw(chk.engineValue)}</span>
                    : <span className="text-slate-400">{chk.note || '모델이 수치를 제시하지 않음'}</span>}
                  <span className="text-slate-500 ml-1">· Semantic Layer {done.semanticLayer === false ? 'OFF' : 'ON'}</span></div>
              )}
              {done.modelId && (
                <div className="text-slate-400">모델 ID <span className="font-mono text-sky-200">{done.modelId}</span> · Tier {done.tier}
                  {' '}· <span style={{ color: BEDROCK }}>{done.regionBadge || `저장: ${done.storageLabel || '서울 리전'} / 추론: ${done.inferenceRoutingLabel || done.inferenceRouting || '—'}`}</span>
                  {done.nonStream && <span className="text-amber-300 ml-1">· 비스트림 응답 (어댑터 폴백)</span>}</div>
              )}
              <div className="text-slate-500">traceId {done.traceId} · {done.elapsedMs}ms — Single Boundary 뷰에 계측 기록됨{done.cached && ' · 캐시 응답'}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
