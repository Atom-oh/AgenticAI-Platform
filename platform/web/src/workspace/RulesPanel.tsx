import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { aborted, messageOf, resource } from './client';
import { useWorkspaceScope } from './WorkspaceScope';
import { can } from './project';
import { ACTIONS, BOOLEAN_ACTIONS, KEYS, STYLE_PROPERTIES, applyManualAssets, blankContract, contractProblems, editable, manualAssets, newRule, newStep, stateLabel } from './rules';
import { JobProgress, ModelPicker, Notice } from './shared';
import BindingsEditor from './BindingsEditor';
import { noticeProblems, noticeRule, type GuidelinePage } from './guidelinePages';
import { useGuidelinePages } from './useGuidelinePages';
import StatePlan from './StatePlan';
import ChangeRequestPanel, { requestIssues } from './ChangeRequestPanel';
import { DESIGN_STARTERS, UX_STATES } from './workflow';
import type { Action, Asset, Contract, EditableContract, GuideRef, Job, Product, Rule, Run, Step, StyleProperty, UXState, WorkspaceConfig } from './types';

export default function RulesPanel({ config, assets, selected, guideRefs = [], contracts, refresh, onApproved, onEditing, product,
  stage = 'design', onContinue, onDraft, initialContractId, runs = [], onPending }: {
  config: WorkspaceConfig; assets: Asset[]; selected: string[]; contracts: Contract[]; refresh: () => void;
  onApproved: (contract: Contract) => void; onEditing: (id: string, dirty: boolean) => void;
  product?: Product;
  runs?: Run[];
  onPending?: (pending: boolean) => void;
  guideRefs?: GuideRef[];
  stage?: 'define' | 'design'; onContinue?: () => void; onDraft?: (draft: EditableContract) => void; initialContractId?: string;
}) {
  const { client: workspaceClient, role, project } = useWorkspaceScope();
  const mayEdit = can(role, 'rules');
  const context = product ? { productId: product.id, guidelineId: product.publishedGuidelineId } : {};
  const manualSelected = selected.filter(id => manualAssets(assets).some(asset => asset.id === id));
  const hasPlanningContext = !project || !!product?.publishedGuidelineId;
  const [record, setRecord] = useState<Contract | null>(null);
  const [initialDraft, setInitialDraft] = useState<EditableContract>(() => blankContract(manualSelected));
  const [draft, setDraft] = useState<EditableContract>(initialDraft);
  const [model, setModel] = useState(config.defaultModel);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const briefId = useId();
  const [proposal, setProposal] = useState<Job | null>(null);
  const [proposalActive, setProposalActive] = useState(false);
  const [baselinePending, setBaselinePending] = useState(false);
  const baselineBusy = useRef(false);
  const baselineStatus = useCallback((value: boolean) => {
    baselineBusy.current = value; setBaselinePending(value); onPending?.(value);
  }, [onPending]);
  const operation = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const proposalRevision = useRef(0);
  const pendingProposal = useRef<{ fingerprint: string; payload: Record<string, unknown> } | null>(null);
  const mounted = useRef(true);
  const dirty = JSON.stringify(editable(record || initialDraft)) !== JSON.stringify(editable(draft));
  const [scenarioFilter, setScenarioFilter] = useState<UXState | 'all' | 'unclassified'>('all');
  const opened = useRef('');
  const requestedContract = useRef(initialContractId || '');
  const activeRecord = useRef(record?.id || ''); activeRecord.current = record?.id || '';
  const [editorEpoch, setEditorEpoch] = useState(0);
  const guide = useGuidelinePages(record?.productId || product?.id, record?.guidelineId || product?.publishedGuidelineId,
    record?.ontologyHash || product?.ontologyHash);
  const problems = [...contractProblems(draft), ...requestIssues(draft), ...noticeProblems(draft, guide.pages),
    ...(!guide.ready ? ['게시 지침의 안내 화면을 조회한 뒤 규칙을 승인하세요.'] : [])];
  useEffect(() => { onEditing(record?.id || '', dirty || baselinePending); }, [record?.id, dirty, baselinePending, onEditing]);
  useEffect(() => { onDraft?.(draft); }, [draft, onDraft]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; operation.current?.abort(); }; }, []);
  const change = (next: EditableContract) => {
    if (next.changeRequest && !draft.changeRequest && !record && JSON.stringify(draft.rules) === JSON.stringify(initialDraft.rules))
      next = { ...next, rules: [] };
    revision.current++; setDraft(next); setChecked(false); setNotice('');
  };
  const apply = (contract: Contract) => { setRecord(contract); setDraft(editable(contract)); setChecked(false); revision.current++; };
  const begin = () => { operation.current?.abort(); const controller = new AbortController(); operation.current = controller; setError(''); setBusy(true); return controller; };
  const open = async (id: string, confirmSwitch = true) => {
    if (confirmSwitch && dirty && !confirm('저장하지 않은 규칙 변경을 버리고 다른 규칙을 열까요?')) return;
    revision.current++;
    setEditorEpoch(value => value + 1);
    const controller = begin();
    setProposalActive(false); setProposal(null); pendingProposal.current = null;
    const empty = blankContract(manualSelected);
    setRecord(null); setInitialDraft(empty); setDraft(empty); setChecked(false);
    if (!id) { setBusy(false); return; }
    try {
      const { contract } = await workspaceClient.get<{ contract: Contract }>(`/contracts/${resource(id)}`, controller.signal);
      if (contract.id !== id || (product && contract.productId && contract.productId !== product.id))
        throw new Error('연결된 작업의 기준을 현재 상품에서 확인하지 못했습니다.');
      if (!controller.signal.aborted) { apply(contract); opened.current = id; }
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  useEffect(() => {
    const previous = requestedContract.current, next = initialContractId || '';
    requestedContract.current = next;
    // Workspace owns the dirty-state guard for URL navigation.
    if (next && next !== activeRecord.current) void open(next, false);
    else if (!next && previous) void open('', false);
  }, [initialContractId]);
  const save = async () => {
    if (baselineBusy.current || !mayEdit || (!hasPlanningContext && !draft.changeRequest)) return;
    const controller = begin();
    try {
      const { contract } = record
        ? await workspaceClient.put<{ contract: Contract }>(`/contracts/${resource(record.id)}`, { version: record.version, ...draft, ...context }, controller.signal)
        : await workspaceClient.post<{ contract: Contract }>('/contracts', { ...draft, ...context }, controller.signal);
      if (controller.signal.aborted) return;
      apply(contract); refresh(); setNotice('규칙을 저장했습니다. 이 버전의 내용을 확인한 뒤 승인하세요.');
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const approve = async () => {
    if (baselineBusy.current || !mayEdit || !hasPlanningContext || !record || dirty || !checked || problems.length) return;
    const controller = begin();
    try {
      const { contract } = await workspaceClient.post<{ contract: Contract }>(`/contracts/${resource(record.id)}/approve`, { version: record.version }, controller.signal);
      if (controller.signal.aborted) return;
      apply(contract); refresh(); onApproved(contract); setNotice(`버전 ${contract.version}의 규칙을 승인했습니다. 시안 생성에서 사용할 수 있습니다.`);
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const propose = async () => {
    if (baselineBusy.current || !mayEdit || !hasPlanningContext || (!manualSelected.length && !product?.publishedGuidelineId && !draft.brief.trim()) || proposalActive || !config.models.some(option => option.id === model)) return;
    if (dirty && !confirm('파일과 화면 설명으로 새 규칙을 제안받을까요? 현재 미저장 내용은 결과를 열 때 교체됩니다.')) return;
    const controller = begin();
    proposalRevision.current = revision.current; setProposalActive(true);
    const fields = { assetIds: manualSelected, brief: draft.brief, model, ...context, ...(guideRefs.length ? { guideRefs } : {}),
      ...(draft.changeRequest ? { changeRequest: draft.changeRequest } : {}),
      ...(draft.requiredStates?.length ? { requiredStates: draft.requiredStates } : {}) };
    const fingerprint = JSON.stringify(fields);
    const payload = pendingProposal.current?.fingerprint === fingerprint ? pendingProposal.current.payload :
      { ...fields, requestId: crypto.randomUUID() };
    pendingProposal.current = { fingerprint, payload };
    try {
      const { job } = await workspaceClient.post<{ job: Job }>('/contracts/propose', payload, controller.signal);
      if (!controller.signal.aborted) { pendingProposal.current = null; setProposal(job); }
    } catch (reason) { if (!controller.signal.aborted) { setError(messageOf(reason)); setProposalActive(false); } }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const proposed = async (job: Job) => {
    if (!mounted.current) return;
    setProposalActive(false); refresh();
    const id = job.result?.contractId;
    if (!id || revision.current !== proposalRevision.current) {
      setNotice('규칙 제안이 완료되었습니다. 현재 편집 내용은 유지됩니다. 저장한 규칙 목록에서 결과를 선택하세요.'); return;
    }
    const controller = begin();
    try {
      const { contract } = await workspaceClient.get<{ contract: Contract }>(`/contracts/${resource(id)}`, controller.signal);
      if (!controller.signal.aborted && revision.current === proposalRevision.current) { apply(contract); setNotice('AI 제안은 아직 승인되지 않았습니다. 근거와 단계를 확인하세요.'); }
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const updateRule = (index: number, rule: Rule) => change({ ...draft, rules: draft.rules.map((item, n) => n === index ? rule : item) });
  const addNotice = (page: GuidelinePage) => {
    if (!mayEdit || busy || draft.rules.length >= 20) return;
    try {
      const assetId = record ? record.guidelineAssetId : product?.guideAssetId;
      change({ ...draft, assetIds: [...new Set([...draft.assetIds, ...(assetId ? [assetId] : [])])],
        rules: [...draft.rules, noticeRule(page, assetId)] });
    } catch (reason) { setError(messageOf(reason)); }
  };
  if (stage === 'define') return <section className="ws-section ws-definition">
    <div className="ws-section-heading"><div><h2>누가, 어떤 일을 마쳐야 하나요?</h2>
      <p>사용자의 목적과 완료 조건을 먼저 정하고, 필요한 화면·상태를 함께 설계합니다.</p></div>
      <label className="ws-field">이어서 설계할 작업<select aria-label="이어서 설계할 작업" value={record?.id || ''} disabled={busy}
        onChange={event => void open(event.target.value)}><option value="">새 UX 설계</option>{contracts.map(contract =>
          <option key={contract.id} value={contract.id}>{contract.title} · v{contract.version} · {stateLabel(contract.status)}</option>)}</select></label>
    </div>
    {project && !product?.publishedGuidelineId && <Notice>이 프로젝트의 상품 지침을 먼저 선택·게시하세요. 공통 가이드와 상품 조건은 함께 적용됩니다.</Notice>}
    {!mayEdit && <Notice>현재 역할에서는 확정된 업무와 상태를 조회합니다. 정의·규칙 편집은 기획·디자인·관리자가 수행합니다.</Notice>}
    {error && <Notice error>{error}{initialContractId && !record && <button onClick={() => void open(initialContractId)}>연결된 작업 다시 조회</button>}</Notice>}
    <fieldset disabled={busy || !mayEdit}>
      <div className="ws-definition-starters" aria-label="흐름 시작점">{DESIGN_STARTERS.map(starter =>
        <button key={starter.label} onClick={() => change({ ...draft, brief: draft.brief.trim() ? draft.brief : starter.brief,
          requiredStates: [...starter.states] })}>{starter.label}</button>)}</div>
      <label className="ws-field">설계 작업 이름<input maxLength={180} value={draft.title}
        onChange={event => change({ ...draft, title: event.target.value })} /></label>
      <label className="ws-field">사용자 목적·완료 조건<textarea rows={4} maxLength={4000} value={draft.brief}
        placeholder="누가 사용하는지, 어떤 일을 마쳐야 하는지, 완료 후 무엇을 확인할 수 있어야 하는지 적어 주세요."
        onChange={event => change({ ...draft, brief: event.target.value })} /></label>
      <ChangeRequestPanel key={`${record?.id || 'new'}:${editorEpoch}`} draft={draft} runs={runs} disabled={busy || proposalActive || !mayEdit} stage="define" onChange={change} onPending={baselineStatus} />
      <details open={!draft.changeRequest}><summary>공통 상태 체크리스트</summary><StatePlan draft={draft} disabled={busy || !mayEdit}
        onChange={requiredStates => change({ ...draft, requiredStates })} /></details>
    </fieldset>
    <div className="ws-stage-next"><p>{record ? `저장된 규칙 v${record.version}${dirty ? ' · 미저장 변경 있음' : ''}` : '흐름·상태 설계 단계에서 규칙과 함께 저장합니다.'}</p>
      <button disabled={baselinePending || !mayEdit || (!hasPlanningContext && !draft.changeRequest) || busy || !dirty} onClick={() => void save()}>업무 요청 저장</button>
      <button className="ws-primary" disabled={baselinePending} onClick={onContinue}>기준·자산 선택으로</button></div>
  </section>;
  return <section className="ws-section ws-rules-panel">
    {!mayEdit && <Notice>현재 역할은 규칙을 조회할 수 있습니다. 규칙 편집은 기획·디자인·관리자에게 요청하세요.</Notice>}
    {project && <Notice>{product ? `${product.title} · ${product.publishedGuidelineId ? `게시 지침 ${product.publishedRevision}차 기준` : '게시한 상품 지침 없음'}` : '업무 정의에서 상품 지침을 선택하면 설계 기준과 연결됩니다.'}</Notice>}
    <div className="ws-section-heading"><div><h2>만들 화면과 확인 기준</h2><p>가이드의 근거와 AI가 추정한 내용을 구분합니다. 확인한 규칙의 버전만 생성에 사용합니다.</p></div>
      <div className="ws-actions"><label className="ws-field">저장한 규칙<select value={record?.id || ''} disabled={busy} onChange={event => void open(event.target.value)}>
        <option value="">새 규칙 작성</option>{contracts.map(contract => <option value={contract.id} key={contract.id}>
          {contract.title} · v{contract.version} · {stateLabel(contract.status)}</option>)}</select></label>
        {record && <button disabled={busy} onClick={() => void open(record.id)}>저장된 버전 다시 불러오기</button>}</div></div>
    <div className="ws-rule-intro">
      <div className="ws-field"><label htmlFor={briefId}>만들 화면 설명</label><textarea id={briefId} maxLength={4000} rows={3} value={draft.brief} disabled={busy || !mayEdit}
        placeholder="예: 납입금액을 입력하고, 동의 후 확인 화면으로 이동하는 가입 화면"
        onChange={event => change({ ...draft, brief: event.target.value })} /></div>
      <div><ModelPicker models={config.models} value={model} onChange={setModel} disabled={busy || proposalActive} />
        <button className="ws-primary" onClick={() => void propose()} disabled={baselinePending || !mayEdit || !hasPlanningContext || busy || proposalActive || (!manualSelected.length && !product?.publishedGuidelineId && !draft.brief.trim()) || !config.models.some(option => option.id === model)}>
          선택한 파일로 규칙 제안받기</button><p className="ws-muted">기준·자산에서 선택한 {manualSelected.length}개를 사용합니다.
            {project && (product?.publishedGuidelineId ? ` ${product.title}의 현재 게시 지침이 자동으로 포함됩니다.` : ' 먼저 업무 정의에서 게시 지침이 있는 상품을 선택하세요.')} JSON 작성은 필요하지 않습니다.</p></div>
    </div>
    {guideRefs.length > 0 && <Notice>고객 가이드에서 선택한 {guideRefs.length}페이지를 다음 AI 제안에 적용합니다.
      저장된 규칙에는 해당 규칙의 원본·페이지 기준이 유지됩니다.</Notice>}
    {proposal && <JobProgress job={proposal} label="가이드에서 규칙 제안" onComplete={job => void proposed(job)} onFailure={() => setProposalActive(false)} />}
    {guide.error && <Notice error>{guide.error} <button onClick={guide.reload}>지침 화면 다시 조회</button></Notice>}
    {error && <Notice error>{error}{initialContractId && !record && <button onClick={() => void open(initialContractId)}>연결된 작업 다시 조회</button>}</Notice>}{notice && <Notice>{notice}</Notice>}
    {draft.changeRequest ? <ChangeRequestPanel key={`${record?.id || 'new'}:${editorEpoch}`} draft={draft} runs={runs} stage="design" disabled={busy || proposalActive || !mayEdit} onChange={change} onPending={baselineStatus} /> :
      <StatePlan draft={draft} disabled={busy || !mayEdit}
        onChange={requiredStates => change({ ...draft, requiredStates })} onInspect={setScenarioFilter} />}
    <fieldset disabled={busy || !mayEdit} className="ws-editable">
      {guide.pages.length > 0 && <section className="ws-guideline-pages"><h3>게시 지침의 안내 화면</h3>
        <p>화면 이름으로 검사 대상을 선택할 수 있습니다. 필수 안내는 해당 화면에서 전체 문구를 확인해야 합니다.</p>
        {guide.pages.map(page => <article key={page.pageId} className="ws-planning-card"><strong>{page.title}</strong>
          <p>{page.required === true ? '필수 안내' : page.required === false ? '선택 안내' : '필수 여부 미확인'}</p>
          <details><summary>안내 원문·화면 연결</summary><p className="ws-notice-copy">{page.content}</p>
            <p>화면 식별자: <code>{page.pageId}</code></p></details>
          <button disabled={draft.rules.length >= 20} onClick={() => addNotice(page)}>{page.title} 전체 안내문 검사 추가</button>
        </article>)}
      </section>}
      <div className="ws-contract-top">
        <label className="ws-field">규칙 묶음 이름<input value={draft.title} maxLength={180} onChange={event => change({ ...draft, title: event.target.value })} /></label>
        <label className="ws-field">화면 너비<input type="number" min={1} value={draft.viewport.width} onChange={event => change({ ...draft, viewport: { ...draft.viewport, width: Number(event.target.value) } })} /></label>
        <label className="ws-field">화면 높이<input type="number" min={1} value={draft.viewport.height} onChange={event => change({ ...draft, viewport: { ...draft.viewport, height: Number(event.target.value) } })} /></label>
      </div>
      <div className="ws-rule-files"><span>규칙에 연결된 파일: {draft.assetIds.length ? draft.assetIds.map(id => assets.find(asset => asset.id === id)?.name || '보관된 파일').join(' · ') : '없음'}</span>
        <button type="button" onClick={() => change({ ...applyManualAssets(draft, assets, manualSelected),
          guideRefs: guideRefs.filter(ref => manualSelected.includes(ref.assetId)) })}>현재 선택한 파일 적용</button></div>
      {!!draft.guideRefs?.length && <details><summary>규칙에 고정된 고객 가이드 {draft.guideRefs.length}페이지</summary>
        {draft.guideRefs.map(ref => <p key={`${ref.assetId}:${ref.sourceId}:${ref.page}`}>
          {assets.find(asset => asset.id === ref.assetId)?.guidelineSources?.find(source => source.id === ref.sourceId)?.name || ref.sourceId}
          {' · '}{ref.page}페이지 · 원본 {ref.sourceSha256.slice(0, 12)}</p>)}</details>}
      <label className="ws-field ws-state-filter">상태별 규칙<select aria-label="상태별 규칙" value={scenarioFilter}
        onChange={event => setScenarioFilter(event.target.value as typeof scenarioFilter)}><option value="all">모든 상태</option>
        <option value="unclassified">아직 분류하지 않은 규칙</option>{Object.entries(UX_STATES).map(([state, info]) =>
          <option key={state} value={state}>{info.label}</option>)}</select></label>
      {!draft.rules.some(rule => scenarioFilter === 'all' || (scenarioFilter === 'unclassified' ? !rule.scenario : rule.scenario === scenarioFilter)) &&
        <Notice>이 상태에 연결된 규칙이 없습니다. 규칙을 추가하거나 AI에게 상태별 규칙을 제안받으세요.</Notice>}
      <div className="ws-rule-cards">{draft.rules.map((rule, index) => scenarioFilter !== 'all' &&
        (scenarioFilter === 'unclassified' ? !!rule.scenario : rule.scenario !== scenarioFilter) ? null : <RuleCard key={rule.id} rule={rule} index={index} assets={assets.filter(asset => draft.assetIds.includes(asset.id))}
        request={draft.changeRequest}
        guideRefs={draft.guideRefs || []}
        pages={guide.pages}
        knownSteps={draft.rules.flatMap(item => item.steps)} onChange={next => updateRule(index, next)}
        onRemove={() => change({ ...draft, rules: draft.rules.filter((_, n) => n !== index) })} />)}</div>
      <button onClick={() => change({ ...draft, rules: [...draft.rules, { ...newRule(),
        ...(scenarioFilter !== 'all' && scenarioFilter !== 'unclassified' ? { scenario: scenarioFilter, title: `${UX_STATES[scenarioFilter].label} 상태 확인` } : {}) }] })}
        disabled={draft.rules.length >= 20}>규칙 추가</button>
      <BindingsEditor draft={draft} onChange={change} />
      <div className="ws-unresolved"><h3>확인이 필요한 내용</h3>
        <p>해결하지 않은 항목이 있으면 승인할 수 없습니다. 근거를 확인하고 규칙에 반영한 뒤 ‘해결됨’을 누르세요.</p>
        {draft.unresolved.map((value, index) => <div className="ws-actions" key={index}>
          <input aria-label={`확인 필요 ${index + 1}`} value={value} onChange={event => change({ ...draft, unresolved: draft.unresolved.map((v, n) => n === index ? event.target.value : v) })} />
          <button onClick={() => change({ ...draft, unresolved: draft.unresolved.filter((_, n) => n !== index) })}>해결됨</button></div>)}
        <button onClick={() => change({ ...draft, unresolved: [...draft.unresolved, '확인할 업무 조건'] })}>확인할 내용 추가</button>
      </div>
    </fieldset>
    {problems.length > 0 && <Notice><ul>{problems.map(problem => <li key={problem}>{problem}</li>)}</ul></Notice>}
    <div className="ws-approval">
      <div><strong>{record ? `규칙 버전 ${record.version} · ${dirty ? '미저장 변경 있음' : stateLabel(record.status)}` : '아직 저장하지 않은 규칙'}</strong>
        <p>규칙을 수정하면 이전 승인은 새 버전에 적용되지 않습니다.</p></div>
      <button className="ws-primary" disabled={baselinePending || !mayEdit || !hasPlanningContext || busy || !dirty} onClick={() => void save()}>규칙 저장</button>
      <label className="ws-check"><input type="checkbox" checked={checked} disabled={!mayEdit || !hasPlanningContext || busy || dirty || !record || !!problems.length}
        onChange={event => setChecked(event.target.checked)} />이 버전의 근거와 모든 확인 단계를 검토했습니다</label>
      <button disabled={baselinePending || !mayEdit || !hasPlanningContext || busy || dirty || !record || !checked || !!problems.length || record.status === 'approved'} onClick={() => void approve()}>
        {record ? `버전 ${record.version} 규칙 승인` : '저장 후 규칙 승인'}</button>
      {record?.status === 'approved' && !dirty && <button className="ws-primary" onClick={onContinue}>승인 기준으로 시안·검수</button>}
    </div>
  </section>;
}

function RuleCard({ rule, index, assets, knownSteps, pages, guideRefs, onChange, onRemove, request }: {
  request?: EditableContract['changeRequest'];
  rule: Rule; index: number; assets: Asset[]; knownSteps: Step[]; onChange: (rule: Rule) => void; onRemove: () => void;
  pages: GuidelinePage[];
  guideRefs: GuideRef[];
}) {
  const updateStep = (n: number, step: Step) => onChange({ ...rule, steps: rule.steps.map((item, position) => position === n ? step : item) });
  return <article className="ws-rule-card">
    <div className="ws-section-heading"><label className="ws-field">규칙 {index + 1}<input value={rule.title} onChange={event => onChange({ ...rule, title: event.target.value })} /></label>
      <label className="ws-check"><input type="checkbox" checked={rule.required} onChange={event => onChange({ ...rule, required: event.target.checked })} />필수 확인</label>
      <button onClick={onRemove} aria-label={`규칙 ${index + 1} 삭제`}>규칙 삭제</button></div>
    {request && <div className="ws-request-fields">
      <label className="ws-field">검증 대상 화면<select value={rule.screenId || ''} onChange={event => onChange({ ...rule, screenId: event.target.value || undefined })}>
        <option value="">화면 연결 없음</option>{request.screens.map(screen => <option key={screen.id} value={screen.id}>{screen.title}</option>)}</select></label>
      <label className="ws-field">검증할 화면 이동<select value={rule.transitionId || ''} onChange={event => onChange({ ...rule, transitionId: event.target.value || undefined })}>
        <option value="">이동 연결 없음</option>{request.transitions.map(link => <option key={link.id} value={link.id}>
          {request.screens.find(screen => screen.id === link.from)?.title} → {request.screens.find(screen => screen.id === link.to)?.title}</option>)}</select></label>
    </div>}
    <label className="ws-field ws-state-filter">검증할 UX 상태<select aria-label={`규칙 ${index + 1}의 UX 상태`}
      value={rule.scenario || ''} onChange={event => {
        const next = { ...rule };
        if (event.target.value) next.scenario = event.target.value as UXState;
        else delete next.scenario;
        onChange(next);
      }}><option value="">상태 미분류</option>{Object.entries(UX_STATES).map(([state, info]) =>
        <option key={state} value={state}>{info.label}</option>)}</select></label>
    {request && <div className="ws-rule-readable">
      <p>{rule.steps.map(step => `${step.targetLabel}: ${ACTIONS[step.action]}${step.value === undefined ? '' :
        ` ${typeof step.value === 'boolean' ? step.value ? '예' : '아니요' : step.value}`}`).join(' → ')}</p>
      <p>{rule.source.kind === 'explicit' ? '선택한 원문에 근거' : rule.source.kind === 'manual' ? '담당자 정의 · 근거 확인 필요' : 'AI 제안 · 근거 확인 필요'}
        {rule.source.page ? ` · ${rule.source.page}페이지` : ''}</p>
    </div>}
    <details className="ws-rule-details" open={!request}><summary>근거와 검사 단계 편집</summary><div className="ws-source">
      <label className="ws-field">근거 종류<select value={rule.source.kind} onChange={event => onChange({ ...rule, source: { ...rule.source, kind: event.target.value as Rule['source']['kind'] } })}>
        <option value="explicit">파일에 명시된 내용</option><option value="inferred">AI가 추정한 내용</option><option value="manual">담당자가 직접 정한 내용</option></select></label>
      <label className="ws-field">근거 파일<select value={rule.source.assetId || ''} onChange={event => onChange({ ...rule,
        source: { ...rule.source, assetId: event.target.value || undefined, sourceId: undefined, page: undefined } })}>
        <option value="">파일 연결 없음</option>{assets.map(asset => <option value={asset.id} key={asset.id}>{asset.name}</option>)}</select></label>
      {guideRefs.some(ref => ref.assetId === rule.source.assetId) && <label className="ws-field">가이드 원본·페이지<select
        value={rule.source.sourceId && rule.source.page ? `${rule.source.sourceId}:${rule.source.page}` : ''}
        onChange={event => { const ref = guideRefs.find(ref => ref.assetId === rule.source.assetId && `${ref.sourceId}:${ref.page}` === event.target.value);
          onChange({ ...rule, source: { ...rule.source, sourceId: ref?.sourceId, page: ref?.page } }); }}>
        <option value="">근거 페이지 선택</option>{guideRefs.filter(ref => ref.assetId === rule.source.assetId).map(ref =>
          <option key={`${ref.sourceId}:${ref.page}`} value={`${ref.sourceId}:${ref.page}`}>
            {assets.find(asset => asset.id === ref.assetId)?.guidelineSources?.find(source => source.id === ref.sourceId)?.name || ref.sourceId} · {ref.page}페이지
          </option>)}</select></label>}
      <div className="ws-field"><label htmlFor={`ws-source-${rule.id}`}>원문 또는 보완 설명</label><textarea id={`ws-source-${rule.id}`} rows={2}
        value={rule.source.quote || ''} onChange={event => onChange({ ...rule, source: { ...rule.source, quote: event.target.value } })} /></div>
    </div>
    <ol className="ws-steps">{rule.steps.map((step, n) => <li className={`ws-step${step.action === 'expectStyle' ? ' ws-step-style' : ''}`} key={n}>
      <span className="ws-step-number">{n + 1}</span>
      <label className="ws-field">동작·확인<select value={step.action} onChange={event => {
        const action = event.target.value as Action;
        updateStep(n, { ...newStep(action), target: step.target, targetLabel: step.targetLabel });
      }}>{Object.entries(ACTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="ws-field">화면 요소<input value={step.targetLabel} list={`targets-${rule.id}`} onChange={event => {
        const label = event.target.value;
        updateStep(n, { ...step, targetLabel: label, target: knownSteps.find(item => item.targetLabel === label)?.target || step.target });
      }} /></label>
      {step.action === 'expectStyle' && <label className="ws-field">스타일 속성<select value={step.property || ''} onChange={event =>
        updateStep(n, { ...step, property: event.target.value as StyleProperty })}>
        {!step.property && <option value="">속성을 선택하세요</option>}
        {Object.entries(STYLE_PROPERTIES).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
      </select></label>}
      {step.action !== 'click' && <label className="ws-field">{step.action.startsWith('expect') ? '기대 결과' : '입력 내용'}
        {BOOLEAN_ACTIONS.includes(step.action) ? <select value={String(step.value)} onChange={event => updateStep(n, { ...step, value: event.target.value === 'true' })}>
          <option value="true">예</option><option value="false">아니요</option></select>
          : step.action === 'press' ? <select value={String(step.value)} onChange={event => updateStep(n, { ...step, value: event.target.value })}>
            {KEYS.map(key => <option key={key} value={key}>{({ Enter: '엔터', Tab: '탭', Escape: '닫기 키', Space: '공백', ArrowUp: '위 화살표', ArrowDown: '아래 화살표', ArrowLeft: '왼쪽 화살표', ArrowRight: '오른쪽 화살표' } as Record<string, string>)[key]}</option>)}</select>
            : step.action === 'expectText' ? <textarea aria-label="기대 결과" rows={3} value={String(step.value ?? '')}
                onChange={event => updateStep(n, { ...step, value: event.target.value })} />
            : <input value={String(step.value ?? '')} placeholder={step.action === 'expectStyle' ? '예: #008485 또는 16px' : undefined}
                onChange={event => updateStep(n, { ...step, value: event.target.value })} />}
      </label>}
      {step.action === 'expectStyle' && <p className="ws-step-style-help">선택한 요소에 실제 적용된 스타일을 확인합니다. 색상은 #008485처럼, 크기·여백은 16px처럼 입력하세요.</p>}
      {step.action === 'expectText' && <label className="ws-field ws-step-match">문구 비교<select value={step.match || 'contains'} onChange={event => updateStep(n, { ...step, match: event.target.value as Step['match'] })}>
        <option value="contains">포함하면 일치</option><option value="equals">전체가 같아야 일치</option></select></label>}
      {step.action === 'expectText' && <div className="ws-step-guide">
        {pages.length > 0 && <label className="ws-field">게시 지침의 안내 화면<select aria-label="게시 지침의 안내 화면"
          value={pages.some(page => page.pageId === step.target) ? step.target : ''} onChange={event => {
            const page = pages.find(page => page.pageId === event.target.value);
            if (page) updateStep(n, { ...step, target: page.pageId, targetLabel: page.title });
          }}><option value="">다른 화면 요소</option>{pages.map(page => <option key={page.pageId} value={page.pageId}>{page.title}</option>)}</select></label>}
        <label className="ws-check"><input type="checkbox" checked={step.normalizeWhitespace === true}
          onChange={event => updateStep(n, { ...step, normalizeWhitespace: event.target.checked })} />
          줄바꿈·연속 공백을 하나의 공백으로 비교</label>
      </div>}
      <div className="ws-actions"><button disabled={n === 0} aria-label={`단계 ${n + 1} 위로`} onClick={() => {
        const steps = [...rule.steps]; [steps[n - 1], steps[n]] = [steps[n], steps[n - 1]]; onChange({ ...rule, steps });
      }}>위로</button><button aria-label={`단계 ${n + 1} 삭제`} onClick={() => onChange({ ...rule, steps: rule.steps.filter((_, p) => p !== n) })}>삭제</button></div>
      <details className="ws-step-advanced"><summary>고급 설정 · 요소 식별자</summary><label className="ws-field">요소 식별자
        <input value={step.target} onChange={event => updateStep(n, { ...step, target: event.target.value })} /></label></details>
    </li>)}</ol>
    <datalist id={`targets-${rule.id}`}>{[...new Set(knownSteps.map(step => step.targetLabel))].map(label => <option key={label} value={label} />)}</datalist>
    <button disabled={rule.steps.length >= 20} onClick={() => onChange({ ...rule, steps: [...rule.steps, newStep('click')] })}>확인 단계 추가</button>
    </details>
  </article>;
}
