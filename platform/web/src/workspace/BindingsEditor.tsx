import type { EditableContract } from './types';

export default function BindingsEditor({ draft, onChange }: { draft: EditableContract; onChange: (draft: EditableContract) => void }) {
  const bindings = draft.bindings || {};
  const targets = new Map(draft.rules.flatMap(rule => rule.steps).map(step => [step.target, step.targetLabel]));
  for (const target of Object.keys(bindings)) if (!targets.has(target)) targets.set(target, '현재 규칙에서 사용하지 않는 대상');
  const changeBinding = (target: string, value: string) => {
    const next = { ...bindings };
    if (value === '') delete next[target];
    else next[target] = value;
    onChange({ ...draft, bindings: next });
  };
  return <details className="ws-bindings">
    <summary>고급 설정 · 원본 HTML 요소 연결</summary>
    <p>원본 HTML에 검사 식별자가 없을 때 사용할 CSS 선택자를 확인·수정합니다. JSON을 작성할 필요는 없습니다.</p>
    <p className="ws-muted">검사기는 data-testid를 먼저 찾고, 없으면 승인한 선택자를 사용합니다. AI 생성 시안은 기존처럼 data-testid를 사용합니다. 연결을 바꾸면 규칙을 다시 저장·승인해야 합니다.</p>
    {[...targets].map(([target, label]) => <div className="ws-binding-row" key={target}>
      <div><strong>{label}</strong><code>{target}</code></div>
      <label className="ws-field">{label} CSS 선택자
        <input aria-label={`${label} CSS 선택자`} value={Object.hasOwn(bindings, target) ? bindings[target] : ''}
          maxLength={300} placeholder={'예: #amount 또는 [name="amount"]'} onChange={event => changeBinding(target, event.target.value)} />
      </label>
      <button disabled={!Object.hasOwn(bindings, target)} onClick={() => changeBinding(target, '')} aria-label={`${label} 연결 해제`}>연결 해제</button>
    </div>)}
    {!targets.size && <p>규칙에 화면 요소를 추가하면 연결을 지정할 수 있습니다.</p>}
  </details>;
}
