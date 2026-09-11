"""Offline inspection and CSP protection for self-contained Studio HTML.

These helpers only process strings: they never resolve URLs or fetch resources.
Figma is unavailable in network 3. External deliverables enter through the
approved file-import process; Studio never fetches their external originals.
Relative URLs also require a fetch and are therefore reported.

Static inspection is a review aid, not a complete JavaScript/CSS/HTML security
analysis. CSP must enforce the resource boundary even for dynamically built URLs.
A meta CSP cannot replace a browser sandbox or block every top-level navigation.
The parent must enforce browser isolation and run browser network-denial checks.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["external_references", "secure_html"]

_SPACE = " \t\n\r\f"
_DATA_ASSET = re.compile(
    r"data:(?:image/[\w.+-]+|font/[\w.+-]+|application/"
    r"(?:font-woff|font-sfnt|vnd\.ms-fontobject|x-font-(?:ttf|otf|woff|truetype|opentype)))"
    r"[;,]", re.I,
)
_CSS_COMMENTS = re.compile(r"/\*.*?\*/", re.S)
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-f]{1,6})(?:\r\n|[ \t\r\n\f])?|([^\r\n\f])|\r\n|[\r\n\f])", re.I)
_CSS_VALUE = r"""(?:"([^"]*)"|'([^']*)'|([^\s;)]+))"""
_CSS_IMPORT = re.compile(r"@import\s*(?:url\s*\(\s*)?" + _CSS_VALUE, re.I)
_CSS_URL = re.compile(r"""\burl\s*\(\s*(?:"([^"]*)"|'([^']*)'|([^\s)]+))""", re.I)
_JS_CALLS = tuple((label, re.compile(pattern)) for label, pattern in (
    ("JavaScript fetch", r"\bfetch\s*\("),
    ("JavaScript XMLHttpRequest", r"\bXMLHttpRequest\b"),
    ("JavaScript beacon", r"\bnavigator\s*\.\s*sendBeacon\s*\("),
    ("JavaScript WebSocket", r"\bWebSocket\s*\("),
    ("JavaScript EventSource", r"\bEventSource\s*\("),
    ("JavaScript worker", r"\b(?:SharedWorker|Worker|importScripts)\s*\("),
    ("JavaScript service worker", r"\bserviceWorker\s*\.\s*register\s*\("),
    ("JavaScript module import", r"""\bimport\s*\(|\b(?:import|export)\s+(?:[^;]*?\s+from\s*)?["']"""),
))
_JS_ASSIGNMENT = re.compile(r"""\.\s*(?:src|srcset|href)\s*=\s*(["'])(.*?)\1""", re.S)
_SRC_TAGS = {"script", "img", "iframe", "frame", "audio", "video", "source", "track", "input", "embed"}
_SVG_HREF_TAGS = {
    "use", "image", "feimage", "script", "animate", "animatemotion",
    "animatetransform", "set", "lineargradient", "radialgradient", "pattern",
    "filter", "textpath", "mpath", "tref", "font-face-uri", "cursor",
}
_CSS_ATTRIBUTES = {"style", "fill", "stroke", "filter", "clip-path", "mask", "cursor",
                   "marker", "marker-start", "marker-mid", "marker-end"}
_PASSIVE_LINKS = {"canonical", "alternate", "author", "help", "license", "next", "prev", "bookmark", "tag"}

_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:; "
    "connect-src 'none'; frame-src 'none'; child-src 'none'; "
    "worker-src 'none'; object-src 'none'; media-src 'none'; manifest-src 'none'; "
    "form-action 'none'; base-uri 'none'"
)
_CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">'


def _external(value: str) -> bool:
    value = value.strip(_SPACE)
    return bool(value and not value.startswith("#") and not _DATA_ASSET.match(value))


def _css_unescape(match: re.Match) -> str:
    if match[1]:
        codepoint = int(match[1], 16)
        return chr(codepoint) if 0 < codepoint <= 0x10ffff else "\ufffd"
    return match[2] or ""


def _srcset_urls(value: str):
    """Read candidates without mistaking a data URL's comma for a separator."""
    position = 0
    while position < len(value):
        while position < len(value) and value[position] in _SPACE + ",":
            position += 1
        start = position
        while position < len(value) and value[position] not in _SPACE:
            position += 1
        url = value[start:position]
        if url:
            yield url.rstrip(",")
        if url.endswith(","):
            continue
        parentheses = 0
        while position < len(value):
            char = value[position]
            position += 1
            if char == "," and not parentheses:
                break
            if char == "(":
                parentheses += 1
            elif char == ")" and parentheses:
                parentheses -= 1


class _References(HTMLParser):
    def __init__(self, findings: dict[str, None], depth: int = 0) -> None:
        super().__init__(convert_charrefs=True)
        self.findings = findings
        self.depth = depth
        self.raw_tag = ""

    def _report(self, description: str) -> None:
        # Only fixed labels enter findings: never URLs, attribute values or code.
        self.findings[description] = None

    def _resource(self, description: str, value: str) -> None:
        if _external(value):
            self._report(description)

    def _css(self, css: str) -> None:
        css = _CSS_ESCAPE.sub(_css_unescape, _CSS_COMMENTS.sub("", css))
        for pattern, label in ((_CSS_IMPORT, "CSS @import"), (_CSS_URL, "CSS url()")):
            for match in pattern.finditer(css):
                self._resource(label, next(group for group in match.groups() if group is not None))

    def _javascript(self, script: str) -> None:
        # Deliberately heuristic: aliases, obfuscation and computed URLs need CSP.
        # Comments/string literals can also produce conservative false positives.
        for label, pattern in _JS_CALLS:
            if pattern.search(script):
                self._report(label)
        for match in _JS_ASSIGNMENT.finditer(script):
            self._resource("JavaScript resource assignment", match[2])

    def handle_starttag(self, tag, attrs):
        # Browsers use the first occurrence of a duplicate HTML attribute.
        attributes = {}
        for name, value in attrs:
            attributes.setdefault(name, value or "")
        if tag in ("script", "style"):
            self.raw_tag = tag
        if tag in _SRC_TAGS and "src" in attributes:
            self._resource(f"{tag} src", attributes["src"])
        if tag == "video":
            self._resource("video poster", attributes.get("poster", ""))
        if tag == "object":
            self._resource("object data", attributes.get("data", ""))
        if tag in {"body", "table", "tr", "td", "th"}:
            self._resource("HTML background", attributes.get("background", ""))
        if tag == "link":
            rels = set(attributes.get("rel", "").lower().split())
            if not rels or not rels.issubset(_PASSIVE_LINKS):
                self._resource("link href", attributes.get("href", ""))
        if tag in _SVG_HREF_TAGS:
            for name in ("href", "xlink:href"):
                self._resource("SVG resource href", attributes.get(name, ""))
        for name in ("srcset", "imagesrcset"):
            if tag in {"img", "source", "link"}:
                for url in _srcset_urls(attributes.get(name, "")):
                    self._resource("image srcset", url)
        for name, value in attributes.items():
            if name in _CSS_ATTRIBUTES:
                self._css(value)
            if name.startswith("on"):
                self._javascript(value)
            if name in ("href", "xlink:href") and value.lstrip().lower().startswith("javascript:"):
                self._javascript(value)
        if tag == "meta" and attributes.get("http-equiv", "").strip().lower() == "refresh":
            content = attributes.get("content", "")
            target = re.search(r"\burl\s*=\s*(.*)", content, re.I | re.S)
            url = target[1].strip(_SPACE + "\"'") if target else ""
            if not url or _external(url):
                self._report("meta refresh")
        if tag == "iframe" and attributes.get("srcdoc"):
            if self.depth < 4:
                _References(self.findings, self.depth + 1).inspect(attributes["srcdoc"])
            else:
                self._report("nested HTML review limit")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == self.raw_tag:
            self.raw_tag = ""

    def handle_comment(self, data):
        if data.startswith((">", "->")):
            self._report("malformed HTML comment")

    def handle_data(self, data):
        if self.raw_tag == "style":
            self._css(data)
        elif self.raw_tag == "script":
            self._javascript(data)

    def inspect(self, html: str) -> None:
        try:
            self.feed(html)
            self.close()
        except AssertionError:
            self._report("malformed HTML")
        # HTMLParser buffers an unterminated script/style until its end tag.
        if self.raw_tag and self.rawdata:
            self.handle_data(self.rawdata)


def external_references(html: str) -> list[str]:
    """Return bounded, deduplicated resource descriptions in encounter order.

    Data images/fonts and fragment references are local. Ordinary anchor hrefs
    are provenance/navigation, not automatic fetches. A nonempty result calls for
    review; an empty result is NOT proof of safe code or complete network denial.
    """
    findings: dict[str, None] = {}
    _References(findings).inspect(html)
    return list(findings)


class _PolicyPlacement(HTMLParser):
    """Locate source edits without serializing or escaping inline scripts/styles."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0] + [match.end() for match in re.finditer("\n", source)]
        self.prolog = True
        self.seen_html = False
        self.prefix_end = 0
        self.head_end: int | None = None
        self.own_policies: list[tuple[int, int]] = []
        self.head_tags: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        end = start + len(self.get_starttag_text())
        if tag == "meta" and self.source[start:end] == _CSP_META:
            self.own_policies.append((start, end))
        if tag == "head":
            self.head_tags.append((start, end))
        if self.prolog:
            if tag == "html" and not self.seen_html:
                self.seen_html = True
                self.prefix_end = end
            else:
                if tag == "head":
                    self.head_end = end
                self.prolog = False

    def handle_startendtag(self, tag, attrs):
        # The slash on a non-void HTML head element does not close it in browsers.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "head":
            start = self._offset()
            self.head_tags.append((start, self.source.index(">", start) + 1))
        self.prolog = False

    def handle_data(self, data):
        if self._offset() == 0:
            data = data.lstrip("\ufeff")
        if data.strip(_SPACE):
            self.prolog = False

    def handle_comment(self, data):
        if data.startswith((">", "->")):
            # HTML5 abruptly closes <!--> and <!--->; HTMLParser can absorb
            # following loadable markup into the comment instead.
            self.prolog = False

    def handle_decl(self, decl):
        if self.prolog and decl.lower().startswith("doctype"):
            self.prefix_end = self.source.index(">", self._offset()) + 1

    def unknown_decl(self, data):
        self.prolog = False

    def handle_pi(self, data):
        self.prolog = False


def secure_html(html: str, *, allow_scripts: bool = True) -> str:
    """Put the offline CSP first in head, creating an early head when necessary.

    Inline styles/scripts and data images/fonts remain usable; other resource
    loads, connections, frames, workers, objects, forms and base URLs are denied
    by policy. Existing CSP policies remain enforced alongside this policy, so
    stricter input restrictions are never weakened. Only identical copies of
    our own policy are replaced for idempotency. Inline code and other markup
    are preserved verbatim; this function does not sanitize or fetch anything.

    Malformed late head tags are normalized behind a new leading policy. This
    stdlib parser is not an HTML5 browser parser. A meta CSP is not a sandbox,
    cannot enforce frame-ancestors, and cannot block all top-level navigation.
    Parent integration must still apply isolation and browser network checks.
    """
    placement = _PolicyPlacement(html)
    try:
        placement.feed(html)
        placement.close()
    except AssertionError:
        # HTMLParser rejects some malformed declarations. Install the policy
        # before that input anyway; any unparsed old CSP can only restrict it.
        pass

    policy = _CSP_META if allow_scripts else _CSP_META.replace("script-src 'unsafe-inline'", "script-src 'none'")
    removals = placement.own_policies
    if placement.head_end is not None:
        position = placement.head_end
        insertion = policy
    else:
        position = placement.prefix_end
        insertion = "<head>" + policy + "</head>"
        removals = removals + placement.head_tags

    edits = [(start, end, "") for start, end in removals]
    edits.append((position, position, insertion))
    pieces = []
    cursor = 0
    for start, end, replacement in sorted(edits):
        pieces.extend((html[cursor:start], replacement))
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces)
