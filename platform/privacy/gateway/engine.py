"""Model detections are data; only deterministic code may transform text."""
from __future__ import annotations

import re
import secrets
import time
from collections import Counter

from .errors import PrivacyFailure
from . import jsonio
from .models import Model, PROMPT_VERSION, TYPES, invoke_model
from .tokens import TOKEN, detector_view

MAX_INPUT_BYTES = 8192
RULES = [
    ("RRN", re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")),
    ("PHONE", re.compile(r"(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("CARD", re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)")),
    ("ACCOUNT", re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{2,6}(?:-\d{1,3})?(?!\d)")),
    ("ACCOUNT", re.compile(r"\b(?:CUST|ACCT)-\d{3,}\b")),
    ("PASSPORT", re.compile(r"\b[MSRODG]\d{8}\b")),
]
# Broader independent residual formats, rather than claiming a successful
# replacement with the very same regex proves that all PII has been found.
RESIDUALS = [
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)01\d(?:[- .]?\d){7,8}(?!\d)"),
    re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)"),
    re.compile(r"\b(?:CUST|ACCT)-\d{3,}\b"),
]
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_SIGN = r"(?:[+\-\u2212\uff0b\uff0d]\s*|(?:마이너스|플러스)\s*)?"
_UNITS = r"(?:[십백천만억조]\s*)+"
_KOREAN_MONEY = r"(?:[영공일이삼사오육칠팔구]\s*)?(?:[십백천만억조]\s*(?:[영공일이삼사오육칠팔구]\s*)?)+원"
_RATE_UNIT = r"(?:%\s*(?:p|포인트)?|퍼센트(?:\s*포인트)?|프로(?:\s*포인트)?|percent(?:age)?(?:\s+points?)?|basis\s+points?|bps?|pp)"
_CURRENCY = r"(?:KRW|USD|EUR|JPY|GBP|CNY|CHF|AUD|CAD|SGD|HKD|NZD|원화|미화|달러|유로|엔화|위안)"
# Protect entire spans, including a sign, compound units and rate periods.
# Short year counts are terms; four-digit birth/calendar years remain eligible
# for DOB removal rather than being mistaken for loan durations.
FACTS = re.compile(
    rf"(?<![\d.,])(?:"
    rf"(?:연|월|일)?\s*{_SIGN}{_NUMBER}\s*{_RATE_UNIT}"
    rf"|{_SIGN}[영공일이삼사오육칠팔구십백천만억조점]+\s*{_RATE_UNIT}"
    rf"|{_SIGN}(?:[₩$€£¥]\s*|{_CURRENCY}\s+){_SIGN}{_NUMBER}"
    rf"|{_SIGN}{_NUMBER}\s+{_CURRENCY}"
    rf"|{_SIGN}(?:{_NUMBER}\s*{_UNITS})+(?:{_NUMBER}\s*)?원?"
    rf"|{_SIGN}{_KOREAN_MONEY}"
    rf"|{_SIGN}{_NUMBER}\s*원"
    rf"|{_SIGN}{_NUMBER}\s*개월"
    rf"|{_SIGN}\d{{1,3}}(?:\.\d+)?\s*년)",
    re.IGNORECASE,
)


def _plausible_numeric_identifier(kind, value):
    """Do not delete ambiguous numeric expressions under an arbitrary label."""
    if not any(char.isdigit() for char in value):
        return True
    digits = "".join(char for char in value if char.isdigit())
    compact = re.sub(r"[\s().+\-]", "", value)
    if kind == "PHONE":
        return compact.isdigit() and 8 <= len(digits) <= 15
    if kind == "RRN":
        return re.fullmatch(r"\d{6}[- ]?[1-8\d*][\d*]{6}", value) is not None
    if kind == "CARD":
        return re.fullmatch(r"[\d*]{13,19}", compact) is not None
    if kind == "ACCOUNT":
        return (re.fullmatch(r"(?:CUST|ACCT)-\d{3,}", value) is not None
                or (re.fullmatch(r"[A-Za-z0-9 \-]+", value) is not None
                    and 10 <= len(digits) <= 34 and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)))
    if kind == "DOB":
        return (re.fullmatch(r"\d{6,8}", compact) is not None
                or re.fullmatch(r"\d{2,4}(?:년|[./-])\s*\d{1,2}(?:월|[./-])\s*\d{1,2}일?", value) is not None)
    if kind == "PASSPORT":
        return compact.isalnum() and any(char.isalpha() for char in compact) and 6 <= len(compact) <= 12
    if kind == "ADDRESS":
        return any(char.isalpha() for char in value)
    return False


def _nonce():
    # 128 random bits without decimal digit runs that generic PII/financial
    # validators could mistake for card numbers or generated amounts.
    alphabet = "abcdefghijklmnop"
    return "".join(alphabet[value >> 4] + alphabet[value & 15] for value in secrets.token_bytes(16))


def _entities(response: dict, model: Model, text: str) -> list[tuple[int, int, str]]:
    try:
        if response.get("model") != model.model_id or len(response["choices"]) != 1:
            raise ValueError()
        choice = response["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError()
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > 32768:
            raise ValueError()
        if content.strip() == "NONE":
            data = {"entities": []}
        elif content.lstrip().startswith("{"):
            data = jsonio.loads(content)
        else:
            entities = []
            for line in content.strip().splitlines():
                # An empty terminal column carries no entity data. Nonempty
                # extra columns, malformed rows and mixed NONE still fail.
                pieces = line.rstrip("\t").split("\t")
                if len(pieces) != 2:
                    raise ValueError()
                entities.append({"type": pieces[0], "original": pieces[1]})
            data = {"entities": entities}
        if not isinstance(data, dict) or set(data) != {"entities"} or not isinstance(data["entities"], list) or len(data["entities"]) > 64:
            raise ValueError()
        spans = []
        for entity in data["entities"]:
            if not isinstance(entity, dict) or set(entity) != {"type", "original"}:
                raise ValueError()
            kind, original = entity["type"], entity["original"]
            if kind not in TYPES or not isinstance(original, str) or not 1 <= len(original) <= 512 or original.strip() != original:
                raise ValueError()
            matches = list(re.finditer(re.escape(original), text))
            if not matches:
                raise ValueError()
            spans.extend((match.start(), match.end(), kind) for match in matches)
        return spans
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PrivacyFailure("invalid-model-output") from None


def deidentify(text: str, model: Model, *, infer=invoke_model, allow_tokens: bool = False) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise PrivacyFailure("invalid-input")
    if not model.enabled:
        raise PrivacyFailure("model-not-configured")
    protected_tokens = [(match.start(), match.end()) for match in TOKEN.finditer(text)]
    if protected_tokens and not allow_tokens:
        raise PrivacyFailure("reserved-token-input")
    model_text = detector_view(text) if allow_tokens else text
    started = time.monotonic()
    try:
        response = infer(model, model_text)
    except PrivacyFailure:
        raise
    except Exception:
        raise PrivacyFailure("model-unavailable") from None
    spans = list(dict.fromkeys(_entities(response, model, model_text)))
    model_count = len(spans)
    model_spans = {(start, end) for start, end, _ in spans}
    for kind, pattern in RULES:
        spans.extend((match.start(), match.end(), kind) for match in pattern.finditer(model_text))
    chosen = []
    # Preserve the model's type on identical spans. The regex account format
    # overlaps telephone numbers, so alphabetical label sorting is incorrect.
    for start, end, kind in sorted(dict.fromkeys(spans), key=lambda span: (span[0], -(span[1] - span[0]))):
        if any(start < token_end and end > token_start for token_start, token_end in protected_tokens):
            raise PrivacyFailure("protected-token-modification")
        if chosen and start < chosen[-1][1]:
            if end <= chosen[-1][1]:
                continue
            raise PrivacyFailure("overlapping-entities")
        chosen.append((start, end, kind))
    pieces, counts, tokens = [], Counter(), {}
    protected = [(match.start(), match.end()) for match in FACTS.finditer(text)]
    if any(start < fact_end and end > fact_start
           for start, end, _ in chosen for fact_start, fact_end in protected):
        raise PrivacyFailure("financial-facts-changed")
    if any(not _plausible_numeric_identifier(kind, text[start:end]) for start, end, kind in chosen):
        raise PrivacyFailure("ambiguous-numeric-entity")
    offset = 0
    for start, end, kind in chosen:
        value = text[start:end]
        key = (kind, value)
        if key not in tokens:
            token = f"⟨{kind}:{_nonce()}⟩"
            while token in text or token in tokens.values():
                token = f"⟨{kind}:{_nonce()}⟩"
            tokens[key] = token
        pieces.extend((text[offset:start], tokens[key]))
        counts[kind] += 1
        offset = end
    pieces.append(text[offset:])
    cleaned = "".join(pieces)
    if FACTS.findall(text) != FACTS.findall(cleaned):
        raise PrivacyFailure("financial-facts-changed")
    residual_view = detector_view(cleaned)
    residual = sum(len(list(pattern.finditer(residual_view))) for pattern in RESIDUALS)
    if residual:
        raise PrivacyFailure("residual-pii")
    return {"ok": True, "text": cleaned, "evidence": {
        "status": "pass", "processor": "eks-sllm", "method": "redaction",
        "model": model.id, "modelId": model.model_id, "modelRevision": model.revision,
        "entityCounts": [{"type": kind, "count": count} for kind, count in sorted(counts.items())],
        "total": sum(counts.values()), "modelDetections": model_count,
        "ruleSupplements": sum((start, end) not in model_spans for start, end, _ in chosen),
        "sourceChars": len(text), "outputChars": len(cleaned), "ruleResidualCount": residual,
        "independentNer": "not-configured", "promptVersion": PROMPT_VERSION,
        "latencyMs": round((time.monotonic() - started) * 1000),
        "scope": "configured-identifiers-not-a-guarantee-of-anonymity",
    }}
