export const CHANNEL = 'portal-react/v1';
export type Session = { nonce: string; id: string };

export function isSession(value: unknown): value is Session {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return typeof v.nonce === 'string' && /^[a-f0-9]{64}$/.test(v.nonce) &&
    typeof v.id === 'string' && /^[a-f0-9]{32}$/.test(v.id);
}

export function newSession(): Session {
  const hex = (length: number) => Array.from(crypto.getRandomValues(new Uint8Array(length)),
    byte => byte.toString(16).padStart(2, '0')).join('');
  return { nonce: hex(32), id: hex(16) };
}

export function responseType(
  event: Pick<MessageEvent, 'source' | 'origin' | 'data'>,
  frame: MessageEventSource | null,
  session: Session,
  hash: string,
): 'rendered' | 'error' | 'resize' | null {
  if (!frame || event.source !== frame || event.origin !== 'null' || !isSession(event.data)) return null;
  const data = event.data as Session & { channel?: unknown; type?: unknown; catalogHash?: unknown };
  if (data.nonce !== session.nonce || data.id !== session.id || data.catalogHash !== hash || data.channel !== CHANNEL) return null;
  return data.type === 'rendered' || data.type === 'error' || data.type === 'resize' ? data.type : null;
}
