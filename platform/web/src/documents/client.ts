import { createWorkspaceClient, readPrivateBlob, resource, sha256, WorkspaceError } from '../workspace/client';
import type { WorkspaceClient } from '../workspace/client';
import type { DocumentRecord, Job, LibraryConfig, Revision, Evidence } from './types';

const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const HASH = /^[a-f0-9]{64}$/;
const messages: Record<string, string> = {
  'source-not-ready': '원문 보관과 본문 추출을 완료한 버전이 필요합니다. 처리 결과와 지원 범위를 확인하세요.',
  'source-not-approved': '현재 승인된 원문이 아닙니다. 검토·승인 상태를 확인하세요.',
  'source-integrity': '보관된 원문·분석 결과의 해시가 일치하지 않습니다. 원본을 확인한 뒤 다시 반입하세요.',
  'source-changed': '승인된 원문이 변경되었습니다. 최신 버전으로 다시 분석하세요.',
  'binding-changed': '원문 연결 기준이 변경되었습니다. 문서함에서 승인본을 확인하세요.',
  'binding-in-use': '이 참조에는 다른 승인 문서가 연결되어 있습니다. 기존 문서의 새 버전을 반입하거나 연결을 정리하세요.',
  'sample-readonly': '합성 예제 원본은 교체할 수 없습니다. 실제 자료는 새 문서로 등록하세요.',
  'sample-immutable': '합성 예제 원본은 교체할 수 없습니다. 실제 자료는 새 문서로 등록하세요.',
  'older-revision': '현재 승인본보다 오래된 반입 버전으로 되돌릴 수 없습니다.',
  'review-not-ready': '검토 요청 상태 또는 원문이 준비되지 않았습니다. 최신 상태를 확인하세요.',
  'archived': '보관 처리된 문서입니다. 변경하려면 새 문서로 등록하세요.',
  'paragraph-not-found': '이 버전에 해당 근거 문단이 없습니다. 다른 버전으로 자동 이동하지 않습니다.',
  'context-limit': '분석 범위를 초과했습니다. 문서와 질문의 범위를 줄여 다시 시도하세요.',
  'regulation-required': '분석할 규정을 선택하세요.',
  'regulation-missing': '선택한 규정을 현재 관계 목록에서 찾을 수 없습니다.',
  'collection-full': '문서함의 문서 수 상한에 도달했습니다.',
  'revision-limit': '이 문서의 버전 수 상한에 도달했습니다.',
  'job-unavailable': '처리 작업 기록이 만료되었거나 없습니다. 최신 상태를 조회하세요.',
  'job-changed': '처리 작업이 변경되거나 종료되었습니다. 최신 상태를 조회하세요.',
};

export function documentMessage(error: unknown): string {
  if (error instanceof WorkspaceError && Object.hasOwn(messages, error.code)) return messages[error.code];
  return error instanceof Error ? error.message : '문서 작업을 완료하지 못했습니다.';
}
const collections = new WeakMap<WorkspaceClient, string>();
export function makeLibraryClient(projectId?: string) {
  const client = createWorkspaceClient({ projectId });
  collections.set(client, projectId || 'personal');
  return client;
}
export const newRequest = () => crypto.randomUUID();

export function sourceHref(source: Pick<Evidence, 'documentId' | 'revisionId' | 'paragraphId' | 'textHash'>,
  options: { projectId?: string | null; analysisId?: string } = {}) {
  if (!ID.test(source.documentId) || !ID.test(source.revisionId) ||
      !source.revisionId.startsWith(source.documentId + '--') ||
      !/^p[0-9]{6}$/.test(source.paragraphId) || !HASH.test(source.textHash) ||
      options.projectId != null && !ID.test(options.projectId) ||
      options.analysisId != null && !ID.test(options.analysisId)) {
    throw new WorkspaceError('근거 문서의 버전 연결을 확인하지 못했습니다.');
  }
  const query = new URLSearchParams({ documentId: source.documentId, revisionId: source.revisionId,
    paragraph: source.paragraphId, textHash: source.textHash });
  if (options.projectId) query.set('projectId', options.projectId);
  if (options.analysisId) query.set('analysisId', options.analysisId);
  return '#/documents?' + query;
}

export function documentHref(documentId?: string, projectId?: string | null, ref?: string) {
  if (documentId && !ID.test(documentId) || projectId && !ID.test(projectId) || ref && !ID.test(ref)) {
    throw new WorkspaceError('문서함 경로를 확인하지 못했습니다.');
  }
  const query = new URLSearchParams();
  if (documentId) query.set('documentId', documentId);
  if (projectId) query.set('projectId', projectId);
  if (ref) query.set('ref', ref);
  return '#/documents' + (query.size ? '?' + query : '');
}

export type DocumentUpload = { documentId: string; revisionId: string; hash: string; requestFingerprint: string; nextPart: number; chunkBytes: number };
export async function uploadDocument(client: WorkspaceClient, file: File,
  input: { title: string; kind: string; graphRef?: string; versionLabel?: string; effectiveDate?: string;
    document?: DocumentRecord; requestId: string },
  config: Pick<LibraryConfig, 'maxFileBytes' | 'extensions'>,
  options: { signal?: AbortSignal; checkpoint?: DocumentUpload; onCheckpoint?: (value: DocumentUpload) => void;
    onProgress?: (value: number) => void } = {}) {
  const extension = file.name.split('.').pop()?.toLowerCase();
  if (!file.size || file.size > config.maxFileBytes || !file.name.includes('.') || !extension ||
      !config.extensions.includes(extension) || /[\\/\u0000-\u001f]/.test(file.name)) {
    throw new WorkspaceError('지원하는 형식과 크기의 원문 파일을 선택하세요.');
  }
  if (input.document?.provenance === 'synthetic_sample') throw new WorkspaceError(messages['sample-readonly'], 409, 'sample-readonly');
  options.signal?.throwIfAborted();
  const hash = await sha256(await file.arrayBuffer());
  const requestFingerprint = await sha256(new TextEncoder().encode(JSON.stringify({
    requestId: input.requestId, collection: collections.get(client) || 'custom-client',
    documentId: input.document?.id || null,
    title: input.document ? null : input.title, kind: input.document ? null : input.kind,
    graphRef: input.document ? null : input.graphRef || null, name: file.name, size: file.size, hash,
    versionLabel: input.versionLabel || '', effectiveDate: input.effectiveDate || null,
  })).buffer);
  options.signal?.throwIfAborted();
  let checkpoint = options.checkpoint;
  if (checkpoint && (checkpoint.hash !== hash || checkpoint.requestFingerprint !== requestFingerprint)) {
    throw new WorkspaceError('파일 또는 반입 조건이 변경되었습니다. 새 반입으로 시작하세요.');
  }
  if (!checkpoint) {
    const common = { requestId: input.requestId, name: file.name, size: file.size, sha256: hash,
      versionLabel: input.versionLabel || '', ...(input.effectiveDate ? { effectiveDate: input.effectiveDate } : {}) };
    const created = await client.post<{ document: DocumentRecord; revision: Revision; chunkBytes: number }>(
      input.document ? `/documents/${resource(input.document.id)}/revisions` : '/documents',
      input.document ? { ...common, version: input.document.version } :
        { ...common, title: input.title, kind: input.kind, ...(input.graphRef ? { graphRef: input.graphRef } : {}) },
      options.signal);
    if (!ID.test(created.document?.id) || !ID.test(created.revision?.id) ||
        !created.revision.id.startsWith(created.document.id + '--') ||
        !Number.isInteger(created.chunkBytes) || created.chunkBytes < 1 || created.chunkBytes > 2_097_152) {
      throw new WorkspaceError('원문 전송 정보를 확인하지 못했습니다.');
    }
    checkpoint = { documentId: created.document.id, revisionId: created.revision.id, hash, requestFingerprint,
      nextPart: 0, chunkBytes: created.chunkBytes };
    options.onCheckpoint?.(checkpoint);
  }
  const root = `/documents/${resource(checkpoint.documentId)}/revisions/${resource(checkpoint.revisionId)}`;
  for (let part = checkpoint.nextPart; part < Math.ceil(file.size / checkpoint.chunkBytes); part++) {
    options.signal?.throwIfAborted();
    await client.part(root + `/parts/${part}`, file.slice(part * checkpoint.chunkBytes, (part + 1) * checkpoint.chunkBytes), options.signal);
    checkpoint = { ...checkpoint, nextPart: part + 1 };
    options.onCheckpoint?.(checkpoint);
    options.onProgress?.(Math.min(100, Math.round((part + 1) * checkpoint.chunkBytes / file.size * 100)));
  }
  return client.post<{ document: DocumentRecord; revision: Revision; job: Job }>(root + '/complete', {}, options.signal);
}

export async function downloadOriginal(client: WorkspaceClient, documentId: string, revision: Revision, signal?: AbortSignal) {
  if (!ID.test(documentId) || !ID.test(revision.id) || !revision.id.startsWith(documentId + '--') || !HASH.test(revision.sha256)) {
    throw new WorkspaceError('원본의 문서·버전 정보를 확인하지 못했습니다.');
  }
  const result = await readPrivateBlob(client, `/documents/${resource(documentId)}/revisions/${resource(revision.id)}/blob?kind=original`, signal);
  signal?.throwIfAborted();
  if (result.sha256 !== revision.sha256 || result.blob.size !== revision.size) {
    throw new WorkspaceError('내려받은 원본이 선택한 문서 버전과 일치하지 않습니다.');
  }
  return new Blob([result.blob], { type: 'application/octet-stream' });
}
