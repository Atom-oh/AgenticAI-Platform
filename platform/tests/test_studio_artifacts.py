"""Offline artifact helpers: no browser, network, or application services required."""
from __future__ import annotations

import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.artifacts import external_references, secure_html  # noqa: E402


class ExternalReferencesTests(unittest.TestCase):
    def test_remote_image_and_css_import_are_rejected(self):
        for html in (
            '<img src="https://assets.example/private/logo.png?token=secret">',
            '<style>@import "https://assets.example/theme.css";</style>',
            '<style>@import url(//assets.example/theme.css);</style>',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_automatic_resource_attributes(self):
        for html in (
            '<script src="//assets.example/app.js"></script>',
            '<link rel="stylesheet" href="/theme.css">',
            '<link rel="preconnect" href="https://assets.example">',
            '<link rel="dns-prefetch" href="//assets.example">',
            '<link rel="modulepreload" href="./module.js">',
            '<iframe src="https://assets.example/frame"></iframe>',
            '<audio src="sound.mp3"></audio>',
            '<video src="/movie.mp4"></video>',
            '<video poster="//assets.example/poster.png"></video>',
            '<source src="https://assets.example/movie.mp4">',
            '<track src="captions.vtt">',
            '<input type="image" src="/submit.png">',
            '<object data="https://assets.example/object"></object>',
            '<embed src="https://assets.example/plugin">',
            '<body background="https://assets.example/background.png">',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_srcset_finds_remote_candidates_among_inline_data(self):
        for html in (
            '<img srcset="data:image/png;base64,AAAA 1x, //assets.example/2.png 2x">',
            '<source srcset="data:image/png;base64,AAAA, /large.png 2x">',
            '<link rel="preload" as="image" imagesrcset="/small.png 1x, /big.png 2x">',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_svg_resource_hrefs(self):
        for html in (
            '<svg><use href="//assets.example/sprite.svg#logo"/></svg>',
            '<svg><image xlink:href="https://assets.example/logo.svg"/></svg>',
            '<svg><feImage href="/filter.svg"/></svg>',
            '<svg><script href="/app.js"/></svg>',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_css_urls_in_styles_attributes_and_svg(self):
        for html in (
            '<style>body { background: url(https://assets.example/bg.png) }</style>',
            '<div style="background: URL(\'//assets.example/bg.png\')"></div>',
            '<style>@font-face { src: url("/font.woff2") }</style>',
            '<svg><path fill="url(//assets.example/filter.svg#x)"/></svg>',
            r'<style>@im\70 ort/**/"https://assets.example/theme.css";</style>',
            r'<div style="background: u\72l(https://assets.example/bg.png)"></div>',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_refresh_includes_implicit_reload(self):
        for content in ("0; url=https://assets.example/next", "0;URL=/next", "10", "0;url="):
            with self.subTest(content=content):
                self.assertTrue(external_references(
                    f'<META HTTP-EQUIV=" Refresh " CONTENT="{content}">'
                ))

    def test_obvious_javascript_network_apis(self):
        for code in (
            'fetch(endpoint)',
            'window.fetch("/api")',
            'new XMLHttpRequest()',
            'navigator . sendBeacon("/log", "event")',
            'new WebSocket("wss://assets.example/socket")',
            'new EventSource("/events")',
            'new Worker("/worker.js")',
            'new SharedWorker("/worker.js")',
            'navigator.serviceWorker.register("/sw.js")',
            'import("/module.js")',
            'import value from "/module.js";',
            'import "/side-effect.js";',
            'export { value } from "/module.js";',
            'new Image().src = "https://assets.example/pixel";',
        ):
            with self.subTest(code=code):
                self.assertTrue(external_references(f"<script>{code}</script>"))

    def test_inline_handlers_and_javascript_links_are_checked(self):
        for html in (
            '<img onerror="fetch(\'/api\')">',
            '<body onload="navigator.sendBeacon(\'/log\')">',
            '<a href="javascript:fetch(\'/api\')">run</a>',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_nested_srcdoc_is_checked(self):
        self.assertTrue(external_references(
            '<iframe srcdoc="&lt;img src=&quot;//assets.example/a.png&quot;&gt;"></iframe>'
        ))

    def test_inline_content_fragments_and_provenance_links_are_allowed(self):
        html = """
        <!doctype html><html><head><style>
          @font-face { src: url(data:font/woff2;base64,AAAA) }
          @font-face { src: url("data:application/font-woff;base64,AAAA") }
          body { background: url('data:image/svg+xml,%3Csvg%3E%3C/svg%3E') }
          .icon { filter: url(#shadow) }
        </style></head><body>
          <img src="data:image/png;base64,AAAA">
          <img srcset="data:image/png;base64,AAAA 1x, data:image/png;base64,BBBB 2x">
          <svg><use href="#logo"/><image href="data:image/png;base64,AAAA"/></svg>
          <a href="https://figma.example/provenance">Design source</a>
          <a href="#details">Details</a>
          <link rel="canonical" href="https://assets.example/source">
          <script>document.body.classList.add("ready")</script>
          <!-- <img src="https://assets.example/comment.png"> -->
          <p>fetch('/this-is-prose')</p>
        </body></html>
        """
        self.assertEqual(external_references(html), [])

    def test_non_inline_sources_are_flagged_even_without_remote_scheme(self):
        for url in ("/asset.png", "asset.png", "../asset.png", "?asset=1",
                    "blob:opaque", "file:///asset.png", "data:text/html,payload"):
            with self.subTest(url=url):
                self.assertTrue(external_references(f'<img src="{url}">'))

    def test_uppercase_entities_and_unclosed_style(self):
        for html in (
            '<IMG SRC="&#104;ttps://assets.example/pixel">',
            '<STYLE>@IMPORT "//assets.example/theme.css";',
            '<script>fetch("/api")',
            '<img src=//assets.example/pixel>',
        ):
            with self.subTest(html=html):
                self.assertTrue(external_references(html))

    def test_diagnostics_are_short_safe_deduplicated_and_deterministic(self):
        html = '<img src="https://secret.example/private?token=&lt;secret&gt;">' * 1000
        descriptions = external_references(html)
        self.assertEqual(len(descriptions), 1)
        self.assertEqual(descriptions, external_references(html))
        for description in descriptions:
            self.assertLessEqual(len(description), 80)
            for sensitive in ("secret", "https:", "<", ">", "private", "\n"):
                self.assertNotIn(sensitive, description)

    def test_empty_document(self):
        self.assertEqual(external_references(""), [])

    def test_malformed_declaration_requires_review_instead_of_crashing(self):
        self.assertTrue(external_references('<![bogus]><img src="/hidden.png">'))

    def test_abruptly_closed_comments_do_not_hide_resources_from_review(self):
        for prefix in ("<!-->", "<!--->"):
            with self.subTest(prefix=prefix):
                self.assertTrue(external_references(
                    prefix + '<img src="/hidden.png">-->'
                ))


class _Markup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        try:
            self.feed(html)
            self.close()
        except AssertionError:
            # The prefix is inspectable even for an invalid later declaration.
            pass

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class SecureHtmlTests(unittest.TestCase):
    def assert_policy_first(self, html):
        tags = _Markup(html).tags
        head_index = next(i for i, (tag, _) in enumerate(tags) if tag == "head")
        self.assertTrue(all(tag == "html" for tag, _ in tags[:head_index]))
        tag, attributes = tags[head_index + 1]
        self.assertEqual(tag, "meta")
        self.assertEqual(attributes.get("http-equiv", "").lower(), "content-security-policy")
        return dict(
            (parts[0], parts[1:])
            for directive in attributes["content"].split(";")
            if (parts := directive.split())
        )

    def test_policy_denies_network_and_permits_only_inline_code_and_data_assets(self):
        directives = self.assert_policy_first(secure_html("<html><head></head></html>"))
        for name in ("default-src", "connect-src", "frame-src", "child-src", "object-src",
                     "worker-src", "media-src", "manifest-src", "form-action", "base-uri"):
            with self.subTest(directive=name):
                self.assertEqual(directives[name], ["'none'"])
        self.assertEqual(directives["script-src"], ["'unsafe-inline'"])
        self.assertEqual(directives["style-src"], ["'unsafe-inline'"])
        self.assertEqual(directives["img-src"], ["data:"])
        self.assertEqual(directives["font-src"], ["data:"])
        # These directives do not work in meta CSP and would imply false safety.
        for name in ("sandbox", "frame-ancestors", "report-uri", "report-to"):
            self.assertNotIn(name, directives)

    def test_policy_precedes_all_existing_head_content(self):
        html = """<!doctype html><html lang="ko"><head>
        <meta charset="utf-8"><link rel="stylesheet" href="/theme.css">
        <script src="/app.js"></script></head><body>화면</body></html>"""
        secured = secure_html(html)
        self.assert_policy_first(secured)
        self.assertTrue(secured.startswith('<!doctype html><html lang="ko"><head><meta '))
        self.assertIn('<meta charset="utf-8">', secured)
        self.assertIn("<body>화면</body>", secured)

    def test_old_policies_are_removed_including_duplicates_and_report_only(self):
        html = """<HTML><HEAD>
        <META HTTP-EQUIV="Content-Security-Policy" CONTENT="default-src *">
        <meta content="script-src 'none'" http-equiv="content-security-policy" />
        <meta http-equiv="Content-Security-Policy-Report-Only" content="report-uri /log">
        </HEAD><body><meta http-equiv="content-security-policy" content="img-src *"></body>
        </HTML>"""
        secured = secure_html(html)
        self.assert_policy_first(secured)
        policies = [
            attrs for tag, attrs in _Markup(secured).tags
            if tag == "meta" and attrs.get("http-equiv", "").lower().startswith("content-security-policy")
        ]
        self.assertEqual(len(policies), 1)
        self.assertNotIn("default-src *", secured)
        self.assertNotIn("report-uri /log", secured)

    def test_creates_head_for_fragments_and_documents_without_head(self):
        for html in (
            "", "<main>hello</main>", "<body>hello</body>",
            '<html lang="ko"><body>hello</body></html>',
            "<!DOCTYPE html>\n<html><body>hello</body></html>",
            '\ufeff<!DOCTYPE html>\n<HTML lang="ko"><BODY>hello</BODY></HTML>',
            "<!-- heading --><html><body>hello</body></html>",
        ):
            with self.subTest(html=html):
                secured = secure_html(html)
                self.assert_policy_first(secured)
                if "hello" in html:
                    self.assertIn("hello", secured)
                if "<!DOCTYPE html>" in html:
                    self.assertLess(secured.index("<!DOCTYPE html>"), secured.index("<head>"))

    def test_malformed_or_late_heads_cannot_put_resources_before_policy(self):
        for html in (
            '<img src="/before.png"><head><title>late</title></head>',
            '<html><body><head><script src="/before.js"></script></head>',
            '<html><head><title>unclosed',
            '<html><head data-note="unterminated',
            '<!-- unclosed comment',
            '<![bogus]><img src="/before.png">',
            '<template><head><img src="/before.png"></head></template>',
            '<script>const text = "<head>";</script><head></head>',
            '<title>fake <head></title><img src="/before.png">',
            '<html><head/><script src="/app.js"></script>',
        ):
            with self.subTest(html=html):
                self.assert_policy_first(secure_html(html))

    def test_preserves_inline_code_and_policy_lookalikes_verbatim(self):
        script = """<script>
        const example = '<meta http-equiv="Content-Security-Policy" content="example">';
        const heading = "<head>example</head>";
        document.body.dataset.ready = "yes";
        </script>"""
        style = '<style>.icon{background:url("data:image/png;base64,AAAA")}</style>'
        comment = '<!-- <head><meta http-equiv="Content-Security-Policy" content="comment"> -->'
        html = f"<html><head>{comment}{style}</head><body>{script}</body></html>"
        secured = secure_html(html)
        self.assert_policy_first(secured)
        for unchanged in (script, style, comment):
            self.assertIn(unchanged, secured)

    def test_policy_precedes_comments_that_a_browser_can_close_early(self):
        for prefix in ("<!-->", "<!--->"):
            with self.subTest(prefix=prefix):
                html = prefix + '<img src="/early.png">--><html><head></head></html>'
                secured = secure_html(html)
                self.assertLess(secured.index("Content-Security-Policy"), secured.index(prefix))
                self.assertEqual(secure_html(secured), secured)

    def test_is_idempotent_for_normal_uppercase_fragment_and_malformed_html(self):
        for html in (
            "", "<div>fragment</div>", "<HTML><HEAD></HEAD><BODY>x</BODY></HTML>",
            "<!doctype html>\n<html><head>\n<title>x</title></head></html>",
            '<head><meta http-equiv="Content-Security-Policy" content="default-src *"></head>',
            '<img src="/first.png"><head>late</head><head>again</head>',
            '<html><head data-note="unterminated',
            '<![bogus]><img src="/first.png">',
            '<!-- unclosed',
            '\ufeff<!doctype html><html><body>x</body></html>',
        ):
            with self.subTest(html=html):
                once = secure_html(html)
                self.assertEqual(secure_html(once), once)


if __name__ == "__main__":
    unittest.main()
