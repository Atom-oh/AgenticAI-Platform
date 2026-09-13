import { isComponentName, type ComponentName } from './names';

// Documentation fragments only. These strings never enter a compiler or renderer.
const snippets: Record<ComponentName, string> = {
  Screen: '<Screen pageId="example" title="화면 예시"><Text>플랫폼 기본 화면</Text></Screen>',
  Stack: '<Stack gap={4}><Text>첫 번째 내용</Text><Text>두 번째 내용</Text></Stack>',
  Grid: '<Grid columns={2} gap={4}><Panel>첫 번째 영역</Panel><Panel>두 번째 영역</Panel></Grid>',
  Inline: '<Inline gap={3} justify="between"><Text>왼쪽</Text><Text>오른쪽</Text></Inline>',
  Panel: '<Panel title="안내" tone="subtle"><Text>내용을 묶어 표시합니다.</Text></Panel>',
  Text: '<Text as="h2" size="lg" tone="brand">컴포넌트 사용 예시</Text>',
  Button: "const [clicks, setClicks] = useState(0);\n<Button label=\"눌러 보기\" kind=\"primary\" onClick={() => setClicks(n => n + 1)} />",
  Input: "const [value, setValue] = useState('예시');\n<Input label=\"입력 예시\" value={value} onChange={setValue} />",
  Checkbox: 'const [checked, setChecked] = useState(false);\n<Checkbox label="선택 예시" checked={checked} onChange={setChecked} />',
  Select: "const [value, setValue] = useState('one');\n<Select label=\"선택 예시\" value={value} onChange={setValue}\n  options={[{value: 'one', label: '첫 번째'}, {value: 'two', label: '두 번째'}]} />",
  RadioGroup: "const [value, setValue] = useState('one');\n<RadioGroup label=\"선택 예시\" value={value} onChange={setValue}\n  options={[{value: 'one', label: '첫 번째'}, {value: 'two', label: '두 번째'}]} />",
  Alert: '<Alert title="안내" message="컴포넌트 표현 예시입니다." tone="info" />',
  Stepper: '<Stepper current="second" steps={[{id: "first", label: "첫 단계"}, {id: "second", label: "다음 단계"}]} />',
  Summary: '<Summary title="예시 요약" items={[{label: "유형", value: "로컬 예시"}, {label: "선택", value: "첫 번째"}]} />',
  AssetImage: '<AssetImage src={localDataImage} alt="로컬 예시 이미지" width={240} height={120} />',
};

export function usageSnippet(name: string): string {
  if (!isComponentName(name)) throw new Error('지원하지 않는 React 컴포넌트입니다.');
  return `// 사용 예시 — 실제 실행 소스와 별도의 설명용 코드입니다.\n// 컴포넌트: @studio/approved-ui, 상태 훅: react\n${snippets[name]}`;
}
