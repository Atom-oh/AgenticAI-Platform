---
name: design-draft-html
description: Rules for generating a Bank UI draft screen as a single HTML file.
---

# Design Draft HTML

This is prompt guidance for the original `demo/uiux-studio/` ordinary-generation path. Refinement, wireframe/flow modes, and the main React workspace have separate contracts. These instructions do not prove that a generated draft passed validation.

Output ONE HTML file per variant with inline CSS. The original style requests Noto Sans KR through a Google Fonts `<link>`; that is an external dependency, so the output is not fully offline/self-contained. It does not authorize external fetching in the main workspace. Mobile frame: 390px wide, minimum height 844px.

Default layout: SINGLE-SCREEN with white card sections, 16px radius, on #f4f6f5. Use one section per concern (source account, recipient, amount, confirmation) and one primary CTA pinned at the bottom.

Hard rules:

- Hit targets >= 44px; body text >= 13px.
- No fake device chrome: status bar, clock/battery, or keyboard.
- Korean UI copy with fictional sample data. `홍길동` and `고객사 A 주거래 통장` are retained prompt fixtures, not actual customer records or product evidence.
- All colors/type/spacing come from design tokens; see `bank-design-system`.

Ordinary generation requests exactly three variants, each moving ONE axis:

1. `밀도` (density): compact ↔ airy, using spacing/card padding.
2. `강조` (emphasis): change which section dominates, such as amount versus recipient.
3. `흐름` (flow): default single-screen versus stepped wizard.

Name each variant with its axis, for example `v1-밀도-compact`. Korean axis values and the name example are runtime fixtures retained for compatibility. Do not use random component recombination to create near-identical outputs. Inspect the actual outputs; prompt instructions alone are not a variant-count or regression test.
