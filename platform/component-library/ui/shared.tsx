import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import './styles.css';

export type Option = { value: string; label: string };
export type Column = { key: string; header: string };
export type Item = { id: string; label: string };
export type Row = Record<string, unknown>;

export function identity(name: string, major: number) {
  return {
    'data-component-id': `CMP-${name}-v${major}`,
    'data-portal-component': name,
    'data-portal-version': `${major}.0.0`,
  };
}

export function displayCell(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? '예' : '아니요';
  return JSON.stringify(value);
}

export function compareCells(left: unknown, right: unknown): number {
  if (typeof left === 'number' && typeof right === 'number') return left - right;
  return displayCell(left).localeCompare(displayCell(right), 'ko', { numeric: true });
}

export function TextControl({ major, label, value, onChange, placeholder, type = 'text', error, prefix }: {
  major: number; label: string; value: string; onChange: (value: string) => void;
  placeholder?: string; type?: 'text' | 'number' | 'password' | 'tel'; error?: string; prefix?: string;
}) {
  const id = useId();
  const invalid = !!error?.trim();
  return <div {...identity('Input', major)} className="apc-field">
    <label htmlFor={id}>{label}</label>
    <div className="apc-input-wrap">
      {prefix && <span className="apc-prefix" aria-hidden="true">{prefix}</span>}
      <input id={id} type={type} value={value} placeholder={placeholder}
        aria-invalid={invalid || undefined} aria-describedby={invalid ? `${id}-error` : undefined}
        onChange={event => onChange(event.currentTarget.value)} />
    </div>
    {invalid && <p id={`${id}-error`} className="apc-error">{error}</p>}
  </div>;
}

export function DateControl({ major, label, value, onChange, min, max }: {
  major: number; label: string; value: string; onChange: (iso: string) => void; min?: string; max?: string;
}) {
  const id = useId();
  return <div {...identity('DatePicker', major)} className="apc-field">
    <label htmlFor={id}>{label}</label>
    <input id={id} type="date" value={value} min={min} max={max}
      onChange={event => onChange(event.currentTarget.value)} />
  </div>;
}

export function TabControl({ major, items, activeId, onChange, variant = 'line' }: {
  major: number; items: Item[]; activeId: string; onChange: (id: string) => void; variant?: 'line' | 'pill';
}) {
  const ref = useRef<HTMLDivElement>(null);
  const prefix = useId();
  const selected = items.findIndex(item => item.id === activeId);
  function keyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === 'ArrowRight') next = (index + 1) % items.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    else return;
    event.preventDefault();
    onChange(items[next].id);
    ref.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }
  return <div {...identity('Tabs', major)} className="apc-tabs" data-variant={variant}
    role="tablist" aria-label="탭 선택" ref={ref}>
    {items.map((item, index) => <button type="button" role="tab" id={`${prefix}-${index}`} key={item.id}
      aria-selected={item.id === activeId} tabIndex={index === (selected < 0 ? 0 : selected) ? 0 : -1}
      onClick={() => onChange(item.id)} onKeyDown={event => keyDown(event, index)}>{item.label}</button>)}
  </div>;
}

export function ModalFrame({ major, open, title, onClose, children, size = 'md' }: {
  major: number; open: boolean; title: string; onClose: () => void; children?: ReactNode; size?: 'sm' | 'md' | 'lg';
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);
  function trap(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return;
    const elements = [...event.currentTarget.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]',
    )].filter(el => el.tabIndex >= 0 && !el.matches(':disabled,[hidden]') && el.getClientRects().length > 0);
    const first = elements[0], last = elements.at(-1);
    if (!first) { event.preventDefault(); event.currentTarget.focus(); return; }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  }
  return <dialog {...identity('Modal', major)} className="apc-modal" data-size={size} ref={ref}
    aria-labelledby={`${id}-title`} tabIndex={-1} onKeyDown={trap}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => {
      if (event.target !== event.currentTarget) return;
      const box = event.currentTarget.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) onClose();
    }}>
    <div className="apc-modal-content">
      <header><h2 id={`${id}-title`}>{title}</h2>
        <button className="apc-close" type="button" aria-label="대화상자 닫기" onClick={onClose}>닫기</button></header>
      <div className="apc-modal-body">{children}</div>
    </div>
  </dialog>;
}
