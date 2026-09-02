'use strict';
// 테스트 공용 — 기준선 승인 컴포넌트(screengen/components_fixture.py 와 같은 스키마)와 샘플 화면 코드

const fn = { type: 'function' };
const node = { type: 'node' };

const COMPONENTS = [
  { name: 'Button', version: 'v2', module: '@atom/ui/button', exportName: 'Button', propsSchema: {
    type: 'object', properties: { label: { type: 'string' }, kind: { type: 'string', enum: ['primary', 'secondary', 'ghost'] },
      disabled: { type: 'boolean' }, onClick: fn }, required: ['label'] } },
  { name: 'DataTable', version: 'v1', module: '@atom/ui/data-table', exportName: 'DataTable', propsSchema: {
    type: 'object', properties: {
      caption: { type: 'string' },
      columns: { type: 'array', items: { type: 'object', properties: { key: { type: 'string' }, header: { type: 'string' },
        align: { type: 'string', enum: ['left', 'right', 'center'] }, width: { type: 'number' } }, required: ['key', 'header'] } },
      rows: { type: 'array', items: { type: 'object' } }, rowKey: { type: 'string' }, emptyText: { type: 'string' } },
    required: ['caption', 'columns', 'rows'] } },
  { name: 'Badge', version: 'v1', module: '@atom/ui/badge', exportName: 'Badge', propsSchema: {
    type: 'object', properties: { label: { type: 'string' }, tone: { type: 'string', enum: ['success', 'warning', 'danger', 'neutral', 'info'] } }, required: ['label'] } },
  { name: 'Card', version: 'v1', module: '@atom/ui/card', exportName: 'Card', propsSchema: {
    type: 'object', properties: { title: { type: 'string' }, subtitle: { type: 'string' }, children: node, actions: node }, required: ['title'] } },
  { name: 'FormField', version: 'v1', module: '@atom/ui/form-field', exportName: 'FormField', propsSchema: {
    type: 'object', properties: { label: { type: 'string' }, htmlFor: { type: 'string' }, required: { type: 'boolean' }, hint: { type: 'string' },
      error: { type: 'string' }, children: node }, required: ['label', 'htmlFor'] } },
  { name: 'Select', version: 'v1', module: '@atom/ui/select', exportName: 'Select', propsSchema: {
    type: 'object', properties: { id: { type: 'string' }, name: { type: 'string' }, value: { type: 'string' },
      options: { type: 'array', items: { type: 'object', properties: { value: { type: 'string' }, label: { type: 'string' } }, required: ['value', 'label'] } },
      onChange: fn, disabled: { type: 'boolean' }, placeholder: { type: 'string' } }, required: ['id', 'options'] } },
  { name: 'PageHeader', version: 'v1', module: '@atom/ui/page-header', exportName: 'PageHeader', propsSchema: {
    type: 'object', properties: { title: { type: 'string' }, description: { type: 'string' }, breadcrumbs: { type: 'array', items: { type: 'string' } }, actions: node }, required: ['title'] } },
  { name: 'Alert', version: 'v1', module: '@atom/ui/alert', exportName: 'Alert', propsSchema: {
    type: 'object', properties: { kind: { type: 'string', enum: ['info', 'success', 'warning', 'error'] }, title: { type: 'string' }, message: { type: 'string' } }, required: ['kind', 'message'] } },
];

// Button@v2 props 만 쓰는 정상 화면 (여신 심사 결과 조회)
const VALID_SCREEN = `// registry: Button@v2, DataTable@v1, PageHeader@v1, Badge@v1, FormField@v1, Select@v1
import { useState } from 'react';
import { Button } from '@atom/ui/button';
import { DataTable } from '@atom/ui/data-table';
import { PageHeader } from '@atom/ui/page-header';
import { Badge } from '@atom/ui/badge';
import { FormField } from '@atom/ui/form-field';
import { Select } from '@atom/ui/select';

// 합성데이터
const SAMPLE_ROWS = [
  { id: 'LN-2026-0001', customer: '김*수', amount: 250000000, status: '승인', date: '2026.09.01' },
  { id: 'LN-2026-0002', customer: '이*영', amount: 120000000, status: '심사중', date: '2026.09.02' },
  { id: 'LN-2026-0003', customer: '박*호', amount: 80000000, status: '반려', date: '2026.09.02' },
];

export default function Screen() {
  const [status, setStatus] = useState('');
  const rows = SAMPLE_ROWS.filter((r) => !status || r.status === status)
    .map((r) => ({ ...r, amountText: r.amount.toLocaleString('ko-KR') + '원' }));
  const columns = [
    { key: 'id', header: '심사번호' },
    { key: 'customer', header: '고객' },
    { key: 'amountText', header: '대출금액 (원)', align: 'right' as const },
    { key: 'status', header: '심사상태' },
    { key: 'date', header: '신청일 (YYYY.MM.DD)' },
  ];
  return (
    <div className="p-6">
      <PageHeader title="여신 심사 결과 조회" description={'총 ' + rows.length + '건'} />
      <FormField label="심사상태" htmlFor="status">
        <Select id="status" value={status} onChange={(e: { target: { value: string } }) => setStatus(e.target.value)}
          options={[{ value: '', label: '전체' }, { value: '승인', label: '승인' }, { value: '심사중', label: '심사중' }]} />
      </FormField>
      <Badge label="승인" tone="success" />
      <DataTable caption="여신 심사 결과 목록" columns={columns} rows={rows} rowKey="id" emptyText="조회 결과가 없습니다." />
      <div style={{ textAlign: 'right' }}>
        <Button label="초기화" kind="secondary" onClick={() => setStatus('')} />
        <Button label="조회" kind="primary" />
      </div>
    </div>
  );
}
`;

// Button@v3 props(variant/tone/size) — v2 스키마만 승인된 상태
const V3_PROPS_SCREEN = `// registry: Button@v2
import { Button } from '@atom/ui/button';

export default function Screen() {
  return <Button label="조회" variant="primary" tone="brand" size="md" />;
}
`;

// alt 없는 raw <img>
const IMG_NO_ALT_SCREEN = `// registry: PageHeader@v1
import { PageHeader } from '@atom/ui/page-header';

export default function Screen() {
  return (
    <div>
      <PageHeader title="테스트 화면" />
      <img src="/chart.png" />
    </div>
  );
}
`;

// fetch() 사용
const FETCH_SCREEN = `// registry: Button@v2
import { Button } from '@atom/ui/button';

export default function Screen() {
  return <Button label="조회" onClick={() => { fetch('/api/loans'); }} />;
}
`;

module.exports = { COMPONENTS, VALID_SCREEN, V3_PROPS_SCREEN, IMG_NO_ALT_SCREEN, FETCH_SCREEN };
