---
name: hana-design-system
description: How to apply Hana design tokens and registry components in generated UI.
---

# Hana Design System

Original HTML Studio prompt guidance. The token/component registry is reference metadata, not the executable `platform/react-kit/` package or proof of customer component approval.

1. Call `list_design_tokens` first. Every color, font size, and spacing value must come from `tokens.color`, `tokens.type`, or `tokens.space`; do not invent values.
2. Call `get_brand_guideline` for palette roles. `primary` (#008485) is the single accent for CTAs, active states, and key highlights; `primaryDark` is for pressed/secondary emphasis; `ink` is text, `bg` is page ground, and `mist` is tinted surfaces.
3. Use `search_assets` and `get_component` for component purpose/usage metadata. Prefer registered components and compose new elements only from token values.
4. Typography: Noto Sans KR, weight 900 for screen titles, 700 for section labels/CTAs, and 400–500 for body text. Font availability and rendered accessibility need verification; this instruction is not a test result.
