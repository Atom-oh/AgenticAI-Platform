// S3 화면 생성 — Registry 승인 컴포넌트만으로 React 코드를 생성하고 실제 검증 게이트(tsc/eslint/axe) 결과를 보인다.
// 승인 상태가 결과를 바꾼다: Registry 에서 Button v2 → DEPRECATED, v3 → APPROVED 로 바꾸고 재생성하면 다른 코드가 나온다.
import { useEffect, useRef, useState } from 'react';
import { sock, WsEvent } from '../lib';

export const S3_PRESET = '여신 심사 결과 조회 화면을 만들어줘';

type Prop = { name: string; type: string; required: boolean };
type Comp = { name: string; version: string; module: string; exportName: string; props: Prop[];
  description?: string; supersededBy?: string | null };
type Stage = { step: string; attempt?: number; [k: string]: any };
type Gate = { ok: boolean | null; note?: string; [k: string]: any };
type GateKey = 'build' | 'types' | 'lint' | 'a11y' | 'visual' | 'registry';
type Gates = Record<GateKey, Gate> & { ok: boolean | null; runner?: string };
type Used = { name: string; version: string; module: string };
type Result = { code: string; componentsUsed: Used[]; gates: Gates; attempts: number; ok: boolean | null;
  regenerated?: boolean; maxRegenerations?: number; usage?: { inputTokens: number; outputTokens: number };
  history?: { attempt: number; code: string; gates: Gates; componentsUsed: Used[]; ok: boolean | null }[];
  reasons?: string[]; componentsSource?: string; componentCount?: number; skills?: string[]; skillsMissing?: string[];
  piiOutbound?: number; gatesRunner?: string; model?: string; cached?: boolean; traceId?: string; elapsedMs?: number;
  runAt?: string; approvedButton?: string };

const STEP_LABEL: Record<string, string> = {
  registry_lookup: '① Registry 정확 조회 — APPROVED 컴포넌트 + propsSchema (벡터 검색 아님)',
  skills: '② 스킬 로드 — 퍼블리싱 규약 · KWCAG 2.2 · 출력 계약',
  generate: '③ Bedrock 코드 생성 (토큰 스트리밍)',
  gates: '④ 검증 게이트 실행 — 빌드 / 타입 / 린트 / KWCAG / 시각(구조) / Registry',
  regenerate: '⑤ 재생성 — 실패 사유를 컨텍스트에 주입 (1회 상한)',
};
const STEP_WHERE: Record<string, string> = {
  registry_lookup: 'Registry · 플랫폼', skills: '플랫폼', generate: '모델 호출 · Bedrock',
  gates: '게이트 Lambda · 플랫폼', regenerate: '모델 호출 · Bedrock',
};
const GATE_TILES: { key: GateKey; label: string; sub: string }[] = [
  { key: 'build', label: '빌드', sub: 'TypeScript 구문 · 트랜스파일' },
  { key: 'types', label: '타입', sub: 'tsc --noEmit strict · 승인 propsSchema 선언' },
  { key: 'lint', label: '린트', sub: 'eslint · import 제한 · fetch/localStorage 금지' },
  { key: 'a11y', label: 'KWCAG 접근성', sub: 'axe-core wcag2a/aa · 스텁 렌더링' },
  { key: 'visual', label: '시각 회귀 (구조)', sub: '구조 스냅샷 비교 — 픽셀 비교 미구현' },
  { key: 'registry', label: 'Registry 승인', sub: '이름@버전 정확 일치 · 헤더/import' },
];
const RUNNER_LABEL: Record<string, string> = {
  lambda: '게이트 Lambda (Node 20)', local: '로컬 node 실행', none: '미연결 — 미판정', error: '실행기 오류 — 미판정',
};

function okIcon(ok: boolean | null | undefined) {
  if (ok === true) return <span className="text-emerald-600 font-bold">✓</span>;
  if (ok === false) return <span className="text-[#E90061] font-bold">✗</span>;
  return <span className="text-slate-500 font-bold">—</span>;
}
function okText(ok: boolean | null | undefined) {
  return ok === true ? '통과' : ok === false ? '실패' : '미판정';
}

/* ---------------- 사용 컴포넌트 배지 (Button 버전을 크게) ---------------- */
function UsedBadges({ used, size = 'md' }: { used: Used[]; size?: 'sm' | 'md' }) {
  if (!used?.length) return <span className="text-xs text-slate-500">사용 컴포넌트 없음</span>;
  const sorted = [...used].sort((a, b) => (a.name === 'Button' ? -1 : b.name === 'Button' ? 1 : a.name.localeCompare(b.name)));
  return (
    <div className="flex flex-wrap gap-1.5 items-center">
      {sorted.map(u => u.name === 'Button'
        ? <span key={u.name + u.version} className={`chip font-bold ${size === 'md' ? 'text-sm px-3 py-1' : 'text-xs'}`}
            style={{ borderColor: 'var(--cloud)', background: 'rgba(56,189,248,.12)', color: '#bae6fd' }}>
            Button <span className="ml-1 text-teal-700">{u.version}</span></span>
        : <span key={u.name + u.version} className={`chip ${size === 'md' ? 'text-xs' : 'text-[10px]'} text-slate-700`}>{u.name}@{u.version}</span>)}
    </div>
  );
}

/* ---------------- 게이트 타일 ---------------- */
function GateTile({ label, sub, gate, k }: { label: string; sub: string; gate?: Gate; k: GateKey }) {
  const [open, setOpen] = useState(false);
  const ok = gate?.ok;
  const border = ok === true ? 'var(--ok)' : ok === false ? '#f43f5e' : '#334155';
  const count = k === 'a11y' ? gate?.violations?.length : k === 'registry' ? gate?.problems?.length : gate?.errors?.length;
  return (
    <div className="panel p-3 text-left" style={{ borderTop: `2px solid ${border}` }}>
      <button className="w-full text-left" onClick={() => setOpen(o => !o)}>
        <div className="flex items-center gap-2">
          <span className="text-lg">{okIcon(ok)}</span>
          <span className="font-semibold text-sm">{label}</span>
          <span className="ml-auto text-[11px] text-slate-400">{okText(ok)}{typeof count === 'number' && count > 0 ? ` · ${count}건` : ''}</span>
        </div>
        <div className="text-[10px] text-slate-500 mt-1">{sub}</div>
        {ok == null && gate?.note && <div className="text-[10px] text-amber-700 mt-1">{gate.note}</div>}
        {k === 'visual' && ok === true && (
          <div className="text-[11px] mt-1 text-slate-700">
            {gate?.changed === true ? <span className="text-amber-700">이전 실행과 구조 변경 감지</span>
              : gate?.changed === false ? <span className="text-emerald-600">이전 실행과 구조 동일</span>
              : <span className="text-slate-500">기준선 없음 (첫 실행)</span>}
          </div>
        )}
        {gate && <div className="text-[10px] text-slate-400 mt-1">{open ? '▾ 상세 닫기' : '▸ 상세 보기'}</div>}
      </button>
      {open && gate && <GateDetail k={k} gate={gate} />}
    </div>
  );
}

function GateDetail({ k, gate }: { k: GateKey; gate: Gate }) {
  return (
    <div className="mt-2 text-[11px] text-slate-700 space-y-1 max-h-64 overflow-y-auto">
      {gate.note && <div className="text-slate-400">{gate.note}</div>}
      {(k === 'build' || k === 'types' || k === 'lint') && (gate.errors || []).map((e: any, i: number) => (
        <div key={i} className="font-mono bg-slate-50 rounded p-1.5 whitespace-pre-wrap break-words">
          <span className="text-[#E90061]">{e.file || 'Screen.tsx'}{e.line ? `:${e.line}` : ''}</span>
          {e.ruleId && <span className="text-amber-700"> [{e.ruleId}]</span>} {e.message}
        </div>
      ))}
      {k === 'types' && gate.declaredModules && (
        <div className="text-slate-500">선언된 승인 모듈: {gate.declaredModules.join(', ')}</div>
      )}
      {k === 'lint' && (gate.warnings || []).length > 0 && (
        <div className="text-slate-500">경고 {gate.warnings.length}건 (실패 아님)</div>
      )}
      {k === 'a11y' && (gate.violations || []).map((v: any, i: number) => (
        <div key={i} className="bg-slate-50 rounded p-1.5">
          <div><span className="text-[#E90061] font-mono">{v.id}</span> <span className="text-slate-400">{v.impact}</span>
            {v.kwcag && <span className="chip text-[10px] ml-1" style={{ borderColor: 'var(--onprem)' }}>KWCAG {v.kwcag}</span>}
            <span className="text-slate-500 ml-1">({v.source === 'kwcag-check' ? '구조 검사' : 'axe'})</span></div>
          <div>{v.help}</div>
          {(v.nodes || []).slice(0, 3).map((n: any, j: number) => (
            <div key={j} className="font-mono text-slate-500 truncate">{n.target} {n.html}</div>
          ))}
        </div>
      ))}
      {k === 'a11y' && gate.ok !== null && (
        <div className="text-slate-500">
          통과 규칙 {gate.passes ?? '—'}개 · 미판정(incomplete) {gate.incomplete?.length ?? 0}개
          {gate.incomplete?.some((x: any) => x.id === 'color-contrast') && ' — 명도 대비는 jsdom 레이아웃 미지원으로 미판정'}
          <div>{gate.kwcagNote}</div>
        </div>
      )}
      {k === 'visual' && gate.snapshot && (
        <div>
          <div className="text-slate-500 font-mono">hash {String(gate.snapshot.hash).slice(0, 16)}… · 노드 {gate.snapshot.nodeCount}개 · 텍스트 {gate.snapshot.textLength}자</div>
          {gate.diff && Object.keys(gate.diff).length > 0 && (
            <table className="mt-1 w-full">
              <tbody>{Object.entries(gate.diff as Record<string, { before: number; after: number }>).map(([tag, d]) => (
                <tr key={tag}><td className="font-mono text-slate-400 pr-2">&lt;{tag}&gt;</td>
                  <td className="text-slate-500">{d.before}</td><td className="text-slate-500 px-1">→</td><td>{d.after}</td></tr>
              ))}</tbody>
            </table>
          )}
          <div className="text-slate-500 mt-1">태그 개수: {Object.entries(gate.snapshot.tagCounts || {}).map(([t, c]) => `${t}:${c}`).join(' ')}</div>
        </div>
      )}
      {k === 'registry' && (
        <div>
          <div className="mb-1"><UsedBadges used={gate.used || []} size="sm" /></div>
          {(gate.problems || []).map((p: string, i: number) => <div key={i} className="text-[#E90061]">✗ {p}</div>)}
          {(gate.warnings || []).map((w: string, i: number) => <div key={i} className="text-amber-700">△ {w}</div>)}
        </div>
      )}
    </div>
  );
}

/* ---------------- 파이프라인 단계 패널 (전 단계 클라우드 — 시안) ---------------- */
function StagePanel({ s }: { s: Stage }) {
  const [open, setOpen] = useState(s.step === 'regenerate' || s.step === 'registry_lookup');
  return (
    <div className="panel p-3 mb-2" style={{ borderLeft: '3px solid var(--cloud)' }}>
      <button className="w-full text-left text-sm font-semibold flex items-center gap-2" onClick={() => setOpen(o => !o)}>
        <span className="text-slate-500">{open ? '▾' : '▸'}</span>
        <span className="flex-1">{STEP_LABEL[s.step] || s.step}{s.attempt ? <span className="text-slate-500 font-normal"> · 시도 {s.attempt}</span> : null}</span>
        <span className="chip text-[10px]" style={{ borderColor: 'var(--cloud)' }}>{STEP_WHERE[s.step] || '클라우드'}</span>
      </button>
      {open && <div className="mt-2 text-xs text-slate-700 space-y-1">
        {s.step === 'registry_lookup' && (
          <div>승인 컴포넌트 <b className="text-teal-700">{s.count}개</b> · 조회 방식 <b>정확 조회(exact)</b> · 출처{' '}
            {s.source === 'registry' ? <b className="text-emerald-600">Registry Consumer API</b>
              : <b className="text-amber-700">픽스처 (Registry 미연결 — 기준선 목록)</b>}
            <div className="text-slate-500 mt-1">{(s.components || []).map((c: Comp) => `${c.name}@${c.version}`).join(', ')}</div>
          </div>
        )}
        {s.step === 'skills' && (
          <div>{(s.names || []).map((n: string) => <span key={n} className="chip text-[10px] mr-1">{n}</span>)}
            <span className="text-slate-500">{s.chars?.toLocaleString()}자</span>
            {s.missing?.length > 0 && <div className="text-[#E90061]">누락 스킬: {s.missing.join(', ')}</div>}</div>
        )}
        {s.step === 'generate' && (
          <div>모델 <span className="font-mono text-slate-400">{s.model}</span> · 시스템 프롬프트 {s.systemChars?.toLocaleString()}자 · 요청 {s.userChars?.toLocaleString()}자
            {s.piiOutbound != null && <> · 경계 페이로드 PII 실측 <b className={s.piiOutbound === 0 ? 'text-emerald-600' : 'text-[#E90061]'}>{s.piiOutbound}건</b></>}</div>
        )}
        {s.step === 'gates' && (
          <div>실행기 <b>{RUNNER_LABEL[s.runner] || s.runner || '—'}</b> · 종합 {okIcon(s.ok)} {okText(s.ok)}
            <div className="text-slate-500 mt-1">
              {GATE_TILES.map(t => <span key={t.key} className="mr-2">{t.label} {okIcon(s.results?.[t.key]?.ok)}</span>)}
            </div>
          </div>
        )}
        {s.step === 'regenerate' && (
          <div><div className="text-amber-700 mb-1">재생성 {(s.attempt ?? 2) - 1}/{s.limit}회 — 아래 사유를 컨텍스트에 넣어 다시 생성 (무한 루프 금지 §12.9)</div>
            {(s.reasons || []).map((r: string, i: number) => <div key={i} className="font-mono text-[11px] bg-slate-50 rounded p-1 break-words">{r}</div>)}</div>
        )}
      </div>}
    </div>
  );
}

/* ---------------- 결과 요약 카드 (이전/현재 비교용) ---------------- */
function ResultSummary({ r, title, accent }: { r: Result; title: string; accent: string }) {
  const [showCode, setShowCode] = useState(false);
  return (
    <div className="panel p-3" style={{ borderTop: `2px solid ${accent}` }}>
      <div className="flex items-center gap-2 mb-2">
        <b className="text-sm">{title}</b>
        {r.cached && <span className="chip text-[10px] text-amber-700" style={{ borderColor: 'var(--onprem)' }}>캐시 응답</span>}
        <span className="ml-auto text-[10px] text-slate-500">{r.runAt}</span>
      </div>
      <UsedBadges used={r.componentsUsed} />
      <div className="flex items-center gap-2 mt-2 text-[11px] text-slate-400 flex-wrap">
        <span>종합 {okIcon(r.ok)} {okText(r.ok)}</span>
        <span>· 시도 {r.attempts}회{r.regenerated ? ' (재생성 1회)' : ''}</span>
        <span>· {r.code.split('\n').length}줄</span>
        {r.approvedButton && <span>· 승인 Button: <b className="text-teal-700">{r.approvedButton}</b></span>}
      </div>
      <div className="mt-1 text-[11px] text-slate-500">{GATE_TILES.map(t => <span key={t.key} className="mr-2">{t.label} {okIcon(r.gates?.[t.key]?.ok)}</span>)}</div>
      <button className="chip text-[10px] mt-2 hover:border-slate-400" onClick={() => setShowCode(s => !s)}>{showCode ? '코드 접기' : '코드 보기'}</button>
      {showCode && <pre className="mt-2 bg-slate-50 rounded p-2 text-[10px] leading-4 overflow-auto max-h-72 font-mono">{r.code}</pre>}
    </div>
  );
}

/* ---------------- 메인 뷰 ---------------- */
export default function ScreenGen() {
  const [prompt, setPrompt] = useState(S3_PRESET);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState('');
  const [comps, setComps] = useState<Comp[] | null>(null);
  const [compsMeta, setCompsMeta] = useState<{ source?: string; gatesRunner?: string; count?: number }>({});
  const [stages, setStages] = useState<Stage[]>([]);
  const [stream, setStream] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [prev, setPrev] = useState<Result | null>(null);
  const [cached, setCached] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const codeRef = useRef<HTMLPreElement | null>(null);

  const loadComps = () => {
    sock.request('screengen_components').then((e: WsEvent) => {
      setComps(e.components || []);
      setCompsMeta({ source: e.source, gatesRunner: e.gatesRunner, count: e.count });
    }).catch((e: Error) => setErr(e.message));
  };
  useEffect(() => { loadComps(); }, []);
  useEffect(() => { if (running && codeRef.current) codeRef.current.scrollTop = codeRef.current.scrollHeight; }, [stream, running]);

  const approvedButton = (list: Comp[] | null) => (list || []).filter(c => c.name === 'Button').map(c => c.version).join(', ') || '없음';

  const run = async (q?: string) => {
    const qq = q ?? prompt;
    if (running || !qq.trim()) return;
    if (q) setPrompt(q);
    if (result) setPrev(result);                          // 이전 결과를 옆에 남긴다 (반전 비교)
    setRunning(true); setErr(''); setStages([]); setStream(''); setResult(null); setCached(false); setAttempt(0); setCopied(false);
    let seenButton = approvedButton(comps);
    try {
      await sock.run('screengen', { prompt: qq, previousSnapshot: result?.gates?.visual?.snapshot }, (e: WsEvent) => {
        if (e.type === 'cache.replay') { setCached(true); setStages([]); setStream(''); }
        if (e.type === 'screengen.stage') {
          setStages(s => [...s, e as unknown as Stage]);
          if (e.step === 'generate') { setAttempt(e.attempt || 1); setStream(''); }
          if (e.step === 'registry_lookup' && Array.isArray(e.components)) {
            setComps(e.components); setCompsMeta(m => ({ ...m, source: e.source, count: e.count }));
            seenButton = approvedButton(e.components);
          }
        }
        if (e.type === 'screengen.token') setStream(t => t + e.t);
        if (e.type === 'screengen.done') {
          setResult({ ...(e as unknown as Result), cached: !!e.cached, runAt: new Date().toLocaleTimeString('ko-KR'), approvedButton: seenButton });
        }
      });
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  const copy = async () => {
    const text = result?.code || stream;
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { setErr('클립보드 복사에 실패했습니다.'); }
  };

  const shownCode = result?.code ?? stream;
  const gates = result?.gates;
  const maxAttempts = 1 + (result?.maxRegenerations ?? 1);
  const attemptsNow = result?.attempts ?? attempt;

  return (
    <div>
      {/* 프리셋 + 입력 */}
      <div className="panel p-3 mb-3 flex gap-2 items-center">
        <button className="chip whitespace-nowrap hover:border-teal-500 text-teal-700" onClick={() => run(S3_PRESET)}>시나리오 S3</button>
        <input className="flex-1 px-3 py-2 rounded-lg bg-white border border-slate-300 text-sm"
          value={prompt} onChange={e => setPrompt(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()} />
        <button onClick={() => run()} disabled={running}
          className="px-5 py-2 rounded-lg bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm disabled:opacity-40">
          {running ? (attempt > 1 ? '재생성 중…' : '생성 중…') : result ? '다시 생성' : '화면 생성'}
        </button>
      </div>
      {err && <div className="text-[#E90061] text-sm mb-3">{err}</div>}

      {/* 반전 시연 콜아웃 */}
      <div className="panel p-3 mb-4 text-xs flex items-center gap-3" style={{ borderColor: 'var(--cloud)' }}>
        <span className="text-lg">⇄</span>
        <div className="flex-1">
          <b>실시간 반전:</b> Registry에서 <b className="text-teal-700">Button v2</b>를 DEPRECATED, <b className="text-teal-700">v3</b>를 APPROVED로 바꾼 뒤
          다시 생성하면 결과가 달라진다 — 승인 상태가 에이전트에게 보이는 컴포넌트를 바꾸고, 그것이 코드를 바꾼다.
          이전 결과는 아래에 나란히 남는다.
        </div>
        <a href="#/registry" className="chip hover:border-teal-500 text-teal-700 whitespace-nowrap">Registry 열기 →</a>
      </div>

      <div className="grid grid-cols-[340px_1fr] gap-4">
        {/* 좌: Registry 소비자 뷰 + 파이프라인 */}
        <div>
          <div className="panel p-3 mb-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--cloud)' }} />
              <b className="text-sm">Registry가 에이전트에게 보여주는 승인 컴포넌트</b>
              <button className="chip text-[10px] ml-auto hover:border-slate-400" onClick={loadComps}>새로고침</button>
            </div>
            <div className="text-[11px] text-slate-500 mb-2">
              Consumer API `list_approved(subtype=COMPONENT)` — APPROVED만 · 정확 조회 · {compsMeta.count ?? comps?.length ?? '…'}개
              {compsMeta.source && compsMeta.source !== 'registry' && <span className="text-amber-700"> · 출처: 픽스처(Registry 미연결)</span>}
              {compsMeta.source === 'registry' && <span className="text-emerald-600"> · 출처: Registry</span>}
            </div>
            {comps === null && <div className="text-xs text-slate-500">불러오는 중…</div>}
            {comps?.length === 0 && <div className="text-xs text-[#E90061]">승인된 컴포넌트가 없다 — 에이전트는 시맨틱 HTML만 쓸 수 있다.</div>}
            <div className="space-y-1.5 max-h-[420px] overflow-y-auto pr-1">
              {(comps || []).map(c => (
                <div key={c.name + c.version} className="rounded-lg p-2 border"
                  style={{ borderColor: c.name === 'Button' ? 'var(--cloud)' : 'var(--line)', background: c.name === 'Button' ? 'rgba(56,189,248,.06)' : undefined }}>
                  <div className="flex items-center gap-2">
                    <span className={`font-mono text-xs ${c.name === 'Button' ? 'text-teal-900 font-bold text-sm' : 'text-slate-800'}`}>{c.name}<span className="text-[#008485]">@{c.version}</span></span>
                    <span className="chip text-[9px] text-emerald-600 ml-auto">APPROVED</span>
                  </div>
                  <div className="text-[10px] text-slate-500 font-mono truncate">{c.module}</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {c.props.map(p => (
                      <span key={p.name} className="text-[10px] px-1.5 rounded bg-white border border-slate-200 font-mono"
                        title={p.type}>{p.name}{p.required ? <span className="text-[#E90061]">*</span> : ''}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="text-[10px] text-slate-400 mt-2">* 필수 props · 게이트 실행기: {RUNNER_LABEL[compsMeta.gatesRunner || ''] || '…'}</div>
          </div>

          <div className="text-xs text-slate-500 mb-2">파이프라인 단계 (펼쳐보기) — <span style={{ color: 'var(--cloud)' }}>■ 클라우드/플랫폼</span>
            <span className="text-slate-400"> · 이 시나리오는 개인데이터 플레인(<span style={{ color: 'var(--onprem)' }}>■</span>)을 거치지 않는다</span></div>
          {stages.map((s, i) => <StagePanel key={i} s={s} />)}
          {running && stages.length === 0 && <div className="text-xs text-slate-500 blink">연결 중…</div>}
        </div>

        {/* 우: 코드 + 게이트 */}
        <div>
          {/* 이전/현재 비교 */}
          {prev && (
            <div className="grid grid-cols-2 gap-3 mb-3">
              <ResultSummary r={prev} title="이전 생성 (Before)" accent="#64748b" />
              {result ? <ResultSummary r={result} title="현재 생성 (After)" accent="var(--cloud)" />
                : <div className="panel p-3 text-xs text-slate-500 flex items-center justify-center">현재 생성 진행 중… <span className="blink ml-1">▌</span></div>}
            </div>
          )}

          {/* 상태 줄 */}
          <div className="flex items-center gap-2 mb-2 text-xs flex-wrap">
            <span className="chip" title="§12.9 재생성은 최대 1회">
              시도 {[...Array(maxAttempts)].map((_, i) => (
                <span key={i} className={`inline-block w-2 h-2 rounded-full mx-0.5 ${i < attemptsNow ? (i === 0 ? 'bg-[#008485]' : 'bg-amber-400') : 'bg-slate-700'}`} />
              ))} {attemptsNow}/{maxAttempts} <span className="text-slate-500 ml-1">(재생성 상한 1회)</span>
            </span>
            {(cached || result?.cached) && <span className="chip text-amber-700" style={{ borderColor: 'var(--onprem)' }}>캐시 응답 — Bedrock 호출 실패/예산 초과로 재생</span>}
            {result && <span className="chip">종합 {okIcon(result.ok)} {okText(result.ok)}</span>}
            {result?.usage && <span className="text-slate-500">토큰 in {result.usage.inputTokens?.toLocaleString()} / out {result.usage.outputTokens?.toLocaleString()}</span>}
            {result?.piiOutbound != null && <span className={result.piiOutbound === 0 ? 'text-emerald-600' : 'text-[#E90061]'}>경계 PII {result.piiOutbound}건</span>}
            {result && <span className="text-slate-400 ml-auto">traceId {result.traceId} · {result.elapsedMs}ms</span>}
          </div>

          {/* 사용 컴포넌트 */}
          {result && (
            <div className="panel p-3 mb-3 flex items-center gap-3">
              <span className="text-xs text-slate-400 whitespace-nowrap">사용 컴포넌트</span>
              <UsedBadges used={result.componentsUsed} />
              <span className="ml-auto text-[11px] text-slate-500 whitespace-nowrap">승인 Button: <b className="text-teal-700">{result.approvedButton}</b></span>
            </div>
          )}

          {/* 코드 */}
          <div className="panel p-3 mb-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--cloud)' }} />
              <b className="text-sm">생성 코드 — Screen.tsx</b>
              {running && <span className="text-xs text-slate-500">스트리밍 중{attempt > 1 ? ` (재생성 ${attempt - 1}/1)` : ''}…</span>}
              <span className="ml-auto text-[10px] text-slate-500">{shownCode ? `${shownCode.split('\n').length}줄` : ''}</span>
              <button className="chip text-[10px] hover:border-teal-500" onClick={copy} disabled={!shownCode}>{copied ? '복사됨 ✓' : '복사'}</button>
            </div>
            <pre ref={codeRef} className="bg-slate-50 rounded-lg p-3 text-[11px] leading-4 font-mono overflow-auto max-h-[460px] whitespace-pre">
              {shownCode || <span className="text-slate-400">프리셋 버튼을 누르면 Registry 승인 컴포넌트만으로 코드를 생성합니다.</span>}
              {running && <span className="blink">▌</span>}
            </pre>
            {result?.history && result.history.length > 0 && (
              <div className="mt-2 text-xs">
                <button className="chip text-[10px] hover:border-slate-400" onClick={() => setShowHistory(s => !s)}>
                  {showHistory ? '1차 시도 접기' : `1차 시도 결과 보기 (실패 → 재생성)`}</button>
                {showHistory && result.history.map(h => (
                  <div key={h.attempt} className="mt-2">
                    <div className="text-slate-400 mb-1">시도 {h.attempt} — 종합 {okIcon(h.ok)} · <UsedBadges used={h.componentsUsed} size="sm" /></div>
                    <div className="text-slate-500 mb-1">{GATE_TILES.map(t => <span key={t.key} className="mr-2">{t.label} {okIcon(h.gates?.[t.key]?.ok)}</span>)}</div>
                    <pre className="bg-slate-50 rounded p-2 text-[10px] leading-4 font-mono overflow-auto max-h-56">{h.code || '(코드블록 없음)'}</pre>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 게이트 결과 */}
          <div className="flex items-center gap-2 mb-2 text-xs text-slate-400">
            <b className="text-slate-800">검증 게이트</b>
            <span>— 실제 도구 실행 결과 (typescript · eslint · axe-core)</span>
            {gates?.runner && <span className="chip text-[10px] ml-auto">{RUNNER_LABEL[gates.runner] || gates.runner}</span>}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {GATE_TILES.map(t => <GateTile key={t.key} k={t.key} label={t.label} sub={t.sub} gate={gates?.[t.key] as Gate | undefined} />)}
          </div>
          {result?.reasons && result.reasons.length > 0 && (
            <div className="mt-3 text-xs text-[#E90061] border border-rose-300 rounded-lg p-3">
              <b>최종 실패 사유 (재생성 1회 후에도 남음 — 더 재시도하지 않는다)</b>
              {result.reasons.map((r, i) => <div key={i} className="font-mono text-[11px] mt-1 break-words">{r}</div>)}
            </div>
          )}
          <div className="text-[10px] text-slate-400 mt-3">
            시각 회귀는 정규화 HTML 해시·태그 개수의 <b>구조 스냅샷</b> 비교다 — 픽셀 비교는 미구현. KWCAG 매핑은 참고용(공식 대응표 아님).
            게이트 실행기가 연결되지 않으면 통과를 흉내내지 않고 "미판정"으로 표기한다.
          </div>
        </div>
      </div>
    </div>
  );
}
