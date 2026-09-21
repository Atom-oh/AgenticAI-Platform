"""Prepare a local PDF/PPTX ZIP as a private, text-only Studio guideline pack."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import posixpath
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from workspace.guidelines import FORMAT, MAX_PACK_BYTES, MAX_PAGE_CHARS, MAX_PAGES, MAX_TOTAL_CHARS, validate_pack

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024


def archive_entries(archive, maximum=20, total_limit=MAX_ARCHIVE_BYTES):
    entries = archive.infolist()
    if len(entries) > maximum or sum(entry.file_size for entry in entries) > total_limit:
        raise ValueError("압축파일의 항목 수 또는 해제 크기 상한을 초과했습니다.")
    names = set()
    for entry in entries:
        name = unicodedata.normalize("NFC", entry.filename)
        path = PurePosixPath(name)
        if (name in names or path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name
                or entry.flag_bits & 1 or entry.file_size > MAX_SOURCE_BYTES
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
                or (entry.file_size > 1024 * 1024 and entry.file_size > max(1, entry.compress_size) * 200)):
            raise ValueError("안전하게 읽을 수 없는 압축파일 항목입니다.")
        names.add(name)
    return entries


def pdf_pages(data):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(data) as document:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError("PDF 페이지 수 상한을 초과했습니다.")
        result, characters = [], 0
        for index in range(len(document)):
            page = document[index]
            try:
                text_page = page.get_textpage()
                try:
                    count = text_page.count_chars()
                    text = text_page.get_text_range(index=0, count=min(count, MAX_PAGE_CHARS)) if count else ""
                finally:
                    text_page.close()
            finally:
                page.close()
            characters += len(text)
            if characters > MAX_TOTAL_CHARS:
                raise ValueError("PDF 추출 텍스트 한도를 초과했습니다.")
            result.append({"page": index + 1, "text": text[:MAX_PAGE_CHARS], "truncated": count > MAX_PAGE_CHARS or len(text) > MAX_PAGE_CHARS})
        return result


def pptx_pages(data, deadline=None):
    from defusedxml import ElementTree
    relation_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    presentation_ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    drawing_ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    deadline = min(deadline if deadline is not None else float("inf"), time.monotonic() + 45)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive_entries(archive, maximum=6000, total_limit=128 * 1024 * 1024)
        def xml(name):
            if archive.getinfo(name).file_size > 8 * 1024 * 1024:
                raise ValueError("슬라이드 XML 크기 상한을 초과했습니다.")
            return ElementTree.fromstring(archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True)
        relations = {item.attrib["Id"]: item for item in xml("ppt/_rels/presentation.xml.rels")}
        slides = xml("ppt/presentation.xml").findall(f"{presentation_ns}sldIdLst/{presentation_ns}sldId")
        if not 1 <= len(slides) <= MAX_PAGES:
            raise ValueError("슬라이드 수 상한을 초과했습니다.")
        result, characters = [], 0
        for number, slide in enumerate(slides, 1):
            if time.monotonic() >= deadline:
                raise ValueError("슬라이드 해석 시간 한도를 초과했습니다.")
            relation = relations[slide.attrib[relation_ns + "id"]]
            target = relation.attrib["Target"]
            if relation.attrib.get("TargetMode") == "External" or ":" in target or "\\" in target:
                raise ValueError("외부 슬라이드는 반입할 수 없습니다.")
            path = posixpath.normpath(posixpath.join("ppt", target))
            if not path.startswith("ppt/slides/") or not path.endswith(".xml"):
                raise ValueError("슬라이드 경로가 올바르지 않습니다.")
            root = xml(path)
            pieces, count, paragraph_seen, truncated = [], 0, False, False
            # Visit each XML node once; nested paragraphs cannot multiply text.
            for index, node in enumerate(root.iter()):
                if index > 500000 or index % 1024 == 0 and time.monotonic() >= deadline:
                    raise ValueError("슬라이드 구조·시간 한도를 초과했습니다.")
                value = ""
                if node.tag == drawing_ns + "p":
                    value = "\n" if paragraph_seen else ""
                    paragraph_seen = True
                elif node.tag == drawing_ns + "t":
                    value = node.text or ""
                elif node.tag == drawing_ns + "br":
                    value = "\n"
                elif node.tag == drawing_ns + "tab":
                    value = "\t"
                remaining = MAX_PAGE_CHARS - count
                pieces.append(value[:remaining]); count += min(len(value), remaining)
                if len(value) > remaining:
                    truncated = True
                    break
            text = "".join(pieces)
            characters += len(text)
            if characters > MAX_TOTAL_CHARS:
                raise ValueError("슬라이드 추출 텍스트 한도를 초과했습니다.")
            result.append({"page": number, "text": text, "truncated": truncated})
        return result


def category_for(name):
    normalized = unicodedata.normalize("NFC", name).lower()
    for category, words in (
        ("writing", ("라이팅", "writing")),
        ("graphics", ("grg", "graphic")),
        ("content", ("cog", "content operation")),
        ("foundation", ("hds", "design system")),
        ("interaction", ("ui가이드", "ui guideline", "ui_guideline")),
    ):
        if any(word in normalized for word in words):
            return category
    return "general"


def prepare(path):
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("압축파일은 400MiB 이하여야 합니다.")
    sources = []
    with zipfile.ZipFile(path) as archive:
        for entry in archive_entries(archive):
            if entry.is_dir():
                continue
            name = unicodedata.normalize("NFC", PurePosixPath(entry.filename).name)
            extension = PurePosixPath(name).suffix.lower()
            if extension not in (".pdf", ".pptx"):
                raise ValueError("가이드 묶음에는 PDF와 PPTX만 넣어 주세요.")
            data = archive.read(entry)
            digest = hashlib.sha256(data).hexdigest()
            pages = pdf_pages(data) if extension == ".pdf" else pptx_pages(data)
            sources.append({"id": f"source-{len(sources) + 1}", "name": name, "sha256": digest,
                            "category": category_for(name), "pageCount": len(pages), "pages": pages})
    return validate_pack({"format": FORMAT, "schemaVersion": 1, "sources": sources})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        pack = prepare(arguments.archive)
        encoded = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_PACK_BYTES:
            raise ValueError("가이드 묶음을 16MiB 이하로 나누어 주세요.")
        with arguments.output.open("xb") as output:
            output.write(encoded)
        print(f"Prepared {len(pack['sources'])} sources / {sum(source['pageCount'] for source in pack['sources'])} pages.")
        print("Text-only extract. Original PDF/PPTX files remain local; review against originals before approval.")
    except Exception:
        parser.exit(1, "Guideline preparation failed. Check the ZIP, supported PDF/PPTX files, limits and output path.\n")


if __name__ == "__main__":
    main()
