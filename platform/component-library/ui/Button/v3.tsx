import { identity } from '../shared';
export interface ButtonV3Props {
  label: string;
  variant: 'solid' | 'outline' | 'ghost';
  tone: 'brand' | 'neutral' | 'critical';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  onClick?: () => void;
}
export function ButtonV3({ label, variant, tone, size = 'md', disabled = false, onClick }: ButtonV3Props) {
  return <button {...identity('Button', 3)} type="button" className="apc-button"
    data-variant={variant} data-tone={tone} data-size={size} disabled={disabled}
    onClick={() => onClick?.()}>{label}</button>;
}
