# platform/tests/test_public_identifiers.py
import importlib.util, pathlib, subprocess, sys, uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_public_identifiers.py"
# Synthetic deny-list entry generated at run time, so it never appears literally in any tracked file.
SENTINEL = "zz" + uuid.uuid4().hex + "zz"
spec = importlib.util.spec_from_file_location("cpi", SCRIPT)
cpi = importlib.util.module_from_spec(spec); spec.loader.exec_module(cpi)


def test_scan_reports_line_and_pattern_index_only(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("ok\nACME 은행 화면\n", encoding="utf-8")
    assert cpi.scan_text([str(f)], ["zzz", "ACME"]) == [(str(f), 2, 2)]


def test_filenames_are_scanned_and_reported_without_the_name(tmp_path):
    assert cpi.scan_names(["demo/acme-directions.html"], ["ACME"]) == [("demo/acme-directions.html", 1)]
    assert "acme" not in cpi.display("demo/acme-directions.html", ["ACME"]).casefold()


def test_missing_config_fails_closed(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(tmp_path / "none.txt")],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2 and "deny-list" in r.stderr


def test_identifiers_split_across_inline_elements_are_detected():
    assert cpi.scan_string("<span>A</span><span>CME</span>", ["ACME"]) == [1]
    assert cpi.scan_string("A<b>CM</b>E", ["ACME"]) == [1]
    assert cpi.scan_string('<span>A</span><span class="label">CME</span>', ["ACME"]) == [1]


def test_multiline_markup_in_files_is_detected(tmp_path):
    f = tmp_path / "page.html"
    f.write_text('<span>A</span><span\n class="label">CME</span>\n<!-- A\nCME -->', encoding="utf-8")
    assert [h[2] for h in cpi.scan_text([str(f)], ["ACME"])] == [1]


def test_embedded_svg_data_images_are_scanned(tmp_path):
    import base64
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><text>ACME</text></svg>').decode()
    assert cpi.scan_string(f'<img src="data:image/svg+xml;base64,{svg}">', ["ACME"]) == [1]
    f = tmp_path / "a.css"; f.write_text(f'.x{{background:url(data:image/svg+xml;base64,{svg})}}', encoding="utf-8")
    assert [h[2] for h in cpi.scan_text([str(f)], ["ACME"])] == [1]


def test_svg_data_url_forms(tmp_path):
    import base64, pytest
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><text>ACME</text></svg>').decode()
    for form in (f"data:image/svg+xml;charset=utf-8;base64,{svg}", f"DATA:image/SVG+xml;base64,{svg}",
                 "data:image/svg+xml;charset=utf-8,%3Csvg%3E%3Ctext%3EACME%3C/text%3E%3C/svg%3E"):
        assert cpi.scan_string(f'<img src="{form}">', ["ACME"]) == [1], form
    for bad in ("data:image/svg;base64,xx", f"data:image/svg+xml;base64,{svg[:-3]}!!"):
        with pytest.raises(cpi.ScanIncomplete):
            cpi.scan_string(f'<img src="{bad}">', ["ACME"])


def test_spaced_and_punctuated_css_strings(tmp_path):
    assert cpi.scan_string('<style>.b::before{content:"Acme " "Bank"}</style>', ["Acme Bank"]) == [1]
    assert cpi.scan_string('<style>.b::before{content:"@acme" /*c*/ "/ui"}</style>', ["@acme/ui"]) == [1]
    site = tmp_path / "site"; (site / "assets").mkdir(parents=True)
    (site / "assets" / "b.css").write_text('.b::before{content:"Acme " "Bank"}', encoding="utf-8")
    pats = tmp_path / "d.txt"; pats.write_text("Acme Bank\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1, r.stdout + r.stderr


def test_scanner_sources_and_plans_self_scan(tmp_path):
    # The scanner, its tests and these plans mention `data:image/svg` as text; with an unrelated synthetic
    # deny-list they scan clean (exit 0), while a malformed SVG data URL in real markup still fails closed (BN1).
    for rel in ("scripts/check_public_identifiers.py", "platform/tests/test_public_identifiers.py",
                "docs/superpowers/plans/2026-09-24-engine-design-loop.md"):
        assert cpi.scan_text([str(ROOT / rel)], [SENTINEL]) == []
    import pytest
    with pytest.raises(cpi.ScanIncomplete):
        cpi.scan_string('<img src="data:image/svg;base64,xx">', [SENTINEL])


def test_css_comments_do_not_split_or_invent_strings(tmp_path):
    assert cpi.scan_string('<style>.b::before{content:"Acme " /* "separator" */ "Bank"}</style>', ["Acme Bank"]) == [1]
    assert cpi.scan_string("<style>.b::before{content:'@acme' /* 'x' */ '/ui'}</style>", ["@acme/ui"]) == [1]
    site = tmp_path / "site"; site.mkdir()
    (site / "b.css").write_text('.b::before{content:"Acme " /* "s" */ "Bank"}', encoding="utf-8")
    pats = tmp_path / "d.txt"; pats.write_text("Acme Bank\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1, r.stdout + r.stderr


def test_descriptive_mentions_are_not_resources():
    # A title attribute, a CSS comment and a CSS content string that merely mention SVG data URLs are text,
    # not resources: clean with an unrelated deny-list. Resource URLs stay strict (BN1).
    for clean in ('<p title="Examples use data:image/svg URLs">clean</p>',
                  '<style>/* Examples use data:image/svg URLs */ p{color:red}</style><p>x</p>',
                  '<style>p::after{content:"see data:image/svg docs"}</style><p>x</p>'):
        assert cpi.scan_string(clean, [SENTINEL]) == [], clean
    import pytest
    for bad in ('<img src="data:image/svg;base64,xx">', '<p style="background:url(data:image/svg;base64,xx)">x</p>',
                '<style>p{background:image-set("data:image/svg;base64,xx" 1x)}</style>'):
        with pytest.raises(cpi.ScanIncomplete):
            cpi.scan_string(bad, [SENTINEL])


def test_published_archives_and_media(tmp_path):
    import hashlib, zipfile
    site = tmp_path / "site"; site.mkdir()
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    reg = tmp_path / "assets.sha256"; reg.write_text("# reviewed media\n", encoding="utf-8")
    def run():
        return subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site),
                               "--no-tree", "--asset-registry", str(reg)], capture_output=True, text=True, cwd=ROOT).returncode
    def zip_with(name, body):
        for f in site.iterdir():
            f.unlink()
        with zipfile.ZipFile(site / "source.zip", "w") as z:
            z.writestr(name, body)
    zip_with("src/App.tsx", "export const t = 'ACME'");  assert run() == 1   # text member
    zip_with("ACME/readme.md", "clean");                  assert run() == 1   # member name
    zip_with("src/App.tsx", "export const t = 'ok'");     assert run() == 0
    zip_with("inner.zip", b"PK");                         assert run() == 2   # nested archive
    zip_with("blob.bin", b"\xff\xfe");                    assert run() == 2   # undecodable unknown member
    for f in site.iterdir():
        f.unlink()
    (site / "broken.zip").write_bytes(b"not a zip");      assert run() == 2
    (site / "broken.zip").unlink()
    (site / "shot.png").write_bytes(b"\x89PNG unreviewed"); assert run() == 1   # unregistered media
    reg.write_text(hashlib.sha256((site / "shot.png").read_bytes()).hexdigest() + "  reviewed test image\n", encoding="utf-8")
    assert run() == 0                                                          # registered media


def test_current_built_sites_scan_completely(tmp_path):
    # The real outputs contain .mp4/.webm/.jpg/.png media, .vtt captions and source/dist ZIPs; after a build they
    # scan with exit 0 and no ScanIncomplete (review round 47, BO1). Skipped only when the build output is absent.
    import pytest
    pats = tmp_path / "d.txt"; pats.write_text(SENTINEL + "\n", encoding="utf-8")
    for site in (ROOT / "docs" / ".vitepress" / "dist", ROOT / "platform" / "web" / "dist"):
        if not site.is_dir():
            pytest.skip(f"{site} not built")
        r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                           capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 0, r.stdout + r.stderr


# The forbidden text is percent-encoded (%41%43%4D%45) or base64-encoded, so only a complete decode can find it.
SVG_ENC = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'%3E"
           "%3Ctext%3E%41%43%4D%45%3C/text%3E%3C/svg%3E")


def test_quoted_svg_attributes_do_not_truncate_payloads(tmp_path):
    assert "ACME" not in SVG_ENC
    assert cpi.scan_string(f'<img src="{SVG_ENC}">', ["ACME"]) == [1]
    assert cpi.scan_string(f'<p style="background:url(&quot;{SVG_ENC}&quot;)">x</p>', ["ACME"]) == [1]
    assert cpi.scan_string(f'.x{{background:url("{SVG_ENC}")}}', ["ACME"], language="css") == [1]
    assert cpi.scan_string(f'const icon = "{SVG_ENC}";', ["ACME"], language="js") == [1]            # bundle
    assert cpi.scan_string(f'const icon=`{SVG_ENC}`;export{{icon}}', ["ACME"], language="js") == [1]   # minified
    assert cpi.scan_string(f"See {SVG_ENC} in the docs.", ["ACME"], language="markdown") == [1]      # prose
    site = tmp_path / "site"; (site / "assets").mkdir(parents=True)
    (site / "assets" / "i.js").write_text(f'const icon="{SVG_ENC}";', encoding="utf-8")
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1, r.stdout + r.stderr
    import pytest
    with pytest.raises(cpi.ScanIncomplete):   # srcset carries several candidates: not exactly one URL
        cpi.scan_string(f'<img srcset="{SVG_ENC} 1x">', [SENTINEL])


def test_normalized_url_headers_are_data_urls(tmp_path):
    import base64
    b64 = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><text>ACME</text></svg>").decode()
    for header in ("da&#9;ta:image/svg+xml;base64,", "da&#10;ta:image/svg+xml;base64,", "da&#13;ta:image/svg+xml;base64,",
                   "&#32;data:image/svg+xml;base64,", "DATA: image/SVG+xml ; base64 ,".replace(" ,", ","),
                   "data:image/svg+xml;charset=utf-8;base64,"):
        assert cpi.scan_string(f'<img src="{header}{b64}">', ["ACME"]) == [1], header
        assert cpi.scan_string(f'.x{{background:url("{header.replace("&#9;", chr(9)).replace("&#10;", chr(10)).replace("&#13;", chr(13)).replace("&#32;", " ")}{b64}")}}',
                               ["ACME"], language="css") == [1], header
    site = tmp_path / "site"; site.mkdir()
    (site / "index.html").write_text(f'<img src="da&#9;ta:image/svg+xml;base64,{b64}">', encoding="utf-8")
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1, r.stdout + r.stderr
    import pytest
    with pytest.raises(cpi.ScanIncomplete):
        cpi.scan_string(f'<img src="da&#9;ta:image/svg+xml;base64,{b64[:-2]}!">', [SENTINEL])
    # URL fragments in bundle code are not URLs: `u.startsWith("data:")` and "data:image/svg+xml" scan clean.
    assert cpi.scan_string('if(u.startsWith("data:")||u==="data:image/svg+xml")x()', [SENTINEL], language="js") == []


def test_misleading_js_quotes_do_not_hide_svg_urls(tmp_path):
    import base64
    b64 = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><text>ACME</text></svg>").decode()
    literal = f'const icon="\\x64ata:image/svg+xml;base64,{b64}";'           # escaped scheme, encoded body
    pct = 'const p="\\u0064ata:image/svg+xml,%3Csvg%3E%3Ctext%3E%41%43%4D%45%3C/text%3E%3C/svg%3E";'
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    for prefix in ('/* " */', 'const quote=/"/;', "const q=/'/;// ' \n", "`${'\"'}`;"):
        for body in (literal, pct):
            code = prefix + body
            assert "ACME" not in code
            assert cpi.scan_string(code, ["ACME"], language="js") == [1], code
            assert cpi.scan_string(f"<script>{code}</script>", ["ACME"]) == [1], code
            site = tmp_path / "site"; (site / "assets").mkdir(parents=True, exist_ok=True)
            (site / "assets" / "i.js").write_text(code, encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site),
                                "--no-tree"], capture_output=True, text=True, cwd=ROOT)
            assert r.returncode == 1, (code, r.stdout + r.stderr)


def test_escaped_css_function_and_scheme(tmp_path):
    import base64
    b64 = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><text>ACME</text></svg>").decode()
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    for decl in (f"background:\\75rl(\\64 ata:image/svg+xml;base64,{b64})", f"background:u\\72 l(data:image/svg+xml;base64,{b64})",
                 f"background:URL( \\64 ata:image/svg+xml;base64,{b64} )", f'background:\\75 rl("\\64 ata:image/svg+xml;base64,{b64}")'):
        css = ".x{" + decl + "}"
        assert "ACME" not in css
        assert cpi.scan_string(css, ["ACME"], language="css") == [1], css
        q = "'" if '"' in decl else '"'   # keep the declaration inside one attribute value
        assert cpi.scan_string(f"<p style={q}{decl}{q}>x</p>", ["ACME"]) == [1], decl
        site = tmp_path / "site"; site.mkdir(exist_ok=True); (site / "s.css").write_text(css, encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                           capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 1, (css, r.stdout + r.stderr)
    # Controls: an ordinary resource still hits; descriptive text and ordinary escaped identifiers stay clean.
    assert cpi.scan_string(f".x{{background:url(data:image/svg+xml;base64,{b64})}}", ["ACME"], language="css") == [1]
    assert cpi.scan_string('.\\31 0x{color:red}p::after{content:"see data:image/svg docs"}', [SENTINEL], language="css") == []


def _tiny_png():
    """A 2x2 PNG (well under Vite's 4096-byte inlining limit); any unreviewed bytes stand in for image text."""
    import struct, zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_inlined_media_requires_registry(tmp_path):
    import base64, hashlib
    png = _tiny_png(); uri = "data:image/png;base64," + base64.b64encode(png).decode()
    empty, reviewed = set(), {hashlib.sha256(png).hexdigest()}
    for text, lang in ((f'<img src="{uri}">', "html"), (f".x{{background:url({uri})}}", "css"),
                       (f'const i="{uri}";export{{i}}', "js"), (f'/* " */const i="\\x64ata:image/png;base64,{uri[22:]}";', "js")):
        assert cpi.scan_string(text, [SENTINEL], language=lang, registry=empty) == [0], text
        assert cpi.scan_string(text, [SENTINEL], language=lang, registry=reviewed) == [], text


def test_media_is_recognized_by_bytes_not_label(tmp_path):
    import base64, hashlib
    png = _tiny_png(); body = base64.b64encode(png).decode()
    pats = tmp_path / "d.txt"; pats.write_text(SENTINEL + "\n", encoding="utf-8")
    reg = tmp_path / "assets.sha256"; reg.write_text("# none\n", encoding="utf-8")
    for label in ("image/png", "application/octet-stream", "text/plain", "", "IMAGE/X-UNKNOWN"):
        uri = f"data:{label};base64,{body}"
        for text, lang in ((f'<img src="{uri}">', "html"), (f".x{{background:url({uri})}}", "css"),
                           (f'const i="{uri}";', "js")):
            assert cpi.scan_string(text, [SENTINEL], language=lang, registry=set()) == [0], (label, lang)
            assert cpi.scan_string(text, [SENTINEL], language=lang, registry={hashlib.sha256(png).hexdigest()}) == []
        site = tmp_path / "site"; site.mkdir(exist_ok=True); (site / "i.js").write_text(f'const i="{uri}";', encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree",
                            "--asset-registry", str(reg)], capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 1 and "unreviewed media" in r.stdout, (label, r.stdout + r.stderr)
    import pytest
    blob = base64.b64encode(b"\x00\xff\xfe binary without a media signature").decode()
    with pytest.raises(cpi.ScanIncomplete):   # unsupported binary resource body
        cpi.scan_string(f'<img src="data:application/octet-stream;base64,{blob}">', [SENTINEL], registry=set())
    text = base64.b64encode(b"hello ACME").decode()   # text bodies are scanned, whatever their label
    assert cpi.scan_string(f'<a href="data:;base64,{text}">x</a>', ["ACME"]) == [1]
    assert cpi.scan_string(f'<iframe src="data:text/html;base64,{text}"></iframe>', ["ACME"]) == [1]


def test_vite_inlined_png_goes_through_the_registry(tmp_path):
    # The real bundler: a small imported PNG is inlined into the JS bundle; the built site fails the gate until
    # the image's hash is reviewed into the registry (review round 52, BT1).
    import hashlib, pytest, shutil
    vite = ROOT / "platform" / "web" / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.exists() or not shutil.which("node"):
        pytest.skip("platform/web dependencies are not installed")
    app = tmp_path / "app"; app.mkdir()
    (app / "logo.png").write_bytes(_tiny_png())
    (app / "main.js").write_text('import logo from "./logo.png"; document.body.style.backgroundImage = `url(${logo})`;',
                                 encoding="utf-8")
    (app / "index.html").write_text('<!doctype html><script type="module" src="./main.js"></script>', encoding="utf-8")
    subprocess.run(["node", str(vite), "build", "--logLevel", "error"], cwd=app, check=True, capture_output=True)
    bundle = "".join(p.read_text(encoding="utf-8") for p in (app / "dist").rglob("*.js"))
    assert "data:image/png;base64," in bundle and not list((app / "dist").rglob("*.png"))   # really inlined
    pats = tmp_path / "d.txt"; pats.write_text(SENTINEL + "\n", encoding="utf-8")
    reg = tmp_path / "assets.sha256"; reg.write_text("# none\n", encoding="utf-8")
    def gate():
        return subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(app / "dist"),
                               "--no-tree", "--asset-registry", str(reg)], capture_output=True, text=True, cwd=ROOT)
    r = gate(); assert r.returncode == 1 and "unreviewed media" in r.stdout, r.stdout + r.stderr
    reg.write_text(hashlib.sha256(_tiny_png()).hexdigest() + "  reviewed test image\n", encoding="utf-8")
    r = gate(); assert r.returncode == 0, r.stdout + r.stderr


def _nested_svg(levels):
    """`levels` SVG documents nested through data-URL images; the innermost holds base64-encoded text."""
    import base64
    inner = "<svg xmlns='http://www.w3.org/2000/svg'><text>ACME</text></svg>"
    for _ in range(levels - 1):
        uri = "data:image/svg+xml;base64," + base64.b64encode(inner.encode()).decode()
        inner = f"<svg xmlns='http://www.w3.org/2000/svg'><image href='{uri}'/></svg>"
    return "data:image/svg+xml;base64," + base64.b64encode(inner.encode()).decode()


def test_nesting_limit_fails_closed(tmp_path):
    import pytest
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    def site_rc(html):
        site = tmp_path / "site"; site.mkdir(exist_ok=True); (site / "index.html").write_text(html, encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site),
                               "--no-tree"], capture_output=True, text=True, cwd=ROOT).returncode
    exact = f'<img src="{_nested_svg(cpi.MAX_NESTING)}">'
    over = f'<img src="{_nested_svg(cpi.MAX_NESTING + 1)}">'
    assert "ACME" not in exact + over
    assert cpi.scan_string(exact, ["ACME"]) == [1] and site_rc(exact) == 1          # exactly at the limit: found
    with pytest.raises(cpi.ScanIncomplete):                                          # one over: never silent
        cpi.scan_string(over, [SENTINEL])
    f = tmp_path / "over.html"; f.write_text(over, encoding="utf-8")
    with pytest.raises(cpi.ScanIncomplete):
        cpi.scan_text([str(f)], [SENTINEL])
    assert site_rc(over) == 2


def test_tree_mode_scans_unknown_tracked_types(tmp_path):
    repo = tmp_path / "repo"; (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "check_public_identifiers.py").write_bytes(SCRIPT.read_bytes())
    (repo / "scripts" / "public-assets.sha256").write_text("# none\n", encoding="utf-8")
    pats = tmp_path / "d.txt"; pats.write_text(SENTINEL + "\n", encoding="utf-8")   # the copied scanner itself mentions ACME
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    def run(name, body):
        for f in list(repo.iterdir()):
            if f.name not in ("scripts", ".git"):
                f.unlink()
        (repo / name).write_bytes(body)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        return subprocess.run([sys.executable, str(repo / "scripts" / "check_public_identifiers.py"),
                               "--patterns-file", str(pats)], capture_output=True, text=True, cwd=repo).returncode
    for name in ("Tour.vue", "run.sh", "rows.jsonl", "Dockerfile", "d.unknownext", "a.HTM", "b.CSS"):
        assert run(name, f"<p>{SENTINEL}</p>".encode()) == 1, name
    assert run("clean.vue", b"<template><p>ok</p></template>") == 0
    assert run("blob.unknownext", b"\xff\xfe\x00") == 2
    for f in list(repo.iterdir()):
        if f.name not in ("scripts", ".git"):
            f.unlink()
    (repo / f"{SENTINEL}-link").symlink_to("scripts")           # a tracked symlink: its name and target are
    (repo / "to-site").symlink_to(f"../{SENTINEL}")             # scanned as text, never followed
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    r = subprocess.run([sys.executable, str(repo / "scripts" / "check_public_identifiers.py"), "--patterns-file",
                        str(pats)], capture_output=True, text=True, cwd=repo)
    assert r.returncode == 1 and r.stdout.count("pattern #1") == 2, r.stdout + r.stderr


def test_extensions_are_case_insensitive_in_both_modes(tmp_path):
    site = tmp_path / "site"; site.mkdir()
    pats = tmp_path / "d.txt"; pats.write_text("ACME\n", encoding="utf-8")
    for name, body in (("a.htm", "<p>ACME</p>"), ("b.HTML", "<p>ACME</p>"), ("c.CSS", 'p::before{content:"\\41 CME"}'),
                       ("d.unknownext", "ACME")):
        for f in site.iterdir():
            f.unlink()
        (site / name).write_text(body, encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                           capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 1, (name, r.stdout + r.stderr)
    assert cpi.classify("x/A.HTM") == "html" and cpi.classify("B.Css") == "css" and cpi.classify("c.PNG") == "media"
    assert cpi.classify("d.WOFF2") == "font" and cpi.classify("e.ZIP") == "archive" and cpi.classify("f.Vtt") == "code"


def test_adjacent_css_strings_are_detected(tmp_path):
    assert cpi.scan_string('<style>.b::before{content:"\\41" "CME"}</style>', ["ACME"]) == [1]
    f = tmp_path / "public" / "brand.css"; f.parent.mkdir(); f.write_text('.b::before{content:"A" /*x*/ "CM" "E"}', encoding="utf-8")
    assert [h[2] for h in cpi.scan_text([str(f)], ["ACME"])] == [1]


def test_standalone_css_is_css_unescaped(tmp_path):
    f = tmp_path / "brand.css"; f.write_text('.brand::before{content:"\\41 CME"}', encoding="utf-8")
    assert [h[2] for h in cpi.scan_text([str(f)], ["ACME"])] == [1]


def test_css_escaped_generated_content_is_detected():
    assert cpi.scan_string('<style>p::before{content:"\\41 CME"}</style><p></p>', ["ACME"]) == [1]
    assert cpi.scan_string('<p style="--x:\\41 CME">x</p>', ["ACME"]) == [1]


def test_url_literals_are_not_comments():
    assert cpi.scan_string('<script>const site="https://example.invalid";document.body.textContent=\'A\'+"CM"+`E`;</script>', ["ACME"]) == [1]


def test_repository_self_scan_is_clean(tmp_path):
    # The scanner itself, browser.py and react_runtime.py contain words such as `evaluate`; with a synthetic
    # deny-list the repository scan must succeed with exit 0 and never raise ScanIncomplete (BJ1).
    pats = tmp_path / "d.txt"; pats.write_text(SENTINEL + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_undecodable_text_file_fails_closed(tmp_path):
    import pytest
    f = tmp_path / "x.html"; f.write_bytes(b"<p>ACME</p><!--\xff-->")
    with pytest.raises(cpi.ScanIncomplete):
        cpi.scan_text([str(f)], ["ACME"])


def test_script_context_is_not_defeated_by_prose_or_comments():
    assert cpi.scan_string("<p>It's clean.</p><script>document.body.textContent='A\\u0043ME';</script>", ["ACME"]) == [1]
    assert cpi.scan_string("<script>/* it's */ x = 'A\\x43ME'</script>", ["ACME"]) == [1]
    assert cpi.scan_string("<script>x = 'A' + \"CM\" + `E`</script>", ["ACME"]) == [1]
    assert cpi.scan_string("<p>It's clean.</p><script>x='hello'</script>", ["ACME"]) == []


def test_javascript_line_continuations_are_removed():
    for sep in ("\n", "\r\n", "\u2028", "\u2029"):
        assert cpi.scan_string('<script>s="A\\' + sep + 'CME"</script>', ["ACME"]) == [1]


def test_many_literals_do_not_hide_a_later_identifier(tmp_path):
    text = "".join(f'"x{i}";' for i in range(25000)) + '"A\\u0043ME"'
    assert cpi.scan_string(text, ["ACME"]) == [1]


def test_oversize_content_is_incomplete_not_clean():
    import pytest
    with pytest.raises(cpi.ScanIncomplete):
        cpi.scan_string("a" * (cpi.MAX_SCAN_BYTES + 1), ["ACME"])


def test_javascript_escaped_identifiers_are_detected(tmp_path):
    assert cpi.scan_string('<script>const n = "A\\u0043ME";</script>', ["ACME"]) == [1]
    assert cpi.scan_string('var s="\\uD569\\uC131\\uC740\\uD589"', ["합성은행"]) == [1]
    bundle = tmp_path / "app.js"; bundle.write_text('x=`A\\x43ME`', encoding="utf-8")
    assert cpi.scan_text([str(bundle)], ["ACME"])


def test_entity_encoded_identifiers_are_detected():
    assert cpi.scan_string("<p>A&#67;ME</p>", ["ACME"]) == [1]
    assert cpi.scan_string('<img alt="A&#x43;ME">', ["ACME"]) == [1]
    assert cpi.scan_string("<p>clean</p>", ["ACME"]) == []


def test_built_site_is_scanned(tmp_path):
    site = tmp_path / "site"; site.mkdir()
    (site / "index.html").write_text("<p>ACME</p>", encoding="utf-8")
    pats = tmp_path / "p.txt"; pats.write_text("ACME\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--patterns-file", str(pats), "--site", str(site), "--no-tree"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1 and "pattern #1" in r.stdout and "ACME" not in r.stdout


def test_tenant_replacements_use_the_neutral_alias():
    """PR #30 review 1, #2: an account number is prefixed by the neutral alias, and the seeded agent's asset ids
    resolve to the renamed seed assets (no leftover short form of the customer name)."""
    import re as _re
    import json
    repo = pathlib.Path(__file__).resolve().parents[2]
    canvas = (repo / "demo/uiux-studio/design-canvas/TransferSingle.dc.html").read_text(encoding="utf-8")
    prefixes = {m.group(1).strip() for m in _re.finditer(r"·\s*([^<·]*?)\s*\d{3}-\d{6}-\d{5}", canvas)}
    assert prefixes == {"고객사 A"}, prefixes
    sys.path.insert(0, str(repo / "demo/uiux-studio"))
    try:
        from feedback.assets_api import _slug
        spec = importlib.util.spec_from_file_location("seed_assets", repo / "demo/uiux-studio/scripts/seed_assets.py")
        seed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seed)
    finally:
        sys.path.remove(str(repo / "demo/uiux-studio"))
    seeded = {f"{s['type']}:{_slug(s['name'])}" for s in seed.SAMPLES}
    for sample in seed.SAMPLES:
        if sample["type"] == "agent":
            ids = json.loads(sample["content"])["asset_ids"]
            assert ids and set(ids) <= seeded, ids
