# Atom Bank publishing conventions

Runtime skill for legacy F5 `screengen` and `screen_builder_agent`. Preserve all
P-x requirements when editing. These are generation/review rules; not every rule
is automatically checked. The approved Registry schemas and import paths in the
current prompt are authoritative. This skill does not define the separate
`@studio/approved-ui` React workspace contract.

## P-1 Layout grid

- Order content as PageHeader → optional query controls → results → actions.
- Use a 12-column layout and spacing in multiples of 8px (`8, 16, 24, 32`).
- Use exactly one primary button per screen, such as `조회`; use secondary/ghost
  for others, using only variants supported by the approved schema.
- Align result actions at the lower right; separate destructive actions
  (delete/cancel) at the far left.
- Put `총 N건` above a results table. Compute the count from the array's `length`;
  do not hardcode it.

## P-2 Amounts

- Format amounts with thousands separators and `원`, for example `1,234,567원`,
  using `toLocaleString('ko-KR')`.
- Use abbreviated 억/만 units only in summary metrics; table cells show full won amounts.
- Right-align all numeric cells (amounts, rates, counts); left-align text.
- Show negative values as `-1,000원`; retain the minus sign rather than relying on red.
- Show interest rates to two decimal places with `%`, such as `3.25%`; limits use `원`.
- Do not invent or calculate financial amounts, interest rates or limits in screen
  code. Only format supplied values (SPEC §12.4).

## P-3 Dates and times

- Date: `YYYY.MM.DD`, such as `2026.09.02`; date/time: `YYYY.MM.DD HH:mm`.
- Separate a range with spaces around `~`: `2026.09.01 ~ 2026.09.30`.
- Include units/formats in table headings: `신청일 (YYYY.MM.DD)`, `대출금액 (원)`.

## P-4 Status badges

Use the approved Badge `tone` values; do not supply arbitrary colors.

| Business meaning | Korean label | Tone |
| --- | --- | --- |
| Approved / complete / normal | `승인` | `success` |
| Reviewing / running / waiting | `심사중` | `warning` |
| Rejected / refused / error | `반려` | Approved error tone: `critical` in the live seed, `danger` in the isolated fixture |
| Held / canceled / not applicable | `보류` | `neutral` |
| Information / reference | `안내` | `info` |

Never pass a tone absent from the current approved `propsSchema`. Keep the text
label visible; color alone must not carry meaning (KWCAG 5.4.1).

## P-5 Copy and terminology

- All user-visible copy is Korean. Use short action labels (normally 2–4 Korean
  characters): `조회`, `초기화`, `승인 요청`, `엑셀 저장`.
- Append `*` to required-field labels and show
  `* 표시는 필수 입력 항목입니다.` at the top of the screen.
- Empty results: `조회 결과가 없습니다.`; loading: `조회 중입니다…`.
- Show errors in an Alert at the top and associate field errors with the input.
  Use FormField `error` only when the approved schema supports it.
  Use the actual approved error prop: the live seed uses `severity="error"`;
  the isolated fixture uses `kind="error"`. Do not mix the two schemas.

## P-6 Prohibited copy

Do not include:

- Guaranteed performance claims: `확정`, `보장`, `무조건`, `최고 수익`, `반드시 오른다`, `손실 없음`.
- Investment solicitation: `지금 가입하세요`, `놓치면 후회`, `강력 추천`.
- Real bank/product/regulation names in generated demo copy (SPEC §12.10).
  Use fictional products such as `안심전세대출 II`.
- Raw personal identifiers: full names, resident IDs, accounts or telephone numbers.
  If needed, display only masked/tokenized values supplied as data, such as
  `김*수`, `110-***-****`, `CUST-…`; never reconstruct originals.

## P-7 Code

- Use only Registry-approved UI components. If one is unavailable, use semantic
  HTML (`<section>`, `<p>`, `<ul>`) rather than inventing a new UI component.
- Keep 3–5 synthetic sample rows in a top-level `const SAMPLE_ROWS = [...]` and
  retain the exact comment `// 합성데이터`.
- Event handlers only update state or log to the console; no network or browser
  storage access (storage restriction: SPEC §12.12).
- Inline `style` is limited to CSS-variable references (`var(--…)`) or alignment
  (`textAlign`). Do not hardcode hex colors.
