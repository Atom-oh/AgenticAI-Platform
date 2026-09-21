import { useEffect, useRef, useState } from 'react';
import { loadReactArchive, loadReactSources } from './reactCatalog';
import type { SourceFile } from '../../portal-react/client';

function download(name: string, contents: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function SourceSession({ hash, version }: { hash: string; version: string }) {
  const active = useRef(true);
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [files, setFiles] = useState<readonly SourceFile[] | null>(null);
  const [path, setPath] = useState('ui/index.tsx');
  const [open, setOpen] = useState(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const perform = async (action: () => Promise<void>) => {
    if (busy.current) return;
    busy.current = true; setPending(true); setError('');
    try { await action(); }
    catch { if (active.current) setError('소스 파일을 확인하지 못했습니다. 다시 시도하거나 화면을 새로고침하세요.'); }
    finally { if (active.current) { busy.current = false; setPending(false); } }
  };
  const file = files?.find(item => item.path === path);
  return <section className="portal-react-source" aria-label="React 컴포넌트 소스">
    <div className="portal-version-tabs">
      <button type="button" disabled={pending} onClick={() => void perform(async () => {
        const bytes = await loadReactArchive(hash);
        if (active.current) download(`studio-ui-${version}-${hash.slice(0, 12)}.zip`, bytes, 'application/zip');
      })}>React 코드 다운로드 (ZIP)</button>
      <button type="button" aria-expanded={open} disabled={pending} onClick={() => {
        if (open) { setOpen(false); return; }
        void perform(async () => {
          const value = files || await loadReactSources(hash);
          if (active.current) { setFiles(value); setOpen(true); }
        });
      }}>소스 보기</button>
    </div>
    <p className="portal-preview-note">컴포넌트가 함께 사용하는 TSX·타입·공통 코드·CSS와 적용 안내를 제공합니다. 기존 React 프로젝트에 폴더째 추가하세요.</p>
    {pending && <p role="status">현재 버전의 소스 파일을 확인하고 있습니다…</p>}
    {error && <p role="alert">{error}</p>}
    {open && files && <div className="portal-version-source">
      <label>소스 파일 <select aria-label="컴포넌트 소스 파일" value={path} onChange={event => setPath(event.target.value)}>
        {files.map(item => <option key={item.path} value={item.path}>{item.path}</option>)}
      </select></label>
      {file && <>
        <pre tabIndex={0} className="portal-code-example">{file.content}</pre>
        <button type="button" onClick={() => download(file.path.split('/').at(-1)!, file.content, 'text/plain;charset=utf-8')}>
          현재 파일 다운로드
        </button>
      </>}
    </div>}
  </section>;
}

export default function ReactSourcePanel({ hash, version }: { hash: string; version: string }) {
  return <SourceSession key={`${hash}:${version}`} hash={hash} version={version} />;
}
