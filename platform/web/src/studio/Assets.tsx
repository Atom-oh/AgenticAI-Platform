// platform/web/src/studio/Assets.tsx
import { useMemo, useState } from 'react';
import { auth, sock } from '../lib';
import { ASSET_TYPES, Asset, TYPE_LABEL, aid } from './types';

function Swatches({ content }: { content: any }) {
  const colors = useMemo(() => {
    try { const o = typeof content === 'string' ? JSON.parse(content) : content;
      return Object.entries(o || {}).filter(([, v]) => typeof v === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(v as string)) as [string, string][]; } catch { return []; }
  }, [content]);
  if (!colors.length) return null;
  return <div className="flex gap-1 mt-2 flex-wrap">{colors.slice(0, 10).map(([k, v]) => <span key={k} title={`${k} ${v}`} className="w-6 h-6 rounded-lg border border-slate-200" style={{ background: v }} />)}</div>;
}

export default function Assets({ assets, canRegister, reload }: { assets: Asset[]; canRegister: boolean; reload: () => void }) {
  const [typeFilter, setTypeFilter] = useState('');
  const [sel, setSel] = useState<any>(null);
  const [reg, setReg] = useState({ name: '', assetType: 'palette', content: '', scope: 'shared' });
  const [msg, setMsg] = useState('');
  const list = assets.filter(a => !typeFilter || a.type === typeFilter);
  const open = async (a: Asset) => {
    const r = await sock.request('studio_asset', { assetId: aid(a) }).catch(() => ({} as any));
    const c = (r as any).content;
    setSel({ ...a, history: (r as any).history, content: (c && typeof c === 'object' && 'content' in c) ? (c as any).content : c });
  };
  const register = async () => {
    if (!reg.name || !reg.content) { setMsg('이름과 내용을 입력하세요'); return; }
    const r = await sock.request('studio_register', { studioToken: auth.studioToken, ...reg });
    setMsg(r.error ? '오류: ' + r.error : `등록됨 (v${r.version || '?'})`);
    if (!r.error) { setReg({ name: '', assetType: 'palette', content: '', scope: 'shared' }); reload(); }
  };
  const ver = (v: any) => String(v).startsWith('v') ? String(v) : `v${v}`;
  return (
    <div className="grid grid-cols-[1.3fr_1fr] gap-5">
      <div className="self-start">
        <div className="flex gap-1.5 mb-3 flex-wrap">
          <button onClick={() => setTypeFilter('')} className={`chip text-xs ${!typeFilter ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>전체 {assets.length}</button>
          {ASSET_TYPES.map(t => { const n = assets.filter(a => a.type === t).length; return n ? <button key={t} onClick={() => setTypeFilter(t)} className={`chip text-xs ${typeFilter === t ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{TYPE_LABEL[t]} {n}</button> : null; })}
        </div>
        <div className="grid grid-cols-2 gap-3">
          {list.map((a, i) => (
            <button key={i} onClick={() => open(a)} className={`panel p-4 text-left hover:shadow-md transition-shadow ${sel && aid(sel) === aid(a) ? 'border-teal-400' : ''}`}>
              <div className="flex items-center gap-2"><span className="text-sm font-bold text-slate-800 truncate">{a.name}</span>
                <span className="chip text-[10px] text-teal-700 border-teal-300 ml-auto">{TYPE_LABEL[a.type] || a.type}</span></div>
              <div className="text-[11px] text-slate-400 mt-1">{ver(a.version)} · {a.actor} · {a.scope === 'mine' ? '내 자산' : '공유'}</div>
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-4 self-start">
        {sel && (
          <div className="panel p-4">
            <div className="text-sm font-bold text-slate-800">{sel.name}<span className="chip text-[10px] ml-2 text-teal-700 border-teal-300">{TYPE_LABEL[sel.type] || sel.type}</span></div>
            {Array.isArray(sel.history) && sel.history.length > 0 && (
              <ol className="mt-2 text-[11px] text-slate-500 border-l-2 border-teal-200 pl-3 space-y-1">
                {sel.history.map((h: any, i: number) => <li key={i}><b className="text-slate-700">{ver(h.version)}</b> · {h.action || '등록'} · {h.actor || ''} · {String(h.updated_at || h.ts || '').slice(0, 19)}</li>)}
              </ol>)}
            {sel.type === 'palette' && <Swatches content={sel.content} />}
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded-xl p-3 mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-slate-700">
              {typeof sel.content === 'object' ? JSON.stringify(sel.content, null, 2).slice(0, 2500) : String(sel.content || '').slice(0, 2500)}</pre>
          </div>
        )}
        <div className="panel p-4">
          <div className="text-sm font-bold text-slate-800 mb-2">새 자산 등록 <span className="text-[11px] font-normal text-slate-400">— uiux-studio 레지스트리에 버전 이력이 기록됩니다</span></div>
          {!canRegister && <div className="text-xs text-slate-400 mb-2">스튜디오 토큰이 없는 계정은 등록할 수 없습니다 (조회만 가능)</div>}
          <input className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" placeholder="이름" value={reg.name} onChange={e => setReg({ ...reg, name: e.target.value })} />
          <div className="flex gap-2 mb-2">
            <select className="flex-1 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={reg.assetType} onChange={e => setReg({ ...reg, assetType: e.target.value })}>
              {ASSET_TYPES.map(t => <option key={t} value={t}>{TYPE_LABEL[t] || t}</option>)}</select>
            <select className="px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={reg.scope} onChange={e => setReg({ ...reg, scope: e.target.value })}>
              <option value="shared">공유</option><option value="mine">내 자산</option></select>
          </div>
          <textarea className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-24 font-mono" placeholder="내용 (markdown 또는 JSON)" value={reg.content} onChange={e => setReg({ ...reg, content: e.target.value })} />
          <button onClick={register} disabled={!canRegister} className="w-full py-2 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm disabled:opacity-40">등록</button>
          {msg && <div className="text-xs text-slate-500 mt-2">{msg}</div>}
        </div>
      </div>
    </div>
  );
}
