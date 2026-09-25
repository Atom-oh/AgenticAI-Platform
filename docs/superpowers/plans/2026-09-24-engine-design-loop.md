# Engine Track — Design Ontology Generation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure, offline-testable engine for the design PoC. The engine takes knowledge from the canonical ontology, extracts a cited PRD from admitted document derivatives, and builds a case-aware flow. It then produces validated compositions: layout variants, state variants and targeted edits. Coverage and a fail-closed verification graph check them. From there the engine emits a react-kit React project and a Browser contract, and packages the customer-convention handoff. A golden benchmark measures quality.

**Architecture:**
- **Shared IR.** The engine is `platform/design_loop/`, with injected model callables and no AWS imports. A **composition tree** is the single intermediate representation.
- **One rendering path.** A composition is emitted as React source for the platform kit (`@studio/approved-ui`, `react-kit/ui`) in the react-kit project layout (`src/App.tsx`, `src/pages/*.tsx`, `src/logic/*.ts`). The preview *is* that source compiled by `react-kit/compile.cjs` and observed by the Browser. There is no separate HTML template renderer, so design and code cannot diverge (Codex #13).
- **Deterministic checks.** The engine derives an assertion-DSL contract (`workspace/rules.py`) from the PRD and flow. The existing Browser verifier (`workspace/browser.py`) executes it.

**Wiring.** Nothing in this plan is wired to an API route, worker task, Runtime mode or UI. That happens in [C](2026-09-24-c-design-poc-slice.md) after [B2](2026-09-24-b2-service-integration.md) exits (roadmap parallel-track rule).

**Tech Stack:** Python 3.12 (pytest), Node 22 with react-kit (`compile.cjs`, TypeScript 5.6.3, esbuild 0.25.12) and source-analyzer. Both are already installed by CI `platform-ci.yml` (`npm ci --ignore-scripts` in `react-kit` and `source-analyzer`).

**Roadmap:** unit 4. Codex findings fixed here: #3–#15, #22 (engine parts), #23 (engine parts).

**Owning contracts touched:**
- `workspace/ONTOLOGY_CONTRACT.md` (uxModel property, PolicyRule properties, publication rewrite, procedure snapshot reader)
- `workspace/AGENTCORE_CONTRACT.md` `platform-ontology/1` (property table)
- `workspace/REACT_CONTRACT.md` (generated-project shape stays within the existing source policy; no change to limits)

Each contract edit ships in the task that changes the interface.

**Acceptance cases supported (O-mode):** ONT-02, ONT-03, ONT-04 (property validation), GEN-01 (context fingerprint), GEN-04 (24-file/128 KiB policy respected by construction), GEN-05 (bounded repair with retained rounds).

## Global Constraints

- The roadmap global constraints apply.
- `design_loop` imports only the standard library and `workspace.ontology_schema` / `workspace.ontology_ux`. Both are pure; B2 packages them into the Runtime image as `design_schema` (Codex #19).
- The engine never reads original customer documents. Document inputs are *admitted derivative pages* produced by B0 intake: `{admissionId, derivativeHash, sourceRef, page, text}`.
- Model callables use the existing `design_deps` contract (`engine/gate.py:210-259`):
  - `deps["generate"](system, user, on_token) -> str`
  - `deps["llm_judge"](item, context) -> {verdict, evidence}`
  - `deps["usage"]()`

  Tests inject fakes. A missing callable is an explicit `blocked` outcome, never a pass.
- Every string the engine puts into a prompt must already be an **admitted, normalized derivative** produced on the private side (review round 3, F2):
  - admitted pages (B0 intake)
  - ontology titles and intents, normalized by the `ontology.context` tool in the Lambda before transfer
  - user-typed edit instructions, normalized at API admission into an `adm_decision` of artifact kind `prompt-text` (C4)

  The Runtime never holds the deny-list. The engine's `deps["normalize"]` hook is therefore a **verify-only** check. In the Runtime it is `boundary_check(text)`: the `engine.gate` identifier rules, which raise on a hit, because nothing should remain. A missing hook means the engine returns `blocked: normalization-unavailable` before any model call.
- Every numeric financial value shown on a screen comes from a PRD binding. Literal financial text in a composition is a critical finding.
- The existing `design_loop` modules (`prd.py`, `generate.py`, `loop.py`, `review.py`, `rules.py`, `checklist.py`) keep serving the legacy `design_*` WebSocket path unchanged. New modules sit beside them and never change their behavior.
- Korean UI strings; synthetic public seeds only.

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/check_public_identifiers.py` (repo root) | Fail-closed deny-list scan of tracked files, filenames and the built site |
| `.github/workflows/public-safety.yml` (create) | Root-scoped scan job, a required dependency of Pages deploy |
| `platform/workspace/ontology_ux.py` | Bounded `uxModel` property: validation, reference enumeration, canonical rewrite |
| `platform/workspace/ontology_schema.py` (modify) | Allow `uxModel` on Atom..PageTemplate and Component; add PolicyRule `severity`/`citation`/`extraction` |
| `platform/workspace/ontology_store.py` (modify) | Rewrite `uxModel` references in `publish_candidate`; add `procedure_snapshot` |
| `platform/design_loop/knowledge.py` | Snapshot → `Knowledge` view, with coverage and an alias map |
| `platform/design_loop/derive.py` | Analyzer output → repeated-composition candidates |
| `platform/design_loop/guide_rules.py` | Admitted guideline pages → cited PolicyRule candidates |
| `platform/design_loop/prd_extract.py` | Admitted document pages → cited PRD with exact value spans |
| `platform/design_loop/flow.py` | Ontology procedure + PRD → flow; independent expected graph; cases; traversal; Mermaid |
| `platform/design_loop/route.py` | Coverage classification → generation strategy |
| `platform/design_loop/composition.py` | Composition schema and full validation |
| `platform/design_loop/react_project.py` | Compositions + flow → react-kit project files (App, pages, logic) |
| `platform/design_loop/contract.py` | PRD + flow → assertion-DSL contract for the Browser verifier |
| `platform/design_loop/gui.py` | Strategy-driven generation of layout variants and state variants |
| `platform/design_loop/edit.py` | Targeted edits: full masked-document stability and layout-box comparison |
| `platform/design_loop/coverage.py` | Expected graph × compositions → coverage findings |
| `platform/design_loop/verify_graph.py` | Reviewer/tester/coverage → master; fail-closed verdicts; HITL metrics |
| `platform/design_loop/financial.py` | Shared financial-quantity grammar `QUANTITY` used by E7 and E10 |
| `platform/design_loop/evidence.py` | Trusted assembler joining compiler build and Browser report (mirrors `react_runtime.py:77-84`) |
| `platform/design_loop/convention.py` | Customer convention: screen registry, path/meta rules, handoff layout |
| `platform/design_loop/handoff.py` | Deterministic convention-layout ZIP derived from approved release source bytes |
| `platform/design_loop/benchmark.py` | Golden oracle and success-criteria metrics |
| `platform/design_loop/local_runner.py` | Offline runner for `compile.cjs` / `analyze.cjs`: contained paths only |
| `platform/seed/design_poc/*.json` | Synthetic ontology, admitted-derivative pages, convention, golden set |
| `platform/tests/test_design_*.py` | Tests |

## Shared test fixtures (created in Task E4, reused afterwards)

`platform/tests/design_fixtures.py` exposes the following:

| Name | Role |
|---|---|
| `SEED` | Path to `seed/design_poc` |
| `raw_graph()` | Unsealed seed graph with local ids |
| `sealed_graph(approved=True)` | Every node and edge sealed with `schema.seal`; `reviewState` set to `approved` when requested |
| `knowledge()` | `Knowledge` built from `sealed_graph()` using the snapshot shape |
| `pages()` | Admitted derivative pages |
| `spec()` | The expected PRD spec for the seed |

---

### Task E1: Public identifier guard (fail-closed, root-scoped, publication-bound)

**Files:**
- Create: `scripts/check_public_identifiers.py`
- Create: `.github/workflows/public-safety.yml`
- Create: `platform/tests/test_public_identifiers.py`
- Create: `scripts/public-assets.sha256`: the approved public-asset registry (review round 47, BO1). One `<sha256>  <note>` line per reviewed media file. It is seeded in this task with the media currently published by the guidebook and web app: the `docs` `.png`, `.jpg`, `.mp4` and `.webm` files. It is seeded only after a reviewer has viewed every image and video frame and confirmed that no forbidden identifier is visible. A new media file is published only through a PR that adds its hash, and that review is the content check. Synthetic kit and demo images for data URLs (BL1) use the same registry.
- Modify: `.github/workflows/deploy-docs.yml` (the `build` job runs the scan over the built site and requires the new workflow's job through `needs` in the same workflow; see Step 3)
- Modify: `.gitignore`

**Interfaces:**
- `scan_text(paths, patterns) -> list[(path, line, index)]` returns only the index of each matched pattern, never the matched text.
- `scan_names(paths, patterns) -> list[(path, index)]`
- `display(path, patterns)`: prints a path in CI output only when the path itself matches no pattern. Otherwise it prints `path-sha256:<16 hex>`, so a matched identifier never appears in public logs. Maintainers resolve the hash locally with the same script.
- CLI: `python3 scripts/check_public_identifiers.py --patterns-file F [--tree .] [--site DIR]`.
- Exit codes:
  - **exit 2** when the pattern file or the public-asset registry is missing, empty or unreadable, or when any file, archive or member cannot be scanned completely (fail closed)
  - **exit 1** on any hit
  - **exit 0** when clean
- `--allow-missing-config` exists only for local developer runs. CI never passes it.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd platform && python3 -m pytest tests/test_public_identifiers.py -q`
Expected: FAIL (the script does not exist yet).

- [ ] **Step 3: Implement the script and workflows**

```python
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
    ap.add_argument("--allow-missing-config", action="store_true")
    args = ap.parse_args(argv)
    patterns = _patterns(args.patterns_file) if args.patterns_file else []
    if not patterns:
        print("public identifier deny-list is not configured", file=sys.stderr)
        return 0 if args.allow_missing_config else 2
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
```

`.github/workflows/public-safety.yml`:

```yaml
name: Public safety scan
on:
  pull_request:
  push:
    branches: [main]
  workflow_call:
    secrets:
      PUBLIC_DENYLIST: { required: true }
permissions: { contents: read }
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Tracked files and names
        env: { PUBLIC_DENYLIST: '${{ secrets.PUBLIC_DENYLIST }}' }
        run: |
          umask 077; printf '%s\n' "$PUBLIC_DENYLIST" > "$RUNNER_TEMP/denylist.txt"
          python3 scripts/check_public_identifiers.py --patterns-file "$RUNNER_TEMP/denylist.txt"
```

In `deploy-docs.yml`, make three changes:
1. Add a `safety` job: `uses: ./.github/workflows/public-safety.yml` with `secrets: inherit`.
2. Add `needs: safety` to `build`.
3. After `npm run docs:build`, add a step that writes the deny-list the same way and runs `python3 scripts/check_public_identifiers.py --patterns-file "$RUNNER_TEMP/denylist.txt" --no-tree --site docs/.vitepress/dist`.

When the secret is missing, both jobs exit 2 and Pages does not deploy.

Append to `.gitignore`:

```
# Tenant originals and tenant-only configuration (never public)
/samples/
/.local/
# Bytecode of the root scanner script (the tree scan reads tracked files only; keep it untracked)
/scripts/__pycache__/
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_public_identifiers.py -q` → PASS. From the repo root, `git check-ignore samples/x .local/x` prints both paths.

- [ ] **Step 5: Operator note (live, repository settings).** The repository owner adds the `PUBLIC_DENYLIST` Actions secret and marks the `Public safety scan / scan` check as required on `main`. Record completion in the PR. Do not claim the check is enforced until it is required.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_public_identifiers.py scripts/public-assets.sha256 .github/workflows/public-safety.yml .github/workflows/deploy-docs.yml platform/tests/test_public_identifiers.py .gitignore
git commit -m "chore: fail-closed public identifier scan for tracked files, names and the built site"
```

### Task E2: Remove existing tenant identifiers (including this plan series)

**Files:** Every tracked file the scan reports. Known at planning time:
- `demo/uiux-studio/design-canvas/*`, `demo/uiux-studio/gallery/index.html`, `demo/uiux-studio/scripts/invoke.py`, `demo/uiux-studio/skills/*design-system*/`
- `platform/design_loop/generate.py`, `platform/seed/design/sm_model.json`
- `REQUIREMENTS.md` and `docs/superpowers/plans/*` (untracked today; scanned before the first commit)

- [ ] **Step 1:** Keep the local deny-list outside the repo, at `~/.config/public-denylist.txt`. It holds customer Korean/English names, abbreviations, app names, package scopes and internal hostnames. Never commit it.
- [ ] **Step 2:** Run `python3 scripts/check_public_identifiers.py --patterns-file ~/.config/public-denylist.txt`. Also scan the untracked plan and requirement files with `scan_text` through a one-off `python3 -c`.
- [ ] **Step 3:** Replace each hit:
  - Korean copy becomes `고객사 A`.
  - Identifiers become `bank`.
  - Renamed paths use `git mv`, and every reference is updated (`grep -rln "<old name>" demo platform docs`).
- [ ] **Step 3a: Legacy public publishers** (review round 4, #3). Two retained paths write generated content to the public web bucket served by CloudFront:
  - `api/handlers/design.py:152-169` (`store_run` → `design-runs/`)
  - `studio/worker_handler.py:184` (drafts)

  - **Packaging** (review round 53, BU2). `platform/api/common/public_scan.py` is **not** a second copy of the scanner. `platform/deploy.sh` step 1 (`api-dist` assembly, `deploy.sh:35-38`) gains two copies right after `cp -r api/common …`:
    - `cp ../scripts/check_public_identifiers.py api-dist/common/public_scan_core.py`
    - `cp ../scripts/public-assets.sha256 api-dist/common/public-assets.sha256`

    `public_scan.py` imports `public_scan_core` when it is packaged, and `scripts/check_public_identifiers.py` from the repository in tests. It passes the registry explicitly: `registry=_registry(Path(__file__).with_name("public-assets.sha256"))`. A missing registry therefore raises `ScanIncomplete` and blocks, and never silently allows media. Both `WsFn` and `StudioLoopFn` use this artifact (`infra/lib/stack.ts:395,512`).
    - Test `test_public_scan_artifact` runs the `deploy.sh` assembly commands into a temporary `api-dist`. It imports `common.public_scan` there with the repository **not** on `sys.path`. Checks: a registered inlined PNG publishes, an unregistered one is blocked, and deleting the packaged registry makes publication fail closed. A `check_infra`-style assertion confirms that `deploy.sh` contains both copy lines.
  The publish hooks use `scan_string`, which checks the raw text, the entity-decoded text and parsed attribute values. That catches `A&#67;ME` in HTML kept by `studio/artifacts.py:284` `secure_html`. `api/common/public_scan.py` treats `ScanIncomplete` like a hit, and blocks the write. Tests for both publishers: an identifier placed after more than 20 000 other literals, an over-bound payload → blocked, a JavaScript-escaped identifier in an inline script (BE3), an entity-encoded identifier, adjacent spans (`<span>A</span><span>CME</span>`), attributed spans (`<span>A</span><span class="label">CME</span>`) and `A<b>CM</b>E` → zero publication writes (AZ2, BC2). Both publishers run in existing functions, `WsFn` (`infra/lib/stack.ts:392`) and `StudioLoopFn` (`:509`). This task therefore also adds the env var `PUBLIC_DENYLIST_PARAM` and `ssm:GetParameter` on exactly that parameter ARN to **both** functions, and `check_infra` asserts it (review round 21, AO2). Tests cover three cases for each publisher: clean synthetic output publishes, a missing configuration blocks, and an identifier hit blocks. Before each put, call `scan_string(text, patterns) -> [pattern index]`, a new **content-based** function in `scripts/check_public_identifiers.py` that `scan_text` also uses internally. `scan_text` is path-based, so publication content cannot go through it (review round 5, #3). The function is packaged as `api/common/public_scan.py` and runs over the text to be published, with the deny-list from the deployment's private parameter. A hit, or a missing deny-list, blocks the publish with an explicit error. Add tests for both writers.
  - **Static-only public HTML** (review round 43, BE3/BK1/BK2). Runtime behaviour cannot be scanned to completeness: interaction handlers, timers and computed text can always be deferred past any snapshot. The legacy publishers' outputs are static drafts (labelled "기존 데모 경로 (정적 시안)" in C6), so before scanning and publishing, `api/common/public_scan.sanitize_static(html)` **removes** executable and generated-content vectors:
    - `<script>`, `<noscript>` and `<template>` elements
    - `on*` event-handler attributes
    - `javascript:` URLs, and **every** `data:` URL except as follows (review round 44, BL1). Every URL-bearing attribute and CSS `url()` value is first normalized with the scanner's `_normalize_url` and classified with its `_data_url` (the WHATWG rules: tabs and newlines removed, C0/space trimmed, MIME type trimmed and case-insensitive). The sanitizer and the scanner therefore see the same scheme and MIME type as the browser, so `da&#9;ta:` or ` DATA: image/SVG+xml` cannot slip past either (review round 49, BQ1). A value that does not decode is removed.
      - A `data:image/svg+xml` value is decoded (base64 or percent-encoding) and treated as nested markup. It is sanitized with the same rules and scanned with the same views, recursively to depth 3 (`MAX_NESTING`), and any hit blocks. A resource nested deeper than that limit is **removed** by the sanitizer, and the scanner raises `ScanIncomplete` if one remains, so over-depth content never passes (review round 54, BV1).
      - A raster `data:image/png|jpeg|webp|gif` value is kept **only** when its decoded bytes' sha256 is in the approved public-asset registry, meaning the synthetic kit and demo assets whose content was reviewed. Otherwise it is removed, because pixel text cannot be scanned.
      - Other `data:` URLs are removed.
    - `<iframe>`/`<object>`/`<embed>`
    - CSS `content:` declarations with a non-empty value, and `@import`

    The result is inert HTML whose visible text is fully determined by its markup. `scan_string` then checks it with the raw, entity-decoded, inline-reconstructed, attribute and **CSS-unescaped** views. CSS escapes such as `\41 CME` are decoded by `_css_unescape` for `<style>` and `style` attributes. The sanitized HTML, not the original, is what gets published. There is no browser rendering, so no Playwright or resource manifest is needed at publication time.
  - **Built sites** (Pages and the web app) are compiled only from tracked, reviewed repository sources, which the repository scan covers, plus pinned dependencies. Their built assets are scanned statically, with the HTML and `.js/.mjs/.cjs` views including JS string-escape decoding. Runtime-constructed text in the platform's own code is a code-review matter and is not a model-generated input, so no rendering is required and the Pages job needs no extra dependency.
  - Tests at both legacy publishers:
    - a draft with `<script>` assigning `"A\u0043".concat("ME")`, an `onclick` using `String.fromCharCode(65,67,77,69)`, and a CSS `::before{content:"\41 CME"}` → published **without** those vectors, and nothing forbidden is visible
    - a draft with plain `ACME` text → blocked
    - a clean static draft → published unchanged except for removed vectors
    - `scan_string` detects `\41 CME` in a `<style>` block through the CSS view
    - SVG data URLs whose header has an inserted TAB, LF, CR or leading space (`da&#9;ta:`), upper-case or whitespace-padded MIME, or `;charset=utf-8`, carrying base64-encoded `ACME` → blocked by the sanitizer and by `scan_string` and `--site` (review round 49, BQ1)
    - SVG data URLs nested exactly `MAX_NESTING` deep with encoded `ACME` → hit; one level deeper → `ScanIncomplete` (exit 2) through `scan_string`, `scan_text` and `--site`, and the sanitizer removes the over-depth resource so the publisher writes nothing unscanned (review round 54, BV1)
    - identical PNG bytes labelled `image/png`, `application/octet-stream`, `text/plain`, no MIME or an unknown image type → unreviewed media in HTML, CSS, JS and `--site`; a registered hash passes; an unrecognized binary body → `ScanIncomplete`; text bodies (`data:;base64,…`, `data:text/html;…`) are scanned as text (review round 53, BU1)
    - the assembled Lambda artifact (`api-dist/common/public_scan.py` with `public_scan_core.py` and `public-assets.sha256`, without repository mounts): registered media publishes, unregistered media is blocked, and a missing registry fails closed (BU2)
    - an unregistered small PNG imported by source code and inlined by the real Vite build into the JS bundle → `--site` exit 1 ("unreviewed media"); after its hash is reviewed into the registry → exit 0. Inlined `data:image/png` URLs in HTML, CSS, JS and escaped JS literals are held to the same registry, as are media data URLs in tracked sources, which covers source media before bundling (review round 52, BT1)
    - CSS whose function name and scheme are both escaped (`\75rl(\64 ata:…)`, `u\72 l(`, a quoted `\75 rl("\64 ata:…")`) with base64-encoded `ACME` → blocked in inline styles, tracked CSS and `--site`; ordinary resources and descriptive text are unchanged controls (review round 51, BS1)
    - a JS bundle whose `\x64ata:`/`\u0064ata:` SVG literal follows a quote inside a comment, a regex literal or a template expression (`/* " */`, `/"/`) → blocked through `scan_string`, an inline `<script>` and `--site` (review round 50, BR1)
    - fixtures encode the forbidden text (`%41%43%4D%45` or base64) so a raw match cannot mask a decoding miss; a percent-encoded SVG with `xmlns='…'` and `viewBox='0 0 10 10'` (quotes and spaces) in a JS bundle literal, including a minified template literal, → blocked (BP1)
    - a percent-encoded SVG with `xmlns='…'` and `ACME` text, in `src`, a `style` `url("…")`, a stylesheet and a JS bundle string → blocked, in both `scan_string` and `--site` modes; `srcset` with an SVG data URL → `ScanIncomplete` (review round 48, BP1)
    - tree mode: tracked `.vue`, `.sh`, `.jsonl`, extensionless and unknown-extension files containing `ACME` → exit 1; an undecodable unknown tracked file → exit 2 (BO1)
    - a published ZIP with a text member or member name containing `ACME` → exit 1; a nested, corrupt or undecodable-member ZIP → exit 2; an unregistered `.png` → exit 1 (BO1)
    - the existing guidebook and web-app build outputs, with their `.mp4`/`.webm`/`.jpg`/`.png` media, `.vtt` captions and source/dist ZIPs, scan with exit 0 once the registry is seeded (BO1)
    - `.htm`, `.HTML`, `.CSS` and unknown-extension text files containing `ACME`, both as tracked files and as copied `web/public` assets → exit 1 in the tree and `--site` modes (review round 47, BO1)
    - `content:"Acme " /* "separator" */ "Bank"` → blocked; a `title` attribute, CSS comment or CSS content string that merely mentions `data:image/svg` → clean, while `src`, `url()` and `image-set` data URLs stay strict (round 47, BM1/BN1)
    - a CSS `content:"\41" "CME"` (adjacent strings), in a `<style>` block, a tracked stylesheet and a copied `web/public` stylesheet → blocked by `scan_string`, the repository scan and the `--site` scan (review round 45, BM1)
    - `data:image/svg+xml;charset=utf-8;base64,…`, upper-case MIME and percent-encoded SVGs containing `ACME` → blocked at both legacy publishers after `secure_html` (`studio/artifacts.py:55,284`); an unparseable `data:image/svg` form or an undecodable payload → `ScanIncomplete`, which blocks (BL1)
    - a base64 `data:image/svg+xml` containing `<text>ACME</text>` → blocked, with zero publication writes. An unregistered raster data image is removed, and a registered one is kept (BL1)
- [ ] **Step 4:** Re-run the scan → exit 0. Run `cd platform && python3 -m pytest tests/test_design_loop.py tests/test_design_handler.py -q` → PASS. Only prompt text changed; the legacy path is unaffected.
- [ ] **Step 5: Commit** `git commit -m "chore: replace tenant identifiers with neutral aliases"`

### Task E3: `uxModel` property, PolicyRule citation fields and canonical rewrite (O-01, O-02, O-04)

**Files:**
- Create: `platform/workspace/ontology_ux.py`
- Modify: `platform/workspace/ontology_schema.py:29-46` (properties), `:236-237` (text loop exclusions), and add validation before the content-hash check
- Modify: `platform/workspace/ontology_store.py:272-279` (`publish_candidate` property rewrite)
- Modify: `platform/workspace/ONTOLOGY_CONTRACT.md`, `platform/workspace/AGENTCORE_CONTRACT.md` `platform-ontology/1` property table
- Test: `platform/tests/test_ontology_ux.py`

**Interfaces:**
- `uxModel` is allowed on `Atom`, `Molecule`, `Organism`, `Pattern`, `PageTemplate` and `Component`.
  - It is also allowed on `Foundation`, restricted to `layers` and `changeReason` (review round 3, #22). This way every asset level, Foundation included, carries the three layers O-01 requires:
    - `layers.code` holds the token import, `{importPath, exportName, props: {}}`
    - `layers.gui` holds the rendered token swatch or icon snapshot
    - `layers.wireframe` holds the block id
  - Foundation keeps its `token`/`name` properties as the value source.
  - Screen and Procedure carry no `uxModel`: screen code is *generated*, and procedure semantics live in `NEXT` edges.
  - This corrects the old coverage map, which claimed Foundation `uxModel` (Codex #22).
- `uxModel` keys (closed):

| Key | Allowed on | Contents |
|---|---|---|
| `intent` | — | str ≤ 500 |
| `conditions` | — | ≤ 50 entries of `{id, when, effect: include\|exclude\|state, target, state?}`. `target` is a node id. `state` is required iff `effect == "state"`. The `when` grammar is `term (" & " term)*`, where `term = "!"? "cond:" id`. |
| `dataBindings` | — | ≤ 50 entries of `{field, source: product\|session\|static, path, required: bool}` |
| `requiredStates` | — | Subset of `STATES` |
| `stateProps` | — | `{state: {prop: literal}}` for each required state, e.g. `{"error": {"tone": "danger"}, "empty": {"children": "조회된 내역이 없습니다"}}`. In `fill` mode, `generate_states` applies them deterministically, so every error, empty, loading or zero/many state has a concrete, validated composition (review round 4, #22). A required state without `stateProps` or an `effect: state` condition → `state-undefined`, major |
| `slots` | PageTemplate only | `{slot: {required: bool, allowed: [nodeId]}}`. `allowed` may not be empty; an empty allow-list is an error, not "unrestricted". |
| `layers.wireframe` | — | `{blocks: [id]}` |
| `layers.gui` | — | `{snapshotHash: sha256, width, height}`. Browser evidence of the rendered component. |
| `layers.code` | — | `{importPath, exportName, props: {name: {type, required, values?, item?}}, childrenProp?: "children"\|null, adapter?}`. Prop types:<br>- `string`, `number`, `boolean`, `enum`<br>- `node`: a ReactNode slot<br>- `list`: an array of objects whose `item` is `{field: "string"\|"number"}`, for example `Summary.items` `{label, value}` and `Stepper.steps` `{id, label}`<br>- `callback`: never set by a composition. It is filled only by the trusted `adapter`: `controlled-text` (Input: `value`/`onChange`), `controlled-bool` (Checkbox), `controlled-choice` (Select/RadioGroup) or `action` (Button `onClick`). See review round 2, N13. |
| `changeReason` | — | str ≤ 500 |

- PolicyRule properties gain:
  - `severity`: `critical`, `major` or `minor`
  - `citation`: `{sourceKind, page, quote, derivativeHash}`
  - `extraction`: `{method: "model", model, promptVersion, admissionId}`
  - `appliesWhen`: an optional `when`-grammar expression. When it is absent, the rule applies to every case. It makes rule applicability explicit, so the flow expectation never requires an eligibility-only rule on the other branch (review round 2, N14).

  These keep model provenance even though the node's `provenance` field is set by the publishing producer (Codex #7).
- Functions:
  - `ontology_ux.validate_ux_model(value, node_type) -> dict`
  - `ontology_ux.references(value) -> list[str]`: every node id referenced by conditions and slots
  - `ontology_ux.rewrite(value, identities: dict[str, str]) -> dict`: maps referenced ids through `identities`, leaving unknown ids unchanged
- `publish_candidate` applies `rewrite` to `properties["uxModel"]` with the same `identities` map used for `usageIds`/`slots` (`ontology_store.py:275-279`).

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_ontology_ux.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ontology_schema import node  # noqa: E402
from workspace import ontology_schema as schema  # noqa: E402
from workspace.ontology_ux import references, rewrite, validate_ux_model  # noqa: E402

CODE = {"importPath": "@studio/approved-ui", "exportName": "Text",
        "props": {"tone": {"type": "enum", "required": False, "values": ["default", "muted"]}},
        "childrenProp": "children"}


def test_valid_models_per_level():
    assert validate_ux_model({"intent": "금리 강조", "layers": {"code": CODE}}, "Atom")
    assert validate_ux_model({"layers": {"code": {**CODE, "exportName": "Panel"}}}, "Pattern")
    tpl = {"slots": {"body": {"required": True, "allowed": ["org-1"]}},
           "layers": {"code": {"importPath": "@studio/approved-ui", "exportName": "Screen",
                               "props": {"body": {"type": "node", "required": True}}, "childrenProp": None}}}
    assert validate_ux_model(tpl, "PageTemplate")


@pytest.mark.parametrize("value,kind", [
    ({"requiredStates": ["sleepy"]}, "Organism"),
    ({"slots": {"body": {"required": True, "allowed": ["a"]}}}, "Organism"),
    ({"slots": {"body": {"required": True, "allowed": []}}}, "PageTemplate"),
    ({"conditions": [{"id": "c1", "when": "x", "effect": "include", "target": "t"}]}, "Organism"),
    ({"conditions": [{"id": "c1", "when": "cond:a", "effect": "state", "target": "t"}]}, "Organism"),
    ({"layers": {"code": {**CODE, "props": {"tone": {"type": "enum", "required": False}}}}}, "Atom"),
    ({"layers": {"gui": {"snapshotHash": "x", "width": 1, "height": 1}}}, "Atom"),
])
def test_invalid_models_rejected(value, kind):
    with pytest.raises(ValueError):
        validate_ux_model(value, kind)


def test_ux_model_not_allowed_on_foundation_screen_or_procedure():
    for kind in ("Screen", "Procedure"):
        with pytest.raises(ValueError):
            schema.validate_node(node("n1", kind=kind, properties={"uxModel": {"intent": "x"}}))
    with pytest.raises(ValueError):   # Foundation: layers/changeReason only
        schema.validate_node(node("f1", kind="Foundation", subtype="color", properties={"uxModel": {"intent": "x"}}))
    ok = {"layers": {"wireframe": {"blocks": ["swatch"]}}}
    assert schema.validate_node(node("f2", kind="Foundation", subtype="color", properties={"uxModel": ok}))


def test_references_and_rewrite():
    value = {"conditions": [{"id": "c1", "when": "cond:auto", "effect": "include", "target": "branch"}],
             "slots": {"body": {"required": True, "allowed": ["summary", "branch"]}}}
    assert sorted(references(value)) == ["branch", "branch", "summary"]
    out = rewrite(value, {"branch": "node-b", "summary": "node-s"})
    assert out["conditions"][0]["target"] == "node-b" and out["slots"]["body"]["allowed"] == ["node-s", "node-b"]


def test_policy_rule_citation_fields():
    rule = node("r1", kind="PolicyRule", properties={
        "ruleId": "r1", "statement": "약관 동의 단계 필수", "required": True, "severity": "critical",
        "citation": {"sourceKind": "document-revision", "page": 3, "quote": "약관 동의", "derivativeHash": "a" * 64},
        "extraction": {"method": "model", "model": "m", "promptVersion": "1", "admissionId": "adm-1"}})
    assert schema.validate_node(rule)["properties"]["severity"] == "critical"
    bad = node("r2", kind="PolicyRule", properties={"ruleId": "r2", "severity": "urgent"})
    with pytest.raises(ValueError):
        schema.validate_node(bad)
```

Add a store-level test to `platform/tests/test_ontology_store.py`, using its existing `candidate()`/`publish()` helpers (L18-26). Publish a PageTemplate whose `uxModel.slots.body.allowed == ["org-local"]` together with a node `org-local`, then assert that the stored template's `allowed` entry equals the canonical id returned in `identities["org-local"]`.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_ontology_ux.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `ontology_ux.py`**

```python
"""Bounded UX SM model property for design ontology nodes (REQUIREMENTS O-01; platform-ontology/1)."""
from __future__ import annotations

import copy
import re

STATES = frozenset({"default", "empty", "error", "ineligible", "loading", "done", "zero", "many"})
PROP_TYPES = frozenset({"string", "number", "boolean", "node", "enum", "list", "callback"})
ADAPTERS = frozenset({"controlled-text", "controlled-bool", "controlled-choice", "action"})
_ITEM_TYPES = frozenset({"string", "number"})
SOURCES = frozenset({"product", "session", "static"})
EFFECTS = frozenset({"include", "exclude", "state"})
KEYS = frozenset({"intent", "conditions", "dataBindings", "requiredStates", "stateProps", "slots", "layers", "changeReason"})
LEVELS = frozenset({"Atom", "Molecule", "Organism", "Pattern", "PageTemplate", "Component", "Foundation"})
FOUNDATION_KEYS = frozenset({"layers", "changeReason"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_PROP = re.compile(r"[a-z][A-Za-z0-9]{0,39}\Z")
_IMPORT = re.compile(r"(?:@[a-z0-9-]+/)?[a-z0-9][a-z0-9._/-]{0,199}\Z")
_EXPORT = re.compile(r"[A-Z][A-Za-z0-9]{0,79}\Z")
_TERM = re.compile(r"!?cond:[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
# The fixed binding catalog of prd_extract.bindings (E7); anything else is not a structural symbol.
# Dynamic segments are server-assigned ordinals (pref-1, notice-1), never user or model names (review round 13, AF1).
BINDING_PATH = re.compile(r"(?:product\.(?:productName|productType|category|eligibility|term|baseRate|summaryItems"
                          r"|preferential\.pref-[1-9][0-9]{0,2}\.(?:condition|rate)|notice\.notice-[1-9][0-9]{0,2})"
                          r"|session\.(?:amount|agreed|branch|preference))\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _fail(message):
    raise ValueError(f"Invalid uxModel: {message}")


def _text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(name)
    return value


def _list(value, limit, name):
    if not isinstance(value, list) or len(value) > limit:
        _fail(name)
    return value


def _id(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail(name)
    return value


def _exact(value, required, optional=(), name="object"):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        _fail(name)
    return value


def parse_when(expr):
    terms = [t.strip() for t in _text(expr, 300, "when").split("&")]
    if not terms or any(not _TERM.fullmatch(t) for t in terms):
        _fail("when grammar")
    return [(t.startswith("!"), t.lstrip("!")[5:]) for t in terms]


def _code(code):
    _exact(code, {"importPath", "exportName", "props"}, {"childrenProp", "adapter"}, "code layer")
    if "adapter" in code and code["adapter"] not in ADAPTERS:
        _fail("adapter")
    if not isinstance(code["importPath"], str) or not _IMPORT.fullmatch(code["importPath"]):
        _fail("importPath")
    if not isinstance(code["exportName"], str) or not _EXPORT.fullmatch(code["exportName"]):
        _fail("exportName")
    if code.get("childrenProp") not in (None, "children"):
        _fail("childrenProp")
    props = code["props"]
    if not isinstance(props, dict) or len(props) > 40:
        _fail("props")
    for name, spec in props.items():
        if not _PROP.fullmatch(name):
            _fail("prop name")
        _exact(spec, {"type", "required"}, {"values", "item"}, "prop spec")
        if spec["type"] not in PROP_TYPES or type(spec["required"]) is not bool:
            _fail("prop type")
        if spec["type"] == "list":
            item = spec.get("item")
            if (not isinstance(item, dict) or not 1 <= len(item) <= 8
                    or any(not _PROP.fullmatch(k) or v not in _ITEM_TYPES for k, v in item.items())):
                _fail("list item shape")
        elif "item" in spec:
            _fail("item on non-list")
        if spec["type"] == "callback" and not code.get("adapter"):
            _fail("callback props require a trusted adapter")
        if spec["type"] == "enum":
            values = _list(spec.get("values"), 30, "enum values")
            if not values or any(not isinstance(v, str) or not v or len(v) > 80 for v in values):
                _fail("enum values")
        elif "values" in spec:
            _fail("values on non-enum")


def validate_ux_model(value, node_type):
    if node_type not in LEVELS:
        _fail("uxModel on a non-design level")
    if not isinstance(value, dict) or value.keys() - KEYS:
        _fail("fields")
    if node_type == "Foundation" and value.keys() - FOUNDATION_KEYS:
        _fail("Foundation uxModel allows layers and changeReason only")
    for key in ("intent", "changeReason"):
        if key in value:
            _text(value[key], 500, key)
    for c in _list(value.get("conditions", []), 50, "conditions"):
        _exact(c, {"id", "when", "effect", "target"}, {"state"}, "condition")
        _id(c["id"], "condition id"), parse_when(c["when"]), _id(c["target"], "target")
        if c["effect"] not in EFFECTS or (c["effect"] == "state") != ("state" in c):
            _fail("effect")
        if "state" in c and c["state"] not in STATES:
            _fail("condition state")
    for b in _list(value.get("dataBindings", []), 50, "dataBindings"):
        _exact(b, {"field", "source", "path", "required"}, name="binding")
        _id(b["field"], "field"), _text(b["path"], 200, "path")
        if not BINDING_PATH.fullmatch(b["path"]):          # trusted binding catalog only (review round 12, AF1)
            _fail("binding path outside the trusted catalog")
        if b["source"] not in SOURCES or type(b["required"]) is not bool:
            _fail("binding source")
    states = _list(value.get("requiredStates", []), len(STATES), "requiredStates")
    if set(states) - STATES or len(set(states)) != len(states):
        _fail("requiredStates")
    state_props = value.get("stateProps", {})          # review round 5, Y2
    if not isinstance(state_props, dict) or set(state_props) - set(states) or len(state_props) > len(STATES):
        _fail("stateProps keys must be declared requiredStates")
    for state, props in state_props.items():
        if not isinstance(props, dict) or not 1 <= len(props) <= 20:
            _fail("stateProps")
        for name, literal in props.items():
            if not _PROP.fullmatch(name) or not isinstance(literal, (str, int, bool)) or isinstance(literal, str) and len(literal) > 500:
                _fail("stateProps value")
    slots = value.get("slots")
    if slots is not None:
        if node_type != "PageTemplate" or not isinstance(slots, dict) or not 1 <= len(slots) <= 12:
            _fail("slots")
        for name, slot in slots.items():
            _id(name, "slot")
            _exact(slot, {"required", "allowed"}, name="slot spec")
            if type(slot["required"]) is not bool:
                _fail("slot required")
            allowed = _list(slot["allowed"], 100, "allowed")
            if not allowed:
                _fail("empty allow-list")
            for item in allowed:
                _id(item, "allowed id")
    layers = value.get("layers", {})
    if not isinstance(layers, dict) or layers.keys() - {"wireframe", "gui", "code"}:
        _fail("layers")
    if "wireframe" in layers:
        _exact(layers["wireframe"], {"blocks"}, name="wireframe")
        for block in _list(layers["wireframe"]["blocks"], 50, "blocks"):
            _id(block, "block")
    if "gui" in layers:
        gui = _exact(layers["gui"], {"snapshotHash", "width", "height"}, name="gui")
        if not isinstance(gui["snapshotHash"], str) or not _HASH.fullmatch(gui["snapshotHash"]):
            _fail("gui snapshot")
        if any(type(gui[k]) is not int or not 1 <= gui[k] <= 4096 for k in ("width", "height")):
            _fail("gui size")
    if "code" in layers:
        _code(layers["code"])
        if node_type == "PageTemplate" and slots:
            props = layers["code"]["props"]
            if any(props.get(name, {}).get("type") != "node" for name in slots):
                _fail("template slots must be node props")
    return value


def references(value):
    refs = [c["target"] for c in value.get("conditions", [])]
    for slot in (value.get("slots") or {}).values():
        refs += slot["allowed"]
    return refs


def rewrite(value, identities):
    out = copy.deepcopy(value)
    for c in out.get("conditions", []):
        c["target"] = identities.get(c["target"], c["target"])
    for slot in (out.get("slots") or {}).values():
        slot["allowed"] = [identities.get(i, i) for i in slot["allowed"]]
    return out
```

- [ ] **Step 4: Hook into `ontology_schema.py`**

```python
DESIGN_PROPERTIES = {"componentName", "packageName", "sourcePath", "description",
                     "slots", "usageIds", "legacyType", "legacyId", "pageId", "procedureId", "uxModel"}
...
    "PolicyRule": {"ruleId", "statement", "required", "guidelineId", "severity", "citation", "extraction", "appliesWhen"},
```

Add `"uxModel"` to `PROPERTIES["Foundation"]` as well; `validate_ux_model` restricts it to `layers`/`changeReason`. Screen and Procedure must reject `uxModel`. They share `DESIGN_PROPERTIES` today through `LEVELS[1:]`, so after the dict is built, set:

```python
PROPERTIES["Screen"] = PROPERTIES["Screen"] - {"uxModel"}
PROPERTIES["Procedure"] = PROPERTIES["Procedure"] - {"uxModel"}
```

In `validate_node`, before `expected = {...}`:

```python
    if "uxModel" in properties:
        from workspace.ontology_ux import validate_ux_model
        validate_ux_model(properties["uxModel"], value["type"])
    if value["type"] == "PolicyRule":
        if "severity" in properties and properties["severity"] not in {"critical", "major", "minor"}:
            raise ValueError("Invalid policy severity")
        if "citation" in properties:
            _fields(properties["citation"], {"sourceKind", "page", "quote", "derivativeHash"}, {"region", "normalizedImageHash"})
            if "region" in properties["citation"]:      # diagram transcription lineage (review round 9, AC2)
                region = properties["citation"]["region"]
                _fields(region, {"left", "top", "width", "height"})
                if any(type(region[k]) is not int or not 0 <= region[k] <= 8192 for k in region) or not region["width"] or not region["height"]:
                    raise ValueError("Invalid citation region")
                _hash(properties["citation"]["normalizedImageHash"])
            if properties["citation"]["sourceKind"] not in {"document-revision", "product-guideline"}:
                raise ValueError("Policy citations require a business source")
            if type(properties["citation"]["page"]) is not int or properties["citation"]["page"] < 1:
                raise ValueError("Invalid citation page")
            _text(properties["citation"]["quote"], 2000)
            _hash(properties["citation"]["derivativeHash"])
        if "appliesWhen" in properties:
            from workspace.ontology_ux import parse_when
            parse_when(properties["appliesWhen"])
        if "extraction" in properties:
            _fields(properties["extraction"], {"method", "model", "promptVersion", "admissionId"})
            if properties["extraction"]["method"] != "model":
                raise ValueError("Invalid extraction method")
            for key in ("model", "promptVersion", "admissionId"):
                _text(properties["extraction"][key], 200)
```

Exclude the structured keys from the generic text loop:

```python
    for key in properties.keys() - hashes - {"bytes", "width", "height", "usageIds", "slots", "required",
                                             "usageBindings", "uxModel", "citation", "extraction"}:
```

- [ ] **Step 4a: Serialization depth (review round 4, X1).** `ontology_schema._json_value` limits nesting to 8 (`ontology_schema.py:51`). A graph plus publication envelope wrapping `properties.uxModel.layers.code.props.<name>.values` exceeds it, because the envelope levels are graph → nodes → node → properties → uxModel → layers → code → props → prop → values.
  - Raise the limit to 14 in `_json_value`.
  - Record it in `ONTOLOGY_CONTRACT.md` as a v1 compatible change: existing hashes are unchanged because canonical bytes do not depend on the limit.
  - Add a test that publishes the **complete seed** through `publish_candidate` and runs `validate_graph` on the stored partition.
  - The ontology maintainer co-reviews the change.

- [ ] **Step 5: Rewrite references in `publish_candidate`** (`ontology_store.py`, after the `fileId` rewrite at L278-279)

```python
            if "uxModel" in properties:
                from workspace.ontology_ux import rewrite
                properties["uxModel"] = rewrite(properties["uxModel"], identities)
```

- [ ] **Step 6: Update the contracts.**
  - `ONTOLOGY_CONTRACT.md`: add a "uxModel property (v1)" section with the key table above, the rewrite rule and the Screen/Procedure/Foundation exclusions.
  - `AGENTCORE_CONTRACT.md` `platform-ontology/1`: add a one-line pointer to that section.
- [ ] **Step 7: Run** `python3 -m pytest tests/test_ontology_ux.py tests/test_ontology_schema.py tests/test_ontology_store.py -q` → PASS.
- [ ] **Step 8: Commit** `git commit -m "feat(ontology): bounded uxModel, cited PolicyRule fields and canonical reference rewrite"`

### Task E4: Procedure snapshot reader and `Knowledge` view (Codex #4, #5)

**Files:**
- Modify: `platform/workspace/ontology_store.py` (add `procedure_snapshot`)
- Create: `platform/design_loop/knowledge.py`
- Create: `platform/seed/design_poc/ontology.json`, `platform/tests/design_fixtures.py`
- Test: `platform/tests/test_design_knowledge.py`, and one case in `platform/tests/test_ontology_store.py`

**Interfaces:**
- `Ontology.procedure_snapshot(procedure_id, *, max_screens=20, max_nodes=300, max_edges=1000) -> dict` works in three steps:
  1. It reads the Procedure.
  2. It collects member Screens through `PART_OF` edges whose `dst` is the procedure (adjacency index), plus `NEXT` edges whose endpoints are both members.
  3. It calls `self.closure(screen_ids, direction="dependencies", max_nodes=…, max_edges=…)` for the design dependencies (`COMPOSES`/`USES`/`GOVERNED_BY`).

  It returns `{schemaVersion, projectId, generation, nodes, edges, coverage}`:
  - `coverage.unknown` is the union of the closure's unknown reasons and its own.
  - Its own reasons are `too-many-screens`, `unmapped-or-inaccessible` and `stale-endpoint-revisions`.
  - `coverage.truncated` is true when any limit is hit.

  It applies the same visibility and `_recheck` rules as `closure`. Tombstoned, rejected and deprecated nodes and edges are excluded.
  - **`uxModel` references** (review round 11, AE1). Slot `allowed` alternatives and condition targets are properties, not edges, so `closure` never follows them. After the closure, `procedure_snapshot` therefore:
    1. collects `ontology_ux.references` from every snapshot node that has a `uxModel`
    2. reads the missing ids with `self.read(ids)` in batches of ≤ 50, applying visibility and exact current revisions
    3. runs `closure(batch, direction="dependencies", max_nodes=remaining_nodes, max_edges=remaining_edges)` in **batches of ≤ 20 seeds**, which is the real limit (`ontology_store.py:557-559`; review round 12, AF2). Every batch keeps the same current generation, and the aggregate node and edge budgets are shared. The test uses 21 or more property-only alternatives.
    4. repeats until no new ids appear, bounded by `max_nodes` and at most 4 rounds

    An unreadable reference → `unmapped-or-inaccessible`, and hitting a limit → `truncated`, both blocking. Test: an approved alternate asset that is only in `slots.body.allowed`, with no Screen composing it, is present in the snapshot and `Knowledge.complete` is true.
- `knowledge.from_snapshot(snapshot, *, include_candidates=False) -> Knowledge`. `Knowledge` has these fields:
  - `generation`
  - `coverage`
  - `assets`: renderable levels → `{id, level, title, intent, code, requiredStates, conditions, bindings, composes: [id]}`
  - `templates`: `{id, title, slots: {name: {required, allowed}}, code}`
  - `screens`: `{id, title, pageId, templateId, assets: [id], procedureId}`
  - `procedures`: `{id, title, screens: [id], transitions: [{id, src, dst, conditionId, condition}]}`
  - `rules`: `{id, ruleId, statement, required, severity, targets: [id], citation, appliesWhen}`
  - `tokens`
  - `aliases`: local id → canonical id, from the `partition-local` alias
  - `unresolved`: `[{nodeId, reference}]` for every `uxModel` reference that is not in the snapshot
- Edge semantics follow `AGENTCORE_CONTRACT.md:44-48`:

| Edge | Meaning here |
|---|---|
| `Screen COMPOSES PageTemplate` | The screen uses the template |
| `Screen COMPOSES Organism\|Molecule\|Atom` | Screen assets |
| `X COMPOSES Y` | Asset composition |
| `Screen PART_OF Procedure` | Procedure membership |
| `Screen NEXT Screen` | Transition; the `properties.conditionId`/`condition` string uses the `when` grammar |
| `Screen\|asset GOVERNED_BY PolicyRule` | Rule target |

  `IMPLEMENTS` is not used, because it is reserved for code→design.
- **Edge eligibility (review round 2, N11).** The ontology has no edge review step: publishing makes edges `candidate`, and `review_node` reviews nodes only (`ontology_store.py:776-790`). Edge usability therefore uses the same rule as the store's own closure (`ontology_store.py:607-612`). An edge is usable when all of these hold:
  - it is not tombstoned
  - its `reviewState ∉ {rejected, deprecated}`
  - both endpoint nodes are live (approved, or reviewed/candidate with `include_candidates`) at the exact revisions the edge names

  This rule is written into `ONTOLOGY_CONTRACT.md` in this task, as "design views accept unreviewed edges between approved nodes; rejected/deprecated edges are excluded".
- `Knowledge.complete` also blocks on `retired-or-rejected-mapping`: a required member that was rejected or retired must not silently disappear.
- When a usable-looking edge references an endpoint node that is excluded as not live, for example a candidate child of an approved organism, `from_snapshot` appends `unapproved-dependency` to `coverage.unknown` with the node id. That reason is **blocking**, so a silently removed dependency can never yield `complete=True` (review round 3, #5 and N11).
- Build order:
  1. nodes
  2. `PART_OF` membership
  3. `COMPOSES`/`GOVERNED_BY`
  4. `NEXT` transitions, only between known member screens

  Membership is therefore always known before transitions are processed.
- **Entry and navigation kinds** (review round 11, AE2). The contract permits loops and back navigation with retained state (`AGENTCORE_CONTRACT.md:47`), so neither "no incoming edge" nor "no revisit" defines the flow. This task amends the schema:
  - The `Procedure` properties gain `entryScreenId`, rewritten through `identities` on publication, like `slots`.
  - The `NEXT` edge properties gain `navigation: "forward"|"back"|"cancel"` (default `forward`). This is added to the closed edge property set at `ontology_schema.py:275-281`.
  - The entry is `entryScreenId` when present. Otherwise it is the unique member with no incoming **forward** edge; zero or several → `ambiguous-entry`.
  - `back`/`cancel` edges are **user actions**. E11 emits them as secondary buttons (`on.click: <transitionId>`, labelled 이전/취소), and they carry `retains` into `App.tsx`'s form-state handling.
  - `flow.traverse` follows only forward edges, so ambiguity and loop rejection apply only to forward routing.
  - E13 adds one rule per back edge: click back, expect the previous page, and expect the retained field values.
  - Tests: `entry → form → done` plus `form → entry (back)` is complete, and a forward → back → forward → terminal journey passes through generated controls. A forward-only cycle is still `loop`.
- Screen order is a topological walk over **forward** edges from the entry screen. It follows unconditional edges first, then conditional ones, in `edge.id` order; unreached members are appended. More than one entry screen → `Knowledge.coverage.unknown += ["ambiguous-entry"]`.
- `Knowledge.complete` is `not coverage.truncated and not unresolved and "ambiguous-entry" not in coverage.unknown`. Generation (C) refuses an incomplete knowledge view with `knowledge-incomplete`.

- [ ] **Step 1: Create the synthetic seed** (`seed/design_poc/ontology.json`). It is synthetic and public, uses the platform kit `@studio/approved-ui` for all code layers, and contains:
  - **Foundations:** `color-brand` (subtype color, token `#1d4ed8`) and `space-m` (subtype spacing).
  - **Atoms**, each with a `layers.code` for its kit component:
    - `title` → `Text` (as h1), with `childrenProp: "children"`
    - `body-text` → `Text`
    - `next-button` → `Button`, with `label` string required
    - `amount-input` → `Input`, with `label` required, `value` bound and `onChange` handled by codegen
    - `consent-check` → `Checkbox`
    - `notice-alert` → `Alert`, with `message` required
    - `rate-summary` → `Summary`, with `items` bound
    - `step-bar` → `Stepper`
  - **Molecules:**
    - `amount-field`: COMPOSES `amount-input` + `body-text`, code `Stack`
    - `consent-row`: COMPOSES `consent-check` + `body-text`, code `Stack`
  - **Organisms:**
    - `product-summary`: code `Panel`; COMPOSES `rate-summary`, `body-text`
    - `eligibility-check`: code `Panel`; `requiredStates: ["default", "ineligible"]`; condition `{id: "c-inel", when: "!cond:eligible", effect: "state", target: "eligibility-check", state: "ineligible"}`
    - `terms-consent`: code `Panel`; COMPOSES `consent-row`
    - `auto-transfer`: code `Panel`; condition `{id: "c-branch", when: "cond:autoTransfer", effect: "include", target: "branch-select"}`
    - `branch-select`: code `Panel`; COMPOSES `body-text`
    - `cta-next`: code `Stack`; COMPOSES `next-button`
  - **PageTemplate `single-task`:**
    - code `Screen`, with props `header`/`body`/`footer` typed `node`
    - `uxModel.slots`:
      - `header {required: true, allowed: [title, step-bar]}`
      - `body {required: true, allowed: [amount-field, product-summary, eligibility-check, terms-consent, auto-transfer, branch-select, notice-alert, body-text]}`
      - `footer {required: true, allowed: [cta-next]}`
  - **Screens:**
    - members: `intro`, `eligibility`, `ineligible`, `terms`, `amount`, `preferential`, `evidence-auto`, `confirm`, `done`
    - each has `pageId` equal to its id, `Screen COMPOSES single-task` and `Screen COMPOSES <organisms>`
  - **Procedure `savings-signup`** with every screen `PART_OF` it.
  - **`NEXT` edges:**
    - `intro→eligibility`
    - `eligibility→terms` (condition `cond:eligible`)
    - `eligibility→ineligible` (condition `!cond:eligible`)
    - `terms→amount`
    - `amount→preferential`
    - `preferential→evidence-auto` (condition `cond:autoTransfer`)
    - `preferential→confirm` (condition `!cond:autoTransfer`)
    - `evidence-auto→confirm`
    - `confirm→done`
  - **PolicyRules**, each with a `citation` whose sourceKind is `document-revision`, and **also** a `document-revision` sourceRef to the synthetic seed document. `review_node` approval of a PolicyRule requires a business source (`ontology_store.py:740-742`). The store-level fixture therefore first creates that document through the documents library helpers used by `tests/test_documents_library.py`. Each rule carries `appliesWhen` (below):
    - `r-terms-required` (severity critical, statement "가입 절차에는 약관 동의 단계가 있어야 합니다", `appliesWhen: "cond:eligible"`), with `terms GOVERNED_BY r-terms-required` and `terms-consent GOVERNED_BY r-terms-required`
    - `r-ineligible-reason` (critical, "자격 미충족이면 사유와 대체 행동을 노출합니다", `appliesWhen: "!cond:eligible"`), with `ineligible GOVERNED_BY r-ineligible-reason`
  - **Every node** is `provenance: "declared"`, `tombstone: false`, with one `sourceRef` `{sourceKind: "asset", sourceId: "seed-design-poc", revision: "1", sha256: <sha of "seed-design-poc">, audienceRevision: "1"}`.

- [ ] **Step 2: Write `tests/design_fixtures.py` and the failing tests**

```python
# platform/tests/design_fixtures.py
import copy, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workspace import ontology_schema as schema  # noqa: E402

SEED = ROOT / "seed" / "design_poc"


def raw_graph():
    return json.loads((SEED / "ontology.json").read_text(encoding="utf-8"))


def sealed_graph(approved=True):
    raw = raw_graph()
    state = "approved" if approved else "candidate"
    nodes = [schema.seal({**n, "reviewState": state}) for n in raw["nodes"]]
    revisions = {n["id"]: n["revision"] for n in nodes}
    # Edges stay "candidate", exactly as publish_candidate stores them; only nodes are reviewed.
    edges = [schema.seal({**e, "reviewState": "candidate",
                          "src": {"id": e["src"], "revision": revisions[e["src"]]},
                          "dst": {"id": e["dst"], "revision": revisions[e["dst"]]}}) for e in raw["edges"]]
    return {"schemaVersion": 1, "projectId": raw["projectId"], "generation": "gen-1", "nodes": nodes, "edges": edges,
            "coverage": {"complete": False, "unknown": ["outside-snapshot-not-certified"],
                         "scope": "procedure-snapshot", "truncated": False}}


def raw_graph_with(source_ref):
    """Publishable candidate graph: sealed nodes/edges, local ids, the given source ref everywhere."""
    raw = raw_graph()
    nodes = [schema.seal({**n, "sourceRefs": [source_ref], "reviewState": "candidate"}) for n in raw["nodes"]]
    rev = {n["id"]: n["revision"] for n in nodes}
    edges = [schema.seal({**e, "sourceRefs": [source_ref], "reviewState": "candidate",
                          "src": {"id": e["src"], "revision": rev[e["src"]]},
                          "dst": {"id": e["dst"], "revision": rev[e["dst"]]}}) for e in raw["edges"]]
    return {"schemaVersion": 1, "projectId": raw["projectId"], "nodes": nodes, "edges": edges}


def seed_document_ref(ctx):
    """Create and approve one synthetic library document with the helpers from tests/test_documents_library.py,
    then return its exact document-revision source ref (revision/audienceRevision as strings)."""
    from test_documents_library import approved_document     # existing helper; add it there if absent
    document, revision = approved_document(ctx, title="데모 적금 상품 설명", text="합성 문서")
    # The library resolves revisions by record id (e.g. "<documentId>--r000001"), not the display ordinal, and
    # the audience lives on the document (review round 4, X9; documents/library.py:212, ontology_sources.py:154).
    return {"sourceKind": "document-revision", "sourceId": document["id"], "revision": revision["id"],
            "sha256": revision["sha256"], "audienceRevision": str(document["aclVersion"])}


def extra_screens_graph(count, *, source_ref):
    nodes = [{"id": "p-big", "type": "Procedure", "scope": {"kind": "project", "projectId": "project-1"},
              "title": "큰 절차", "revision": 1, "sourceRefs": [source_ref], "provenance": "declared",
              "reviewState": "candidate", "tombstone": False}]
    edges = []
    for i in range(count):
        nodes.append({**nodes[0], "id": f"s{i:02d}", "type": "Screen", "title": f"화면 {i}"})
        edges.append({"id": f"po{i:02d}", "type": "PART_OF", "src": {"id": f"s{i:02d}", "revision": 1},
                      "dst": {"id": "p-big", "revision": 1}, "sourceRefs": [source_ref], "provenance": "declared",
                      "reviewState": "candidate", "tombstone": False})
    return {"schemaVersion": 1, "projectId": "project-1",
            "nodes": [schema.seal(n) for n in nodes], "edges": [schema.seal(e) for e in edges]}


def knowledge(**kw):
    from design_loop.knowledge import from_snapshot
    return from_snapshot(sealed_graph(**kw))
```

In the seed JSON, edges carry `src`/`dst` as plain ids; `sealed_graph` expands them.

```python
# platform/tests/test_design_knowledge.py
import copy
from design_fixtures import knowledge, sealed_graph
from workspace import ontology_schema as schema
from design_loop.knowledge import from_snapshot


def test_seed_is_contract_valid():
    g = sealed_graph()
    schema.validate_graph({k: g[k] for k in ("schemaVersion", "projectId", "nodes", "edges")})


def test_membership_is_built_before_transitions_regardless_of_edge_order():
    g = sealed_graph()
    g["edges"] = sorted(g["edges"], key=lambda e: e["type"] != "NEXT")   # NEXT edges first
    k = from_snapshot(g)
    assert k.procedures["savings-signup"]["screens"][:3] == ["intro", "eligibility", "terms"]
    assert len(k.procedures["savings-signup"]["transitions"]) == 9


def test_template_is_resolved_through_composes_not_implements():
    k = knowledge()
    assert k.screens["amount"]["templateId"] == "single-task"
    assert "amount-field" in k.screens["amount"]["assets"]


def test_truncation_and_unresolved_references_make_knowledge_incomplete():
    g = sealed_graph(); g["coverage"]["truncated"] = True
    assert not from_snapshot(g).complete
    g = sealed_graph()
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "branch-select"]
    g["edges"] = [e for e in g["edges"] if "branch-select" not in (e["src"]["id"], e["dst"]["id"])]
    k = from_snapshot(g)
    assert not k.complete and {"nodeId": "auto-transfer", "reference": "branch-select"} in k.unresolved


def test_candidate_dependency_of_an_approved_node_blocks_completeness():
    g = sealed_graph()
    g["nodes"] = [schema.seal({**n, "reviewState": "candidate"}) if n["id"] == "consent-row" else n for n in g["nodes"]]
    k = from_snapshot(g)
    assert not k.complete and "consent-row" in k.coverage["unapproved"]


def test_candidate_member_screen_of_an_approved_procedure_blocks_completeness():
    g = sealed_graph()
    g["nodes"] = [schema.seal({**n, "reviewState": "candidate"}) if n["id"] == "confirm" else n for n in g["nodes"]]
    k = from_snapshot(g)
    assert not k.complete and "confirm" in k.coverage["unapproved"]


def test_candidates_excluded_by_default():
    k = knowledge(approved=False)
    assert not k.assets and not k.screens
    assert from_snapshot(sealed_graph(approved=False), include_candidates=True).assets
```

Store test (append to `tests/test_ontology_store.py`):

```python
def test_procedure_snapshot_includes_members_transitions_and_dependencies(storage_with_project):
    """storage_with_project: the existing fixture pattern in this file (project, owner context, Ontology(ctx))."""
    ctx, ontology = storage_with_project
    from design_fixtures import raw_graph_with, seed_document_ref
    graph = raw_graph_with(seed_document_ref(ctx))        # real document-revision ref from the library helpers
    published = ontology.publish_candidate("design-seed", graph, expected_generation=None, request_id="seed-1")
    ids = published["identities"]
    for local in sorted(ids):                       # every published node exactly once
        node = ontology.read([ids[local]])["nodes"][0]
        for decision in ("reviewed", "approved"):
            ontology.review_node(ids[local], expected_generation=ontology.current()["generation"],
                                 revision=node["revision"], decision=decision, reason="seed",
                                 request_id=f"{decision}-{local}")
    snap = ontology.procedure_snapshot(ids["savings-signup"])
    types = {n["type"] for n in snap["nodes"]}
    assert {"Screen", "PageTemplate", "Organism", "Procedure"} <= types
    assert sum(1 for e in snap["edges"] if e["type"] == "NEXT") == 9
    assert all(e["reviewState"] == "candidate" for e in snap["edges"])      # edges are never node-reviewed
    from design_loop.knowledge import from_snapshot
    k = from_snapshot(snap)
    assert k.complete and len(k.procedures[ids["savings-signup"]]["transitions"]) == 9
    tpl = k.templates[ids["single-task"]]
    assert all(a in k.assets for slot in tpl["slots"].values() for a in slot["allowed"])  # rewrite applied (Codex #4)


def test_procedure_snapshot_truncates_at_max_screens(storage_with_project):
    ctx, ontology = storage_with_project
    from design_fixtures import extra_screens_graph, seed_document_ref
    graph = extra_screens_graph(21, source_ref=seed_document_ref(ctx))   # 21 Screens PART_OF "p-big"
    ids = ontology.publish_candidate("big", graph, expected_generation=None, request_id="big-1")["identities"]
    for local in ids:
        node = ontology.read([ids[local]])["nodes"][0]
        for decision in ("reviewed", "approved"):
            ontology.review_node(ids[local], expected_generation=ontology.current()["generation"],
                                 revision=node["revision"], decision=decision, reason="seed",
                                 request_id=f"{decision}-{local}")
    snap = ontology.procedure_snapshot(ids["p-big"])
    assert snap["coverage"]["truncated"] and "too-many-screens" in snap["coverage"]["unknown"]
    assert sum(1 for n in snap["nodes"] if n["type"] == "Screen") == 20
```

Write this test with the file's existing `candidate()`/`publish()` helpers and owner-role context. It uses the real `publish_candidate` and verifies that the returned `identities` map is applied (Codex #4).

- [ ] **Step 3: Run to verify failure** → FAIL.

- [ ] **Step 4: Implement `procedure_snapshot` in `ontology_store.py`** (after `closure`, reusing `_node`, `_index`, `_part`, `_visible`, `_visible_node`, `_recheck`)

```python
    def procedure_snapshot(self, procedure_id, *, max_screens=20, max_nodes=300, max_edges=1000):
        schema._identifier(procedure_id)
        current = self.current()
        if not current:
            fail(409, "ontology-not-indexed", "프로젝트 온톨로지가 아직 등록되지 않았습니다.")
        procedure = self._node(current, procedure_id)
        if (not procedure or procedure["type"] != "Procedure" or procedure["tombstone"]
                or not self._visible_node(current, procedure)):
            fail(404, "not-found", "조회할 수 있는 절차가 없습니다.")
        unknown, members, flow_edges = set(), {}, {}

        def edges_of(identifier):
            for edge_id in self._index(current, "adjacency", _bucket(identifier)).get(identifier, []):
                loc = self._index(current, "edges", _bucket(edge_id)).get(edge_id)
                if not loc:
                    fail(409, "ontology-integrity", "온톨로지 관계 인덱스가 누락되었습니다.")
                edge = next((e for e in self._part(current, loc["partition"])["graph"]["edges"] if e["id"] == edge_id), None)
                if not edge or edge["contentHash"] != loc["contentHash"]:
                    fail(409, "ontology-integrity", "관계 원본과 인덱스가 일치하지 않습니다.")
                if edge["tombstone"] or edge["reviewState"] in {"rejected", "deprecated"}:
                    continue
                if not self._visible(edge["sourceRefs"]):
                    unknown.add("unmapped-or-inaccessible")
                    continue
                yield edge

        for edge in edges_of(procedure_id):
            if edge["type"] != "PART_OF" or edge["dst"]["id"] != procedure_id:
                continue
            screen = self._node(current, edge["src"]["id"])
            if (not screen or screen["type"] != "Screen" or screen["tombstone"]
                    or not self._visible_node(current, screen)):
                unknown.add("unmapped-or-inaccessible")
                continue
            if screen["revision"] != edge["src"]["revision"] or procedure["revision"] != edge["dst"]["revision"]:
                unknown.add("stale-endpoint-revisions")
                continue
            if len(members) >= max_screens:
                unknown.add("too-many-screens")
                break
            members[screen["id"]] = screen
            flow_edges[edge["id"]] = edge
        for identifier in list(members):
            for edge in edges_of(identifier):
                if (edge["type"] == "NEXT" and edge["src"]["id"] == identifier and edge["dst"]["id"] in members
                        and all(members[edge[end]["id"]]["revision"] == edge[end]["revision"] for end in ("src", "dst"))):
                    flow_edges[edge["id"]] = edge
        design = self.closure(sorted(members), direction="dependencies", max_nodes=max_nodes, max_edges=max_edges) \
            if members else {"nodes": [], "edges": [], "coverage": {"unknown": [], "truncated": False}}
        nodes = {n["id"]: n for n in design["nodes"]}
        nodes.update(members)
        nodes[procedure_id] = procedure
        edges = {e["id"]: e for e in design["edges"]}
        edges.update(flow_edges)
        unknown |= set(design["coverage"]["unknown"])
        self._recheck(current)
        truncated = design["coverage"]["truncated"] or "too-many-screens" in unknown
        return {"schemaVersion": 1, "projectId": self.ctx.project_id, "generation": current["generation"],
                "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
                "edges": sorted(edges.values(), key=lambda e: e["id"]),
                "coverage": {"complete": False, "scope": "authorized-procedure-snapshot",
                             "truncated": truncated, "unknown": sorted(unknown | {"outside-snapshot-not-certified"})}}
```

- [ ] **Step 5: Implement `design_loop/knowledge.py`**

```python
"""Read-only design view over an authorized procedure snapshot. No I/O."""
from __future__ import annotations

from dataclasses import dataclass, field

from workspace.ontology_ux import references

RENDERABLE = ("Atom", "Molecule", "Organism", "Pattern", "Component")
_BLOCKING = {"ambiguous-entry", "stale-endpoint-revisions", "unmapped-or-inaccessible", "too-many-screens", "truncated",
             "retired-or-rejected-mapping", "unapproved-dependency"}


@dataclass
class Knowledge:
    generation: str
    coverage: dict
    assets: dict = field(default_factory=dict)
    templates: dict = field(default_factory=dict)
    screens: dict = field(default_factory=dict)
    procedures: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    unresolved: list = field(default_factory=list)

    @property
    def complete(self):
        return not self.coverage.get("truncated") and not self.unresolved and not _BLOCKING & set(self.coverage.get("unknown", []))


def _live(node, include_candidates):
    if node.get("tombstone"):
        return False
    return node["reviewState"] == "approved" or include_candidates and node["reviewState"] in ("candidate", "reviewed")


def from_snapshot(snapshot, *, include_candidates=False):
    every = {n["id"]: n for n in snapshot.get("nodes", [])}
    nodes = {i: n for i, n in every.items() if _live(n, include_candidates)}
    dropped = set()
    for e in snapshot.get("edges", []):
        if e.get("tombstone") or e["reviewState"] in ("rejected", "deprecated"):
            continue
        src, dst = e["src"]["id"], e["dst"]["id"]
        if src in nodes and dst in every and dst not in nodes:
            dropped.add(dst)               # candidate dependency of a live node
        if dst in nodes and src in every and src not in nodes and e["type"] in ("PART_OF", "NEXT", "COMPOSES"):
            dropped.add(src)               # candidate member Screen of a live Procedure (review round 4, #5/N11)
    edges = [e for e in snapshot.get("edges", []) if not e.get("tombstone")
             and e["reviewState"] not in ("rejected", "deprecated")
             and e["src"]["id"] in nodes and e["dst"]["id"] in nodes
             and nodes[e["src"]["id"]]["revision"] == e["src"]["revision"]
             and nodes[e["dst"]["id"]]["revision"] == e["dst"]["revision"]]
    k = Knowledge(generation=str(snapshot.get("generation") or ""), coverage=dict(snapshot.get("coverage") or {}))
    if dropped:
        k.coverage["unknown"] = sorted(set(k.coverage.get("unknown", [])) | {"unapproved-dependency"})
        k.coverage["unapproved"] = sorted(dropped)
    for n in nodes.values():
        props, kind = n.get("properties", {}), n["type"]
        ux = props.get("uxModel", {})
        for alias in n.get("aliases", []):
            if alias["namespace"] == "partition-local":
                k.aliases[alias["value"]] = n["id"]
        if kind in RENDERABLE:
            k.assets[n["id"]] = {"id": n["id"], "level": kind, "title": n["title"], "intent": ux.get("intent"),
                                 "code": ux.get("layers", {}).get("code"), "requiredStates": ux.get("requiredStates", []),
                                 "stateProps": ux.get("stateProps", {}),
                                 "conditions": ux.get("conditions", []), "bindings": ux.get("dataBindings", []),
                                 "composes": []}
        elif kind == "PageTemplate":
            k.templates[n["id"]] = {"id": n["id"], "title": n["title"], "slots": ux.get("slots", {}),
                                    "code": ux.get("layers", {}).get("code")}
        elif kind == "Screen":
            k.screens[n["id"]] = {"id": n["id"], "title": n["title"], "pageId": props.get("pageId", n["id"]),
                                  "templateId": None, "assets": [], "procedureId": None}
        elif kind == "Procedure":
            k.procedures[n["id"]] = {"id": n["id"], "title": n["title"], "screens": [], "transitions": []}
        elif kind == "PolicyRule":
            k.rules[n["id"]] = {"id": n["id"], "ruleId": props.get("ruleId", n["id"]),
                                "statement": props.get("statement", n["title"]), "required": bool(props.get("required")),
                                "severity": props.get("severity", "major"), "targets": [], "citation": props.get("citation"),
                                "appliesWhen": props.get("appliesWhen")}
        elif kind == "Foundation" and props.get("token"):
            k.tokens[props.get("name", n["id"])] = props["token"]
        for ref in references(ux) if ux else []:
            if ref not in nodes:
                k.unresolved.append({"nodeId": n["id"], "reference": ref})
    by_type = lambda t: [e for e in edges if e["type"] == t]
    for e in by_type("PART_OF"):
        s, p = e["src"]["id"], e["dst"]["id"]
        if s in k.screens and p in k.procedures:
            k.screens[s]["procedureId"] = p
            k.procedures[p]["screens"].append(s)
    for e in by_type("COMPOSES"):
        s, d = e["src"]["id"], e["dst"]["id"]
        if s in k.screens and d in k.templates:
            k.screens[s]["templateId"] = d
        elif s in k.screens and d in k.assets:
            k.screens[s]["assets"].append(d)
        elif s in k.assets and d in k.assets:
            k.assets[s]["composes"].append(d)
    for e in by_type("GOVERNED_BY"):
        if e["dst"]["id"] in k.rules:
            k.rules[e["dst"]["id"]]["targets"].append(e["src"]["id"])
    for e in sorted(by_type("NEXT"), key=lambda x: x["id"]):
        s, d = e["src"]["id"], e["dst"]["id"]
        proc = k.screens.get(s, {}).get("procedureId")
        if proc and k.screens.get(d, {}).get("procedureId") == proc:
            props = e.get("properties", {})
            k.procedures[proc]["transitions"].append({"id": e["id"], "src": s, "dst": d,
                                                      "conditionId": props.get("conditionId"),
                                                      "condition": props.get("condition")})
    for proc in k.procedures.values():
        proc["screens"] = _ordered(proc, k)
    return k


def _ordered(proc, k):
    members, moves = proc["screens"], proc["transitions"]
    incoming = {t["dst"] for t in moves}
    entries = [s for s in members if s not in incoming]
    if len(entries) != 1:
        k.coverage.setdefault("unknown", []).append("ambiguous-entry")
    order, queue = [], entries[:1]
    while queue:
        cursor = queue.pop(0)
        if cursor in order:
            continue
        order.append(cursor)
        outs = sorted((t for t in moves if t["src"] == cursor), key=lambda t: (t["condition"] is not None, t["id"]))
        queue += [t["dst"] for t in outs]
    return order + [s for s in members if s not in order]
```

- [ ] **Step 6: Update `ONTOLOGY_CONTRACT.md`.** Add a `procedure_snapshot` section covering its reads, limits, coverage reasons and its non-authority: it is a view, not approval.
- [ ] **Step 7: Run** `python3 -m pytest tests/test_design_knowledge.py tests/test_ontology_store.py -q` → PASS.
- [ ] **Step 8: Commit** `git commit -m "feat(design): authorized procedure snapshot and ordered knowledge view"`

### Task E5: Asset derivation from real analyzer output (O-03, O-10; Codex #6)

**Files:**
- Create: `platform/design_loop/derive.py`
- Create: `platform/design_loop/local_runner.py` (used again in E11)
- Test: `platform/tests/test_design_derive.py`

**Interfaces:**
- `local_runner.run_node(script: Path, request: dict, *, timeout=30) -> dict`:
  - writes `request` to a fresh temp dir as `in.json`
  - runs `node <script> in.json out.json` with `cwd=script.parent` and `env={"PATH": …}`
  - returns the parsed `out.json`, or raises `RunnerError`

  Only the fixed file names `in.json`/`out.json` are written. No caller-supplied path touches the filesystem (Codex #15).
- **Resolver authority (review round 8, AB2).** The analyzer trusts the `resolver.packages` it is given (`analyze.cjs:65-71`), so the resolver is never caller-supplied. It is a **server-configured resolver profile**, like the existing `resolverProfileId` selection (`ontology_jobs.py:80-85`):
  - `{id, revision, hash, aliases, packages}`, with every package entry verified against the platform package registry (`react-kit/catalog.json` / the registered `package` source kind) by name, version and sha256
  - administered IAM-only, like admission policies
  - B0 intake I6a freezes the profile id, revision and hash in the collection decision, and `source.analyze` rechecks it before analysis and before publication
  - `jsx_sequences` treats `approved-package` as resolved **only** when the package is in the frozen profile. Otherwise it counts as unresolved.
  - Tests: a forged package hash is rejected at profile validation; a request-supplied alias override is ignored or rejected; an unregistered package in the analyzer output is not counted as resolved.
- `jsx_sequences(analysis) -> dict[path, list[Use]]`:
  - `Use` is `{identity: "<package-or-targetPath>#<symbol>", symbol, localName, line, column}`.
  - Only `kind == "jsx-use"` references whose `resolution.status` is `approved-package` or `resolved-local` count.
  - Identity comes from `resolution.package` or `resolution.targetPath`, plus `symbol`.
  - Order is `(line, column)`, which is the analyzer's source position.
  - Unresolved uses are reported separately.
- `mine(sequences, *, min_len=2, max_len=5, min_support=2, exclude=frozenset()) -> list[Candidate]`:
  - n-grams are over `identity`, and `exclude` holds brand identities.
  - Only maximal grams are kept: a gram is dropped when a longer gram has identical usage.
- `candidate_graph(candidates, *, project_id, source_ref) -> {nodes, edges, coverage}`:
  - `source_ref` is an `asset` ref with string `revision`/`audienceRevision`.
  - Candidates are Molecule when there are ≤3 elements, otherwise Organism, with `provenance: "parser-extracted"`.
  - Coverage copies the analyzer's `coverage.truncated` and the unresolved count.

- [ ] **Step 1: Write the failing tests** (run the real analyzer; skip only when `source-analyzer/node_modules` is absent)

```python
# platform/tests/test_design_derive.py
import hashlib, shutil
from pathlib import Path

import pytest
from design_fixtures import ROOT
from workspace import ontology_schema as schema
from design_loop.derive import candidate_graph, jsx_sequences, mine
from design_loop.local_runner import run_node

ANALYZER = ROOT / "source-analyzer" / "analyze.cjs"
needs = pytest.mark.skipif(not (ROOT / "source-analyzer" / "node_modules").exists() or not shutil.which("node"),
                           reason="source-analyzer dependencies not installed")
KIT = "import { Title, RateBadge, InfoRow, Button, Checkbox, Logo } from '@demo/kit';\nimport Default from '@demo/kit';\n"
FILES = {
    "a/one.tsx": KIT + "export const A = () => <><Title/><RateBadge/><InfoRow/><Button/></>;",
    "a/two.tsx": KIT + "export const B = () => <><Title/><RateBadge/><InfoRow/><Checkbox/></>;",
    "b/three.tsx": KIT + "export const C = () => <>\n<Title/><RateBadge/><InfoRow/></>;",
    "b/four.tsx": KIT + "export const D = () => <><Logo/><Button/><Default/></>;",
}


def analyze():
    files = [{"path": p, "kind": "code", "text": t, "sha256": hashlib.sha256(t.encode()).hexdigest()} for p, t in FILES.items()]
    resolver = {"aliases": {}, "packages": {"@demo/kit": {"version": "1.0.0", "sha256": "a" * 64}}, "jsonAssetFields": []}
    return run_node(ANALYZER, {"schemaVersion": 1, "files": files, "resolver": resolver})


@needs
def test_same_line_order_is_source_order_not_alphabetical():
    seq = jsx_sequences(analyze())["a/one.tsx"]
    assert [u["symbol"] for u in seq] == ["Title", "RateBadge", "InfoRow", "Button"]


@needs
def test_default_and_named_imports_have_distinct_identity():
    seq = jsx_sequences(analyze())["b/four.tsx"]
    assert seq[-1]["identity"] == "@demo/kit#default" and seq[1]["identity"] == "@demo/kit#Button"


@needs
def test_mine_repeated_triplet_and_exclude_brand():
    cands = mine(jsx_sequences(analyze()), min_support=3, exclude=frozenset({"@demo/kit#Logo"}))
    top = cands[0]
    assert [i.split("#")[1] for i in top["identities"]] == ["Title", "RateBadge", "InfoRow"] and top["support"] == 3
    assert all("@demo/kit#Logo" not in c["identities"] for c in cands)


@needs
def test_candidate_graph_is_contract_valid_with_string_revisions():
    ref = {"sourceKind": "asset", "sourceId": "code-1", "revision": "1", "sha256": "b" * 64, "audienceRevision": "1"}
    g = candidate_graph(mine(jsx_sequences(analyze()), min_support=3), project_id="p1", source_ref=ref)
    assert g["nodes"] and all(n["reviewState"] == "candidate" and n["provenance"] == "parser-extracted" for n in g["nodes"])
    schema.validate_graph({"schemaVersion": 1, "projectId": "p1", "nodes": g["nodes"], "edges": g["edges"]})
```

Check the analyzer's `symbol` value for default imports in `importBinding`. If it reports `"default"`, the identity is `@demo/kit#default`; otherwise adjust the test to the observed value and keep the distinct-identity assertion.

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement `local_runner.py` and `derive.py`**

```python
# local_runner.py
"""Offline runner for trusted repository Node tools. Fixed file names only; no caller path reaches the filesystem."""
from __future__ import annotations
import json, os, subprocess, tempfile
from pathlib import Path


class RunnerError(RuntimeError):
    pass


def run_node(script, request, *, timeout=30):
    script = Path(script).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in.json"), Path(tmp, "out.json")
        src.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(["node", str(script), str(src), str(out)], cwd=script.parent, timeout=timeout,
                              capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
        if proc.returncode != 0 or not out.exists():
            raise RunnerError(f"{script.name} failed")
        return json.loads(out.read_text(encoding="utf-8"))
```

```python
# derive.py
"""Derive reusable composition candidates from statically analyzed screen code (REQUIREMENTS O-03)."""
from __future__ import annotations
from collections import defaultdict
from workspace import ontology_schema as schema

RESOLVED = {"approved-package", "resolved-local"}


def jsx_sequences(analysis):
    per = defaultdict(list)
    for ref in analysis.get("references", []):
        if ref.get("kind") != "jsx-use":
            continue
        res = ref.get("resolution") or {}
        if res.get("status") not in RESOLVED:
            continue
        origin = res.get("package") or res.get("targetPath")
        per[ref["path"]].append({"identity": f"{origin}#{ref.get('symbol')}", "symbol": ref.get("symbol"),
                                 "localName": ref.get("localName"), "line": ref["line"], "column": ref["column"]})
    return {p: sorted(v, key=lambda u: (u["line"], u["column"])) for p, v in per.items()}


def mine(sequences, *, min_len=2, max_len=5, min_support=2, exclude=frozenset()):
    usage = defaultdict(set)
    for path, seq in sequences.items():
        ids = [u["identity"] for u in seq]
        for n in range(min_len, max_len + 1):
            for i in range(len(ids) - n + 1):
                gram = tuple(ids[i:i + n])
                if not set(gram) & exclude:
                    usage[gram].add(path)
    kept = {g: u for g, u in usage.items() if len(u) >= min_support}
    maximal = {g: u for g, u in kept.items()
               if not any(len(o) > len(g) and kept[o] == u and any(o[i:i + len(g)] == g for i in range(len(o) - len(g) + 1))
                          for o in kept)}
    out = [{"id": "cand-" + schema.digest(list(g))[:24], "level": "Molecule" if len(g) <= 3 else "Organism",
            "identities": list(g), "support": len(u), "usage": sorted(u), "score": len(u) * len(g)}
           for g, u in maximal.items()]
    return sorted(out, key=lambda c: (-c["score"], c["identities"]))


def candidate_graph(candidates, *, project_id, source_ref, analysis_coverage=None):
    scope = {"kind": "project", "projectId": project_id}
    nodes = [schema.seal({"id": c["id"], "scope": scope, "type": c["level"],
                          "title": " + ".join(i.split("#")[1] for i in c["identities"])[:300], "revision": 1,
                          "sourceRefs": [source_ref], "provenance": "parser-extracted", "reviewState": "candidate",
                          "tombstone": False,
                          "properties": {"description": f"반복 {c['support']}회: " + ", ".join(c["usage"][:20])[:7900]}})
             for c in candidates[:200]]
    cov = analysis_coverage or {}
    return {"nodes": nodes, "edges": [],
            "coverage": {"complete": False, "scope": "observed-static-references", "truncated": bool(cov.get("truncated")),
                         "unknown": ["runtime-composition-not-observed"] +
                                    (["unresolved-references"] if cov.get("unresolvedObservations") else [])}}
```

- [ ] **Step 4: Run** → PASS (or SKIP without node dependencies; CI installs them).
- [ ] **Step 5: Commit** `git commit -m "feat(design): mine repeated JSX compositions from real analyzer output"`

### Task E5a: PageTemplate reverse-derivation (O-03; review round 19, AM1)

O-03 requires Molecule, Organism **and template** candidates from operating screens. Flat JSX sequences (E5) cannot show layout structure, so this task adds nesting evidence.

**Files:**
- Modify: `platform/source-analyzer/analyze.cjs`. This is owned by the ontology maintainer and co-reviewed. `jsx-use` references gain `parent` (the index of the enclosing `jsx-use` reference in the same file, or `null` at the file's root JSX) and `depth`, from a stack maintained in the existing `walk` at `analyze.cjs:292-307`.
- Modify: `platform/design_loop/derive.py` (add `mine_templates`)
- Test: `platform/source-analyzer/test/analyze.test.cjs` and `platform/tests/test_design_derive.py`

**Interfaces:**
- `page_structures(analysis) -> dict[path, {root: identity, regions: [identity]}]`: for every file whose root JSX element resolves, `regions` are the ordered identities of its **direct** children (depth 1).
- `mine_templates(structures, *, min_support=2) -> [TemplateCandidate]` groups files by `(root, len(regions))`, aligning regions position by position.
  - **Capacity** (review round 23, AQ2): a structure with more than **12** direct regions (the E3 slot limit) is not proposed as a template. It is recorded as `coverage.unknown += ["template-capacity"]` with its file count, and the other candidates in the batch still publish. Test: 12- and 13-region inputs go through mining and publication. The 12-region input yields a valid 12-slot template, and the 13-region input yields a coverage note and no template, without aborting the batch.
  - Each position becomes a slot `slot<n>`, for example `slot1`. The name satisfies both the slot id and the code-layer prop-name pattern `[a-z][A-Za-z0-9]{0,39}` from E3, because each template slot must be a `node` prop of the same name (review round 22, AP1), with `allowed` = the set of identities observed there.
  - A group with at least `min_support` files is proposed as a `PageTemplate` candidate. Its `uxModel.slots` has `required: true` when the position is present in every file of the group. `usage` lists the files. `Q-03` thresholds come from settings.
- **Identity resolution** (review round 20, AN1). Analyzer identities such as `@studio/approved-ui#Panel` or `components/Header.tsx#Header` are **not** ontology ids. Before building nodes, `resolve_identities(identities, k)` maps each identity to an asset node id:
  - through existing asset nodes whose `uxModel.layers.code` `{importPath, exportName}` matches, or
  - through the local id of a molecule/organism candidate mined in the same run, `cand-<hash>`, which is a valid `_ID`

  An identity with no mapping stays **explicit**: its slot gets no `allowed` entry for it, and the template candidate records `properties.description` `"unmapped: <count>"`. Unmapped identities are never put into `allowed`.
  - A slot whose identities are **all** unmapped is **omitted** from the template's `uxModel.slots`, because an empty allow-list is invalid in E3. It is recorded in `properties.description` (`"unmapped region at position <n>"`). The template's code layer `props` then exclude that slot too.
  - A template left with no mapped slot is not emitted. Its observation goes into the mining result's `coverage.unknown` as `unmapped-template-structure`, and the other candidates in the batch still publish (review round 21, AN1).
  - Test: a fully mapped mined template **including its code layer** (`props: {slot1: {type: "node", …}}`) publishes through the real `publish_candidate`.
  - Test: real analyzer output where one region is entirely unmapped → a valid template with the remaining slots, published alongside other candidates. A fully unmapped structure → no template, the coverage note is present, and the batch still publishes. File usages map to their analyzed **Screen** node ids, from the collection's screen registry; unmapped files go only into description text, not `usageIds`. Source paths and symbols stay in the candidate's `sourceRefs[*].location` (`path`, `exportName`).
- `candidate_graph` (E5) emits these as `PageTemplate` nodes (`provenance: "parser-extracted"`, candidate), with the mapped usages in `usageIds`. A test takes real analyzer output through `mine_templates` → `resolve_identities` → `candidate_graph` → `publish_candidate` successfully. When the region identities map to existing asset nodes, it also emits `Screen COMPOSES PageTemplate` candidate edges from the analyzed screens.

**Tests:**
- The analyzer reports `parent`/`depth` correctly for nested JSX; the node test uses the real analyzer.
- Three files sharing `Screen > [Header, Stack, Footer]` → one PageTemplate candidate with 3 slots, which passes `validate_ux_model` and `schema.validate_graph`.
- The **actual mining API** (C1 `POST /design/{id}/mining`, test in C) publishes it as a reviewable candidate.
- Files with different region counts produce no merged template.

- [ ] **Steps:** failing tests → analyzer change → `mine_templates` → pass. Commit: `git commit -m "feat(design): derive PageTemplate candidates from repeated screen structure"`

### Task E6: Guideline pages → cited PolicyRule candidates (O-02, O-04; Codex #7)

**Files:**
- Create: `platform/design_loop/guide_rules.py`
- Test: `platform/tests/test_design_guide_rules.py`

**Interfaces:**
- Input pages come from B0 intake admitted derivatives, never from `guidelines.context_for`: `pages = [{admissionId, derivativeHash, sourceRef, page, text}]`.
  - `sourceRef` is the original's `document-revision` or `product-guideline` ref.
  - The derivative text is what the model sees and what quotes are checked against.
- `cited_pages(pages) -> dict[(sourceId, page), page]` is the single key shape used by both extraction and citation verification. `page` is always a positive integer. For unpaginated sources it is the **logical page** assigned by B0 intake (review round 8, AB4). It replaces the mismatched `(sourceId, page)` and `(assetId, sourceId, page)` shapes.
- `extract_rules(pages, deps, *, asset_ids, model, prompt_version="guide-rules-1") -> {"rules", "rejected"}`
  - A rule is `{ruleId, statement, required, severity, appliesTo, citation: {sourceKind, sourceId, page, quote, derivativeHash}, extraction: {method: "model", model, promptVersion, admissionId}}`.
  - A quote must appear verbatim, after whitespace normalization, in the cited derivative page. Otherwise the rule goes to `rejected` with `quote-not-found`.
- The extraction prompt asks for an optional `appliesWhen` per rule. The result is validated with `ontology_ux.parse_when`: invalid → `rejected: applies-when`. Condition ids must be among the PRD condition ids passed in as `condition_ids`.
- `rule_graph(rules, *, project_id, revisions: dict[assetId, int]) -> {nodes, edges}` returns PolicyRule candidates and `asset GOVERNED_BY rule` edges. It **carries `appliesWhen`** into the node properties (review round 4, N14). A test asserts a conditional rule keeps it after `publish_candidate`.
  - Edge endpoint revisions come from `revisions`, the live snapshot. An unknown asset is dropped from `appliesTo`, with a `rejected` entry `unknown-target`.
  - `provenance` is `"model-inferred"` on the draft graph. The *publishing producer* is decided in C, through the ledger completion path (see C Task C2).
- Review authority: PolicyRule approval is `planner`/`owner` (`ontology_store.py:728`). The UI routes these candidates to the planner queue, and designers can comment. REQUIREMENTS O-04 says "UX 디자이너가 확정", which conflicts with this authority. The conflict is recorded as roadmap open decision D-1 and is **not** resolved by code.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_design_guide_rules.py
import json
from design_loop.guide_rules import cited_pages, extract_rules, rule_graph
from workspace import ontology_schema as schema

REF = {"sourceKind": "document-revision", "sourceId": "guide-1", "revision": "3", "sha256": "c" * 64, "audienceRevision": "2"}
PAGES = [{"admissionId": "adm-1", "derivativeHash": "d" * 64, "sourceRef": REF, "page": 12,
          "text": "가입 절차에는 반드시 약관 동의 단계를 둔다. 자격이 없으면 사유와 대안을 보여준다."}]


def gen(payload):
    return {"generate": lambda s, u, t: json.dumps(payload, ensure_ascii=False)}


def rule(**over):
    base = {"statement": "약관 동의 단계 필수", "required": True, "severity": "critical", "appliesTo": ["terms-consent"],
            "citation": {"sourceId": "guide-1", "page": 12, "quote": "반드시 약관 동의 단계를 둔다"}}
    base.update(over)
    return base


def test_single_key_shape():
    assert list(cited_pages(PAGES)) == [("guide-1", 12)]


def test_verified_quote_keeps_model_provenance():
    out = extract_rules(PAGES, gen({"rules": [rule()]}), asset_ids=["terms-consent"], model="model-a")
    r = out["rules"][0]
    assert r["extraction"] == {"method": "model", "model": "model-a", "promptVersion": "guide-rules-1", "admissionId": "adm-1"}
    assert r["citation"]["derivativeHash"] == "d" * 64 and r["citation"]["sourceKind"] == "document-revision"


def test_unverified_quote_rejected():
    out = extract_rules(PAGES, gen({"rules": [rule(citation={"sourceId": "guide-1", "page": 12, "quote": "배너는 세 줄"})]}),
                        asset_ids=[], model="m")
    assert not out["rules"] and out["rejected"][0]["reason"] == "quote-not-found"


def test_rule_graph_uses_live_revisions_and_is_contract_valid():
    out = extract_rules(PAGES, gen({"rules": [rule()]}), asset_ids=["terms-consent"], model="m")
    g = rule_graph(out["rules"], project_id="p1", source_ref_for=lambda c: REF, revisions={"terms-consent": 4})
    assert g["edges"][0]["src"] == {"id": "terms-consent", "revision": 4}
    for n in g["nodes"]:
        schema.validate_node(n)
```

- [ ] **Step 2: Fail** → **Step 3: Implement**

The `extract_rules` system prompt asks for JSON `{"rules": [...]}` with verbatim quotes. The implementation:
- normalizes whitespace
- looks up `cited_pages()[(sourceId, page)]`
- requires `quote in page.text`
- bounds the statement (500 characters) and the rule count (200)
- builds `ruleId = "rule-" + schema.digest([sourceId, page, statement])[:24]`

`rule_graph` seals PolicyRule nodes with these properties:
- `ruleId`
- `statement`
- `required`
- `severity`
- `guidelineId`: the sourceId
- `citation`: without `sourceId`, because the schema's `citation` fields are `{sourceKind, page, quote, derivativeHash}`
- `extraction`

It also seals `GOVERNED_BY` edges `{src: {id: asset, revision: revisions[asset]}, dst: {id: ruleId, revision: 1}}`.

- [ ] **Step 4: Pass** → **Step 5: Commit** `git commit -m "feat(design): cited guideline rules from admitted derivatives with model provenance"`

### Task E7: PRD extraction with exact value spans (P-01, P-02; Codex #8)

**Files:**
- Create: `platform/design_loop/prd_extract.py`, `platform/seed/design_poc/pages.json`
- Test: `platform/tests/test_design_prd_extract.py`

**Interfaces:**
- `extract_prd(pages, deps, *, model) -> {"prd": PRD | None, "issues": [{field, code}]}`
- PRD shape:

```json
{"productName": {"value": "...", "cite": C},
 "productType": {"value": "수신"}, "category": {"value": "적금", "cite": C},
 "eligibility": {"text": {"value": "...", "cite": C}, "conditionId": "eligible"},
 "term": {"value": "12개월", "cite": C}, "baseRate": {"value": "연 2.0%", "cite": C},
 "preferential": [{"id": "autoTransfer", "condition": {"value": "...", "cite": C}, "rate": {"value": "연 0.5%p", "cite": C},
                   "evidence": "input|auto|none"}],
 "notices": [{"id": "n1", "text": {"value": "...", "cite": C}}]}
```

  where `C = {sourceId, page, quote, derivativeHash}`.
- Rules:
  - **Required citations.** `productName`, `category`, `eligibility.text`, `term`, `baseRate`, every preferential `condition` and `rate`, and every notice `text` each need a cite. A missing cite → `missing-citation`.
  - **Quote check.** A quote must be found verbatim in its derivative page → otherwise `quote-not-found`.
  - **Boundaries are checked against the full admitted page, not the model's quote** (review round 24, R1-8). The model may shorten a quote, for example `2개월` out of `가입기간 12개월`. `extract_prd` therefore locates the quote's offset in the normalized full page. `re.finditer` is used, and an ambiguous multiple match is resolved only when every occurrence satisfies the checks. It then applies the token-boundary checks below to the value's span **within the page text**: the character before the span, the nearest preceding non-space sign, and the following unit and characters.
    - Tests shorten the quote and the value **together**, as in the three cases below, and assert `value-not-verbatim`. PRD confirmation (C2) refuses them.
    - `2개월` within `가입기간 12개월` → rejected
    - `2.0%` within `변동 -2.0%` → rejected
    - `0.5%` within `우대 연 0.5%p` → rejected
  - **Exact financial spans** (review rounds 2 and 3, #8). For `term`, `baseRate` and preferential `rate`:
    - The normalized value must include its unit and match `design_loop.financial.QUANTITY` in full, optionally prefixed by `연`. This is the same shared grammar E10 uses (AV2). A bare `12` → `value-unit-missing`.
    - The value must also occur in the normalized quote as a whole token span:
      - `m = re.search(re.escape(value) + r"(?![0-9,%p]|\.\d)", quote)`, so a trailing sentence period is allowed
      - the character immediately before `m.start()` is not a digit, `.` or `,`
      - the nearest non-space character before the number is not a sign (`+`, `-` or `−`) unless `value` itself carries that sign (review round 4, #8)

      Otherwise → `value-not-verbatim`.

    This rejects:
    - `2년` and `2개월` against `가입기간 12개월`
    - `12` (no unit)
    - `0.5%` against `0.5%p`
    - `2.0%` against `-2.0%`
  - **Other values.** The value must be a substring of its quote → otherwise `value-not-in-quote`.
  - **Productivity.** `productType` is uncited metadata, limited to `수신|여신` → otherwise `bad-type`.
- **Binding path segments are server-assigned ordinals** (review round 13, AF1). `extract_prd` assigns `pref-1`, `pref-2`, … to preferential entries and `notice-1`, … to notices, in document order. The model's or the ontology's own condition id is kept separately, as `conditionId` (for example `autoTransfer`), used by flow conditions, and it is opaque-tokenized in model context (B2 S2). The regex only fixes the shape. **Membership** is exact: every bound path must be a key of `bindings(prd)` for the current PRD. `composition.validate` checks this with `binding_paths`, and the tool Lambda rechecks it before transfer. A canary such as `product.notice.customer-canary` fails the shape check, and a well-formed but absent `product.notice.notice-9` fails membership. The seed uses `product.preferential.pref-1.rate`.
- `bindings(prd) -> dict[path, value]` is the deterministic binding catalog used by GUI and codegen. `binding_types(prd) -> dict[path, "string"|{"list": item}]` gives each value's type. Derived list values are built deterministically from cited values only, for example `product.summaryItems = [{label: "기본금리", value: baseRate}, {label: "가입기간", value: term}]`. It contains `product.productName`, `product.baseRate`, `product.term`, `product.preferential.pref-<n>.rate`, `product.notice.notice-<n>` and so on. A composition may display financial values only through these paths.
- `prd_hash(prd) = schema.digest(prd)` identifies the PRD revision in C.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_design_prd_extract.py
import copy, json
import pytest
from design_loop.prd_extract import bindings, extract_prd

D = "e" * 64
PAGES = [
    {"admissionId": "adm-2", "derivativeHash": D, "sourceRef": {"sourceId": "desc"}, "page": 1,
     "text": "상품명: 데모 청년 적금\n상품유형: 적금\n기본금리 연 2.0%\n가입기간 12개월\n우대금리 자동이체 시 연 0.5%p"},
    {"admissionId": "adm-3", "derivativeHash": D, "sourceRef": {"sourceId": "terms"}, "page": 3,
     "text": "만 19세 이상 34세 이하 개인만 가입할 수 있습니다. 이 예금은 예금자보호법에 따라 보호됩니다."}]


def c(src, page, quote):
    return {"sourceId": src, "page": page, "quote": quote, "derivativeHash": D}


GOOD = {"productName": {"value": "데모 청년 적금", "cite": c("desc", 1, "상품명: 데모 청년 적금")},
        "productType": {"value": "수신"}, "category": {"value": "적금", "cite": c("desc", 1, "상품유형: 적금")},
        "eligibility": {"text": {"value": "만 19세 이상 34세 이하", "cite": c("terms", 3, "만 19세 이상 34세 이하 개인만")},
                        "conditionId": "eligible"},
        "term": {"value": "12개월", "cite": c("desc", 1, "가입기간 12개월")},
        "baseRate": {"value": "연 2.0%", "cite": c("desc", 1, "기본금리 연 2.0%")},
        "preferential": [{"id": "autoTransfer", "condition": {"value": "자동이체", "cite": c("desc", 1, "자동이체 시 연 0.5%p")},
                          "rate": {"value": "연 0.5%p", "cite": c("desc", 1, "자동이체 시 연 0.5%p")}, "evidence": "input"}],
        "notices": [{"id": "n1", "text": {"value": "예금자보호법에 따라 보호됩니다",
                                           "cite": c("terms", 3, "이 예금은 예금자보호법에 따라 보호됩니다.")}}]}


def run(obj):
    return extract_prd(PAGES, {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False)}, model="m")


def test_good_prd_passes():
    out = run(GOOD)
    assert out["issues"] == [] and bindings(out["prd"])["product.baseRate"] == "연 2.0%"


@pytest.mark.parametrize("path,value", [("term", "2년"), ("term", "2개월"), ("term", "12"), ("baseRate", "연 2.1%"),
                                        ("baseRate", "2.0%p"), ("baseRate", "2.0")])
def test_changed_financial_value_is_rejected(path, value):
    bad = copy.deepcopy(GOOD); bad[path]["value"] = value
    assert {c["code"] for c in run(bad)["issues"] if c["field"] == path} & {"value-not-verbatim", "value-unit-missing"}


def test_trailing_period_is_accepted():
    pages = [{**PAGES[0], "text": PAGES[0]["text"] + "\n기본금리는 연 2.0%."}]
    obj = copy.deepcopy(GOOD); obj["baseRate"] = {"value": "연 2.0%", "cite": c("desc", 1, "기본금리는 연 2.0%.")}
    out = extract_prd(pages + PAGES[1:], {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False)}, model="m")
    assert not [i for i in out["issues"] if i["field"] == "baseRate"]


def test_sign_and_suffix_boundaries():
    pages = [{**PAGES[0], "text": "우대 연 0.5%p, 변동 - 2.0%"}]
    obj = copy.deepcopy(GOOD); obj["baseRate"] = {"value": "2.0%", "cite": c("desc", 1, "변동 - 2.0%")}
    out = extract_prd(pages + PAGES[1:], {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False)}, model="m")
    assert {"field": "baseRate", "code": "value-not-verbatim"} in out["issues"]


def test_notice_and_condition_citations_are_required():
    bad = copy.deepcopy(GOOD); del bad["notices"][0]["text"]["cite"]; del bad["preferential"][0]["condition"]["cite"]
    issues = run(bad)["issues"]
    assert {"field": "notices.n1", "code": "missing-citation"} in issues
    assert {"field": "preferential.autoTransfer.condition", "code": "missing-citation"} in issues
```

- [ ] **Step 2: Fail** → **Step 3: Implement.** Create `seed/design_poc/pages.json` with the two synthetic pages above, then implement `prd_extract.py`:
  - `_norm = lambda t: re.sub(r"\s+", " ", t).strip()`
  - a table `CITED` of `(path, getter, financial: bool)` covering the required fields
  - `_check(path, entry, index, financial)` appends the codes above
  - `bindings()` flattens values into dotted paths
  - parse failure → `{"prd": None, "issues": [{"field": "*", "code": "parse-failed"}]}`

  No field is ever corrected by the code: issues block P-03 confirmation in C.
- [ ] **Step 4: Pass** → **Step 5: Commit** `git commit -m "feat(design): PRD extraction with required citations and exact financial spans"`

### Task E8: Flow model, independent expected graph and case traversal (U-01, U-02; Codex #9)

**Files:**
- Create: `platform/design_loop/flow.py`
- Test: `platform/tests/test_design_flow.py`

**Interfaces:**
- `build_flow(k, procedure_id) -> Flow` returns `{procedureId, entry, screens: [id], transitions: [{id, src, dst, when}], terminals: [id]}`.
  - `when` is the NEXT edge's `condition`, or `None`.
  - Terminals are members with no outgoing transition.
- `expected(prd, k) -> Expectation` is derived **only** from the PRD conditions and the approved rules, without reading the flow's transitions. It returns `{conditions: [id], requirements: [...]}`, where each requirement is one of:
  - `{"kind": "reach-terminal", "case": "*"}`: every case must reach some terminal.
  - `{"kind": "terminal-state", "when": "!cond:eligible", "state": "ineligible"}`. It is emitted when the PRD has an eligibility condition. It requires the path under that case to end on a screen whose assets include one with `requiredStates ∋ "ineligible"`, or that is governed by a required rule targeting the ineligible outcome.
  - `{"kind": "visit-when", "when": "cond:eligible & cond:<id>", "asset": "<evidence asset>"}`. It is emitted for each preferential condition with `evidence == "input"`. The requirement applies **only to eligible cases**, because an ineligible path ends at the ineligible terminal (review round 3, #9). An eligible path with the condition must visit a screen containing an asset whose `conditions` include `cond:<id>` with effect `include`. An eligible path without the condition must not visit it. Ineligible paths are not checked for this requirement.
  - `{"kind": "rule-screen", "rule": ruleId, "when": rule.appliesWhen or None}`. It is emitted for each required rule whose targets include a screen or asset. Every path whose case satisfies `when` (all paths when `when` is `None`) must visit a screen that is, or contains, a target.
  - `{"kind": "notice", "id": notice id}`: some screen on every successful path must bind `product.notice.notice-<n>`. It is checked in coverage (E15).
- `enumerate_cases(conditions, limit=64) -> {"cases": [dict], "truncated": bool}`. Truncation is **blocking** in coverage.
- `evaluate(when, case)` uses `ontology_ux.parse_when`.
- `traverse(flow, case, *, max_steps=50) -> {"path": [id], "end": "terminal"|"dead-end"|"loop"|"ambiguous"}`:
  - It starts at `entry` and follows the transition(s) whose `when` holds.
  - Zero enabled transitions from a non-terminal → `dead-end`.
  - More than one enabled transition → `ambiguous`.
  - Revisiting a screen with the same case → `loop`.
- `flow_issues(flow, expectation, k) -> [{code, case?, detail}]`. The codes are:
  - `dead-end`, `ambiguous-branch`, `loop`
  - `terminal-state-missing`, `conditional-visit-missing`, `conditional-visit-leak`, `rule-screen-missing`
  - `unreachable-screen`: a member that no case visits
  - `unused-transition`
  - `case-enumeration-truncated`
- `mermaid(flow, case=None) -> str` renders `flowchart TD`, with the chosen case path in bold (`==>`).

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_design_flow.py
import copy
from design_fixtures import knowledge
from design_loop.flow import build_flow, enumerate_cases, expected, flow_issues, mermaid, traverse
from test_design_prd_extract import GOOD

K = knowledge()


def test_cases_and_paths():
    flow = build_flow(K, "savings-signup")
    exp = expected(GOOD, K)
    assert exp["conditions"] == ["eligible", "autoTransfer"]
    cases = enumerate_cases(exp["conditions"])["cases"]
    assert len(cases) == 4
    assert traverse(flow, {"eligible": False, "autoTransfer": True})["path"] == ["intro", "eligibility", "ineligible"]
    on = traverse(flow, {"eligible": True, "autoTransfer": True})["path"]
    assert "evidence-auto" in on and on[-1] == "done"
    assert "evidence-auto" not in traverse(flow, {"eligible": True, "autoTransfer": False})["path"]


def test_complete_seed_has_no_issues():
    assert flow_issues(build_flow(K, "savings-signup"), expected(GOOD, K), K) == []


def test_removing_the_conditional_parent_is_detected():
    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"] = [t for t in proc["transitions"] if t["dst"] != "evidence-auto" and t["src"] != "evidence-auto"]
    proc["transitions"].append({"id": "t-x", "src": "preferential", "dst": "confirm", "conditionId": None, "condition": "cond:autoTransfer"})
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert "conditional-visit-missing" in codes and "unreachable-screen" in codes


def test_missing_ineligible_terminal_is_detected():
    k = copy.deepcopy(K)
    proc = k.procedures["savings-signup"]
    proc["transitions"] = [t for t in proc["transitions"] if t["dst"] != "ineligible"]
    codes = {i["code"] for i in flow_issues(build_flow(k, "savings-signup"), expected(GOOD, k), k)}
    assert {"dead-end", "terminal-state-missing"} & codes


def test_truncated_enumeration_blocks():
    assert enumerate_cases([f"c{i}" for i in range(8)], limit=64)["truncated"]


def test_mermaid_highlights_case_path():
    text = mermaid(build_flow(K, "savings-signup"), {"eligible": True, "autoTransfer": True})
    assert text.startswith("flowchart TD") and "preferential ==> evidence-auto" in text
```

- [ ] **Step 2: Fail** → **Step 3: Implement** `flow.py` exactly per the interface above. Two details:
  - The expectation's `visit-when` asset is found in `k.assets` by its `conditions[*].when == "cond:<id>"` with effect `include`. None found → `expected` still emits the requirement, with `asset: None`, and `flow_issues` reports `conditional-visit-missing` for it.
  - `unused-transition` compares the set of transitions taken across all cases with all transitions.
- [ ] **Step 4: Pass** → **Step 5: Commit** `git commit -m "feat(design): flow traversal against an independent expected graph"`

### Task E9: Coverage route drives the generation strategy (U-03; Codex #22)

**Files:**
- Create: `platform/design_loop/route.py`
- Test: `platform/tests/test_design_route.py`

**Interfaces:**
- `classify(k, flow, screen_id) -> {"route", "score", "missing"}` scores one screen:
  - `template`: the screen has an approved `templateId`
  - `assets`: every asset is approved and has a code layer
  - `rules`: every rule targeting the screen is approved

  `score` is the number of covered dimensions, from 0 to 3. The route is `structured` at 3, `unstructured` at 0 (or when the screen has no template), and `hybrid` otherwise.
- `strategy(route) -> {"mode", "modelCalls"}`:

| Route | Mode | Model use |
|---|---|---|
| `structured` | `fill` | Deterministic composition from the approved screen assets and template slots, plus PRD bindings. No model call (`modelCalls: 0`); the model may only propose copy, through the separate edit path. |
| `hybrid` | `adapt` | Start from the `fill` composition. The model may only add or remove assets that `uxModel.conditions` names, or that the slot allow-lists permit. |
| `unstructured` | `compose` | The model composes from the allow-listed assets. A proposed unknown asset becomes a `new-asset-candidate` finding. |

`gui.generate_screen` (E11) requires the strategy argument.

- [ ] **Step 1: Tests**:
  - A seed screen is `structured` and `strategy(...)["modelCalls"] == 0`.
  - Removing the code layer from `amount-field` makes `amount` `hybrid` with `missing == {"assets": ["amount-field"]}`.
  - A screen without a template is `unstructured`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): per-screen coverage route selects generation strategy"`

### Task E10: Composition schema and full validation (G-01; Codex #10)

**Files:**
- Create: `platform/design_loop/composition.py`
- Test: `platform/tests/test_design_composition.py`

**Interfaces:**
- Composition v1:

```json
{"schemaVersion": 1, "screenId": "amount", "templateId": "single-task", "state": "default", "variant": "a",
 "surface": "page", "slots": {"header": [NODE], "body": [NODE], "footer": [NODE]}}
```

  A NODE is `{"id": "n1", "asset": "title", "props": {...}, "bind": {"children": "product.productName"}, "on": {"click": "t-..."}, "visibleWhen"?: {"when": "<when>", "negate": bool}, "children": [NODE]}`.
  - **Case-dependent visibility (review round 16, AJ1).** One composition per screen and state contains **every** node the page can show. A node whose presence depends on the case carries `visibleWhen`, in the `when` grammar. `visibleWhen` is valid only when it is derived from an applicable `uxModel.conditions` entry:
    - for an `effect: include` condition targeting the node's asset: `{when: <that condition's when>, negate: false}`
    - for `effect: exclude`: `{when: <same when>, negate: true}`. This is **whole-expression** negation, so `exclude when cond:a & cond:b` hides the node only when both hold (review round 17, AJ1). A per-literal rewrite is never used.
    - One shared evaluator `ontology_ux.visible(spec, case) = evaluate(spec.when, case) != spec.negate` is used by `composition.visible`, by coverage, and, emitted identically, by `src/logic/flow.ts` `visible(spec, caseState)`. Test: `exclude cond:a & cond:b` on the same screen and state gives the correct visibility for all four assignments of `(a, b)`, in the engine, in coverage and in the compiled page through Browser visibility rules.

    Any other `visibleWhen` → `visibility-unbound`, critical. A conditional target without `visibleWhen` → `visibility-missing`, critical.
  - `composition.visible(c, case) -> composition` returns the tree with nodes whose `visibleWhen` is false removed, together with their subtrees.
  - `on.click` names a flow transition id and is valid only on assets whose code has an `onClick` prop, or on Button.
  - `surface ∈ {page, bottom-sheet, full-popup, layer, tab}`.
- `validate(c, k, *, flow=None, binding_paths=frozenset(), mode="fill") -> list[Finding]`. A Finding is `{severity, code, path, message}`. The checks and their codes:

| Check | Code | Severity |
|---|---|---|
| Top-level keys exactly as above, `schemaVersion == 1` | `shape` | critical |
| Tree limits: depth ≤ 6, ≤ 200 nodes, id regex, unique ids | `tree-limit`, `bad-id`, `duplicate-id` | critical |
| Template known and screen's template matches | `unknown-template`, `template-mismatch` | critical |
| Slot keys equal the template's slots | `unknown-slot`, `missing-slot` | critical |
| Required slot non-empty | `slot-empty` | critical |
| Top-level slot nodes ∈ slot `allowed` (empty allow-lists cannot exist, E3) | `not-allowed` | critical |
| Nested child asset ∈ parent `composes` | `not-composed` | critical |
| Asset unknown (`mode == "compose"` → `new-asset-candidate` major with `approvable: False`) | `unknown-asset` | critical |
| Props: unknown / type / enum / required-missing | `unknown-prop`, `prop-type`, `prop-required` | critical |
| Prop both literal and bound | `prop-conflict` | critical |
| Bound path ∉ `binding_paths`, or the binding's value type (string or list-of-items from the PRD catalog, e.g. `product.summaryItems` → `list<{label, value}>`) differs from the prop type | `unknown-binding`, `binding-type` | critical |
| A composition sets a `callback` prop directly | `callback-literal` | critical |
| An `on.click` on a component whose adapter is not `action` | `unknown-transition` | critical |
| `on.click` values: `"next"` is valid on any screen with ≥ 1 outgoing transition (review round 3, N14). `"finish"` is valid **only** on terminal screens (review round 6, Z4). An explicit transition id must leave this screen. Anything else → `unknown-transition` | `unknown-transition` | critical |
| Any literal string, meaning every string-typed prop, text child and list-item field, that contains a financial quantity per the **shared grammar** `design_loop.financial.QUANTITY` (review round 28, AV2). The grammar is a number with optional sign and thousands separators, then an optional Korean magnitude (`천`, `만`, `억`, `조`), then a unit from `%p`, `%`, `bp`, `원`, `개월`, `년`, `일`, `세`, `회`, `배`. E7's value-unit check uses the same module | `literal-financial-value` | critical |
| `on.click` transition not in flow or not leaving this screen | `unknown-transition` | critical |
| `state ∉ STATES`, `surface` unknown | `bad-state`, `bad-surface` | critical |

  In `adapt` mode, the check also takes a base composition, as `validate(..., base=c0)`. Any change other than adding or removing an asset allowed by `uxModel.conditions` → `adapt-scope`, critical.
- `walk(c)` yields `(path, node, parent)`.
- `canonical(c)` / `digest(c)` form a composition-specific bounded serializer: sorted keys, UTF-8, no floats other than finite numbers, and a nesting limit of 32 JSON levels. That limit covers the 6-level node tree (up to 4 JSON levels per node). It is **not** `schema.digest`, whose 8-level limit rejects valid trees (`ontology_schema.py:51`; review round 3, F6). Manifests, edits and approval use `composition.digest` for composition hashes. A test hashes the deepest accepted tree.
- `text_of(c, values)` returns the visible literal text plus the resolved binding values. It is used by coverage.

- [ ] **Step 1: Tests.** Start from a valid fill composition for `amount`; `validate(...) == []`. Then add one test per code in the table, each mutating one field. Include explicitly:
  - an empty required slot
  - a nested `next-button` under `amount-field` (`not-composed`)
  - `bind: {"children": "product.unknown"}` (`unknown-binding`)
  - literal `"연 2.0%"`, `"999만원"` and `"1억원"`, and the same in a `Summary.items` value (`literal-financial-value`). The same values bound from the cited PRD pass. Through `verify_graph`, a generated or edited composition with an unbound `999만원` fails deterministically even with a passing fake judge
  - the same prop in `props` and `bind` (`prop-conflict`)
- [ ] **Step 2–4:** Fail → implement → pass. The binding catalog passed in tests is `set(prd_extract.bindings(GOOD))`.
- [ ] **Step 5: Commit** `git commit -m "feat(design): composition validation for slots, nesting, bindings and financial literals"`

### Task E11: GUI generation — fill, adapt, compose; variants and states (G-02, G-03)

**Files:**
- Create: `platform/design_loop/gui.py`
- Test: `platform/tests/test_design_gui.py`

**Interfaces:**
- `fill(k, flow, screen_id, binding_values) -> composition` is deterministic:
  - It uses the template slots in order. Each screen asset goes into the first slot whose allow-list contains it.
  - Required props are bound from the asset's `dataBindings`.
  - Text copy comes from the asset title, which is not a financial value.
  - On a **terminal** screen (no outgoing transition, e.g. `ineligible` and `done`), the CTA uses `on.click: "finish"` (review round 6, Z4). `App.tsx` handles it by setting `finished: true` and rendering the same page with a `testId="flow-finished"` status element. No successor is invented. Pages whose assets have no CTA simply omit it.
  - The CTA `on.click` is the generic action `"next"`, never a fixed transition id. At runtime the generated `src/logic/flow.ts` `next(screen, caseState)` selects the **single** transition whose `when` holds for the current case, per `flow.traverse`. Every eligibility outcome therefore has a working action (review round 2, N14). `on.click` may also name an explicit transition id, used for secondary buttons such as "이전".
- `generate_screen(screen_id, k, flow, deps, *, strategy, binding_paths, variants=3, retries=1) -> {"screenId", "variants": [...]}`:
  - `fill` mode: variants are deterministic layout axes. The kit's `variationAxes` from `catalog.json`, such as `Stack.gap` or `Panel.tone`, give variants a/b/c with no model call.
  - `adapt` / `compose` mode: model calls, each validated with `composition.validate(mode=…)`. The findings are appended on retry. Every attempt is kept in `attempts` (GEN-05).
  - A missing `deps["generate"]` in a mode that needs it → `{"blocked": "model-unavailable"}`.
- `states_for(screen_id, k, flow, expectation) -> [state]` returns:
  - `default`
  - the union of the asset `requiredStates`
  - any state required by `terminal-state` expectations on this screen
- `generate_states(base, states, k, deps, *, strategy)`: for `fill`, it applies `effect: state` conditions **and each asset's `stateProps[state]`** deterministically. For each node whose asset declares the state, it overlays the literal props. The result is validated, and any prop not in the asset's code-layer props is rejected. Otherwise it asks the model with the same validation rules.
  - Test (review round 5, Y2): an asset with `stateProps: {"error": {"tone": "danger"}}` is published through `publish_candidate`, loaded through `from_snapshot`, and the error-state composition carries `tone: "danger"`. It then compiles and renders in the node-enabled test.
  - `stateProps: "invalid"` is rejected at `validate_ux_model`.

- [ ] **Step 1: Tests:**
  - `fill` produces a composition that validates.
  - Structured variants make zero model calls; count them with a fake that raises if called.
  - `compose` retries once with the findings, and keeps both attempts.
  - A missing model gives `blocked`.
  - `states_for("eligibility")` contains `ineligible`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): strategy-driven layout and state variants"`

### Task E12: React project generation and compile (R-01, R-02; Codex #13, #15)

**Files:**
- Create: `platform/design_loop/react_project.py`
- Test: `platform/tests/test_design_react_project.py`

**Interfaces:**
- `project(flow, screens: dict[(screenId, state), composition], k, prd_bindings, *, cases) -> dict[path, str]` (keyed by screen **and** state) emits a react-kit project that satisfies `react-kit/policy.cjs` (at most 24 files, 128 KiB, `SOURCE_PATH`):
  - `src/pages/<slug>.tsx`, one per screen for its default state. **Each approved non-default state** gets its own page, `src/pages/<slug>--<state>.tsx` (review round 2, N16). The `slug` is the persistent **UI page id** from the screen registry (E17), not the canonical node id (review round 6, Z1). Canonical ids are `node-` + 48 hex characters (`ontology_schema.py:82`), which would exceed the policy's 64-character `pageId` limit (`policy.cjs:123`) once a state suffix is added.
  - `Registry.page_id(screen_node_id)` returns the Screen's `properties.pageId` **unchanged** when it matches the kit `pageId` pattern `^[a-z][a-z0-9-]{0,63}$`. That includes published notice pages up to 64 characters, whose identity must be preserved (`collaboration.py:187`, `react_generation.py:182`; review round 30, AX1). Otherwise it returns `p-` + the first 12 hex characters of `digest(screen_node_id)`.
  - A separate **short key** `Registry.page_key(screen_node_id)` = `k<n>`, a persisted ordinal of ≤ 6 characters, is used for every **derived** id: state pages `k<n>--<state>` (their `pageId` and page file slug), test ids `k<n>-cta`, `k<n>-f<m>`, `k<n>-n<m>`, and radio options `k<n>-f<m>-o<j>`. Derived ids therefore stay short even when the published page id is 64 characters. The page root keeps `testId = pageId`.
  - Test: 40-, 41- and 64-character published page ids go through contract approval (notice coverage), compilation and Browser verification.
  - It is persisted, and collisions are checked, with a numeric suffix appended.
  - Derived ids use the short key, so `k<n>--<state>` is always far below 64 characters, whatever the length of the published page id (AX1).
  - The node-enabled compile test runs on a knowledge view built from a **real** `publish_candidate` + review fixture with canonical ids, including a state page. The file count stays ≤ 24, and an overflow raises `ValueError("project-file-limit")` rather than dropping states.
  - A node with `visibleWhen` is emitted as `{visible({when: "<expr>", negate: <bool>}, caseState) && (<Comp …/>)}`. `when` is the grammar evaluator in `src/logic/flow.ts`. `caseState` is App's current case, derived from the form state and the case fixture, and it is passed to every page as a prop (`PageProps.caseState`). This is the case-dependent rendering step (AJ1).
  - Callback props are emitted only by trusted adapters:
    - `controlled-text` / `controlled-choice` → `value={form["<nodeId>"]} onChange={v => setField("<nodeId>", v)}`
    - `controlled-bool` → `checked={Boolean(form["<nodeId>"])} onChange={v => setField("<nodeId>", v)}`, matching `CheckboxProps.checked` (`react-kit/ui/types.ts:23`; review round 3, N13)
    - The form state lives in `App.tsx`. Every page has the signature `export default function <Pascal>Page({ data, form, setField, go }: PageProps)`, and `PageProps` is declared in `src/logic/types.ts`.
    - `action` → `onClick={() => go("next")}`, or `go("<transitionId>")`
  - List props are emitted from bindings as `items={data["product.summaryItems"]}`, typed through `src/logic/data.ts`.
  - Each page exports `export const meta = { sid, type, dver, status, level1, level2, level3 } as const;`, with values from the registry and convention. The meta is inside the compiled, approved source, so the handoff's `convention/screens.json` only indexes it (E17). Each page does `export default function <Pascal>Page({ data, go }: PageProps)`.
    - It renders the template's code component with slot node props. For `Screen`, it renders `<Screen pageId="<pageId>" title={...} width="mobile">` with the slots in order inside a `<Stack>`.
    - `pageId` is the one attribute emitted as a **literal JSX string**, because `react-kit/policy.cjs:121-123` requires it (review round 3, F7). It is validated first against `^[a-z][a-z0-9-]{0,63}$`: `<slug>` for the default state, `<slug>--<state>` for state pages. It is unique across the project. Every other text prop uses expression attributes.
    - Kit components: literal string props become `prop={"…"}` **expression attributes** built with `json.dumps(value, ensure_ascii=False)`, so quotes and backslashes can never break JSX (Codex #15).
    - `childrenProp` text renders as `{"…"}`.
    - Bound props render as `{data["product.baseRate"]}`.
    - `on.click` renders as `onClick={() => go("t-…")}`.
    - Every node gets `testId` from `Registry.test_ids`: `k<p>-n<n>`, or `k<p>-f<n>` for controlled inputs, where `k<p>` is the page short key, which is always ≤ 80 characters (AU1). All kit components support `testId`.
  - `src/logic/flow.ts`: the transitions table, and `next(transitionId, caseState)` that returns the destination screen.
  - `src/logic/data.ts`: `export const data = {…}`, the PRD binding values, verbatim strings.
  - `src/logic/cases.ts`: the case list, used by the preview case selector.
  - `src/App.tsx`: `useState` for the current screen and case. It **always** renders a `Select` labelled `검증용 케이스` with `testId="case-select"`, whose options are the `"<caseIndex>:<screenId>:<state>[:empty]"` entries, even for a single case, because it also selects screens, states and empty forms (review round 34, BB1). It also renders the current page. Test: a single-case, multi-screen project passes compilation, Browser verification and the exported `npm test`.
- `asset_gallery(k, binding_values) -> dict[path, str]` renders each renderable asset once, with the adapter wiring and sample values of each prop type. It is used by the type check above, and by C5 to produce each asset's `layers.gui` snapshot.
- `compile_request(files, *, catalog_hash, contract) -> dict` builds the `compile.cjs` request. `compile_local(request) -> dict` runs `react-kit/compile.cjs` through `local_runner.run_node`. B2 replaces this runner with Code Interpreter, keeping the request and response shapes.
- Delivery fidelity is by construction. The same `files` bytes are:
  - compiled for preview
  - compiled for Browser verification
  - approved
  - rebuilt at release (`releases.py:101`, `process_release`)
  - remapped for the customer-convention handoff (E17)

- [ ] **Step 1: Tests** (the compile tests skip without `react-kit/node_modules`)

```python
# platform/tests/test_design_react_project.py
import json, shutil
import pytest
from design_fixtures import ROOT, knowledge
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.prd_extract import bindings
from design_loop.react_project import compile_local, compile_request, project
from design_loop.gui import fill
from test_design_prd_extract import GOOD

K = knowledge()
needs = pytest.mark.skipif(not (ROOT / "react-kit" / "node_modules").exists() or not shutil.which("node"), reason="react-kit")


def build():
    flow = build_flow(K, "savings-signup")
    screens = {(s, "default"): fill(K, flow, s, bindings(GOOD)) for s in flow["screens"]}
    cases = enumerate_cases(expected(GOOD, K)["conditions"])["cases"]
    return project(flow, screens, K, bindings(GOOD), cases=cases)


def test_project_respects_source_policy_shape():
    files = build()
    assert "src/App.tsx" in files and len(files) <= 24
    assert sum(len(v.encode()) for v in files.values()) <= 128 * 1024
    assert all(p == "src/App.tsx" or p.startswith("src/pages/") or p.startswith("src/logic/") for p in files)


@needs
def test_each_seed_asset_mapping_typechecks_against_the_real_kit():
    # One page that renders every seed asset once with sample values of each declared prop type, compiled with the
    # real kit types (react-kit/ui/types.ts). A mapping that declares Summary.items as "string" fails the types gate.
    from design_loop.react_project import asset_gallery, kit_catalog_hash
    files = asset_gallery(K, bindings(GOOD))            # src/App.tsx + src/pages/gallery.tsx
    out = compile_local(compile_request(files, catalog_hash=kit_catalog_hash(), contract=None))
    assert out["gates"]["types"]["status"] == "pass", out.get("diagnostics")


def test_quotes_cannot_break_jsx():
    files = build()
    page = files["src/pages/intro.tsx"]
    assert 'title={"' in page or "title={data[" in page


@needs
def test_generated_project_compiles_with_the_real_kit():
    catalog = json.loads((ROOT / "react-kit" / "catalog.json").read_text())
    from design_loop.react_project import kit_catalog_hash
    out = compile_local(compile_request(build(), catalog_hash=kit_catalog_hash(), contract=None))
    assert out["ok"], out.get("diagnostics")
    assert all(out["gates"][g]["status"] == "pass" for g in ("policy", "types", "build", "components"))


@needs
def test_injected_quote_in_copy_still_compiles():
    files = build()
    files["src/pages/intro.tsx"] = files["src/pages/intro.tsx"].replace('title={"', 'title={"Say \\"yes\\" ', 1)
    assert compile_local(compile_request(files, catalog_hash=__import__("design_loop.react_project", fromlist=["x"]).kit_catalog_hash(), contract=None))["ok"]
```

`kit_catalog_hash()` gets the pinned hash by running `node -e "console.log(require('./manifest.cjs').catalog().hash)"` in `react-kit` through `local_runner`, using a small fixed `hash.cjs` script created in this task. Codegen never computes the catalog hash itself.

- [ ] **Step 2–4:** Fail → implement `project`, `compile_request`, `compile_local`, `kit_catalog_hash` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): react-kit project generation from compositions and flow"`


### Task E12a: Kit text scaling for large-text verification (V-02; review round 3, F11)

**Files:**
- Modify: `platform/react-kit/ui/tokens.css`, `platform/react-kit/catalog.json` (version bump)
- Modify: `platform/workspace/browser.py` (delegating to `browser_core`, B2 S5)
- Test: `platform/react-kit/test/text-scale.test.cjs`, `platform/tests/test_workspace_browser.py`

**Interfaces:**
- Every **`font-size`** in `tokens.css` becomes `calc(var(--studio-text-scale, 1) * <n>px)`. **Unitless `line-height` values stay as they are**: body `1.6` and button `1.4` are multipliers that already scale with font size (`tokens.css:34,75`). Only px line-heights, if any, are scaled (review round 4, X8). A test compares **every** computed style of each kit component at scale 1 with the pre-change kit (`getComputedStyle` over all properties) and requires equality.
- `catalog.json` `version` is incremented, so `catalogHash` changes. Existing approved runs become historical (`AGENTCORE_CONTRACT.md:379-382`), and this is stated in the PR.
- `evaluate_bundle(..., text_scale=1.0)`: at `2.0` it adds `:root{--studio-text-scale:2}` through the verifier's init script. The page cannot author it. The result is `largeText: {status, overflow: [testId], unscaled: [testId]}`, where `unscaled` lists text elements whose computed size did not change.

**Steps:**
- [ ] **Step 1: Tests:**
  - At scale 1, computed sizes equal the previous px values.
  - At scale 2, `Text` and `Button` computed sizes double.
  - A fixture with a fixed-width container reports overflow.
  - The existing browser tests pass unchanged at the default scale.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(react-kit): scalable type tokens and large-text verification pass"`

### Task E12b: Exported test runner visibility semantics (review round 29, AW1)

The Studio verifier treats an **absent** target as satisfying `expectVisible false` (`browser.py:141-143`). The exported project's runner instead waits for attachment first (`react-kit/templates/flow.test.cjs:69`). A conditionally unmounted node (E12 `visibleWhen`) would therefore pass in the Studio but fail the delivered `npm test`.

**Files:** Modify `platform/react-kit/templates/flow.test.cjs`. Test in `platform/react-kit/test/flow-template.test.cjs`.

**Change:** before `waitFor({state: 'attached'})`, when `step.action === 'expectVisible' && step.value === false`, poll `target.count()` until the step timeout. A count of 0 → the step passes, which is the same as the verifier. A count of 1 → assert it is not visible. A count above 1 → fail with "Unique target required", so duplicate-target rejection is preserved. All other actions are unchanged. The catalog and template hashes change, as in E12a.

**Tests:** these run the real exported project from `sourceProject`, with `npm test`, against three pages:
- (a) an absent conditional node → pass
- (b) a node that is present but hidden → pass
- (c) a node that is wrongly visible → fail

A page with two nodes sharing the target still fails. E17's handoff verification adds `npm test` alongside `typecheck` and `build`.

### Task E13: Browser contract from PRD and flow (V-02 tester input)

**Files:**
- Create: `platform/design_loop/contract.py`
- Test: `platform/tests/test_design_contract.py`

**Interfaces:**
- **The contract is composition-independent and frozen at flow approval (review round 7, AA4).** Its signature is `derive(prd, flow, k, registry, published_pages, *, cases, criteria, viewport={"width": 390, "height": 844}) -> contract`, with no compositions argument. Every target is a **deterministic test id** that E12 must emit, and that E10 validates the presence of:
  - page root: `<pageId>`
  - primary action: `k<p>-cta`
  - controlled inputs: `k<p>-f<n>`, where `k<p>` is the page short key (AX1)
  - conditional or targeted nodes: `k<p>-n<n>`
  - radio options: `k<p>-f<n>-o<j>`
  - the terminal marker: `flow-finished`

  **One mapping for every consumer** (review round 32, AZ1). The only target formulas are the page root, which is the literal `pageId`, and `Registry.test_ids(screen)`, which yields `k<p>-cta`, `k<p>-f<n>`, `k<p>-n<n>` and `k<p>-f<n>-o<j>`. E12 codegen, E13 contract derivation (page, branch, visibility and interaction rules), E14 edit exclusion and the B2 S5 boxes and checkpoints all call it, and no module builds a target string itself. Test: a complete generated contract where `pageId != page_key`, both an ordinary id and a 64-character published id, passes the real verifier on the compiled project.

  **Bounded test ids** (review round 27, AU1). The DSL limits targets to `[A-Za-z][A-Za-z0-9_-]{0,79}` (`rules.py:8,171`), so names are never built from binding field names or node ids. `Registry.test_ids(screen)` allocates short ordinals: `f<n>` in the order binding fields first appear, and `n<n>` for nodes. Page ids are ≤ 40 characters, so every id is ≤ 50 characters. The allocation is persisted with the registry and collision-checked. The same map is used by codegen (E12), the contract (E13), edit subtree exclusion (E14) and Browser boxes and checkpoints. A test uses a 128-character binding field and canonical node ids through contract derivation and `validate_contract`.

  A composition missing a contract target → `missing-contract-target`, which is critical.
  - C1 derives the contract once, approves it and stores its hash.
  - C3, repairs, compose and release pass **that exact normalized contract** to every compile (`project.cjs:89` embeds it in `test/contract.json`, so it is part of `sourceHash`) and every Browser run. They never re-derive it.
  - A needed contract change (a PRD or flow change) requires a new flow approval.
  - Test: compile two different variants with the frozen contract, and assert the report `contractHash` equals the run's `contractHash` for both, and that the real `_run_approve` succeeds.
- `derive` returns **one** contract (review round 3, F8). The existing run, approval and release path binds exactly one contract and one contract hash per run (`rules.py:244`, `releases.py:22`), and the contract is part of the source archive hash (`react-kit/project.cjs:89`). The contract must pass `workspace/rules.validate_contract`, which allows ≤ 20 rules and ≤ 20 steps per rule.
  - **Preview harness.** `App.tsx` exposes a `Select` with testId `case-select`, whose options are `"<caseIndex>:<screenId>:<state>"`. It starts any case at any screen **and state page**, with the prior form state filled from the case fixture.
  - **Rule budget.** There is one rule per `(screen, state)` page (N16). Each rule uses a representative case in which that page is reachable, and contains:
    1. `select`
    2. `expectVisible` of the page root
    3. every required `expectText` for that page
    4. for default pages, the `click` of the enabled action
    5. `expectVisible` of the next page. On **terminal pages**, the rule instead clicks `finish` (when present) and expects `flow-finished`, without expecting a successor (review round 6, Z4).

    **Branch outcomes are packed** (review round 18, AL1):
    - Each (conditional transition, case taking it) check is a 3-step sequence: `select "<i>:<screen>:default"` → `click k<p>-cta` → `expectVisible <destination>`.
    - All sequences for the same branching screen are appended into **one rule**. When the 20-step limit is reached, the rule is split, so no check is dropped.
    - The seed's `eligibility` screen has 4 checks (12 steps) and `preferential` has 2 (6 steps), which gives **2 rules**.

    For each conditional node (`visibleWhen`), the visibility outcomes are packed the same way. The rule checks one case where the expression holds and one where it does not, **on the same screen and state**: `select` + `expectVisible k<p>-n<n> true`, then `select` + `expectVisible … false`, which is 4 steps (AJ1, AL1). Nodes on the same page share a rule up to 20 steps. The seed's `branch-select` gives **1 rule**.

    Actions are **component-specific** (review round 30, AX2):
    - `Input` (controlled-text): `fill` → `expectValue`
    - `Checkbox` (controlled-bool): `check` → `expectChecked`
    - `Select` (controlled-choice): `select` on the `<select>` target → `expectValue`
    - `RadioGroup` (controlled-choice): `check` on the **option** target `k<p>-f<n>-o<j>` → `expectChecked` on it, and `expectChecked false` on another option. The kit gives each radio `data-testid = ${testId}-${value}` (`react-kit/ui/index.tsx:111-128`). E12 therefore emits RadioGroup and Select option **values** as ordinals `o1`, `o2`, …, with the display labels unchanged. `src/logic/data.ts` maps them to business values, so the targets stay within the DSL pattern.

    Finally there is **one interaction rule per interactive page**: a page with a controlled-* adapter or a required consent (review round 4, X5). It starts with `select "<i>:<screen>:default:empty"`, which means **no prefilled form state**, and then:
    1. `expectEnabled next false`, when the page has a required input or consent guard
    2. `fill`/`check` of every required control with the case fixture values
    3. `expectValue`/`expectChecked` of those controls
    4. `expectEnabled next true`
    5. `click`
    6. `expectVisible` of the destination

    One invalid-input step (`fill` with an out-of-range amount, then `expectText` of the error) is added when the asset declares `min`/`max`. The seed adds 3 such rules (amount, terms, preferential). The seed total is therefore 9 default-page + 1 state-page + 2 packed branch + 1 packed visibility + 3 interaction = **16 rules**, plus one rule per required published page and per back edge (none in the seed). The limit is 20 (`rules.py:120`), and anything beyond it → `contract-capacity`. Every check the unpacked form would contain is present: the test enumerates the expected (transition, case) and (node, outcome) checks and asserts each appears exactly once as a step sequence (AL1).

    Page rules: 9 default pages plus 1 `eligibility:ineligible` state page, 10 rules, each ≤ 20 steps. The packed branch, visibility and interaction rules described here add to them.
  - **Overflow.** If a page needs more than 20 steps, or the design needs more than 20 rules, `derive` returns the finding `contract-capacity`, which is critical. The design must be split into separate design records, one per procedure segment. Assertions are never dropped and there is never more than one contract per run.
  - **Sources.** Each rule's `source` is `{kind: "inferred", quote, page}`, carrying the PRD citation page and a **bounded quote excerpt**. `rules.py:152` limits source quotes to 2 000 characters, while the assertion value may be 4 000. The excerpt is the citation quote cut at the last whitespace before 2 000 characters and suffixed with `…`. The full citation stays in the PRD and approval manifest (review round 29, AW2). Test: a 4 000-character cited notice produces a valid contract with a ≤ 2 000-character source quote and the complete 4 000-character assertion value, which passes `validate_contract` and `/contracts/{id}/approve`. It does not use `kind: "explicit"`: explicit sources require `assetId` and a matching `guideRefs` entry (`rules.py:141-159`). The exact citation binding lives in the PRD and the approval manifest.
  - **Published mandatory pages (review round 5, Y3).** The product's published context has `pages[*] = {pageId, required, content}` (`criteria.py:16-24`, `react_generation.py:176-185`). Four things follow from it:
    - `flow.expected` adds `{"kind": "published-page", "pageId", "content"}` for every required page.
    - The ontology procedure must contain a Screen whose `pageId` equals it. Otherwise → `published-page-missing`, which is critical.
    - E12 gives that page's `Screen` the literal `pageId` **and** `testId=<pageId>`.
    - E13 adds a required rule with **one** `expectText target=<pageId> match=contains value=<full normalized content>` step (review round 7, Z5).
    - This task extends the DSL: `rules.py:176` raises the `expectText`/`expectValue` value limit from 2 000 to **4 000** characters, the published-notice limit (`collaboration.py:132`). `REACT_CONTRACT.md` is updated.
    - Existing contracts remain valid, and `contract_hash` is unchanged for existing values.
    - `notice_coverage_issues` (`criteria.py:16-24`) then sees the complete content in one step. That also covers unbroken text with no whitespace.
    - Test: 4 000-character notices, both with spaces and unbroken, pass `validate_contract`, `notice_coverage_issues`, the real `/contracts/{id}/approve`, and a Browser `evaluate_bundle` check. A 4 001-character value is rejected.

    The existing `notice_coverage_issues` approval gate (`http.py:799-806`) and the generation observation check (`react_generation.py:176-185`, which requires `actualComponent == "Screen"`) therefore pass through the **normal** contract path. Neither is skipped. Test: a published product with a required notice page `notice-consent` goes through the real `/contracts/{id}/approve` path and succeeds, while a flow without that Screen yields `published-page-missing`.
  - **Criteria.** The contract carries the **top-level** criteria fields that `workspace/rules.py:16-17` and `criteria.resolve_generation_context` read (`criteria.py:35-45`): `projectId`, `productId`, `guidelineId`, `guidelineAssetId`, `catalogHash`, `ontologyHash`.
    - `ontologyHash` keeps its existing meaning: the **published product projection hash** that `collaboration.is_current` compares (`collaboration.py:590-609`, `http.py:717`). It is copied from the product's current published context.
    - `assetIds` includes `guidelineAssetId`, as `rules.py:102` requires.
    - The design procedure snapshot is bound separately, as `designSnapshotHash`, in the contract (added to `validate_contract`'s closed field set in this task), the run and the approval manifest (review round 4, X2). A test asserts `collaboration.is_current(scope, contract)` stays true for a derived contract.
  - **Large text** (V-02 큰글씨; review round 3, F11). This is not a contract field, because `rules.py` drops unknown fields.
    - The kit's px-based type tokens (`react-kit/ui/tokens.css:34-86`) are changed to `calc(var(--studio-text-scale, 1) * <n>px)` in a separate kit task, E12a. This bumps the catalog version and hash.
    - The verifier (B2 S5 and `workspace/browser_core`) takes `text_scale=2` for a second pass.
    - The second pass asserts that every text node's **computed font-size doubled** and that no required element overflows its container.
    - It reports `largeText: {status, overflow, unscaled}`.
    - `verify_graph` treats a missing, failed or unscaled large-text result as a required tester failure.
- `contract_hash` uses `workspace.rules.contract_hash`.

- [ ] **Step 1: Tests:**
  - `derive(...)` returns one contract that passes the real `workspace.rules.validate_contract` (with `asset_texts=None`), with **16** rules for the seed. The test enumerates all 6 (transition, case) branch checks and both visibility outcomes and finds each as a step sequence, and a synthetic 21-rule design → `contract-capacity`.
  - Every rule has ≤ 20 steps.
  - The union of `expectText` values equals the coverage requirement set.
  - The `eligibility:ineligible` state page has its own rule.
  - Both eligibility branch outcomes are exercised through the generic action.
  - A synthetic 30-page design returns `contract-capacity`.
  - A generated contract for a Select and a RadioGroup page passes the real verifier. A no-op `setField` mutation on each fails through `expectValue` or `expectChecked` (AX2).
  - Interaction rules with prefill disabled catch two bugs:
    - a generated page whose `setField` is a no-op fails `expectValue`
    - a page whose consent guard was removed fails `expectEnabled next false`

    Both run through `workspace.browser.evaluate_bundle` in the node-enabled test.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): derive browser verification contract from PRD and flow"`

### Task E14: Targeted edit with full stability checks (G-04; Codex #12)

**Files:**
- Create: `platform/design_loop/edit.py`
- Test: `platform/tests/test_design_edit.py`

**Interfaces:**
- `locate(c, target_id) -> (slot, path_indices)`. A missing target raises `KeyError`.
- `apply_edit(c, target_id, replacement) -> dict` requires `replacement.id == target_id`.
- `stability(before, after, target_id) -> {"stable", "changes": [path]}`:
  - It compares the **complete** documents: all top-level fields plus every node outside the target subtree, including order, props, bind, on and children.
  - The target subtree is replaced by a mask carrying the target's location on both sides.
  - The target must still exist, **at the same slot and index path** (`target-moved`).
  - A deleted target → `target-missing`.
  - Changes to `templateId`, `surface`, `state` or `variant` are reported as top-level changes.
- `layout_stable(before, after, *, excluded_testids, tolerance_px=1) -> {"stable", "moved", "missing", "blocked"}`:
  - `before` and `after` are the B2 S5 checkpoint maps `{"<ruleId>:<step>": {testId: [x, y, w, h]}}`.
  - `excluded_testids = subtree_testids(c, target_id)`, which is every testId generated for the target node and its descendants (the registry-allocated `k<p>-n<n>`/`k<p>-f<n>` ids).
  - For each checkpoint present in `before`, every non-excluded testId must be present in `after` (otherwise it is in `missing`) and within tolerance (otherwise in `moved`).
  - A checkpoint absent from `after` → `blocked`.

  This detects surrounding layout shifts on every screen and state that the rules visit (review round 3, #12).
- `edit(c, target_id, instruction, k, deps, *, flow, binding_paths)`. The model returns only the replacement node. The result is `{composition, findings, stability}`, and approval in C requires both `stability.stable` and a later `layout_stable` pass.

- [ ] **Step 1: Tests.** The first three cases are the ones the old plan missed:
  - a template change → unstable
  - a surface change → unstable
  - deleting the target → `target-missing`

  Also cover:
  - moving the target to another slot → `target-moved`
  - an outside prop change → the path is listed
  - an id change from the model → `target-id-changed`
  - `layout_stable` with a neighbour moved by 5px → unstable. A missing neighbour → `missing`. A missing checkpoint → `blocked`. A moved descendant of the target is ignored.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): complete-document and layout-box stability for targeted edits"`

### Task E15: Coverage verifier (V-03; Codex #9, #11)

**Files:**
- Create: `platform/design_loop/coverage.py`
- Test: `platform/tests/test_design_coverage.py`

**Interfaces:**
- `check(prd, flow, expectation, screens: dict[(screenId, state), composition], k, binding_values) -> {"findings", "blocked": bool}`. Every per-case check below evaluates **`composition.visible(screens[...], case)`**, the tree actually shown in that case, not the full tree (review round 16, AJ1):
  - Every `flow_issues` result → critical.
  - For every case path, and every screen on it: a default composition is required (`missing-screen`, critical).
  - Every required PRD item bound to that screen's purpose must appear in `text_of(...)` (`missing-required`, critical).
  - Every `effect: include` condition that holds in the case must have its target present (`missing-conditional`, critical).
  - `effect: exclude` targets must be absent (`forbidden-present`, critical).
  - Required rule targets must be present (`missing-rule-target`, critical, with `ruleId`).
  - Notices must be bound on the confirm screen, or on a screen the rule designates (`missing-notice`, critical).
  - Each state in `states_for` must have a composition (`missing-state`, major).
  - `blocked` is true when case enumeration was truncated or `k.complete` is false. A blocked coverage result can never make a screen approvable.

- [ ] **Step 1: Tests:**
  - The complete seed gives no findings.
  - Removing `evidence-auto`'s composition gives `missing-screen`.
  - Removing `branch-select` from the auto-transfer screen gives `missing-conditional`.
  - **Same screen, both outcomes (AJ1):**
    - with `branch-select` carrying `visibleWhen: "cond:autoTransfer"`, both `autoTransfer` cases pass coverage
    - removing `visibleWhen` → `visibility-missing`, and coverage fails for the `!autoTransfer` case through the exclude condition
    - in the node-enabled test, the compiled page shows the node for one case and hides it for the other, through the Browser visibility rules
  - An incomplete knowledge view gives `blocked`.
  - Changing a notice binding gives `missing-notice`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): coverage over cases, conditions, rules and notices"`

### Task E16: Verification graph — fail-closed master and HITL metrics (V-01, V-02, V-04, V-05; Codex #11, #22, #23)

**Files:**
- Create: `platform/design_loop/verify_graph.py`
- Test: `platform/tests/test_design_verify_graph.py`

**Interfaces:**
- `verify(bundle, k, deps, *, mode="fill", required=("reviewer", "tester", "coverage")) -> Result`. Every finding carries a `role` (`reviewer`, `tester` or `coverage`):
  - `bundle` is `{prd, flow, expectation, screens, binding_values, contract, browser_report}`.
  - **Reviewer:** deterministic `composition.validate` plus `deps["llm_judge"]` for each checklist item. This is the key `engine.gate.design_deps()` returns (`engine/gate.py:259`); see review round 2, #11. A missing `llm_judge` means the reviewer is `unavailable`. Judge verdicts of `fail` are critical or major per the item's severity. `incomplete` means `unavailable` for that item.
  - **Tester:** `bundle["browser_report"]` must be the **assembled** report from `design_loop.evidence.assemble(build, browser)` (review round 4, X6):
    - It mirrors `workspace/react_runtime.py:77-84` and `react_generation.py:189`: `{**browser_report, "build": {build minus files/previewHtml/sourceFiles}, "sourceHash", "bundleHash", "catalogHash", "artifactSha256": sha256(previewHtml bytes), "contractHash": rules.contract_hash(contract), "outputType": "react"}`. These are the report fields `_run_approve` compares (`http.py:1059-1101`; review round 5, X6).
    - A test runs the real `_run_approve` over a round whose report came from `assemble`.
    - It requires `browser.bundleHash == build.bundleHash` and `build.catalogHash == contract.catalogHash`.
    - It raises on any mismatch.

    `react_quality.react_report_passes(contract, report)` must then hold. A missing report means the tester is `unavailable`. Each failed required check → critical. Accessibility violations → major.
  - **Coverage:** from E15.
  - `Result` is `{"verdict": "pass"|"fail"|"blocked", "approvable": bool, "findings", "unavailable": [role], "roles": {role: summary}}`.
    - **Judge context (review round 4, X10).** `design_deps.llm_judge` reads `context["prd"]["steps"]` and `context["flowText"]`, truncating it to 6 000 characters (`engine/gate.py:238-247`). `verify_graph.review_context(bundle, screen_id, state)` therefore builds, **per page**:
    - `{"prd": {"steps": [{"id", "title"} for each flow screen]}, "flowText": "[<screen>:<state>] " + composition.text_of(page)}`
    - `flowText` is ≤ 6 000 characters. A longer page → the item is `incomplete` for that page, meaning reviewer unavailable, never truncated silently.
  - **Judge budget** (review round 6, Z2):
    - Items with `method: "rule"` run deterministically through **`design_loop.rules_v2`**, with no model call (review round 7, AA2). The legacy `rules.run_rule` reads `flow.steps[*].html` and `prd.steps` (`rules.py:28-52`) and yields vacuous passes on the new model, so it is not used.
      - `rules_v2` ports each predicate the base sets use (`step_exists`, `steps_exist`, `text_present`, `transitions_match_buttons`, `required_present`, `a11y_labels`, `a11y_structure`) to the new inputs: the flow `screens`/`transitions` and canonical ids through `Registry.page_id`, the page compositions, `text_of` with **resolved** PRD bindings, and the Browser checkpoints for a11y.
      - `$spec.*` arguments resolve against `prd_extract.bindings(prd)`.
      - An unresolvable argument, or an empty target set, → `incomplete`, never pass.
      - A base item whose `fn` has no v2 port becomes an `llm` item.
      - Tests cover a missing notice (`text_present` fails), a missing step (`steps_exist` fails), a valid complete seed (all pass) and an empty-target case (`incomplete`).
    - `method: "llm"` items are judged **in one batched call per distinct reviewer-input hash**: `batch_judge(items, page_context)`, built on `deps["generate"]` with the same boundary. It returns a verdict per item id, and a missing or unparsable item → `incomplete`.
    - The cache key (review rounds 7 and 8, AA5) is `review_key = digest({judgeSystem, judgeUser, model, promptVersion, profileHash, ruleRevisions})`. `judgeSystem`/`judgeUser` are the **exact** payload strings `batch_judge` would send, so page text with resolved values, the flow step order, item texts and the context all enter the key. `ruleRevisions` is `{ruleId: [revision, contentHash]}`. Any change to anything the judge consumes changes the key. The composition IR alone is not enough, because the same IR can render different PRD values, and item or rule text can change.
    - Stored judgments are `{reviewKey, verdicts, receiptHash, admissions}`.
    - `design.compose` reuses a judgment only when the recomputed `reviewKey` matches **and** its lineage re-verifies. Otherwise compose is `needs_changes` with `review-evidence-missing`.
    - Test: change a PRD value, or a rule revision, while the composition IR stays unchanged, and assert compose is blocked.
    - The worst case per candidate is pages × 1 call. The C3 admission preflight computes `variants × (generate calls + distinct pages) + repairs × (…)` against `maxCallsByOperation` and rejects with `call-budget` before any paid work.
    - The test runs through the real ledger `intent`/`outcome` wrappers with the seed: 3 fill variants × 10 pages → at most 30 judge calls (fewer when pages are identical across variants) + 0 generate calls, within `maxCallsByOperation["design.generate"] = 120` (review round 8, Z2).
  - Each checklist item is judged once per page it targets, with `item.target` set to `screen`. The checklist comes from `verify_graph.design_checklist(prd, k)`, a new adapter (review round 5, X10). The existing `build_checklist` reads the legacy ProductSpec fields (`checklist.py:21-49`, `models.py:38`), so it returns zero items for the new PRD. The adapter builds:
    - the applicable base items from the **packaged, versioned resource** `design_loop/resources/checklists.json`, copied from `seed/design/checklists.json` with its sha256 recorded in `design_loop/resources/manifest.json` and in the job's backend configuration revision (review round 6, Z6). They are matched on `appliesTo` against `prd.productType.value` and `prd.category.value`.
    - one item per preferential condition
    - one per notice
    - one per required PolicyRule

    Each item carries `source`, either the base set id or a PRD/rule citation. A test asserts it is non-empty for the seed, and that an empty checklist → the reviewer is `unavailable`.
  - A test inspects the fake gate's received `user` payload for one page. It contains that page's text and the step list, and no other page's text.
  - `blocked` when any required role is unavailable, or coverage is blocked.
    - `fail` when there is any critical finding.
    - `pass` otherwise.
    - `approvable` is `verdict == "pass"` **and** no `new-asset-candidate` finding.
- `run(bundle, k, deps, *, regenerate, rebuild, max_rounds=3, emit=None) -> {"verdict", "approvable", "rounds", "history", "escalated": bool}`:
  - `rebuild(bundle) -> bundle` is required (review round 3, F10). After every `regenerate`, `run` calls `rebuild`, which regenerates the React project, recompiles it and reruns the Browser, returning a bundle whose `browser_report.bundleHash` equals the new build's `bundleHash`.
  - `verify` refuses a bundle whose report `bundleHash` does not match `bundle["build"]["bundleHash"]`, with the finding `stale-evidence` and verdict `blocked`. A repaired composition can therefore never be judged with the previous Browser evidence.
  - It regenerates on `fail`, up to the cap.
  - `blocked` stops immediately with no regeneration: missing evidence is not a generation problem.
  - Every round is kept in `history`.
  - When the cap is reached with `fail`, the run is `escalated: True, approvable: False`. Escalation queues the bundle for human decision; it never grants approvability.
- `Metrics.record(kind, *, screenId, reason=None, scope=None)` and `Metrics.summary()`. The summary contains:
  - `approvedWithoutEdit`
  - `editsPerScreen`
  - `rejectReasons`
  - `humanCorrections` (O-11, P1 input)
- `rejection_candidates(metrics, *, project_id, source_ref) -> {nodes, edges}` turns each distinct rejection reason (V-05) into a `PolicyRule` candidate:
  - `statement` is the reason, `required: False`, `severity: "major"`.
  - `provenance` is `"declared"`, because a human entered it.
  - `sourceRef` is the `run-round` ref, which is available after B0 sharing.
  - PolicyRule approval requires a business source (`ontology_store.py:740-742`). The planner therefore approves such a candidate only after publishing a revision that adds the governing `document-revision` or `product-guideline` ref (review round 3, #22). Until then it stays a reviewed ontology-improvement candidate. The C5 queue shows it as "업무 근거 연결 필요".

  Humans then review it into the ontology.
- `failure_checklist(history)` (P-04 input).

- [ ] **Step 1: Tests**

```python
# platform/tests/test_design_verify_graph.py
import copy
import pytest
from design_fixtures import knowledge
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.gui import fill
from design_loop.prd_extract import bindings
from design_loop.verify_graph import Metrics, rejection_candidates, run, verify
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
EXP = expected(GOOD, K)
PASS_REPORT = {"passed": True, "functionalStatus": "pass", "blockingFindings": [], "engineError": None,
               "checks": [{"caseId": "r1", "required": True, "status": "pass", "steps": [], "visibleText": ""}],
               "accessibility": {"status": "pass", "violations": []}, "visual": {"status": "not-run"},
               "bundleHash": "b" * 64, "renderer": "react-bundle",
               "build": {"ok": True, "catalogHash": "c" * 64, "sourceHash": "s" * 64, "bundleHash": "b" * 64,
                         "gates": {g: {"status": "pass"} for g in ("policy", "types", "build", "components")}}}
# CONTRACT_FULL and PASS_REPORT are generated once from the E19 chain (a real compile + evaluate_bundle of the seed) and
# stored as tests/fixtures/design_pass_report.json; the fixture regeneration script is tests/fixtures/make_design_pass.py.
import json as _json, pathlib as _pl
_FIX = _json.loads((_pl.Path(__file__).parent / "fixtures" / "design_pass_report.json").read_text(encoding="utf-8"))
CONTRACT_FULL, PASS_REPORT = _FIX["contract"], _FIX["report"]
JUDGE = {"llm_judge": lambda item, ctx: {"verdict": "pass", "evidence": "ok"}}


def bundle(**over):
    screens = {(s, "default"): fill(K, FLOW, s, bindings(GOOD)) for s in FLOW["screens"]}
    screens[("eligibility", "ineligible")] = {**screens[("eligibility", "default")], "state": "ineligible"}
    base = {"prd": GOOD, "flow": FLOW, "expectation": EXP, "screens": screens, "binding_values": bindings(GOOD),
            "contract": CONTRACT_FULL, "browser_report": copy.deepcopy(PASS_REPORT),
            "build": {"bundleHash": PASS_REPORT["bundleHash"]},
            "checklist": [{"id": "c1", "text": "톤", "severity": "major"}]}
    base.update(over)
    return base


def test_pass_fixture_satisfies_the_real_checker():
    # The canned report must be accepted by the production predicate, not only by verify_graph (review round 3, #11).
    from workspace.react_quality import react_report_passes
    assert react_report_passes(CONTRACT_FULL, PASS_REPORT)


def test_clean_bundle_passes_and_is_approvable():
    r = verify(bundle(), K, JUDGE)
    assert r["verdict"] == "pass" and r["approvable"] and not r["unavailable"]


def test_missing_tester_is_blocked_not_pass():
    r = verify(bundle(browser_report=None), K, JUDGE)
    assert r["verdict"] == "blocked" and r["unavailable"] == ["tester"] and not r["approvable"]


def test_missing_judge_is_blocked():
    r = verify(bundle(), K, {})
    assert r["verdict"] == "blocked" and "reviewer" in r["unavailable"]


def test_browser_required_check_failure_is_critical_and_not_approvable():
    report = copy.deepcopy(PASS_REPORT)
    report["checks"][0]["status"] = "fail"; report["passed"] = False; report["functionalStatus"] = "fail"
    r = verify(bundle(browser_report=report), K, JUDGE)
    assert r["verdict"] == "fail" and any(f["severity"] == "critical" and f["role"] == "tester" for f in r["findings"])


def test_cap_reached_escalates_without_approvability():
    bad = bundle(); bad["screens"].pop(("terms", "default"))
    out = run(bad, K, JUDGE, regenerate=lambda b, findings: b, rebuild=lambda b: b, max_rounds=2)
    assert out["escalated"] and not out["approvable"] and out["rounds"] == 2 and len(out["history"]) == 2


def test_repair_rebuilds_and_retests():
    bad = bundle(); bad["screens"].pop(("terms", "default"))
    rebuilt = []
    def rebuild(b):
        rebuilt.append(1)
        report = copy.deepcopy(PASS_REPORT); report["checks"][0]["status"] = "fail"   # Browser-only defect
        report["passed"] = False; report["functionalStatus"] = "fail"
        return {**b, "browser_report": report, "build": {**b.get("build", {}), "bundleHash": report["bundleHash"]}}
    out = run(bad, K, JUDGE, regenerate=lambda b, f: bundle(), rebuild=rebuild, max_rounds=2)
    assert rebuilt and out["verdict"] == "fail"      # the new Browser failure is seen, not the old pass


def test_stale_browser_evidence_is_blocked():
    b = bundle(); b["build"] = {"bundleHash": "e" * 64}
    assert verify(b, K, JUDGE)["verdict"] == "blocked"


def test_blocked_does_not_regenerate():
    calls = []
    out = run(bundle(browser_report=None), K, JUDGE, regenerate=lambda b, f: calls.append(1) or b, rebuild=lambda b: b, max_rounds=3)
    assert out["verdict"] == "blocked" and out["rounds"] == 1 and calls == []


def test_new_asset_candidate_is_never_approvable():
    b = bundle()
    comp = copy.deepcopy(b["screens"][("amount", "default")])
    comp["slots"]["body"].append({"id": "nx", "asset": "rate-simulator", "props": {}, "children": []})
    b["screens"][("amount", "default")] = comp
    r = verify(b, K, JUDGE, mode="compose")
    assert any(f["code"] == "new-asset-candidate" for f in r["findings"]) and not r["approvable"]


def test_rejection_reasons_become_policy_candidates():
    m = Metrics(); m.record("reject", screenId="amount", reason="금리 강조가 약함")
    ref = {"sourceKind": "run-round", "sourceId": "run-1", "revision": "2", "sha256": "d" * 64,
           "audienceRevision": "current-project-members-v1"}
    g = rejection_candidates(m, project_id="p1", source_ref=ref)
    node = g["nodes"][0]
    assert node["type"] == "PolicyRule" and node["reviewState"] == "candidate" and node["provenance"] == "declared"
    assert node["properties"]["statement"] == "금리 강조가 약함" and node["properties"]["required"] is False
```

Each test builds the bundle from the seed (`fill` compositions), with `browser_report` set to a canned report shaped like the output of `evaluate_bundle` (keys from `workspace/browser.py` L238-418: `passed`, `functionalStatus`, `checks`, `accessibility`, `visual`, `blockingFindings`, `bundleHash`, `renderer`) plus `build` gates.

- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): fail-closed verification graph with escalation and V-05 candidates"`

### Task E17: Customer convention, screen registry and handoff ZIP (R-01, R-03; Codex #14, #15)

**Files:**
- Create: `platform/design_loop/convention.py`, `platform/design_loop/handoff.py`, `platform/seed/design_poc/convention.json`
- Test: `platform/tests/test_design_handoff.py`

**Interfaces:**
- Convention JSON. The synthetic copy is in Git; the tenant copy is private.

```json
{"schemaVersion": 1, "screenIdPattern": "^(?P<area>[a-z]{3})-(?P<num>[0-9]{4})$", "area": "sav",
 "pathTemplate": "{area}/{screenId}{suffix}.tsx", "statePathTemplate": "{area}/{screenId}{suffix}.{state}.tsx",
 "surfaceSuffix": {"page": "", "bottom-sheet": "_B01", "full-popup": "_F01", "layer": "_L01", "tab": "_T01"},
 "typeCodes": {"page": "P", "bottom-sheet": "B", "full-popup": "F", "layer": "L", "tab": "T"},
 "meta": ["sid", "type", "dver", "status", "level1", "level2", "level3"]}
```

- `Registry` persists the step → screen id mapping in C, as a design record:
  - `assign(screen_node_id) -> sid` allocates the next `num` for the area and stays stable across regenerations.
  - `sid_for(screen_node_id)`
- `Convention.path(sid, surface, state="default")` validates `sid` against the pattern and normalizes the result with `posixpath.normpath`. The result must be relative with no `..` segment, NFC-normalized, ≤ 200 bytes and match `^[A-Za-z0-9_./-]+\.tsx$`. Uppercase is allowed so that the convention's `_B01`/`_F01`/`_L01`/`_T01` suffixes pass (review round 3, #14).
- `layout(convention, registry, approved) -> [{source, target, sid, state}]`:
  - The **selected layout variant** of each screen is exported, together with each approved state variant at its state path.
  - Duplicate targets → `ValueError("duplicate-handoff-path")`.
- `handoff.build(release_source_zip: bytes, layout, *, manifest_fields) -> (bytes, sha256)` (review round 4, X7):
  - The ZIP contains the **complete approved release project, unchanged**. This is every entry of the release `source.zip` produced by `react-kit/project.cjs` `sourceProject` (`project.cjs:68-102`): `src/App.tsx`, pages, logic, the pinned kit `ui/` files, `package.json`, `package-lock.json`, `tsconfig.json`, `studio.lock.json`, `test/contract.json`, `test/flow.test.cjs`, `README.md`, and the compile/policy scripts. It is placed under `project/`, with byte-identical entries. It therefore builds and tests on its own (`npm ci && npm run typecheck && npm run build && npm test`).
  - Nothing is relocated. The react-kit source policy (`policy.cjs:5`) allows only `src/pages/*` in approved source, and moving files would break relative imports (`policy.cjs:45`).
  - The customer screen-ID convention is delivered as `convention/screens.json`. Each entry maps `project/src/pages/<slug>.tsx` to `{sid, type, dver, status, levels, conventionPath, sha256}`, where `conventionPath` comes from `Convention.path`.
  - The same meta is **inside** each page source as `export const meta = {...} as const;`, emitted by E12 and therefore compiled, tested and approved.
  - The customer FE applies the path layout with the included `convention/apply.cjs` script. The script copies files to `conventionPath` and rewrites only the page-to-logic import specifiers. It then type-checks the **transformed** layout with its own generated `convention/tsconfig.json`, which includes `**/*.tsx` of the output directory, through `npx tsc --noEmit -p convention/tsconfig.json`. The project's `npm run typecheck` checks only `src/` (`project.cjs:82`, `compile.cjs:181`; review round 5, X7). A node test applies it to the seed project, asserts the type check covers the relocated files, and asserts a planted type error in a relocated file fails.
  - The top-level `manifest.json` carries `approvalHash`, `releaseId`, `sourceHash`, `bundleHash` and `sourceZipSha256`, plus `componentPackage: "@studio/approved-ui"` and `customerPackage: "not-verified"`.
  - The ZIP also contains `CHANGES.md` and `verification-report.json`.
  - The ZIP is deterministic, ≤ 512 entries and ≤ 40 MiB.
  - REQUIREMENTS R-01's physical path layout is applied by the customer after approval. It is recorded as roadmap decision D-6.

- [ ] **Step 1: Tests:**
  - A generated id such as `amount` is rejected by `Convention.path`; the registry maps it to `sav-0001`, and the test does not mask the id (Codex #14).
  - A state variant goes to `.ineligible.tsx`.
  - Two screens mapped to the same sid raise `duplicate-handoff-path`.
  - A `../../x` template is rejected.
  - Every `project/` entry is byte-equal to the release `source.zip` entry, and the entry sets are equal.
  - `npm ci && npm run typecheck && npm run build && npm test` succeeds inside the extracted `project/`, including the conditional-visibility rules (E12b). This test is node-enabled.
  - `convention/apply.cjs` output type-checks.
  - The build is deterministic.
  - The export name is asserted exactly: page files `export default function IntroPage`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): convention registry and exact-byte handoff package"`

### Task E18: Golden benchmark oracle (O-05, §8 success criteria)

**Files:**
- Create: `platform/design_loop/benchmark.py`, `platform/seed/design_poc/golden.json`
- Test: `platform/tests/test_design_benchmark.py`

**Interfaces:**
- `golden.json` is human-authored, independent of ontology rules:

```json
[{"id": "g-inel", "case": {"eligible": false, "autoTransfer": true},
  "mustShow": [{"screen": "ineligible", "text": "가입 조건"}], "mustNotShow": [{"screen": "*", "text": "가입 완료"}],
  "endsAt": "ineligible"}]
```

  Each case lists `mustShow`, `mustNotShow` and `endsAt`.
- `score(outputs, golden) -> {"passed", "failed": [id], "rate"}`. `outputs` holds each case's rendered visible text **per page checkpoint** (review round 5, Y6).
  - The Browser verifier (B2 S5 / `browser_core`) records `checkpoints: {"<ruleId>:<step>": {"case", "page", "state", "visibleText", "truncated"}}` at every `expectVisible` of a page root, **before** any navigation. `visibleText` is capped at 16 000 characters, with `truncated` set.
  - `score` builds per-(case, page) text from those checkpoints. A golden case whose page has no checkpoint, or whose checkpoint is truncated, is **incomplete**, and the benchmark result is `blocked`. It never counts as a pass.
  - Test: a planted defect on a page that the rule leaves by navigation is detected from its checkpoint.
- `compare(before, after) -> {"improved", "regressed": [id]}`. It judges an ontology change by the golden set only. Rules deleted from the ontology cannot raise the score, because the golden expectations do not come from rules (Codex #22).
- `success_metrics(runs) -> dict` computes the REQUIREMENTS §8 indicators from recorded run data:
  - coverage pass rate
  - regeneration rounds
  - `approvedWithoutEdit`
  - edit stability rate
  - time to hand-off

- [ ] **Step 1: Tests:**
  - A golden pass and a golden fail.
  - Removing `r-ineligible-reason` from the knowledge, then regenerating with a fake that omits the reason text, makes `compare` report a regression. This tests that the oracle cannot be gamed.
  - The metric math.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): independent golden benchmark and success metrics"`

### Task E19: Offline engine end-to-end

**Files:** Create `platform/tests/test_design_engine_e2e.py`.

- [ ] **Step 1:** A single offline test chains the whole engine:
  1. seed knowledge
  2. `extract_prd` with a fake generator returning GOOD
  3. flow and expectation
  4. `fill` all screens and states
  5. `project`
  6. `compile_local` (skipped without node)
  7. `contract.derive`
  8. `workspace.browser.evaluate_bundle` on the compiled bundle (skipped without Playwright/axe)
  9. `verify_graph.verify` → `pass` and `approvable`
  10. `handoff.build`, with the release source equal to the generated files

  It asserts every hash link across the chain: composition digest → source hash → bundle hash → handoff manifest.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_design_*.py -q` → PASS. In CI, the compile and Browser steps run because `platform-ci.yml` installs react-kit, Playwright and axe.
- [ ] **Step 3: Commit** `git commit -m "test(design): offline engine chain from ontology to handoff"`

## Verification before PR

```bash
cd platform
python3 -m pytest tests/test_ontology_ux.py tests/test_design_*.py tests/test_public_identifiers.py -q
python3 -m pytest tests/ -q
(cd react-kit && node --test test/*.test.cjs)
(cd source-analyzer && npm test)
git diff --check
python3 ../scripts/check_public_identifiers.py --patterns-file ~/.config/public-denylist.txt
```

The PR title is "Engine: design ontology generation engine (offline, unwired)". The body states:
- no API, worker, Runtime or UI wiring
- the legacy `design_*` path is unchanged
- which acceptance cases are supported at O level only
