"""개인 금융데이터 정확 조회 — 온프렘 플레인 소속 (SPEC §3.2).

DATA_BACKEND=rds 면 프라이빗 RDS PostgreSQL에서, 아니면 컨테이너 내 합성 레코드에서
키 기반 정확 조회한다. 벡터 검색 금지 (§12.1). 전부 합성데이터·토큰 식별자.
"""
from __future__ import annotations

import json
import os

_SYNTH = {
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


def _rds_conn():
    import pg8000.native
    return pg8000.native.Connection(
        user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"], database=os.environ.get("PGDATABASE", "bank"))


def ensure_rds_seed() -> None:
    """RDS에 합성 테이블·레코드를 멱등 시드한다 (컨테이너 기동 시 1회)."""
    con = _rds_conn()
    con.run("""CREATE TABLE IF NOT EXISTS customer_profile (
        email TEXT PRIMARY KEY, profile JSONB NOT NULL)""")
    for email, p in _SYNTH.items():
        con.run("""INSERT INTO customer_profile(email, profile) VALUES(:e, :p)
                   ON CONFLICT (email) DO UPDATE SET profile = :p""",
                e=email, p=json.dumps(p, ensure_ascii=False))
    con.close()


def exact_lookup(email: str) -> dict:
    email = (email or "").lower()
    if os.environ.get("DATA_BACKEND") == "rds":
        con = _rds_conn()
        rows = con.run("SELECT profile FROM customer_profile WHERE email = :e", e=email)
        con.close()
        if rows:
            p = rows[0][0]
            return p if isinstance(p, dict) else json.loads(p)
        return _DEFAULT
    return _SYNTH.get(email, _DEFAULT)
