import { identity, displayCell, type Column, type Row } from '../shared';
export interface TableV1Props { columns: Column[]; rows: Row[] }
export function TableV1({ columns, rows }: TableV1Props) {
  return <div {...identity('Table', 1)} className="apc-table-wrap">
    <table className="apc-table"><thead><tr>{columns.map(column =>
      <th key={column.key} scope="col">{column.header}</th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}>{columns.map(column =>
        <td key={column.key}>{displayCell(row[column.key])}</td>)}</tr>)}</tbody>
    </table>
  </div>;
}
