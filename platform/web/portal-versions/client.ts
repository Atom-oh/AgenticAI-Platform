import { readBytes } from '../portal-react/client';

export type Binding = {
  kind: 'react-component'; id: string; name: string; version: string;
  package: string; exportName: string; sourceHash: string;
};
type SourceFile = { path: string; sha256: string; content: string };
export type VersionComponent = {
  id: string; name: string; version: string; exportName: string; entry: string;
  description: string; changes: string; props: Record<string, string>; usage: string;
  sourceHash: string; files: SourceFile[];
};
export type VersionCatalog = { package: string; hash: string; components: VersionComponent[] };
type Manifest = { schemaVersion: 1; catalog: VersionCatalog; renderer: { file: string; sha256: string; bytes: number } };
export type VersionDocument = { html: string; catalog: VersionCatalog };
const digest = /^[a-f0-9]{64}$/;
const sourcePath = /^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.(?:tsx?|css|json)$/;
const obj = (v: any) => v && typeof v === 'object' && !Array.isArray(v);
const text = (v: any, max: number) => typeof v === 'string' && v.length > 0 && v.length <= max;
export function validateManifest(value: any): Manifest {
  if (!obj(value) || value.schemaVersion !== 1 || !obj(value.catalog) || !obj(value.renderer)) throw new Error('invalid-version-manifest');
  const c = value.catalog, r = value.renderer;
  if (c.package !== '@atom/portal-components' || !digest.test(c.hash) || !Array.isArray(c.components) ||
      !c.components.length || c.components.length > 80 || !digest.test(r.sha256) ||
      r.file !== `versions-${r.sha256}.html` || !Number.isInteger(r.bytes) || r.bytes < 1 || r.bytes > 1_000_000) {
    throw new Error('invalid-version-manifest');
  }
  const ids = new Set();
  for (const item of c.components) {
    if (!obj(item) || !/^[A-Z][A-Za-z]{0,40}$/.test(item.name) || !/^\d{1,2}\.0\.0$/.test(item.version) ||
        item.id !== `CMP-${item.name}-v${item.version.split('.')[0]}` || ids.has(item.id) ||
        item.exportName !== `${item.name}V${item.version.split('.')[0]}` || !sourcePath.test(item.entry) ||
        !digest.test(item.sourceHash) || !text(item.description, 1000) || !text(item.changes, 2000) ||
        !text(item.usage, 5000) || !obj(item.props) || Object.keys(item.props).length > 32 ||
        !Object.entries(item.props).every(([k, v]) => /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(k) && text(v, 1000)) ||
        !Array.isArray(item.files) || !item.files.length || item.files.length > 16) throw new Error('invalid-version-component');
    ids.add(item.id);
    const paths = new Set();
    for (const file of item.files) {
      if (!obj(file) || !sourcePath.test(file.path) || !digest.test(file.sha256) || !text(file.content, 100_000) || paths.has(file.path)) {
        throw new Error('invalid-version-source');
      }
      paths.add(file.path);
    }
    if (!paths.has(item.entry) || item.files.map((f: SourceFile) => f.path).join() !== [...paths].sort().join()) throw new Error('invalid-version-source-order');
  }
  return value as Manifest;
}

export function matchBinding(catalog: VersionCatalog, binding: Binding): VersionComponent {
  const component = catalog.components.find(c => c.id === binding.id);
  if (binding.kind !== 'react-component' || binding.package !== catalog.package || !component ||
      !(['name', 'version', 'exportName', 'sourceHash'] as const).every(key => component[key] === binding[key])) {
    throw new Error('component-version-mismatch');
  }
  return component;
}
async function hash(bytes: Uint8Array) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
}
const encode = (v: string) => new TextEncoder().encode(v);
export function createVersionLoader({ fetcher = fetch, origin }: { fetcher?: typeof fetch; origin?: string } = {}) {
  let pending: Promise<VersionDocument> | undefined;
  const url = (file: string) => new URL('/portal-renderers/versions/' + file, origin ?? globalThis.location.origin).href;
  const decode = (bytes: Uint8Array) => new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const document = () => pending ||= (async () => {
    const manifest = validateManifest(JSON.parse(decode(await readBytes(url('index.json'), 800_000, 'application/json', fetcher))));
    const { catalog, renderer } = manifest;
    for (const c of catalog.components) {
      for (const file of c.files) if (await hash(encode(file.content)) !== file.sha256) throw new Error('component-source-integrity');
      const identity = { id: c.id, version: c.version, package: catalog.package, exportName: c.exportName,
        files: c.files.map(({ path, sha256 }) => ({ path, sha256 })) };
      if (await hash(encode(JSON.stringify(identity))) !== c.sourceHash) throw new Error('component-identity-integrity');
    }
    if (await hash(encode(JSON.stringify({ package: catalog.package, components: catalog.components }))) !== catalog.hash) {
      throw new Error('component-catalog-integrity');
    }
    const bytes = await readBytes(url(renderer.file), renderer.bytes, 'text/html', fetcher);
    if (bytes.byteLength !== renderer.bytes || await hash(bytes) !== renderer.sha256) throw new Error('version-renderer-integrity');
    return { html: decode(bytes), catalog };
  })().catch(error => { pending = undefined; throw error; });
  return { document };
}
const loader = createVersionLoader();
export const loadVersionDocument = loader.document;
