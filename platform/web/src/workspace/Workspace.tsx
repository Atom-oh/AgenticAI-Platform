import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createWorkspaceClient, listAll, messageOf, resource, workspaceClient } from './client';
import { WorkspaceScope, useWorkspaceScope } from './WorkspaceScope';
import { ROLE_LABELS, roleFor } from './project';
import IntakePanel from './IntakePanel';
import SampleGallery from './SampleGallery';
import RulesPanel from './RulesPanel';
import RunsPanel from './RunsPanel';
import CollaborationSidebar from './CollaborationSidebar';
import GuidelineLibrary from './GuidelineLibrary';
import ProductPlanner from './ProductPlanner';
import GuidelineHistory from './GuidelineHistory';
import ComponentCatalogPanel from './ComponentCatalogPanel';
import HandoffPanel from './HandoffPanel';
import { WORKFLOW_STEPS, initialWorkflowStep, readWorkflowRoute, workflowHash, type WorkflowStep } from './workflow';
import { Notice } from './shared';
import type { Asset, Contract, GuideRef, Product, Project, Run, Selection, WorkspaceConfig } from './types';
import './workspace.css';

export default function Workspace({ initialStep }: { initialStep?: 'files' | 'guides' } = {}) {
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [route, setRoute] = useState(() => readWorkflowRoute(location.hash));
  const [projectId, setProjectId] = useState(route.projectId);
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const createRequest = useRef({ name: '', id: '' });
  const operation = useRef<AbortController | null>(null);
  const unsaved = useRef(false);
  useEffect(() => {
    const changed = () => {
      const next = readWorkflowRoute(location.hash);
      setRoute(next); setProjectId(next.projectId);
    };
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
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
    if (unsaved.current && !confirm('저장하지 않은 설계 변경을 닫고 새 프로젝트를 만들까요?')) return;
    const abort = new AbortController(); operation.current?.abort(); operation.current = abort; setCreating(true); setError('');
    if (createRequest.current.name !== name.trim()) createRequest.current = { name: name.trim(), id: crypto.randomUUID() };
    try {
      const { project: value } = await workspaceClient.post<{ project: Project }>('/projects',
        { name: createRequest.current.name, requestId: createRequest.current.id }, abort.signal);
      if (!abort.signal.aborted) {
        setProjects(items => [value, ...items.filter(item => item.id !== value.id)]); setProjectId(value.id); setName('');
        const hash = workflowHash(location.hash, { projectId: value.id });
        history.replaceState(null, '', hash); setRoute(readWorkflowRoute(hash));
      }
    } catch (reason) { if (!abort.signal.aborted) setError(messageOf(reason)); }
    finally { if (!abort.signal.aborted) setCreating(false); }
  };
  const role = projectId ? project?.id === projectId ? roleFor(project, config?.actorId) : null : 'owner';
  const client = useMemo(() => createWorkspaceClient(projectId ? { projectId } : {}), [projectId]);
  const scope = useMemo(() => ({ client, project, actorId: config?.actorId, role }), [client, project, config?.actorId, role]);
  return <div className="designer-workspace">
    <header className="ws-header"><div><h1>UX 설계 작업실</h1><p>업무를 정의하고, 고객 기준으로 흐름과 상태를 설계해 검증된 React 소스를 전달하세요.</p></div></header>
    <div className="ws-project-bar">
      <label className="ws-field">작업 공간<select aria-label="작업 공간" value={projectId} disabled={creating} onChange={event => {
        if (unsaved.current && !confirm('저장하지 않은 설계 변경을 닫고 다른 작업 공간으로 이동할까요?')) return;
        const next = event.target.value; setProject(null); setProjectId(next); unsaved.current = false;
        const hash = workflowHash(location.hash, { projectId: next });
        history.replaceState(null, '', hash); setRoute(readWorkflowRoute(hash));
      }}><option value="">개인 작업실 · 기존 비공개 이력</option>{projects.map(item =>
        <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <span>{projectId ? role ? `${ROLE_LABELS[role]} · 프로젝트 참여자와 공유` : '참여 권한 확인 중' : '개인 파일은 프로젝트로 자동 공유되지 않습니다.'}</span>
      <button onClick={() => setRetry(value => value + 1)}>작업 공간 새로 조회</button>
      <details><summary>새 프로젝트</summary><label className="ws-field">프로젝트 이름<input maxLength={120} value={name}
        onChange={event => setName(event.target.value)} /></label><button disabled={creating || !name.trim()} onClick={() => void create()}>프로젝트 만들기</button></details>
    </div>
    {error && <Notice error>{error}</Notice>}
    {route.invalid && <Notice error>작업 링크의 식별자를 확인하지 못했습니다. 선택한 작업 공간을 확인한 뒤 다시 시작하세요.
      <button onClick={() => {
        const hash = workflowHash(location.hash, { projectId });
        history.replaceState(null, '', hash); setRoute(readWorkflowRoute(hash));
      }}>선택한 작업 공간에서 새로 시작</button></Notice>}
    {!config || loading ? <p role="status">작업실을 준비하고 있습니다…</p> :
      !route.invalid && role && (!projectId || project?.id === projectId) ? <WorkspaceScope.Provider value={scope}>
        <ProjectWorkspace key={`${projectId || 'personal'}:${config.actorId || ''}:${role}`} config={config} initialStep={initialStep} route={route}
          onDirty={value => { unsaved.current = value; }} onProjectRefresh={() => setRetry(value => value + 1)} />
      </WorkspaceScope.Provider> : <Notice>참여 권한이 확인된 작업 공간을 선택하세요. 이전 프로젝트의 자료는 표시하지 않습니다.</Notice>}
  </div>;
}

function ProjectWorkspace({ config, initialStep, route, onDirty, onProjectRefresh }: {
  config: WorkspaceConfig; initialStep?: 'files' | 'guides'; route: ReturnType<typeof readWorkflowRoute>;
  onDirty: (dirty: boolean) => void; onProjectRefresh: () => void;
}) {
  const { client, project, role } = useWorkspaceScope();
  const [step, setStep] = useState<WorkflowStep>(() => initialWorkflowStep(route, role, initialStep));
  const [assetTab, setAssetTab] = useState<'guides' | 'files' | 'components'>(initialStep === 'files' || route.assetId ? 'files' : 'guides');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState(route.productId);
  const [selection, setSelection] = useState<Selection>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [guideRefs, setGuideRefs] = useState<GuideRef[]>([]);
  const [preferredContract, setPreferredContract] = useState('');
  const [editing, setEditing] = useState({ id: '', dirty: false });
  const [planningDirty, setPlanningDirty] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [requestedRun, setRequestedRun] = useState({ id: route.runId, round: route.round });
  const heading = useRef<HTMLHeadingElement>(null);
  const activeStage = useRef(step); activeStage.current = step;
  const reviewSelection = useCallback((value: Selection) => {
    if (activeStage.current === 'handoff') return;
    setSelection(value);
    if (value?.round && activeStage.current === 'review') history.replaceState(null, '',
      workflowHash(location.hash, { projectId: project?.id || '', productId, step: 'review', runId: value.run.id, round: value.round.number }));
  }, [project?.id, productId]);
  const handoffSelection = useCallback((value: Selection) => {
    if (activeStage.current !== 'handoff') return;
    setSelection(value);
    if (value?.round) history.replaceState(null, '', workflowHash(location.hash, {
      projectId: project?.id || '', productId, step: 'handoff', runId: value.run.id, round: value.round.number }));
  }, [project?.id, productId]);
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
  useEffect(() => { setGuideRefs(previous => previous.filter(ref => selected.includes(ref.assetId))); }, [selected]);
  useEffect(() => { onDirty(editing.dirty || planningDirty); }, [editing.dirty, planningDirty, onDirty]);
  useEffect(() => {
    setStep(initialWorkflowStep(route, role, initialStep));
    setProductId(route.productId); setRequestedRun({ id: route.runId, round: route.round });
  }, [route]);
  const onEditing = useCallback((id: string, dirty: boolean) => {
    setEditing(previous => previous.id === id && previous.dirty === dirty ? previous : { id, dirty });
    if (id && ['define', 'design'].includes(activeStage.current)) {
      const params = new URLSearchParams(location.hash.split('?')[1]);
      params.set('contractId', id);
      history.replaceState(null, '', location.hash.split('?')[0] + '?' + params.toString());
    }
  }, []);
  const onApproved = useCallback((contract: Contract) => {
    setPreferredContract(contract.id); setContracts(previous => [contract, ...previous.filter(item => item.id !== contract.id)]);
  }, []);
  const product = products.find(item => item.id === productId);
  const scopedContracts = contracts.filter(item => !productId || !item.productId || item.productId === productId);
  const scopedRuns = runs.filter(item => !productId || item.productId === productId);
  const navigate = (next: WorkflowStep, runId?: string, round?: number) => {
    setStep(next);
    const target = runId ? { id: runId, round } : {
      id: selection?.run.id || requestedRun.id, round: selection?.round?.number || requestedRun.round,
    };
    if (target.id && (next === 'review' || next === 'handoff')) setRequestedRun(target);
    const hash = workflowHash(location.hash, { projectId: project?.id || '', productId, step: next,
      contractId: editing.id || undefined,
      ...(next === 'review' || next === 'handoff' ? { runId: target.id, round: target.round } : {}) });
    if (hash !== location.hash) history.pushState(null, '', hash);
    requestAnimationFrame(() => heading.current?.focus({ preventScroll: true }));
  };
  const chooseProduct = (id: string) => {
    if (id !== productId && (editing.dirty || planningDirty) && !confirm('저장하지 않은 설계 변경을 닫고 다른 상품을 선택할까요?')) return;
    setProductId(id); setSelection(null); setPreferredContract(''); setEditing({ id: '', dirty: false }); setRequestedRun({ id: '', round: undefined });
    setPlanningDirty(false);
    setSelected([]); setGuideRefs([]);
    history.replaceState(null, '', workflowHash(location.hash, { projectId: project?.id || '', productId: id, step }));
  };
  const current = WORKFLOW_STEPS.find(item => item.id === step)!;
  const saveProduct = (value: Product) => {
    setProducts(items => [value, ...items.filter(item => item.id !== value.id)]); setProductId(value.id);
    history.replaceState(null, '', workflowHash(location.hash, { projectId: project?.id || '', productId: value.id, step }));
  };
  return <>
    {project && <div className="ws-product-context"><label className="ws-field">공유 상품<select aria-label="공유 상품" value={productId}
      onChange={event => chooseProduct(event.target.value)}><option value="">상품 미선택 · 전체 이력</option>
      {products.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <p>{product?.publishedGuidelineId ? `게시 ${product.publishedRevision}차 기준으로 함께 설계합니다.` : '업무 정의에서 상품 조건과 진행 절차를 먼저 확정하세요.'}</p></div>}
    <nav className="ws-workflow-nav" aria-label="UX 설계 단계">{WORKFLOW_STEPS.map((item, index) =>
      <button key={item.id} data-workflow-step={item.id} aria-label={`${index + 1} ${item.label}`}
        aria-current={step === item.id ? 'step' : undefined} onClick={() => navigate(item.id)}>
        <span>{String(index + 1).padStart(2, '0')}</span><strong>{item.label}</strong><small>{item.output}</small>
      </button>)}</nav>
    <div className="ws-stage-heading"><div><h2 ref={heading} tabIndex={-1}>{current.label}</h2><p>{current.purpose}</p></div>
      <button onClick={() => void refresh()} disabled={loading}>{loading ? '조회 중…' : '내 작업 새로 조회'}</button></div>
    {error && <Notice error>{error}</Notice>}
    {productId && !product && !loading && <Notice error>연결된 상품을 이 작업 공간에서 찾지 못했습니다. 현재 프로젝트의 상품을 다시 선택하세요.</Notice>}
    {(!productId || product) && <div className="ws-collaboration-layout"><div className="ws-work-area">
      {project && <section className="ws-section ws-product-definition" hidden={step !== 'define'}>
        <h2>상품 조건과 진행 절차</h2><p>팀이 사용할 상품 기준을 게시한 뒤 UX 목적과 상태를 정합니다.</p>
        <details open={!product?.publishedGuidelineId}><summary>{product?.publishedGuidelineId ? '게시 기준 확인·새 버전 작성' : '상품 지침 작성·게시'}</summary>
          <ProductPlanner key={product?.id || 'new'} product={product} onSaved={saveProduct} onEditing={setPlanningDirty} refresh={() => void refresh()} />
          {product && <GuidelineHistory key={product.id} product={product} assets={assets} />}</details></section>}
      <div hidden={step !== 'define' && step !== 'design'}><RulesPanel key={productId || 'unbound'} config={config} assets={assets} selected={selected}
        runs={scopedRuns}
        guideRefs={guideRefs} stage={step === 'define' ? 'define' : 'design'} initialContractId={route.contractId}
        contracts={scopedContracts} product={product} onContinue={() => navigate(step === 'define' ? 'assets' : 'review')}
        refresh={() => void refresh()} onApproved={onApproved} onEditing={onEditing} /></div>
      <div hidden={step !== 'assets'}>
        <nav className="ws-asset-tabs" aria-label="설계 기준 종류">{([['guides', '고객 가이드'], ['files', '화면·그래픽 자료'], ['components', '실행 컴포넌트']] as const).map(([id, label]) =>
          <button key={id} aria-pressed={assetTab === id} onClick={() => setAssetTab(id)}>{label}</button>)}</nav>
        {step === 'assets' && assetTab === 'guides' && <GuidelineLibrary assets={assets} selected={guideRefs} onSelected={refs => {
          const assetIds = [...new Set([...selected, ...refs.map(ref => ref.assetId)])];
          if (assetIds.length > 20) { setError('선택한 파일은 최대 20개입니다. 화면·그래픽 자료에서 선택을 줄이세요.'); return; }
          setSelected(assetIds); setGuideRefs(refs);
        }} onContinue={() => navigate('design')} onUpload={() => setAssetTab('files')} />}
        <div hidden={assetTab !== 'files'}><SampleGallery catalog={config.componentCatalog} active={step === 'assets' && assetTab === 'files'} />
          <IntakePanel config={config} assets={assets} selected={selected} onSelected={setSelected} initialAssetId={route.assetId}
            refresh={() => void refresh()} onContinue={() => navigate('design')} /></div>
        {assetTab === 'components' && <section className="ws-section"><h2>설계에 사용할 실행 컴포넌트</h2>
          <p>가이드의 구성 요소와 실제 구현을 대조하세요. 연결되지 않은 구성은 흐름·상태 설계에서 확인할 내용으로 남깁니다.</p>
          <ComponentCatalogPanel summary={config.componentCatalog} />
          <button onClick={() => navigate('design')}>흐름·상태 설계로</button></section>}
      </div>
      <div hidden={step !== 'review'}><RunsPanel key={productId || 'unbound'} config={config} assets={assets}
        contracts={scopedContracts} runs={scopedRuns} initialRunId={requestedRun.id} initialRound={requestedRun.round}
        refresh={() => void refresh()} preferredContract={preferredContract} editing={editing} onSelection={reviewSelection} product={product}
        onHandoff={(runId, round) => navigate('handoff', runId, round)} /></div>
      {step === 'handoff' && <HandoffPanel key={productId || 'unbound'} runs={scopedRuns} contracts={scopedContracts} selection={selection}
        product={product} initialRunId={requestedRun.id} initialRound={requestedRun.round} onSelection={handoffSelection}
        onReview={(runId, round) => navigate('review', runId, round)} />}
    </div><CollaborationSidebar products={products} product={product} onProduct={chooseProduct} selection={selection} assets={assets} catalog={config.componentCatalog}
      workflow refresh={() => void refresh()} onProductSaved={saveProduct}
      onProjectRefresh={onProjectRefresh} /></div>}
  </>;
}
