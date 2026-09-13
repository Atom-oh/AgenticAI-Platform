---
name: studio-draft-html
description: "Studio draft output contract: one self-contained HTML document, frame markers and one variation axis"
---

# Studio Draft HTML — Output contract

Runtime skill loaded by `studio/prompts.py`. Return one complete HTML document
inside one `html` fence, with only one explanatory sentence outside it.

- Inline `<style>` only. No external `<script src>`, `fetch` or external CSS.
  Use local/system fonts, preferring Noto Sans KR with sans-serif fallback;
  do not download font resources.
- Mobile frames are 390px wide and at least 844px tall. Place white section cards
  (16px radius) on #f4f6f5, with one primary CTA fixed at the bottom.
- Required frame marker: `<section data-step="n" data-screen="SCR-…">` per screen.
  Even a single-screen draft has `data-step="1"`. For `ux-flow`, number frames in
  procedure order and make the first element of each frame an `<h2>` step title.
- No fake device chrome: status bars, clocks, batteries or virtual keyboards.
- Use Korean copy and realistic synthetic data (`김아톰`, `아톰 주거래 통장 …`).
  Copy rates, periods, limits and preferential conditions exactly from the prompt's DesignSpec.
- Account/phone/resident/card identifiers must remain masked, for example
  `110-***-******` or `010-****-****`. Never fill every digit: the boundary gate
  scans the prior HTML again during repair/refinement and can refuse those values.
- Touch targets are at least 44px; body text is at least 13px.

Change only the requested axis; keep the other axes at their defaults. Preserve
the Korean axis keys used by the runtime:

- `밀도` (density): compact ↔ airy, through spacing and card padding.
- `강조` (emphasis): which section dominates, such as amounts versus conditions.
- `흐름` (flow): single screen by default, or a step-by-step wizard.

For `refine`, change only the instructed elements. Preserve all other markup,
order and text, and return the entire HTML document again.
