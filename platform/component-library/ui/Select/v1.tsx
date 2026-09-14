import { useId } from 'react';
import { identity, type Option } from '../shared';
export interface SelectV1Props { label: string; options: Option[]; value: string; onChange: (value: string) => void }
export function SelectV1({ label, options, value, onChange }: SelectV1Props) {
  const id = useId();
  return <div {...identity('Select', 1)} className="apc-field"><label htmlFor={id}>{label}</label>
    <select id={id} value={value} onChange={event => onChange(event.currentTarget.value)}>
      {!options.some(option => option.value === value) && <option value={value} disabled>선택하세요</option>}
      {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  </div>;
}
