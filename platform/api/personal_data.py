"""개인 금융데이터 — 정확 조회 (SPEC F3).

전부 합성데이터이며 식별자는 생성 시점부터 토큰 형태다 (§8).
벡터 검색을 절대 쓰지 않는다 — 키 기반 정확 조회만 (§12.1).
Phase 3에서 온프렘 플레인(RDS PostgreSQL, 프라이빗 서브넷)으로 이관한다 —
이 모듈의 함수 시그니처가 그 API 계약이다.
"""
from __future__ import annotations

# email → 합성 고객 프로필 (데모 계정별 결정론)
_PROFILES = {
    "demo@atomai.click": {
        "customerId": "CUST-0042", "name": "김데모", "segment": "우대",
        "product": {"code": "PRD-LN-001", "name": "아톰 안심전세대출 II", "baseRate": "4.5"},
        "account": {"accountId": "ACCT-0007", "balanceKrw": 12_450_000},
        "salaryTransferMonths": 6, "cardMonthlyKrw": 450_000,
        "autoTransferCount": 2, "isFirstHome": True, "isNewlywed": False,
        "jeonse": {"depositKrw": 300_000_000, "guaranteeRatio": "0.8",
                   "annualIncomeKrw": 68_000_000, "existingDebtKrw": 40_000_000},
    },
}
_DEFAULT = {
    "customerId": "CUST-0777", "name": "이방문", "segment": "일반",
    "product": {"code": "PRD-LN-001", "name": "아톰 안심전세대출 II", "baseRate": "4.5"},
    "account": {"accountId": "ACCT-0777", "balanceKrw": 3_200_000},
    "salaryTransferMonths": 1, "cardMonthlyKrw": 120_000,
    "autoTransferCount": 0, "isFirstHome": False, "isNewlywed": False,
    "jeonse": {"depositKrw": 200_000_000, "guaranteeRatio": "0.8",
               "annualIncomeKrw": 42_000_000, "existingDebtKrw": 0},
}


def exact_lookup(email: str) -> dict:
    """JWT로 검증된 이메일 키의 본인 레코드만 반환한다 — 엔타이틀먼트 스코핑."""
    return _PROFILES.get((email or "").lower(), _DEFAULT)
