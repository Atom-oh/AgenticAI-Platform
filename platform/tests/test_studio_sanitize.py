# platform/tests/test_studio_sanitize.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from common import pii  # noqa: E402
from studio import sanitize  # noqa: E402


def test_scrub_masks_account_and_phone_keeps_korean_text():
    text = "계좌 110-234-567890 전화 010-1234-5678 안내드립니다"
    out = sanitize.scrub(text)
    assert "110-234-567890" not in out and "010-1234-5678" not in out
    assert "***-***-******" in out
    assert "안내드립니다" in out and "계좌" in out and "전화" in out


def test_scrub_no_identifiers_returns_unchanged_with_zero_count():
    text = "가입기간 선택 화면입니다"
    out, n = sanitize.scrub_result(text)
    assert out == text and n == 0


def test_scrub_result_is_gate_safe():
    text = "계좌 110-234-567890 전화 010-1234-5678 카드 4111-1111-1111-1111 주민 900101-1234567"
    out, n = sanitize.scrub_result(text)
    assert n > 0
    assert pii.scan_rules(out) == []
