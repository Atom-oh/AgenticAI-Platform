"""Source resolution through current authority and metadata-only inspection receipts.

`resolve` reads only through `workspace.ontology_sources.Sources.resolve` (current
authority, never the historical `authorize`) and the document library's verified
projection. `inspect` stores counts only: no PII samples, no deny-list terms.
"""
from __future__ import annotations

import importlib
import importlib.util
import unicodedata
from pathlib import Path

from workbench.service import Service, fail
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources

PROFILE = "inspect-1"
LOGICAL_PAGE_CHARS = 4000
INTAKE_KINDS = ("document-revision", "product-guideline", "asset")
RESERVED_KINDS = ("published-asset", "ux-contract", "run-round")
_API_DIR = Path(__file__).resolve().parents[1] / "api"
_pii_module = None


def _pii():
    """The independent rules detector from `api/common/pii.py` (shared with engine/gate)."""
    global _pii_module
    if _pii_module is None:
        try:
            module = importlib.import_module("common.pii")
        except Exception:  # noqa: BLE001 - local layout without api/ on sys.path
            module = None
        if module is None or not hasattr(module, "scan_rules"):
            spec = importlib.util.spec_from_file_location("_intake_common_pii", str(_API_DIR / "common" / "pii.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        _pii_module = module
    return _pii_module


def context(host, scope, claims=None):
    """A server-side context for an already verified scope (JWT resolved upstream)."""
    return Service(host, scope, claims or {"sub": scope.get("actor")})


def _reference(source_ref):
    if not isinstance(source_ref, dict):
        fail(400, "invalid-source", "원본 참조 형식이 올바르지 않습니다.")
    try:
        ref = schema.source_ref(source_ref)
    except ValueError:
        fail(400, "invalid-source", "원본 참조 형식이 올바르지 않습니다.")
    if "location" in ref:
        fail(400, "invalid-source", "원본 전체 리비전만 반입할 수 있습니다.")
    kind = ref["sourceKind"]
    if kind not in INTAKE_KINDS and kind not in RESERVED_KINDS:
        fail(422, "intake-source-unsupported", "반입할 수 없는 원본 유형입니다.")
    return ref


def _logical(paragraphs):
    """Group unpaginated paragraphs, in order, into pages of <= 4 000 characters."""
    pages, current = [], []

    def close():
        if current:
            # Projection paragraphs keep their literal delimiters; joining with ""
            # reproduces the extracted text exactly.
            pages.append({"page": len(pages) + 1, "text": "".join(p["text"] for p in current),
                          "paragraphIds": [current[0]["id"], current[-1]["id"]], "logical": True})
            current.clear()

    for paragraph in paragraphs:
        size = sum(len(p["text"]) for p in current) + len(paragraph["text"])
        if current and size > LOGICAL_PAGE_CHARS:
            close()
        current.append(paragraph)
    close()
    return pages


def _physical(paragraphs):
    grouped = {}
    for paragraph in paragraphs:
        grouped.setdefault(paragraph["page"], []).append(paragraph)
    if list(grouped) != sorted(grouped):
        fail(409, "source-integrity", "원본 페이지 순서를 확인하지 못했습니다.")
    return [{"page": page, "text": "".join(p["text"] for p in items),
             "paragraphIds": [items[0]["id"], items[-1]["id"]], "logical": False}
            for page, items in grouped.items()]


def _text_pages(text):
    """Guideline/code text: paragraphs of at most one logical page each."""
    import re
    paragraphs, start = [], 0
    bounds = [match.end() for match in re.finditer(r"\r?\n[ \t]*\r?\n", text or "")]
    for end in [*bounds, len(text or "")]:
        for offset in range(start, end, LOGICAL_PAGE_CHARS):
            paragraphs.append({"id": f"p{len(paragraphs) + 1:06d}",
                               "text": text[offset:min(end, offset + LOGICAL_PAGE_CHARS)]})
        start = end
    return _logical(paragraphs)


def pages_hash(pages):
    return schema.digest([{"page": p["page"], "text": p["text"]} for p in pages])


def resolve(host, scope, source_ref, *, claims=None, sources=None):
    """Resolve `source_ref` with current authority into ordered `{page, text}` pages."""
    ref = _reference(source_ref)
    reader = sources or Sources(context(host, scope, claims))
    kind = ref["sourceKind"]
    if kind in RESERVED_KINDS:
        # B0 sharing installs these kinds' ontology authority adapters, but
        # source-admission/1 has no intake adapter for them. Refuse before any
        # lookup so intake never becomes an existence oracle for rounds,
        # contracts or publications.
        fail(503, "ontology-source-adapter-unavailable", "이 원본 유형의 반입 연결이 아직 준비되지 않았습니다.")
    if kind == "document-revision":
        from documents.errors import DocumentError
        from documents.library import Library
        result = reader.resolve(ref, text=False)
        try:
            library = Library(host, reader.ctx.scope)
            document = library.document(ref["sourceId"])
            revision = library.revision(document, ref["revision"], approved=True)
            if (revision["id"] != result["record"]["id"] or revision["version"] != result["record"]["version"]
                    or revision["sha256"] != ref["sha256"] or str(document["aclVersion"]) != ref["audienceRevision"]):
                fail(409, "ontology-source-stale", "문서 원본 또는 읽기 권한이 변경되었습니다.")
            projection = library.projection(document, revision)
        except DocumentError as error:
            fail(error.status, error.code, error.message)
        paragraphs = projection["paragraphs"]
        if not paragraphs:
            fail(422, "source-empty", "추출된 문단이 없는 원본입니다.")
        numbered = [p["page"] is not None for p in paragraphs]
        if all(numbered):
            pages = _physical(paragraphs)
        elif not any(numbered):
            pages = _logical(paragraphs)
        else:
            fail(409, "source-integrity", "원본 페이지 구분이 일관되지 않습니다.")
        record = {"document": document, "revision": revision}
    else:
        result = reader.resolve(ref, text=True)
        if not isinstance(result.get("text"), str) or not result["text"].strip():
            fail(422, "source-empty", "텍스트로 반입할 수 없는 원본입니다.")
        pages = _text_pages(result["text"])
        record = {kind: result["record"]}
    return {"ref": ref, "pages": pages, "originalHash": pages_hash(pages), "records": record,
            "sources": reader}


def fold(value):
    """The deny-list matching form: NFC, case-folded for ASCII only."""
    value = unicodedata.normalize("NFC", value)
    return "".join(ch.lower() if ch.isascii() else ch for ch in value)


def _entries(denylist):
    entries = []
    for entry in denylist or ():
        if not isinstance(entry, dict) or not isinstance(entry.get("term"), str) or not entry["term"].strip():
            raise ValueError("Invalid private deny-list entry")
        entries.append((fold(entry["term"].strip()), entry))
    # Longest term first: a longer name containing a shorter one wins.
    return sorted(entries, key=lambda item: (-len(item[0]), item[0]))


def find_terms(text, denylist):
    """Non-overlapping deny-list spans over the NFC form of `text`, longest first.

    ASCII case folding preserves length, so spans index the NFC text directly.
    """
    folded = fold(text)
    claimed, spans = [False] * len(folded), []
    for term, entry in _entries(denylist):
        start = folded.find(term)
        while start != -1:
            end = start + len(term)
            if not any(claimed[start:end]):
                claimed[start:end] = [True] * len(term)
                spans.append((start, end, entry))
            start = folded.find(term, start + 1)
    return sorted(spans, key=lambda span: span[0])


def count_identifiers(text, denylist):
    return len(find_terms(text, denylist))


def receipt_hash(receipt):
    return schema.digest({key: value for key, value in receipt.items() if key != "hash"})


def joined(pages):
    """The contiguous text of ordered pages: page boundaries are not token boundaries."""
    return unicodedata.normalize("NFC", "".join(page["text"] for page in pages))


def inspect(pages, *, denylist, contiguous=True):
    """Metadata-only receipt. `denylist=None` means private config was unavailable.

    With `contiguous` (the default for logical and physical pages of one source),
    detection also runs over the joined text, so an identifier split by a page
    boundary is still found; each count is the larger of the two passes. Separate
    files of a code collection pass `contiguous=False`.
    """
    pii = _pii()
    counts, identifiers, chars = {}, 0, 0
    for page in pages:
        text = page["text"]
        chars += len(text)
        for hit in pii.scan_rules(text):
            counts[hit["type"]] = counts.get(hit["type"], 0) + 1
        if denylist:
            identifiers += count_identifiers(text, denylist)
    if contiguous and len(pages) > 1:
        text, spanning = joined(pages), {}
        for hit in pii.scan_rules(text):
            spanning[hit["type"]] = spanning.get(hit["type"], 0) + 1
        for kind, count in spanning.items():
            counts[kind] = max(counts.get(kind, 0), count)
        if denylist:
            identifiers = max(identifiers, count_identifiers(text, denylist))
    blocking = []
    if denylist is None:
        blocking.append("denylist-unavailable")
    if counts:
        blocking.append("redaction-required")
    suggestion = "sensitive" if counts else "internal-non-sensitive"
    receipt = {"profile": PROFILE, "pages": len(pages), "chars": chars,
               "pii": [{"type": kind, "count": counts[kind]} for kind in sorted(counts)],
               "identifiers": {"count": identifiers}, "classificationSuggestion": suggestion,
               "blocking": blocking}
    receipt["hash"] = receipt_hash(receipt)
    return receipt
