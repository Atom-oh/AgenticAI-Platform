export const CHANNEL = 'portal-diagram/v1';
export type RenderSession = { nonce: string; id: string };
export type ViewCommand = 'zoom-in' | 'zoom-out' | 'fit';

export function isSession(value: unknown): value is RenderSession {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  return typeof data.nonce === 'string' && /^[a-f0-9]{64}$/.test(data.nonce) &&
    typeof data.id === 'string' && /^[a-f0-9]{32}$/.test(data.id);
}

export function newRenderSession(): RenderSession {
  const randomHex = (length: number) => Array.from(crypto.getRandomValues(new Uint8Array(length)),
    byte => byte.toString(16).padStart(2, '0')).join('');
  return { nonce: randomHex(32), id: randomHex(16) };
}

/** An opaque sandbox has origin "null"; origin alone never authenticates it. */
export function rendererResponse(
  event: Pick<MessageEvent, 'source' | 'origin' | 'data'>,
  source: MessageEventSource | null,
  session: RenderSession,
): 'rendered' | 'error' | 'escape' | null {
  if (!source || event.source !== source || event.origin !== 'null') return null;
  const data = event.data;
  if (!isSession(data) || data.nonce !== session.nonce || data.id !== session.id) return null;
  const message = data as RenderSession & { channel?: unknown; type?: unknown };
  return message.channel === CHANNEL && (message.type === 'rendered' || message.type === 'error' || message.type === 'escape') ? message.type : null;
}
