import { identity } from '../shared';
export interface ButtonV1Props {
  label: string;
  variant?: 'primary' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  onClick?: () => void;
}
export function ButtonV1({ label, variant = 'primary', size = 'md', onClick }: ButtonV1Props) {
  return <button {...identity('Button', 1)} type="button" className="apc-button"
    data-variant={variant} data-size={size} onClick={() => onClick?.()}>{label}</button>;
}
