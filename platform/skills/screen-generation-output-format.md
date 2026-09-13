# Screen generation output contract

Runtime skill for legacy F5 `screengen` and `screen_builder_agent`. The Python
Registry/import parser and Node gates check parts of this contract; the remaining
rules still guide generation and review. Use the current approved component
schemas. This is not the React workspace's `@studio/approved-ui` contract.

## O-1 One file, one code fence

- Return exactly one TypeScript/TSX file in one `tsx` code fence.
- Outside the fence, allow at most one sentence. Never output multiple fences.
- The assumed filename is `Screen.tsx`.

## O-2 First-line Registry header

The first source line lists every used Registry component and its approved version:

```tsx
// registry: {이름}@{승인버전}, {이름}@{승인버전}
```

- Use `// registry: ` followed by `이름@버전` pairs separated by comma and space.
- Copy versions exactly from the current approved prompt headings (`### 이름@버전`).
  No version in this skill overrides that list.
- Every imported Registry component appears in the header; every header entry is
  imported and used. Unapproved names/versions fail the Registry gate.
- For example, if only `Foo@v1` is approved, `Foo@v2` and an unlisted `Bar` are invalid.

## O-3 Imports

- Import only from `'react'` and the exact approved `'@atom/ui/<module>'` paths.
- Use named component imports and copy each record's `import:` path:

```tsx
import { useState } from 'react';
import { Foo } from '@atom/ui/foo';
```

- Do not import third-party packages such as `axios`, `lodash`, `dayjs` or
  `styled-components`. Do not use `require()` or dynamic `import()`.

## O-4 Component signature

Export one default function component with no props:

```tsx
export default function Screen() {
  return ( … );
}
```

Do not add other exports. Local helper functions/components may remain unexported;
UI elements must still follow P-7's approved-component rule. Use only hooks imported
from React, such as `useState` and `useMemo`.

## O-5 Approved props

- Use only properties in the current approved `propsSchema` and supply every
  required property. Do not infer schemas from memory or another version.
- Use only declared `enum` literals. Unknown props fail strict `tsc --noEmit` checks;
  for example, `theme="dark"` is invalid when `theme` is absent from the schema.
- Pass arrow functions to function props (`onClick`, `onChange`); their bodies
  only update state or call `console.log`.

## O-6 Prohibited APIs (storage restriction: SPEC §12.12)

- Network: `fetch`, `XMLHttpRequest`, `WebSocket`, `navigator.sendBeacon`.
- Storage: `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie`.
- Navigation/code execution: `window.location` mutation, `eval`, `new Function`.
- The lint runner restricts globals/properties; do not use aliases to bypass it.

## O-7 Styles

- Prefer Tailwind classes through `className`.
- Inline `style` only references CSS variables, such as
  `style={{ color: 'var(--muted)' }}`, or alignment (`textAlign`). No hex/rgb literals.
- No global CSS, `<style>` elements or CSS-in-JS libraries.

## O-8 Data

- Keep 3–5 display rows in top-level `const SAMPLE_ROWS` with `// 합성데이터`.
- Use only masked/tokenized identifiers such as `CUST-3F9A` and `김*수`;
  do not fabricate full-format resident IDs or accounts.
- Do not calculate financial amounts or rates; only format supplied values.

## O-9 Korean output

All visible titles, labels, buttons, table headers and empty states are Korean.
Generated code comments remain Korean; identifiers use English camelCase.
The English prose in this skill does not change that output-language requirement.

## Before returning output

1. Match the first-line `// registry: …` header to the imported/used components.
2. Confirm all imports use `react` or the approved `@atom/ui/…` path.
3. Export only `export default function Screen()`.
4. Check required props and enum literals against the current schemas.
5. Supply DataTable `caption` and matching FormField `htmlFor` / input `id`.
6. Remove prohibited APIs such as `fetch` and `localStorage`.
7. Check `1,234,567원`, `YYYY.MM.DD`, and the approved badge-tone conventions.
