// 디자인 스튜디오 — 소스 기반 네이티브 통합 (스튜디오 백엔드: 자산 레지스트리·AgentCore Memory·Runtime).
// 방향: 큰 시안 프리뷰 갤러리 · 자산(팔레트 스와치) 카드 · 브리프→3축 variant 생성 · 승인=few-shot 학습 루프.
import { useEffect, useMemo, useState } from 'react';
import { auth, sock } from './lib';

type Draft = { id: string; title: string; axis: string; status: string; url: string; created_at: string };
type Asset = { name: string; type: string; version: string; actor: string; updated_at: string; scope: string; asset_id?: string };

const ASSET_TYPES = ['token', 'palette', 'icon-set', 'component', 'style-guide', 'skill', 'workflow', 'agent'];
const TYPE_LABEL: Record<string, string> = {
  palette: '팔레트', token: '토큰', 'icon-set': '아이콘', component: '컴포넌트',
  'style-guide': '스타일가이드', skill: '스킬', workflow: '워크플로우', agent: '에이전트',
};
const BRIEF_PRESETS = [
  { label: '대출 신청 완료', brief: '모바일 대출 신청 완료 화면 — 안심되는 톤, 다음 단계 안내와 상담 연결 버튼 포함' },
  { label: '심사 결과 조회', brief: '여신 심사 결과 조회 화면 — 승인/보류/거절 상태별 명확한 안내, 필요 서류 리스트' },
  { label: '환전 위저드', brief: '외화 환전 신청 스텝 위저드 — 통화 선택, 금액 입력, 우대율 표시, 확인 단계' },
];

const aid = (a: Asset) => (a as any).asset_id || `${a.type}:${a.name.trim().replace(/\s+/g, '-')}`;

/* 팔레트 JSON → 색 스와치 */
function Swatches({ content }: { content: any }) {
  const colors = useMemo(() => {
    try {
      const o = typeof content === 'string' ? JSON.parse(content) : content;
      return Object.entries(o || {}).filter(([, v]) => typeof v === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(v as string)) as [string, string][];
    } catch { return []; }
  }, [content]);
  if (!colors.length) return null;
  return (
    <div className="flex gap-1 mt-2 flex-wrap">
      {colors.slice(0, 10).map(([k, v]) => (
        <span key={k} title={`${k} ${v}`} className="w-6 h-6 rounded-lg border border-slate-200" style={{ background: v }} />
      ))}
    </div>
  );
}

export default function Studio() {
  const [tab, setTab] = useState<'gallery' | 'generate' | 'assets'>('gallery');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const canWrite = !!auth.studioToken;
  const load = () => {
    sock.request('studio_drafts').then(e => setDrafts(e.drafts || [])).catch(() => {});
    sock.request('assets').then(e => setAssets(e.assets || [])).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const approved = drafts.filter(d => d.status === '승인됨').length;

  return (
    <div>
      {/* 히어로 스트립 */}
      <div className="panel p-5 mb-4 flex items-center gap-6"
        style={{ background: 'linear-gradient(105deg, #eaf5f4 0%, #ffffff 55%, #faf7ef 100%)' }}>
        <div>
          <div className="text-lg font-bold text-[#0b4f4b]">디자인 스튜디오</div>
          <div className="text-sm text-slate-500 mt-1">
            등록한 <b className="text-[#008485]">디자인 자산이 조건</b>이 되어 화면 시안을 생성하고,
            <b className="text-amber-700"> 승인</b>은 AgentCore Memory의 <b>few-shot 학습</b>으로 다음 생성에 반영됩니다.
          </div>
        </div>
        <div className="flex gap-3 ml-auto">
          {[['자산', assets.length, '#008485'], ['시안', drafts.length, '#AD9A5F'], ['승인', approved, '#0e9f6e']].map(([l, v, c]) => (
            <div key={l as string} className="text-center px-4 py-2 rounded-xl bg-white border border-slate-200">
              <div className="text-xl font-bold" style={{ color: c as string }}>{v as number}</div>
              <div className="text-[11px] text-slate-500">{l}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 mb-4">
        {([['gallery', '🖼 시안 갤러리'], ['generate', '✨ 생성 플레이그라운드'], ['assets', '🎨 디자인 자산']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-4 py-2 rounded-xl text-sm font-semibold border ${tab === id
              ? 'bg-[#008485] text-white border-[#008485]'
              : 'bg-white text-slate-600 border-slate-200 hover:border-teal-400'}`}>{label}</button>
        ))}
        {!canWrite && <span className="text-xs text-slate-400 ml-2">이 계정은 조회 전용입니다</span>}
      </div>

      {tab === 'gallery' && <Gallery drafts={drafts} canWrite={canWrite} reload={load} />}
      {tab === 'generate' && <Generate assets={assets} canWrite={canWrite} reload={load} />}
      {tab === 'assets' && <Assets assets={assets} canWrite={canWrite} reload={load} />}
    </div>
  );
}

/* ---------------- 갤러리: 대형 프리뷰 카드 ---------------- */
function Gallery({ drafts, canWrite, reload }: { drafts: Draft[]; canWrite: boolean; reload: () => void }) {
  const [filter, setFilter] = useState<'전체' | '검토중' | '승인됨'>('전체');
  const [busy, setBusy] = useState('');
  const list = drafts.filter(d => filter === '전체' || d.status === filter);
  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setBusy(id);
    await sock.request('studio_feedback', { studioToken: auth.studioToken, draftId: id, decision }).catch(() => {});
    setBusy(''); reload();
  };
  const color = (s: string) => s === '승인됨' ? 'text-emerald-600 border-emerald-300 bg-emerald-50'
    : s === '반려' ? 'text-[#E90061] border-rose-300 bg-rose-50' : 'text-amber-700 border-amber-300 bg-amber-50';
  return (
    <div>
      <div className="flex gap-2 mb-3">
        {(['전체', '검토중', '승인됨'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`chip ${filter === f ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{f}</button>
        ))}
        <span className="text-xs text-slate-400 self-center ml-2">승인된 시안은 다음 생성의 few-shot 레퍼런스로 주입됩니다</span>
      </div>
      <div className="grid grid-cols-3 gap-5">
        {list.map(d => (
          <div key={d.id} className="panel overflow-hidden group hover:shadow-lg transition-shadow">
            <div className="relative h-72 overflow-hidden bg-slate-50 border-b border-slate-100">
              <iframe src={d.url} title={d.title} sandbox="allow-same-origin"
                className="pointer-events-none origin-top-left"
                style={{ width: '200%', height: '200%', transform: 'scale(0.5)' }} />
              <a href={d.url} target="_blank" rel="noopener"
                className="absolute inset-0 flex items-end justify-end p-2 opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ background: 'linear-gradient(transparent 65%, rgba(11,47,43,.45))' }}>
                <span className="text-white text-xs bg-[#008485] px-3 py-1.5 rounded-lg">원본 크게 보기 ↗</span>
              </a>
            </div>
            <div className="p-4">
              <div className="text-sm font-bold text-slate-800 truncate">{d.title}</div>
              <div className="flex items-center gap-2 mt-2">
                <span className={`chip text-[11px] ${color(d.status)}`}>{d.status}</span>
                <span className="chip text-[11px] text-slate-500">{d.axis}</span>
                {d.status === '승인됨' && <span className="chip text-[11px] text-teal-700 border-teal-300">few-shot 반영</span>}
              </div>
              {canWrite && d.status === '검토중' && (
                <div className="flex gap-2 mt-3">
                  <button disabled={busy === d.id} onClick={() => decide(d.id, 'approve')}
                    className="flex-1 py-1.5 rounded-lg text-xs font-semibold bg-[#008485] text-white hover:bg-[#0a6b6c]">✓ 승인</button>
                  <button disabled={busy === d.id} onClick={() => decide(d.id, 'reject')}
                    className="flex-1 py-1.5 rounded-lg text-xs font-semibold border border-rose-300 text-[#E90061] hover:bg-rose-50">반려</button>
                </div>
              )}
            </div>
          </div>
        ))}
        {list.length === 0 && <div className="text-slate-400 text-sm col-span-3">시안이 없습니다 — 생성 플레이그라운드에서 만들어 보세요.</div>}
      </div>
    </div>
  );
}

/* ---------------- 생성 플레이그라운드 ---------------- */
function Generate({ assets, canWrite, reload }: { assets: Asset[]; canWrite: boolean; reload: () => void }) {
  const [models, setModels] = useState<any[]>([]);
  const [brief, setBrief] = useState(BRIEF_PRESETS[0].brief);
  const [modelId, setModelId] = useState('');
  const [sel, setSel] = useState<string[]>([]);
  const [job, setJob] = useState<any>(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { sock.request('studio_models').then(e => setModels(e.models || [])).catch(() => {}); }, []);
  const groups = useMemo(() => {
    const g: Record<string, Asset[]> = {};
    for (const a of assets.filter(a => a.type !== 'agent')) (g[a.type] = g[a.type] || []).push(a);
    return g;
  }, [assets]);
  const toggle = (id: string) => setSel(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const run = async () => {
    if (!canWrite) return;
    setMsg(''); setJob({ status: 'running' });
    const r = await sock.request('studio_generate', { studioToken: auth.studioToken, brief, modelId, assetIds: sel });
    if (r.error) { setMsg('오류: ' + r.error); setJob(null); return; }
    setJob({ job_id: r.job_id, status: 'running' });
    const poll = async () => {
      const j = await sock.request('studio_jobs', { jobId: r.job_id }).catch(() => ({} as any));
      const st = (j.job || {}).status || 'running';
      setJob({ ...(j.job || {}), job_id: r.job_id, status: st });
      if (st === 'running') setTimeout(poll, 6000); else reload();
    };
    setTimeout(poll, 6000);
  };
  return (
    <div className="grid grid-cols-[1.1fr_1fr] gap-5">
      <div className="panel p-5">
        <div className="text-sm font-bold text-slate-800 mb-2">① 브리프 — 어떤 화면인가요?</div>
        <div className="flex gap-2 mb-2">
          {BRIEF_PRESETS.map(p => (
            <button key={p.label} onClick={() => setBrief(p.brief)}
              className={`chip text-xs ${brief === p.brief ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500 hover:border-teal-300'}`}>{p.label}</button>
          ))}
        </div>
        <textarea className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-24"
          value={brief} onChange={e => setBrief(e.target.value)} />
        <div className="text-sm font-bold text-slate-800 mt-4 mb-2">② 자산 선택 — 시안의 조건이 됩니다</div>
        {Object.entries(groups).map(([t, list]) => (
          <div key={t} className="mb-2">
            <span className="text-[11px] text-slate-400 mr-2">{TYPE_LABEL[t] || t}</span>
            {list.map(a => (
              <button key={aid(a)} onClick={() => toggle(aid(a))}
                className={`chip text-xs mr-1 mb-1 ${sel.includes(aid(a)) ? 'text-teal-800 border-teal-400 bg-teal-50' : 'text-slate-500 hover:border-slate-400'}`}>{a.name}</button>
            ))}
          </div>
        ))}
        <div className="text-sm font-bold text-slate-800 mt-4 mb-2">③ 모델</div>
        <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm"
          value={modelId} onChange={e => setModelId(e.target.value)}>
          <option value="">기본 모델</option>
          {models.map((m: any) => <option key={m.id || m} value={m.id || m}>{m.name || m.id || m}</option>)}
        </select>
        <button onClick={run} disabled={!canWrite || job?.status === 'running'}
          className="w-full mt-4 py-2.5 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-bold text-sm disabled:opacity-40">
          {job?.status === 'running' ? '생성 중… (3축 variant)' : '✨ 시안 생성 — 밀도·강조·흐름 3안'}
        </button>
        {msg && <div className="text-[#E90061] text-xs mt-2">{msg}</div>}
      </div>
      <div className="panel p-5">
        <div className="text-sm font-bold text-slate-800 mb-2">작동 방식</div>
        <ol className="text-sm text-slate-600 space-y-2 list-decimal ml-4">
          <li>브리프 + 선택한 자산(팔레트·토큰·스타일가이드)이 프롬프트 조건으로 주입됩니다.</li>
          <li>AgentCore Runtime의 디자인 에이전트가 <b>밀도·강조·흐름 3가지 축</b>으로 variant를 생성합니다.</li>
          <li>갤러리에서 <b className="text-[#008485]">승인</b>하면 AgentCore Memory에 기록되어 <b>다음 생성의 few-shot 레퍼런스</b>가 됩니다 — 쓸수록 취향에 수렴.</li>
        </ol>
        {job && (
          <div className="mt-4 p-3 rounded-xl border border-slate-200 bg-slate-50 text-sm">
            잡 <span className="font-mono text-xs">{job.job_id || '…'}</span> — 상태:{' '}
            <b className={job.status === 'done' ? 'text-emerald-600' : 'text-amber-700'}>{job.status}</b>
            {job.status === 'running' && <span className="text-slate-500"> · 1~2분 소요, 완료 시 갤러리에 3안이 추가됩니다</span>}
            {job.status === 'done' && <span className="text-slate-600"> · 갤러리 탭에서 확인하세요 ✨</span>}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- 디자인 자산 (팔레트 스와치 카드) ---------------- */
function Assets({ assets, canWrite, reload }: { assets: Asset[]; canWrite: boolean; reload: () => void }) {
  const [sel, setSel] = useState<any>(null);
  const [reg, setReg] = useState({ name: '', assetType: 'palette', content: '' });
  const [msg, setMsg] = useState('');
  const open = async (a: Asset) => {
    const r = await sock.request('studio_asset', { assetId: aid(a) }).catch(() => ({} as any));
    const c = (r as any).content;  // 응답 전체를 스프레드하면 r.type이 자산 type을 덮어쓴다 — 필요한 필드만
    setSel({ ...a,
      history: (r as any).history,
      content: (c && typeof c === 'object' && 'content' in c) ? (c as any).content : c });
  };
  const register = async () => {
    if (!reg.name || !reg.content) { setMsg('이름과 내용을 입력하세요'); return; }
    const r = await sock.request('studio_register', { studioToken: auth.studioToken, ...reg });
    setMsg(r.error ? '오류: ' + r.error : `등록됨 (v${r.version || '?'})`);
    if (!r.error) { setReg({ name: '', assetType: 'palette', content: '' }); reload(); }
  };
  return (
    <div className="grid grid-cols-[1.3fr_1fr] gap-5">
      <div className="grid grid-cols-2 gap-3 self-start">
        {assets.map((a, i) => (
          <button key={i} onClick={() => open(a)}
            className={`panel p-4 text-left hover:shadow-md transition-shadow ${sel && aid(sel) === aid(a) ? 'border-teal-400' : ''}`}>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-slate-800 truncate">{a.name}</span>
              <span className="chip text-[10px] text-teal-700 border-teal-300 ml-auto">{TYPE_LABEL[a.type] || a.type}</span>
            </div>
            <div className="text-[11px] text-slate-400 mt-1">{String(a.version).startsWith('v') ? a.version : `v${a.version}`} · {a.actor}</div>
          </button>
        ))}
      </div>
      <div className="space-y-4 self-start">
        {sel && (
          <div className="panel p-4">
            <div className="text-sm font-bold text-slate-800">{sel.name}
              <span className="chip text-[10px] ml-2 text-teal-700 border-teal-300">{TYPE_LABEL[sel.type] || sel.type}</span></div>
            {Array.isArray(sel.history) && sel.history.length > 0 && (
              <div className="text-xs text-slate-400 mt-1">버전 이력: {sel.history.map((h: any) => String(h.version).startsWith('v') ? h.version : `v${h.version}`).join(' → ')}</div>
            )}
            {sel.type === 'palette' && <Swatches content={sel.content} />}
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded-xl p-3 mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-slate-700">
              {typeof sel.content === 'object' ? JSON.stringify(sel.content, null, 2).slice(0, 2500) : String(sel.content || '').slice(0, 2500)}</pre>
          </div>
        )}
        {canWrite && (
          <div className="panel p-4">
            <div className="text-sm font-bold text-slate-800 mb-2">새 자산 등록 <span className="text-[11px] font-normal text-slate-400">— 버전 이력이 기록됩니다</span></div>
            <input className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm"
              placeholder="이름" value={reg.name} onChange={e => setReg({ ...reg, name: e.target.value })} />
            <select className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm"
              value={reg.assetType} onChange={e => setReg({ ...reg, assetType: e.target.value })}>
              {ASSET_TYPES.map(t => <option key={t} value={t}>{TYPE_LABEL[t] || t}</option>)}
            </select>
            <textarea className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-24 font-mono"
              placeholder="내용 (markdown 또는 JSON)" value={reg.content} onChange={e => setReg({ ...reg, content: e.target.value })} />
            <button onClick={register} className="w-full py-2 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm">등록</button>
            {msg && <div className="text-xs text-slate-500 mt-2">{msg}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
