import { useState, type ReactNode } from 'react';
import {
  Screen, Stack, Grid, Inline, Panel, Text, Button, Input, Checkbox, Select, RadioGroup,
  Alert, Stepper, Summary, AssetImage,
} from '../../../react-kit/ui';
import type { AlertProps, ButtonProps, TextProps } from '../../../react-kit/ui/types';
import type { ComponentName } from './names';

const options = [{ value: 'one', label: '첫 번째' }, { value: 'two', label: '두 번째' }];
const steps = [{ id: 'first', label: '첫 단계' }, { id: 'second', label: '다음 단계' }, { id: 'third', label: '마지막 단계' }];
const image = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120" viewBox="0 0 240 120">' +
  '<rect width="240" height="120" rx="16" fill="#e9f6f3"/><circle cx="60" cy="60" r="28" fill="#008485"/>' +
  '<path d="M112 36h88M112 60h60M112 84h76" stroke="#006567" stroke-width="8" stroke-linecap="round"/></svg>',
);

function Knobs({ children }: { children: ReactNode }) {
  return <div className="preview-knobs" role="group" aria-label="예시 조정"><Stack gap={3}>{children}</Stack></div>;
}

/** Trusted, precompiled examples. No metadata can supply props, JSX or callbacks. */
export function Example({ name, compact }: { name: ComponentName; compact: boolean }) {
  const [clicks, setClicks] = useState(0), [disabled, setDisabled] = useState(false);
  const [kind, setKind] = useState<ButtonProps['kind']>('primary');
  const [value, setValue] = useState('예시'), [checked, setChecked] = useState(false);
  const [selection, setSelection] = useState('one'), [step, setStep] = useState(0);
  const [tone, setTone] = useState<AlertProps['tone']>('info');
  const [gap, setGap] = useState<2 | 4 | 6>(4), [columns, setColumns] = useState<1 | 2 | 3>(2);
  const [size, setSize] = useState<TextProps['size']>('md');
  const feedback = (content: string) => compact ? null : <output data-testid="example-feedback" aria-live="polite">{content}</output>;
  const spacing = <Select label="간격" value={String(gap)} onChange={v => setGap(v === '2' ? 2 : v === '6' ? 6 : 4)}
    options={[{ value: '2', label: '좁게' }, { value: '4', label: '기본' }, { value: '6', label: '넓게' }]} />;
  let content: ReactNode;
  let controls: ReactNode = null;
  switch (name) {
    case 'Screen':
      content = <Screen pageId="portal-example" title={compact ? '예시 화면' : '플랫폼 기본 화면'} width="mobile">
        <Stack gap={3}><Text>화면의 제목과 본문을 담습니다.</Text>
          {!compact && <Panel title="콘텐츠 영역"><Text>원본 Screen의 main 영역과 스타일입니다.</Text></Panel>}
        </Stack>
      </Screen>;
      break;
    case 'Stack':
      content = <Stack gap={gap}><Text>첫 번째 내용</Text><Text tone="muted">두 번째 내용</Text>
        {!compact && <Text tone="brand">세 번째 내용</Text>}</Stack>;
      controls = spacing;
      break;
    case 'Grid':
      content = <Grid columns={columns} gap={gap}>
        <Panel tone="subtle"><Text>첫 영역</Text></Panel><Panel tone="brand"><Text>두 번째</Text></Panel>
      </Grid>;
      controls = <><Select label="열 수" value={String(columns)}
        onChange={v => setColumns(v === '1' ? 1 : v === '3' ? 3 : 2)}
        options={[{ value: '1', label: '1열' }, { value: '2', label: '2열' }, { value: '3', label: '3열' }]} />{spacing}</>;
      break;
    case 'Inline':
      content = <Inline gap={gap} justify="between"><Text>첫 번째 항목</Text><Text tone="brand">두 번째 항목</Text></Inline>;
      controls = spacing;
      break;
    case 'Panel':
      content = <Panel title="묶음 영역" tone={selection === 'one' ? 'subtle' : 'brand'}><Text>함께 읽을 내용을 묶습니다.</Text></Panel>;
      controls = <Select label="패널 표현" value={selection} onChange={setSelection}
        options={[{ value: 'one', label: '은은하게' }, { value: 'two', label: '브랜드' }]} />;
      break;
    case 'Text':
      content = <Stack gap={2}><Text as="h2" size={size}>읽기 쉬운 문장</Text><Text tone="muted" size="sm">원본 타이포그래피와 색상</Text></Stack>;
      controls = <Select label="글자 크기" value={size || 'md'} onChange={v => setSize(v === 'sm' ? 'sm' : v === 'lg' ? 'lg' : 'md')}
        options={[{ value: 'sm', label: '작게' }, { value: 'md', label: '기본' }, { value: 'lg', label: '크게' }]} />;
      break;
    case 'Button':
      content = <Stack gap={3}><Button label="눌러 보기" kind={kind} disabled={disabled} onClick={() => setClicks(n => n + 1)} />
        {feedback(`버튼을 ${clicks}번 눌렀습니다.`)}</Stack>;
      controls = <><Select label="버튼 표현" value={kind || 'primary'}
        onChange={v => setKind(v === 'secondary' ? 'secondary' : v === 'danger' ? 'danger' : 'primary')}
        options={[{ value: 'primary', label: '기본' }, { value: 'secondary', label: '보조' }, { value: 'danger', label: '주의' }]} />
        <Checkbox label="버튼 비활성화" checked={disabled} onChange={setDisabled} /></>;
      break;
    case 'Input':
      content = <Stack gap={3}><Input label="입력 예시" value={value} onChange={setValue} disabled={disabled}
        hint={compact ? undefined : '입력한 내용은 이 예시에서만 유지됩니다.'} />
        {feedback(`입력한 값: ${value || '비어 있음'}`)}</Stack>;
      controls = <Checkbox label="입력 비활성화" checked={disabled} onChange={setDisabled} />;
      break;
    case 'Checkbox':
      content = <Stack gap={3}><Checkbox label="선택 예시" checked={checked} onChange={setChecked} disabled={disabled} />
        {feedback(checked ? '선택했습니다.' : '선택하지 않았습니다.')}</Stack>;
      controls = <Checkbox label="체크박스 비활성화" checked={disabled} onChange={setDisabled} />;
      break;
    case 'Select':
      content = <Stack gap={3}><Select label="선택 예시" value={selection} onChange={setSelection} options={options} disabled={disabled} />
        {feedback(`현재 선택: ${selection === 'one' ? '첫 번째' : '두 번째'}`)}</Stack>;
      controls = <Checkbox label="선택 비활성화" checked={disabled} onChange={setDisabled} />;
      break;
    case 'RadioGroup':
      content = <Stack gap={3}><RadioGroup label="하나 선택" value={selection} onChange={setSelection} options={options} />
        {feedback(`현재 선택: ${selection === 'one' ? '첫 번째' : '두 번째'}`)}</Stack>;
      break;
    case 'Alert':
      content = <Alert title="표현 예시" message="실제 처리 결과가 아닌 컴포넌트 표현 예시입니다." tone={tone} />;
      controls = <Select label="알림 표현" value={tone || 'info'}
        onChange={v => setTone(v === 'success' ? 'success' : v === 'warning' ? 'warning' : v === 'danger' ? 'danger' : 'info')}
        options={[{ value: 'info', label: '안내' }, { value: 'success', label: '완료 표현' },
          { value: 'warning', label: '주의' }, { value: 'danger', label: '오류 표현' }]} />;
      break;
    case 'Stepper':
      content = <Stepper steps={compact ? steps.slice(0, 2) : steps} current={steps[step].id} />;
      controls = <><Inline gap={3}>
        <Button label="이전 단계 예시" kind="secondary" disabled={step === 0} onClick={() => setStep(n => Math.max(0, n - 1))} />
        <Button label="다음 단계 예시" disabled={step === steps.length - 1} onClick={() => setStep(n => Math.min(steps.length - 1, n + 1))} />
      </Inline><Text size="sm" tone="muted">현재 위치만 바뀝니다. 검증 완료를 나타내지 않습니다.</Text></>;
      break;
    case 'Summary':
      content = <Summary title="예시 요약" items={compact ? [{ label: '유형', value: '로컬 예시' }] :
        [{ label: '유형', value: '로컬 예시' }, { label: '선택', value: '첫 번째' }, { label: '데이터', value: '고정 예시' }]} />;
      break;
    case 'AssetImage':
      content = <AssetImage src={image} alt="초록색 원과 선으로 구성된 로컬 예시 이미지" width={240} height={120} />;
      break;
  }
  return <Stack gap={compact ? 2 : 6}>
    <div data-preview-example={name}>{content}</div>
    {!compact && controls && <Knobs>{controls}</Knobs>}
  </Stack>;
}
