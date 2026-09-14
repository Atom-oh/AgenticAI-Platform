import { useId, useState } from 'react';
import { identity, type Option } from '../shared';
export interface SelectV2Props {
  label: string; options: Option[]; value: string; onChange: (value: string) => void;
  searchable?: boolean; placeholder?: string;
}
export function SelectV2({ label, options, value, onChange, searchable = false, placeholder = '선택하세요' }: SelectV2Props) {
  const id = useId();
  const [query, setQuery] = useState('');
  const selected = options.find(option => option.value === value);
  const filtered = options.filter(option => !searchable || option.label.toLocaleLowerCase('ko').includes(query.trim().toLocaleLowerCase('ko')));
  // Keep a selected option in the native select while filtering. Dropping it
  // would make the browser display another value without an onChange event.
  const retained = selected && !filtered.includes(selected) ? selected : null;
  return <div {...identity('Select', 2)} className="apc-field">
    <label htmlFor={id}>{label}</label>
    {searchable && <input type="search" aria-label={`${label} 검색`} placeholder="선택 항목 검색"
      value={query} onChange={event => setQuery(event.currentTarget.value)} />}
    <select id={id} value={value} onChange={event => onChange(event.currentTarget.value)}>
      {!selected && <option value={value}>{placeholder}</option>}
      {retained && <option value={retained.value}>{retained.label} (현재 선택)</option>}
      {filtered.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    {searchable && <p className="apc-hint" role="status">검색 결과 {filtered.length}개</p>}
  </div>;
}
