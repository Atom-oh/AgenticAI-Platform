"""결정론적 계산엔진 단위테스트 (SPEC F3 — 계산엔진은 순수 함수 + 단위테스트 필수)."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onprem.calc_engine import (jeonse_loan_limit, preferential_rate,  # noqa: E402
                                verify_no_generated_numbers)
from onprem.masking import mask, unmask  # noqa: E402


def test_preferential_all_met():
    p = {"salaryTransferMonths": 6, "cardMonthlyKrw": 500_000,
         "autoTransferCount": 4, "isFirstHome": True}
    r = preferential_rate(p, "4.5")
    # 0.3+0.2+0.1+0.2=0.8 → 상한 0.6 적용 → 4.5-0.6=3.9
    assert r.value == Decimal("3.90")
    assert any("상한" in s.label for s in r.steps)


def test_preferential_partial():
    p = {"salaryTransferMonths": 6, "cardMonthlyKrw": 200_000,
         "autoTransferCount": 1, "isFirstHome": False}
    r = preferential_rate(p, "4.5")
    assert r.value == Decimal("4.20")  # -0.3만 충족
    labels = [s.value for s in r.steps]
    assert any("미충족" in v for v in labels)


def test_preferential_none_met():
    r = preferential_rate({}, "4.5")
    assert r.value == Decimal("4.50")


def test_jeonse_limit_deposit_bound():
    r = jeonse_loan_limit(300_000_000, "0.8", 100_000_000, 40_000_000)
    # min(2.4억, 4억)=2.4억 - 0.4억 = 2.0억
    assert r.value == Decimal("200000000")
    assert len(r.steps) == 4


def test_jeonse_limit_income_bound_and_floor():
    r = jeonse_loan_limit(500_000_000, "0.9", 30_000_000, 200_000_000)
    # min(4.5억, 1.2억)=1.2억 - 2억 → 음수 방지 0
    assert r.value == Decimal("0")


def test_output_verifier_catches_invented_numbers():
    allowed = ["3.90", "200,000,000", "4.5"]
    ok_text = "적용금리는 3.90%이며 한도는 200,000,000원입니다."
    assert verify_no_generated_numbers(ok_text, allowed) == []
    bad_text = "적용금리는 3.15%로 예상됩니다."
    assert "3.15" in verify_no_generated_numbers(bad_text, allowed)


def test_mask_unmask_roundtrip():
    text = "고객 CUST-0042 (demo@atomai.click) 계좌 ACCT-0007 우대금리 안내"
    r = mask(text, {"customerName": "김데모"})
    assert "CUST-0042" not in r.text and "ACCT-0007" not in r.text
    assert "demo@atomai.click" not in r.text
    fields = {m["field"] for m in r.masked_fields}
    assert {"customer_id", "account_id", "email"} <= fields
    assert unmask(r.text, r.mapping) == text
