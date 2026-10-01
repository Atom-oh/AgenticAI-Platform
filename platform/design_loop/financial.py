"""Shared financial-quantity grammar (engine plan, File Structure; review round 28, AV2).

One grammar serves both PRD value-unit checks (E7) and the composition literal-financial-value check (E10): a
number with an optional sign and thousands separators, then an optional Korean magnitude, then a unit.
"""
from __future__ import annotations

import re

SIGNS = "+-−"
_NUMBER = r"[+\-−]?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_MAGNITUDE = r"(?:천|만|억|조)"
_UNIT = r"(?:%p|%|bp|원|개월|년|일|세|회|배)"
QUANTITY = re.compile(_NUMBER + r"\s?(?:" + _MAGNITUDE + r"\s?)?" + _UNIT)
# A PRD financial value: the quantity in full, optionally prefixed by "연" (per annum).
VALUE = re.compile(r"(?:연\s?)?" + QUANTITY.pattern)
# A unit annotation set apart from any number: "(만원)", "[만원]", "만원", "%" -- a unit that does NOT touch another
# Hangul syllable on either side, which distinguishes it from an ordinary word that happens to contain "원".
_UNIT_ANNOTATION = re.compile(r"(?<![가-힣])(?:" + _MAGNITUDE + r"\s?)?" + _UNIT + r"(?![가-힣])")
# Any decimal digit (Unicode \d: ASCII and full-width alike) -- the presence of a numeric quantity, wherever it sits.
_DIGIT = re.compile(r"\d")
# Field/label vocabulary that marks a value as financial even when no unit is written next to it.
FINANCIAL_TERMS = ("금리", "이율", "이자", "수익", "한도", "금액", "잔액", "잔고", "원금", "수수료", "납입", "적립",
                   "예치", "저축", "만기", "기간", "상환", "대출", "연체", "보험료", "세율", "세금", "우대", "할인",
                   "환율", "캐시백", "보너스", "가입액", "월액", "연액")


def is_value(text):
    return isinstance(text, str) and VALUE.fullmatch(text) is not None


def contains_quantity(text):
    return isinstance(text, str) and QUANTITY.search(text) is not None


def has_unit_annotation(text):
    return isinstance(text, str) and _UNIT_ANNOTATION.search(text) is not None


def has_number(text):
    return isinstance(text, str) and _DIGIT.search(text) is not None


def has_financial_term(text):
    return isinstance(text, str) and any(term in text for term in FINANCIAL_TERMS)


def is_strong_context(text):
    """A unit written in the text (with or without its number): financial wherever it appears on a page."""
    return contains_quantity(text) or has_unit_annotation(text)


def is_financial_context(text):
    return is_strong_context(text) or has_financial_term(text)


def needs_binding(strings, context=()):
    """True if displaying `strings` literally would show a financial value (PR #30 review 5, #2).

    The decision is "contains a number in a financial context", never "matches a value grammar": any digit in
    `strings`, whatever words or punctuation surround it ("월 최대 약 999", "최소 100에서 최대 999까지"), needs a PRD
    binding when `strings` themselves or the supplied `context` strings carry a unit (annotation or quantity) or
    a financial term. A unit-bearing quantity ("999만원") needs a binding on its own. Callers choose the context
    scope; ambiguity resolves to requiring a binding (SPEC 12.4: deterministic financial values)."""
    strings = [s for s in strings if isinstance(s, str)]
    if any(contains_quantity(s) for s in strings):
        return True
    if not any(has_number(s) for s in strings):
        return False
    return any(is_financial_context(s) for s in [*strings, *context])
