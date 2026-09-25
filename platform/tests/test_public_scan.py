# platform/tests/test_public_scan.py — publication gate at the two legacy public writers (Task E2, Step 3a)
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import public_scan as ps  # noqa: E402

# Synthetic deny-list entries: the literal test token and a runtime SENTINEL that never appears in any file.
PATTERNS = ["ACME"]
SENTINEL = "zz" + uuid.uuid4().hex + "zz"
ONE_PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=")
ACME_SVG = "<svg xmlns='http://www.w3.org/2000/svg'><text>ACME</text></svg>"


def _b64svg(markup):
    return "data:image/svg+xml;base64," + base64.b64encode(markup.encode()).decode()


def _nested_svg(levels):
    inner = ACME_SVG
    for _ in range(levels - 1):
        inner = f"<svg xmlns='http://www.w3.org/2000/svg'><image href='{_b64svg(inner)}'/></svg>"
    return _b64svg(inner)


def _other_png():
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\xff\x00" * 3 for _ in range(3))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 3, 3, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def blocked(html):
    with pytest.raises(ps.PublicationBlocked):
        ps.prepare_static_html(html, PATTERNS)


# ------------------------------------------------------------------ gate core

def test_missing_unreadable_or_empty_deny_list_warns_and_scans_nothing(monkeypatch, caplog):
    """User decision (PR #30 fix round 1): no configured deny-list -> warn, identifier scan skipped."""
    monkeypatch.delenv(ps.PARAM_ENV, raising=False)
    with caplog.at_level("WARNING"):
        assert ps.load_patterns() == []
    assert "not configured" in caplog.text
    monkeypatch.setenv(ps.PARAM_ENV, "/test/denylist-" + uuid.uuid4().hex)
    monkeypatch.setattr(ps, "_fetch_parameter", lambda name: (_ for _ in ()).throw(RuntimeError("AccessDenied")))
    assert ps.load_patterns() == []
    monkeypatch.setenv(ps.PARAM_ENV, "/test/denylist-" + uuid.uuid4().hex)
    monkeypatch.setattr(ps, "_fetch_parameter", lambda name: "# comment only\n\n")
    assert ps.load_patterns() == []
    monkeypatch.setenv(ps.PARAM_ENV, "/test/denylist-" + uuid.uuid4().hex)
    monkeypatch.setattr(ps, "_fetch_parameter", lambda name: "ACME\n# c\nOther Name\n")
    assert ps.load_patterns() == ["ACME", "Other Name"]


def test_without_a_deny_list_the_static_safety_still_applies():
    """No deny-list: identifiers are not scanned, but sanitization, CSP and the media registry stay mandatory."""
    out = ps.prepare_static_html("<p>ACME</p><script>x=1</script><img onerror=alert(1) src=/x.png>", [])
    assert "<p>ACME</p>" in out and "<script" not in out and "onerror" not in out and out.startswith(ps.CSP_META)
    unknown = base64.b64encode(_other_png()).decode()
    out = ps.prepare_static_html(f'<img alt="a" src="data:image/png;base64,{unknown}">', [])
    assert unknown not in out
    ps.check_publishable("<p>ACME</p>", [])
    with pytest.raises(ps.PublicationBlocked):          # unreviewed media still blocks the published text
        ps.check_publishable(f'<img src="data:image/png;base64,{unknown}">', [])


def test_clean_static_draft_is_published_unchanged():
    draft = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>시안</title>'
             '<style>.card{color:#123;border:1px solid #ddd}</style></head>'
             '<body><main class="card"><h1>예금 가입</h1><p>금리 &amp; 기간을 확인하세요.</p>'
             '<a href="/next">다음</a><img alt="로고" src="data:image/png;base64,'
             + base64.b64encode(ONE_PX).decode() + '"></main></body></html>')
    assert ps.prepare_static_html(draft, PATTERNS) == draft.replace("<!doctype html>", "<!doctype html>" + ps.CSP_META, 1)


def test_plain_and_encoded_identifiers_are_blocked():
    for html in ("<p>ACME</p>", "<p>A&#67;ME</p>", '<img alt="A&#x43;ME" src="/x.png">',
                 "<span>A</span><span>CME</span>", '<span>A</span><span class="label">CME</span>', "A<b>CM</b>E",
                 "<script>document.body.textContent='A\\u0043ME'</script>",
                 "".join(f'"x{i}";' for i in range(25000)) + '"A\\u0043ME"',
                 '<p style="--x:\\41 CME">x</p>', '<style>p::before{content:"\\41 CME"}</style><p></p>'):
        blocked(html)


def test_css_escaped_content_is_seen_by_the_css_view():
    assert ps.core.scan_string('<style>p::before{content:"\\41 CME"}</style>', PATTERNS) == [1]


def test_over_bound_payload_is_blocked():
    blocked("<p>" + "a" * (ps.core.MAX_SCAN_BYTES + 1) + "</p>")


def test_undetectable_runtime_vectors_are_removed_before_publication():
    draft = ('<html><body><p id="t">안내</p>'
             '<script>document.getElementById("t").textContent = "A\\u0043".concat("ME")</script>'
             '<button onclick="this.textContent=String.fromCharCode(65,67,77,69)">확인</button>'
             '<noscript>x</noscript><template><p>t</p></template><iframe src="/x"></iframe>'
             '<object data="/x"></object><embed src="/x"><a href="java&#9;script:alert(1)">링크</a>'
             '<style>@import url(/x.css);p::after{content:"x"}q{content:""}</style></body></html>')
    out = ps.prepare_static_html(draft, PATTERNS)
    for vector in ("<script", "onclick", "fromCharCode", "concat", "<noscript", "<template", "<iframe", "<object",
                   "<embed", "javascript", "@import", 'content:"x"'):
        assert vector not in out, vector
    assert '<p id="t">안내</p>' in out and "<button>확인</button>" in out and 'content:""' in out
    assert ps.core.scan_string(out, PATTERNS, registry=ps.registry()) == []


def test_svg_data_urls_with_normalized_headers_are_blocked():
    b64 = base64.b64encode(ACME_SVG.encode()).decode()
    for header in ("da&#9;ta:image/svg+xml;base64,", "da&#10;ta:image/svg+xml;base64,", "da&#13;ta:image/svg+xml;base64,",
                   "&#32;data:image/svg+xml;base64,", "DATA: image/SVG+xml ;base64,", "data:image/svg+xml;charset=utf-8;base64,"):
        blocked(f'<img src="{header}{b64}">')
    blocked("<img src=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Ctext%3E%41%43%4D%45%3C/text%3E%3C/svg%3E\">")


def test_undecodable_svg_data_url_blocks():
    blocked('<img src="data:image/svg;base64,xx">')
    blocked(f'<img src="{_b64svg(ACME_SVG)[:-3]}!!">')


def test_clean_svg_is_kept_sanitized():
    svg = "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script><text>ok</text></svg>"
    out = ps.prepare_static_html(f'<img src="{_b64svg(svg)}">', PATTERNS)
    inner = base64.b64decode(out.split("base64,", 1)[1].split('"', 1)[0]).decode()
    assert "<script" not in inner and "<text>ok</text>" in inner


def test_nesting_limit_blocks_or_removes_never_publishes_unscanned():
    blocked(f'<img src="{_nested_svg(ps.MAX_NESTING)}">')                  # exactly at the limit: found
    with pytest.raises(ps.PublicationBlocked):                               # one deeper: incomplete original
        ps.prepare_static_html(f'<img src="{_nested_svg(ps.MAX_NESTING + 1)}">', [SENTINEL])
    # The sanitizer itself removes the over-depth resource, so even a direct sanitize writes nothing unscanned.
    out = ps.sanitize_static(f'<img src="{_nested_svg(ps.MAX_NESTING + 1)}">')
    ps.check_publishable(out, [SENTINEL])


def test_raster_media_is_kept_only_when_registered():
    reviewed, unknown = base64.b64encode(ONE_PX).decode(), base64.b64encode(_other_png()).decode()
    assert hashlib.sha256(ONE_PX).hexdigest() in ps.registry()
    for label in ("image/png", "application/octet-stream", "text/plain", "", "IMAGE/X-UNKNOWN"):
        out = ps.prepare_static_html(f'<img alt="a" src="data:{label};base64,{unknown}">', [SENTINEL])
        assert unknown not in out and "src=" not in out, label
        out = ps.prepare_static_html(f'<img alt="a" src="data:{label};base64,{reviewed}">', [SENTINEL])
        assert reviewed in out, label
    out = ps.prepare_static_html(f'<p style="background:url(data:image/png;base64,{unknown})">x</p>', [SENTINEL])
    assert unknown not in out
    blob = base64.b64encode(b"\x00\xff\xfe binary without a media signature").decode()
    with pytest.raises(ps.PublicationBlocked):     # unrecognized binary body: incomplete, blocked
        ps.prepare_static_html(f'<img src="data:application/octet-stream;base64,{blob}">', [SENTINEL])
    text = base64.b64encode(b"hello ACME").decode()
    blocked(f'<a href="data:;base64,{text}">x</a>')   # text bodies are scanned as text


def test_escaped_css_function_and_scheme_are_blocked():
    b64 = base64.b64encode(ACME_SVG.encode()).decode()
    for decl in (f"background:\\75rl(\\64 ata:image/svg+xml;base64,{b64})", f"background:u\\72 l(data:image/svg+xml;base64,{b64})",
                 f'background:\\75 rl("\\64 ata:image/svg+xml;base64,{b64}")'):
        q = "'" if '"' in decl else '"'
        blocked(f"<p style={q}{decl}{q}>x</p>")
        blocked(f"<style>.x{{{decl}}}</style><p class=x>x</p>")


# ------------------------------------------------------------------ packaged Lambda artifact (BU2)

def _assemble(tmp_path):
    """Run the deploy.sh `api-dist` assembly lines that matter for the gate into a temporary directory."""
    dist = tmp_path / "api-dist"
    dist.mkdir()
    shutil.copytree(ROOT / "api" / "common", dist / "common")
    for line in (ROOT / "deploy.sh").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("cp ../scripts/"):
            subprocess.run(["bash", "-c", line.strip().replace("api-dist/", f"{dist}/")], cwd=ROOT, check=True)
    return dist


def _artifact_run(dist, code):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path[:0] = [{str(dist)!r}]\n" + code],
                          cwd=dist, capture_output=True, text=True, env=env)


def test_public_scan_artifact_runs_without_the_repository(tmp_path):
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "cp ../scripts/check_public_identifiers.py api-dist/common/public_scan_core.py" in deploy
    assert "cp ../scripts/public-assets.sha256 api-dist/common/public-assets.sha256" in deploy
    dist = _assemble(tmp_path)
    moved = shutil.move(str(dist), str(tmp_path / "isolated" / "api-dist"))   # the repository path is unreachable
    dist = Path(moved)
    reviewed, unknown = base64.b64encode(ONE_PX).decode(), base64.b64encode(_other_png()).decode()
    code = f"""
from common import public_scan as ps
assert ps.core.__name__.endswith("public_scan_core"), ps.core.__name__
assert "scripts" not in str(ps.REGISTRY_PATH)
ps.check_publishable('<img src="data:image/png;base64,{reviewed}">', ["{SENTINEL}"])
try:
    ps.check_publishable('<img src="data:image/png;base64,{unknown}">', ["{SENTINEL}"])
except ps.PublicationBlocked:
    print("unregistered-blocked")
"""
    r = _artifact_run(dist, code)
    assert r.returncode == 0 and "unregistered-blocked" in r.stdout, r.stdout + r.stderr
    (dist / "common" / "public-assets.sha256").unlink()
    r = _artifact_run(dist, f"""
from common import public_scan as ps
try:
    ps.prepare_static_html("<p>clean</p>", ["{SENTINEL}"])
except ps.PublicationBlocked:
    print("fail-closed")
""")
    assert r.returncode == 0 and "fail-closed" in r.stdout, r.stdout + r.stderr


# ------------------------------------------------------------------ both legacy publishers

def _design(monkeypatch, patterns):
    from handlers import design
    writes = []
    monkeypatch.setattr(design, "WEB_BUCKET", "web")
    monkeypatch.setattr(design, "_put", lambda key, data, ctype: writes.append((key, data)))
    monkeypatch.setattr(design, "_get_json", lambda key, default: default)
    monkeypatch.setattr(ps, "load_patterns", patterns)
    return design, writes


def _run(html):
    return {"flow": {"steps": [{"id": "intro", "title": "소개", "html": html}]}, "prd": {}, "report": {}}


def _no_patterns():
    return []            # load_patterns() without a configured deny-list (warns; user decision, fix round 1)


FORBIDDEN_DRAFTS = ("<p>ACME</p>", "<p>A&#67;ME</p>", "<span>A</span><span>CME</span>",
                    '<span>A</span><span class="label">CME</span>', "A<b>CM</b>E",
                    "<script>x='A\\u0043ME'</script>", "".join(f'"x{i}";' for i in range(25000)) + '"A\\u0043ME"',
                    "<p>" + "a" * 20_000_001 + "</p>", f'<img src="{_b64svg(ACME_SVG)}">',
                    '<style>p::before{content:"\\41 CME"}</style><p></p>')


def test_design_store_run_gate(monkeypatch):
    design, writes = _design(monkeypatch, lambda: list(PATTERNS))
    full = design.store_run("r1", _run('<html><body><p>안내</p><script>x=1</script></body></html>'), {})
    html = dict(writes)["design-runs/r1/intro.html"].decode()
    assert "<p>안내</p>" in html and "<script>x=1" not in html and full["steps"][0]["id"] == "intro"
    for draft in FORBIDDEN_DRAFTS:
        writes.clear()
        with pytest.raises(ps.PublicationBlocked):
            design.store_run("r2", _run(draft), {})
        assert writes == [], draft[:40]
    writes.clear()
    with pytest.raises(ps.PublicationBlocked):     # identifiers in the published run report JSON block too
        design.store_run("r3", {**_run("<p>ok</p>"), "report": {"summary": "ACME"}}, {})
    assert writes == []
    design, writes = _design(monkeypatch, _no_patterns)      # no deny-list: sanitized page still publishes
    design.store_run("r4", _run("<p>clean</p><script>x=1</script>"), {})
    html = dict(writes)["design-runs/r4/intro.html"].decode()
    assert "<p>clean</p>" in html and "<script" not in html and ps.CSP_META in html


def test_design_store_run_without_bucket_needs_no_deny_list(monkeypatch):
    from handlers import design
    monkeypatch.setattr(design, "WEB_BUCKET", "")
    monkeypatch.setattr(ps, "load_patterns", _no_patterns)
    assert design.store_run("r5", _run("<p>clean</p>"), {})["steps"][0]["id"] == "intro"


def test_worker_publish_gate(monkeypatch):
    import test_studio_worker as tw
    for patterns, draft, published in ((lambda: list(PATTERNS), "<html><body><p>안내</p><script>x=1</script></body></html>", True),
                                       (lambda: list(PATTERNS), "<html><body><p>A&#67;ME</p></body></html>", False),
                                       (lambda: list(PATTERNS), "<html><body><span>A</span><span>CME</span></body></html>", False),
                                       (lambda: list(PATTERNS), f'<html><body><img src="{_b64svg(ACME_SVG)}"></body></html>', False),
                                       (_no_patterns, "<html><body><p>안내</p><script>x=1</script></body></html>", True)):
        apigw, s3, store = tw._Apigw(), tw._S3(), tw.StudioStore()
        tw._wire(monkeypatch, apigw, s3, store)
        monkeypatch.setattr(ps, "load_patterns", patterns)
        monkeypatch.setattr(tw.w, "_generate", lambda system, user, max_tokens, _d=draft, **kw: tw.FakeStream(["```html\n", _d, "\n```"]))
        store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
        tw.w.handler(tw._event(maxRounds=1), None)
        bodies = [b.decode() for (bucket, key), b in s3.objects.items() if key.startswith("studio/drafts/")]
        if published:
            assert bodies and all("<script>x=1" not in b and "<p>안내</p>" in b for b in bodies)
            assert store.get_job("job123456789")["status"] == "done"
        else:
            assert bodies == [], draft[:40]
            assert store.get_job("job123456789")["status"] == "failed"


# ------------------------------------------------------------------ foreign content and parser differentials (PR #30 review 1, #1)

MUTATION_DRAFTS = (
    "<svg><style><img src=x onerror=\"window.__fired=1\">ACME</style></svg>",
    "<math><mtext><table><mglyph><style><img src=x onerror=\"window.__fired=1\"></style></mglyph></table></mtext></math>",
    "<svg></p><style><a id=\"</style><img src=x onerror='window.__fired=1'>\"></style></svg>",
    "<xmp><a title=\"</xmp><img src=x onerror='window.__fired=1'>\"></xmp>",
    "<noembed><a title=\"</noembed><img src=x onerror='window.__fired=1'>\"></noembed>",
    "<noframes><a title=\"</noframes><img src=x onerror='window.__fired=1'>\"></noframes>",
    "<svg><style/><img src=x onerror=\"window.__fired=1\"></svg>",
    "<textarea><b title=\"</textarea><img src=x onerror='window.__fired=1'>\"></b></textarea>",
    "<img src=x \"onerror=window.__fired=1>",
)


def test_svg_style_markup_cannot_survive_sanitization():
    for draft in MUTATION_DRAFTS:
        out = ps.sanitize_static(draft, media=set())
        assert not re.search(r"<[^>]*onerror", out, re.I), (draft, out)
        style = out.lower().split("<style", 1)[1].split("</style", 1)[0] if "<style" in out.lower() else ""
        assert "<" not in style.split(">", 1)[-1], (draft, out)


def test_published_static_html_forbids_scripts_by_csp():
    out = ps.prepare_static_html("<!doctype html><html><head><title>t</title></head><body><p>x</p></body></html>", PATTERNS)
    assert out.startswith("<!doctype html>" + ps.CSP_META), out[:200]
    assert "script-src 'none'" in ps.CSP_META
    assert ps.prepare_static_html("<p>x</p>", PATTERNS).startswith(ps.CSP_META)


def _chromium():
    path = os.environ.get("WORKSPACE_CHROMIUM_PATH") or \
        "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed")
    return sync_playwright, (path if os.path.isfile(path) else None)


def test_sanitized_mutation_drafts_do_not_execute_in_chromium():
    sync_playwright, path = _chromium()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        try:
            def load(html):
                page = browser.new_page()
                page.route("**/*", lambda route: route.abort())
                page.set_content(html)
                page.wait_for_timeout(100)
                fired, text = page.evaluate("[window.__fired, document.body ? document.body.innerText : '']")
                page.close()
                return fired, text
            # Unsanitized controls fire, so the probe is meaningful.
            assert load(MUTATION_DRAFTS[0])[0] == 1 and load(MUTATION_DRAFTS[3])[0] == 1
            for draft in MUTATION_DRAFTS:
                fired, text = load(ps.sanitize_static(draft, media=set()))
                assert fired is None and "ACME" not in text, draft
            # The published page carries script-src 'none': an inline handler behind it is refused.
            published = ps.prepare_static_html("<p>x</p>", PATTERNS)
            assert load(published + "<img src=x onerror=\"window.__fired=1\">")[0] is None
        finally:
            browser.close()


def test_both_publishers_neutralize_foreign_content(monkeypatch):
    design, writes = _design(monkeypatch, lambda: [SENTINEL])
    design.store_run("r6", _run("<html><body>" + MUTATION_DRAFTS[0].replace("ACME", "") + "</body></html>"), {})
    html = dict(writes)["design-runs/r6/intro.html"].decode()
    assert not re.search(r"<[^>]*onerror", html, re.I) and ps.CSP_META in html
    import test_studio_worker as tw
    apigw, s3, store = tw._Apigw(), tw._S3(), tw.StudioStore()
    tw._wire(monkeypatch, apigw, s3, store)
    monkeypatch.setattr(ps, "load_patterns", lambda: [SENTINEL])
    draft = "<html><body><p>안내</p>" + MUTATION_DRAFTS[3] + "</body></html>"
    monkeypatch.setattr(tw.w, "_generate", lambda system, user, max_tokens, _d=draft, **kw: tw.FakeStream(["```html\n", _d, "\n```"]))
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    tw.w.handler(tw._event(maxRounds=1), None)
    bodies = [b.decode() for (bucket, key), b in s3.objects.items() if key.startswith("studio/drafts/")]
    assert bodies and all(not re.search(r"<[^>]*onerror", b, re.I) and ps.CSP_META in b for b in bodies)
