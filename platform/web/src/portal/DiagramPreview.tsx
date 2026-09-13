import { useEffect, useId, useRef, useState } from 'react';
import { diagramToMermaid, validateDiagram, type PortalDiagram } from './diagram';
import { loadRendererDocument } from '../../portal-renderer/client';
import { CHANNEL, newRenderSession, rendererResponse, type RenderSession, type ViewCommand } from '../../portal-renderer/protocol';

export type { PortalDiagram } from './diagram';

type Prepared = { visual: PortalDiagram; dsl: string };

function DiagramSession({ prepared, title, large = false }: { prepared: Prepared; title?: string; large?: boolean }) {
  const { visual, dsl } = prepared;
  const frame = useRef<HTMLIFrameElement>(null);
  const session = useRef<RenderSession | null>(null);
  const initialized = useRef(false);
  const [html, setHtml] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'rendered' | 'error'>('loading');
  const [expanded, setExpanded] = useState(false);
  const viewportId = useId();
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const controller = new AbortController();
    let finished = false;
    const fail = () => {
      if (controller.signal.aborted || finished) return;
      finished = true;
      setHtml(null); setState('error'); controller.abort();
    };
    const timeout = setTimeout(fail, 25_000);
    const receive = (event: MessageEvent) => {
      if (!session.current || controller.signal.aborted || finished) return;
      const result = rendererResponse(event, frame.current?.contentWindow ?? null, session.current);
      if (result === 'error') { fail(); clearTimeout(timeout); }
      if (result === 'rendered') { finished = true; setState('rendered'); clearTimeout(timeout); }
    };
    window.addEventListener('message', receive);
    initialized.current = false;
    try {
      session.current = newRenderSession();
      void loadRendererDocument(controller.signal).then(document => {
        if (!controller.signal.aborted) setHtml(document);
      }).catch(fail);
    } catch { fail(); }
    return () => { controller.abort(); clearTimeout(timeout); window.removeEventListener('message', receive); };
  }, []);

  const render = () => {
    if (!frame.current?.contentWindow || !session.current || initialized.current) return;
    initialized.current = true;
    // '*' is necessary for the opaque recipient; replies are source/nonce/id bound.
    frame.current.contentWindow.postMessage({ channel: CHANNEL, type: 'render', ...session.current, visual }, '*');
  };
  const view = (command: ViewCommand) => {
    if (state === 'rendered' && session.current) {
      frame.current?.contentWindow?.postMessage({ channel: CHANNEL, type: 'view', ...session.current, command }, '*');
    }
  };
  const name = title || visual.title || '관계 다이어그램';
  return <section className="portal-diagram" data-diagram-state={state} aria-labelledby={titleId} aria-describedby={descriptionId}
    style={{ minWidth: 0, maxWidth: '100%' }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
      <h3 id={titleId} style={{ margin: '8px 0', overflowWrap: 'anywhere' }}>{name}</h3>
      <div role="group" aria-label="다이어그램 보기" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" onClick={() => view('zoom-out')} disabled={state !== 'rendered'} aria-label="다이어그램 축소">−</button>
        <button type="button" onClick={() => view('zoom-in')} disabled={state !== 'rendered'} aria-label="다이어그램 확대">+</button>
        <button type="button" onClick={() => view('fit')} disabled={state !== 'rendered'}>화면에 맞춤</button>
        {!large && <button type="button" aria-expanded={expanded} aria-controls={viewportId}
          onClick={() => setExpanded(value => !value)}>{expanded ? '접기' : '크게 보기'}</button>}
      </div>
    </div>
    <p id={descriptionId}>관계 표시용 다이어그램입니다. 화면 실행·UX 검증 결과가 아닙니다.</p>
    {state === 'error'
      ? <p role="alert">다이어그램을 불러오거나 표시하지 못했습니다. 원본 데이터와 로컬 렌더러를 확인해 주세요.</p>
      : <p role="status" aria-live="polite">{state === 'rendered' ? '다이어그램을 표시했습니다.' : '다이어그램을 불러오고 있습니다…'}</p>}
    <div id={viewportId} data-portal-diagram-viewport="" aria-busy={state === 'loading'} style={{
      height: large || expanded ? 'min(65dvh, 760px)' : 360, minHeight: 240,
      border: '1px solid #d6dce6', borderRadius: 10, overflow: 'hidden', background: '#fff',
    }}>
      {html && state !== 'error' ? <iframe data-portal-diagram="" ref={frame} title={name}
        sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={html} onLoad={render}
        style={{ display: 'block', width: '100%', height: '100%', minHeight: 0, maxHeight: '100%', border: 0,
          visibility: state === 'rendered' ? 'visible' : 'hidden' }} /> : null}
    </div>
    {visual.note && <p style={{ overflowWrap: 'anywhere' }}>{visual.note}</p>}
    {visual.truncated.nodes > 0 || visual.truncated.edges > 0
      ? <p>노드 {visual.truncated.nodes}개 · 연결 {visual.truncated.edges}개 생략</p> : null}
    <details style={{ marginTop: 12 }}>
      <summary>출처와 다이어그램 소스</summary>
      <p style={{ overflowWrap: 'anywhere' }}>{visual.source || '출처 미지정'}</p>
      <p>실선은 순서, 점선은 참조 관계이며 테두리가 점선인 노드는 누락된 참조입니다.</p>
      <pre style={{ maxHeight: 260, maxWidth: '100%', overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{dsl}</pre>
    </details>
  </section>;
}

export function DiagramPreview({ visual, title, large = false }: { visual: PortalDiagram; title?: string; large?: boolean }) {
  const [retry, setRetry] = useState(0);
  let prepared: Prepared;
  try {
    const checked = validateDiagram(visual);
    prepared = { visual: checked, dsl: diagramToMermaid(checked) };
  } catch {
    return <p className="portal-diagram-error" role="alert">다이어그램 데이터가 올바르지 않거나 표시 한도를 초과했습니다.</p>;
  }
  // Key all graph/source revisions, not just the title or source asset ID. React
  // removes the old browsing context in the selection commit, before effects/fetch.
  const selection = JSON.stringify([prepared.visual, title, retry]);
  return <div style={{ minWidth: 0, maxWidth: '100%' }}>
    <DiagramSession key={selection} prepared={prepared} title={title} large={large} />
    <button type="button" onClick={() => setRetry(value => value + 1)} style={{ marginTop: 12 }}>다시 불러오기</button>
  </div>;
}

export default DiagramPreview;
