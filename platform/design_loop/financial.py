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
# neither field alone matching QUANTITY: a bare number in one field, a standalone unit annotation such as
# "(만원)", "[만원]" or "만원" in another (PR #30 review round 2, #4; review round 3, #1: any punctuation, not
# only parentheses, must count — the unit is set apart by NOT touching another Hangul syllable on either side,
# which is what distinguishes a genuine unit annotation from an ordinary word that happens to contain "원").
#
# review round 4, #2: a "bare" number is still financial even with a qualifier WORD directly next to it --
# "최대 999" (max 999), "999 이상" (999 or more) -- not just when it is the ENTIRE field; and a range such as
# "500~999" is one quantity split across a number field and a unit-annotation field just as a single number is.
# One qualifier word (never a multi-word phrase, to avoid matching an ordinary sentence that happens to hold a
# number) is allowed directly before and/or after the number/range, separated by at most one space.
_QUALIFIER = r"[가-힣]+"
_RANGE_SEP = r"\s?[~\-–—]\s?"
_NUMBER_OR_RANGE = _NUMBER + r"(?:" + _RANGE_SEP + _NUMBER + r")?"
_BARE_NUMBER = re.compile(r"(?:" + _QUALIFIER + r"\s?)?" + _NUMBER_OR_RANGE + r"(?:\s?" + _QUALIFIER + r")?")
_UNIT_ANNOTATION = re.compile(r"(?<![가-힣])(?:" + _MAGNITUDE + r"\s?)?" + _UNIT + r"(?![가-힣])")


def is_value(text):
    return isinstance(text, str) and VALUE.fullmatch(text) is not None


def contains_quantity(text):
    return isinstance(text, str) and QUANTITY.search(text) is not None


def is_bare_number(text):
    return isinstance(text, str) and _BARE_NUMBER.fullmatch(text.strip()) is not None


def has_unit_annotation(text):
    return isinstance(text, str) and _UNIT_ANNOTATION.search(text) is not None


def contains_split_quantity(strings):
    """True if a bare number and a standalone unit annotation both appear among sibling `strings` of the
    same record, so together they express a financial quantity that no single field reveals alone."""
    strings = list(strings)
    return any(is_bare_number(s) for s in strings) and any(has_unit_annotation(s) for s in strings)
