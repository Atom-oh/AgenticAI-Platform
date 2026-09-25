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
# A record split across sibling fields (e.g. a Summary item's {label, value}) can carry the same quantity with
# neither field alone matching QUANTITY: a bare number in one field, a parenthesized unit annotation such as
# "(만원)" or "(개월)" in another (PR #30 review round 2, #4).
_BARE_NUMBER = re.compile(_NUMBER)
_PAREN_UNIT = re.compile(r"\(\s*(?:" + _MAGNITUDE + r"\s?)?" + _UNIT + r"\s*\)")


def is_value(text):
    return isinstance(text, str) and VALUE.fullmatch(text) is not None


def contains_quantity(text):
    return isinstance(text, str) and QUANTITY.search(text) is not None


def is_bare_number(text):
    return isinstance(text, str) and _BARE_NUMBER.fullmatch(text.strip()) is not None


def has_parenthetical_unit(text):
    return isinstance(text, str) and _PAREN_UNIT.search(text) is not None


def contains_split_quantity(strings):
    """True if a bare number and a parenthesized unit annotation both appear among sibling `strings` of the
    same record, so together they express a financial quantity that no single field reveals alone."""
    strings = list(strings)
    return any(is_bare_number(s) for s in strings) and any(has_parenthetical_unit(s) for s in strings)
