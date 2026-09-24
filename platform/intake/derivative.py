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


def _replace_links(text, denylist):
    count = 0
    hosts = _hosts(denylist)

    def link(match):
        nonlocal count
        count += 1
        return LINK_ALIAS

    text = INTERNAL_URL.sub(link, text)
    if hosts:
        def deny_listed(match):
            nonlocal count
            if find_terms(match.group(), hosts):
                count += 1
                return LINK_ALIAS
            return match.group()
        text = _URL.sub(deny_listed, text)
    return text, count


def _replace_terms(text, denylist):
    spans = find_terms(text, denylist)
    if not spans:
        return text, 0
    parts, cursor = [], 0
    for start, end, entry in spans:
        parts.append(text[cursor:start])
        parts.append(entry["alias"])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts), len(spans)


def _normalize_one(text, denylist):
    text = unicodedata.normalize("NFC", text)
    text, links = _replace_links(text, denylist)
    text, terms = _replace_terms(text, denylist)
    return text, links + terms


def derivative_hash(pages):
    return schema.digest([{"page": page["page"], "text": page["text"]} for page in pages])


def normalize(pages, denylist) -> dict:
    denylist = canonical_entries(denylist)
    result, total = [], 0
    for page in pages:
        text, count = _normalize_one(page["text"], denylist)
        result.append({"page": page["page"], "text": text})
        total += count
    return {"pages": result, "replacements": {"aliasCount": total}, "derivativeHash": derivative_hash(result)}


def _scan(text, denylist, pii_counts):
    text = unicodedata.normalize("NFC", text)
    for hit in _pii().scan_rules(text):
        pii_counts[hit["type"]] = pii_counts.get(hit["type"], 0) + 1
    return len(find_terms(text, denylist)) + len(INTERNAL_URL.findall(text))


def residual(pages, denylist) -> dict:
    denylist = canonical_entries(denylist)
    identifiers, pii_counts = 0, {}
    for page in pages:
        identifiers += _scan(page["text"], denylist, pii_counts)
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
