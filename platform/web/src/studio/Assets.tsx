// platform/web/src/studio/Assets.tsx
import { useId, useMemo, useState } from 'react';
import { auth, sock } from '../lib';
import { ASSET_TYPES, Asset, TYPE_LABEL, aid } from './types';

// Keep the character limit aligned with studio.asset_import.MAX_CONTENT_CHARS.
const MAX_CONTENT_CHARS = 20_000;
const MAX_FILE_BYTES = MAX_CONTENT_CHARS * 4; // UTF-8 uses at most four bytes per code point.
const JSON_ASSET_TYPES = new Set(['token', 'palette', 'icon-set', 'agent']);

function contentError(content: string, assetType: string, jsonFile = false): string {
  if (!content.trim()) return '자산 내용이 비어 있습니다. 내용을 입력하세요.';
  // Count Unicode code points, as Python len(str) does, including supplementary characters.
  if ([...content].length > MAX_CONTENT_CHARS) return '자산 내용은 20,000자 이하여야 합니다. 내용을 줄여 주세요.';
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(content)) {
    return '텍스트가 아닌 제어 문자가 있습니다. JSON, Markdown 또는 TXT 텍스트 파일을 선택하세요.';
  }
  if (jsonFile || JSON_ASSET_TYPES.has(assetType)) {
    let parsed: unknown;
    try { parsed = JSON.parse(content); } catch {
      return jsonFile
        ? 'JSON 형식이 올바르지 않습니다. 따옴표와 쉼표 등 파일 내용을 확인하세요.'
        : `${TYPE_LABEL[assetType]} 자산은 올바른 JSON 내용이 필요합니다. 자산 유형이나 내용을 확인하세요.`;
    }
    if (assetType === 'agent' && (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed))) {
      return '에이전트 JSON은 객체({...})여야 합니다. 배열이나 단일 값은 사용할 수 없습니다.';
    }
  }
  return '';
}

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
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ error: boolean; text: string } | null>(null);
  const formId = useId();
  const importFile = async (file: File) => {
    setMsg('');
    setImportResult(null);
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!file.name.includes('.') || !['json', 'md', 'markdown', 'txt'].includes(extension || '')) {
      setImportResult({ error: true, text: '지원하지 않는 파일 확장자입니다. .json, .md, .markdown, .txt 파일을 선택하세요.' });
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setImportResult({ error: true, text: '파일 크기는 80,000바이트 이하여야 합니다. 필요한 텍스트만 담은 파일을 선택하세요.' });
      return;
    }
    setImporting(true);
    try {
      let bytes: ArrayBuffer;
      try { bytes = await file.arrayBuffer(); } catch {
        setImportResult({ error: true, text: '파일을 읽을 수 없습니다. 로컬 파일을 다시 선택하세요.' });
        return;
      }
      let content: string;
      try {
        content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      } catch {
        setImportResult({ error: true, text: 'UTF-8 텍스트 파일이 아닙니다. UTF-8로 저장한 파일을 선택하세요.' });
        return;
      }
      const error = contentError(content, reg.assetType, extension === 'json');
      if (error) { setImportResult({ error: true, text: error }); return; }
      setReg(current => ({ ...current, content, name: current.name.trim() ? current.name : file.name.replace(/\.[^.]+$/, '') }));
      const checked = extension === 'json' || JSON_ASSET_TYPES.has(reg.assetType) ? 'JSON 구문 확인 완료' : '텍스트 확인 완료';
      setImportResult({ error: false, text: `${file.name}: ${checked}. 내용을 불러왔습니다. 아직 등록되지 않았습니다. 확인 후 등록 버튼을 누르세요.` });
    } finally {
      setImporting(false);
    }
  };
  const list = assets.filter(a => !typeFilter || a.type === typeFilter);
  const open = async (a: Asset) => {
    const r = await sock.request('studio_asset', { assetId: aid(a) }).catch(() => ({} as any));
    const c = (r as any).content;
    setSel({ ...a, history: (r as any).history, content: (c && typeof c === 'object' && 'content' in c) ? (c as any).content : c });
  };
  const register = async () => {
    if (!canRegister || importing) return;
    if (!reg.name.trim()) { setMsg('이름을 입력하세요'); return; }
    const error = contentError(reg.content, reg.assetType);
    if (error) { setMsg(error); return; }
    const r = await sock.request('studio_register', { studioToken: auth.studioToken, ...reg });
    setMsg(r.error ? '오류: ' + r.error : `등록됨 (v${r.version || '?'})`);
    if (!r.error) { setReg({ name: '', assetType: 'palette', content: '', scope: 'shared' }); setImportResult(null); reload(); }
  };
  const ver = (v: any) => String(v).startsWith('v') ? String(v) : `v${v}`;
  return (
    <div className="studio-assets-layout">
      <div className="self-start">
        <div className="flex gap-1.5 mb-3 flex-wrap">
          <button onClick={() => setTypeFilter('')} className={`chip text-xs ${!typeFilter ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>전체 {assets.length}</button>
          {ASSET_TYPES.map(t => { const n = assets.filter(a => a.type === t).length; return n ? <button key={t} onClick={() => setTypeFilter(t)} className={`chip text-xs ${typeFilter === t ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{TYPE_LABEL[t]} {n}</button> : null; })}
        </div>
        <div className="asset-cards">
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
          {!canRegister && <div className="text-xs text-slate-500 mb-2">공유 자산 등록 권한을 확인할 수 없습니다. 담당자에게 자산 등록 권한을 요청하세요. 파일 내용 확인은 가능합니다.</div>}
          <fieldset disabled={importing} aria-busy={importing}>
          <legend className="sr-only">새 자산 내용 입력</legend>
          <label htmlFor={`${formId}-name`} className="sr-only">자산 이름</label>
          <input id={`${formId}-name`} className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" placeholder="이름" value={reg.name} onChange={e => setReg({ ...reg, name: e.target.value })} />
          <div className="flex gap-2 mb-2">
            <label htmlFor={`${formId}-type`} className="sr-only">자산 유형</label>
            <select id={`${formId}-type`} className="flex-1 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={reg.assetType} onChange={e => { setReg({ ...reg, assetType: e.target.value }); setImportResult(null); setMsg(''); }}>
              {ASSET_TYPES.map(t => <option key={t} value={t}>{TYPE_LABEL[t] || t}</option>)}</select>
            <label htmlFor={`${formId}-scope`} className="sr-only">공개 범위</label>
            <select id={`${formId}-scope`} className="px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={reg.scope} onChange={e => setReg({ ...reg, scope: e.target.value })}>
              <option value="shared">공유</option><option value="mine">내 자산</option></select>
          </div>
          <label htmlFor={`${formId}-file`} className="block text-xs font-semibold text-slate-700 mb-1">로컬 텍스트 파일 불러오기</label>
          <p id={`${formId}-file-help`} className="text-xs text-slate-500 mb-2">
            Figma 내보내기·다운로드는 3호망에서 진행하세요. 금융망 Studio는 사용자가 선택한 로컬 파일만 읽으며 외부 Figma에 접속하지 않습니다.
            텍스트 자산 가져오기를 지원하며, 전체 .fig 파일·ZIP·이미지 패키지는 지원하지 않습니다.
            파일 선택은 아래 내용을 채웁니다. 등록은 내용을 확인한 뒤 별도로 진행하세요.
          </p>
          <p id={`${formId}-file-limits`} className="text-xs text-slate-500 mb-2">
            UTF-8 .json / .md / .markdown / .txt · 최대 80,000바이트, 내용 20,000자.
            먼저 자산 유형을 선택하세요. 토큰·팔레트·아이콘·에이전트는 JSON 내용이 필요합니다.
          </p>
          <input id={`${formId}-file`} type="file" accept=".json,.md,.markdown,.txt"
            aria-describedby={`${formId}-file-help ${formId}-file-limits ${formId}-file-result`}
            aria-invalid={importResult?.error || undefined}
            className="block w-full min-w-0 text-xs text-slate-600 mb-2"
            onChange={e => { const file = e.currentTarget.files?.[0]; e.currentTarget.value = ''; if (file) void importFile(file); }} />
          <div id={`${formId}-file-result`} role={importResult?.error ? 'alert' : 'status'} aria-atomic="true"
            className={`text-xs mb-2 break-words ${importResult?.error ? 'text-red-700' : 'text-teal-700'}`}>
            {importing ? '파일 내용을 확인하고 있습니다…' : importResult?.text}
          </div>
          <label htmlFor={`${formId}-content`} className="sr-only">자산 내용</label>
          <textarea id={`${formId}-content`} aria-describedby={`${formId}-file-limits`} className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-24 font-mono" placeholder="내용 (markdown 또는 JSON)" value={reg.content} onChange={e => { setReg({ ...reg, content: e.target.value }); setImportResult(null); setMsg(''); }} />
          <button onClick={register} disabled={!canRegister || importing} className="w-full py-2 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm disabled:opacity-40">등록</button>
          </fieldset>
          {msg && <div role="status" className="text-xs text-slate-500 mt-2">{msg}</div>}
        </div>
      </div>
    </div>
  );
}
