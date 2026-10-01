"""Shared financial-quantity grammar and the displayed-numeral rule (engine plan, File Structure; review round 28,
AV2; PR #30 review 6, #1).

Two things live here:

* The PRD value grammar (E7): a number with an optional sign and thousands separators, then an optional Korean
  magnitude, then a unit. `prd_extract` uses it to accept a cited value span.
* The composition rule (E10, the plan's global PRD-binding constraint, SPEC 12.4): **every numeric quantity in
  displayed literal text needs a PRD binding.** No "financial context" is inferred -- context heuristics kept
  missing split, qualified and spelled-out values (R2#4, R3#1, R4#2, R5#2, R6#1). A number is exempt only when it
  sits inside one of the explicit non-financial display forms in `NONFINANCIAL_FORMS`; numeral forms that cannot
  be classified (circled, Roman, CJK ideographic, vulgar fractions) are rejected outright.
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


# ---- displayed numerals ---------------------------------------------------------------------------------------

# The ONLY numbers a composition may show literally. Each entry is (name, pattern, why). Derived from what the
# engine's own clean controls and the seed screens need -- the seed (seed/design_poc/ontology.json) shows no
# literal number at all, and the one non-financial form the tests rely on is a progress-step counter. Keep it
# minimal: anything else, including "N/M", counts and dates, needs a PRD binding.
NONFINANCIAL_FORMS = (
    ("step-counter", re.compile(r"(?<![\d.,])\d{1,2}\s?단계"),
     "progress step of a multi-step procedure (\"3단계 중 1단계\"); the step index is not a product value"),
)

# Decimal digits of any script (ASCII and full-width alike): Python's Unicode `\d`.
_DIGITS = re.compile(r"\d+")

# Sino-Korean numerals (일/이/삼 ... 십/백/천 ... 만/억/조). 영/공 are left out: "영원", "공지" are ordinary words and a
# spelled-out zero is not a displayable amount.
_DIGIT_SYL = "일이삼사오육륙칠팔구"
_SMALL = {"십": 1, "백": 2, "천": 3}
_BIG = {"만": 1, "억": 2, "조": 3}
_NUMERAL_SYL = frozenset(_DIGIT_SYL) | frozenset(_SMALL) | frozenset(_BIG)
# Units/counters that make a single numeral syllable a quantity ("삼개월", "만원", "오 년"). Single-syllable counters
# such as 회/건/개/일/세/번/분 are deliberately absent for a lone numeral syllable: 사회, 조건, 이건, 조회, 오일, 조세,
# 이번 and 구분 are words. A multi-syllable numeral is a quantity on its own, whatever follows.
_UNITS_AFTER_DIGIT = ("개월", "년", "퍼센트", "%")
_UNITS_AFTER_MAGNITUDE = ("원", "개월", "년", "퍼센트", "%")
_UNITS_ANY = ("원", "개월", "년", "퍼센트", "%", "세", "회", "건", "개", "배", "일")
# Word prefixes that parse as a multi-syllable numeral but are ordinary words. They apply only when no unit
# follows the numeral part ("일조원" is still 1조원, "천사백만원" still parses past "천사").
_NUMERAL_LOOKALIKES = ("일조", "천사", "천만에", "천만다행", "조만간", "이만")
# Native Korean numerals before a period/age/amount counter ("한 달", "세달", "열 살").
# The counter may carry one particle ("한 달간", "세 살부터") but not run on into another word ("네 해지", "한 살림").
_NATIVE = re.compile(r"(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰)\s?(?:달|해|살|개월)"
                     r"(?:간|에|의|은|는|이|을|도|만|째|동안|부터|까지)?(?![가-힣])")
_HANGUL_WORD = re.compile(r"[가-힣]+")


def _sino_numeral(s):
    """True if `s` is a well-formed Sino-Korean numeral: groups of [digit]천[digit]백[digit]십[digit] in strictly
    descending small magnitudes, separated by strictly descending 만/억/조. "천천", "사이", "일이" are not numerals."""
    if not s:
        return False
    last_big, i = 99, 0
    while i < len(s):
        last_small, digit, started = 99, False, False
        while i < len(s) and s[i] not in _BIG:
            ch = s[i]
            if ch in _DIGIT_SYL:
                if digit:
                    return False                      # two digits in a row ("사이", "일이")
                digit = True
            else:
                rank = _SMALL[ch]
                if rank >= last_small:
                    return False                      # "천천", "십백"
                last_small, digit = rank, False
            started = True
            i += 1
        if i < len(s):
            rank = _BIG[s[i]]
            if rank >= last_big:
                return False                          # "만만", "만억"
            last_big = rank
            i += 1
        elif started and digit and last_small == 99 and last_big != 99:
            return False                              # a bare digit after 만/억/조 ("만일", "조사", "만이")
    return True


def _starts_with_unit(rest, units):
    return any(rest.startswith(u) for u in units)


def _hangul_numerals(text):
    """`[(word, unit_bearing)]`: Korean numerals in quantity position -- a multi-syllable Sino-Korean numeral at the
    start of a word ("삼십만원", "구백구십구만", "오천원"), one numeral syllable followed by a unit ("만원", "삼개월",
    "오 년"), or a native numeral before a period/age counter ("한 달", "열 살")."""
    found = []
    for m in _HANGUL_WORD.finditer(text):
        word = m.group()
        run = 0
        while run < len(word) and word[run] in _NUMERAL_SYL:
            run += 1
        following = text[m.end():].lstrip()
        for i in range(run, 0, -1):
            numeral, rest = word[:i], word[i:]
            unit_next = (_starts_with_unit(rest, _UNITS_ANY)
                         or not rest and _starts_with_unit(following, _UNITS_ANY))
            if i >= 2 and _sino_numeral(numeral):
                if not unit_next and any(word.startswith(w) for w in _NUMERAL_LOOKALIKES):
                    continue
                found.append((word, unit_next))
                break
            if i == 1:
                units = _UNITS_AFTER_MAGNITUDE if numeral in _SMALL or numeral in _BIG else _UNITS_AFTER_DIGIT
                if _starts_with_unit(rest, units) or not rest and _starts_with_unit(following, units):
                    found.append((word, True))
    found += [(m.group(), True) for m in _NATIVE.finditer(text)]
    return found


def _unsupported(text):
    """Numeric characters that are not decimal digits: ①, Ⅻ, ½, 一, 万, superscripts ... (fail closed)."""
    return [ch for ch in text if ch.isnumeric() and not ch.isdecimal()]


def _mask_nonfinancial(text):
    for _, pattern, _ in NONFINANCIAL_FORMS:
        text = pattern.sub(lambda m: " " * len(m.group()), text)
    return text


def numerals(text):
    """`[(code, token)]` for every number displayed in `text` that is not inside an allowed non-financial form.

    code `literal-financial-value`: decimal digits (any script) or a Korean numeral word in quantity position.
    code `unsupported-numeral-form`: a numeric character the engine cannot classify (rejected, fail closed)."""
    if not isinstance(text, str):
        return []
    masked = _mask_nonfinancial(text)
    out = [("literal-financial-value", t) for t in _DIGITS.findall(masked)]
    out += [("literal-financial-value", t) for t, _ in _hangul_numerals(masked)]
    out += [("unsupported-numeral-form", t) for t in _unsupported(masked)]
    return out


def contains_quantity(text):
    """A unit-bearing quantity, digits or Korean numerals ("999만원", "삼십만원"); used where a value is a reviewed
    design token from a closed set (enum props) rather than free display text."""
    if not isinstance(text, str):
        return False
    return QUANTITY.search(text) is not None or any(unit for _, unit in _hangul_numerals(text))


def needs_binding(strings):
    """True if displaying any of `strings` literally would show a number (see `numerals`)."""
    return any(numerals(s) for s in strings if isinstance(s, str))
