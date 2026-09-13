import { useEffect, useRef, useState } from 'react';
import { loadReactDocument } from './reactCatalog';
import { isComponentName, type ComponentName } from '../../portal-react/examples/names';
import { CHANNEL, newSession, responseType, type Session } from '../../portal-react/protocol';
import type { ReactDocument } from '../../portal-react/client';

function PreviewSession({ name, compact }: { name: ComponentName; compact: boolean }) {
  const container = useRef<HTMLDivElement | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const session = useRef<Session | null>(null);
  const documentRef = useRef<ReactDocument | null>(null);
  const initialized = useRef(false);
  const failRef = useRef(() => {});
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const [visible, setVisible] = useState(!compact);
  const [document, setDocument] = useState<ReactDocument | null>(null);
  const [state, setState] = useState<'loading' | 'rendered' | 'error'>('loading');
  const [height, setHeight] = useState(400);

  useEffect(() => {
    if (visible) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '200px' });
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    let active = true, failed = false;
    const fail = () => {
      if (!active || failed) return;
      failed = true;
      clearTimeout(timer.current);
      documentRef.current = null;
      setDocument(null);
      setState('error');
    };
    failRef.current = fail;
    initialized.current = false;
    const receive = (event: MessageEvent) => {
      if (!active || failed || !session.current || !documentRef.current) return;
      const result = responseType(event, frame.current?.contentWindow ?? null, session.current, documentRef.current.catalog.hash);
      if (result === 'error') fail();
      if (result === 'rendered') { clearTimeout(timer.current); setState('rendered'); }
      if (result === 'resize' && !compact && typeof event.data.height === 'number' &&
          Number.isFinite(event.data.height) && event.data.height >= 80 && event.data.height <= 2000) {
        setHeight(Math.max(240, Math.min(1200, Math.ceil(event.data.height))));
      }
    };
    window.addEventListener('message', receive);
    try {
      session.current = newSession();
      void loadReactDocument().then(value => {
        if (!active || failed) return;
        documentRef.current = value;
        setDocument(value);
      }).catch(fail);
    } catch { fail(); }
    return () => {
      active = false;
      clearTimeout(timer.current);
      documentRef.current = null;
      session.current = null;
      window.removeEventListener('message', receive);
    };
  }, [visible, compact]);

  const render = () => {
    if (initialized.current || !session.current || !documentRef.current || !frame.current?.contentWindow) return;
    initialized.current = true;
    timer.current = setTimeout(() => failRef.current(), 10_000);
    try {
      // The recipient has an opaque origin. Authentication is the exact Window
      // plus the independently generated frame nonce and request ID in its reply.
      frame.current.contentWindow.postMessage({
        channel: CHANNEL, type: 'render', ...session.current, name, compact,
        catalogHash: documentRef.current.catalog.hash,
      }, '*');
    } catch { failRef.current(); }
  };

  return <div ref={node => { container.current = node; node?.toggleAttribute('inert', compact); }}
    data-portal-react-container="" data-react-preview-state={state}
    className={`portal-react-preview${compact ? ' portal-react-preview--compact' : ''}`}
    aria-hidden={compact || undefined} style={{ minWidth: 0, width: '100%' }}>
    {state === 'error'
      ? <p role="alert">컴포넌트 미리보기를 불러오지 못했습니다. 다시 불러오세요.</p>
      : !compact && <p role="status" aria-live="polite">{state === 'rendered'
        ? '실제 React 컴포넌트를 표시했습니다.' : 'React 컴포넌트를 불러오고 있습니다…'}</p>}
    <div style={{ height: compact ? 224 : height, minWidth: 0, width: '100%', overflow: 'hidden' }} aria-busy={state === 'loading'}>
      {document && state !== 'error' && <iframe ref={frame} data-portal-react="" title={`${name} 컴포넌트 예시`}
        sandbox="allow-scripts" referrerPolicy="no-referrer" loading={compact ? 'lazy' : 'eager'}
        tabIndex={compact ? -1 : undefined} srcDoc={document.html} onLoad={render}
        style={{ width: '100%', height: '100%', display: 'block', border: 0, visibility: state === 'rendered' ? 'visible' : 'hidden' }} />}
    </div>
  </div>;
}

export function ReactComponentPreview({ name, compact = false }: { name: string; compact?: boolean }) {
  const [retry, setRetry] = useState(0);
  if (!isComponentName(name)) return <p role="alert">현재 패키지에 없는 컴포넌트입니다. 목록에서 선택해 주세요.</p>;
  return <div className="portal-react-preview-root" style={{ minWidth: 0 }}>
    <PreviewSession key={`${name}:${compact}:${retry}`} name={name} compact={compact} />
    {!compact && <button type="button" onClick={() => setRetry(n => n + 1)}>다시 불러오기</button>}
  </div>;
}

export default ReactComponentPreview;
