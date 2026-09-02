---
name: hana-design-system
description: How to apply Hana design tokens and registry components in generated UI.
---

# Hana Design System

1. ALWAYS call `list_design_tokens` first. Tokens are law: every color, font
   size, and spacing value in your output must come from `tokens.color`,
   `tokens.type`, `tokens.space`. Never invent values.
2. Call `get_brand_guideline` for palette roles: `primary` (#008485) is the
   single accent — CTAs, active states, key highlights. `primaryDark` for
   pressed/secondary emphasis. `ink` for text, `bg` for page ground,
   `mist` for tinted surfaces.
3. Components in the registry are your vocabulary: `search_assets` /
   `get_component` give each component's purpose and usage rules. Prefer
   them; compose new elements only from token values.
4. Typography: Noto Sans KR only. Weight 900 for screen titles, 700 for
   section labels and CTAs, 400-500 for body.
