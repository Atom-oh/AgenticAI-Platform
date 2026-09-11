"""Resolve only explicitly selected local CSS/image files, without network I/O."""
from __future__ import annotations

import html as markup
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]+))\s*\)""", re.I)
MAX_HTML_BYTES = 1_000_000


def replace_bounded(source: str, old: str, new: str, maximum: int = MAX_HTML_BYTES) -> str:
    predicted = len(source.encode()) + source.count(old) * (len(new.encode()) - len(old.encode()))
    if predicted > maximum:
        raise ValueError("연결한 리소스를 포함한 HTML이 1MB를 넘습니다. 자료를 나누거나 이미지 크기를 줄여 주세요.")
    return source.replace(old, new)


def _name(reference):
    try:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        return PurePosixPath(unquote(parsed.path)).name
    except ValueError:
        return None


def bundle_html(source: str, files: dict[str, dict], maximum: int = MAX_HTML_BYTES) -> tuple[str, list[str]]:
    """files contains selected unique names with either css text or image uri."""
    used = set()
    if len(source.encode()) > maximum:
        raise ValueError("검사할 HTML이 1MB를 넘습니다.")

    def css(text):
        pieces, cursor, size = [], 0, 0
        for match in URL.finditer(text):
            value = next(part for part in match.groups() if part is not None)
            name = _name(value)
            asset = files.get(name) if name else None
            replacement = match.group(0)
            if asset and asset["kind"] == "image":
                used.add(asset["id"])
                replacement = f'url("{asset["uri"]}")'
            prefix = text[cursor:match.start()]
            size += len(prefix.encode()) + len(replacement.encode())
            if size > maximum:
                raise ValueError("스타일의 반복 이미지 참조가 HTML 크기 상한을 넘습니다.")
            pieces.extend((prefix, replacement))
            cursor = match.end()
        tail = text[cursor:]
        if size + len(tail.encode()) > maximum:
            raise ValueError("스타일 내용이 HTML 크기 상한을 넘습니다.")
        pieces.append(tail)
        return "".join(pieces)

    class Rewriter(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.starts = [0] + [match.end() for match in re.finditer("\n", source)]
            self.edits = []
            self.in_style = False
            self.bytes = len(source.encode())

        def add_edit(self, start, end, replacement):
            predicted = self.bytes - len(source[start:end].encode()) + len(replacement.encode())
            if predicted > maximum:
                raise ValueError("반복된 로컬 리소스가 HTML 크기 상한을 넘습니다.")
            self.bytes = predicted
            self.edits.append((start, end, replacement))

        def _source_offset(self):
            line, column = self.getpos()
            return self.starts[line - 1] + column

        def handle_starttag(self, tag, attrs):
            if tag == "style":
                self.in_style = True
            values = {}
            for name, value in attrs:
                values.setdefault(name, value)
            selected = files.get(_name(values.get("href") or ""))
            replacement = None
            if tag == "link" and "stylesheet" in (values.get("rel") or "").lower().split() and selected and selected["kind"] == "css":
                # Do not discard a supplied integrity requirement without verification.
                if (values.get("integrity") or "disabled" in values
                        or "alternate" in (values.get("rel") or "").lower().split()
                        or any(name.startswith("on") for name in values)):
                    return
                used.add(selected["id"])
                content = re.sub(r"</style", r"<\\/style", css(selected["text"]), flags=re.I)
                applicability = "".join(f' {name}="{markup.escape(values[name], quote=True)}"'
                                        for name in ("media", "title", "nonce", "type", "id") if values.get(name) is not None)
                replacement = f'<style data-import-asset="{selected["id"]}"{applicability}>{content}</style>'
            else:
                changed = False
                for attr in ("src", "poster", "href", "xlink:href"):
                    # Only image-loading tags, not anchors, scripts, frames or objects.
                    if tag not in ("img", "source", "image", "feimage", "input", "video"):
                        continue
                    resource = files.get(_name(values.get(attr) or ""))
                    if resource and resource["kind"] == "image":
                        values[attr] = resource["uri"]
                        used.add(resource["id"])
                        changed = True
                if values.get("style"):
                    rewritten = css(values["style"])
                    changed = changed or rewritten != values["style"]
                    values["style"] = rewritten
                if changed:
                    encoded = "".join(f" {name}" if value is None else f' {name}="{markup.escape(value, quote=True)}"'
                                      for name, value in values.items())
                    replacement = f"<{tag}{encoded}>"
            if replacement is not None:
                start = self._source_offset()
                self.add_edit(start, start + len(self.get_starttag_text()), replacement)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

        def handle_endtag(self, tag):
            if tag == "style":
                self.in_style = False

        def handle_data(self, data):
            if self.in_style:
                rewritten = css(data)
                if rewritten != data:
                    start = self._source_offset()
                    self.add_edit(start, start + len(data), rewritten)

    parser = Rewriter()
    parser.feed(source)
    parser.close()
    result, cursor = [], 0
    for start, end, replacement in sorted(parser.edits):
        result.extend((source[cursor:start], replacement))
        cursor = end
    result.append(source[cursor:])
    return "".join(result), sorted(used)
