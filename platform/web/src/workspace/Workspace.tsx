import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createWorkspaceClient, listAll, messageOf, resource, workspaceClient } from './client';
import { WorkspaceScope, useWorkspaceScope } from './WorkspaceScope';
import { ROLE_LABELS, roleFor } from './project';
import IntakePanel from './IntakePanel';
import RulesPanel from './RulesPanel';
import RunsPanel from './RunsPanel';
import CollaborationSidebar from './CollaborationSidebar';
import { Notice } from './shared';
import type { Asset, Contract, Product, Project, Run, Selection, WorkspaceConfig } from './types';
import './workspace.css';

export default function Workspace() {
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const createRequest = useRef({ name: '', id: '' });
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    void Promise.allSettled([workspaceClient.get<WorkspaceConfig>('/config', abort.signal),
      listAll<Project>(workspaceClient, 'projects', abort.signal)]).then(([configuration, list]) => {
      if (abort.signal.aborted) return;
      if (configuration.status === 'fulfilled') setConfig(configuration.value);
      if (list.status === 'fulfilled') setProjects(list.value);
      setError(configuration.status === 'rejected' ? messageOf(configuration.reason) : list.status === 'rejected' ? messageOf(list.reason) : '');
    });
    return () => abort.abort();
  }, [retry]);
  useEffect(() => {
    const abort = new AbortController(); setProject(null); setError('');
    if (!projectId) { setLoading(false); return () => abort.abort(); }
    setLoading(true);
    workspaceClient.get<{ project: Project }>(`/projects/${resource(projectId)}`, abort.signal).then(({ project: value }) => {
      if (abort.signal.aborted) return;
      if (value.id !== projectId || !roleFor(value, config?.actorId)) throw new Error('이 프로젝트의 현재 참여 권한을 확인할 수 없습니다.');
      setProject(value);
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [projectId, retry, config?.actorId]);
  useEffect(() => () => operation.current?.abort(), []);
  const create = async () => {
    if (!name.trim() || creating) return;
    const abort = new AbortController(); operation.current?.abort(); operation.current = abort; setCreating(true); setError('');
    if (createRequest.current.name !== name.trim()) createRequest.current = { name: name.trim(), id: crypto.randomUUID() };
    try {
      const { project: value } = await workspaceClient.post<{ project: Project }>('/projects',
        { name: createRequest.current.name, requestId: createRequest.current.id }, abort.signal);
      if (!abort.signal.aborted) { setProjects(items => [value, ...items.filter(item => item.id !== value.id)]); setProjectId(value.id); setName(''); }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setCreating(false); }
  };
  const role = projectId ? project?.id === projectId ? roleFor(project, config?.actorId) : null : 'owner';
  const client = useMemo(() => createWorkspaceClient(projectId ? { projectId } : {}), [projectId]);
  const scope = useMemo(() => ({ client, project, actorId: config?.actorId, role }), [client, project, config?.actorId, role]);
  return <div className="designer-workspace">
    <header className="ws-header"><div><h1>파일·스킬 작업실</h1><p>파일과 상품 지침에서 시작해, 고정한 기준으로 React 화면을 만들고 근거를 함께 검토하세요.</p></div></header>
    <div className="ws-project-bar">
      <label className="ws-field">작업 공간<select aria-label="작업 공간" value={projectId} disabled={creating} onChange={event => {
        setProject(null); setProjectId(event.target.value);
      }}><option value="">개인 작업실 · 기존 비공개 이력</option>{projects.map(item =>
        <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <span>{projectId ? role ? `${ROLE_LABELS[role]} · 프로젝트 참여자와 공유` : '참여 권한 확인 중' : '개인 파일은 프로젝트로 자동 공유되지 않습니다.'}</span>
      <button onClick={() => setRetry(value => value + 1)}>작업 공간 새로 조회</button>
      <details><summary>새 프로젝트</summary><label className="ws-field">프로젝트 이름<input maxLength={120} value={name}
        onChange={event => setName(event.target.value)} /></label><button disabled={creating || !name.trim()} onClick={() => void create()}>프로젝트 만들기</button></details>
    </div>
    {error && <Notice error>{error}</Notice>}
    {!config || loading ? <p role="status">작업실을 준비하고 있습니다…</p> :
      role && (!projectId || project?.id === projectId) ? <WorkspaceScope.Provider value={scope}>
        <ProjectWorkspace key={`${projectId || 'personal'}:${config.actorId || ''}:${role}`} config={config} onProjectRefresh={() => setRetry(value => value + 1)} />
      </WorkspaceScope.Provider> : <Notice>참여 권한이 확인된 작업 공간을 선택하세요. 이전 프로젝트의 자료는 표시하지 않습니다.</Notice>}
  </div>;
}

function ProjectWorkspace({ config, onProjectRefresh }: { config: WorkspaceConfig; onProjectRefresh: () => void }) {
  const { client, project } = useWorkspaceScope();
  const [step, setStep] = useState<'files' | 'rules' | 'runs'>('files');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState('');
  const [selection, setSelection] = useState<Selection>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [preferredContract, setPreferredContract] = useState('');
  const [editing, setEditing] = useState({ id: '', dirty: false });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort; setLoading(true);
    const requests = await Promise.allSettled([listAll<Asset>(client, 'assets', abort.signal),
      listAll<Contract>(client, 'contracts', abort.signal), listAll<Run>(client, 'runs', abort.signal),
      project ? listAll<Product>(client, 'products', abort.signal) : Promise.resolve([] as Product[])]);
    if (abort.signal.aborted) return;
    const [files, rules, results, productList] = requests;
    if (files.status === 'fulfilled') { setAssets(files.value); setSelected(previous => previous.filter(id => files.value.some(asset => asset.id === id && !asset.archived && !asset.system))); }
    if (rules.status === 'fulfilled') setContracts(rules.value);
    if (results.status === 'fulfilled') setRuns(results.value);
    if (productList.status === 'fulfilled') setProducts(productList.value);
    const failure = requests.find((request): request is PromiseRejectedResult => request.status === 'rejected');
    setError(failure ? messageOf(failure.reason) : ''); setLoading(false);
  }, [client, project?.id]);
  useEffect(() => { void refresh(); return () => controller.current?.abort(); }, [refresh]);
  const onEditing = useCallback((id: string, dirty: boolean) => setEditing(previous =>
    previous.id === id && previous.dirty === dirty ? previous : { id, dirty }), []);
  const onApproved = useCallback((contract: Contract) => {
    setPreferredContract(contract.id); setContracts(previous => [contract, ...previous.filter(item => item.id !== contract.id)]);
  }, []);
  const product = products.find(item => item.id === productId);
  const chooseProduct = (id: string) => {
    if (id !== productId && editing.id && editing.dirty && !confirm('저장하지 않은 규칙을 닫고 다른 상품을 선택할까요?')) return;
    setProductId(id); setSelection(null); setPreferredContract(''); setEditing({ id: '', dirty: false });
  };
  return <>
    <div className="ws-section-heading"><nav className="ws-navigation" aria-label="작업실 단계">
      {([['files', '1', '파일 준비'], ['rules', '2', '규칙 확인·승인'], ['runs', '3', '생성·검수·수정']] as const).map(([id, number, label]) =>
        <button key={id} aria-current={step === id ? 'step' : undefined} className={step === id ? 'is-selected' : ''}
          onClick={() => setStep(id)}><span>{number}</span>{label}</button>)}
    </nav><button onClick={() => void refresh()} disabled={loading}>{loading ? '조회 중…' : '내 작업 새로 조회'}</button></div>
    {error && <Notice error>{error}</Notice>}
    <div className="ws-collaboration-layout"><div className="ws-work-area">
      <div hidden={step !== 'files'}><IntakePanel config={config} assets={assets} selected={selected} onSelected={setSelected}
        refresh={() => void refresh()} onContinue={() => setStep('rules')} /></div>
      <div hidden={step !== 'rules'}><RulesPanel key={productId || 'unbound'} config={config} assets={assets} selected={selected}
        contracts={contracts.filter(item => !productId || item.productId === productId)} product={product}
        refresh={() => void refresh()} onApproved={onApproved} onEditing={onEditing} /></div>
      <div hidden={step !== 'runs'}><RunsPanel key={productId || 'unbound'} config={config} assets={assets}
        contracts={contracts.filter(item => !productId || item.productId === productId)} runs={runs.filter(item => !productId || item.productId === productId)}
        refresh={() => void refresh()} preferredContract={preferredContract} editing={editing} onSelection={setSelection} product={product} /></div>
    </div><CollaborationSidebar products={products} product={product} onProduct={chooseProduct} selection={selection} assets={assets} catalog={config.componentCatalog}
      refresh={() => void refresh()} onProductSaved={value => { setProducts(items => [value, ...items.filter(item => item.id !== value.id)]); setProductId(value.id); }}
      onProjectRefresh={onProjectRefresh} /></div>
  </>;
}
