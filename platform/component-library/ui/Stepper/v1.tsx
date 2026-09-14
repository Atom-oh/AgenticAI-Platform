import { identity, type Item } from '../shared';
export interface StepperV1Props { steps: Item[]; current: string }
export function StepperV1({ steps, current }: StepperV1Props) {
  const active = steps.findIndex(step => step.id === current);
  return <ol {...identity('Stepper', 1)} className="apc-stepper" aria-label="진행 단계">
    {steps.map((step, index) => <li key={step.id} aria-current={step.id === current ? 'step' : undefined}
      data-complete={active >= 0 && index < active}>
      <span className="apc-step-number" aria-hidden="true">{active >= 0 && index < active ? '✓' : index + 1}</span>{step.label}
    </li>)}
  </ol>;
}
