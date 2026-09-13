---
name: studio-design-system
description: Atom Bank demo design tokens and palette rules for Studio HTML drafts
---

# Studio Design System

Runtime skill loaded by `studio/prompts.py`; applies to legacy HTML drafts.

1. Palette roles: `primary` #008485 is the single accent for CTA/active states/key
   highlights; `primaryDark` #00615f for pressed/secondary emphasis; `ink` #17332f
   for body text; `bg` #fbfcfb for page backgrounds; `mist` #e6f3f2 for tinted
   surfaces; `danger` #e90061 only for errors/warnings.
2. Use these tokens for colors/type and the 4px spacing scale
   `4/8/12/16/24/32`. Do not invent arbitrary values.
3. Typography: Noto Sans KR; screen titles weight 900, section labels/CTA 700,
   body 400–500, body size at least 13px.
4. User-selected palette/token/style-guide assets in the prompt override these defaults.
5. When registered components are supplied, use their names and roles first;
   build any new elements only from the token values.
