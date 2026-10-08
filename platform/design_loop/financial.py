"""Shared financial-quantity grammar (engine plan, File Structure; review round 28, AV2).

The PRD value grammar (E7): a number with an optional sign and thousands separators, then an optional Korean
magnitude, then a unit. `prd_extract` uses it to accept a cited value span; the span's whole-token boundary check
lives in `prd_extract._boundary`.

Displayed text is no longer scanned for numbers here. A composition may show a literal only when it has an approved
source (`provenance`: PRD binding, approved knowledge copy or engine template), so how a number is spelled does not
matter for approval (PR #30 review 7, #1 follow-up).
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
