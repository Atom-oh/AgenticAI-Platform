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


def is_value(text):
    return isinstance(text, str) and VALUE.fullmatch(text) is not None


def contains_quantity(text):
    return isinstance(text, str) and QUANTITY.search(text) is not None
