'use strict';
const step = (action, target, value) => value === undefined ? { action, target } : { action, target, value };
const rule = (id, title, steps) => ({ id, title, required: true, source: { kind: 'manual' }, steps });
const samples = [
  {
    id: 'amount-review',
    title: '납입금액 입력·확인',
    description: '금액과 기간을 바꾸면 확인 화면과 원금 합계까지 함께 바뀝니다.',
    checks: ['빈 값·범위 밖·소수 입력 시 진행 차단', '금액·기간 변경을 확인 화면에 전달', '뒤로 돌아가 수정한 값과 빠른 금액 버튼 반영'],
    guidance: '월 납입금액은 10,000~500,000원 범위의 정수입니다. 납입기간은 6개월 또는 12개월입니다. 총 납입원금은 월 납입금액 × 기간입니다. 이자·세금은 계산하지 않습니다. 다음·뒤로 이동해도 입력값을 보존합니다.',
    rules: [
      rule('amount-limits', '금액 범위와 형식 검사', [
        step('fill', 'amount', ''), step('expectEnabled', 'amount-next', false),
        step('fill', 'amount', '9999'), step('expectEnabled', 'amount-next', false),
        step('fill', 'amount', '500001'), step('expectEnabled', 'amount-next', false),
        step('fill', 'amount', '10000.5'), step('expectEnabled', 'amount-next', false),
        step('fill', 'amount', 'abc'), step('expectEnabled', 'amount-next', false),
        step('fill', 'amount', '10000'), step('expectEnabled', 'amount-next', true),
        step('fill', 'amount', '500000'), step('expectEnabled', 'amount-next', true),
      ]),
      rule('amount-propagation', '금액과 기간 전달', [
        step('fill', 'amount', '20000'), step('select', 'period', '6'), step('click', 'amount-next'),
        step('expectText', 'amount-summary', '20,000원'), step('expectText', 'amount-summary', '6개월'),
        step('expectText', 'amount-summary', '120,000원'),
      ]),
      rule('amount-edit', '뒤로 이동 및 빠른 금액 선택', [
        step('click', 'amount-300000'), step('expectValue', 'amount', '300000'),
        step('click', 'amount-next'), step('click', 'amount-back'), step('expectValue', 'amount', '300000'),
        step('click', 'amount-100000'), step('select', 'period', '6'), step('click', 'amount-next'),
        step('expectText', 'amount-summary', '100,000원'), step('expectText', 'amount-summary', '600,000원'),
      ]),
    ],
  },
  {
    id: 'required-consent',
    title: '필수 안내·동의',
    description: '필수 동의가 빠지면 진행을 막고, 선택 동의는 독립적으로 유지합니다.',
    checks: ['필수 두 항목 완료 전 진행 차단', '필수 일괄 확인이 선택 동의를 변경하지 않음', '동의 철회·뒤로가기·초기화 상태 반영'],
    guidance: '상품 안내와 이용 조건은 필수 확인 항목입니다. 새 소식 받기는 선택 항목으로, 미선택이어도 진행할 수 있습니다. 필수 일괄 확인 버튼은 선택 항목을 바꾸지 않습니다. 뒤로 가기는 상태를 보존하고 초기화는 모든 선택을 해제합니다. 샘플 문구는 법적 동의의 근거가 아닙니다.',
    rules: [
      rule('consent-required', '필수 누락 차단 및 회복', [
        step('expectEnabled', 'consent-next', false), step('check', 'consent-guide', true),
        step('expectEnabled', 'consent-next', false), step('check', 'consent-terms', true),
        step('expectEnabled', 'consent-next', true), step('click', 'consent-next'),
        step('expectText', 'consent-summary', '선택하지 않음'),
      ]),
      rule('consent-optional', '필수 일괄 확인과 선택 동의의 독립성', [
        step('click', 'consent-required'), step('expectChecked', 'consent-news', false),
        step('check', 'consent-news', true), step('click', 'consent-required'), step('expectChecked', 'consent-news', true),
        step('click', 'consent-next'), step('expectText', 'consent-summary', '선택함'),
      ]),
      rule('consent-revoke', '동의 수정과 초기화', [
        step('click', 'consent-required'), step('check', 'consent-news', true), step('click', 'consent-next'),
        step('click', 'consent-back'), step('expectChecked', 'consent-news', true),
        step('check', 'consent-guide', false), step('expectEnabled', 'consent-next', false),
        step('check', 'consent-guide', true), step('click', 'consent-next'), step('click', 'consent-reset'),
        step('expectChecked', 'consent-guide', false), step('expectChecked', 'consent-terms', false),
        step('expectChecked', 'consent-news', false), step('expectEnabled', 'consent-next', false),
      ]),
    ],
  },
  {
    id: 'product-compare',
    title: '상품 비교·선택',
    description: '카드형·목록형으로 바꾸어도 선택 상품과 기간이 유지됩니다.',
    checks: ['상품과 기간을 확인 화면에 전달', '카드형·목록형 변경 시 선택값 유지', '뒤로 돌아가 상품을 바꾸면 요약도 갱신'],
    guidance: '두 상품은 비교 UX용 가상 예시입니다. 기본형은 매월 정액, 자유형은 자유 납입 방식입니다. 기간은 6개월 또는 12개월입니다. 보기 방식은 카드형과 목록형 중 선택하며, 보기 방식 변경은 선택 상품과 기간을 초기화하지 않습니다. 실제 금리·가입 조건·추천 로직은 포함하지 않습니다.',
    rules: [
      rule('product-propagation', '선택 상품과 기간 전달', [
        step('click', 'choose-flexible'), step('select', 'product-period', '6'), step('click', 'product-next'),
        step('expectText', 'product-summary', '내맘대로 자유형'), step('expectText', 'product-summary', '자유 납입'),
        step('expectText', 'product-summary', '6개월'),
      ]),
      rule('product-layout', '보기 방식이 바뀌어도 선택 보존', [
        step('click', 'choose-flexible'), step('select', 'product-period', '6'), step('check', 'product-layout-list', true),
        step('expectText', 'product-selection', '내맘대로 자유형 · 6개월'),
        step('check', 'product-layout-cards', true), step('expectText', 'product-selection', '내맘대로 자유형 · 6개월'),
        step('expectValue', 'product-period', '6'),
      ]),
      rule('product-edit', '뒤로 가기와 재선택', [
        step('click', 'choose-flexible'), step('select', 'product-period', '6'), step('click', 'product-next'),
        step('click', 'product-back'), step('expectValue', 'product-period', '6'), step('click', 'choose-steady'),
        step('select', 'product-period', '12'), step('click', 'product-next'),
        step('expectText', 'product-summary', '차곡차곡 기본형'), step('expectText', 'product-summary', '매월 정액'),
        step('expectText', 'product-summary', '12개월'),
      ]),
    ],
  },
];
module.exports = { samples };
