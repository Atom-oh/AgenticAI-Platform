import { auth } from '../lib';
import type { Asset, Job, Purpose } from './types';

export class WorkspaceError extends Error {
  constructor(message: string, public status = 0, public code = '') { super(message); this.name = 'WorkspaceError'; }
}
export const messageOf = (reason: unknown) => reason instanceof Error ? reason.message : '요청을 처리하지 못했습니다. 다시 시도하세요.';
export const aborted = (reason: unknown) => reason instanceof Error && reason.name === 'AbortError';
export const resource = (id: string) => encodeURIComponent(id);
const API_ERROR_MESSAGES: Record<string, string> = {
  unauthorized: '로그인이 필요하거나 만료되었습니다. 다시 로그인하세요.',
  conflict: '다른 변경으로 저장된 버전이 달라졌습니다. 최신 버전을 다시 불러온 뒤 시도하세요.',
  'too-large': '파일이나 내용이 허용된 크기를 초과했습니다. 파일을 나누거나 크기를 줄여 주세요.',
  'unsupported-extension': '지원하지 않는 파일 형식입니다. 파일 선택 영역에 안내된 형식으로 준비하세요.',
  'parts-missing': '파일의 일부 전송이 완료되지 않았습니다. 파일 목록에서 전송 상태를 확인하고 다시 시도하세요.',
  'asset-not-ready': '선택한 파일의 보관이 끝나지 않았거나 사용 목록에서 제외된 파일입니다. 내 파일에서 상태를 확인하고 보관 완료된 파일을 다시 선택하세요.',
  'contract-unresolved': '규칙에 확인이 필요한 내용이 남아 있습니다. 해당 내용을 해결하고 규칙을 저장한 뒤 다시 승인하세요.',
  'contract-not-approved': '현재 규칙 버전이 승인되지 않았습니다. 규칙을 저장·승인한 뒤 해당 버전을 선택하세요.',
  'approval-evidence-required': '선택한 라운드의 검수 통과와 근거를 확인할 수 없습니다. 동작 검증·실행 화면·검수 기록을 확인하고 재검수한 뒤 승인하세요.',
  unavailable: '작업실을 일시적으로 사용할 수 없습니다. 작업 진행 상태를 조회한 뒤 다시 시도하세요.',
};
function responseMessage(status: number, data: { error?: string; code?: string }) {
  if (typeof data.error === 'string' && /[가-힣]/.test(data.error)) return data.error;
  if (typeof data.code === 'string' && Object.hasOwn(API_ERROR_MESSAGES, data.code)) return API_ERROR_MESSAGES[data.code];
  if (status === 401) return '로그인이 만료되었습니다. 다시 로그인하세요.';
  if (status === 409) return '저장된 상태나 버전이 변경되었거나 필요한 검증이 끝나지 않았습니다. 새로 조회한 뒤 다시 확인하세요.';
  if (status === 404) return '이 파일이나 작업을 찾을 수 없습니다. 내 작업 목록을 새로 조회하세요.';
  if (status === 413) return '파일이나 내용이 허용된 크기를 초과했습니다.';
  if (status >= 500) return '작업실에서 요청을 처리하지 못했습니다. 진행 상태를 조회한 뒤 다시 시도하세요.';
  return '입력 내용과 파일 형식을 확인한 뒤 다시 시도하세요.';
}

export function createWorkspaceClient({ token = () => auth.token, fetcher = fetch }: {
  token?: () => string | null; fetcher?: typeof fetch;
} = {}) {
  async function request(path: string, method: string, body?: unknown, signal?: AbortSignal, binary = false) {
    if (!/^\/[a-z]/.test(path) || path.includes('://') || path.includes('\\') || path.includes('..')) {
      throw new WorkspaceError('허용되지 않은 요청 경로입니다.');
    }
    signal?.throwIfAborted();
    const accessToken = token();
    if (!accessToken) throw new WorkspaceError('로그인이 필요합니다. 다시 로그인하세요.', 401);
    const controller = new AbortController();
    const cancel = () => controller.abort();
    signal?.addEventListener('abort', cancel, { once: true });
    const timeout = setTimeout(cancel, 120_000);
    const headers = new Headers({ Authorization: `Bearer ${accessToken}` });
    if (body !== undefined) headers.set('Content-Type', binary ? 'application/octet-stream' : 'application/json');
    try {
      const response = await fetcher('/studio-api' + path, {
        method, headers, signal: controller.signal, redirect: 'error', cache: 'no-store',
        body: body === undefined ? undefined : binary ? body as Blob : JSON.stringify(body),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new WorkspaceError(responseMessage(response.status, data), response.status, data.code || '');
      }
      // Consume the response while the timeout/cancellation still protects it.
      if (method === 'GET' && path.includes('/blob?')) {
        return { response, bytes: await response.arrayBuffer() };
      }
      return response.status === 204 ? {} : await response.json();
    } catch (reason) {
      if (controller.signal.aborted && !signal?.aborted) throw new WorkspaceError('응답 시간이 초과되었습니다. 다시 조회하세요.');
      throw reason;
    } finally {
      clearTimeout(timeout); signal?.removeEventListener('abort', cancel);
    }
  }
  return {
    get: <T = unknown>(path: string, signal?: AbortSignal): Promise<T> => request(path, 'GET', undefined, signal),
    post: <T = unknown>(path: string, body: unknown, signal?: AbortSignal): Promise<T> => request(path, 'POST', body, signal),
    put: <T = unknown>(path: string, body: unknown, signal?: AbortSignal): Promise<T> => request(path, 'PUT', body, signal),
    part: (path: string, bytes: Blob, signal?: AbortSignal) => request(path, 'PUT', bytes, signal, true),
    remove: (path: string, signal?: AbortSignal) => request(path, 'DELETE', undefined, signal),
  };
}
export type WorkspaceClient = ReturnType<typeof createWorkspaceClient>;
export const workspaceClient = createWorkspaceClient();

export async function listAll<T>(client: WorkspaceClient, kind: 'assets' | 'contracts' | 'runs', signal?: AbortSignal): Promise<T[]> {
  const result: T[] = [];
  const seen = new Set<string>();
  let cursor = '';
  do {
    const page = await client.get<Record<string, unknown>>(`/${kind}${cursor ? '?cursor=' + encodeURIComponent(cursor) : ''}`, signal);
    if (!Array.isArray(page[kind])) throw new WorkspaceError('작업 목록을 확인하지 못했습니다.');
    result.push(...page[kind] as T[]);
    cursor = typeof page.cursor === 'string' ? page.cursor : '';
    if (cursor && seen.has(cursor)) throw new WorkspaceError('목록의 다음 페이지를 확인하지 못했습니다.');
    seen.add(cursor);
    if (seen.size > 100) throw new WorkspaceError('조회할 작업이 너무 많습니다. 작업실 담당자에게 문의하세요.');
  } while (cursor);
  return result;
}

export async function sha256(bytes: ArrayBuffer): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
}
export type UploadCheckpoint = { assetId: string; hash: string; chunkBytes: number; nextPart: number };
export async function uploadFile(client: WorkspaceClient, file: File, options: {
  purpose: Purpose; maxFileBytes: number; extensions: string[]; parentId?: string;
  signal?: AbortSignal; checkpoint?: UploadCheckpoint;
  onCheckpoint?: (value: UploadCheckpoint) => void; onProgress?: (percent: number) => void;
}) {
  options.signal?.throwIfAborted();
  const extension = file.name.split('.').pop()?.toLowerCase();
  if (!file.name.includes('.') || !options.extensions.map(e => e.replace(/^\./, '').toLowerCase()).includes(extension || '')) {
    throw new WorkspaceError('지원하는 파일 형식을 선택하세요.');
  }
  if (!file.size || file.size > options.maxFileBytes) throw new WorkspaceError('파일이 비어 있거나 허용된 크기를 초과했습니다.');
  if (Array.from(file.name).length > 180) throw new WorkspaceError('파일 이름을 180자 이내로 줄여 주세요.');
  const hash = await sha256(await file.arrayBuffer());
  options.signal?.throwIfAborted();
  let checkpoint = options.checkpoint;
  if (checkpoint && checkpoint.hash !== hash) throw new WorkspaceError('원본 파일이 변경되었습니다. 새 파일로 추가하세요.');
  if (!checkpoint) {
    const created = await client.post<{ asset: Asset; chunkBytes: number }>('/assets',
      { name: file.name, size: file.size, sha256: hash, purpose: options.purpose, ...(options.parentId ? { parentId: options.parentId } : {}) }, options.signal);
    if (!created.asset?.id || !Number.isInteger(created.chunkBytes) || created.chunkBytes <= 0 || created.chunkBytes > 2097152) {
      throw new WorkspaceError('파일 전송 정보를 확인하지 못했습니다.');
    }
    checkpoint = { assetId: created.asset.id, hash, chunkBytes: created.chunkBytes, nextPart: 0 };
    options.onCheckpoint?.(checkpoint);
  }
  const { chunkBytes, assetId } = checkpoint;
  for (let index = checkpoint.nextPart; index < Math.ceil(file.size / chunkBytes); index++) {
    options.signal?.throwIfAborted();
    await client.part(`/assets/${resource(assetId)}/parts/${index}`, file.slice(index * chunkBytes, (index + 1) * chunkBytes), options.signal);
    checkpoint = { ...checkpoint, nextPart: index + 1 };
    options.onCheckpoint?.(checkpoint);
    options.onProgress?.(Math.min(100, Math.round(((index + 1) * chunkBytes / file.size) * 100)));
  }
  return client.post<{ asset: Asset; job: Job }>(`/assets/${resource(assetId)}/complete`, {}, options.signal);
}

export async function readPrivateBlob(client: WorkspaceClient, path: string, signal?: AbortSignal) {
  let offset = 0, total = -1, hash = '', mime = '';
  const parts: ArrayBuffer[] = [];
  do {
    signal?.throwIfAborted();
    const { response, bytes } = await client.get<{ response: Response; bytes: ArrayBuffer }>(
      `${path}${path.includes('?') ? '&' : '?'}offset=${offset}`, signal);
    const rawTotal = response.headers.get('X-Total-Size');
    const rawSize = response.headers.get('X-Chunk-Size');
    const size = Number(rawTotal), chunkSize = Number(rawSize);
    const nextHash = response.headers.get('X-SHA256') || '';
    const nextMime = response.headers.get('X-Content-Type') || '';
    if (rawTotal === null || rawSize === null || !Number.isSafeInteger(size) || size < 0 || size > 128 * 1024 * 1024 ||
        chunkSize !== bytes.byteLength || chunkSize > 2097152 || !/^[a-f0-9]{64}$/i.test(nextHash)) {
      throw new WorkspaceError('파일 전송 정보를 확인하지 못했습니다.');
    }
    if (total >= 0 && (size !== total || nextHash !== hash || nextMime !== mime)) throw new WorkspaceError('전송 중 파일이 변경되었습니다. 다시 조회하세요.');
    total = size; hash = nextHash; mime = nextMime;
    if (offset + bytes.byteLength > total || (!bytes.byteLength && offset < total)) throw new WorkspaceError('파일의 일부를 받지 못했습니다.');
    parts.push(bytes); offset += bytes.byteLength;
  } while (offset < total);
  const blob = new Blob(parts, { type: mime });
  if ((await sha256(await blob.arrayBuffer())).toLowerCase() !== hash.toLowerCase()) throw new WorkspaceError('원본과 받은 파일이 일치하지 않습니다.');
  signal?.throwIfAborted();
  return { blob, mime, sha256: hash };
}

function wait(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal?.throwIfAborted();
    const cancel = () => { clearTimeout(timer); reject(new DOMException('취소됨', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve(); }, ms);
    signal?.addEventListener('abort', cancel, { once: true });
  });
}
export async function pollJob(client: Pick<WorkspaceClient, 'get'>, id: string, options: {
  signal?: AbortSignal; interval?: number; onUpdate?: (job: Job) => void;
} = {}): Promise<Job> {
  const started = Date.now();
  while (Date.now() - started < 30 * 60_000) {
    options.signal?.throwIfAborted();
    const { job } = await client.get<{ job: Job }>(`/jobs/${resource(id)}`, options.signal);
    options.signal?.throwIfAborted();
    if (!job || job.id !== id) throw new WorkspaceError('작업 기록이 일치하지 않습니다.');
    options.onUpdate?.(job);
    if (job.status === 'completed') return job;
    if (job.status === 'failed') throw new WorkspaceError(job.error || '작업을 완료하지 못했습니다. 입력을 확인하고 다시 시도하세요.');
    if (!['queued', 'running'].includes(job.status)) throw new WorkspaceError('작업 상태를 확인하지 못했습니다.');
    await wait(options.interval ?? 1500, options.signal);
  }
  throw new WorkspaceError('작업 상태 조회를 멈췄습니다. 다시 조회하면 진행 상태를 확인할 수 있습니다.');
}
