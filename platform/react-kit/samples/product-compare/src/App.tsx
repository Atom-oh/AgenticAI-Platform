import { useState } from 'react';
import { Screen, Stack, Grid, Panel, Text, RadioGroup, Select, Button, Summary, Alert } from '@studio/approved-ui';

const products = [
  { id: 'steady', name: '차곡차곡 기본형', description: '같은 금액을 매달 계획적으로 모으는 가상 상품', method: '매월 정액' },
  { id: 'flexible', name: '내맘대로 자유형', description: '여유가 생길 때 자유롭게 모으는 가상 상품', method: '자유 납입' },
];

export default function App() {
  const [selected, setSelected] = useState('steady');
  const [period, setPeriod] = useState('12');
  const [layout, setLayout] = useState('cards');
  const [review, setReview] = useState(false);
  const product = products.find(item => item.id === selected) || products[0];

  if (review) return <Screen pageId="product-review" title="선택한 상품을 확인하세요" width="mobile">
    <Stack gap={6}>
      <Summary testId="product-summary" title="관심 상품" items={[
        { label: '상품 이름', value: product.name },
        { label: '납입 방식', value: product.method },
        { label: '선택 기간', value: `${period}개월` },
      ]} />
      <Alert message="가상 상품의 비교 샘플입니다. 실제 상품 추천이나 가입은 실행되지 않습니다." />
      <Button testId="product-back" kind="secondary" label="다시 비교하기" onClick={() => setReview(false)} />
    </Stack>
  </Screen>;

  return <Screen pageId="product-compare" title="나에게 맞는 방식을 비교해 보세요" width="content">
    <Stack gap={6}>
      <Text tone="muted">샘플 03 · 같은 가이드 안에서 카드형과 목록형 비교</Text>
      <RadioGroup testId="product-layout" label="보기 방식" value={layout} onChange={setLayout}
        options={[{ value: 'cards', label: '카드형' }, { value: 'list', label: '목록형' }]} />
      <Text tone="muted" size="sm">좁은 화면에서는 카드형도 한 열로 표시됩니다. 넓은 화면에서 두 배치를 비교해 보세요.</Text>
      <Grid columns={layout === 'cards' ? 2 : 1} gap={4}>
        {products.map(item => <Panel key={item.id} title={item.name} tone={selected === item.id ? 'brand' : 'default'}>
          <Stack>
            <Text>{item.description}</Text>
            <Text tone="muted">{item.method} · 6개월 또는 12개월</Text>
            <Button testId={`choose-${item.id}`} label={selected === item.id ? `${item.name} 선택됨` : `${item.name} 선택`}
              kind={selected === item.id ? 'primary' : 'secondary'} onClick={() => setSelected(item.id)} />
          </Stack>
        </Panel>)}
      </Grid>
      <Select testId="product-period" label="관심 기간" value={period} onChange={setPeriod}
        options={[{ value: '6', label: '6개월' }, { value: '12', label: '12개월' }]} />
      <Text testId="product-selection">현재 선택: {product.name} · {period}개월</Text>
      <Button testId="product-next" label="선택한 상품 확인" onClick={() => setReview(true)} />
    </Stack>
  </Screen>;
}
