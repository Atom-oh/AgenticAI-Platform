// 에이전트 빌더 — AgentCore Harness 기반 에이전트를 만들고(→ Registry PENDING_APPROVAL), 승인된 것만 호출한다 (Consumer 게이트).
// 색 규칙: Bedrock/AgentCore = var(--bedrock)(시안), VPC 내부(도구 실행·마스킹) = var(--vpc)(앰버). 이 화면은 Tier 0/1 전용 (SPEC §11-4).
import { useCallback, useEffect, useRef, useState } from 'react';
import { sock, WsEvent } from '../lib';

type Agent = {
  name: string; version: string; title: string; description: string; status: string; allowedTargets?: string[];
  agentcoreStatus: string | null; harnessArn: string | null; harnessStatus: string; harnessError?: string;
  model?: string; allowedTools: string[]; skills: string[]; memory: boolean; scenario: string; runtime?: string;
  createdBy?: string; updatedAt?: number; subtype?: string;
};
type Tool = { name: string; description: string };
type SkillRec = { name: string; version: string; status: string };
type Catalog = {
  agents: Agent[]; tools: Tool[]; skills: SkillRec[]; models: string[]; gateway: { arn: string; url: string };
  commonRules?: string; defaultModel?: string; harnessError?: string | null; agentcoreRegistryError?: string | null;
  registryBackend?: string;
};
type ToolCall = { name: string; toolUseId?: string; input?: string };
type Msg = {
  role: 'user' | 'agent'; text: string; tools: ToolCall[]; running?: boolean;
  done?: WsEvent | null; error?: string; status?: string; gate?: string; stageErrors?: string[];
};
type CreateResult = { ok: boolean; error?: string; code?: number; stage?: string; record?: any;
  harness?: { arn?: string; status?: string; reused?: boolean; note?: string | null }; agentcoreRegistry?: any };

// 시연 프리셋 — 시나리오 에이전트 4종에 1:1
const PRESETS: Record<string, string> = {
  S1: '전세자금대출 담보 인정 규정이 개정되면 영향받는 상품·화면·컴포넌트·담당부서·문서는?',
  S2: '제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?',
  S3: '여신 심사 결과 조회 화면을 만들어줘',
  F7: '전세대출 보증 동향 요약으로 내부 보고서를 써줘',
};
// 서버(agentcore/agent_specs.COMMON_RULES)가 정본 — 카탈로그 응답 전 초기값으로만 쓴다
const FALLBACK_RULES = '공통 규칙: 한국어로 답한다. 숫자(금리·한도·금액)는 도구가 반환한 값만 사용하고 새로 만들지 않는다. '
  + '도구 결과에 없는 사실을 단정하지 않는다. 개인 식별자는 토큰(⟨…⟩) 형태 그대로 두고 재식별을 시도하지 않는다. '
  + '투자 권유·수익률 비교 요청은 정중히 거절한다.';
const promptTemplate = (title: string, rules: string) =>
  `당신은 아톰은행 ${title.trim() || '○○'} 에이전트다. 질문을 받으면 허용된 도구만 사용해 근거를 확인한 뒤 `
  + `① 한 줄 요약 ② 근거(도구 결과·노드 ID 병기) ③ 권고 조치 2~3개 순서로 답한다. 도구 결과에 없는 항목을 만들지 않는다. ${rules}`;

const STATUS_KO: Record<string, string> = {
  DRAFT: '초안', PENDING_APPROVAL: '승인 대기', APPROVED: '승인', REJECTED: '반려', DEPRECATED: '폐기',
};
const statusColor = (s: string) =>
  s === 'APPROVED' ? 'text-emerald-400' : s === 'PENDING_APPROVAL' ? 'text-amber-400'
    : s === 'DEPRECATED' ? 'text-rose-400' : s === 'REJECTED' ? 'text-rose-300' : 'text-slate-400';
const statusBorder = (s: string) =>
  s === 'APPROVED' ? 'border-emerald-700' : s === 'PENDING_APPROVAL' ? 'border-amber-700'
    : s === 'DEPRECATED' || s === 'REJECTED' ? 'border-rose-800' : 'border-slate-700';
const shortModel = (m?: string) => (m || '—').replace('global.anthropic.', '');
const shortSid = (s?: string | null) => (s ? `${s.slice(0, 8)}…${s.slice(-8)}` : '—');
const errOf = (e: WsEvent | null | undefined) => (e && (e.type === 'error' || e.ok === false)) ? (e.error || e.message || '오류') : '';

function RegChip({ s }: { s: string }) {
  return <span className={`chip text-[10px] font-semibold ${statusColor(s)} ${statusBorder(s)}`} title="플랫폼 Registry 상태 (거버넌스 원장)">
    Registry · {STATUS_KO[s] || s}</span>;
}
function AcChip({ s }: { s: string | null }) {
  return <span className="chip text-[10px]" style={{ borderColor: 'var(--bedrock)', color: s ? '#bae6fd' : '#64748b' }}
    title="AgentCore Agent Registry(us-east-1) 미러 상태 — null 이면 미러 없음/도달 불가">
    AgentCore · {s ? (STATUS_KO[s] || s) : '—'}</span>;
}
function HarnessChip({ s, err }: { s: string; err?: string }) {
  const ready = s === 'READY' || s === 'ACTIVE';
  const cls = ready ? 'text-sky-300' : s === 'none' ? 'text-slate-500' : s === 'FAILED' ? 'text-rose-400' : 'text-amber-300';
  const label = s === 'none' ? 'Harness 없음' : s === 'unknown' ? 'Harness ?' : `Harness · ${s}`;
  return <span className={`chip text-[10px] ${cls}`} style={{ borderColor: ready ? 'var(--bedrock)' : undefined }}
    title={err || (s === 'none' ? '이 레코드에 연결된 AgentCore Harness 가 없다 (파이프라인형 에이전트)' : 'AgentCore Harness 상태')}>{label}</span>;
}
function ScenarioTag({ s }: { s: string }) {
  const custom = !PRESETS[s];
  return <span className={`chip text-[10px] ${custom ? 'text-slate-400' : 'text-sky-300'}`}>{custom ? '사용자 정의' : `시나리오 ${s}`}</span>;
}

/* ---------------- 좌: 카탈로그 ---------------- */
function CatalogList({ cat, sel, onSelect, onRefresh, loading }: {
  cat: Catalog | null; sel: Agent | null; onSelect: (a: Agent) => void; onRefresh: () => void; loading: boolean;
}) {
  const agents = cat?.agents || [];
  const harnessFirst = [...agents].sort((a, b) => {
    const ha = a.harnessStatus !== 'none' ? 0 : 1, hb = b.harnessStatus !== 'none' ? 0 : 1;
    return ha - hb || a.name.localeCompare(b.name);
  });
  return (
    <div className="panel p-3 flex flex-col min-h-0" style={{ borderTop: '2px solid var(--bedrock)' }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--bedrock)' }} />
        <b className="text-sm">카탈로그</b>
        <span className="text-xs text-slate-500">AGENT 레코드 {agents.length}건</span>
        <button className="chip text-[10px] ml-auto hover:border-slate-500" onClick={onRefresh} disabled={loading}>{loading ? '조회 중…' : '새로고침'}</button>
      </div>
      <div className="text-[11px] text-slate-500 mb-2">
        플랫폼 Registry(거버넌스 원장) ⟷ AgentCore Registry(발견) ⟷ Harness(실행). 세 상태를 그대로 보인다.
      </div>
      {cat?.harnessError && <div className="text-[11px] text-amber-300 mb-2">Harness 목록 조회 실패 — {cat.harnessError}</div>}
      {cat?.agentcoreRegistryError && <div className="text-[11px] text-amber-300 mb-2">AgentCore Registry 도달 불가 — {cat.agentcoreRegistryError}</div>}
      <div className="flex-1 overflow-y-auto space-y-2 min-h-0 pr-1" style={{ maxHeight: 'calc(100vh - 240px)' }}>
        {!cat && <div className="text-xs text-slate-500">카탈로그 불러오는 중…</div>}
        {cat && agents.length === 0 && <div className="text-xs text-slate-500">AGENT 레코드가 없다 — 오른쪽에서 만들거나 관리자 seed_agents 를 실행한다.</div>}
        {harnessFirst.map(a => {
          const on = sel?.name === a.name && sel?.version === a.version;
          return (
            <button key={`${a.name}@${a.version}`} onClick={() => onSelect(a)}
              className={`w-full text-left rounded-lg border p-2.5 hover:border-slate-500 ${on ? 'border-sky-500 bg-sky-500/5' : 'border-slate-800'}`}>
              <div className="flex items-center gap-2">
                <b className="text-sm truncate">{a.title}</b>
                <ScenarioTag s={a.scenario} />
                {a.memory && <span className="chip text-[10px] text-slate-400" title="AgentCore managed memory (SEMANTIC)">메모리</span>}
              </div>
              <div className="font-mono text-[11px] text-slate-500 mt-0.5">{a.name}@{a.version} · {shortModel(a.model)}</div>
              <div className="flex flex-wrap gap-1 mt-1.5">
                <RegChip s={a.status} /><AcChip s={a.agentcoreStatus} /><HarnessChip s={a.harnessStatus} err={a.harnessError} />
              </div>
              <div className="text-[11px] text-slate-500 mt-1 line-clamp-2">{a.description}</div>
              <div className="text-[10px] text-slate-600 mt-1">
                도구 {a.allowedTools.length}개{a.skills.length ? ` · 스킬 ${a.skills.length}개` : ''}{a.runtime ? ` · ${a.runtime}` : ''}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------- 우상: 만들기 폼 ---------------- */
function CreateForm({ cat, onCreated, onApprove }: {
  cat: Catalog | null; onCreated: (rec: any) => void; onApprove: (name: string, version: string) => Promise<WsEvent>;
}) {
  const [open, setOpen] = useState(true);
  const [name, setName] = useState('');
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState(promptTemplate('', FALLBACK_RULES));
  const [promptDirty, setPromptDirty] = useState(false);
  const [tools, setTools] = useState<string[]>([]);
  const [skills, setSkills] = useState<string[]>([]);
  const [memory, setMemory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState(false);
  const [res, setRes] = useState<CreateResult | null>(null);
  const [approved, setApproved] = useState<WsEvent | null>(null);

  const rules = cat?.commonRules || FALLBACK_RULES;
  useEffect(() => { if (!model && cat) setModel(cat.defaultModel || cat.models[0] || ''); }, [cat, model]);
  useEffect(() => { if (!promptDirty) setPrompt(promptTemplate(title, rules)); }, [title, rules, promptDirty]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter(x => x !== v) : [...list, v]);
  const nameOk = /^[a-z][a-z0-9_]{2,40}$/.test(name);

  const submit = async () => {
    if (busy || !nameOk || !prompt.trim()) return;
    setBusy(true); setRes(null); setApproved(null);
    try {
      const e = await sock.request('agent_create', {
        name, title, description: desc, model, systemPrompt: prompt, allowedTools: tools, skills, memory,
      });
      const er = errOf(e);
      if (er) setRes({ ok: false, error: er, code: e.code, stage: e.stage, harness: e.harness });
      else { setRes(e as unknown as CreateResult); onCreated(e.record); }
    } catch (e: any) { setRes({ ok: false, error: e.message }); }
    setBusy(false);
  };
  const approve = async () => {
    if (!res?.record || approving) return;
    setApproving(true);
    try { setApproved(await onApprove(res.record.name, res.record.recordVersion)); }
    catch (e: any) { setApproved({ type: 'error', message: e.message }); }
    setApproving(false);
  };

  const rec = res?.record;
  const nowStatus: string | undefined = approved?.ok ? approved.record?.status : rec?.status;
  return (
    <div className="panel p-3 mb-3" style={{ borderTop: '2px solid var(--bedrock)' }}>
      <button className="w-full flex items-center gap-2 text-left" onClick={() => setOpen(o => !o)}>
        <span className="text-slate-500">{open ? '▾' : '▸'}</span>
        <b className="text-sm">에이전트 만들기</b>
        <span className="text-xs text-slate-500">모델 · 시스템 프롬프트 · Skills · Gateway 도구 · 메모리 → Harness 생성 → Registry 승인 대기</span>
      </button>
      {open && (
        <div className="mt-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-slate-400">이름 <span className="text-slate-600">(영문 snake_case · Harness 이름 bank_&lt;이름&gt;)</span>
              <input className={`mt-1 w-full px-3 py-1.5 rounded-lg bg-slate-900 border text-sm font-mono ${name && !nameOk ? 'border-rose-700' : 'border-slate-700'}`}
                value={name} onChange={e => setName(e.target.value.trim())} placeholder="card_benefit_agent" />
            </label>
            <label className="text-xs text-slate-400">제목
              <input className="mt-1 w-full px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-sm"
                value={title} onChange={e => setTitle(e.target.value)} placeholder="카드 혜택 상담 에이전트" />
            </label>
            <label className="text-xs text-slate-400 col-span-2">설명
              <input className="mt-1 w-full px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-sm"
                value={desc} onChange={e => setDesc(e.target.value)} placeholder="무엇을 하고, 어떤 도구만 쓰는지" />
            </label>
            <label className="text-xs text-slate-400">모델 <span className="text-slate-600">(Bedrock · global 교차 리전 추론)</span>
              <select className="mt-1 w-full px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono"
                value={model} onChange={e => setModel(e.target.value)}>
                {(cat?.models || [model]).filter(Boolean).map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-400 flex flex-col">메모리 <span className="text-slate-600">(AgentCore managed memory · SEMANTIC · 30일)</span>
              <button type="button" onClick={() => setMemory(m => !m)}
                className={`mt-1 self-start chip text-xs ${memory ? 'text-sky-300' : 'text-slate-400'}`}
                style={{ borderColor: memory ? 'var(--bedrock)' : undefined }}>
                <span className={`inline-block w-2 h-2 rounded-full ${memory ? 'bg-sky-400' : 'bg-slate-600'}`} />{memory ? '켬 — 세션 간 기억' : '끔 — 세션 안에서만'}
              </button>
            </label>
          </div>

          <div className="mt-2 text-xs text-slate-400 flex items-center gap-2">시스템 프롬프트
            <span className="text-slate-600">— 끝에 플랫폼 공통 규칙이 붙는다 (서버 정본 agent_specs.COMMON_RULES)</span>
            {promptDirty && <button className="chip text-[10px] ml-auto hover:border-slate-500" onClick={() => { setPromptDirty(false); setPrompt(promptTemplate(title, rules)); }}>템플릿으로 되돌리기</button>}
          </div>
          <textarea className="mt-1 w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm leading-relaxed" rows={5}
            value={prompt} onChange={e => { setPrompt(e.target.value); setPromptDirty(true); }} />

          <div className="grid grid-cols-2 gap-3 mt-2">
            <div>
              <div className="text-xs text-slate-400 mb-1">Skills <span className="text-slate-600">(Registry SKILL → S3 SKILL.md 로 Harness 에 연결)</span></div>
              <div className="flex flex-wrap gap-1">
                {(cat?.skills || []).map(s => {
                  const on = skills.includes(s.name);
                  return <button key={s.name} onClick={() => toggle(skills, setSkills, s.name)} title={`${s.name}@${s.version} · ${STATUS_KO[s.status] || s.status}`}
                    className={`chip text-[11px] ${on ? 'text-sky-200' : s.status === 'APPROVED' ? 'text-slate-300' : 'text-slate-500'} hover:border-slate-500`}
                    style={{ borderColor: on ? 'var(--bedrock)' : undefined, background: on ? 'rgba(56,189,248,.12)' : undefined }}>
                    {on ? '✓ ' : ''}{s.name}{s.status !== 'APPROVED' && <span className="text-[9px] text-amber-400">{STATUS_KO[s.status] || s.status}</span>}
                  </button>;
                })}
                {cat && cat.skills.length === 0 && <span className="text-[11px] text-slate-500">SKILL 레코드 없음</span>}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-1">Tools <span className="text-slate-600">(AgentCore Gateway · Lambda 타깃 · IAM 인바운드)</span></div>
              <div className="flex flex-wrap gap-1">
                {(cat?.tools || []).map(t => {
                  const on = tools.includes(t.name);
                  return <button key={t.name} onClick={() => toggle(tools, setTools, t.name)} title={t.description}
                    className={`chip text-[11px] font-mono ${on ? 'text-amber-100' : 'text-slate-300'} hover:border-slate-500`}
                    style={{ borderColor: on ? 'var(--vpc)' : undefined, background: on ? 'rgba(251,191,36,.10)' : undefined }}>
                    {on ? '✓ ' : ''}{t.name}
                  </button>;
                })}
              </div>
              {tools.length > 0 && (
                <ul className="mt-1.5 text-[11px] text-slate-400 space-y-0.5">
                  {tools.map(n => <li key={n}><span className="font-mono text-amber-200">{n}</span> — {cat?.tools.find(t => t.name === n)?.description}</li>)}
                </ul>
              )}
              <div className="text-[10px] text-slate-500 mt-1.5 break-all">
                Gateway URL: <span className="font-mono text-slate-400">{cat?.gateway?.url || '미설정 (GATEWAY_URL env 없음)'}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3 mt-3">
            <button onClick={submit} disabled={busy || !nameOk || !prompt.trim() || !cat}
              className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
              {busy ? 'Harness 생성 중… (READY 대기 최대 90초)' : '만들기 → 승인 대기'}
            </button>
            <span className="text-[11px] text-slate-500">생성 순서: Harness(ensure, 멱등) → Registry 레코드 DRAFT → PENDING_APPROVAL → AgentCore Registry 미러</span>
          </div>

          {res && !res.ok && (
            <div className="mt-3 rounded-lg border border-rose-800 bg-rose-950/30 p-2 text-xs text-rose-300">
              생성 실패{res.stage ? ` (${res.stage})` : ''}: {res.error}
              {res.harness?.arn && <div className="text-slate-400 mt-1">Harness 는 만들어졌다 — {res.harness.status} · {res.harness.arn}</div>}
            </div>
          )}
          {res?.ok && rec && (
            <div className="mt-3 rounded-lg border border-slate-700 p-3 text-xs">
              <div className="flex items-center gap-2 flex-wrap">
                <b className="text-sm">{rec.payload?.title || rec.name}</b>
                <span className="font-mono text-slate-500">{rec.name}@{rec.recordVersion}</span>
                {nowStatus && <RegChip s={nowStatus} />}
                <HarnessChip s={res.harness?.status || 'unknown'} />
                <AcChip s={approved?.ok ? approved.agentcoreRegistry?.status : (res.agentcoreRegistry?.error ? null : res.agentcoreRegistry?.status)} />
              </div>
              <div className="text-slate-400 mt-1.5 font-mono break-all">Harness ARN: {res.harness?.arn || '—'}</div>
              {res.harness?.note && <div className="text-amber-300 mt-1">{res.harness.note}</div>}
              {res.agentcoreRegistry?.error && <div className="text-amber-300 mt-1">AgentCore Registry 미러 실패 — {res.agentcoreRegistry.error} (플랫폼 Registry 에는 등록됨)</div>}
              {!res.agentcoreRegistry?.error && res.agentcoreRegistry && (
                <div className="text-slate-500 mt-1">AgentCore Registry: {res.agentcoreRegistry.action} · {res.agentcoreRegistry.status} · {res.agentcoreRegistry.recordId}</div>
              )}
              {nowStatus === 'PENDING_APPROVAL' && (
                <div className="flex items-center gap-2 mt-2">
                  <span className="text-amber-300">승인 대기 — Consumer 게이트가 호출을 거부한다.</span>
                  <a href="#/registry" className="chip hover:border-sky-500 text-sky-300">#/registry 에서 승인 →</a>
                  <button onClick={approve} disabled={approving}
                    className="chip hover:border-emerald-500 text-emerald-300 disabled:opacity-40" title="관리자 편의 — 감사 이벤트에 actor·사유가 남는다">
                    {approving ? '승인 중…' : '데모: 즉시 승인'}
                  </button>
                </div>
              )}
              {approved && !approved.ok && <div className="text-rose-300 mt-1">승인 실패: {errOf(approved)}</div>}
              {approved?.ok && <div className="text-emerald-400 mt-2">승인됨 — 아래 채팅에서 호출할 수 있다 (감사: {approved.audit?.actor} · {approved.audit?.reason})</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------------- 채팅 말풍선 ---------------- */
function ToolCard({ t }: { t: ToolCall }) {
  let pretty = t.input;
  if (t.input) { try { pretty = JSON.stringify(JSON.parse(t.input), null, 2); } catch { pretty = t.input; } }
  return (
    <div className="rounded-lg p-2 text-[11px]" style={{ border: '1px solid var(--vpc)', background: 'rgba(251,191,36,.08)' }}>
      <div className="font-semibold text-amber-300">🔧 도구 호출: <span className="font-mono">{t.name}</span>
        <span className="text-slate-500 font-normal"> · Gateway → Lambda · VPC 내부에서 마스킹 후 반환</span></div>
      {t.input != null
        ? <pre className="font-mono whitespace-pre-wrap break-words text-amber-100/80 mt-1 max-h-40 overflow-y-auto">{pretty}</pre>
        : <span className="text-slate-500">입력 수신 중…</span>}
    </div>
  );
}

function Bubble({ m }: { m: Msg }) {
  if (m.role === 'user') {
    return <div className="flex justify-end"><div className="max-w-[80%] rounded-2xl rounded-br-md bg-slate-800 px-3 py-2 text-sm whitespace-pre-wrap">{m.text}</div></div>;
  }
  const u = m.done?.usage || {};
  const isGate = m.gate === 'consumer' || m.done?.gate === 'consumer';
  return (
    <div className="flex justify-start">
      <div className="max-w-[88%] rounded-2xl rounded-bl-md px-3 py-2 text-sm" style={{ background: 'rgba(56,189,248,.06)', borderLeft: '3px solid var(--bedrock)' }}>
        {m.tools.length > 0 && <div className="space-y-1.5 mb-2">{m.tools.map((t, i) => <ToolCard key={`${t.toolUseId || t.name}-${i}`} t={t} />)}</div>}
        {(m.text || m.running) && (
          <div className="md text-slate-200">{m.text}{m.running && <span className="blink">▌</span>}</div>
        )}
        {(m.stageErrors || []).map((s, i) => <div key={i} className="text-[11px] text-rose-300 mt-1 font-mono break-all">스트림 오류: {s}</div>)}
        {m.error && (
          <div className={`rounded-lg p-2 mt-1 text-xs border ${isGate ? 'border-rose-700 bg-rose-950/40 text-rose-300' : 'border-rose-800 bg-rose-950/20 text-rose-300'}`}>
            {isGate ? '⛔ 거버넌스 게이트 (Consumer): ' : '⚠ 호출 실패: '}{m.error}
            {m.status && <span className="ml-2"><RegChip s={m.status} /></span>}
            {isGate && <div className="text-[11px] text-rose-200/70 mt-1">APPROVED 가 아닌 레코드는 Harness 를 호출하는 코드 경로에 들어가지 않는다 — Registry 에서 승인한 뒤 다시 보낸다.</div>}
          </div>
        )}
        {m.done && !m.error && (
          <div className="text-[10px] text-slate-500 mt-1.5 flex gap-2 flex-wrap">
            <span>입력 {u.inputTokens ?? 0} · 출력 {u.outputTokens ?? 0} 토큰</span>
            <span>· <span className="font-mono">{m.done.modelId || '—'}</span></span>
            <span>· 세션 <span className="font-mono">{shortSid(m.done.sessionId)}</span></span>
            {m.done.stopReason && <span>· {m.done.stopReason}</span>}
            {typeof m.done.toolCalls === 'number' && m.done.toolCalls > 0 && <span>· 도구 {m.done.toolCalls}회</span>}
            {m.done.elapsedMs != null && <span>· {m.done.elapsedMs} ms</span>}
            <span>· {m.done.runtime || 'AgentCore Harness'}</span>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- 우하: 채팅 ---------------- */
function Chat({ sel, onApprove, onRefresh }: {
  sel: Agent | null; onApprove: (name: string, version: string) => Promise<WsEvent>; onRefresh: () => void;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);   // React 상태에만 — 브라우저 스토리지 저장 금지 (§12.12)
  const [approving, setApproving] = useState(false);
  const [approveErr, setApproveErr] = useState('');
  const [cfg, setCfg] = useState<WsEvent | null>(null);
  const [cfgOpen, setCfgOpen] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const key = sel ? `${sel.name}@${sel.version}` : '';

  useEffect(() => { setMsgs([]); setSessionId(null); setInput(''); setCfg(null); setCfgOpen(false); setApproveErr(''); }, [key]);
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight; }, [msgs]);
  useEffect(() => {
    if (!cfgOpen || cfg || !sel) return;
    sock.request('agent_get', { name: sel.name, version: sel.version }).then(setCfg).catch((e: Error) => setCfg({ type: 'error', message: e.message }));
  }, [cfgOpen, cfg, sel]);

  const patchLast = useCallback((fn: (m: Msg) => Msg) => setMsgs(ms => {
    const i = ms.length - 1; if (i < 0) return ms;
    const c = ms.slice(); c[i] = fn(c[i]); return c;
  }), []);

  const send = async (text?: string) => {
    const q = (text ?? input).trim();
    if (!q || !sel || running) return;
    setInput(''); setRunning(true);
    setMsgs(m => [...m, { role: 'user', text: q, tools: [] }, { role: 'agent', text: '', tools: [], running: true }]);
    try {
      await sock.run('agent_invoke', { name: sel.name, version: sel.version, message: q, sessionId: sessionId ?? undefined }, (e: WsEvent) => {
        if (e.type === 'agent.token') patchLast(m => ({ ...m, text: m.text + e.t }));
        else if (e.type === 'agent.stage') {
          if (e.step === 'tool_start') patchLast(m => ({ ...m, tools: [...m.tools, { name: e.name, toolUseId: e.toolUseId }] }));
          else if (e.step === 'tool_input') patchLast(m => {
            const tools = m.tools.slice();
            const i = tools.map((t, idx) => (t.name === e.name && t.input == null ? idx : -1)).filter(i => i >= 0).pop();
            if (i != null) tools[i] = { ...tools[i], input: e.input ?? '' };
            else tools.push({ name: e.name, toolUseId: e.toolUseId, input: e.input ?? '' });
            return { ...m, tools };
          });
          else if (e.step === 'error') patchLast(m => ({ ...m, stageErrors: [...(m.stageErrors || []), e.message] }));
        } else if (e.type === 'agent.done') {
          if (e.sessionId) setSessionId(e.sessionId);
          patchLast(m => ({ ...m, done: e, error: e.error, status: e.status, gate: e.gate, running: false }));
        }
      });
    } catch (err: any) { patchLast(m => ({ ...m, error: err.message, running: false })); }
    setRunning(false);
  };

  const approve = async () => {
    if (!sel || approving) return;
    setApproving(true); setApproveErr('');
    try { const e = await onApprove(sel.name, sel.version); const er = errOf(e); if (er) setApproveErr(er); else onRefresh(); }
    catch (e: any) { setApproveErr(e.message); }
    setApproving(false);
  };

  const preset = sel ? PRESETS[sel.scenario] : undefined;
  const hz = cfg?.harness;
  return (
    <div className="panel p-3 flex flex-col min-h-0" style={{ borderTop: '2px solid var(--bedrock)' }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--bedrock)' }} />
        <b className="text-sm">{sel ? sel.title : '채팅'}</b>
        {sel && <span className="font-mono text-[11px] text-slate-500">{sel.name}@{sel.version}</span>}
        {sel && <RegChip s={sel.status} />}
        <span className="chip text-[10px] text-amber-300 border-amber-700" title="SPEC §11-4 — AgentCore 는 global 교차 리전 추론이 강제되므로 Tier 2 경로에는 쓰지 않는다">
          AgentCore Harness · Tier 0/1 전용
        </span>
        <span className="ml-auto text-[10px] text-slate-500">세션 <span className="font-mono">{shortSid(sessionId)}</span></span>
        {sessionId && <button className="chip text-[10px] hover:border-slate-500" onClick={() => { setSessionId(null); setMsgs([]); }}>새 세션</button>}
      </div>
      <div className="text-[11px] text-slate-500 mt-1">
        에이전트는 Bedrock을 직접 호출한다 — 개인데이터는 도구가 VPC 내부에서 마스킹한 뒤에만 반환된다(도구 출력 = 경계).
      </div>

      {!sel && <div className="flex-1 flex items-center justify-center text-sm text-slate-500 py-16">카탈로그에서 에이전트를 선택한다</div>}
      {sel && (
        <>
          <div className="flex items-center gap-2 mt-2 flex-wrap text-[11px]">
            {preset && <button className="chip hover:border-sky-500 text-sky-300" onClick={() => send(preset)} disabled={running}>시나리오 질문 ({sel.scenario})</button>}
            <span className="text-slate-500">도구 {sel.allowedTools.length ? sel.allowedTools.map(t => <span key={t} className="font-mono text-amber-200/80 mr-1">{t}</span>) : '없음'}</span>
            <button className="chip text-[10px] ml-auto hover:border-slate-500" onClick={() => setCfgOpen(o => !o)}>{cfgOpen ? 'Harness 설정 닫기' : 'Harness 설정 보기'}</button>
          </div>
          {cfgOpen && (
            <div className="mt-2 rounded-lg border border-slate-800 p-2 text-[11px] text-slate-400">
              {!cfg && <span>조회 중…</span>}
              {cfg && errOf(cfg) && <span className="text-rose-300">{errOf(cfg)}</span>}
              {cfg && !errOf(cfg) && cfg.harnessError && <span className="text-amber-300">Harness 조회 실패 — {cfg.harnessError}</span>}
              {cfg && !errOf(cfg) && !cfg.harnessError && !hz && <span>연결된 Harness 없음 (파이프라인형 · {sel.runtime})</span>}
              {hz && (
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                  <div>상태 <b className="text-slate-200">{hz.status}</b></div>
                  <div>모델 <span className="font-mono text-slate-300">{hz.model?.bedrockModelConfig?.modelId || '—'}</span></div>
                  <div>반복 상한 {hz.maxIterations ?? '—'} · 타임아웃 {hz.timeoutSeconds ?? '—'}s</div>
                  <div>메모리 {hz.memory?.disabled ? '끔' : (hz.memory?.managedMemoryConfiguration?.strategies || []).join(',') || '—'}</div>
                  <div className="col-span-2 break-all">허용 도구 <span className="font-mono">{(hz.allowedTools || []).join(', ') || '—'}</span></div>
                  <div className="col-span-2 break-all">스킬 <span className="font-mono">{(hz.skills || []).map((s: any) => s?.s3?.uri || JSON.stringify(s)).join(', ') || '없음'}</span></div>
                  <div className="col-span-2 break-all font-mono text-slate-500">{hz.arn}</div>
                  <details className="col-span-2 mt-1"><summary className="cursor-pointer">시스템 프롬프트</summary>
                    <div className="md text-slate-300 mt-1 text-[11px]">{hz.systemPrompt}</div></details>
                </div>
              )}
            </div>
          )}
          {sel.status !== 'APPROVED' && (
            <div className="mt-2 rounded-lg border border-amber-800 bg-amber-950/20 p-2 text-[11px] text-amber-300 flex items-center gap-2 flex-wrap">
              <span>{STATUS_KO[sel.status] || sel.status} 상태 — 전송하면 Consumer 게이트가 거부한다 (거부 자체가 시연 포인트).</span>
              {sel.status === 'PENDING_APPROVAL' && <>
                <a href="#/registry" className="chip hover:border-sky-500 text-sky-300">#/registry 에서 승인 →</a>
                <button className="chip hover:border-emerald-500 text-emerald-300 disabled:opacity-40" onClick={approve} disabled={approving}>{approving ? '승인 중…' : '데모: 즉시 승인'}</button>
              </>}
              {approveErr && <span className="text-rose-300">{approveErr}</span>}
            </div>
          )}
          {sel.harnessStatus === 'none' && !sel.harnessArn && (
            <div className="mt-2 text-[11px] text-slate-400">이 레코드는 파이프라인형({sel.runtime})이라 연결된 Harness 가 없다 — 전송하면 서버가 그 사실을 그대로 돌려준다. 실행은 각 시나리오 화면에서 한다.</div>
          )}

          <div ref={listRef} className="flex-1 overflow-y-auto space-y-3 mt-3 pr-1" style={{ minHeight: 220, maxHeight: 'calc(100vh - 560px)' }}>
            {msgs.length === 0 && <div className="text-xs text-slate-600 py-6 text-center">메시지를 보내면 Harness 스트림(토큰·도구 호출)이 여기에 나타난다</div>}
            {msgs.map((m, i) => <Bubble key={i} m={m} />)}
          </div>
          <div className="flex gap-2 mt-3">
            <input className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
              value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !e.nativeEvent.isComposing && send()}
              placeholder={preset ? `예: ${preset}` : '메시지'} disabled={running} />
            <button onClick={() => send()} disabled={running || !input.trim()}
              className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
              {running ? '응답 중…' : '보내기'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------------- 메인 뷰 ---------------- */
export default function AgentBuilder() {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [selKey, setSelKey] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const e = await sock.request('agents_catalog');
      const er = errOf(e);
      if (er) setErr(er); else setCat(e as unknown as Catalog);
    } catch (e: any) { setErr(e.message); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const sel = cat?.agents.find(a => `${a.name}@${a.version}` === selKey) || null;
  const approve = async (name: string, version: string) => {
    const e = await sock.request('agent_transition', { name, version, to: 'APPROVED', reason: '데모 — 빌더 즉시 승인' });
    if (!errOf(e)) load();
    return e;
  };

  return (
    <div>
      <div className="panel p-3 mb-3 text-xs flex items-center gap-3" style={{ borderColor: 'var(--bedrock)' }}>
        <span className="text-lg">🧩</span>
        <div className="flex-1">
          <b>에이전트 빌더</b> — 모델·시스템 프롬프트·Skills·Gateway 도구·메모리를 골라 <b className="text-sky-300">AgentCore Harness</b>를 만들면
          Registry 에 <b className="text-amber-300">승인 대기</b>로 들어간다. 승인된 에이전트만 호출된다(Consumer 게이트) — 미승인 호출은 Harness 에 닿기 전에 거부된다.
          {cat?.registryBackend && <span className="text-slate-500"> · Registry 백엔드 {cat.registryBackend}</span>}
        </div>
        <a href="#/registry" className="chip hover:border-sky-500 text-sky-300 whitespace-nowrap">Registry 열기 →</a>
      </div>
      {err && <div className="text-rose-400 text-sm mb-3">{err} <button className="chip text-[10px] ml-2" onClick={load}>다시 시도</button></div>}
      <div className="grid grid-cols-[360px_1fr] gap-4 items-start">
        <CatalogList cat={cat} sel={sel} onSelect={a => setSelKey(`${a.name}@${a.version}`)} onRefresh={load} loading={loading} />
        <div className="min-w-0">
          <CreateForm cat={cat} onApprove={approve}
            onCreated={rec => { load(); setSelKey(`${rec.name}@${rec.recordVersion}`); }} />
          <Chat sel={sel} onApprove={approve} onRefresh={load} />
        </div>
      </div>
    </div>
  );
}
