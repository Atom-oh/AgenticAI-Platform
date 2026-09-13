"""Immutable local intake. Uploaded bytes are never executed or sent to a model."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

from documents.errors import DocumentError
from documents.library import (
    CHUNK_BYTES, MAX_PARAGRAPH_CHARS, MAX_PROJECTION_BYTES, MAX_TEXT_CHARS,
    Library, canonical_json, file_fields,
)
from workspace.collaboration import Collaboration, CollaborationError
from workspace.storage import Conflict


class _HTMLText(HTMLParser):
    _noncontent = frozenset({"head", "template"})
    _untranscribed = frozenset({
        "svg", "canvas", "iframe", "object", "embed", "img", "image", "picture",
        "video", "audio", "math", "applet", "frame", "frameset", "input",
        "textarea", "xmp", "plaintext", "noembed", "noframes",
    })
    _discarded = frozenset({"script", "style", "iframe", "object", "svg", "canvas", "video", "audio", "applet"})
    _blocks = frozenset({"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.fragments = []
        self.length = 0
        self.truncated = False
        self.warnings = set()

    def append(self, value):
        remaining = MAX_TEXT_CHARS - self.length
        if len(value) > remaining:
            self.truncated = True
        if remaining:
            self.fragments.append(value[:remaining])
            self.length += min(len(value), remaining)

    def handle_starttag(self, tag, attrs):
        if tag in self._untranscribed:
            self.warnings.add("html-visible-content-not-transcribed")
        if tag == "template" and any(name.startswith("shadowroot") for name, _ in attrs):
            self.warnings.add("html-visible-content-not-transcribed")
        if tag == "script" or any(name.startswith("on") for name, _ in attrs):
            self.warnings.add("html-active-content-not-executed")
        if tag == "style" or any(name == "style" for name, _ in attrs):
            self.warnings.add("html-style-not-evaluated")
        if tag == "link" and any(name == "rel" and "stylesheet" in (value or "").lower().split() for name, value in attrs):
            self.warnings.add("html-style-not-evaluated")
        if any(name in ("href", "src", "action", "formaction", "xlink:href")
               and "".join((value or "").split()).lower().startswith(("javascript:", "vbscript:", "data:text/html"))
               for name, value in attrs):
            self.warnings.add("html-active-content-not-executed")
        # Non-content metadata and unhandled visible/active subtrees both stay
        # out of the text projection. Only the latter invalidate completeness.
        if tag in self._noncontent or tag in self._discarded:
            self.hidden.append(tag)
        if not self.hidden:
            if tag in self._blocks:
                self.append("\n\n")
            elif tag == "br":
                self.append("\n")
            elif tag in ("td", "th"):
                self.append("\t")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self._blocks:
            self.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.append(data)


def _paragraphs(texts):
    paragraphs = []
    for page, value in texts:
        # Keep delimiters and whitespace in the projection, including CRLF.
        start = 0
        bounds = [match.end() for match in re.finditer(r"\r?\n[ \t]*\r?\n", value)]
        for end in [*bounds, len(value)]:
            for offset in range(start, end, MAX_PARAGRAPH_CHARS):
                literal = value[offset:min(end, offset + MAX_PARAGRAPH_CHARS)]
                paragraphs.append({"id": f"p{len(paragraphs) + 1:06d}", "text": literal, "page": page,
                                   "sha256": hashlib.sha256(literal.encode("utf-8")).hexdigest()})
            start = end
    return paragraphs


def extract(name, data):
    """Return the exact hashable projection; no hash is embedded in the projection."""
    file_fields({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    extension = Path(name).suffix.lower().lstrip(".")
    status, warnings, pages, texts = "complete", [], 0, []
    if extension == "pdf":
        try:
            process = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(__file__).with_name("_pdf.py"))], input=data,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True,
            )
            result = json.loads(process.stdout)
            texts, pages, status, warnings = result["texts"], result["pages"], result["parseStatus"], result["warnings"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            status, warnings = "failed", ["pdf-parser-limit-or-failure"]
    else:
        try:
            value = data.decode("utf-8")
            if "\x00" in value:
                raise UnicodeError()
            if extension in ("html", "htm"):
                parser = _HTMLText()
                parser.feed(value)
                parser.close()
                value = "".join(parser.fragments)
                warnings.extend(parser.warnings)
                if parser.truncated:
                    warnings.append("text-character-limit")
                if parser.hidden:
                    warnings.append("incomplete-html")
            if len(value) > MAX_TEXT_CHARS:
                warnings.append("text-character-limit")
                value = value[:MAX_TEXT_CHARS]
            texts = [(None, value)]
            if warnings:
                status = "partial"
        except (UnicodeError, ValueError):
            status, warnings = "failed", ["invalid-utf8-text"]
    if not any(value.strip() for _, value in texts):
        if status == "complete":
            status = "empty"
        warnings.append("no-extractable-text")
    return {"schemaVersion": 1, "paragraphs": _paragraphs(texts), "pages": pages,
            "parseStatus": status, "warnings": sorted(set(warnings))}


def finalize(worker, owner, job):
    """Publish extraction, audit and terminal job result under one authority fence."""
    storage = worker.storage
    data = job.get("input", {})
    collaboration = getattr(worker, "collaboration", None) or Collaboration(storage)
    try:
        scope = collaboration.resolve_scope(data.get("actorId"), data.get("projectId"))
    except CollaborationError as error:
        raise DocumentError(error.status, error.code, error.message) from None
    if scope["owner"] != owner or job.get("task") != "document-finalize":
        raise DocumentError(403, "forbidden", "The job does not belong to this collection")
    library = Library(worker, scope)
    document = library.document(data.get("documentId"), "edit")
    revision = library.revision(document, data.get("revisionId"))
    current_job = storage.get(owner, "job", job["id"])
    if (not current_job or current_job.get("input") != data or current_job.get("task") != "document-finalize"
            or revision.get("jobId") != job["id"]):
        raise DocumentError(409, "job-changed", "The intake job no longer matches this revision")
    result = {"documentId": document["id"], "revisionId": revision["id"], "parseStatus": revision["parseStatus"]}
    if revision.get("textHash"):
        library.projection(document, revision)
        return result
    if revision["status"] != "processing" or current_job["status"] not in ("queued", "running"):
        raise DocumentError(409, "intake-not-ready", "This revision is not awaiting extraction")
    try:
        pieces = []
        for index in range(revision["partCount"]):
            part = revision.get("parts", {}).get(str(index), {})
            key = library.blob_key(revision, f"parts/{index}")
            if part.get("status") != "stored" or part.get("key") != key:
                raise DocumentError(409, "parts-missing", "An immutable upload part is missing")
            expected_size = min(CHUNK_BYTES, revision["size"] - index * CHUNK_BYTES)
            try:
                info = storage.blob_info(key)
                if info["size"] != expected_size or info["sha256"] != part.get("sha256"):
                    raise DocumentError(409, "part-changed", "Upload part integrity could not be verified")
                piece = storage.get_blob(key, length=CHUNK_BYTES)
            except (FileNotFoundError, ValueError):
                raise DocumentError(409, "parts-missing", "An immutable upload part is unavailable") from None
            if len(piece) != expected_size or hashlib.sha256(piece).hexdigest() != part.get("sha256"):
                raise DocumentError(409, "part-changed", "Upload part integrity could not be verified")
            pieces.append(piece)
        original = b"".join(pieces)
        if len(original) != revision["size"] or hashlib.sha256(original).hexdigest() != revision["sha256"]:
            raise DocumentError(409, "original-mismatch", "Original bytes do not match the declared size and hash")
        projection = extract(revision["name"], original)
        encoded = canonical_json(projection)
        if len(encoded) > MAX_PROJECTION_BYTES:
            raise DocumentError(409, "extraction-limit", "The extraction projection exceeds the storage limit")
        # Immutable, unreferenced objects may survive a failed transaction.
        # They cannot be addressed through a document/asset route until committed.
        original_key = library.blob_key(revision, "original")
        projection_key = library.blob_key(revision, "projection.json")
        storage.put_blob_once(original_key, original, "application/octet-stream")
        storage.put_blob_once(projection_key, encoded, "application/json")
        parsed = {**revision, "status": "draft" if projection["parseStatus"] == "complete" else "failed",
                  "parseStatus": projection["parseStatus"], "warnings": projection["warnings"],
                  "pages": projection["pages"], "paragraphCount": len(projection["paragraphs"]),
                  "textHash": hashlib.sha256(encoded).hexdigest(), "originalKey": original_key,
                  "projectionKey": projection_key, "completedAt": storage.clock()}
        result["parseStatus"] = projection["parseStatus"]
        library.commit([
            library.write("docrevision", parsed, revision["version"]),
            library.write("job", {**current_job, "status": "completed", "progress": 100, "result": result},
                          current_job["version"]),
            library.audit(document, "extracted", revision, parseStatus=projection["parseStatus"],
                          sha256=revision["sha256"], textHash=parsed["textHash"]),
        ], [document])
        return result
    except DocumentError as error:
        # Safe domain failures can be recorded only while the actor still has
        # authority. Revocation never gains a fallback write/publication path.
        try:
            library.commit([
                library.write("docrevision", {**revision, "status": "failed", "parseStatus": "failed",
                                              "warnings": [error.code], "error": error.message}, revision["version"]),
                library.audit(document, "intake-failed", revision, code=error.code),
            ], [document])
        except (DocumentError, Conflict):
            pass
        raise
