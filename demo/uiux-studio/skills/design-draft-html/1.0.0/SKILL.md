---
name: design-draft-html
description: Rules for generating a Hana UI draft screen as self-contained HTML.
---

# Design Draft HTML

Output: ONE self-contained HTML file per variant. Inline CSS only. Google
Fonts Noto Sans KR via <link>. Mobile frame 390px wide, min-height 844px.

Layout default: SINGLE-SCREEN, card-sectioned (approved direction) — white
cards (radius 16px) on #f4f6f5 ground, one section per concern
(출금계좌 / 받는분 / 금액 / 확인), one primary CTA pinned at the bottom.

Hard rules:
- Hit targets >= 44px. Body text >= 13px.
- NO fake device chrome: no status bar, no clock/battery, no fake keyboard.
- Korean copy, real-sounding but sample data (김하나, 하나 주거래 통장 …).
- All colors/type/spacing from the design tokens (see hana-design-system).

Variation policy — produce exactly 3 variants per brief, each moving ONE axis:
1. 밀도 (density): compact ↔ airy — spacing scale and card padding shift.
2. 강조 (hierarchy): which section dominates (e.g. 금액 중심 vs 받는분 중심).
3. 흐름 (flow): single-screen (default) vs stepped wizard.
Name each variant with its axis (e.g. "v1-밀도-compact"). Do NOT produce
variants by randomly recombining components — near-identical output is a
failure.
