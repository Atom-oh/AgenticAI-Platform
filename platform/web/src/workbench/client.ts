import { createWorkspaceClient, pollJob, WorkspaceError, type WorkspaceClient } from '../workspace/client';
import type { Job, Skill, SkillExecution } from './types';

export { resource, messageOf, aborted, WorkspaceError } from '../workspace/client';
export type { WorkspaceClient } from '../workspace/client';

export function createWorkbenchClient(projectId: string, options: Omit<Parameters<typeof createWorkspaceClient>[0], 'projectId'> = {}) {
  return createWorkspaceClient({ ...options, ...(projectId ? { projectId } : {}) });
}
export const wb = (path: string) => `/workbench${path}`;
export function readContext(hash = window.location.hash) {
  return new URLSearchParams(hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : '');
}
const scopedKeys = ['product', 'productId', 'change', 'changeId', 'target', 'targetId', 'impactHash',
  'sourceRevision', 'targetRevision', 'revision', 'runId', 'assetId', 'guidelineId', 'sessionId', 'reportId', 'skillId', 'executionId', 'sourceId', 'batchId', 'generation'];
export function contextHash(view: string, patch: Record<string, string | undefined> = {}, hash = window.location.hash) {
  const params = readContext(hash);
  if (patch.projectId !== undefined && patch.projectId !== (params.get('projectId') || params.get('project') || '')) {
    scopedKeys.forEach(key => params.delete(key));
  }
  if (patch.projectId !== undefined) params.delete('project');
  Object.entries(patch).forEach(([key, value]) => value ? params.set(key, value) : params.delete(key));
  return `#/${view}${params.size ? '?' + params.toString() : ''}`;
}
export async function listWorkbench<T>(client: Pick<WorkspaceClient, 'get'>, path: string, signal?: AbortSignal): Promise<T[]> {
  return listPages<T>(client, wb(path), 'items', signal);
}
export async function listPages<T>(client: Pick<WorkspaceClient, 'get'>, path: string, key: string, signal?: AbortSignal): Promise<T[]> {
  const items: T[] = [], seen = new Set<string>();
  let cursor = '';
  do {
    signal?.throwIfAborted();
    const result = await client.get<Record<string, unknown>>(path + (cursor ? `${path.includes('?') ? '&' : '?'}cursor=${encodeURIComponent(cursor)}` : ''), signal);
    signal?.throwIfAborted();
    if (!Array.isArray(result[key])) throw new WorkspaceError('서버의 목록 응답을 확인하지 못했습니다.');
    items.push(...result[key] as T[]);
    cursor = typeof result.cursor === 'string' ? result.cursor : '';
    if (cursor && (seen.has(cursor) || seen.size >= 100)) throw new WorkspaceError('다음 페이지를 확인하지 못했습니다. 조회 범위를 줄여 주세요.');
    seen.add(cursor);
  } while (cursor);
  return items;
}
export async function waitForJob(client: Pick<WorkspaceClient, 'get'>, id: string, options: {
  signal?: AbortSignal; interval?: number; onUpdate?: (job: Job) => void;
} = {}): Promise<Job> {
  return await pollJob(client, id, options) as unknown as Job;
}
export function errorText(reason: unknown) {
  if (reason instanceof WorkspaceError && /not[-_]configured|unavailable|not[-_]run/.test(reason.code)) {
    return `아직 연결 또는 실행 설정이 준비되지 않았습니다. 운영자에게 설정 상태를 확인하세요. (${reason.code})`;
  }
  return reason instanceof Error ? reason.message : '요청을 처리하지 못했습니다.';
}

type SkillJobOptions = { signal?: AbortSignal; onUpdate?: (job: Job) => void };
export async function validateSkill(client: Pick<WorkspaceClient, 'get' | 'post'>, skill: Skill, options: SkillJobOptions = {}): Promise<Skill> {
  const response = await client.post<{ skill: Skill; job?: Job }>(wb(`/skills/${encodeURIComponent(skill.id)}/validate`),
    { version: skill.version }, options.signal);
  let saved = response.skill;
  if (response.job) {
    if (!response.job.id) throw new WorkspaceError('검증 작업의 접수 기록을 확인하지 못했습니다.');
    await waitForJob(client, response.job.id, options);
    saved = (await client.get<{ skill: Skill }>(wb(`/skills/${encodeURIComponent(skill.id)}`), options.signal)).skill;
  }
  options.signal?.throwIfAborted();
  const behavior = saved?.validation?.behavior as { status?: string } | undefined;
  if (saved?.id !== skill.id || saved.contentHash !== skill.contentHash || saved.version <= skill.version ||
    saved.validation?.contentHash !== skill.contentHash || !['passed', 'failed', 'not-run'].includes(behavior?.status || '')) {
    throw new WorkspaceError('현재 버전의 저장된 검증 결과를 확인하지 못했습니다. Skill을 다시 조회하세요.');
  }
  return saved;
}
function assertExecutionVersion(execution: SkillExecution, skill: Skill, id: string) {
  if (execution.artifact?.id !== id || execution.artifact.skillId !== skill.id ||
    execution.artifact.skillVersion !== skill.version || execution.artifact.contentHash !== skill.contentHash) {
    throw new WorkspaceError('실행 결과의 Skill 버전·콘텐츠 해시가 현재 승인 기준과 다릅니다.');
  }
}
export async function readSkillExecution(client: Pick<WorkspaceClient, 'get'>, skill: Skill, id: string, options: SkillJobOptions = {}): Promise<SkillExecution> {
  const path = wb(`/skills/${encodeURIComponent(skill.id)}/executions/${encodeURIComponent(id)}`);
  let value = await client.get<SkillExecution>(path, options.signal);
  assertExecutionVersion(value, skill, id);
  if (value.artifact.status !== 'completed' && value.artifact.jobId) {
    await waitForJob(client, value.artifact.jobId, options);
    value = await client.get<SkillExecution>(path, options.signal);
    assertExecutionVersion(value, skill, id);
  }
  options.signal?.throwIfAborted();
  if (value.artifact.status !== 'completed' || !value.result || typeof value.result.answer !== 'string' ||
    !['answered', 'refused', 'insufficient-evidence'].includes(value.result.status)) {
    throw new WorkspaceError('저장된 실행 결과가 아직 준비되지 않았습니다. 실행 상태를 다시 확인하세요.');
  }
  return value;
}
export async function executeApprovedSkill(client: Pick<WorkspaceClient, 'get' | 'post'>, skill: Skill, input: string, requestId: string,
  options: SkillJobOptions & { onQueued?: (id: string) => void } = {}): Promise<SkillExecution> {
  const response = await client.post<{ artifact: SkillExecution['artifact']; job: Job }>(wb(`/skills/${encodeURIComponent(skill.id)}/execute`),
    { version: skill.version, contentHash: skill.contentHash, input, requestId }, options.signal);
  options.signal?.throwIfAborted();
  if (!response.artifact?.id || !response.job?.id) throw new WorkspaceError('Skill 실행 접수 기록을 확인하지 못했습니다.');
  assertExecutionVersion({ artifact: response.artifact, result: null }, skill, response.artifact.id);
  options.onQueued?.(response.artifact.id);
  await waitForJob(client, response.job.id, options);
  // Job receipts are never an answer source; the result endpoint rechecks access.
  return readSkillExecution(client, skill, response.artifact.id, options);
}
