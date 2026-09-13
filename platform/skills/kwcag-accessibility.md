# KWCAG accessibility rules

Runtime skill for legacy F5 screen generation. These KWCAG 2.2-oriented rules
remain generation requirements even where the stub/jsdom gate cannot measure
them. The gate's mapping is indicative, not an official equivalence table or
accessibility certification. Real React workspace checks use a separate browser path.

## A-1 Alternative text (KWCAG 5.1.1)

- Every `<img>` has `alt`: describe meaningful images; use `alt=""` for decoration.
- Give icon-only buttons an action name, such as `aria-label="닫기"`.
- SVG icons use either `role="img"` with `aria-label`, or `aria-hidden="true"`.

## A-2 Labels (KWCAG 7.3.2, 6.5.3)

- Wrap inputs/Select in FormField; its `htmlFor` must exactly match the input `id`.
- Always provide visible label text; placeholders do not replace labels.
- Buttons need nonempty labels that supply their accessible names.
- IDs must be unique within the screen (8.1.1).

## A-3 Contrast (KWCAG 5.4.3)

- Require at least 4.5:1 for body text and 3:1 for large text (18pt or larger).
  Use design tokens such as `var(--text)` and `var(--muted)`, not hex literals.
- Express status through approved Badge `tone` and a text label (5.4.1).
- jsdom lacks browser layout, so contrast can be `미판정(incomplete)`.
  Do not describe that item as passed. The current gate's aggregate `ok` checks
  violations separately and does not imply every incomplete item passed.

## A-4 Focus and keyboard (KWCAG 6.1.1, 6.1.2)

- Match focus order to DOM order; never use positive `tabIndex`.
- Clickable actions use `<button>` or Button, not `<div onClick>` or `<span onClick>`.
- Avoid mouse-only row actions; put a button inside the row.
- Do not remove the focus indicator with `outline: none`.

## A-5 Tables (KWCAG 5.3.1)

- Use DataTable with a required `caption`, such as `caption="여신 심사 결과 목록"`.
- A direct `<table>` needs `<caption>` and `<thead>` with `<th scope="col">`.
  Never use tables for page layout.
- Use `-` or `해당없음` for otherwise empty data cells.

## A-6 Headings and structure (KWCAG 6.4.2, 5.3.2)

- Use one PageHeader (`<h1>`) per screen; section titles use Card `title` (`<h2>`).
- Mark lists with `<ul>/<ol>` and paragraphs with `<p>`; do not stack `<br>` for spacing.
- Keep visual and DOM order aligned; do not reverse them through CSS.

## A-7 Status announcements (KWCAG 8.2.1)

- Put changing result/status text in Badge (`role="status"`) or a `role="status"` region.
- Use Alert (`role="alert"`) for errors.
- Mark required inputs and explain the requirement in text, beyond an asterisk.
  Pass `required` to FormField only when its approved schema supports that prop;
  otherwise preserve the required semantics on the supported input/markup.

## A-8 Language and document (KWCAG 7.1.1)

- Do not manage `<html lang>` inside the screen component; the gate wraps it in `lang="ko"`.
- Expand foreign abbreviations in Korean at first use, such as `LTV(담보인정비율)`.

## What the gate measures

`gates/a11y.js` statically renders semantic UI stubs into jsdom, then runs axe-core
`wcag2a`/`wcag2aa` and structural checks for table captions, header associations and
positive `tabIndex`. `gates/kwcag-map.js` provides the indicative mapping.
This path does not verify real component implementations, browser contrast or
dynamic focus movement. Preserve unmet and incomplete findings explicitly.
