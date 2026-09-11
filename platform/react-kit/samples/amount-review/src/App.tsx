import { useState } from 'react';
import { Screen, Stack, Inline, Panel, Text, Input, Select, Button, Stepper, Summary, Alert } from '@studio/approved-ui';

// Sample business rules live in this composition; the locked ui/ package owns the visuals.
export default function App() {
  const [amount, setAmount] = useState('100000');
  const [period, setPeriod] = useState('12');
  const [review, setReview] = useState(false);
  const valid = /^\d+$/.test(amount) && Number(amount) >= 10000 && Number(amount) <= 500000;
  const money = (value: number) => `${value.toLocaleString('ko-KR')}원`;
  const steps = [{ id: 'entry', label: '금액 입력' }, { id: 'review', label: '내용 확인' }];

  if (review) return <Screen pageId="amount-review" title="입력한 내용을 확인하세요" width="mobile">
    <Stack gap={6}>
      <Stepper steps={steps} current="review" />
      <Summary testId="amount-summary" title="입력 내용" items={[
        { label: '매월 납입금액', value: money(Number(amount)) },
        { label: '납입기간', value: `${period}개월` },
        { label: '총 납입원금', value: money(Number(amount) * Number(period)) },
      ]} />
      <Alert message="동작 확인용 샘플입니다. 실제 상품 가입이나 이체는 실행되지 않습니다." />
      <Button testId="amount-back" kind="secondary" label="입력 내용 수정" onClick={() => setReview(false)} />
    </Stack>
  </Screen>;

  return <Screen pageId="amount-entry" title="매달 얼마를 모을까요?" width="mobile">
    <Stack gap={6}>
      <Stepper steps={steps} current="entry" />
      <Text tone="muted">샘플 01 · 입력값 검증과 다음 화면 전달</Text>
      <Panel title="나에게 맞는 금액" tone="subtle">
        <Stack>
          <Input testId="amount" label="월 납입금액" value={amount} onChange={setAmount}
            hint="10,000원부터 500,000원까지 정수로 입력하세요." required
            error={valid ? undefined : '10,000원 이상 500,000원 이하의 정수를 입력하세요.'} />
          <Inline>
            <Button testId="amount-100000" kind="secondary" label="10만원" onClick={() => setAmount('100000')} />
            <Button testId="amount-300000" kind="secondary" label="30만원" onClick={() => setAmount('300000')} />
          </Inline>
          <Select testId="period" label="납입기간" value={period} onChange={setPeriod}
            options={[{ value: '6', label: '6개월' }, { value: '12', label: '12개월' }]} />
        </Stack>
      </Panel>
      <Text tone="muted">표시 금액은 원금 합계이며 이자·세금 계산을 포함하지 않습니다.</Text>
      <Button testId="amount-next" label="입력 내용 확인" disabled={!valid}
        onClick={() => { if (valid) setReview(true); }} />
    </Stack>
  </Screen>;
}
