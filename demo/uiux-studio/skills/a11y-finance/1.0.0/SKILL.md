---
name: a11y-finance
description: 금융권 웹접근성 체크리스트 (KWCAG-informed) for generated drafts.
---

# Accessibility — Finance

- Text contrast >= 4.5:1 against its background (#17332f on #fbfcfb passes;
  never place #8aa19c text on #e6f3f2).
- Every interactive element: >= 44px hit target, visible focus style.
- Amounts and account numbers: never color-only meaning; pair with label text.
- Form inputs carry <label>; buttons are <button>, not styled <div>, in
  final production handoff (drafts may use divs but must note it).
- Font sizes scale with rem; minimum body 13px, 시니어 모드 16px+.
