---
name: a11y-finance
description: Finance accessibility checklist (KWCAG-informed) for generated drafts.
---

# Accessibility — Finance

Original HTML Studio prompt guidance, not an automated accessibility certification or the main React workspace's release gate. Keep accessibility defects visible for review.

- Text contrast >= 4.5:1 against its background (#17332f on #fbfcfb is the intended pairing; never place #8aa19c text on #e6f3f2).
- Every interactive element: >= 44px hit target and visible focus style.
- Amounts and account numbers: never convey meaning through color alone; pair with label text.
- Form inputs carry `<label>`; buttons are `<button>`, not styled `<div>`, in final production handoff. Any draft using a div must identify that limitation; it is not a production accessibility pass.
- Font sizes scale with rem; minimum body 13px, senior mode 16px+.
