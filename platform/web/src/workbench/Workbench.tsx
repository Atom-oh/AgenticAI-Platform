import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { WorkspaceScope } from '../workspace/WorkspaceScope';
import { createWorkbenchClient, contextHash, readContext, listPages, resource, wb } from './client';
import { WorkbenchScope, useAction, useLoad, ActionState, Empty, Field, label, LoadState, Notice } from './shared';
import { Planning, Deliverables, Components } from './WorkspaceViews';
import { Changes, Development } from './Changes';
import KnowledgeView from './Knowledge';
import Skills from './Skills';
import { Sources, Batches, Tools, Operations } from './Operations';
import Pension from './Pension';
import Reports from './Reports';
import type { Overview, Project, WorkbenchView } from './types';
import './workbench.css';

export const WORKBENCH_VIEWS: Record<WorkbenchView, { title: string; group: string; description: string }> = {
  planning: { title: '상품기획서', group: '기획', description: '상품의 조건과 업무 흐름을 정리하고, 팀이 함께 사용할 기준을 게시하세요.' },
  changes: { title: '변경 요청·영향 분석', group: '기획', description: '무엇이 바뀌는지 비교하고, 연결된 화면과 개발 작업의 근거를 확인하세요.' },
  deliverables: { title: '디자인 산출물', group: '디자인', description: '저장된 시안과 검증 기록을 같은 기준으로 검토하세요.' },
  components: { title: 'React 컴포넌트', group: '개발', description: '실제 플랫폼 패키지의 속성·버전과 연결된 작업을 확인하세요.' },
  development: { title: '개발 작업·검증', group: '개발', description: '변경 근거부터 소스 검증과 개발 전달까지 이어서 처리하세요.' },
  knowledge: { title: '규정집·위키', group: '공통 지식', description: '프로젝트에서 조회할 수 있는 지식과 원본 버전, 연결 근거를 살펴보세요.' },
  skills: { title: 'Skill 제작실', group: '공통 지식', description: '반복되는 업무를 지침으로 만들고, 검증된 버전을 승인하세요.' },
  pension: { title: '연금 상담', group: '업무 시나리오', description: '가상 페르소나의 연금 현황을 살펴보고, 가정을 바꾸며 상담을 평가하세요.' },
  reports: { title: '보고서 작업실', group: '업무 시나리오', description: '변경과 상담의 근거를 모아 보고서를 만들고, 정확한 버전을 검토하세요.' },
  sources: { title: '지식 원본 등록', group: '플랫폼 운영', description: '사내 Confluence, Git 자산과 문서 스냅샷의 수집 범위를 관리하세요.' },
  batches: { title: '수집·ETL 배치', group: '플랫폼 운영', description: '수집에서 색인 게시까지 실제 처리 상태와 실패 근거를 확인하세요.' },
  tools: { title: 'MCP 도구 등록', group: '플랫폼 운영', description: '내부 지식·영향 분석 도구를 등록하고 제공 범위를 확인하세요.' },
  operations: { title: '운영자 센터', group: '플랫폼 운영', description: '프로젝트 데이터와 플랫폼 연결의 준비 상태를 구분해 확인하세요.' },
};
const rootClient = createWorkbenchClient('');
const operatorViews = new Set(['sources', 'batches', 'tools', 'operations']);

export default function Workbench({ view, onNavigate, onOverview }: {
  view: WorkbenchView | string; onNavigate?: (view: string) => void; onOverview?: (overview: Overview | null) => void;
}) {
  const [hash, setHash] = useState(() => window.location.hash);
  const params = useMemo(() => readContext(hash), [hash]);
  const projectId = params.get('projectId') || params.get('project') || '';
  const overviewCallback = useRef(onOverview);
  overviewCallback.current = onOverview;
  const reportOverview = useCallback((overview: Overview | null) => overviewCallback.current?.(overview), []);
  useEffect(() => { reportOverview(null); return () => reportOverview(null); }, [projectId, reportOverview]);
  const [name, setName] = useState('');
  const create = useAction();
  const [createdProjects, setCreatedProjects] = useState<Project[]>([]);
  const projects = useLoad(signal => listPages<Project>(rootClient, '/projects', 'projects', signal), []);
  const options = [...createdProjects, ...(projects.data || []).filter(item => !createdProjects.some(saved => saved.id === item.id))];
  useEffect(() => {
    const changed = () => setHash(window.location.hash);
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  function navigate(next: string, patch: Record<string, string | undefined> = {}) {
    const route = Object.hasOwn(WORKBENCH_VIEWS, next) ? `wb-${next}` : next;
    const target = contextHash(route, patch);
    if (next !== view) onNavigate?.(route);
    window.location.hash = target;
    setHash(target);
  }
  const meta = WORKBENCH_VIEWS[view as WorkbenchView];
  return <div className="workbench">
    <div className="wb-project-bar">
      <Field label="프로젝트"><select aria-label="프로젝트" value={projectId} disabled={create.busy}
        onChange={event => navigate(view, { projectId: event.target.value })}>
        <option value="">프로젝트 선택</option>
        {projectId && !options.some(item => item.id === projectId) && <option value={projectId}>연결된 프로젝트</option>}
        {options.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select></Field>
      <details className="wb-create-project"><summary>새 프로젝트</summary>
        <form onSubmit={event => { event.preventDefault(); void create.run(signal =>
          rootClient.post<{ project: Project }>('/projects', { name: name.trim(), requestId: create.requestId(name.trim()) }, signal),
        result => { setCreatedProjects(items => [result.project, ...items.filter(item => item.id !== result.project.id)]); setName(''); navigate(view, { projectId: result.project.id }); }); }}>
          <Field label="프로젝트 이름"><input required maxLength={180} value={name} onChange={event => setName(event.target.value)} /></Field>
          <button className="wb-primary" disabled={create.busy || !name.trim()}>프로젝트 만들기</button>
        </form>
      </details>
      <button type="button" className="wb-link" onClick={projects.refresh}>목록 새로고침</button>
    </div>
    {projects.error && <Notice error>{projects.error} <button onClick={projects.refresh}>목록 다시 조회</button></Notice>}
    <ActionState action={create} />
    {meta ? <>
      <header className="wb-heading"><span className="wb-eyebrow">{meta.group} / 함께 만드는 업무 기준</span>
        <h1>{meta.title}</h1><p>{meta.description}</p></header>
      {projectId ? /^[a-zA-Z0-9_-]{1,160}$/.test(projectId) ?
        <ScopedWorkbench key={projectId} projectId={projectId} view={view as WorkbenchView} params={params} navigate={navigate} onOverview={reportOverview} /> :
        <Notice error>프로젝트 식별자가 올바르지 않습니다. 목록에서 프로젝트를 다시 선택하세요.</Notice> :
        <Empty>프로젝트를 선택하거나 새로 만드세요. 프로젝트별 자료와 작업은 각각 보관됩니다.</Empty>}
    </> : <Notice error>요청한 작업 화면을 찾을 수 없습니다.</Notice>}
  </div>;
}

function ScopedWorkbench({ projectId, view, params, navigate, onOverview }: {
  projectId: string; view: WorkbenchView; params: URLSearchParams;
  navigate: (view: string, patch?: Record<string, string | undefined>) => void;
  onOverview: (overview: Overview | null) => void;
}) {
  const client = useMemo(() => createWorkbenchClient(projectId), [projectId]);
  const access = useLoad(async signal => {
    const [overview, { project }] = await Promise.all([
      client.get<Overview>(wb('/overview'), signal),
      rootClient.get<{ project: Project }>(`/projects/${resource(projectId)}`, signal),
    ]);
    if (!overview.actorId || !['owner', 'planner', 'designer', 'developer'].includes(overview.role) ||
      project.id !== projectId || project.members?.[overview.actorId]?.role !== overview.role) {
      throw new Error('현재 프로젝트의 참여 권한을 확인하지 못했습니다. 다시 조회하세요.');
    }
    return { overview, project };
  }, [client, view]);
  const example = useAction();
  const ready = access.data;
  useEffect(() => { onOverview(ready?.overview || null); }, [ready, onOverview]);
  useEffect(() => () => onOverview(null), [onOverview]);
  return <LoadState state={access}>{ready && <>
    <div className="wb-context-bar"><span className="wb-role">{label(ready.overview.role)}</span>
      <span>{ready.project.name}</span><span className="wb-muted">서버에서 확인한 현재 권한</span>
      {ready.overview.operator === true && <span className="wb-status">플랫폼 운영 권한</span>}
      <button className="wb-link" onClick={access.refresh}>권한·상태 새로고침</button>
      <button disabled={example.busy || !(ready.overview.role === 'owner' || ready.overview.role === 'planner')} onClick={() => void example.run(signal => client.post<{ productId?: string; description?: string }>(
        wb('/examples'), { requestId: `synthetic-v1:${projectId}` }, signal),
      result => { navigate(view, result.productId ? { productId: result.productId } : {}); access.refresh(); },
      '이 프로젝트에 가상 예제를 준비했습니다. 실제 고객 자료나 외부 연결이 아닙니다.')}>가상 예제 준비</button>
    </div>
    <ActionState action={example} />
    {(params.get('productId') || params.get('changeId') || params.get('targetId')) && <details className="wb-context-details">
      <summary>이어지는 작업 맥락</summary><dl>{['productId', 'changeId', 'targetId', 'impactHash', 'sourceRevision'].map(key =>
        params.get(key) ? <div key={key}><dt>{({ productId: '상품', changeId: '변경', targetId: '대상', impactHash: '영향 기준', sourceRevision: '원본 버전' } as Record<string, string>)[key]}</dt><dd>{params.get(key)}</dd></div> : null)}</dl>
    </details>}
    {operatorViews.has(view) && ready.overview.operator !== true ?
      <Notice error>운영자 화면에 접근할 권한이 없습니다. 서버가 확인한 플랫폼 운영 권한이 필요합니다. 프로젝트 관리자 권한과는 별도입니다.</Notice> :
      <WorkspaceScope.Provider value={{ client, project: ready.project, role: ready.overview.role, actorId: ready.overview.actorId }}>
        <WorkbenchScope.Provider value={{ client, ...ready, params, navigate, refresh: access.refresh }}>
          <View key={`${view}:${ready.overview.role}`} view={view} />
        </WorkbenchScope.Provider>
      </WorkspaceScope.Provider>}
  </>}</LoadState>;
}

function View({ view }: { view: WorkbenchView }) {
  switch (view) {
    case 'planning': return <Planning />;
    case 'changes': return <Changes />;
    case 'deliverables': return <Deliverables />;
    case 'components': return <Components />;
    case 'development': return <Development />;
    case 'knowledge': return <KnowledgeView />;
    case 'skills': return <Skills />;
    case 'pension': return <Pension />;
    case 'reports': return <Reports />;
    case 'sources': return <Sources />;
    case 'batches': return <Batches />;
    case 'tools': return <Tools />;
    case 'operations': return <Operations />;
  }
}
