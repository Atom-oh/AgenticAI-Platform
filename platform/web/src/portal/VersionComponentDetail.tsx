import { useEffect, useRef, useState } from 'react';
import { loadVersionDocument, resetVersionDocument, matchBinding, type Binding, type VersionComponent, type VersionDocument } from '../../portal-versions/client';
import { CHANNEL, isSession, newSession, type Session } from '../../portal-versions/protocol';

function download(name: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function VersionSession({ binding }: { binding: Binding }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const session = useRef<Session | null>(null);
  const current = useRef<VersionDocument | null>(null);
  const sent = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const [loaded, setLoaded] = useState<{ document: VersionDocument; component: VersionComponent }>();
  const [state, setState] = useState<'loading' | 'rendered' | 'error'>('loading');
  const [height, setHeight] = useState(400);
  const [tab, setTab] = useState<'preview' | 'source'>('preview');
  const [path, setPath] = useState('');

  useEffect(() => {
    let active = true, failed = false;
    const fail = () => { if (active) { failed = true; clearTimeout(timer.current); setState('error'); } };
    const receive = (event: MessageEvent) => {
      const d = event.data;
      if (!active || failed || !current.current || !session.current ||
          event.source !== frame.current?.contentWindow || event.origin !== 'null' || !isSession(event.data) ||
          d.channel !== CHANNEL || d.nonce !== session.current.nonce || d.id !== session.current.id ||
          d.assetId !== binding.id || d.sourceHash !== binding.sourceHash || d.catalogHash !== current.current.catalog.hash) return;
      if (d.type === 'error') fail();
      if (d.type === 'rendered') { clearTimeout(timer.current); setState('rendered'); }
      if (d.type === 'resize' && Number.isFinite(d.height) && d.height >= 80 && d.height <= 2000) setHeight(Math.min(1200, Math.max(240, d.height)));
    };
    window.addEventListener('message', receive);
    session.current = newSession();
    void loadVersionDocument().then(document => {
      const component = matchBinding(document.catalog, binding);
      if (active) {
        current.current = document;
        setPath(component.entry);
        setLoaded({ document, component });
      }
    }).catch(fail);
    return () => {
      active = false; clearTimeout(timer.current);
      current.current = null; session.current = null;
      window.removeEventListener('message', receive);
    };
  }, [binding.id, binding.name, binding.version, binding.package, binding.exportName, binding.sourceHash]);

  const render = () => {
    if (sent.current || !session.current || !current.current || !frame.current?.contentWindow) return;
    sent.current = true;
    timer.current = setTimeout(() => setState('error'), 10_000);
    frame.current.contentWindow.postMessage({
      channel: CHANNEL, type: 'render', ...session.current, assetId: binding.id,
      version: binding.version, sourceHash: binding.sourceHash, catalogHash: current.current.catalog.hash,
    }, '*');
  };
  const component = loaded?.component;
  const file = component?.files.find(f => f.path === path);
  return <section className="portal-version-detail" aria-label="버전에 연결된 React 구현"
    data-version-state={state} data-version-id={binding.id}>
    <p className="portal-eyebrow">React 소스 연결 · v{binding.version}</p>
    <p className="portal-preview-note">이 자산 ID에 연결한 플랫폼 참조 구현입니다. 고객 원본 패키지와 운영 승인 여부는 별도입니다.</p>
    {state === 'error' && <p role="alert">구현 파일을 확인하지 못했거나 API와 웹의 버전이 다릅니다. 새로고침 후 다시 확인해 주세요.</p>}
    {!loaded && state === 'loading' && <p role="status">버전과 소스 파일을 확인하고 있습니다…</p>}
    {loaded && component && <>
      <div className="portal-version-tabs" aria-label="구현 보기">
        <button type="button" aria-pressed={tab === 'preview'} onClick={() => setTab('preview')}>실행 예제</button>
        <button type="button" aria-pressed={tab === 'source'} onClick={() => setTab('source')}>React 소스 · {component.files.length}개 파일</button>
      </div>
      <div hidden={tab !== 'preview'}>
        {state === 'loading' && <p role="status">React 실행 예제를 불러오고 있습니다…</p>}
        {state !== 'error' && <iframe ref={frame} data-portal-version="" title={`${binding.name} v${binding.version} 실행 예제`}
          sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={loaded.document.html} onLoad={render}
          style={{ width: '100%', height, display: 'block', border: 0, visibility: state === 'rendered' ? 'visible' : 'hidden' }} />}
      </div>
      <div hidden={tab !== 'source'} className="portal-version-source">
        <label>소스 파일 <select aria-label="소스 파일" value={path} onChange={e => setPath(e.target.value)}>
          {component.files.map(f => <option key={f.path} value={f.path}>{f.path}</option>)}
        </select></label>
        {file && <>
          <pre tabIndex={0} className="portal-code-example">{file.content}</pre>
          <button type="button" onClick={() => download(file.path.split('/').at(-1)!, file.content, 'text/plain;charset=utf-8')}>현재 파일 다운로드</button>
        </>}
        <button type="button" onClick={() => download(`${component.id}-source.json`, JSON.stringify({
          package: binding.package, id: binding.id, version: binding.version, sourceHash: binding.sourceHash, files: component.files,
        }, null, 2), 'application/json')}>전체 소스 묶음 다운로드 (JSON)</button>
        <p className="portal-preview-note">파일 경로를 유지해 로컬 프로젝트에 추가하세요. 공유 파일과 CSS도 함께 필요합니다.</p>
      </div>
      <details className="portal-technical" open>
        <summary>버전 변경 · 개발 속성</summary>
        <p>{component.changes}</p>
        <table><tbody>{Object.entries(component.props).map(([name, value]) =>
          <tr key={name}><th>{name}</th><td>{value}</td></tr>)}</tbody></table>
        <details><summary>사용 예시 (TSX)</summary><pre className="portal-code-example">{component.usage}</pre></details>
        <details><summary>패키지 · 소스 기준</summary>
          <p><code>{binding.package}</code> · <code>{binding.exportName}</code></p>
          <p><code>platform/component-library/{component.entry}</code></p>
          <p className="portal-hash">{binding.sourceHash}</p>
        </details>
      </details>
    </>}
  </section>;
}

export default function VersionComponentDetail({ binding }: { binding: Binding }) {
  const [retry, setRetry] = useState(0);
  return <div>
    <VersionSession key={`${JSON.stringify(binding)}:${retry}`} binding={binding} />
    <button type="button" className="portal-secondary" onClick={() => { resetVersionDocument(); setRetry(n => n + 1); }}>구현 다시 불러오기</button>
  </div>;
}
