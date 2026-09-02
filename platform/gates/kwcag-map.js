'use strict';
// axe-core 규칙 ID → KWCAG 2.2 검사항목 참고 매핑.
// **지시적(indicative) 매핑**이다 — 공식 대응표가 아니며, 가장 가까운 검사항목 1개만 적는다.
const MAP = {
  // 5. 인식의 용이성
  'image-alt': '5.1.1 적절한 대체 텍스트 제공',
  'input-image-alt': '5.1.1 적절한 대체 텍스트 제공',
  'area-alt': '5.1.1 적절한 대체 텍스트 제공',
  'object-alt': '5.1.1 적절한 대체 텍스트 제공',
  'svg-img-alt': '5.1.1 적절한 대체 텍스트 제공',
  'role-img-alt': '5.1.1 적절한 대체 텍스트 제공',
  'image-redundant-alt': '5.1.1 적절한 대체 텍스트 제공',
  'video-caption': '5.2.1 자막 제공',
  'td-headers-attr': '5.3.1 표의 구성',
  'th-has-data-cells': '5.3.1 표의 구성',
  'scope-attr-valid': '5.3.1 표의 구성',
  'table-fake-caption': '5.3.1 표의 구성',
  'table-duplicate-name': '5.3.1 표의 구성',
  'kwcag-table-caption': '5.3.1 표의 구성',
  'kwcag-th-scope': '5.3.1 표의 구성',
  'list': '5.3.2 콘텐츠의 선형구조',
  'listitem': '5.3.2 콘텐츠의 선형구조',
  'definition-list': '5.3.2 콘텐츠의 선형구조',
  'dlitem': '5.3.2 콘텐츠의 선형구조',
  'color-contrast': '5.4.3 텍스트 콘텐츠의 명도 대비',
  'color-contrast-enhanced': '5.4.3 텍스트 콘텐츠의 명도 대비',
  'link-in-text-block': '5.4.1 색에 무관한 콘텐츠 인식',
  'audio-caption': '5.4.2 자동 재생 금지',
  'no-autoplay-audio': '5.4.2 자동 재생 금지',
  // 6. 운용의 용이성
  'nested-interactive': '6.1.1 키보드 사용 보장',
  'scrollable-region-focusable': '6.1.1 키보드 사용 보장',
  'tabindex': '6.1.2 초점 이동과 표시',
  'focus-order-semantics': '6.1.2 초점 이동과 표시',
  'kwcag-positive-tabindex': '6.1.2 초점 이동과 표시',
  'accesskeys': '6.1.4 문자 단축키',
  'meta-refresh': '6.2.1 응답시간 조절',
  'blink': '6.3.1 깜빡임과 번쩍임 사용 제한',
  'marquee': '6.3.1 깜빡임과 번쩍임 사용 제한',
  'bypass': '6.4.1 반복 영역 건너뛰기',
  'document-title': '6.4.2 제목 제공',
  'frame-title': '6.4.2 제목 제공',
  'frame-title-unique': '6.4.2 제목 제공',
  'empty-heading': '6.4.2 제목 제공',
  'heading-order': '6.4.2 제목 제공',
  'page-has-heading-one': '6.4.2 제목 제공',
  'link-name': '6.4.3 적절한 링크 텍스트',
  'button-name': '6.5.3 레이블과 네임',
  'input-button-name': '6.5.3 레이블과 네임',
  'target-size': '6.5.1 단일 포인터 입력 지원',
  // 7. 이해의 용이성
  'html-has-lang': '7.1.1 기본 언어 표시',
  'html-lang-valid': '7.1.1 기본 언어 표시',
  'valid-lang': '7.1.1 기본 언어 표시',
  'html-xml-lang-mismatch': '7.1.1 기본 언어 표시',
  'label': '7.3.2 레이블 제공',
  'select-name': '7.3.2 레이블 제공',
  'label-title-only': '7.3.2 레이블 제공',
  'form-field-multiple-labels': '7.3.2 레이블 제공',
  'autocomplete-valid': '7.3.4 반복 입력 정보',
  // 8. 견고성
  'duplicate-id': '8.1.1 마크업 오류 방지',
  'duplicate-id-active': '8.1.1 마크업 오류 방지',
  'duplicate-id-aria': '8.1.1 마크업 오류 방지',
  'aria-allowed-attr': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-allowed-role': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-required-attr': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-required-children': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-required-parent': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-roles': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-valid-attr': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-valid-attr-value': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-hidden-body': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-hidden-focus': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-input-field-name': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-toggle-field-name': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-command-name': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-progressbar-name': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-tooltip-name': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-prohibited-attr': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-conditional-attr': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-deprecated-role': '8.2.1 웹 애플리케이션 접근성 준수',
  'aria-text': '8.2.1 웹 애플리케이션 접근성 준수',
  'presentation-role-conflict': '8.2.1 웹 애플리케이션 접근성 준수',
};

const NOTE = 'axe 규칙 → KWCAG 2.2 검사항목 참고 매핑 (지시적 · 공식 대응표 아님)';

function kwcagFor(ruleId) {
  if (MAP[ruleId]) return MAP[ruleId];
  if (typeof ruleId === 'string' && ruleId.startsWith('aria-')) return '8.2.1 웹 애플리케이션 접근성 준수';
  return '';
}

module.exports = { MAP, NOTE, kwcagFor };
