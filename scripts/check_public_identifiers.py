#!/usr/bin/env python3
"""Fail-closed public identifier scan (REQUIREMENTS §7.3). Patterns live outside Git.
Output names the file, line and pattern index only; matched text is never printed."""
from __future__ import annotations
import argparse, os, pathlib, re, subprocess, sys

_LANGUAGE = {".js": "js", ".mjs": "js", ".cjs": "js", ".css": "css", ".html": "html", ".htm": "html",
             ".xhtml": "html", ".svg": "html", ".md": "markdown", ".txt": "markdown"}
TEXT = (".py", ".ts", ".tsx", ".json", ".yml", ".yaml", ".map", ".xml", ".webmanifest", ".vtt", *_LANGUAGE)
# Media whose pixels or frames cannot be scanned: published only when byte-identical to a reviewed entry in
# `scripts/public-assets.sha256` (the approved public-asset registry of BL1). Fonts carry glyphs, not page text.
MEDIA = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico", ".mp4", ".webm", ".mov", ".pdf",
         ".mp3", ".wav", ".m4a", ".ogg")
FONT = (".woff", ".woff2", ".ttf", ".otf", ".eot")
ARCHIVE = (".zip",)
# The registry is a resource beside the scanner module: `scripts/public-assets.sha256` in the repository, and
# `common/public-assets.sha256` beside `common/public_scan.py` in the assembled Lambda artifact (round 53, BU2).
REGISTRY = pathlib.Path(__file__).resolve().parent / "public-assets.sha256"
MAX_ARCHIVE_MEMBERS, MAX_ARCHIVE_BYTES = 2_000, 64_000_000


def classify(path):
    """The only extension classifier (review round 47, BO1): case-insensitive, used by both CLI modes.
    Returns the scan language for text files, "media", "font" or "archive", or None for an unknown type."""
    suffix = pathlib.Path(path).suffix.lower()
    if suffix in _LANGUAGE:
        return _LANGUAGE[suffix]
    for kind, suffixes in (("code", TEXT), ("media", MEDIA), ("font", FONT), ("archive", ARCHIVE)):
        if suffix in suffixes:
            return kind
    return None


def _registry(path=None):
    try:
        lines = pathlib.Path(path or REGISTRY).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ScanIncomplete("public-asset registry is missing") from error
    return {l.split()[0].lower() for l in lines if l.strip() and not l.startswith("#")}


def _media_hits(name, data, registry):
    """An unreviewed media file is a hit with pattern index 0 (review round 47, BO1)."""
    import hashlib
    return [] if hashlib.sha256(data).hexdigest() in registry else [(name, 0, 0)]


def scan_archive(path, patterns, registry):
    """Scan a published ZIP completely (review round 47, BO1): member names, text members through the same views
    and classifier, media members against the registry. Nested archives, unknown undecodable members, a corrupt
    archive or one past the member/size bounds make the scan incomplete."""
    import zipfile
    hits = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > MAX_ARCHIVE_MEMBERS or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                raise ScanIncomplete(f"archive exceeds the scan bound: {display(path, patterns)}")
            for m in members:
                name = f"{path}!/{m.filename}"
                hits += [(name, 0, i) for _, i in scan_names([m.filename], patterns)]
                kind, data = classify(m.filename), archive.read(m)
                if kind == "archive":
                    raise ScanIncomplete(f"nested archive: {display(name, patterns)}")
                if kind == "media":
                    hits += _media_hits(name, data, registry)
                elif kind != "font":
                    try:
                        content = data.decode("utf-8")
                    except UnicodeDecodeError as error:
                        raise ScanIncomplete(f"undecodable archive member: {display(name, patterns)}") from error
                    hits += [(name, 0, i) for i in scan_string(content, patterns, language=kind or "code",
                                                                registry=registry)]
    except (zipfile.BadZipFile, OSError) as error:
        raise ScanIncomplete(f"unreadable archive: {display(path, patterns)}") from error
    return hits


def _folded(patterns):
    return [(i + 1, p.casefold()) for i, p in enumerate(patterns) if p.strip()]


def _decoded_views(text, language="html", depth=0):
    """Raw text plus entity-decoded visible text and attribute values (review round 32, AZ2)."""
    import html as _html
    from html.parser import HTMLParser

    class _Collect(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts, self.attrs, self.scripts, self.styles, self.urls, self.srcsets = [], [], [], [], [], []
            self._in = None

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self._in = None

        def handle_data(self, data):
            {"script": self.scripts, "style": self.styles}.get(self._in, self.parts).append(data)

        def handle_starttag(self, tag, attrs):
            self.attrs.extend(v for _, v in attrs if v)
            self.styles.extend(v for k, v in attrs if k == "style" and v)
            # URL-bearing attributes are the only HTML resources (round 47, BN1); `title`, `alt`, `aria-*` are text.
            self.urls.extend(v for k, v in attrs if k in _URL_ATTRS - {"srcset", "imagesrcset"} and v)
            self.srcsets.extend(v for k, v in attrs if k in ("srcset", "imagesrcset") and v)
            if tag in ("script", "style"):
                self._in = tag

    collector = _Collect()
    try:
        collector.feed(text)
        collector.close()
    except Exception:  # noqa: BLE001 — malformed markup still gets the unescape view below
        pass
    parts = collector.parts
    # Text reconstructed across inline boundaries (<span>A</span><span class="x">CME</span>, A<b>CM</b>E) plus
    # the space-joined view; attribute values form their own views (review rounds 35-36, BC2).
    # Fail-closed script views (review round 40, BE3). Quote tokenization alone is defeated by prose apostrophes
    # and comments, so escapes are decoded over the WHOLE text and every script/JS context also gets a view with
    # comments removed and string concatenation collapsed ("A" + 'CME' -> ACME). Extra hits are acceptable;
    # misses are not.
    scripts = collector.scripts + ([text] if language == "js" else [])   # explicit contexts only (round 42, BJ1)
    views = [text, _html.unescape(text), " ".join(parts), "".join(parts), *collector.attrs, *_js_strings(text),
             _decode_escapes(text)]
    for script in scripts:
        # Views only ever ADD hits, so each transformation is offered alongside the untransformed form
        # (review round 41, BE3): comment stripping can no longer hide content such as "https://…" + 'A' + "CME".
        views += [_collapsed_script(script, strip_comments=True), _collapsed_script(script, strip_comments=False),
                  _letters_only(_decode_escapes(script))]
    for css in collector.styles + ([text] if language == "css" else []):   # standalone stylesheets too (BL2)
        decoded = _css_unescape(css)
        tokens = _css_tokens(css)
        # `content: "\41" "CME"`, `"Acme " "Bank"` or `"Acme " /* "x" */ "Bank"` renders as one string. The joined
        # view concatenates the contents of actual string tokens only (comments are tokenized as comments), so
        # adjacent strings are reconstructed exactly with spaces, "@" and "/" (rounds 46-47, BM1).
        views += [decoded, "".join(tokens["strings"]), _letters_only(_css_unescape(tokens["code"]))]
    if True:                                        # embedded SVG images (BL1); depth bound enforced below (BV1)
        # Strict only where a data URL is an actual resource of publishable markup or a stylesheet: attribute
        # values, style blocks and .css files of the "html"/"css" languages. Source code, Markdown prose, the
        # scanner's own regex and these plans mention `data:image/svg` as text; there parseable URLs are still
        # decoded and scanned, but an unparsed mention is not a resource and does not block (round 46, BN1).
        # Built sites are scanned as "html"/"css", so every rendered resource meets the strict path.
        # Resources are parsed URL values only (round 47, BN1): URL-bearing attributes, CSS url() tokens and
        # CSS strings that are themselves data URLs (image-set, @import). Descriptive attributes, comments and
        # prose that mention `data:image/svg` get the lenient decode below and ordinary identifier scanning.
        strict = language in ("html", "css")
        sheets = collector.styles + ([text] if language == "css" else [])
        resources = collector.urls + [u for css in sheets for u in _css_tokens(css)["urls"]]
        # Backstop for any CSS spelling the tokenizer misreads: SVG mentions in each CSS-unescaped sheet (BS1).
        mentions = [svg for css in sheets for svg in _svg_mentions(_css_unescape(css))]
        svgs = [svg for r in resources for svg in _svg_resource(r, strict=strict)]
        svgs += [svg for r in collector.srcsets for svg in _svg_resource(r, strict=strict, srcset=True)]
        # Complete JS string literals are URL candidates too (round 49, BP1): a bundle's
        # "data:image/svg+xml,%3Csvg xmlns='…' viewBox='0 0 1 1'%3E…" is one literal whatever it contains.
        svgs += [svg for lit in _js_strings(text) for svg in _svg_resource(lit, strict=language in ("js", "css"))]
        svgs += _svg_mentions(text) + _svg_mentions(_html.unescape(text))
        # Tokenization can be misled by quotes in comments or regex literals (`/* " */`, `/"/`), so SVG URLs are
        # ALSO extracted from the whole-text JS-unescaped view and from every script's unescaped text, where
        # `"\x64ata:image/svg+xml;base64,…"` has become a plain `data:` mention (review round 50, BR1).
        svgs += _svg_mentions(_decode_escapes(text)) + mentions
        svgs += [svg for script in scripts for svg in _svg_mentions(_decode_escapes(script))]
        nested = [item for item in dict.fromkeys(svgs) if not isinstance(item, _Media)]
        if nested and depth >= MAX_NESTING:
            # Another encoded document remains at the limit: it was not inspected, so the scan is incomplete and
            # publication is blocked; exhaustion never passes silently (review round 54, BV1).
            raise ScanIncomplete("embedded resources nest deeper than the scan limit")
        for item in dict.fromkeys(svgs):
            if isinstance(item, _Media):   # inlined raster/audio/video/PDF: registry-checked by hash (round 52, BT1)
                views.append(_MediaView(item.sha256))
            elif isinstance(item, _DataText):   # text bodies: every view, lenient resources (round 53, BU1)
                views += _decoded_views(str(item), "markdown", depth + 1)
            else:
                views += _decoded_views(item, "html", depth + 1)
    return views


_WS = " \t\n\f\r"
class _DataText(str):
    """A decoded non-SVG text body (`data:text/plain,…`, `data:,…`): scanned as lenient text (round 53, BU1)."""


class _Unsupported(bytes):
    """A binary body that is neither recognized media nor a font: blocks in strict contexts (round 53, BU1)."""


class _MediaView(str):
    """A view carrying an inlined media hash. It is a distinct type, never a text prefix, so no decoded text
    (including this scanner's own source) can impersonate it; scan_string checks it against the registry."""


class _Media(bytes):
    """The decoded body of an inlined media data URL (review round 52, BT1)."""
    _MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF", b"BM", b"\x00\x00\x01\x00",
              b"%PDF", b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"OggS", b"\x1aE\xdf\xa3", b"fLaC")

    def is_media_file(self):
        """A recognizable media signature. Resource positions (attributes, url(), complete literals) count every
        non-empty media body; free-text mentions, whose end is guessed, count only bodies that are real media, so
        `u.startsWith("data:image/")…` followed by other code on the line is not an image."""
        return self.startswith(self._MAGIC) or self[4:8] == b"ftyp"

    def is_font(self):
        return self.startswith((b"wOFF", b"wOF2", b"OTTO", b"\x00\x01\x00\x00", b"true", b"ttcf"))

    @property
    def sha256(self):
        import hashlib
        return hashlib.sha256(self).hexdigest()


def _normalize_url(value):
    """WHATWG URL parsing (review round 49, BQ1): leading and trailing C0 controls and spaces are stripped and
    every ASCII tab or newline is removed before the scheme is read, so `da&#9;ta:`, `da&#10;ta:` and a leading
    space still yield a `data:` URL, exactly as a browser resolves them."""
    return re.sub(r"[\t\n\r]", "", value).strip("".join(map(chr, range(0x21))))


def _data_url(url):
    """WHATWG `data:` URL processor over a normalized URL. Returns the decoded SVG text for an SVG body, a _Media
    for other image/audio/video/PDF bodies, and None for any other MIME type once its body has decoded. The MIME type is whitespace-trimmed and case-insensitive, parameters
    such as `;charset=utf-8` are allowed, `;base64` may carry surrounding whitespace, and the ENTIRE text after the
    first comma is the body, so quotes or spaces inside SVG markup cannot truncate it (BL1, BP1). Raises
    ValueError/UnicodeDecodeError when the URL has no body or the body does not decode."""
    import base64, urllib.parse
    if url[:5].lower() != "data:":
        raise ValueError("not a data URL")
    mime, sep, body = url[5:].partition(",")
    if not sep:
        raise ValueError("data URL without a body")
    mime = mime.strip(_WS)
    b64 = re.search(r";[ \t\n\f\r]*base64[ \t\n\f\r]*$", mime, flags=re.I)
    if b64:
        mime = mime[:b64.start()]
    essence = mime.split(";")[0].strip(_WS).lower() or "text/plain"
    raw = urllib.parse.unquote_to_bytes(body)
    if b64:   # forgiving-base64: ASCII whitespace removed, then strict decoding; every body must decode
        raw = base64.b64decode(re.sub(rb"[ \t\n\f\r]", b"", raw), validate=True)
    # Any MIME type naming SVG (including non-standard `image/svg`) is treated as SVG markup, fail-closed.
    if "svg" in essence:
        return raw.decode("utf-8")
    if not raw:
        return None   # an empty body (the prefix "data:image/png;base64," in code) renders nothing
    # Every other body is classified by its BYTES, never by its label (review round 53, BU1): browsers sniff
    # images, and Vite labels unknown extensions `application/octet-stream`. Recognized media (a media MIME type
    # or a media signature) must be byte-identical to a reviewed registry entry, like standalone media files,
    # because Vite inlines small imported images into JS and CSS (round 52, BT1). Fonts pass as glyph data.
    # UTF-8 text bodies (`text/plain`, `text/html`, JSON, an omitted MIME type) are scanned as text. Anything
    # else is an unsupported binary resource.
    media = _Media(raw)
    if media.is_media_file() or essence.split("/")[0] in ("image", "audio", "video") or essence == "application/pdf":
        return media
    if media.is_font() or essence.startswith(("font/", "application/font-", "application/x-font-")):
        return None
    try:
        return _DataText(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return _Unsupported(raw)


def _svg_resource(value, *, strict, srcset=False):
    """An actual resource URL (a URL-bearing attribute value, a CSS url()/data-URL string token, or a complete
    JS string literal) is normalized and parsed as a WHOLE (rounds 48-49, BP1/BQ1). strict (publishable markup,
    stylesheets and built bundles): a `data:` value that does not decode fails closed, and `srcset` candidate
    lists carrying a `data:` URL are unsupported. Not strict: the value falls back to the mention decoder."""
    v = _normalize_url(value)
    if srcset:
        if strict and "data:" in v.lower():
            raise ScanIncomplete("data URL in srcset is not supported")
        return _svg_mentions(v)
    if v[:5].lower() != "data:":
        return _svg_mentions(v) if "data:" in v.lower() else []
    if "," not in v:   # a fragment such as the literal "data:" in `url.startsWith("data:")`, not a URL
        return []
    try:
        svg = _data_url(v)
    except (ValueError, UnicodeDecodeError) as error:
        if strict:
            raise ScanIncomplete("undecodable data URL") from error
        return _svg_mentions(v)
    if isinstance(svg, _Media) and not strict and not svg.is_media_file():
        return []   # lenient contexts (source code, Markdown) count only real media bytes, as for mentions
    if isinstance(svg, _Unsupported):
        if strict:   # a resource body the scanner cannot inspect is never published (BU1)
            raise ScanIncomplete("unsupported binary data URL body")
        return []
    return [] if svg is None else [svg]


_MENTION_ENDS = (r"[^\"'<>()\s]*", r"[^\"<>()\s]*", r"[^\"<>\n]*", r"[^'<>\n]*", r"[^<>\n]*")


def _svg_mentions(text):
    """SVG data URLs inside prose, Markdown or source code, where the URL end is unknown. Every `data:` occurrence
    is tried with each plausible terminator (quote, whitespace, line end), on the text as given and with tabs and
    newlines removed (BQ1), and each candidate that `_data_url` decodes as SVG is scanned. Undecodable candidates
    are skipped here; resources and complete string literals are held to `_svg_resource`."""
    out = []
    for source in (text, re.sub(r"[\t\n\r]", "", text)):
        for m in re.finditer(r"data:", source, flags=re.I):
            media = []
            for end in _MENTION_ENDS:
                candidate = source[m.start():m.end()] + re.match(end, source[m.end():]).group(0)
                try:
                    item = _data_url(candidate)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(item, _Media):
                    if item.is_media_file():   # prose/code mentions count only as real media bytes
                        media.append(item)
                elif isinstance(item, str) and not isinstance(item, _DataText):   # SVG only: guessed-end text and binaries are skipped
                    out.append(item)
            if media:   # one media body per occurrence: the longest decoded candidate, never a truncated prefix
                out.append(max(media, key=len))
    return list(dict.fromkeys(out))


_URL_ATTRS = {"src", "href", "xlink:href", "srcset", "poster", "data", "action", "formaction", "background",
              "ping", "cite", "longdesc", "manifest", "icon"}


_CSS_IDENT = re.compile(r"(?:[A-Za-z0-9_-]|[^\x00-\x7f]|\\[0-9A-Fa-f]{1,6}[ \t\n\r\f]?|\\[^\n\r\f0-9A-Fa-f])+")


def _css_tokens(css):
    """Minimal CSS tokenizer (review round 47, BM1/BN1), following CSS Syntax §4 for the parts that matter:
    comments `/* */` (quotes inside are comment text), strings with backslash escapes, and `url(` with an
    unquoted or quoted value. Returns the unescaped string contents in order, the unescaped URL values (plus
    string tokens that are themselves data URLs), and the source with comments removed. An unterminated comment
    or string is a parse error that the browser recovers from by running to the end, and so do we."""
    strings, urls, code, i, n = [], [], [], 0, len(css)
    def read_string(j, quote):
        out = []
        while j < n and css[j] != quote:
            if css[j] == "\\" and j + 1 < n:
                out.append(css[j:j + 2]); j += 2
            else:
                out.append(css[j]); j += 1
        return _css_unescape("".join(out)), j + 1
    while i < n:
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
            code.append(" ")
        elif css[i] in "\"'":
            value, i = read_string(i + 1, css[i])
            strings.append(value)
            if value.lstrip().lower().startswith("data:"):
                urls.append(value.strip())
            code.append('"' + value + '"')
        elif (ident := _CSS_IDENT.match(css, i)) and ident.end() < n and css[ident.end()] == "(" \
                and _css_unescape(ident.group(0)).lower() in ("url", "src"):
            # Function names are identifiers, so escapes such as `\75rl(` or `u\72l(` spell `url(` (round 51, BS1).
            j = ident.end() + 1
            while j < n and css[j] in " \t\n\r\f":
                j += 1
            if j < n and css[j] in "\"'":
                value, j = read_string(j + 1, css[j])
            else:
                end = css.find(")", j)
                end = n if end < 0 else end
                value, j = _css_unescape(css[j:end]).strip(), end
            urls.append(value)
            end = css.find(")", j)
            i = n if end < 0 else end + 1
            code.append("url(" + value + ")")
        elif ident := _CSS_IDENT.match(css, i):   # other identifiers are copied whole, so no escape splits them
            code.append(ident.group(0)); i = ident.end()
        else:
            code.append(css[i]); i += 1
    return {"strings": strings, "urls": urls, "code": "".join(code)}


def _css_unescape(css):
    """CSS escapes: backslash + 1-6 hex digits (+ optional whitespace), or backslash + any char (round 43)."""
    import re
    def one(m):
        if m.group(1):
            code = int(m.group(1), 16)
            return chr(code) if 0 < code <= 0x10FFFF else "\ufffd"
        return m.group(2)
    return re.sub(r"\\(?:([0-9A-Fa-f]{1,6})\s?|(.))", one, css, flags=re.S)


def _letters_only(code):
    """Letters and digits of every script, in order; catches any concatenation, spacing or comment trick."""
    return "".join(ch for ch in code if ch.isalnum())


def _decode_escapes(text):
    try:
        return _js_unescape(text)
    except (ValueError, OverflowError) as error:   # e.g. "\\u{110000}": fail closed, never skip
        raise ScanIncomplete("undecodable escape sequence") from error


def _collapsed_script(script, *, strip_comments):
    import re
    code = _decode_escapes(script)
    if strip_comments:
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)              # block comments
        code = re.sub(r"(?m)//[^\n]*$", "", code)                       # line comments
    code = re.sub(r"[\"'`]\s*\+\s*[\"'`]", "", code)                 # "A" + "CME" -> "ACME"
    return re.sub(r"[\"'`]", "", code)


# An escape is a backslash plus CRLF or any single character, so "\<CRLF>" continuations stay inside the literal.
_JS_STRING = __import__("re").compile(r'"(?:[^"\\\r\n]|\\(?:\r\n|.))*"|\'(?:[^\'\\\r\n]|\\(?:\r\n|.))*\'|`(?:[^`\\]|\\(?:\r\n|.))*`', __import__("re").S)


def _js_unescape(body):
    """Decode JS/JSON string escapes without executing anything (review round 37, BE3)."""
    import re
    def one(m):
        esc = m.group(1)
        if esc.startswith("u{"):
            return chr(int(esc[2:-1], 16))
        if esc[0] in "ux" and len(esc) > 1:          # a lone "\\u" / "\\x" is not an escape; keep the letter
            return chr(int(esc[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}.get(esc, esc)
    # Line continuations (backslash + LF, CRLF, CR, U+2028 or U+2029) contribute nothing in JavaScript
    # (review round 39, BE3); remove them before decoding the other escapes.
    body = re.sub("\\\\(?:\r\n|[\n\r\u2028\u2029])", "", body)
    return re.sub(r"\\(u\{[0-9A-Fa-f]{1,6}\}|u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2}|.)", one, body, flags=re.S)


class ScanIncomplete(Exception):
    """The content could not be scanned completely; callers must block publication (review round 38, BF1)."""


MAX_SCAN_BYTES = 20_000_000
MAX_NESTING = 3   # embedded SVG/text data URLs decoded inside each other; one more level → ScanIncomplete (BV1)


def _js_strings(text):
    """Every quoted/template string literal in inline scripts, JSON or bundles, decoded. No literal-count cap:
    the regex is linear and the input is bounded by MAX_SCAN_BYTES, so nothing is silently skipped."""
    out = []
    for m in _JS_STRING.finditer(text):
        try:
            out.append(_js_unescape(m.group(0)[1:-1]))
        except (ValueError, OverflowError) as error:
            raise ScanIncomplete("undecodable string literal") from error
    return out


def scan_string(text, patterns, *, language="html", registry=None):
    """Static scan. `language` is "html" (default; publishable markup), "css", "markdown" (.md/.txt: the HTML
    views without strict resource checks), "js" for .js/.mjs/.cjs, or "code"
    for other source files (Python, TypeScript source, YAML, ...). Code gets the raw, entity and escape views and
    the HTML-parsed views, but its whole text is never treated as a script, so ordinary identifiers such as
    `evaluate` are not special (BJ1). Runtime-constructed text is covered by the rendered scan (BE3)."""
    if len(text.encode("utf-8")) > MAX_SCAN_BYTES:
        raise ScanIncomplete("content exceeds the scan bound")
    folded = _folded(patterns)
    views = _decoded_views(text, language)
    media = {str(v) for v in views if isinstance(v, _MediaView)}
    views = [v.casefold() for v in views if not isinstance(v, _MediaView)]
    hits = {i for i, needle in folded if any(needle in v for v in views)}
    # Inlined media must be reviewed: index 0 = "unreviewed media", as for standalone files (review round 52, BT1).
    # The registry is loaded only when media is present; a missing registry makes the scan incomplete.
    if media and not media <= (_registry() if registry is None else registry):
        hits.add(0)
    return sorted(hits)


def scan_text(paths, patterns, registry=None):
    """Scan each file's COMPLETE content (review round 37, BE2): multiline tags and comments keep their context.
    Diagnostics report the first raw line containing the needle when one exists, otherwise line 0."""
    hits, folded = [], _folded(patterns)
    for path in paths:
        try:
            content = pathlib.Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as error:   # an expected text file must scan completely (BJ2)
            raise ScanIncomplete(f"unreadable or undecodable text file: {display(path, patterns)}") from error
        language = classify(path) or "code"   # markdown: html views, lenient resources (BN1); BO1
        lines = content.casefold().splitlines()
        for i in scan_string(content, patterns, language=language, registry=registry):
            needle = dict(folded).get(i)
            line = next((n for n, l in enumerate(lines, 1) if needle in l), 0) if needle else 0
            hits.append((path, line, i))
    return hits


def scan_names(paths, patterns):
    folded = _folded(patterns)
    return [(p, i) for p in paths for i, needle in folded if needle in p.casefold()]


def _patterns(path):
    try:
        values = [l.strip() for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]
    except OSError:
        values = []
    return values


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--patterns-file", default=os.environ.get("PUBLIC_DENYLIST_FILE", ""))
    ap.add_argument("--site", default="")
    ap.add_argument("--no-tree", action="store_true")
    ap.add_argument("--asset-registry", default="")   # tests only; CI uses the tracked registry
    # Only when NO private deny-list is configured (CI: the PUBLIC_DENYLIST secret is absent or empty) the scan warns
    # and passes. A configured deny-list keeps every fail-closed rule (hits, incomplete scans, unreviewed media).
    ap.add_argument("--allow-missing-patterns", "--allow-missing-config", dest="allow_missing_patterns",
                    action="store_true")
    args = ap.parse_args(argv)
    patterns = _patterns(args.patterns_file) if args.patterns_file else []
    if not patterns:
        print("public identifier deny-list is not configured", file=sys.stderr)
        if args.allow_missing_patterns:
            print("::warning title=Public identifier scan skipped::No private deny-list is configured "
                  "(PUBLIC_DENYLIST is empty); identifiers, incomplete scans and unreviewed media were NOT checked.")
            print("WARNING: public identifier scan skipped: deny-list not configured (--allow-missing-patterns)",
                  file=sys.stderr)
            return 0
        return 2
    hits = []
    if not args.no_tree:
        files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True,
                               cwd=pathlib.Path(__file__).resolve().parents[1]).stdout.split("\n")
        files = [f for f in files if f]
        hits += [(p, 0, i) for p, i in scan_names(files, patterns)]
        root = pathlib.Path(__file__).resolve().parents[1]
        # Unknown tracked types (`.vue`, `.sh`, `.jsonl`, extensionless files, ...) are scanned as text like any
        # other; an undecodable one makes the scan incomplete (review round 48, BO1). Tracked media and fonts are
        # reviewed in their PR; what is published is held to the registry by the --site scan.
        links = [f for f in files if (root / f).is_symlink()]   # Git stores a symlink as its target text
        hits += [(f, 0, i) for f in links for i in scan_string(os.readlink(root / f), patterns, language="code")]
        hits += scan_text([str(root / f) for f in files
                           if f not in links and classify(f) not in ("media", "font", "archive")], patterns,
                          _registry(args.asset_registry))   # inlined media in tracked sources too (BT1)
        hits += [h for f in files if classify(f) == "archive" for h in scan_archive(str(root / f), patterns, _registry(args.asset_registry))]
    if args.site:
        site = [str(p) for p in pathlib.Path(args.site).rglob("*") if p.is_file()]
        hits += [(p, 0, i) for p, i in scan_names(site, patterns)]
        # Every built file is published, so none is skipped silently (BO1): text types and unknown types are
        # scanned as text (an undecodable unknown file makes the scan incomplete), archives are scanned member by
        # member, media must be registered, and only fonts pass without content inspection.
        registry = _registry(args.asset_registry)
        hits += scan_text([p for p in site if classify(p) not in ("media", "font", "archive")], patterns, registry)
        hits += [h for p in site if classify(p) == "archive" for h in scan_archive(p, patterns, registry)]
        hits += [h for p in site if classify(p) == "media" for h in _media_hits(p, pathlib.Path(p).read_bytes(), registry)]
    for path, line, index in hits:
        reason = "unreviewed media (sha256 not in scripts/public-assets.sha256)" if index == 0 else f"deny-list pattern #{index}"
        print(f"{display(path, patterns)}:{line}: {reason}")
    return 1 if hits else 0


def run(argv=None):
    try:
        return main(argv)
    except ScanIncomplete as error:          # incomplete scanning is a failure, never success (BF1)
        print(f"public identifier scan incomplete: {error}", file=sys.stderr)
        return 2


def display(path, patterns):
    """Public CI logs must not echo an identifier through a file name (review round 2, #3)."""
    import hashlib
    if scan_names([path], patterns):
        return "path-sha256:" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return path


if __name__ == "__main__":
    sys.exit(run())
