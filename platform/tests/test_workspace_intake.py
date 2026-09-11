import io

import pytest
from PIL import Image

from workspace.intake import extract_file


def image_bytes(format="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (390, 844), "white").save(out, format=format)
    return out.getvalue()


def test_images_have_private_derivatives_and_original_dimensions():
    for name, kind in (("a.png", "PNG"), ("a.jpg", "JPEG")):
        data = image_bytes(kind)
        result = extract_file(name, data)
        assert result["parseStatus"] == "complete"
        assert result["dimensions"] == {"width": 390, "height": 844}
        assert result["previews"][0]["data"].startswith(b"\x89PNG")
        assert data == image_bytes(kind)


def test_wrong_image_extension_and_corrupt_content_rejected():
    with pytest.raises(ValueError):
        extract_file("a.png", image_bytes("JPEG"))
    with pytest.raises(ValueError):
        extract_file("a.jpg", b"not a jpeg")


def test_transparent_png_is_not_flattened_to_a_black_rectangle():
    source = io.BytesIO()
    Image.new("RGBA", (30, 30), (255, 255, 255, 0)).save(source, format="PNG")
    result = extract_file("logo.png", source.getvalue())
    with Image.open(io.BytesIO(result["previews"][0]["data"])) as preview:
        assert preview.mode == "RGBA"
        assert preview.getpixel((0, 0))[3] == 0


def test_html_reports_external_resources_but_does_not_fetch_or_execute():
    data = b'<HTML><HEAD></HEAD><BODY><h1>Hello</h1><script>fetch("https://invalid.test")</script></BODY></HTML>'
    result = extract_file("screen.html", data)
    assert "Hello" in result["text"] and "fetch" not in result["text"]
    assert result["parseStatus"] == "partial" and result["resources"]
    assert b"script-src 'none'" in result["previews"][0]["data"]


def test_svg_entities_rejected_and_active_content_excluded():
    with pytest.raises(ValueError):
        extract_file("bad.svg", b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>')
    result = extract_file("icon.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><image href="https://invalid.test/x"/><path d="M0 0L10 10"/></svg>')
    assert result["parseStatus"] == "partial"
    preview = result["previews"][0]["data"]
    assert b"<script" not in preview and b"https://invalid" not in preview and b"path" in preview


def test_fig_is_preserved_as_an_uninterpreted_archive():
    result = extract_file("source.fig", b"fig-kiwi" + bytes(range(100)))
    assert result["parseStatus"] == "unsupported"
    assert not result["previews"] and result["warnings"]


def test_text_json_and_limits_are_honest():
    assert extract_file("SKILL.md", b"\xef\xbb\xbf# Guide\nDo not lose state")["text"].startswith("# Guide")
    with pytest.raises(ValueError):
        extract_file("tokens.json", b"{bad")
    result = extract_file("long.txt", b"x" * 200_001)
    assert result["truncated"] and result["parseStatus"] == "partial"
    with pytest.raises(ValueError):
        extract_file("run.exe", b"binary")


def test_pdf_has_page_preview_and_extracted_text():
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=390, height=844)
    data = io.BytesIO()
    writer.write(data)
    result = extract_file("spec.pdf", data.getvalue())
    assert result["pages"] == 1
    assert result["previews"][0]["mime"] == "image/png"
    assert result["parseStatus"] == "complete"
