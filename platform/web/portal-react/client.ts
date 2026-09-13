import type { ReactCatalog } from '../src/portal/reactCatalog';
import { COMPONENT_NAMES, isComponentName } from './examples/names';

const ROOT = '/portal-renderers/react/';
export const LIMITS = Object.freeze({ manifest: 64_000, html: 1_000_000, timeout: 15_000 });
type Renderer = { file: string; sha256: string; bytes: number };
export type Manifest = { schemaVersion: 1; catalog: ReactCatalog; renderer: Renderer };
export type ReactDocument = { html: string; catalog: ReactCatalog };
const digestPattern = /^[a-f0-9]{64}$/;
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown, size: number): v is string => typeof v === 'string' && v.length > 0 && v.length <= size;

export function validateManifest(input: unknown): Manifest {
  if (!record(input) || input.schemaVersion !== 1 || !record(input.catalog) || !record(input.renderer)) {
    throw new Error('invalid-react-manifest');
  }
  const c = input.catalog, r = input.renderer;
  if (c.id !== 'studio-ui' || !text(c.version, 32) || !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(c.version) ||
      !text(c.label, 200) || !text(c.hash, 64) || !digestPattern.test(c.hash) ||
      !Array.isArray(c.components) || c.components.length !== COMPONENT_NAMES.length ||
      !text(r.sha256, 64) || !digestPattern.test(r.sha256) || r.file !== `react-${r.sha256}.html` ||
      typeof r.bytes !== 'number' || !Number.isInteger(r.bytes) || r.bytes < 1 || r.bytes > LIMITS.html) {
    throw new Error('invalid-react-manifest');
  }
  const seen = new Set<string>();
  const components = c.components.map(item => {
    if (!record(item) || !isComponentName(item.name) || seen.has(item.name) || !text(item.description, 1000) ||
        !record(item.props) || Object.keys(item.props).length > 32 ||
        !Object.entries(item.props).every(([k, v]) => /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(k) && text(v, 1000)) ||
        !Array.isArray(item.variationAxes) || item.variationAxes.length > 16 ||
        !item.variationAxes.every(axis => text(axis, 64))) throw new Error('invalid-react-catalog');
    seen.add(item.name);
    return Object.freeze({
      name: item.name, description: item.description,
      props: Object.freeze(Object.fromEntries(Object.entries(item.props))) as Record<string, string>,
      variationAxes: Object.freeze([...item.variationAxes]) as unknown as string[],
    });
  });
  const catalog: ReactCatalog = Object.freeze({
    id: c.id, version: c.version, label: c.label, hash: c.hash,
    components: Object.freeze(components) as unknown as ReactCatalog['components'],
  });
  return { schemaVersion: 1, catalog, renderer: { file: r.file as string, sha256: r.sha256, bytes: r.bytes } };
}

async function readBytes(url: string, maximum: number, mime: string, fetcher: typeof fetch): Promise<Uint8Array> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), LIMITS.timeout);
  try {
    const response = await fetcher(url, { signal: controller.signal, credentials: 'omit', redirect: 'error', cache: 'no-store' });
    controller.signal.throwIfAborted();
    if (!response.ok || response.redirected || !response.body || (response.url && response.url !== url) ||
        response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== mime) {
      throw new Error('react-renderer-unavailable');
    }
    const reader = response.body.getReader(), chunks: Uint8Array[] = [];
    let size = 0;
    const cancel = () => { void reader.cancel().catch(() => {}); };
    controller.signal.addEventListener('abort', cancel, { once: true });
    try {
      const declared = response.headers.get('content-length');
      if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum)) throw new Error('react-renderer-too-large');
      for (;;) {
        controller.signal.throwIfAborted();
        const { done, value } = await reader.read();
        controller.signal.throwIfAborted();
        if (done) break;
        size += value.byteLength;
        if (size > maximum) throw new Error('react-renderer-too-large');
        chunks.push(value);
      }
    } catch (error) {
      await reader.cancel().catch(() => {});
      throw error;
    } finally {
      controller.signal.removeEventListener('abort', cancel);
      reader.releaseLock();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  } finally { clearTimeout(timer); }
}

export function createReactLoader({ fetcher = fetch, origin }: { fetcher?: typeof fetch; origin?: string } = {}) {
  let manifestPromise: Promise<Manifest> | undefined;
  let documentPromise: Promise<ReactDocument> | undefined;
  const localURL = (file: string) => new URL(ROOT + file, origin ?? globalThis.location.origin).href;
  const decode = (bytes: Uint8Array) => new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const manifest = () => manifestPromise ||= readBytes(localURL('index.json'), LIMITS.manifest, 'application/json', fetcher)
    .then(bytes => validateManifest(JSON.parse(decode(bytes))))
    .catch(error => { manifestPromise = undefined; throw error; });
  return {
    catalog: () => manifest().then(value => value.catalog),
    document: () => documentPromise ||= (async () => {
      const { catalog, renderer } = await manifest();
      const bytes = await readBytes(localURL(renderer.file), renderer.bytes, 'text/html', fetcher);
      if (bytes.byteLength !== renderer.bytes) throw new Error('react-renderer-size-mismatch');
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
      const sha = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
      if (sha !== renderer.sha256) throw new Error('react-renderer-integrity-mismatch');
      return Object.freeze({ html: decode(bytes), catalog });
    })().catch(error => { documentPromise = undefined; manifestPromise = undefined; throw error; }),
  };
}
