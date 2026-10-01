"""Publication gate for the legacy public writers (engine plan Task E2, Step 3a).

Two retained paths write generated HTML to the public web bucket served by CloudFront:
`api/handlers/design.py` `store_run` (design-runs/*) and `studio/worker_handler.py` (studio drafts).
Before every put they call this module:

- `sanitize_static(html)` removes executable and generated-content vectors (scripts, event handlers,
  `javascript:` and unreviewed `data:` URLs, frames/objects, CSS `content:` and `@import`), so the visible
  text of the published page is fully determined by its markup.
  Every start tag is re-serialized from its parsed attributes and all text is re-escaped, so the published
  bytes parse the same way in a browser as in this parser (no mutation through raw-text elements, SVG/MathML
  foreign content or malformed attribute names). `<style>` text never contains `<`, because inside SVG or MathML
  foreign content a browser parses it as markup (PR #30 review 1, #1).
- `prepare_static_html(html, patterns)` sanitizes, prepends `CSP_META` (`script-src 'none'`) and then scans with
  the same `scan_string` views as the repository scan. It raises `PublicationBlocked` on any identifier hit,
  unreviewed media or incomplete scan.

This is not a second scanner. The core is `scripts/check_public_identifiers.py`: `deploy.sh` copies it into
`api-dist/common/public_scan_core.py` together with `public-assets.sha256`; in the repository the script is
loaded from its own path. The approved-media registry is always read explicitly and eagerly, so a missing
registry blocks publication instead of silently allowing media.

The deny-list lives only in the deployment's private SSM parameter named by `PUBLIC_DENYLIST_PARAM`.
User decision (PR #30 fix round 1): when NO deny-list is configured (parameter name unset, parameter unreadable or
empty) `load_patterns()` logs a warning and returns `[]`; the identifier scan is then skipped, but sanitization, the
CSP and the approved-media registry stay mandatory (unreviewed media in the published text still blocks). With a
configured deny-list every rule stays fail-closed: hits, incomplete scans and unreviewed media block.
Matched text is never logged or returned.
"""
from __future__ import annotations

import base64
import importlib.util
import logging
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path


def _load_core():
    try:
        from common import public_scan_core as core  # the packaged Lambda artifact (deploy.sh)
        return core
    except ImportError:
        pass
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_public_identifiers.py"
    spec = importlib.util.spec_from_file_location("public_scan_core", script)
    if spec is None or spec.loader is None:
        raise ImportError("public identifier scanner is not available")
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    return core


core = _load_core()
ScanIncomplete = core.ScanIncomplete
MAX_NESTING = core.MAX_NESTING
# Beside the core module in both layouts: common/public-assets.sha256 (artifact), scripts/public-assets.sha256 (repo).
REGISTRY_PATH = core.REGISTRY
PARAM_ENV = "PUBLIC_DENYLIST_PARAM"
_CACHE_TTL_S = 300
_cache: dict = {}
_log = logging.getLogger(__name__)


class PublicationBlocked(Exception):
    """Publication must not happen. The message never contains matched text."""


def registry() -> set:
    """The approved public-asset registry, read now. Missing or unreadable → PublicationBlocked (BU2)."""
    try:
        return core._registry(REGISTRY_PATH)
    except ScanIncomplete as error:
        raise PublicationBlocked("public-asset registry is unavailable") from error


def _fetch_parameter(name: str) -> str:
    import boto3
    return boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def _unconfigured(reason: str) -> list:
    _log.warning("public identifier deny-list %s; identifier scan skipped (static sanitization and the media "
                 "registry still apply)", reason)
    return []


def load_patterns() -> list:
    """Deny-list from the private SSM parameter. Not configured, unreadable or empty -> `[]` with a warning
    (user decision, PR #30 fix round 1); the caller then publishes only sanitized, media-checked HTML."""
    name = os.environ.get(PARAM_ENV, "").strip()
    if not name:
        return _unconfigured("is not configured")
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < _CACHE_TTL_S:
        return list(hit[1])
    try:
        raw = _fetch_parameter(name)
    except Exception:  # noqa: BLE001 - an unreadable deny-list counts as not configured (warned, never logged)
        return _unconfigured("is not configured (unreadable)")
    patterns = [line.strip() for line in str(raw or "").splitlines() if line.strip() and not line.startswith("#")]
    if not patterns:
        return _unconfigured("is not configured (empty)")
    _cache[name] = (time.time(), patterns)
    return list(patterns)


def check_publishable(text: str, patterns: list, *, language: str = "html", media: set | None = None) -> None:
    """Scan `text` exactly as it will be published. Any hit, unreviewed media (index 0) or incomplete scan
    blocks. Only pattern indexes are reported. With no deny-list (`[]`) only the media registry is enforced
    (an incomplete scan still blocks, because media could not be verified)."""
    reviewed = registry() if media is None else media
    try:
        hits = core.scan_string(text, list(patterns or []), language=language, registry=reviewed)
    except ScanIncomplete as error:
        raise PublicationBlocked("public identifier scan incomplete") from error
    if hits:
        raise PublicationBlocked("public identifier scan hit: " + ",".join(f"#{i}" for i in hits))


def prepare_static_html(html: str, patterns: list) -> str:
    """Sanitize, then scan, and return the HTML to publish (the sanitized text, never the original).

    The ORIGINAL is scanned too (fail-closed reading of the plan): an identifier that is statically visible
    in any view of the original, or an original that cannot be scanned completely (over-bound, undecodable or
    over-depth embedded resources), blocks publication even though the sanitizer would remove the vector.
    Unreviewed media in the original is not a block by itself: the sanitizer removes it, and the sanitized
    output is then held to the registry like any other published byte."""
    reviewed = registry()                  # mandatory with or without a deny-list
    if not patterns:                       # no deny-list configured: warned by load_patterns; no identifier scan
        clean = _with_csp(sanitize_static(html, media=reviewed))
        check_publishable(clean, [], language="html", media=reviewed)
        return clean
    try:
        original_hits = [i for i in core.scan_string(html, patterns, language="html", registry=reviewed) if i]
    except ScanIncomplete as error:
        raise PublicationBlocked("public identifier scan incomplete") from error
    if original_hits:
        raise PublicationBlocked("public identifier scan hit: " + ",".join(f"#{i}" for i in original_hits))
    clean = _with_csp(sanitize_static(html, media=reviewed))
    check_publishable(clean, patterns, language="html", media=reviewed)
    return clean


# Defence in depth for the static-only rule: even markup the sanitizer missed cannot run a script.
CSP_META = ('<meta http-equiv="Content-Security-Policy" '
            'content="script-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'">')
_LEADING_DOCTYPE = re.compile(r"\A\s*<!doctype[^>]*>", re.I)


def _with_csp(html: str) -> str:
    """Insert CSP_META first in the document (after a leading doctype). A meta before `<html>`/`<head>` is
    placed in the implied head by the HTML parser, where a CSP meta takes effect."""
    m = _LEADING_DOCTYPE.match(html)
    at = m.end() if m else 0
    return html[:at] + CSP_META + html[at:]


# ---------------------------------------------------------------- sanitizer

_DROP_WITH_CONTENT = {"script", "noscript", "template", "iframe", "object", "frame", "frameset", "applet",
                      "animate", "animatemotion", "animatetransform", "set", "handler", "listener", "foreignobject",
                      # raw-text elements whose content a browser never parses as markup (parser differentials)
                      "xmp", "noembed", "noframes", "plaintext"}
_DROP_VOID = {"embed", "base", "param"}
_RCDATA = {"textarea", "title"}          # text only: nested markup is dropped, never re-emitted
_VOID = {"area", "br", "col", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_ATTR_NAME = re.compile(r"\A[a-z_:][-a-z0-9_:.]*\Z")
_TAG_NAME = re.compile(r"\A[a-z][-a-z0-9_:.]*\Z")
_URL_ATTRS = core._URL_ATTRS | {"to", "from", "values", "by", "formaction", "imagesrcset", "srcdoc"}
_SRCSET = {"srcset", "imagesrcset"}
_UNSAFE_SCHEMES = ("javascript:", "vbscript:", "livescript:")
_EMPTY_CONTENT = {'""', "''", "none", "normal", ""}


def _clean_url(value: str, media: set, depth: int):
    """The URL to keep, or None to drop the attribute/token. Same normalization and data-URL rules as the
    scanner (`_normalize_url`, `_data_url`), so both see what a browser resolves."""
    v = core._normalize_url(value)
    lower = v.lower()
    if lower.startswith(_UNSAFE_SCHEMES):
        return None
    if not lower.startswith("data:"):
        return value
    try:
        body = core._data_url(v)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(body, core._Media):
        return value if body.sha256 in media else None       # pixels cannot be scanned: registry only
    if isinstance(body, str) and not isinstance(body, core._DataText):   # SVG markup: sanitize recursively
        if depth + 1 > MAX_NESTING:
            return None                                      # over-depth resources are removed (BV1)
        inner = sanitize_static(body, media=media, _depth=depth + 1)
        return "data:image/svg+xml;base64," + base64.b64encode(inner.encode("utf-8")).decode("ascii")
    return None                                              # other data: bodies (text, fonts, binaries)


_CSS_URL = re.compile(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)\s]*))\s*\)", re.I)
_CSS_STRING = re.compile(r"\"([^\"]*)\"|'([^']*)'")
_CSS_CONTENT = re.compile(r"(^|[;{\s])content\s*:\s*([^;}]*)", re.I)


def _clean_css(css: str, media: set, depth: int) -> str:
    """Conservative CSS cleaning. CSS with escapes, `expression(` or bindings is dropped whole, because an
    escape can spell `content`, `url(` or a scheme (BS1). Comments and `@import` are removed; a non-empty
    `content:` declaration is removed; every url()/data-URL string goes through `_clean_url`."""
    lower = css.lower()
    if "\\" in css or "expression(" in lower or "binding" in lower or "behavior" in lower:
        return ""
    css = re.sub(r"/\*.*?(\*/|$)", " ", css, flags=re.S)
    css = re.sub(r"@import[^;]*;?", "", css, flags=re.I)

    def content(m):
        return m.group(0) if m.group(2).strip().lower() in _EMPTY_CONTENT else m.group(1)
    css = _CSS_CONTENT.sub(content, css)

    def url(m):
        raw = next(g for g in m.groups() if g is not None)
        kept = _clean_url(raw, media, depth)
        return "none" if kept is None else 'url("' + kept.replace('"', "%22") + '")'
    css = _CSS_URL.sub(url, css)

    def string(m):
        raw = next(g for g in m.groups() if g is not None)
        if core._normalize_url(raw).lower().startswith(("data:", *_UNSAFE_SCHEMES)):
            kept = _clean_url(raw, media, depth)
            return '""' if kept is None else '"' + kept.replace('"', "%22") + '"'
        return m.group(0)
    return _CSS_STRING.sub(string, css)


def _attr(value: str) -> str:
    """Double-quoted attribute text: `&`, `"`, `<` and `>` are escaped; nothing else changes."""
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


class _Sanitizer(HTMLParser):
    def __init__(self, media: set, depth: int):
        super().__init__(convert_charrefs=False)
        self.media, self.depth, self.out, self.skip, self.in_style = media, depth, [], 0, False
        self.rcdata = None

    def _attrs(self, tag, attrs):
        kept, changed, seen = [], False, set()
        for name, value in attrs:
            if not _ATTR_NAME.match(name) or name in seen:   # malformed names never reach the output
                changed = True
                continue
            seen.add(name)
            if name.startswith("on"):          # event handlers
                changed = True
                continue
            if value is not None and name in _SRCSET and "data:" in value.lower():
                changed = True      # candidate lists carrying data URLs are unsupported (BP1)
                continue
            if value is not None and name in _URL_ATTRS:
                new = _clean_url(value, self.media, self.depth)
                if new is None:
                    changed = True
                    continue
                changed |= new != value
                value = new
            elif value is not None and name == "style":
                new = _clean_css(value, self.media, self.depth)
                changed |= new != value
                value = new
            elif value is not None and core._normalize_url(value).lower().startswith(_UNSAFE_SCHEMES):
                changed = True
                continue
            kept.append((name, value))
        if tag == "meta" and any(n == "http-equiv" and (v or "").strip().lower() == "refresh" for n, v in kept):
            return None, True
        return kept, changed

    def _emit_tag(self, tag, attrs, close):
        """Always re-serialized (never the source text), so attribute values cannot smuggle markup that a
        browser would read differently inside a raw-text or foreign-content context."""
        kept, _changed = self._attrs(tag, attrs)
        if kept is None:
            return False
        parts = "".join(f" {n}" if v is None else f' {n}="{_attr(v)}"' for n, v in kept)
        if close and tag in _VOID:
            self.out.append(f"<{tag}{parts} />")
        elif close:                  # a browser ignores "/>" on HTML elements: close explicitly
            self.out.append(f"<{tag}{parts}></{tag}>")
        else:
            self.out.append(f"<{tag}{parts}>")
        return True

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in _DROP_WITH_CONTENT:
            if tag in _DROP_WITH_CONTENT:
                self.skip += 1
            return
        if tag in _DROP_VOID or not _TAG_NAME.match(tag):
            return
        if self.rcdata:              # markup inside textarea/title is text to a browser: never re-emit it
            return
        if self._emit_tag(tag, attrs, close=False):
            if tag == "style":
                self.in_style = True
            elif tag in _RCDATA:
                self.rcdata = tag

    def handle_startendtag(self, tag, attrs):
        if self.skip or self.rcdata or tag in _DROP_WITH_CONTENT or tag in _DROP_VOID or not _TAG_NAME.match(tag):
            return
        self._emit_tag(tag, attrs, close=True)

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            if self.skip:
                self.skip -= 1
            return
        if self.skip or tag in _DROP_VOID or not _TAG_NAME.match(tag):
            return
        if self.rcdata and tag != self.rcdata:
            return
        self.rcdata = None
        if tag == "style":
            self.in_style = False
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_style:
            # In SVG/MathML foreign content a browser tokenizes <style> text as markup; without "<" it is text.
            self.out.append(_clean_css(data, self.media, self.depth).replace("<", ""))
        else:
            self.out.append(data.replace("<", "&lt;").replace(">", "&gt;"))

    def handle_entityref(self, name):
        if not self.skip and not self.in_style and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip and not self.in_style and re.fullmatch(r"[0-9]+|[xX][0-9A-Fa-f]+", name):
            self.out.append(f"&#{name};")

    def handle_decl(self, decl):
        if not self.skip and self.depth == 0 and decl.strip().lower().startswith("doctype"):
            self.out.append("<!doctype html>" if decl.strip().lower() == "doctype html" else "<!DOCTYPE html>")

    def handle_comment(self, data):
        return        # comments are dropped: conditional comments and hidden text never publish

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return        # CDATA and other declarations are dropped


def sanitize_static(html: str, *, media: set | None = None, _depth: int = 0) -> str:
    """Inert static HTML whose visible text is fully determined by its markup (review round 43, BE3/BK1/BK2).
    Embedded SVG data URLs are sanitized recursively to MAX_NESTING; deeper ones are removed."""
    reviewed = registry() if media is None else media
    parser = _Sanitizer(reviewed, _depth)
    parser.feed(html)
    parser.close()
    return "".join(parser.out)
