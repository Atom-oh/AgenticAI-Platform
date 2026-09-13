const ROOT = '/portal-renderers/mermaid/';
export const RENDERER_LIMITS = Object.freeze({ manifest: 4096, html: 8_000_000, timeout: 15_000 });
export type RendererManifest = {
  schemaVersion: 1;
  rendererVersion: 1;
  mermaidVersion: string;
  file: string;
  sha256: string;
  bytes: number;
};

export function validateRendererManifest(input: unknown): RendererManifest {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('invalid-renderer-manifest');
  const value = input as Record<string, unknown>;
  if (value.schemaVersion !== 1 || value.rendererVersion !== 1 ||
      value.mermaidVersion !== '11.17.2' ||
      typeof value.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.sha256) ||
      value.file !== `mermaid-${value.mermaidVersion}-${value.sha256}.html` ||
      typeof value.bytes !== 'number' || !Number.isInteger(value.bytes) || value.bytes < 1 ||
      value.bytes > RENDERER_LIMITS.html) throw new Error('invalid-renderer-manifest');
  return { schemaVersion: 1, rendererVersion: 1, mermaidVersion: value.mermaidVersion,
    file: value.file as string, sha256: value.sha256, bytes: value.bytes };
}

export async function readRendererBytes(
  path: string, maximum: number, signal: AbortSignal, fetcher: typeof fetch = fetch,
): Promise<Uint8Array> {
  signal.throwIfAborted();
  const response = await fetcher(path, { signal, credentials: 'omit', redirect: 'error', cache: 'no-store' });
  signal.throwIfAborted();
  if (!response.ok || !response.body || response.redirected) throw new Error('renderer-unavailable');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', cancel, { once: true });
  try {
    const declared = response.headers.get('content-length');
    if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum)) throw new Error('renderer-too-large');
    for (;;) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) throw new Error('renderer-too-large');
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    signal.removeEventListener('abort', cancel);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}

/** Verify static local bytes before they become an executable srcDoc. */
export async function loadRendererDocument(signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<string> {
  signal.throwIfAborted();
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, RENDERER_LIMITS.timeout);
  try {
    const decode = (bytes: Uint8Array) => new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    const manifestBytes = await readRendererBytes(ROOT + 'index.json', RENDERER_LIMITS.manifest, controller.signal, fetcher);
    const manifest = validateRendererManifest(JSON.parse(decode(manifestBytes)));
    const bytes = await readRendererBytes(ROOT + manifest.file, manifest.bytes, controller.signal, fetcher);
    if (bytes.byteLength !== manifest.bytes) throw new Error('renderer-size-mismatch');
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    const hash = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
    controller.signal.throwIfAborted();
    if (hash !== manifest.sha256) throw new Error('renderer-integrity-mismatch');
    return decode(bytes);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', cancel);
  }
}
