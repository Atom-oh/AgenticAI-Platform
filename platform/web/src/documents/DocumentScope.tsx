import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import { auth } from '../lib';
import { listAll, WorkspaceError } from '../workspace/client';
import type { WorkspaceClient } from '../workspace/client';
import { documentHref, documentMessage, makeLibraryClient } from './client';
import type { LibraryConfig, Project } from './types';
import './documents.css';

const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
export type DocumentRoute = {
  projectId?: string; documentId?: string; revisionId?: string; paragraph?: string;
  textHash?: string; analysisId?: string; ref?: string; register?: boolean;
};
export function readRoute(hash: string): DocumentRoute {
  const query = new URLSearchParams(hash.split('?')[1] || '');
  const route: DocumentRoute = {};
  if (query.get('register') === '1') route.register = true;
  for (const key of ['projectId', 'documentId', 'revisionId', 'analysisId', 'ref'] as const) {
    if (!query.has(key)) continue;
    const value = query.get(key)!;
    if (query.getAll(key).length !== 1 || !ID.test(value)) throw new Error('문서 연결 정보를 확인할 수 없습니다. 문서함에서 다시 선택하세요.');
    route[key] = value;
  }
  if (query.has('textHash')) {
    const value = query.get('textHash')!;
    if (query.getAll('textHash').length !== 1 || !/^[a-f0-9]{64}$/.test(value)) throw new Error('원문 버전의 확인값이 올바르지 않습니다.');
    route.textHash = value;
  }
  if (query.has('paragraph')) {
    const value = query.get('paragraph')!;
    if (query.getAll('paragraph').length !== 1 || !/^p[0-9]{6}$/.test(value)) throw new Error('근거 문단 연결이 올바르지 않습니다.');
    route.paragraph = value;
  }
  if (route.revisionId && (!route.documentId || !route.revisionId.startsWith(route.documentId + '--')) ||
      (route.textHash || route.paragraph) && !route.revisionId) {
    throw new Error('문서와 원문 버전의 연결이 맞지 않습니다. 최신 버전으로 자동 이동하지 않습니다.');
  }
  return route;
}
export function analysisHref(analysisId?: string, projectId?: string) {
  if (analysisId && !ID.test(analysisId) || projectId && !ID.test(projectId)) throw new WorkspaceError('분석 연결 정보를 확인하지 못했습니다.');
  const query = new URLSearchParams();
  if (analysisId) query.set('analysisId', analysisId);
  if (projectId) query.set('projectId', projectId);
  return '#/s1' + (query.size ? '?' + query : '');
}
export function libraryHref(options: DocumentRoute = {}) {
  const href = documentHref(options.documentId, options.projectId, options.ref);
  const query = new URLSearchParams(href.split('?')[1] || '');
  if (options.analysisId && ID.test(options.analysisId)) query.set('analysisId', options.analysisId);
  if (options.register) query.set('register', '1');
  return '#/documents' + (query.size ? '?' + query : '');
}
export function revisionHref(documentId: string, revisionId: string, options: DocumentRoute = {}) {
  const query = new URLSearchParams({ documentId, revisionId });
  if (options.projectId) query.set('projectId', options.projectId);
  if (options.analysisId) query.set('analysisId', options.analysisId);
  if (options.textHash) query.set('textHash', options.textHash);
  const href = '#/documents?' + query;
  readRoute(href);
  return href;
}

// Auth currently has no subscription API. Observe it without persisting tokens or content.
const snapshot = () => JSON.stringify([auth.token, window.location.hash]);
function subscribe(listener: () => void) {
  let previous = snapshot();
  const check = () => { const next = snapshot(); if (next !== previous) { previous = next; listener(); } };
  const timer = window.setInterval(check, 250);
  for (const event of ['hashchange', 'authchange', 'focus', 'pageshow']) window.addEventListener(event, check);
  document.addEventListener('visibilitychange', check);
  return () => {
    clearInterval(timer);
    for (const event of ['hashchange', 'authchange', 'focus', 'pageshow']) window.removeEventListener(event, check);
    document.removeEventListener('visibilitychange', check);
  };
}
type Scope = {
  client: WorkspaceClient; config: LibraryConfig; route: DocumentRoute;
  current: () => boolean; deny: (error: unknown) => void;
};
const Context = createContext<Scope | null>(null);
export function useDocumentScope() {
  const value = useContext(Context);
  if (!value) throw new Error('문서함 범위를 확인하지 못했습니다.');
  return value;
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`doc-notice${error ? ' doc-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
export function DocumentBoundary({ view, children }: { view: 'documents' | 's1'; children: ReactNode }) {
  const identity = useSyncExternalStore(subscribe, snapshot, () => '[]');
  const [token, hash] = JSON.parse(identity) as [string | null, string];
  let route: DocumentRoute;
  try { route = readRoute(hash); } catch (error) {
    return <div className="doc-app"><Notice error>{documentMessage(error)} <a href={'#/' + view}>목록으로 돌아가기</a></Notice></div>;
  }
  if (!token) return <div className="doc-app"><Notice error>로그인이 필요하거나 만료되었습니다. 다시 로그인하세요.</Notice></div>;
  return <CollectionSession key={identity} {...{ identity, route, view }}>{children}</CollectionSession>;
}
function CollectionSession({ identity, route, view, children }: {
  identity: string; route: DocumentRoute; view: 'documents' | 's1'; children: ReactNode;
}) {
  const client = useMemo(() => makeLibraryClient(route.projectId), [route.projectId]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [config, setConfig] = useState<LibraryConfig>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const mounted = useRef(false);
  const current = useCallback(() => mounted.current && snapshot() === identity, [identity]);
  const deny = useCallback((reason: unknown) => {
    if (!current()) return;
    setConfig(undefined); setProjects([]); setError(documentMessage(reason));
  }, [current]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    setConfig(undefined); setError('');
    Promise.all([
      listAll<Project>(makeLibraryClient(), 'projects', controller.signal),
      client.get<LibraryConfig>('/documents/config', controller.signal),
    ]).then(([items, value]) => {
      if (controller.signal.aborted || !current()) return;
      if ((value.projectId || undefined) !== route.projectId ||
          route.projectId && !items.some(item => item.id === route.projectId)) {
        throw new WorkspaceError('현재 참여 중인 프로젝트를 확인할 수 없습니다. 개인 문서함 또는 참여 프로젝트를 다시 선택하세요.', 403);
      }
      setProjects(items); setConfig(value);
    }).catch(reason => { if (!controller.signal.aborted) deny(reason); });
    return () => { mounted.current = false; controller.abort(); };
  }, [attempt, client, current, deny, route.projectId]);
  const scope = useMemo(() => config ? { client, config, route, current, deny } : null, [client, config, route, current, deny]);
  const title = view === 'documents' ? '내부 문서함' : '규정 영향 분석';
  return <div className="doc-app">
    <header className="doc-heading">
      <div><p className="doc-eyebrow">{view === 'documents' ? '원문과 검토 기록' : 'S1 · 원문 기반 검토'}</p>
        <h2>{title}</h2><p className="doc-muted">{view === 'documents'
          ? '등록한 원문을 버전별로 보관하고, 검토한 자료만 분석에 사용합니다.'
          : '규정 원문과 연결된 영향 후보를 확인하고, 담당자가 변경 여부를 판단합니다.'}</p></div>
      <label className="doc-scope">문서함 범위<select aria-label="문서함 범위" value={route.projectId || ''} onChange={event => {
        const id = event.target.value;
        if (id && !projects.some(project => project.id === id)) return;
        window.location.hash = view === 's1' ? analysisHref(undefined, id || undefined)
          : '#/documents' + (id ? '?projectId=' + encodeURIComponent(id) : '');
      }}>
        <option value="">개인 문서함</option>
        {route.projectId && !projects.some(project => project.id === route.projectId) &&
          <option value={route.projectId}>선택한 프로젝트 · 확인 필요</option>}
        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select></label>
    </header>
    {error ? <Notice error>{error} <button onClick={() => setAttempt(value => value + 1)}>참여 권한 다시 조회</button></Notice>
      : !scope ? <Notice>문서함과 참여 권한을 확인하고 있습니다…</Notice>
      : <Context.Provider value={scope}>{children}</Context.Provider>}
  </div>;
}

/** Every operation checks both its generation and the live auth/URL identity.
 * A fetcher that ignores AbortSignal cannot repopulate an old private view. */
export function usePrivateTask() {
  const scope = useDocumentScope();
  const active = useRef<AbortController>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const cancel = useCallback(() => {
    active.current?.abort(); active.current = undefined; setBusy(false); setError('');
  }, []);
  useEffect(() => () => { active.current?.abort(); active.current = undefined; }, []);
  const run = useCallback(async (work: (signal: AbortSignal, current: () => boolean) => Promise<void>) => {
    cancel();
    if (!scope.current()) return;
    const controller = new AbortController();
    active.current = controller;
    const current = () => active.current === controller && !controller.signal.aborted && scope.current();
    setBusy(true); setError('');
    try { await work(controller.signal, current); }
    catch (reason) {
      if (!current()) return;
      setError(documentMessage(reason));
      if (reason instanceof WorkspaceError && [401, 403].includes(reason.status)) scope.deny(reason);
    } finally { if (current()) { setBusy(false); active.current = undefined; } }
  }, [cancel, scope]);
  return { run, cancel, busy, error };
}

export function useDocumentQuery<T>(path: string) {
  const { client } = useDocumentScope();
  const { run, busy, error } = usePrivateTask();
  const [data, setData] = useState<T>();
  const [version, setVersion] = useState(0);
  useEffect(() => {
    setData(undefined);
    void run(async (signal, current) => {
      const value = await client.get<T>(path, signal);
      if (current()) setData(value);
    });
  }, [client, path, run, version]);
  return { data, busy, error, reload: () => setVersion(value => value + 1) };
}
