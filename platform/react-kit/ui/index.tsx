import { useId } from 'react';
import { VERSION, choice, dimension, finite, label, optionalText, options as checkedOptions, requireCallback, text } from './internal';
import type {
  AlertProps, AssetImageProps, ButtonProps, CheckboxProps, GridProps, InlineProps, InputProps, PanelProps,
  RadioGroupProps, ScreenProps, SelectProps, StackProps, StepperProps, SummaryProps, TextProps,
} from './types';
import './tokens.css';

export type * from './types';

export function Screen({ pageId, children, width, title, id, testId }: ScreenProps) {
  const headingId = useId();
  return <main className="sui-screen" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Screen" data-studio-version={VERSION} data-page-id={label(pageId, 'Screen.pageId')}
    data-width={choice(width, ['mobile', 'content', 'wide'] as const, 'content')} aria-labelledby={optionalText(title) ? headingId : undefined}>
    {optionalText(title) && <header className="sui-screen-heading"><h1 id={headingId}>{text(title)}</h1></header>}
    {children}
  </main>;
}

export function Stack({ children, gap, id, testId }: StackProps) {
  return <div className="sui-stack" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Stack" data-studio-version={VERSION}
    data-gap={choice(gap, [1, 2, 3, 4, 6, 8] as const, 4)}>{children}</div>;
}

export function Grid({ children, columns, gap, id, testId }: GridProps) {
  return <div className="sui-grid" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Grid" data-studio-version={VERSION} data-columns={choice(columns, [1, 2, 3] as const, 1)}
    data-gap={choice(gap, [1, 2, 3, 4, 6, 8] as const, 4)}>{children}</div>;
}

export function Inline({ children, gap, align, justify, id, testId }: InlineProps) {
  return <div className="sui-inline" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Inline" data-studio-version={VERSION} data-gap={choice(gap, [1, 2, 3, 4, 6] as const, 4)}
    data-align={choice(align, ['start', 'center', 'end'] as const, 'center')}
    data-justify={choice(justify, ['start', 'between', 'end'] as const, 'start')}>{children}</div>;
}

export function Panel({ children, title, tone, id, testId }: PanelProps) {
  const headingId = useId();
  return <section className="sui-panel" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Panel" data-studio-version={VERSION} data-tone={choice(tone, ['default', 'subtle', 'brand'] as const, 'default')}
    aria-labelledby={optionalText(title) ? headingId : undefined}>
    {optionalText(title) && <h2 className="sui-panel-title" id={headingId}>{text(title)}</h2>}
    {children}
  </section>;
}

export function Text({ children, as, size, tone, id, testId }: TextProps) {
  // Runtime allowlist is essential: generated TSX can attempt `as={... as any}`.
  const Tag = choice(as, ['span', 'p', 'h1', 'h2', 'h3'] as const, 'p');
  return <Tag className="sui-text" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Text" data-studio-version={VERSION}
    data-size={size === undefined ? undefined : choice(size, ['sm', 'md', 'lg'] as const, 'md')}
    data-tone={choice(tone, ['default', 'muted', 'brand', 'danger'] as const, 'default')}>{children}</Tag>;
}

export function Button({ label: caption, onClick, kind, disabled, type, id, testId }: ButtonProps) {
  return <button className="sui-button" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Button" data-studio-version={VERSION}
    data-kind={choice(kind, ['primary', 'secondary', 'danger'] as const, 'primary')}
    type={choice(type, ['button', 'submit'] as const, 'button')} disabled={disabled === true}
    onClick={() => { if (disabled !== true && typeof onClick === 'function') onClick(); }}>
    {label(caption, 'Button.label')}
  </button>;
}

export function Input({ label: caption, value, onChange, type, hint, error, required, disabled, min, max, placeholder, id, testId }: InputProps) {
  const generatedId = useId();
  const inputId = optionalText(id) || `studio-input-${generatedId}`;
  const hintId = `${inputId}-hint`, errorId = `${inputId}-error`;
  const describedBy = [optionalText(hint) ? hintId : '', optionalText(error) ? errorId : ''].filter(Boolean).join(' ') || undefined;
  const inputType = choice(type, ['text', 'number', 'email', 'tel'] as const, 'text');
  requireCallback(onChange, 'Input.onChange');
  return <div className="sui-field" data-studio-component="Input" data-studio-version={VERSION}>
    <label className="sui-label" htmlFor={inputId}>{label(caption, 'Input.label')}{required === true && <span aria-hidden="true" className="sui-required"> *</span>}</label>
    <input className="sui-control" id={inputId} data-testid={optionalText(testId)} type={inputType} value={text(value)}
      onChange={event => onChange(event.currentTarget.value)} required={required === true} disabled={disabled === true}
      min={inputType === 'number' ? finite(min) : undefined} max={inputType === 'number' ? finite(max) : undefined}
      placeholder={optionalText(placeholder)} aria-invalid={optionalText(error) ? true : undefined} aria-describedby={describedBy} />
    {optionalText(hint) && <p className="sui-hint" id={hintId}>{text(hint)}</p>}
    {optionalText(error) && <p className="sui-error" id={errorId} role="alert">{text(error)}</p>}
  </div>;
}

export function Checkbox({ label: caption, checked, onChange, disabled, required, id, testId }: CheckboxProps) {
  const generatedId = useId();
  const inputId = optionalText(id) || `studio-check-${generatedId}`;
  requireCallback(onChange, 'Checkbox.onChange');
  return <label className="sui-checkbox" htmlFor={inputId} data-studio-component="Checkbox" data-studio-version={VERSION}>
    <input id={inputId} data-testid={optionalText(testId)} type="checkbox" checked={checked === true}
      onChange={event => onChange(event.currentTarget.checked)} disabled={disabled === true} required={required === true} />
    <span>{label(caption, 'Checkbox.label')}{required === true && <span aria-hidden="true" className="sui-required"> *</span>}</span>
  </label>;
}

export function Select({ label: caption, value, onChange, options, disabled, id, testId }: SelectProps) {
  const generatedId = useId();
  const inputId = optionalText(id) || `studio-select-${generatedId}`;
  requireCallback(onChange, 'Select.onChange');
  return <div className="sui-field" data-studio-component="Select" data-studio-version={VERSION}>
    <label className="sui-label" htmlFor={inputId}>{label(caption, 'Select.label')}</label>
    <select className="sui-control" id={inputId} data-testid={optionalText(testId)} value={text(value)}
      disabled={disabled === true} onChange={event => onChange(event.currentTarget.value)}>
      {checkedOptions(options).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  </div>;
}

export function RadioGroup({ label: caption, value, onChange, options, id, testId }: RadioGroupProps) {
  const generatedId = useId();
  const legendId = `studio-radio-label-${generatedId}`;
  const name = `studio-radio-${generatedId}`;
  requireCallback(onChange, 'RadioGroup.onChange');
  return <fieldset className="sui-radio-group" id={optionalText(id)} data-testid={optionalText(testId)}
    role="radiogroup" aria-labelledby={legendId} data-studio-component="RadioGroup" data-studio-version={VERSION}>
    <legend id={legendId} className="sui-label">{label(caption, 'RadioGroup.label')}</legend>
    <div className="sui-radio-options">{checkedOptions(options).map((option, index) => {
      const optionId = `${name}-${index + 1}`;
      return <label className="sui-radio-option" key={option.value} htmlFor={optionId}>
        <input id={optionId} name={name} type="radio" value={option.value} checked={value === option.value}
          data-testid={optionalText(testId) ? `${testId}-${option.value}` : undefined}
          onChange={event => { if (event.currentTarget.checked) onChange(option.value); }} />
        <span>{option.label}</span>
      </label>;
    })}</div>
  </fieldset>;
}

export function Alert({ message, title, tone, id, testId }: AlertProps) {
  const level = choice(tone, ['info', 'success', 'warning', 'danger'] as const, 'info');
  return <div className="sui-alert" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Alert" data-studio-version={VERSION} data-tone={level} role={level === 'danger' || level === 'warning' ? 'alert' : 'status'}>
    <span className="sui-sr-only">{({ info: '안내', success: '완료', warning: '주의', danger: '오류' })[level]}: </span>
    {optionalText(title) && <strong className="sui-alert-title">{text(title)}</strong>}
    <p>{label(message, 'Alert.message')}</p>
  </div>;
}

export function Stepper({ steps, current, id, testId }: StepperProps) {
  if (!Array.isArray(steps)) throw new Error('Stepper.steps는 단계 목록이어야 합니다.');
  return <nav className="sui-stepper" id={optionalText(id)} data-testid={optionalText(testId)} aria-label="진행 단계"
    data-studio-component="Stepper" data-studio-version={VERSION}>
    <ol>{steps.map((step, index) => <li key={label(step.id, 'Stepper.step.id')} aria-current={step.id === current ? 'step' : undefined}>
      <span className="sui-step-number" aria-hidden="true">{index + 1}</span><span>{label(step.label, 'Stepper.step.label')}</span>
    </li>)}</ol>
  </nav>;
}

export function Summary({ title, items, id, testId }: SummaryProps) {
  const headingId = useId();
  if (!Array.isArray(items)) throw new Error('Summary.items는 요약 항목 목록이어야 합니다.');
  return <section className="sui-summary" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="Summary" data-studio-version={VERSION} aria-labelledby={optionalText(title) ? headingId : undefined}>
    {optionalText(title) && <h2 className="sui-panel-title" id={headingId}>{text(title)}</h2>}
    <dl>{items.map((item, index) => <div key={index}><dt>{label(item.label, 'Summary.item.label')}</dt><dd>{text(item.value)}</dd></div>)}</dl>
  </section>;
}

export function AssetImage({ src, alt, width, height, id, testId }: AssetImageProps) {
  if (typeof src !== 'string' || !/^data:image\/(?:png|jpeg|gif|webp|avif|svg\+xml)(?:;[a-z0-9=.+-]+)*,/i.test(src)) {
    throw new Error('AssetImage.src는 반입된 data:image 형식만 사용할 수 있습니다.');
  }
  if (typeof alt !== 'string') throw new Error('AssetImage.alt에 대체 설명을 입력하세요. 장식 이미지는 빈 문자열을 사용하세요.');
  return <img className="sui-asset-image" id={optionalText(id)} data-testid={optionalText(testId)}
    data-studio-component="AssetImage" data-studio-version={VERSION} src={src} alt={alt}
    width={dimension(width)} height={dimension(height)} decoding="async" />;
}
