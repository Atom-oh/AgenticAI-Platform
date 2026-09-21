"""Prepare large private source archives as bounded, resumable reference packs.

No source code, build hook, external link or protected document is executed.
Outputs stay local until uploaded through the authenticated workspace intake.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from workspace.guidelines import FORMAT, MAX_PACK_BYTES, MAX_PAGE_CHARS, MAX_PAGES, MAX_TOTAL_CHARS, validate_pack
from workspace.prepare_guidelines import archive_entries, category_for, pdf_pages, pptx_pages

SOURCE_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js", ".scss", ".css"}
SUPPORTED = SOURCE_EXTENSIONS | {".pdf", ".pptx", ".txt", ".md", ".xlsx"}


def source_record(name, data, *, isolate_pdf=False):
    original_name = name
    name = unicodedata.normalize("NFC", name)
    path = PurePosixPath(name)
    extension = path.suffix.lower()
    if extension not in SUPPORTED:
        raise ValueError("unsupported-format")
    if data.startswith((b"SCDSA002", b"SCDSA004")):
        raise ValueError("protected-original")
    digest = hashlib.sha256(data).hexdigest()
    metadata = {"path": original_name}
    if extension == ".pdf":
        if isolate_pdf:
            from workspace.source_parse_task import isolated_pdf_pages
            pages = isolated_pdf_pages(data)
        else:
            pages = pdf_pages(data)
        category = category_for(name)
    elif extension == ".pptx":
        pages, category = pptx_pages(data), "specification"
    elif extension == ".xlsx":
        pages, category = workbook_pages(data), "inventory"
    else:
        prefix = data[:4 * (MAX_PAGE_CHARS + 1)]
        value = codecs.getincrementaldecoder("utf-8-sig")().decode(prefix, final=len(prefix) == len(data))
        if "\x00" in value:
            raise ValueError("non-text-original")
        code = extension in SOURCE_EXTENSIONS or bool(re.search(r"(?:import\s+[\s\S]{0,300}\bfrom\s+['\"]|export\s+(?:default|const)|<[A-Z]\w+[\s/>])", value))
        category = "source" if code else "general"
        if code:
            meta = re.search(r"export\s+const\s+meta\s*=\s*`([^`]{0,12000})`", value)
            if meta:
                for key in ("sid", "pver", "dver", "sync", "mdate", "status"):
                    match = re.search(rf"^\s*{key}\s*:\s*(.+)$", meta[1], re.M)
                    if match:
                        metadata[key] = match[1].strip()[:1024]
            metadata["imports"] = ", ".join(sorted(set(re.findall(r"\bfrom\s+['\"]([^'\"]+)['\"]", value))))[:1024]
        pages = [{"page": 1, "text": value[:MAX_PAGE_CHARS],
                  "truncated": len(prefix) < len(data) or len(value) > MAX_PAGE_CHARS}]
    return {"id": "src-" + hashlib.sha256((name + digest).encode()).hexdigest()[:24],
            "name": path.name, "sha256": digest, "category": category, "metadata": metadata,
            "pageCount": len(pages), "pages": pages}


def workbook_pages(data):
    """OOXML cells only; preserve sheet/row coordinates, never evaluate formulas."""
    from defusedxml import ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive_entries(archive, maximum=3000, total_limit=128 * 1024 * 1024)
        def xml(name):
            if archive.getinfo(name).file_size > 16 * 1024 * 1024:
                raise ValueError("workbook-xml-too-large")
            return ET.fromstring(archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True)
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_chars = 0
            for item in xml("xl/sharedStrings.xml"):
                value = "".join(node.text or "" for node in item.iter(ns + "t"))
                shared_chars += len(value)
                if len(value) > MAX_PAGE_CHARS or shared_chars > MAX_TOTAL_CHARS or len(strings) >= 500000:
                    raise ValueError("workbook-shared-string-limit")
                strings.append(value)
        pages, characters, cell_count, row_count = [], 0, 0, 0
        for name in sorted(archive.namelist()):
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            # Groups of rows are searchable pages; each retains exact OOXML coordinates.
            lines = []
            for row in xml(name).iter(ns + "row"):
                row_count += 1
                row_id = row.get("r", "?")
                if row_count > 100000 or len(row_id) > 10:
                    raise ValueError("workbook-row-limit")
                cells, row_chars = [], len(name) + len(row_id) + 10
                for cell in row.findall(ns + "c"):
                    cell_count += 1
                    reference = cell.get("r", "?")
                    if cell_count > 500000 or len(reference) > 16:
                        raise ValueError("workbook-cell-limit")
                    node = cell.find(ns + "v")
                    value = node.text or "" if node is not None else "".join(t.text or "" for t in cell.iter(ns + "t"))
                    if cell.get("t") == "s":
                        index = int(value)
                        if not 0 <= index < len(strings):
                            raise ValueError("workbook-shared-string-reference")
                        value = strings[index]
                    if value:
                        addition = len(reference) + len(value) + 5
                        row_chars += addition
                        if row_chars > MAX_PAGE_CHARS or characters + row_chars > MAX_TOTAL_CHARS:
                            raise ValueError("workbook-extraction-limit")
                        cells.append(f"{reference}: {value}")
                line = f"{name} row {row_id}: " + " | ".join(cells)
                characters += len(line) + 1
                if characters > MAX_TOTAL_CHARS:
                    raise ValueError("workbook-extraction-limit")
                if lines and sum(map(len, lines)) + len(line) > 15000:
                    pages.append({"page": len(pages) + 1, "text": "\n".join(lines)})
                    if len(pages) >= MAX_PAGES:
                        raise ValueError("workbook-page-limit")
                    lines = []
                lines.append(line)
            if lines:
                content = "\n".join(lines)
                pages.append({"page": len(pages) + 1, "text": content[:MAX_PAGE_CHARS], "truncated": len(content) > MAX_PAGE_CHARS})
        return pages


def prepare_archive(path, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records, errors, written = [], [], []
    def flush():
        if not records:
            return
        pack = validate_pack({"format": FORMAT, "schemaVersion": 1, "sources": records})
        data = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > MAX_PACK_BYTES:
            raise ValueError("reference-pack-too-large")
        name = "sources-" + hashlib.sha256(data).hexdigest()[:24] + ".json"
        target = output / name
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("existing-output-mismatch")
        else:
            with target.open("xb") as stream:
                stream.write(data)
        written.append(name)
        records.clear()
    with zipfile.ZipFile(path) as archive:
        # Corpus inventory is bounded independently of model context and HTTP uploads.
        entries = archive_entries(archive, maximum=30000, total_limit=8 * 1024**3)
        for entry in sorted(entries, key=lambda item: item.filename):
            if entry.is_dir() or PurePosixPath(entry.filename).name == ".DS_Store":
                continue
            if PurePosixPath(entry.filename).suffix.lower() not in SUPPORTED:
                errors.append({"path": entry.filename, "reason": "unsupported-format"})
                continue
            try:
                source = source_record(entry.filename, archive.read(entry))
                single = validate_pack({"format": FORMAT, "schemaVersion": 1, "sources": [source]})
                if len(json.dumps(single, ensure_ascii=False).encode()) > MAX_PACK_BYTES:
                    raise ValueError("source-pack-too-large")
            except Exception as error:
                errors.append({"path": entry.filename, "reason": str(error) if isinstance(error, ValueError) and
                               str(error) in ("protected-original", "unsupported-format", "source-pack-too-large") else "parse-failed"})
                continue
            if records and (len(records) >= 20 or sum(len(s["pages"]) for s in records) + len(source["pages"]) > 1200
                            or sum(len(p["text"]) for s in records for p in s["pages"]) +
                            sum(len(p["text"]) for p in source["pages"]) > 2_500_000
                            or len(json.dumps({"format": FORMAT, "schemaVersion": 1, "sources": records + [source]},
                                              ensure_ascii=False).encode()) > MAX_PACK_BYTES):
                flush()
            records.append(source)
        flush()
    receipt = {"archive": Path(path).name, "packs": written, "excluded": errors, "originalStatus": "local-only",
               "reviewStatus": "unreviewed", "execution": "none"}
    receipt_path = output / ("receipt-" + hashlib.sha256(Path(path).name.encode()).hexdigest()[:16] + ".json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = prepare_archive(args.archive, args.output_dir)
    except Exception:
        parser.exit(1, "Source preparation failed. Check archive limits and the output directory; completed packs remain reusable.\n")
    print(f"Prepared {len(receipt['packs'])} reference packs; excluded {len(receipt['excluded'])} originals. Review the local receipt.")


if __name__ == "__main__":
    main()
