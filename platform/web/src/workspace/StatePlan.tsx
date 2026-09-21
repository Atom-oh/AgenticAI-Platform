import type { EditableContract, UXState } from './types';
import { stateRules, UX_STATES } from './workflow';

export default function StatePlan({ draft, disabled, onChange, onInspect }: {
  draft: EditableContract; disabled?: boolean; onChange: (states: UXState[]) => void; onInspect?: (state: UXState) => void;
}) {
  const required = draft.requiredStates || [];
  return <section className="ws-state-plan" aria-label="UX 상태 설계">
    <div className="ws-section-heading"><div><h3>이번 흐름에서 다룰 상태</h3>
      <p>정상 경로와 함께, 오류·복귀·이탈 후의 행동을 정하세요. 선택한 상태마다 필수 검증 규칙이 필요합니다.</p></div>
      <span>{required.length}개 상태 선택</span></div>
    <div className="ws-state-grid">{(Object.entries(UX_STATES) as [UXState, typeof UX_STATES[UXState]][]).map(([state, info]) => {
      const rules = stateRules(draft.rules, state);
      const count = rules.filter(rule => rule.required && rule.steps.some(step => step.action.startsWith('expect'))).length;
      const selected = required.includes(state);
      return <article key={state} className={selected ? 'is-selected' : ''} data-ux-state={state}>
        <label className="ws-check"><input type="checkbox" aria-label={`${info.label} 상태 포함`} checked={selected}
          disabled={disabled} onChange={() => onChange(selected ? required.filter(item => item !== state) : [...required, state])} />
          <strong>{info.label}</strong></label><p>{info.prompt}</p>
        <span className={selected && !count ? 'ws-state-missing' : 'ws-muted'}>{count ? `필수 규칙 ${count}개 연결` : selected ? '필수 규칙 연결 필요' : '설계 범위 미선택'}</span>
        {onInspect && <button onClick={() => onInspect(state)}>{info.label} 규칙 보기</button>}
      </article>;
    })}</div>
    <p className="ws-muted">이 표는 설계 범위입니다. 상태 분류만으로 동작을 검증하거나 가이드 준수를 승인하지 않습니다.</p>
  </section>;
}

export function StateEvidence({ contract, evidence }: { contract: EditableContract; evidence?: Record<string, unknown> }) {
  const checks = Array.isArray(evidence?.checks) && evidence.checks.every(check =>
    check !== null && typeof check === 'object' && typeof check.caseId === 'string' && typeof check.status === 'string')
    ? evidence.checks as { caseId: string; status: string }[] : [];
  const stateResult = (ids: string[]) => {
    const matched = ids.map(id => checks.filter(check => check.caseId === id));
    return !evidence || !ids.length || matched.some(items => items.length !== 1) ? '미판정' :
      matched.some(items => items[0].status === 'fail') ? '실패' : matched.every(items => items[0].status === 'pass') ? '통과' : '미판정';
  };
  return <section className="ws-state-evidence"><h3>설계한 상태의 검수 결과</h3>
    {contract.changeRequest && <div>{contract.changeRequest.screens.filter(screen => screen.change !== 'remove').flatMap(screen =>
      screen.states.map(state => <article key={`${screen.id}:${state}`}><strong>{screen.title} · {UX_STATES[state]?.label || state}</strong>
        <span>{stateResult(contract.rules.filter(rule => rule.required && rule.screenId === screen.id && rule.scenario === state).map(rule => rule.id))}</span></article>))}
      {contract.changeRequest.screens.filter(screen => screen.change === 'remove').map(screen => <article key={screen.id}>
        <strong>{screen.title} · 삭제 확인</strong>
        <span>{stateResult(contract.rules.filter(rule => rule.required && rule.screenId === screen.id).map(rule => rule.id))}</span>
      </article>)}
      {contract.changeRequest.transitions.map(link => <article key={link.id}><strong>
        {contract.changeRequest!.screens.find(screen => screen.id === link.from)?.title} → {contract.changeRequest!.screens.find(screen => screen.id === link.to)?.title}
      </strong><span>{stateResult(contract.rules.filter(rule => rule.required && rule.transitionId === link.id).map(rule => rule.id))}</span></article>)}</div>}
    <div>{(contract.requiredStates || []).map(state => {
      const rules = stateRules(contract.rules, state).filter(rule => rule.required);
      const matched = rules.map(rule => checks.filter(check => check.caseId === rule.id));
      const status = !evidence || !rules.length || matched.some(items => items.length !== 1) ? '미판정' :
        matched.some(items => items[0].status === 'fail') ? '실패' :
          matched.every(items => items[0].status === 'pass') ? '통과' : '미판정';
      return <article key={state} data-state-result={state}><strong>{UX_STATES[state]?.label || state}</strong>
        <span>{status}</span><small>연결된 필수 규칙 {rules.length}개</small></article>;
    })}</div><p className="ws-muted">선택한 라운드와 일치하는 보고서의 결과입니다. 선택하지 않은 상태나 가이드 전체를 검증했다는 뜻은 아닙니다.</p>
  </section>;
}
