# 화면 생성 출력 계약 (Skill · 위반 시 게이트 실패)

생성 결과는 아래 형식을 **정확히** 따른다. 이 계약은 Python 오케스트레이터(헤더·import 파서)와 Node 게이트 Lambda(tsc·eslint·axe)가 기계적으로 검사한다.

## O-1 파일 하나, 코드블록 하나

- 출력은 **TypeScript(TSX) 파일 1개**를 담은 단일 코드블록이다: ```tsx … ```.
- 코드블록 밖의 설명은 한 문장 이내로 제한한다. 코드블록을 두 개 이상 출력하지 않는다.
- 파일명은 `Screen.tsx`로 간주된다.

## O-2 첫 줄 Registry 헤더

- 파일의 **첫 줄**은 사용한 Registry 컴포넌트를 승인 버전과 함께 나열하는 주석이다.

```tsx
// registry: {이름}@{승인버전}, {이름}@{승인버전}
```

- 형식: `// registry: ` + `이름@버전`을 쉼표+공백으로 구분. 버전은 시스템 프롬프트의 승인 목록 제목(`### 이름@버전`)에 적힌 값을 **그대로** 쓴다. 이 문서에는 예시 버전을 적지 않는다 — 승인 목록만이 출처다.
- import 한 모든 Registry 컴포넌트가 헤더에 있어야 하고, 헤더의 컴포넌트는 모두 실제로 import·사용되어야 한다.
- 승인 목록에 없는 이름·버전을 헤더에 쓰면 Registry 게이트가 실패한다 (예: 승인 목록에 `Foo@v1`만 있는데 `Foo@v2`를 쓰는 경우, 또는 승인 목록에 없는 `Bar`를 쓰는 경우).

## O-3 import 규칙

- 허용되는 import 출처는 두 가지뿐이다: `'react'` 와 `'@atom/ui/<module>'`.
- 각 컴포넌트는 승인 목록에 명시된 **모듈 경로**에서 이름 그대로 가져온다:

```tsx
import { useState } from 'react';
import { Foo } from '@atom/ui/foo';        // 승인 목록의 "import:" 줄을 그대로 복사한다
```

- `@atom/ui` 이외의 서드파티 패키지(`axios`, `lodash`, `dayjs`, `styled-components` 등) import 금지 — 린트 게이트에서 실패한다.
- `require()`, 동적 `import()` 금지.

## O-4 컴포넌트 시그니처

- 기본 내보내기는 **props 없는 함수 컴포넌트**다:

```tsx
export default function Screen() {
  return ( … );
}
```

- 다른 export를 두지 않는다. 보조 컴포넌트는 같은 파일 안에서 비-export 함수로 정의한다.
- React 훅은 `react`에서 import한 것만 사용한다 (`useState`, `useMemo`).

## O-5 props는 승인 스키마대로

- 각 컴포넌트의 props는 승인 목록에 첨부된 **propsSchema**(JSON Schema)에 있는 속성만 사용한다. `required` 속성은 반드시 전달한다.
- 스키마에 없는 속성을 넘기면 타입 게이트(`tsc --noEmit`, strict)가 실패한다. 예: 스키마에 `theme` 속성이 없는 컴포넌트에 `theme="dark"`를 넘기면 실패. 다른 버전의 스키마를 기억으로 추측하지 말고 **현재 승인 목록의 스키마만** 본다.
- `enum`이 정의된 속성은 열거된 리터럴 값만 쓴다.
- 함수형 속성(`onClick`, `onChange`)은 화살표 함수로 전달하고, 본문은 상태 변경 또는 `console.log`만 허용한다.

## O-6 금지 API (SPEC §12.10)

- `fetch`, `XMLHttpRequest`, `WebSocket`, `navigator.sendBeacon` — 네트워크 호출 금지.
- `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie` — 브라우저 저장소 금지.
- `window.location` 조작, `eval`, `new Function` 금지.
- 위 항목은 린트 게이트(`no-restricted-globals`/`no-restricted-properties`)에서 오류로 처리된다.

## O-7 스타일

- Tailwind 유틸리티 클래스(`className`)를 우선 사용한다.
- 인라인 `style`은 CSS 변수 참조(`style={{ color: 'var(--muted)' }}`)와 정렬(`textAlign`)만 허용. hex/rgb 색상 리터럴 금지.
- 전역 CSS, `<style>` 태그, CSS-in-JS 라이브러리 금지.

## O-8 데이터

- 표시용 샘플 데이터는 파일 상단 상수(`const SAMPLE_ROWS`)로 두고 `// 합성데이터` 주석을 단다. 3~5행.
- 개인식별정보는 토큰/마스킹 형태만 (`CUST-3F9A`, `김*수`). 실제 형식의 주민번호·계좌번호를 만들지 않는다.
- 화면 코드가 금액·금리를 계산하지 않는다 — 전달된 값을 포맷만 한다.

## O-9 한국어

- 화면에 보이는 모든 텍스트(제목, 레이블, 버튼, 표 헤더, 빈 상태 문구)는 한국어다.
- 코드 주석은 한국어, 식별자(변수·함수명)는 영문 camelCase.

## 체크리스트 (출력 전 자체 점검)

1. 첫 줄이 `// registry: …` 이고 import 한 컴포넌트와 정확히 일치하는가
2. import 출처가 `react` 와 `@atom/ui/…` 만인가
3. `export default function Screen()` 하나만 export 하는가
4. props가 스키마에 있는 속성·enum 값만 쓰는가, required가 채워졌는가
5. DataTable에 `caption`, FormField에 `htmlFor`=입력 `id` 가 있는가
6. fetch/localStorage 등 금지 API가 없는가
7. 금액 `1,234,567원`, 날짜 `YYYY.MM.DD`, 배지 tone 규칙을 지켰는가
