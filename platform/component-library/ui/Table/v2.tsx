import { useMemo, useState } from 'react';
import { identity, displayCell, compareCells, type Column, type Row } from '../shared';
export interface TableV2Props { columns: Column[]; rows: Row[]; sortable?: boolean; emptyText?: string }
export function TableV2({ columns, rows, sortable = false, emptyText = '표시할 항목이 없습니다.' }: TableV2Props) {
  const [sort, setSort] = useState<{ key: string; direction: 1 | -1 } | null>(null);
  const active = sortable && sort && columns.some(column => column.key === sort.key) ? sort : null;
  const displayed = useMemo(() => active ? rows.map((row, index) => ({ row, index })).sort((a, b) =>
    active.direction * compareCells(a.row[active.key], b.row[active.key]) || a.index - b.index).map(item => item.row) : rows,
  [rows, active]);
  return <div {...identity('Table', 2)} className="apc-table-wrap"><table className="apc-table">
    <thead><tr>{columns.map(column => <th key={column.key} scope="col"
      aria-sort={active?.key === column.key ? active.direction === 1 ? 'ascending' : 'descending' : undefined}>
      {sortable ? <button type="button" className="apc-sort" aria-label={`${column.header} 정렬`}
        onClick={() => setSort(previous => ({ key: column.key, direction: previous?.key === column.key && previous.direction === 1 ? -1 : 1 }))}>
        {column.header}<span aria-hidden="true">{active?.key === column.key ? active.direction === 1 ? '↑' : '↓' : '↕'}</span>
      </button> : column.header}</th>)}</tr></thead>
    <tbody>{displayed.length ? displayed.map((row, index) => <tr key={index}>{columns.map(column =>
      <td key={column.key}>{displayCell(row[column.key])}</td>)}</tr>) :
      <tr><td className="apc-empty" colSpan={Math.max(columns.length, 1)}>{emptyText}</td></tr>}</tbody>
  </table></div>;
}
