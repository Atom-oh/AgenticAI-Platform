---
name: studio-a11y-finance
description: KWCAG-oriented financial accessibility rules for Studio HTML drafts
---

# Accessibility — Finance

Runtime skill loaded by `studio/prompts.py`; preserve these generation requirements.
A generated draft still needs review; this skill alone does not certify accessibility.

- Body contrast must be at least 4.5:1. Use #17332f on #fbfcfb; do not place
  #8aa19c text on #e6f3f2.
- All interactive elements need touch targets of at least 44px and visible focus styles.
- Amounts/accounts need text labels; color alone must not convey meaning.
- Inputs use `<input>` with `<label>`; actions use `<button>`, never a styled div.
  Consent uses `<input type="checkbox">`.
- Use rem font sizes: body at least 13px, senior mode at least 16px.
