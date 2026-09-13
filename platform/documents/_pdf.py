"""Bounded local PDF subprocess. Only structured results leave this process."""
from __future__ import annotations

import io
import json
import logging
import resource
import sys


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
            for index in range(min(count, 200)):
                page = reader.pages[index]
                value = page.extract_text() or ""
                if not value.strip():
                    result["warnings"].append("pdf-page-without-text")
                resources = page.get("/Resources")
                objects = resources.get("/XObject", {}) if resources else {}
                if any(obj.get_object().get("/Subtype") == "/Image" for obj in objects.values()):
                    result["warnings"].append("pdf-images-not-transcribed")
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
