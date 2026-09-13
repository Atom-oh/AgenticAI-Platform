import { Component, useEffect, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Screen, Stack, Button, Text } from '../../react-kit/ui';
import { Example } from './examples';
import { isComponentName, type ComponentName } from './examples/names';
import { CHANNEL, isSession, type Session } from './protocol';

declare const __PORTAL_REACT_CATALOG_HASH__: string;
const catalogHash = __PORTAL_REACT_CATALOG_HASH__;
const host = document.getElementById('root')!;
const root = createRoot(host);
let active: Session | null = null;
let parentOrigin: string | null = null;

function reply(type: 'rendered' | 'error' | 'resize', height?: number) {
  if (active && parentOrigin) window.parent.postMessage({ channel: CHANNEL, ...active, catalogHash, type, height }, parentOrigin);
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { reply('error'); }
  render() { return this.state.failed ? <p role="alert">컴포넌트 예시를 표시하지 못했습니다.</p> : this.props.children; }
}

function App({ name, compact }: { name: ComponentName; compact: boolean }) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    reply('rendered');
    const resize = () => reply('resize', Math.ceil(host.getBoundingClientRect().height));
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();
    return () => observer.disconnect();
  }, []);
  const content = <Stack gap={compact ? 2 : 6}>
    {!compact && <Text size="sm" tone="muted">플랫폼 기본 React 컴포넌트 · 로컬 동작 예시</Text>}
    <Example key={revision} name={name} compact={compact} />
    {!compact && <div className="preview-reset"><Button label="예시 초기화" kind="secondary" onClick={() => setRevision(n => n + 1)} /></div>}
  </Stack>;
  return <div className="preview-shell" data-preview-mode={compact ? 'compact' : 'full'}>
    {name === 'Screen' ? content : <Screen pageId={`preview-${name}`} width={compact ? 'mobile' : 'content'}>{content}</Screen>}
  </div>;
}

window.addEventListener('message', event => {
  // Accept a single initialization from the immediate parent, then bind this frame
  // permanently to that request. Metadata never selects imports or supplies code.
  if (event.source !== window.parent || active || !isSession(event.data)) return;
  const data = event.data as Session & {
    channel?: unknown; type?: unknown; name?: unknown; compact?: unknown; catalogHash?: unknown;
  };
  if (data.channel !== CHANNEL || data.type !== 'render') return;
  active = { nonce: data.nonce, id: data.id };
  parentOrigin = event.origin === 'null' ? '*' : event.origin;
  if (!isComponentName(data.name) || typeof data.compact !== 'boolean' || data.catalogHash !== catalogHash ||
      Object.keys(data).some(key => !['channel', 'type', 'nonce', 'id', 'name', 'compact', 'catalogHash'].includes(key))) {
    host.textContent = '지원하지 않는 컴포넌트이거나 미리보기 요청이 올바르지 않습니다.';
    host.setAttribute('role', 'alert');
    reply('error');
    return;
  }
  root.render(<Boundary><App name={data.name} compact={data.compact} /></Boundary>);
});

window.addEventListener('error', () => reply('error'));
window.addEventListener('unhandledrejection', () => reply('error'));
