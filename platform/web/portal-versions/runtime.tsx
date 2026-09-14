import { Component, useEffect, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { DEMOS } from '../../component-library/demos';
import '../../component-library/ui/styles.css';
import { CHANNEL, isSession, type Session } from './protocol';

declare const __VERSION_CATALOG_HASH__: string;
declare const __VERSION_IDENTITIES__: { id: string; version: string; sourceHash: string }[];
const catalogHash = __VERSION_CATALOG_HASH__;
const host = document.getElementById('root')!;
let session: Session | null = null;
let parentOrigin = '*';
let identity: { id: string; version: string; sourceHash: string } | undefined;
function reply(type: string, height?: number) {
  if (session && identity) window.parent.postMessage({
    channel: CHANNEL, ...session, catalogHash, assetId: identity.id, sourceHash: identity.sourceHash, type, height,
  }, parentOrigin);
}
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { reply('error'); }
  render() { return this.state.failed ? <p role="alert">예시를 표시하지 못했습니다.</p> : this.props.children; }
}
function App({ assetId }: { assetId: string }) {
  const Demo = DEMOS[assetId];
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    reply('rendered');
    const observer = new ResizeObserver(() => reply('resize', Math.ceil(host.getBoundingClientRect().height)));
    observer.observe(host);
    return () => observer.disconnect();
  }, []);
  return <div className="version-example" data-component-id={assetId} data-component-version={identity?.version}>
    <Demo key={revision} />
    <button type="button" style={{ marginTop: 24 }} onClick={() => setRevision(n => n + 1)}>예시 초기화</button>
  </div>;
}
window.addEventListener('message', event => {
  if (event.source !== window.parent || session || !isSession(event.data)) return;
  const d = event.data as Session & Record<string, unknown>;
  if (d.channel !== CHANNEL || d.type !== 'render') return;
  session = { nonce: d.nonce, id: d.id };
  parentOrigin = event.origin === 'null' ? '*' : event.origin;
  identity = __VERSION_IDENTITIES__.find(c => c.id === d.assetId);
  if (!identity || !Object.hasOwn(DEMOS, identity.id) || d.catalogHash !== catalogHash || d.sourceHash !== identity.sourceHash ||
      d.version !== identity.version ||
      Object.keys(d).some(key => !['channel', 'type', 'nonce', 'id', 'assetId', 'version', 'catalogHash', 'sourceHash'].includes(key))) {
    host.textContent = '요청한 자산과 구현 버전이 일치하지 않습니다.';
    reply('error');
    return;
  }
  createRoot(host).render(<Boundary><App assetId={identity.id} /></Boundary>);
});
window.addEventListener('error', () => reply('error'));
window.addEventListener('unhandledrejection', () => reply('error'));
