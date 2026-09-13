"""Literal local extraction and immutable intake publication, with no AWS network."""
from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from test_documents_library import CHUNK, api, begin, call, finalize, path, project, revoke, upload


def pdf(*pages, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=400)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 10 100 Td (" + text + ") Tj ET").encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("name", ["terms.txt", "terms.md", "terms.markdown"])
def test_extraction_preserves_literal_unicode_whitespace_and_financial_values(name):
    from documents.intake import extract
    source = " \t# **원문** 0010.00%  \r\n\r\n<script>literal</script>\n"
    result = extract(name, source.encode())
    assert result["parseStatus"] == "complete"
    assert "".join(p["text"] for p in result["paragraphs"]) == source
    for paragraph in result["paragraphs"]:
        assert paragraph["page"] is None
        assert paragraph["sha256"] == hashlib.sha256(paragraph["text"].encode()).hexdigest()


def test_html_extracts_text_without_running_or_fetching_resources():
    from documents.intake import extract
    source = b'<html><head><style>secret-css</style></head><body><p>Literal &amp; 10.00%</p><script>secret-js</script><img src="https://invalid.example/pixel"><p>Second</p></body></html>'
    result = extract("policy.html", source)
    text = "".join(p["text"] for p in result["paragraphs"])
    assert "Literal & 10.00%" in text and "Second" in text
    assert "secret-js" not in text and "secret-css" not in text and "https://" not in text


def test_pdf_extracts_local_text_and_page_numbers():
    from documents.intake import extract
    result = extract("policy.pdf", pdf("First 10.00%", "Second"))
    assert result["parseStatus"] == "complete" and result["pages"] == 2
    assert any("First 10.00%" in p["text"] and p["page"] == 1 for p in result["paragraphs"])
    assert any("Second" in p["text"] and p["page"] == 2 for p in result["paragraphs"])


@pytest.mark.parametrize("name,data", [
    ("empty.txt", b" \n\t"), ("invalid.txt", b"\xff\xfe"),
    ("damaged.pdf", b"%PDF-damaged synthetic"),
    ("encrypted.pdf", None), ("scanned.pdf", b"blank-pdf"),
])
def test_incomplete_extraction_is_reported_and_cannot_be_approved(api, name, data):
    if data is None:
        data = pdf("Restricted", encrypted=True)
    elif data == b"blank-pdf":
        data = pdf("")
    result = finalize(api, upload(api, data=data, name=name))
    assert result["revision"]["parseStatus"] != "complete"
    assert result["revision"]["warnings"]
    assert call(api, "POST", path(result) + "/submit", {"version": result["revision"]["version"]})[0] == 409
    assert call(api, "GET", path(result) + "/blob")[1] == data


def test_text_character_and_paragraph_limits_report_truncation():
    from documents.intake import extract
    source = "한" * 400_001
    result = extract("long.txt", source.encode())
    assert sum(len(p["text"]) for p in result["paragraphs"]) == 400_000
    assert all(len(p["text"]) <= 2_000 for p in result["paragraphs"])
    assert result["parseStatus"] != "complete" and result["warnings"]


def test_pdf_page_limit_is_not_silently_approved():
    from documents.intake import extract
    result = extract("long.pdf", pdf(*["Page"] * 201))
    assert result["parseStatus"] != "complete"
    assert result["warnings"]
    assert all(p["page"] <= 200 for p in result["paragraphs"])


def test_hash_mismatch_never_publishes_a_revision(api):
    from documents.errors import DocumentError
    from documents.intake import finalize as process
    result = begin(api, data=b"abc")
    assert call(api, "PUT", path(result) + "/parts/0", b"xyz")[0] == 200
    result = call(api, "POST", path(result) + "/complete", {})[1]
    job = api.storage.claim_job("alice", result["job"]["id"])
    with pytest.raises(DocumentError):
        process(SimpleNamespace(storage=api.storage, collaboration=api.collaboration), "alice", job)
    saved = api.storage.get("alice", "docrevision", result["revision"]["id"])
    assert saved["status"] == "failed" and not saved.get("textHash")


def test_acl_revocation_at_finalize_commit_never_publishes_text(api):
    from documents.errors import DocumentError
    from documents.intake import finalize as process
    from workspace.storage import Conflict
    pid = project(api)
    result = upload(api, actor="bob", project=pid)
    owner = f"project:{pid}"
    job = api.storage.claim_job(owner, result["job"]["id"])
    api.storage.table().before_transaction = lambda: revoke(api, pid)
    with pytest.raises((DocumentError, Conflict)):
        process(SimpleNamespace(storage=api.storage, collaboration=api.collaboration), owner, job)
    saved = api.storage.get(owner, "docrevision", result["revision"]["id"])
    assert saved["status"] != "draft" and not saved.get("textHash")


def test_finalization_is_idempotent_and_does_not_mutate_projection(api):
    from documents.intake import finalize as process
    result = upload(api)
    job = api.storage.claim_job("alice", result["job"]["id"])
    worker = SimpleNamespace(storage=api.storage, collaboration=api.collaboration)
    process(worker, "alice", job)
    before = api.storage.get("alice", "docrevision", result["revision"]["id"])
    process(worker, "alice", job)
    after = api.storage.get("alice", "docrevision", result["revision"]["id"])
    assert before == after
    projection = json.loads(api.storage.get_blob(after["projectionKey"]))
    assert set(projection) == {"schemaVersion", "paragraphs", "pages", "parseStatus", "warnings"}


def test_dense_paragraphs_within_text_limit_are_retained(api):
    data = b"a\n\n" * 133_333
    result = finalize(api, upload(api, data=data))
    assert result["revision"]["parseStatus"] == "complete"
    assert result["totalParagraphs"] == 133_333


def test_changed_part_with_extra_bytes_after_queue_is_not_silently_truncated(api):
    from documents.errors import DocumentError
    from documents.intake import finalize as process
    data = b"x" * CHUNK
    result = upload(api, data=data)
    revision = api.storage.get("alice", "docrevision", result["revision"]["id"])
    api.storage.put_blob(revision["parts"]["0"]["key"], data + b"changed", "application/octet-stream")
    job = api.storage.claim_job("alice", result["job"]["id"])
    with pytest.raises(DocumentError):
        process(SimpleNamespace(storage=api.storage, collaboration=api.collaboration), "alice", job)
    stored = api.storage.get("alice", "docrevision", revision["id"])
    assert stored["status"] == "failed" and not stored.get("textHash")
