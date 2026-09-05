---
name: studio-a11y-finance
description: 금융권 웹접근성 체크(KWCAG 기반) — 스튜디오 시안용
---

# Accessibility — Finance

- 본문 대비 ≥ 4.5:1 (#17332f on #fbfcfb 통과; #8aa19c 텍스트를 #e6f3f2 위에 놓지 않는다).
- 모든 인터랙티브 요소: 44px 이상 터치 타깃, 보이는 포커스 스타일.
- 금액·계좌번호: 색만으로 의미를 주지 않고 레이블 텍스트를 함께 둔다.
- 입력은 `<label>`을 가진 `<input>`, 버튼은 `<button>`(styled div 금지). 동의는 `<input type="checkbox">`.
- 글자 크기는 rem, 본문 최소 13px, 시니어 모드 16px+.
