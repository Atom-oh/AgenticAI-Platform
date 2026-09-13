"""Bounded local PDF subprocess. Only structured results leave this process."""
from __future__ import annotations

import io
import json
import logging
import resource
import sys


def _content_warnings(page, reader, budget):
    """Inspect every Form stream/resources without decoding raster image data."""
    from pypdf.generic import ContentStream, StreamObject

    def resolved(value):
        return value.get_object() if hasattr(value, "get_object") else value

    warnings, visited = set(), set()
    if page.get("/Annots"):
        # Appearance streams can carry visible clauses outside page /Contents.
        # They are not included in this text projection.
        warnings.add("pdf-annotations-not-transcribed")
    stack = [(page.get_contents(), resolved(page.get("/Resources", {})), frozenset(), 0)]
    while stack:
        stream, resources, ancestors, depth = stack.pop()
        if depth > 32 or budget["objects"] <= 0:
            raise ValueError("PDF content inspection limit")
        resources = resolved(resources)
        if not isinstance(resources, dict):
            raise ValueError("Invalid PDF resources")
        if resources.get("/Pattern"):
            # Tiling patterns may paint images or other untranscribed content.
            warnings.add("pdf-patterns-not-transcribed")
        identity = (id(stream), id(resources))
        if identity in ancestors:
            raise ValueError("PDF resource cycle")
        if identity in visited:
            continue
        visited.add(identity)
        budget["objects"] -= 1
        if stream is not None:
            content = stream if isinstance(stream, ContentStream) else ContentStream(stream, reader)
            operations = content.operations
            budget["operations"] -= len(operations)
            if budget["operations"] < 0:
                raise ValueError("PDF content inspection limit")
            if any(operator == b"INLINE IMAGE" for _, operator in operations):
                warnings.add("pdf-images-not-transcribed")
        objects = resolved(resources.get("/XObject", {}))
        if not isinstance(objects, dict) or len(objects) > budget["objects"]:
            raise ValueError("Invalid or excessive PDF XObjects")
        for reference in objects.values():
            obj = resolved(reference)
            if not isinstance(obj, StreamObject):
                raise ValueError("Invalid PDF XObject")
            subtype = obj.get("/Subtype")
            if subtype == "/Image":
                budget["objects"] -= 1
                warnings.add("pdf-images-not-transcribed")
            elif subtype == "/Form":
                # A Form may omit its own resource dictionary and inherit the
                # caller's. Inspect its stream even when it has no XObjects:
                # inline image data has no /Image resource entry.
                child_resources = resolved(obj.get("/Resources", resources))
                stack.append((obj, child_resources, ancestors | {identity}, depth + 1))
            else:
                warnings.add("pdf-unsupported-xobject")
    return warnings


def main():
    # Protect the worker from damaged/compressed streams. No uploaded program runs.
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    from pypdf import PdfReader

    logged = []

    class Capture(logging.Handler):
        def emit(self, record):
            logged.append(True)  # Never retain or expose the parser's message.

    logging.getLogger().handlers = [Capture()]
    logging.getLogger().setLevel(logging.WARNING)
    result = {"texts": [], "pages": 0, "parseStatus": "failed", "warnings": []}
    try:
        data = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024 or not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
            raise ValueError()
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            result.update(parseStatus="encrypted", warnings=["encrypted-pdf"])
        else:
            count = len(reader.pages)
            result.update(pages=count, parseStatus="complete")
            if count > 200:
                result["warnings"].append("pdf-page-limit")
            remaining = 400_000
            budget = {"objects": 2048, "operations": 200_000}
            for index in range(min(count, 200)):
                page = reader.pages[index]
                result["warnings"].extend(_content_warnings(page, reader, budget))
                value = page.extract_text() or ""
                if not value.strip():
                    result["warnings"].append("pdf-page-without-text")
                if len(value) > remaining:
                    result["warnings"].append("text-character-limit")
                result["texts"].append([index + 1, value[:remaining]])
                remaining -= min(len(value), remaining)
                if remaining == 0 and index + 1 < count:
                    result["warnings"].append("text-character-limit")
                    break
            if logged:
                result["warnings"].append("pdf-parser-warning")
            if result["warnings"]:
                result["parseStatus"] = "partial"
            if not count or not any(value.strip() for _, value in result["texts"]):
                result.update(parseStatus="empty", warnings=[*result["warnings"], "pdf-without-extractable-text"])
    except Exception:
        result.update(parseStatus="failed", warnings=["damaged-or-unsupported-pdf"])
    result["warnings"] = sorted(set(result["warnings"]))
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
