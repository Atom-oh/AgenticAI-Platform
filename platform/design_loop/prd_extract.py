"""Admitted document pages -> cited PRD with exact value spans (engine plan, Task E7; P-01, P-02; Codex #8).

The model proposes; this module only verifies. No field is ever corrected by the code: every problem is an issue,
and issues block PRD confirmation (P-03) in C. Financial values must be the exact, unit-bearing span of the admitted
page (checked against the full normalized page, not the model's possibly shortened quote; review round 24, R1-8).
"""
from __future__ import annotations

import json
import re

from workspace import ontology_schema as schema

from .financial import SIGNS, is_value
from .guide_rules import cited_pages
from .model_call import call

PRODUCT_TYPES = frozenset({"수신", "여신"})
EVIDENCE = frozenset({"input", "auto", "none"})
TOP = frozenset({"productName", "productType", "category", "eligibility", "term", "baseRate", "preferential", "notices"})
MAX_PREFERENTIAL, MAX_NOTICES, MAX_VALUE = 20, 50, 500
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SYSTEM = (
    "You extract a product requirements document from admitted pages. Reply with JSON only, shaped as "
    '{"productName": V, "productType": {"value": "수신|여신"}, "category": V, '
    '"eligibility": {"text": V, "conditionId": id}, "term": V, "baseRate": V, '
    '"preferential": [{"id": id, "condition": V, "rate": V, "evidence": "input|auto|none"}], '
    '"notices": [{"id": id, "text": V}]} where V = {"value": str, "cite": {"sourceId", "page", "quote", '
    '"derivativeHash"}}. Quotes are copied verbatim from the cited page; financial values keep their unit exactly.'
)
SUMMARY_ITEM = {"label": "string", "value": "string"}


def _norm(text):
    return re.sub(r"\s+", " ", text).strip()


def _prompt(pages):
    return "\n\n".join(f"[page sourceId={p['sourceRef']['sourceId']} page={p['page']} derivativeHash={p['derivativeHash']}]"
                       f"\n{_norm(p['text'])}" for p in pages)


def _boundary(text, start, end, value):
    """Whole-token span of a financial `value` at text[start:end] (review rounds 2-4, #8)."""
    if re.match(r"[0-9,%p]|\.\d", text[end:]):
        return False
    if start > 0 and text[start - 1] in "0123456789.,":
        return False
    lead = re.match(r"(?:연\s?)?", value).end()
    signed = value[lead:lead + 1] in SIGNS
    j = start + lead - 1
    while j >= 0 and text[j] == " ":
        j -= 1
    return signed or j < 0 or text[j] not in SIGNS


def _located(page_text, quote):
    return [m.start() for m in re.finditer(re.escape(quote), page_text)]


class _Checker:
    def __init__(self, pages):
        self.keyed = cited_pages(pages)
        self.order = {key: i for i, key in enumerate(self.keyed)}
        self.texts = {key: _norm(p["text"]) for key, p in self.keyed.items()}
        self.issues = []

    def issue(self, field, code):
        item = {"field": field, "code": code}
        if item not in self.issues:
            self.issues.append(item)

    def position(self, entry):
        cite = entry.get("cite") if isinstance(entry, dict) else None
        key = (cite.get("sourceId"), cite.get("page")) if isinstance(cite, dict) else None
        if key not in self.texts or not isinstance(cite.get("quote"), str):
            return (len(self.order), 0)
        offsets = _located(self.texts[key], _norm(cite["quote"]))
        return (self.order[key], offsets[0] if offsets else 0)

    def check(self, field, entry, *, financial=False):
        if entry is None:
            return self.issue(field, "missing-field")
        if not isinstance(entry, dict) or not isinstance(entry.get("value"), str) or not entry["value"].strip() \
                or len(entry["value"]) > MAX_VALUE or entry.keys() - {"value", "cite"}:
            return self.issue(field, "bad-value")
        cite = entry.get("cite")
        if cite is None:
            return self.issue(field, "missing-citation")
        if (not isinstance(cite, dict) or set(cite) != {"sourceId", "page", "quote", "derivativeHash"}
                or not isinstance(cite["quote"], str) or not _norm(cite["quote"])):
            return self.issue(field, "bad-citation")
        key = (cite["sourceId"], cite["page"])
        page = self.keyed.get(key)
        if page is None:
            return self.issue(field, "quote-not-found")
        if cite["derivativeHash"] != page["derivativeHash"]:
            return self.issue(field, "derivative-mismatch")
        text, quote, value = self.texts[key], _norm(cite["quote"]), _norm(entry["value"])
        occurrences = _located(text, quote)
        if not occurrences:
            return self.issue(field, "quote-not-found")
        if not financial:
            if value not in quote:
                self.issue(field, "value-not-in-quote")
            return None
        if not is_value(value):
            return self.issue(field, "value-unit-missing")
        spans = [m.start() for m in re.finditer(re.escape(value), quote)]
        # every occurrence of the quote in the page must yield a whole-token value span (ambiguity fails closed)
        if not spans or not all(any(_boundary(text, o + s, o + s + len(value), value) for s in spans) for o in occurrences):
            self.issue(field, "value-not-verbatim")
        return None


def _entries(checker, prd, name, limit):
    values = prd.get(name, [])
    if not isinstance(values, list) or len(values) > limit:
        checker.issue(name, "too-many" if isinstance(values, list) else "bad-value")
        return []
    ids, out = set(), []
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _ID.fullmatch(item["id"]) \
                or item["id"] in ids:
            checker.issue(name, "bad-id")
            continue
        ids.add(item["id"])
        out.append(item)
    return out


def extract_prd(pages, deps, *, model):
    text, blocked = call(deps, _SYSTEM, _prompt(pages))
    if blocked:
        return {"prd": None, "issues": [{"field": "*", "code": blocked}], "blocked": blocked}
    try:
        prd = json.loads(text)
    except ValueError:
        prd = None
    if not isinstance(prd, dict):
        return {"prd": None, "issues": [{"field": "*", "code": "parse-failed"}]}
    try:
        schema.canonical(prd)
    except ValueError:
        return {"prd": None, "issues": [{"field": "*", "code": "parse-failed"}]}
    k = _Checker(pages)
    for key in sorted(prd.keys() - TOP):
        k.issue(key, "unknown-field")
    for name in ("productName", "category"):
        k.check(name, prd.get(name))
    ptype = prd.get("productType")
    if not isinstance(ptype, dict) or ptype.get("value") not in PRODUCT_TYPES or ptype.keys() - {"value", "cite"}:
        k.issue("productType", "bad-type")
    elig = prd.get("eligibility")
    if not isinstance(elig, dict) or elig.keys() - {"text", "conditionId"}:
        k.issue("eligibility", "missing-field" if elig is None else "bad-value")
    else:
        k.check("eligibility.text", elig.get("text"))
        if not isinstance(elig.get("conditionId"), str) or not _ID.fullmatch(elig["conditionId"]):
            k.issue("eligibility", "bad-id")
    k.check("term", prd.get("term"), financial=True)
    k.check("baseRate", prd.get("baseRate"), financial=True)
    prefs = _entries(k, prd, "preferential", MAX_PREFERENTIAL)
    for p in prefs:
        field = f"preferential.{p['id']}"
        if p.keys() - {"id", "condition", "rate", "evidence"}:
            k.issue(field, "unknown-field")
        k.check(field + ".condition", p.get("condition"))
        k.check(field + ".rate", p.get("rate"), financial=True)
        if p.get("evidence") not in EVIDENCE:
            k.issue(field + ".evidence", "bad-evidence")
    notices = _entries(k, prd, "notices", MAX_NOTICES)
    for n in notices:
        if n.keys() - {"id", "text"}:
            k.issue(f"notices.{n['id']}", "unknown-field")
        k.check(f"notices.{n['id']}", n.get("text"))
    conditions = [elig.get("conditionId")] if isinstance(elig, dict) else []
    if len(set(conditions + [p["id"] for p in prefs])) != len(conditions) + len(prefs):
        k.issue("preferential", "duplicate-condition")
    out = dict(prd)
    # Server-assigned ordinals (pref-<n>, notice-<n>) follow document order, never the model's order (AF1).
    out["preferential"] = [p for _, p in sorted(enumerate(prefs), key=lambda e: (k.position(e[1].get("rate") or e[1].get("condition")), e[0]))]
    out["notices"] = [n for _, n in sorted(enumerate(notices), key=lambda e: (k.position(e[1].get("text")), e[0]))]
    return {"prd": out, "issues": k.issues}


def _value(entry):
    return entry["value"] if isinstance(entry, dict) and isinstance(entry.get("value"), str) else None


def bindings(prd):
    """Deterministic binding catalog: dotted path -> verbatim cited value (or a list derived from cited values)."""
    out = {}
    for name in ("productName", "productType", "category", "term", "baseRate"):
        if _value(prd.get(name)) is not None:
            out[f"product.{name}"] = _value(prd[name])
    elig = prd.get("eligibility")
    if isinstance(elig, dict) and _value(elig.get("text")) is not None:
        out["product.eligibility"] = _value(elig["text"])
    for n, p in enumerate(prd.get("preferential") or [], 1):
        for part in ("condition", "rate"):
            if isinstance(p, dict) and _value(p.get(part)) is not None:
                out[f"product.preferential.pref-{n}.{part}"] = _value(p[part])
    for n, notice in enumerate(prd.get("notices") or [], 1):
        if isinstance(notice, dict) and _value(notice.get("text")) is not None:
            out[f"product.notice.notice-{n}"] = _value(notice["text"])
    items = [{"label": label, "value": out[path]} for label, path in (("기본금리", "product.baseRate"),
                                                                     ("가입기간", "product.term")) if path in out]
    if items:
        out["product.summaryItems"] = items
    return out


def binding_types(prd):
    return {path: {"list": dict(SUMMARY_ITEM)} if isinstance(value, list) else "string"
            for path, value in bindings(prd).items()}


def prd_hash(prd):
    return schema.digest(prd)
