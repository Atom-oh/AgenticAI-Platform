"""Local parsing only. Original bytes are never modified or executed."""
from __future__ import annotations

import io
import json
import re
import warnings
from html.parser import HTMLParser
from pathlib import PurePath
from xml.etree import ElementTree

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_IMAGE_PIXELS = 24_000_000
MAX_PDF_PAGES = 20
EXTENSIONS = ("html", "htm", "png", "jpg", "jpeg", "svg", "pdf", "fig", "md", "markdown", "txt", "json", "css")


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, value):
        if not self.hidden and value.strip():
            self.text.append(value.strip())


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("UTF-8 텍스트 파일이 아닙니다.") from error
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        raise ValueError("텍스트가 아닌 제어 문자가 포함되어 있습니다.")
    return text


def _png(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _base(kind: str) -> dict:
    return {"format": kind, "text": "", "parseStatus": "complete", "pages": 1,
            "warnings": [], "resources": [], "previews": [], "truncated": False}


def _bound_text(result: dict) -> dict:
    if len(result["text"]) > MAX_TEXT_CHARS or len(result["text"].encode()) > 180_000:
        result["text"] = result["text"][:MAX_TEXT_CHARS].encode()[:180_000].decode("utf-8", "ignore")
        result["truncated"] = True
        result["parseStatus"] = "partial"
        result["warnings"].append("분석 크기 상한으로 텍스트 앞부분만 해석했습니다. 원본 전체는 보관됩니다.")
    return result


def _image(data: bytes, extension: str) -> dict:
    from PIL import Image, ImageOps
    result = _base("png" if extension == "png" else "jpeg")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as check:
                if check.format not in ("PNG", "JPEG") or (extension == "png") != (check.format == "PNG"):
                    raise ValueError("이미지 내용과 파일 확장자가 일치하지 않습니다.")
                if check.width * check.height > MAX_IMAGE_PIXELS:
                    raise ValueError("이미지는 2,400만 화소 이하여야 합니다.")
                check.verify()
            with Image.open(io.BytesIO(data)) as original:
                image = ImageOps.exif_transpose(original).convert("RGBA" if extension == "png" else "RGB")
                result["dimensions"] = {"width": image.width, "height": image.height}
                image.thumbnail((1920, 2160))
                result["previews"] = [{"page": 1, "mime": "image/png", "data": _png(image),
                                       "width": image.width, "height": image.height}]
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("손상되었거나 지원하지 않는 이미지입니다.") from error
    return result


def _svg(data: bytes) -> dict:
    from defusedxml import ElementTree as SafeXML
    result = _base("svg")
    try:
        root = SafeXML.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as error:
        raise ValueError("안전하게 해석할 수 없는 SVG입니다. DTD·엔티티 또는 손상을 확인하세요.") from error
    local = lambda tag: tag.split("}")[-1].lower() if isinstance(tag, str) else ""
    if local(root.tag) != "svg":
        raise ValueError("SVG 루트 요소가 없습니다.")
    removed = 0
    forbidden = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video",
                 "animate", "animatemotion", "animatetransform", "set"}
    for parent in list(root.iter()):
        for child in list(parent):
            if local(child.tag) in forbidden:
                parent.remove(child)
                removed += 1
        for key, value in list(parent.attrib.items()):
            name = local(key)
            bad = name.startswith("on") or name in ("base", "src")
            if name in ("href", "xlink:href"):
                bad = not (value.startswith("#") or value.startswith(("data:image/png;", "data:image/jpeg;")))
            if re.search(r"url\s*\(", value, re.I):
                bad = bool(re.search(r"url\s*\(\s*[\"']?(?!#)[^)]", value, re.I))
            if bad:
                del parent.attrib[key]
                removed += 1
        if local(parent.tag) == "style" and parent.text:
            if re.search(r"@import|url\s*\(", parent.text, re.I):
                parent.text = ""
                removed += 1
    text = [element.text.strip() for element in root.iter()
            if local(element.tag) in ("text", "title", "desc") and element.text and element.text.strip()]
    result["text"] = "\n".join(text)
    sanitized = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    result["previews"] = [{"page": 1, "mime": "image/svg+xml", "data": sanitized}]
    if removed:
        result["parseStatus"] = "partial"
        result["warnings"].append(f"미리보기에서 실행·외부 참조 요소 {removed}개를 제외했습니다.")
        result["resources"] = ["SVG active/external references"]
    return _bound_text(result)


def _pdf(data: bytes) -> dict:
    import pypdfium2 as pdfium
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise ValueError("PDF 내용과 확장자가 일치하지 않습니다.")
    result = _base("pdf")
    document = None
    try:
        document = pdfium.PdfDocument(data)
        count = len(document)
        if count == 0:
            raise ValueError("PDF에 페이지가 없습니다.")
        result["pages"] = count
        texts = []
        for index in range(min(count, MAX_PDF_PAGES)):
            page = document[index]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range()[:20_000]
                    texts.append(f"[페이지 {index + 1}]\n{text}")
                finally:
                    text_page.close()
                width, height = page.get_size()
                if width <= 0 or height <= 0 or max(width, height) > 100_000:
                    raise ValueError("PDF 페이지 크기가 지원 범위를 넘습니다.")
                scale = min(1600 / max(width, height), 2)
                bitmap = page.render(scale=scale, may_draw_forms=False)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    result["previews"].append({"page": index + 1, "mime": "image/png", "data": _png(image),
                                               "width": image.width, "height": image.height})
                finally:
                    bitmap.close()
            finally:
                page.close()
        result["text"] = "\n\n".join(texts)
        if count > MAX_PDF_PAGES:
            result["parseStatus"] = "partial"
            result["warnings"].append(f"총 {count}페이지 중 앞 {MAX_PDF_PAGES}페이지만 해석했습니다.")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("손상·암호화되었거나 해석할 수 없는 PDF입니다.") from error
    finally:
        if document is not None:
            document.close()
    return _bound_text(result)


def extract_file(name: str, data: bytes) -> dict:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES:
        raise ValueError("파일은 비어 있지 않은 50MiB 이하의 파일이어야 합니다.")
    extension = PurePath(name).suffix.lower().lstrip(".")
    if extension not in EXTENSIONS:
        raise ValueError("지원하지 않는 파일 형식입니다.")
    if extension in ("png", "jpg", "jpeg"):
        return _image(data, extension)
    if extension == "svg":
        return _svg(data)
    if extension == "pdf":
        return _pdf(data)
    result = _base(extension)
    if extension == "fig":
        result.update(parseStatus="unsupported", pages=0)
        result["warnings"] = ["FIG 원본만 보관했습니다. 내부 화면 해석은 지원하지 않습니다. PNG/PDF를 함께 반입하면 참고할 수 있습니다."]
        return result
    text = _decode(data)
    if extension in ("html", "htm"):
        from studio.artifacts import external_references, secure_html
        parsed = TextOnly()
        try:
            parsed.feed(text)
            parsed.close()
        except Exception as error:
            raise ValueError("HTML 구조를 해석하지 못했습니다.") from error
        result["text"] = "\n".join(parsed.text)
        result["resources"] = external_references(text)
        if result["resources"]:
            result["parseStatus"] = "partial"
            result["warnings"].append("외부 리소스를 내려받지 않았습니다. 누락 목록을 확인하세요.")
        # The extra restrictive policy is preserved by secure_html. Preview is
        # additionally sandboxed by its parent; originals remain separate.
        preview = secure_html(text, allow_scripts=False)
        result["previews"] = [{"page": 1, "mime": "text/html", "data": preview.encode()}]
    else:
        if extension == "json":
            try:
                json.loads(text)
            except (ValueError, RecursionError) as error:
                raise ValueError("올바른 JSON 파일이 아닙니다.") from error
        result["text"] = text
        if extension == "css":
            from studio.artifacts import external_references
            result["resources"] = external_references("<style>" + text + "</style>")
            if result["resources"]:
                result["parseStatus"] = "partial"
                result["warnings"].append("스타일 파일의 외부 참조는 불러오지 않았습니다.")
    return _bound_text(result)
