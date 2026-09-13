"""Literal local extraction and immutable intake publication, with no AWS network."""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_documents_library import CHUNK, api, approve, begin, call, finalize, path, project, revoke, upload


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
    assert api.storage.get("alice", "job", job["id"])["status"] == "failed"


def test_intake_domain_failure_through_worker_terminates_job_and_revision(api):
    from workspace.worker import Worker
    result = begin(api, data=b"abc")
    assert call(api, "PUT", path(result) + "/parts/0", b"xyz")[0] == 200
    result = call(api, "POST", path(result) + "/complete", {})[1]
    done = Worker(storage=api.storage).handle({"owner": "alice", "jobId": result["job"]["id"]})
    assert done["status"] == "failed" and done["failurePersisted"] is True
    assert call(api, "GET", "/jobs/" + result["job"]["id"])[1]["job"]["status"] == "failed"
    revision = api.storage.get("alice", "docrevision", result["revision"]["id"])
    assert revision["status"] == "failed" and revision["warnings"] == ["original-mismatch"]
    replay = call(api, "POST", path(result) + "/complete", {})[1]
    assert replay["job"]["status"] == replay["revision"]["status"] == "failed"


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


def pdf_with_form_or_image(encoding, *, depth=2):
    """A real raster clause with a selectable heading; no OCR substitutes."""
    from PIL import Image, ImageDraw
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf("Synthetic base rate: 10.00%"))))
    page = writer.pages[0]
    resources = page["/Resources"]
    image = Image.new("RGB", (250, 24), "white")
    ImageDraw.Draw(image).text((2, 2), "Synthetic exception: 25.00%", fill="black")
    pixels = image.tobytes()
    raster = DecodedStreamObject()
    raster.update({
        NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(250), NameObject("/Height"): NumberObject(24),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
    })
    raster.set_data(pixels)
    inline = b"BI /W 250 /H 24 /CS /RGB /BPC 8 ID " + pixels + b"\nEI\n"
    node = writer._add_object(raster)
    command = b"/Clause Do"
    if encoding in ("inline", "form-inline"):
        command = inline
    elif encoding == "form-text":
        command = b"BT /F1 12 Tf 10 40 Td (Synthetic exception: 25.00%) Tj ET"
    if encoding.startswith("form") or encoding == "cycle":
        for level in range(depth):
            form = DecodedStreamObject()
            form.update({
                NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 300, 100)]),
                NameObject("/Resources"): DictionaryObject({NameObject("/Font"): resources["/Font"]}),
            })
            if level or encoding not in ("form-inline", "form-text"):
                form["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Clause"): node})
            form.set_data(command)
            node = writer._add_object(form)
            command = b"/Clause Do"
        if encoding == "cycle":
            # A resource cycle need not be executed to require bounded inspection.
            form["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Cycle"): node})
            form.set_data(b"q Q")
    if encoding == "pattern-image":
        pattern = DecodedStreamObject()
        pattern.update({
            NameObject("/Type"): NameObject("/Pattern"), NameObject("/PatternType"): NumberObject(1),
            NameObject("/PaintType"): NumberObject(1), NameObject("/TilingType"): NumberObject(1),
            NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 250, 24)]),
            NameObject("/XStep"): NumberObject(250), NameObject("/YStep"): NumberObject(24),
            NameObject("/Resources"): DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/Clause"): node}),
            }),
        })
        pattern.set_data(b"q 250 0 0 24 0 0 cm /Clause Do Q")
        resources[NameObject("/Pattern")] = DictionaryObject({NameObject("/Clause"): writer._add_object(pattern)})
        command = b"/Pattern cs /Clause scn 0 0 250 24 re f"
    elif encoding == "annotation-image":
        appearance = DecodedStreamObject()
        appearance.update({
            NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 250, 24)]),
            NameObject("/Resources"): DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/Clause"): node}),
            }),
        })
        appearance.set_data(b"q 250 0 0 24 0 0 cm /Clause Do Q")
        annotation = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Stamp"),
            NameObject("/Rect"): ArrayObject([NumberObject(n) for n in (0, 0, 250, 24)]),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): writer._add_object(appearance)}),
        })
        page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
        command = b""
    elif encoding != "inline":
        resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Clause"): node})
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + b"\nq\n" + command + b"\nQ")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("encoding", ["direct", "form-image", "inline", "form-inline"])
def test_pdf_untranscribed_image_encodings_block_submit_and_review(api, encoding):
    original = pdf_with_form_or_image(encoding)
    result = finalize(api, upload(api, data=original, name="synthetic-clause.pdf", graphRef="REG-1"))
    text = "".join(p["text"] for p in result["paragraphs"])
    assert "Synthetic base rate: 10.00%" in text
    assert "Synthetic exception: 25.00%" not in text
    assert result["revision"]["parseStatus"] == "partial"
    assert "pdf-images-not-transcribed" in result["revision"]["warnings"]
    assert call(api, "POST", path(result) + "/submit", {"version": result["revision"]["version"]})[0] == 409
    assert call(api, "POST", path(result) + "/review", {
        "version": result["revision"]["version"], "decision": "approved", "note": "Must not omit the exception",
    })[0] == 409
    document = api.storage.get("alice", "document", result["document"]["id"])
    assert document["approvedRevisionId"] is None
    assert call(api, "GET", path(result) + "/blob")[1] == original


@pytest.mark.parametrize("encoding,depth", [("cycle", 1), ("form-text", 40)])
def test_pdf_resource_cycles_and_excessive_depth_fail_closed(encoding, depth):
    from documents.intake import extract
    result = extract("bounded.pdf", pdf_with_form_or_image(encoding, depth=depth))
    assert result["parseStatus"] != "complete" and result["warnings"]


def test_supported_text_only_nested_pdf_forms_remain_approvable(api):
    original = pdf_with_form_or_image("form-text")
    result = finalize(api, upload(api, data=original, name="text-form.pdf", graphRef="REG-1"))
    assert result["revision"]["parseStatus"] == "complete"
    assert "Synthetic exception: 25.00%" in "".join(p["text"] for p in result["paragraphs"])
    assert approve(api, result)[0] == 200


@pytest.mark.parametrize("encoding", ["pattern-image", "annotation-image"])
def test_pdf_unhandled_image_containers_cannot_claim_complete_extraction(api, encoding):
    original = pdf_with_form_or_image(encoding)
    result = finalize(api, upload(api, data=original, name="unhandled-image.pdf"))
    text = "".join(p["text"] for p in result["paragraphs"])
    assert "Synthetic base rate: 10.00%" in text and "Synthetic exception: 25.00%" not in text
    assert result["revision"]["parseStatus"] == "partial" and result["revision"]["warnings"]
    assert call(api, "POST", path(result) + "/submit", {"version": result["revision"]["version"]})[0] == 409
    assert call(api, "POST", path(result) + "/review", {
        "version": result["revision"]["version"], "decision": "approved", "note": "",
    })[0] == 409
    assert call(api, "GET", path(result) + "/blob")[1] == original


@pytest.mark.parametrize("clause", [
    '<svg xmlns="http://www.w3.org/2000/svg"><text>Synthetic exception rate 25.00%.</text></svg>',
    '<canvas>Synthetic exception rate 25.00%.</canvas>',
    '<img src="https://invalid.example/exception.png" alt="Synthetic exception rate 25.00%.">',
    '<iframe srcdoc="<p>Synthetic exception rate 25.00%.</p>"></iframe>',
    '<object data="https://invalid.example/exception.svg"></object>',
    '<embed src="https://invalid.example/exception.pdf">',
    '<video src="https://invalid.example/exception.mp4"></video>',
    '<input value="Synthetic exception rate 25.00%.">',
    '<script>document.write("Synthetic exception rate 25.00%.")</script>',
    '<p onmouseover="this.textContent=\'Synthetic exception rate 25.00%.\'">Hover</p>',
    '<style>p::after {content: "Synthetic exception rate 25.00%."}</style>',
    '<link rel="stylesheet" href="https://invalid.example/exception.css">',
    '<p style="background-image:url(https://invalid.example/exception.svg)">Background</p>',
    '<image src="https://invalid.example/exception.png">',
    '<div><template shadowrootmode="open"><p>Synthetic exception rate 25.00%.</p></template></div>',
    '<textarea><template>Synthetic exception rate 25.00%.</template></textarea>',
])
def test_html_unsupported_visible_or_active_content_blocks_approval(api, clause):
    original = ("<html><body><p>Synthetic base rate 10.00%.</p>" + clause + "</body></html>").encode()
    result = finalize(api, upload(api, data=original, name="mixed.html", graphRef="REG-1"))
    assert "Synthetic base rate 10.00%." in "".join(p["text"] for p in result["paragraphs"])
    assert result["revision"]["parseStatus"] == "partial" and result["revision"]["warnings"]
    assert call(api, "POST", path(result) + "/submit", {"version": result["revision"]["version"]})[0] == 409
    assert call(api, "POST", path(result) + "/review", {
        "version": result["revision"]["version"], "decision": "approved", "note": "Missing visible content",
    })[0] == 409
    assert api.storage.get("alice", "document", result["document"]["id"])["approvedRevisionId"] is None
    assert call(api, "GET", path(result) + "/blob")[1] == original


def test_static_html_text_and_noncontent_template_remain_approvable(api):
    original = b'<html><head><title>Metadata</title></head><body><p>Literal &amp; 10.00%</p><template>Inactive example</template><p>Exception 25.00%.</p></body></html>'
    result = finalize(api, upload(api, data=original, name="static.html"))
    assert result["revision"]["parseStatus"] == "complete"
    text = "".join(p["text"] for p in result["paragraphs"])
    assert "Literal & 10.00%" in text and "Exception 25.00%." in text
    assert "Inactive example" not in text and "Metadata" not in text
    assert approve(api, result)[0] == 200


@pytest.mark.parametrize("clause", ["<p>Visible exception 25.00%</p>", "Visible exception 25.00%"])
def test_visible_flow_or_text_in_head_cannot_be_approved_as_complete(api, clause):
    original = f"<html><head>{clause}</head><body><p>Base rate 10.00%</p></body></html>".encode()
    result = finalize(api, upload(api, data=original, name="head-flow.html"))
    assert result["revision"]["parseStatus"] != "complete"
    assert "html-visible-content-not-transcribed" in result["revision"]["warnings"]
    assert call(api, "POST", path(result) + "/submit", {"version": result["revision"]["version"]})[0] == 409


def pdf_with_vector_clause(encoding):
    """Selectable heading plus an outlined '25' that text extraction cannot read."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf("Synthetic base rate: 10.00%"))))
    page = writer.pages[0]
    # Seven-segment digits 2 and 5, painted as rectangles without a text operator.
    bars = [(10, 70, 20, 3), (27, 60, 3, 10), (10, 57, 20, 3), (10, 47, 3, 10), (10, 44, 20, 3),
            (40, 70, 20, 3), (40, 60, 3, 10), (40, 57, 20, 3), (57, 47, 3, 10), (40, 44, 20, 3)]
    paint = b"S" if encoding == "stroke" else b"f"
    command = b"\n".join((" ".join(map(str, bar)) + " re ").encode() + paint for bar in bars)
    if encoding == "form":
        form = DecodedStreamObject()
        form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                     NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 100, 100)]),
                     NameObject("/Resources"): DictionaryObject()})
        form.set_data(command)
        page["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Clause"): writer._add_object(form)})
        command = b"/Clause Do"
    elif encoding == "type3":
        glyph = DecodedStreamObject()
        glyph.set_data(b"100 0 d0\n" + command)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type3"),
            NameObject("/FontBBox"): ArrayObject([NumberObject(n) for n in (0, 0, 100, 100)]),
            NameObject("/FontMatrix"): ArrayObject([NumberObject(n) for n in (1, 0, 0, 1, 0, 0)]),
            NameObject("/CharProcs"): DictionaryObject({NameObject("/A"): writer._add_object(glyph)}),
            NameObject("/Encoding"): DictionaryObject({NameObject("/Type"): NameObject("/Encoding"),
                NameObject("/Differences"): ArrayObject([NumberObject(65), NameObject("/A")])}),
            NameObject("/FirstChar"): NumberObject(65), NameObject("/LastChar"): NumberObject(65),
            NameObject("/Widths"): ArrayObject([NumberObject(100)]),
            NameObject("/Resources"): DictionaryObject(),
        })
        page["/Resources"]["/Font"][NameObject("/Outline")] = writer._add_object(font)
        command = b"BT /Outline 1 Tf (A) Tj ET"
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + b"\nq\n" + command + b"\nQ")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("encoding", ["fill", "stroke", "form", "type3"])
def test_pdf_untranscribed_vector_clauses_block_approval_and_preserve_original(api, encoding):
    original = pdf_with_vector_clause(encoding)
    result = finalize(api, upload(api, data=original, name="outlined-clause.pdf", graphRef="REG-1"))
    text = "".join(p["text"] for p in result["paragraphs"])
    assert "Synthetic base rate: 10.00%" in text
    assert "25" not in text
    assert result["revision"]["parseStatus"] == "partial"
    warning = "pdf-type3-fonts-not-transcribed" if encoding == "type3" else "pdf-vector-graphics-not-transcribed"
    assert warning in result["revision"]["warnings"]
    assert call(api, "POST", path(result) + "/submit", {"version": result["revision"]["version"]})[0] == 409
    assert call(api, "POST", path(result) + "/review", {
        "version": result["revision"]["version"], "decision": "approved", "note": "Missing outlined number",
    })[0] == 409
    assert api.storage.get("alice", "document", result["document"]["id"])["approvedRevisionId"] is None
    assert call(api, "GET", path(result) + "/blob")[1] == original
