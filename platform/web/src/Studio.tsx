// 디자인 스튜디오 — 네이티브 통합 (embed 아님).
// 데이터는 서버사이드 프록시로 스튜디오 API에서 가져오고, 쓰기(승인/반려·생성·자산 등록)는
// 로그인 시 함께 받은 사용자 본인의 스튜디오 토큰을 전달한다.
import { useEffect, useState } from 'react';
import { auth, sock } from './lib';

type Draft = { id: string; title: string; axis: string; status: string; url: string; created_at: string };
type Asset = { name: string; type: string; version: string; actor: string; updated_at: string; scope: string; asset_id?: string };

const ASSET_TYPES = ['token', 'palette', 'icon-set', 'component', 'style-guide', 'skill', 'workflow', 'agent'];

export default function Studio() {
  const [tab, setTab] = useState<'gallery' | 'assets' | 'generate'>('gallery');
  const canWrite = !!auth.studioToken;
  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        {([['gallery', '갤러리'], ['assets', '디자인 자산'], ['generate', '생성']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`chip ${tab === id ? 'text-sky-300 border-sky-700' : 'text-slate-400 hover:border-slate-500'}`}>{label}</button>
        ))}
        <span className="text-xs text-slate-500 ml-2">
          소스 기반 네이티브 통합 — 스튜디오 백엔드(자산 레지스트리·AgentCore Memory·Runtime)를 그대로 사용
          {!canWrite && ' · 이 계정은 스튜디오 쓰기 권한이 없어 조회만 가능합니다'}
        </span>
      </div>
      {tab === 'gallery' && <Gallery canWrite={canWrite} />}
      {tab === 'assets' && <Assets canWrite={canWrite} />}
      {tab === 'generate' && <Generate canWrite={canWrite} />}
    </div>
  );
}

function Gallery({ canWrite }: { canWrite: boolean }) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [busy, setBusy] = useState('');
  const load = () => sock.request('studio_drafts').then(e => setDrafts(e.drafts || [])).catch(() => {});
  useEffect(() => { load(); }, []);
  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setBusy(id);
    await sock.request('studio_feedback', { studioToken: auth.studioToken, draftId: id, decision })
      .catch(() => {});
    setBusy(''); load();
  };
  const color = (s: string) => s === '승인됨' ? 'text-emerald-400' : s === '반려' ? 'text-rose-400' : 'text-amber-300';
  return (
    <div className="grid grid-cols-3 gap-4">
      {drafts.map(d => (
        <div key={d.id} className="panel overflow-hidden">
          <iframe src={d.url} className="w-full h-56 bg-white pointer-events-none" style={{ zoom: 0.55 } as any}
            sandbox="allow-same-origin" title={d.title} />
          <div className="p-3">
            <div className="text-sm font-semibold truncate">{d.title}</div>
            <div className="flex items-center gap-2 mt-1">
              <span className={`text-xs font-semibold ${color(d.status)}`}>{d.status}</span>
              <span className="chip text-[10px]">{d.axis}</span>
              <a className="text-xs text-sky-400 ml-auto" href={d.url} target="_blank" rel="noopener">원본 ↗</a>
            </div>
            {canWrite && d.status === '검토중' && (
              <div className="flex gap-2 mt-2">
                <button disabled={busy === d.id} onClick={() => decide(d.id, 'approve')}
                  className="chip text-emerald-300 hover:border-emerald-600 flex-1 justify-center">승인 → few-shot 학습</button>
                <button disabled={busy === d.id} onClick={() => decide(d.id, 'reject')}
                  className="chip text-rose-300 hover:border-rose-600 flex-1 justify-center">반려</button>
              </div>
            )}
          </div>
        </div>
      ))}
      {drafts.length === 0 && <div className="text-slate-500 text-sm">시안이 없습니다 — 생성 탭에서 만들어 보세요.</div>}
    </div>
  );
}

function Assets({ canWrite }: { canWrite: boolean }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [sel, setSel] = useState<any>(null);
  const [reg, setReg] = useState({ name: '', assetType: 'palette', content: '' });
  const [msg, setMsg] = useState('');
  const load = () => sock.request('assets').then(e => setAssets(e.assets || [])).catch(() => {});
  useEffect(() => { load(); }, []);
  const open = async (a: Asset) => {
    const id = (a as any).asset_id || `${a.type}:${a.name}`;
    const r = await sock.request('studio_asset', { assetId: id });
    setSel({ ...a, ...r });
  };
  const register = async () => {
    if (!reg.name || !reg.content) { setMsg('이름과 내용을 입력하세요'); return; }
    const r = await sock.request('studio_register', { studioToken: auth.studioToken, ...reg });
    setMsg(r.error ? '오류: ' + r.error : `등록됨 (v${r.version || '?'})`);
    if (!r.error) { setReg({ name: '', assetType: 'palette', content: '' }); load(); }
  };
  return (
    <div className="grid grid-cols-[1fr_420px] gap-4">
      <div className="panel overflow-hidden self-start">
        <table className="w-full text-sm">
          <thead><tr className="text-xs text-slate-500 border-b border-slate-800">
            <th className="text-left p-3">이름</th><th className="text-left p-3">타입</th>
            <th className="text-left p-3">버전</th><th className="text-left p-3">등록자</th></tr></thead>
          <tbody>{assets.map((a, i) => (
            <tr key={i} onClick={() => open(a)} className="border-b border-slate-900 cursor-pointer hover:bg-slate-900/60">
              <td className="p-3">{a.name}</td>
              <td className="p-3"><span className="chip text-[10px]">{a.type}</span></td>
              <td className="p-3 text-xs">v{a.version}</td>
              <td className="p-3 text-xs text-slate-500">{a.actor}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="space-y-4">
        {sel && (
          <div className="panel p-4">
            <div className="text-sm font-semibold mb-2">{sel.name} <span className="chip text-[10px] ml-1">{sel.type}</span></div>
            {Array.isArray(sel.history) && sel.history.length > 0 && (
              <div className="text-xs text-slate-400 mb-2">버전 이력: {sel.history.map((h: any) => `v${h.version}`).join(' → ')}</div>
            )}
            <pre className="text-xs bg-slate-950 rounded p-2 max-h-64 overflow-auto whitespace-pre-wrap">
              {typeof sel.content === 'object' ? JSON.stringify(sel.content, null, 2).slice(0, 3000) : String(sel.content || '').slice(0, 3000)}</pre>
          </div>
        )}
        {canWrite && (
          <div className="panel p-4">
            <div className="text-sm font-semibold mb-2">새 자산 등록</div>
            <input className="w-full mb-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
              placeholder="이름" value={reg.name} onChange={e => setReg({ ...reg, name: e.target.value })} />
            <select className="w-full mb-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
              value={reg.assetType} onChange={e => setReg({ ...reg, assetType: e.target.value })}>
              {ASSET_TYPES.map(t => <option key={t}>{t}</option>)}
            </select>
            <textarea className="w-full mb-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm h-28 font-mono"
              placeholder="내용 (markdown 또는 JSON)" value={reg.content} onChange={e => setReg({ ...reg, content: e.target.value })} />
            <button onClick={register} className="w-full py-2 rounded-lg bg-sky-500/90 text-slate-950 font-semibold text-sm">등록 (버전 히스토리 기록)</button>
            {msg && <div className="text-xs text-slate-400 mt-2">{msg}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

function Generate({ canWrite }: { canWrite: boolean }) {
  const [models, setModels] = useState<any[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [brief, setBrief] = useState('모바일 대출 신청 완료 화면 — 안심되는 톤, 다음 단계 안내 포함');
  const [modelId, setModelId] = useState('');
  const [selAssets, setSelAssets] = useState<string[]>([]);
  const [job, setJob] = useState<any>(null);
  const [msg, setMsg] = useState('');
  useEffect(() => {
    sock.request('studio_models').then(e => { setModels(e.models || []); }).catch(() => {});
    sock.request('assets').then(e => setAssets((e.assets || []).filter((a: Asset) => a.type !== 'agent'))).catch(() => {});
  }, []);
  const toggle = (id: string) => setSelAssets(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const run = async () => {
    setMsg(''); setJob(null);
    const r = await sock.request('studio_generate', {
      studioToken: auth.studioToken, brief, modelId, assetIds: selAssets });
    if (r.error) { setMsg('오류: ' + r.error); return; }
    setJob({ job_id: r.job_id, status: 'running' });
    const poll = async () => {
      const j = await sock.request('studio_jobs', { jobId: r.job_id });
      setJob(j.job || { job_id: r.job_id, status: '?' });
      if ((j.job || {}).status === 'running') setTimeout(poll, 5000);
    };
    setTimeout(poll, 5000);
  };
  if (!canWrite) return <div className="text-slate-500 text-sm">이 계정은 스튜디오 생성 권한이 없습니다.</div>;
  return (
    <div className="max-w-2xl space-y-3">
      <textarea className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm h-24"
        value={brief} onChange={e => setBrief(e.target.value)} placeholder="디자인 브리프" />
      <select className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm"
        value={modelId} onChange={e => setModelId(e.target.value)}>
        <option value="">기본 모델</option>
        {models.map((m: any) => <option key={m.id || m} value={m.id || m}>{m.name || m.id || m}</option>)}
      </select>
      <div className="flex flex-wrap gap-1">
        {assets.map(a => {
          const id = (a as any).asset_id || `${a.type}:${a.name}`;
          return <button key={id} onClick={() => toggle(id)}
            className={`chip text-xs ${selAssets.includes(id) ? 'text-sky-300 border-sky-700' : 'text-slate-400'}`}>{a.name}</button>;
        })}
      </div>
      <button onClick={run} className="px-5 py-2 rounded-lg bg-sky-500/90 text-slate-950 font-semibold text-sm">
        생성 (3개 축 variant · AgentCore Runtime)</button>
      {job && <div className="panel p-3 text-sm">
        잡 {job.job_id || job.asset_id} — 상태: <b className={job.status === 'done' ? 'text-emerald-400' : 'text-amber-300'}>{job.status}</b>
        {job.status === 'done' && <span className="text-slate-400"> · 갤러리 탭에서 새 시안을 확인하세요</span>}
        {job.status === 'running' && <span className="text-slate-500"> · 1~2분 소요 (비동기)</span>}
      </div>}
      {msg && <div className="text-rose-400 text-sm">{msg}</div>}
    </div>
  );
}
