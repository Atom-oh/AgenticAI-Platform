import { identity } from '../shared';
export interface ButtonV2Props {
  label: string;
  kind: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
  onClick?: () => void;
}
export function ButtonV2({ label, kind, disabled = false, onClick }: ButtonV2Props) {
  return <button {...identity('Button', 2)} type="button" className="apc-button"
    data-kind={kind} disabled={disabled} onClick={() => onClick?.()}>{label}</button>;
}
