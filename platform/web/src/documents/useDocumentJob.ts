import { resource, WorkspaceError } from '../workspace/client';
import type { WorkspaceClient } from '../workspace/client';
import type { Job } from './types';

export async function watchDocumentJob(client: WorkspaceClient, id: string, signal: AbortSignal, onUpdate: (job: Job) => void) {
  const started = Date.now();
  while (Date.now() - started < 30 * 60_000) {
    signal.throwIfAborted();
    const { job } = await client.get<{ job: Job }>('/jobs/' + resource(id), signal);
    signal.throwIfAborted();
    if (!job || job.id !== id) throw new WorkspaceError('처리 작업 기록이 일치하지 않습니다.');
    onUpdate(job);
    if (job.status === 'completed' || job.status === 'failed') return job;
    if (!['queued', 'running'].includes(job.status)) throw new WorkspaceError('처리 작업의 상태를 확인하지 못했습니다. 다시 조회하세요.');
    await new Promise<void>((resolve, reject) => {
      const cancel = () => { clearTimeout(timer); reject(new DOMException('취소됨', 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, 1500);
      signal.addEventListener('abort', cancel, { once: true });
      if (signal.aborted) cancel();
    });
  }
  throw new WorkspaceError('진행 상태 조회를 멈췄습니다. 다시 조회하면 저장된 작업을 확인할 수 있습니다.');
}

export async function watchAuthorizedResource<T>(
  load: () => Promise<T | undefined>, pending: (value: T) => boolean, signal: AbortSignal,
) {
  const deadline = Date.now() + 30 * 60_000;
  while (Date.now() < deadline) {
    signal.throwIfAborted();
    const value = await load();
    signal.throwIfAborted();
    if (value === undefined || !pending(value)) return value;
    await new Promise<void>((resolve, reject) => {
      const cancel = () => { clearTimeout(timer); reject(new DOMException('취소됨', 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, 1500);
      signal.addEventListener('abort', cancel, { once: true });
      if (signal.aborted) cancel();
    });
  }
  throw new WorkspaceError('진행 상태 조회를 멈췄습니다. 다시 조회하면 저장된 상태를 확인할 수 있습니다.');
}
