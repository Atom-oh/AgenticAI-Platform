import { useEffect, useId, useRef, useState } from 'react';
import { aborted, messageOf, resource, workspaceClient } from './client';
import { ACTIONS, BOOLEAN_ACTIONS, KEYS, STYLE_PROPERTIES, blankContract, contractProblems, editable, newRule, newStep, stateLabel } from './rules';
import { JobProgress, ModelPicker, Notice } from './shared';
import BindingsEditor from './BindingsEditor';
import type { Action, Asset, Contract, EditableContract, Job, Rule, Step, StyleProperty, WorkspaceConfig } from './types';

export default function RulesPanel({ config, assets, selected, contracts, refresh, onApproved, onEditing }: {
  config: WorkspaceConfig; assets: Asset[]; selected: string[]; contracts: Contract[]; refresh: () => void;
  onApproved: (contract: Contract) => void; onEditing: (id: string, dirty: boolean) => void;
}) {
  const [record, setRecord] = useState<Contract | null>(null);
  const [draft, setDraft] = useState<EditableContract>(() => blankContract(selected));
  const [model, setModel] = useState(config.defaultModel);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const briefId = useId();
  const [proposal, setProposal] = useState<Job | null>(null);
  const [proposalActive, setProposalActive] = useState(false);
  const operation = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const proposalRevision = useRef(0);
  const pendingProposal = useRef<{ fingerprint: string; payload: Record<string, unknown> } | null>(null);
  const mounted = useRef(true);
  const dirty = record ? JSON.stringify(editable(record)) !== JSON.stringify(draft) : true;
  const problems = contractProblems(draft);
  useEffect(() => { onEditing(record?.id || '', dirty); }, [record?.id, dirty, onEditing]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; operation.current?.abort(); }; }, []);
  const change = (next: EditableContract) => { revision.current++; setDraft(next); setChecked(false); setNotice(''); };
  const apply = (contract: Contract) => { setRecord(contract); setDraft(editable(contract)); setChecked(false); revision.current++; };
  const begin = () => { operation.current?.abort(); const controller = new AbortController(); operation.current = controller; setError(''); setBusy(true); return controller; };
  const open = async (id: string) => {
    if (dirty && !confirm('저장하지 않은 규칙 변경을 버리고 다른 규칙을 열까요?')) return;
    revision.current++;
    const controller = begin();
    if (!id) { setRecord(null); setDraft(blankContract(selected)); setChecked(false); setBusy(false); return; }
    try {
      const { contract } = await workspaceClient.get<{ contract: Contract }>(`/contracts/${resource(id)}`, controller.signal);
      if (!controller.signal.aborted) apply(contract);
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const save = async () => {
    const controller = begin();
    try {
      const { contract } = record
        ? await workspaceClient.put<{ contract: Contract }>(`/contracts/${resource(record.id)}`, { version: record.version, ...draft }, controller.signal)
        : await workspaceClient.post<{ contract: Contract }>('/contracts', draft, controller.signal);
      if (controller.signal.aborted) return;
      apply(contract); refresh(); setNotice('규칙을 저장했습니다. 이 버전의 내용을 확인한 뒤 승인하세요.');
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const approve = async () => {
    if (!record || dirty || !checked || problems.length) return;
    const controller = begin();
    try {
      const { contract } = await workspaceClient.post<{ contract: Contract }>(`/contracts/${resource(record.id)}/approve`, { version: record.version }, controller.signal);
      if (controller.signal.aborted) return;
      apply(contract); refresh(); onApproved(contract); setNotice(`버전 ${contract.version}의 규칙을 승인했습니다. 시안 생성에서 사용할 수 있습니다.`);
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const propose = async () => {
    if (!selected.length || proposalActive || !config.models.some(option => option.id === model)) return;
    if (dirty && !confirm('파일과 화면 설명으로 새 규칙을 제안받을까요? 현재 미저장 내용은 결과를 열 때 교체됩니다.')) return;
    const controller = begin();
    proposalRevision.current = revision.current; setProposalActive(true);
    const fields = { assetIds: selected, brief: draft.brief, model };
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
  return <section className="ws-section ws-rules-panel">
    <div className="ws-section-heading"><div><h2>만들 화면과 확인 기준</h2><p>가이드의 근거와 AI가 추정한 내용을 구분합니다. 확인한 규칙의 버전만 생성에 사용합니다.</p></div>
      <div className="ws-actions"><label className="ws-field">저장한 규칙<select value={record?.id || ''} disabled={busy} onChange={event => void open(event.target.value)}>
        <option value="">새 규칙 작성</option>{contracts.map(contract => <option value={contract.id} key={contract.id}>
          {contract.title} · v{contract.version} · {stateLabel(contract.status)}</option>)}</select></label>
        {record && <button disabled={busy} onClick={() => void open(record.id)}>저장된 버전 다시 불러오기</button>}</div></div>
    <div className="ws-rule-intro">
      <div className="ws-field"><label htmlFor={briefId}>만들 화면 설명</label><textarea id={briefId} maxLength={4000} rows={3} value={draft.brief} disabled={busy}
        placeholder="예: 납입금액을 입력하고, 동의 후 확인 화면으로 이동하는 가입 화면"
        onChange={event => change({ ...draft, brief: event.target.value })} /></div>
      <div><ModelPicker models={config.models} value={model} onChange={setModel} disabled={busy || proposalActive} />
        <button className="ws-primary" onClick={() => void propose()} disabled={busy || proposalActive || !selected.length || !config.models.some(option => option.id === model)}>
          선택한 파일로 규칙 제안받기</button><p className="ws-muted">파일 탭에서 선택한 {selected.length}개를 사용합니다. JSON 작성은 필요하지 않습니다.</p></div>
    </div>
    {proposal && <JobProgress job={proposal} label="가이드에서 규칙 제안" onComplete={job => void proposed(job)} onFailure={() => setProposalActive(false)} />}
    {error && <Notice error>{error}</Notice>}{notice && <Notice>{notice}</Notice>}
    <fieldset disabled={busy} className="ws-editable">
      <div className="ws-contract-top">
        <label className="ws-field">규칙 묶음 이름<input value={draft.title} maxLength={180} onChange={event => change({ ...draft, title: event.target.value })} /></label>
        <label className="ws-field">화면 너비<input type="number" min={1} value={draft.viewport.width} onChange={event => change({ ...draft, viewport: { ...draft.viewport, width: Number(event.target.value) } })} /></label>
        <label className="ws-field">화면 높이<input type="number" min={1} value={draft.viewport.height} onChange={event => change({ ...draft, viewport: { ...draft.viewport, height: Number(event.target.value) } })} /></label>
      </div>
      <div className="ws-rule-files"><span>규칙에 연결된 파일: {draft.assetIds.length ? draft.assetIds.map(id => assets.find(asset => asset.id === id)?.name || '보관된 파일').join(' · ') : '없음'}</span>
        <button type="button" onClick={() => change({ ...draft, assetIds: [...selected] })}>현재 선택한 파일 적용</button></div>
      <div className="ws-rule-cards">{draft.rules.map((rule, index) => <RuleCard key={rule.id} rule={rule} index={index} assets={assets.filter(asset => draft.assetIds.includes(asset.id))}
        knownSteps={draft.rules.flatMap(item => item.steps)} onChange={next => updateRule(index, next)}
        onRemove={() => change({ ...draft, rules: draft.rules.filter((_, n) => n !== index) })} />)}</div>
      <button onClick={() => change({ ...draft, rules: [...draft.rules, newRule()] })} disabled={draft.rules.length >= 20}>규칙 추가</button>
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
      <button className="ws-primary" disabled={busy || !dirty} onClick={() => void save()}>규칙 저장</button>
      <label className="ws-check"><input type="checkbox" checked={checked} disabled={busy || dirty || !record || !!problems.length}
        onChange={event => setChecked(event.target.checked)} />이 버전의 근거와 모든 확인 단계를 검토했습니다</label>
      <button disabled={busy || dirty || !record || !checked || !!problems.length || record.status === 'approved'} onClick={() => void approve()}>
        {record ? `버전 ${record.version} 규칙 승인` : '저장 후 규칙 승인'}</button>
    </div>
  </section>;
}

function RuleCard({ rule, index, assets, knownSteps, onChange, onRemove }: {
  rule: Rule; index: number; assets: Asset[]; knownSteps: Step[]; onChange: (rule: Rule) => void; onRemove: () => void;
}) {
  const updateStep = (n: number, step: Step) => onChange({ ...rule, steps: rule.steps.map((item, position) => position === n ? step : item) });
  return <article className="ws-rule-card">
    <div className="ws-section-heading"><label className="ws-field">규칙 {index + 1}<input value={rule.title} onChange={event => onChange({ ...rule, title: event.target.value })} /></label>
      <label className="ws-check"><input type="checkbox" checked={rule.required} onChange={event => onChange({ ...rule, required: event.target.checked })} />필수 확인</label>
      <button onClick={onRemove} aria-label={`규칙 ${index + 1} 삭제`}>규칙 삭제</button></div>
    <div className="ws-source">
      <label className="ws-field">근거 종류<select value={rule.source.kind} onChange={event => onChange({ ...rule, source: { ...rule.source, kind: event.target.value as Rule['source']['kind'] } })}>
        <option value="explicit">파일에 명시된 내용</option><option value="inferred">AI가 추정한 내용</option><option value="manual">담당자가 직접 정한 내용</option></select></label>
      <label className="ws-field">근거 파일<select value={rule.source.assetId || ''} onChange={event => onChange({ ...rule, source: { ...rule.source, assetId: event.target.value || undefined } })}>
        <option value="">파일 연결 없음</option>{assets.map(asset => <option value={asset.id} key={asset.id}>{asset.name}</option>)}</select></label>
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
            : <input value={String(step.value ?? '')} placeholder={step.action === 'expectStyle' ? '예: #008485 또는 16px' : undefined}
                onChange={event => updateStep(n, { ...step, value: event.target.value })} />}
      </label>}
      {step.action === 'expectStyle' && <p className="ws-step-style-help">선택한 요소에 실제 적용된 스타일을 확인합니다. 색상은 #008485처럼, 크기·여백은 16px처럼 입력하세요.</p>}
      {step.action === 'expectText' && <label className="ws-field ws-step-match">문구 비교<select value={step.match || 'contains'} onChange={event => updateStep(n, { ...step, match: event.target.value as Step['match'] })}>
        <option value="contains">포함하면 일치</option><option value="equals">전체가 같아야 일치</option></select></label>}
      <div className="ws-actions"><button disabled={n === 0} aria-label={`단계 ${n + 1} 위로`} onClick={() => {
        const steps = [...rule.steps]; [steps[n - 1], steps[n]] = [steps[n], steps[n - 1]]; onChange({ ...rule, steps });
      }}>위로</button><button aria-label={`단계 ${n + 1} 삭제`} onClick={() => onChange({ ...rule, steps: rule.steps.filter((_, p) => p !== n) })}>삭제</button></div>
      <details className="ws-step-advanced"><summary>고급 설정 · 요소 식별자</summary><label className="ws-field">요소 식별자
        <input value={step.target} onChange={event => updateStep(n, { ...step, target: event.target.value })} /></label></details>
    </li>)}</ol>
    <datalist id={`targets-${rule.id}`}>{[...new Set(knownSteps.map(step => step.targetLabel))].map(label => <option key={label} value={label} />)}</datalist>
    <button disabled={rule.steps.length >= 20} onClick={() => onChange({ ...rule, steps: [...rule.steps, newStep('click')] })}>확인 단계 추가</button>
  </article>;
}
