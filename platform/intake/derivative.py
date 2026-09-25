"""`identifier-normalization-1`: de-identified derivatives and the residual scan.

The private deny-list comes only from private configuration named by an env var
(`INTAKE_DENYLIST_PARAM`: SSM SecureString, or `INTAKE_DENYLIST_KEY`: private S3
object). It is never read from Git or a request body, and its terms are never
written to a record, receipt, log or error. Numbers are left untouched so that
financial values stay verbatim (P-02).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

from intake.inspect import _pii, find_terms
from workspace import ontology_schema as schema

PROFILE = "identifier-normalization-1"
ORG_ALIAS = "고객사 A"
LINK_ALIAS = "[내부 링크]"
MAX_ENTRIES = 5000
_KINDS = {"org": ORG_ALIAS, "name": ORG_ALIAS, "url": LINK_ALIAS, "host": LINK_ALIAS}
INTERNAL_URL = re.compile(r"https?://[^\s]*\.(?:internal|corp|local)\b[^\s]*", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class DenylistUnavailable(Exception):
    """The private deny-list could not be read or validated. Admission is blocked."""

    code = "denylist-unavailable"

    def __init__(self):
        super().__init__("The private identifier deny-list is unavailable")


class NormalizationBlocked(Exception):
    """A residual identifier or PII hit survived normalization. Counts only."""

    code = "normalization-blocked"

    def __init__(self, counts):
        self.counts = dict(sorted(counts.items()))
        self.types = sorted(self.counts)
        super().__init__("Normalization left residual identifiers: " +
                         ", ".join(f"{kind}={count}" for kind, count in self.counts.items()))


class NormalizationInvariant(Exception):
    """Normalization would change text outside a replaced identifier (for example a
    numeric/financial value), or the page buffer could not be kept consistent."""

    code = "normalization-invariant"

    def __init__(self):
        super().__init__("Normalization would change the source outside replaced identifiers")


def _entries(raw):
    try:
        values = json.loads(raw) if isinstance(raw, (str, bytes)) else None
    except ValueError:
        values = None
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_ENTRIES:
        raise DenylistUnavailable()
    entries = []
    for value in values:
        if (not isinstance(value, dict) or not {"term"} <= value.keys() <= {"term", "alias", "kind"}
                or not isinstance(value["term"], str) or not value["term"].strip() or len(value["term"]) > 200
                or value.get("kind", "org") not in _KINDS
                or "alias" in value and (not isinstance(value["alias"], str) or not value["alias"].strip()
                                         or len(value["alias"]) > 60)):
            raise DenylistUnavailable()
        term = unicodedata.normalize("NFC", value["term"].strip())
        entry = {"term": term, "alias": value.get("alias") or _KINDS[value.get("kind", "org")]}
        if value.get("kind") == "host":
            entry["host"] = True
        entries.append(entry)
    aliases = {entry["alias"] for entry in entries}
    if any(find_terms(alias, entries) for alias in aliases):
        raise DenylistUnavailable()  # an alias that re-matches a term cannot converge
    return entries


def load_denylist(*, ssm=None, s3=None) -> list:
    """Read the private deny-list; any missing/unreadable/invalid config is unavailable."""
    parameter, key = os.environ.get("INTAKE_DENYLIST_PARAM", ""), os.environ.get("INTAKE_DENYLIST_KEY", "")
    try:
        if parameter:
            if ssm is None:
                import boto3
                ssm = boto3.client("ssm")
            raw = ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"]
        elif key:
            if key.startswith("s3://"):
                bucket, _, name = key[5:].partition("/")
            else:
                bucket, name = os.environ.get("WORKSPACE_BUCKET", ""), key
            if not bucket or not name:
                raise DenylistUnavailable()
            if s3 is None:
                import boto3
                s3 = boto3.client("s3")
            with s3.get_object(Bucket=bucket, Key=name)["Body"] as body:
                raw = body.read(4_000_001)
            if len(raw) > 4_000_000:
                raise DenylistUnavailable()
        else:
            raise DenylistUnavailable()
    except DenylistUnavailable:
        raise
    except Exception:  # noqa: BLE001 - any read failure blocks; never log the payload
        raise DenylistUnavailable() from None
    return _entries(raw)


def canonical_entries(denylist):
    """Apply per-kind default aliases to an already private, validated list."""
    if not denylist:
        raise DenylistUnavailable()
    result = []
    for entry in denylist:
        if not isinstance(entry, dict) or not isinstance(entry.get("term"), str) or not entry["term"].strip():
            raise DenylistUnavailable()
        kind = entry.get("kind", "host" if entry.get("host") else "org")
        if kind not in _KINDS:
            raise DenylistUnavailable()
        item = {"term": unicodedata.normalize("NFC", entry["term"].strip()),
                "alias": entry.get("alias") or _KINDS[kind]}
        if kind == "host":
            item["host"] = True
        result.append(item)
    return result


def _hosts(denylist):
    return [entry for entry in denylist or () if entry.get("host")]


def _apply(texts, spans):
    """Replace non-overlapping joined-text `spans` while keeping the page split.

    A replacement that crosses a page boundary is written on the page where it
    starts; its remainder is removed from the following page(s). Page numbers
    and order (the citation mapping) never change.
    """
    if not spans:
        return list(texts)
    whole, result, offset = "".join(texts), [], 0
    for text in texts:
        begin, finish = offset, offset + len(text)
        parts, cursor = [], begin
        for start, end, replacement in spans:
            if end <= begin or start >= finish:
                continue
            if start >= begin:
                parts.append(whole[cursor:start])
                parts.append(replacement)
            cursor = min(end, finish)
        parts.append(whole[cursor:finish])
        result.append("".join(parts))
        offset = finish
    return result


def _link_spans(text, denylist):
    return [(match.start(), match.end(), LINK_ALIAS) for match in INTERNAL_URL.finditer(text)]


def _host_spans(text, denylist):
    hosts = _hosts(denylist)
    if not hosts:
        return []
    return [(match.start(), match.end(), LINK_ALIAS) for match in _URL.finditer(text)
            if find_terms(match.group(), hosts)]


def _term_spans(text, denylist):
    return [(start, end, entry["alias"]) for start, end, entry in find_terms(text, denylist)]


def _replace_links(text, denylist):
    """One string: internal links, then deny-listed hosts, become the link alias."""
    count = 0
    for finder in (_link_spans, _host_spans):
        text = unicodedata.normalize("NFC", text)
        spans = finder(text, denylist)
        count += len(spans)
        text = _replace_checked([text], spans)[0]
    return text, count


def _replace_terms(text, denylist):
    text = unicodedata.normalize("NFC", text)
    spans = _term_spans(text, denylist)
    return _replace_checked([text], spans)[0], len(spans)


_NUMERIC = re.compile(r"\d(?:[\d,.]*\d)?")
_JUNCTION = 32      # context checked on each side of a page junction
_MAX_SHIFT = 64     # characters that may move across one junction


def _numbers(text):
    return [match.group() for match in _NUMERIC.finditer(text)]


def _buffer(texts):
    """NFC pages whose concatenation is exactly the NFC form of the joined input.

    A composition that spans a page boundary (for example a leading Hangul jamo
    at the end of one page and its vowel at the start of the next) is attributed
    to the following page: the unstable tail moves across the junction. Page
    numbers and order never change. Any junction that cannot be stabilized, or a
    buffer that differs from the NFC of the whole input, is refused.
    """
    pages = [unicodedata.normalize("NFC", text) for text in texts]
    for index in range(len(pages) - 1):
        moved = 0
        while True:
            left, right = pages[index][-_JUNCTION:], pages[index + 1][:_JUNCTION]
            if unicodedata.normalize("NFC", left + right) == left + right:
                break
            if not pages[index] or moved >= _MAX_SHIFT:
                raise NormalizationInvariant()
            pages[index], pages[index + 1] = pages[index][:-1], unicodedata.normalize(
                "NFC", pages[index][-1] + pages[index + 1])
            moved += 1
    if "".join(pages) != unicodedata.normalize("NFC", "".join(texts)):
        raise NormalizationInvariant()
    return pages


def _replace_checked(texts, spans):
    """Apply `spans` (offsets into the joined buffer) and verify the result.

    Hard invariant: the output is exactly the buffer with each span substituted,
    and every numeric/financial token outside a replaced identifier appears
    unchanged, in order, in the output.
    """
    whole = "".join(texts)
    result = _apply(texts, spans)
    output = "".join(result)
    expected, gaps, cursor = [], [], 0
    for start, end, replacement in spans:
        gaps.append(whole[cursor:start])
        expected.extend([whole[cursor:start], replacement])
        cursor = end
    gaps.append(whole[cursor:])
    expected.append(whole[cursor:])
    if output != "".join(expected):
        raise NormalizationInvariant()
    position, found = 0, []
    for index, gap in enumerate(gaps):
        found.append(output[position:position + len(gap)])
        position += len(gap) + (len(spans[index][2]) if index < len(spans) else 0)
    if [_numbers(gap) for gap in gaps] != [_numbers(text) for text in found]:
        raise NormalizationInvariant()
    return result


def _normalize_pages(texts, denylist):
    """Links, deny-listed hosts, then terms, each matched and replaced on one NFC buffer.

    Spans are computed on the joined NFC buffer and applied to that same buffer's
    page split, so offsets never refer to a differently normalized text.
    """
    original = list(texts)
    texts, total = _buffer(original), 0
    if _numbers("".join(texts)) != _numbers("".join(original)):
        raise NormalizationInvariant()
    for finder in (_link_spans, _host_spans, _term_spans):
        spans = finder("".join(texts), denylist)
        total += len(spans)
        replaced = _replace_checked(texts, spans)
        texts = _buffer(replaced)  # an alias junction may need recomposition
        if _numbers("".join(texts)) != _numbers("".join(replaced)):
            raise NormalizationInvariant()
    return texts, total


def _normalize_one(text, denylist):
    texts, count = _normalize_pages([text], denylist)
    return texts[0], count


def derivative_hash(pages):
    return schema.digest([{"page": page["page"], "text": page["text"]} for page in pages])


def normalize(pages, denylist) -> dict:
    """Normalize one source's ordered pages; identifiers split by a page boundary are found."""
    denylist = canonical_entries(denylist)
    texts, total = _normalize_pages([page["text"] for page in pages], denylist)
    result = [{"page": page["page"], "text": text} for page, text in zip(pages, texts)]
    return {"pages": result, "replacements": {"aliasCount": total}, "derivativeHash": derivative_hash(result)}


def _scan(text, denylist, pii_counts):
    text = unicodedata.normalize("NFC", text)
    for hit in _pii().scan_rules(text):
        pii_counts[hit["type"]] = pii_counts.get(hit["type"], 0) + 1
    return len(find_terms(text, denylist)) + len(INTERNAL_URL.findall(text))


def residual(pages, denylist, *, contiguous=True) -> dict:
    """Residual identifiers/PII per page and, for one source's pages, across boundaries."""
    denylist = canonical_entries(denylist)
    identifiers, pii_counts = 0, {}
    for page in pages:
        identifiers += _scan(page["text"], denylist, pii_counts)
    if contiguous and len(pages) > 1:
        spanning = {}
        identifiers = max(identifiers, _scan("".join(page["text"] for page in pages), denylist, spanning))
        for kind, count in spanning.items():
            pii_counts[kind] = max(pii_counts.get(kind, 0), count)
    return {"identifiers": identifiers,
            "pii": [{"type": kind, "count": pii_counts[kind]} for kind in sorted(pii_counts)]}


def require_clean(report):
    counts = {item["type"]: item["count"] for item in report["pii"]}
    if report["identifiers"]:
        counts["IDENTIFIER"] = report["identifiers"]
    if counts:
        raise NormalizationBlocked(counts)


def normalize_text(text, denylist) -> str:
    """Normalize one prompt string (title, edit instruction). B2 binds it as deps["normalize"]."""
    if not isinstance(text, str):
        raise ValueError("Prompt text must be a string")
    denylist = canonical_entries(denylist)
    normalized, _ = _normalize_one(text, denylist)
    require_clean(residual([{"page": 1, "text": normalized}], denylist))
    return normalized
