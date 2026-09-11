import { useState } from 'react';
import { Screen, Stack, Panel, Text, Checkbox, Button, Summary, Alert } from '@studio/approved-ui';

export default function App() {
  const [guide, setGuide] = useState(false);
  const [terms, setTerms] = useState(false);
  const [news, setNews] = useState(false);
  const [review, setReview] = useState(false);
  const ready = guide && terms;
  const reset = () => { setGuide(false); setTerms(false); setNews(false); setReview(false); };

  if (review) return <Screen key="review" pageId="consent-review" title="선택한 동의 내용을 확인하세요" width="mobile">
    <Stack gap={6}>
      <Summary testId="consent-summary" title="현재 선택" items={[
        { label: '상품 안내 확인', value: guide ? '확인함' : '미확인' },
        { label: '이용 조건 확인', value: terms ? '확인함' : '미확인' },
        { label: '새 소식 받기', value: news ? '선택함' : '선택하지 않음' },
      ]} />
      <Alert tone="success" message="필수 항목이 확인되었습니다. 이 화면은 법적 동의나 실제 신청을 기록하지 않습니다." />
      <Button testId="consent-back" kind="secondary" label="동의 내용 수정" onClick={() => setReview(false)} />
      <Button testId="consent-reset" kind="secondary" label="선택 초기화" onClick={reset} />
    </Stack>
  </Screen>;

  return <Screen key="entry" pageId="consent-entry" title="필수 안내를 확인해 주세요" width="mobile">
    <Stack gap={6}>
      <Text tone="muted">샘플 02 · 필수 조건과 선택 항목 구분</Text>
      <Panel title="확인할 내용" tone="subtle">
        <Stack>
          <Text testId="consent-notice">이 화면은 동의 UX를 검토하기 위한 가상 예시입니다. 실제 약관과 상품 설명은 고객사의 승인된 문구로 교체해야 합니다.</Text>
          <Checkbox testId="consent-guide" label="[필수] 상품 안내를 확인했습니다" checked={guide} onChange={setGuide} required />
          <Checkbox testId="consent-terms" label="[필수] 이용 조건을 확인했습니다" checked={terms} onChange={setTerms} required />
          <Checkbox testId="consent-news" label="[선택] 새 소식을 받겠습니다" checked={news} onChange={setNews} />
          <Button testId="consent-required" kind="secondary" label="필수 항목만 모두 확인"
            onClick={() => { setGuide(true); setTerms(true); }} />
        </Stack>
      </Panel>
      <Alert message={ready ? '필수 항목을 모두 확인했습니다. 다음으로 진행할 수 있습니다.' : '필수 항목 두 가지를 확인하면 다음으로 진행할 수 있습니다. 선택 항목은 동의하지 않아도 됩니다.'} />
      <Button testId="consent-next" label="선택 내용 확인" disabled={!ready}
        onClick={() => { if (ready) setReview(true); }} />
    </Stack>
  </Screen>;
}
