# platform/tests/test_design_handoff.py
"""Task E17: customer convention, screen registry and the exact-byte handoff ZIP (R-01, R-03; Codex #14, #15)."""
import base64
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT, SEED, knowledge
from design_loop import handoff
from design_loop.convention import Convention, Registry, layout
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.gui import fill
from design_loop.prd_extract import bindings
from design_loop.react_project import project
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
VALUES = bindings(GOOD)
CASES = enumerate_cases(expected(GOOD, K)["conditions"])["cases"]
CONV = Convention.load(SEED / "convention.json")
CHROMIUM = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
needs = pytest.mark.skipif(not (ROOT / "react-kit" / "node_modules").exists() or not shutil.which("node")
                           or not shutil.which("npm"), reason="react-kit")


def assigned():
    registry = Registry(K, convention=CONV).register_flow(FLOW)
    for s in FLOW["screens"]:
        registry.assign(s)
    return registry


def approved_screens():
    screens = {(s, "default"): fill(K, FLOW, s, VALUES) for s in FLOW["screens"]}
    screens[("eligibility", "ineligible")] = {**screens[("eligibility", "default")], "state": "ineligible"}
    return screens


def release(registry=None, screens=None):
    """A synthetic release source.zip: the generated project with convention meta plus kit-like files."""
    registry = registry or assigned()
    screens = screens or approved_screens()
    meta = {key: CONV.meta(K, registry, FLOW, key[0], key[1], c) for key, c in screens.items()}
    files = {name: text.encode("utf-8") for name, text in project(FLOW, screens, K, VALUES, cases=CASES,
                                                                  registry=registry, meta=meta).items()}
    files.update({"package.json": b'{"name": "studio-react-project"}\n', "ui/index.tsx": b"export {};\n",
                  "test/contract.json": b'{"rules":[]}\n', "README.md": "# 검증된 React 프로젝트\n".encode()})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return buffer.getvalue(), files, registry, screens


def fields(files, **over):
    return {"approvalHash": "a" * 64, "releaseId": "rel-1", "sourceHash": handoff.source_hash(files),
            "bundleHash": "b" * 64, **over}


def entries(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def test_generated_ids_are_rejected_and_the_registry_assigns_stable_convention_ids():
    with pytest.raises(ValueError):
        CONV.path("amount", "page")                                    # a generated id is never a customer id
    registry = Registry(K, convention=CONV)
    assert registry.assign("amount") == "sav-0001"
    assert CONV.path(registry.sid_for("amount"), "page") == "sav/sav-0001.tsx"
    assert registry.assign("intro") == "sav-0002" and registry.assign("amount") == "sav-0001"
    again = Registry(K, registry.to_json(), convention=CONV)            # persisted across regenerations
    assert again.sid_for("amount") == "sav-0001" and again.assign("terms") == "sav-0003"
    assert CONV.path("sav-0001", "bottom-sheet") == "sav/sav-0001_B01.tsx"
    assert CONV.path("sav-0001", "full-popup") == "sav/sav-0001_F01.tsx"
    with pytest.raises(ValueError, match="convention-required"):
        Registry(K).assign("amount")
    with pytest.raises(KeyError):
        registry.sid_for("terms")


def test_a_state_variant_goes_to_its_state_path():
    assert CONV.path("sav-0002", "page", "ineligible") == "sav/sav-0002.ineligible.tsx"
    registry = assigned()
    out = {(e["screen"], e["state"]): e for e in layout(CONV, registry, approved_screens())}
    sid = registry.sid_for("eligibility")
    assert out[("eligibility", "ineligible")]["target"] == f"sav/{sid}.ineligible.tsx"
    assert out[("eligibility", "ineligible")]["source"] == \
        f"project/src/pages/{registry.state_page_id('eligibility', 'ineligible')}.tsx"
    assert out[("intro", "default")] == {"source": "project/src/pages/intro.tsx", "target": "sav/sav-0001.tsx",
                                         "sid": "sav-0001", "state": "default", "screen": "intro"}


def test_two_screens_mapped_to_the_same_sid_raise():
    registry = assigned()
    state = registry.to_json()
    state["sids"]["terms"] = state["sids"]["amount"]
    with pytest.raises(ValueError, match="duplicate-handoff-path"):
        layout(CONV, Registry(K, state, convention=CONV), approved_screens())


@pytest.mark.parametrize("template", ["../../x{suffix}.tsx", "/abs/{screenId}.tsx", "{area}/../../{screenId}.tsx",
                                      "{area}/{screenId}.ts", "{area}/{screenId} x.tsx", "{area}/" + "a" * 200 + ".tsx",
                                      "{area}/{unknown}.tsx", "{area}/화면{screenId}.tsx", "{area}\\{screenId}.tsx"])
def test_unsafe_or_off_shape_templates_are_rejected(template):
    data = copy.deepcopy(CONV.data)
    data["pathTemplate"] = template
    with pytest.raises(ValueError):
        Convention(data).path("sav-0001", "page")


def test_invalid_conventions_are_refused():
    for change in ({"schemaVersion": 2}, {"screenIdPattern": "^[a-z]+$"}, {"area": "SAVINGS"},
                   {"surfaceSuffix": {"page": ""}}, {"meta": ["sid"]}):
        with pytest.raises(ValueError):
            Convention({**CONV.data, **change})


def test_project_entries_are_byte_equal_and_complete():
    data, files, registry, screens = release()
    out, digest = handoff.build(data, layout(CONV, registry, screens), manifest_fields=fields(files))
    got = entries(out)
    assert {n[len("project/"):] for n in got if n.startswith("project/")} == set(files)
    assert all(got["project/" + name] == files[name] for name in files)
    assert set(got) - {"project/" + n for n in files} == {"convention/screens.json", "convention/apply.cjs",
                                                           "manifest.json", "CHANGES.md", "verification-report.json"}
    assert digest == hashlib.sha256(out).hexdigest()


def test_the_build_is_deterministic():
    data, files, registry, screens = release()
    plan = layout(CONV, registry, screens)
    first = handoff.build(data, plan, manifest_fields=fields(files))
    assert handoff.build(data, plan, manifest_fields=fields(files)) == first


def test_export_name_meta_and_manifest():
    data, files, registry, screens = release()
    out, _ = handoff.build(data, layout(CONV, registry, screens), manifest_fields=fields(files),
                           verification_report={"verdict": "pass"})
    got = entries(out)
    intro = got["project/src/pages/intro.tsx"].decode()
    assert "export default function IntroPage(" in intro
    assert 'export const meta = { sid: "sav-0001", type: "P", dver: "1", status: "draft", level1: "데모 적금 가입", ' \
           'level2: "상품 소개", level3: "default" } as const;' in intro
    screens_json = json.loads(got["convention/screens.json"])
    entry = screens_json["entries"]["project/src/pages/intro.tsx"]
    assert entry == {"sid": "sav-0001", "type": "P", "dver": "1", "status": "draft",
                     "levels": ["데모 적금 가입", "상품 소개", "default"], "conventionPath": "sav/sav-0001.tsx",
                     "state": "default", "sha256": hashlib.sha256(files["src/pages/intro.tsx"]).hexdigest()}
    manifest = json.loads(got["manifest.json"])
    assert manifest == {"schemaVersion": 1, "approvalHash": "a" * 64, "releaseId": "rel-1",
                        "sourceHash": handoff.source_hash(files), "bundleHash": "b" * 64,
                        "sourceZipSha256": hashlib.sha256(data).hexdigest(),
                        "componentPackage": "@studio/approved-ui", "customerPackage": "not-verified"}
    assert json.loads(got["verification-report.json"]) == {"verdict": "pass"} and got["CHANGES.md"]


def test_refusals():
    data, files, registry, screens = release()
    plan = layout(CONV, registry, screens)
    with pytest.raises(ValueError, match="source-hash-mismatch"):
        handoff.build(data, plan, manifest_fields=fields(files, sourceHash="0" * 64))
    with pytest.raises(ValueError, match="unmapped-page"):
        handoff.build(data, plan[1:], manifest_fields=fields(files))
    wrong = [dict(plan[0], sid="sav-0099")] + plan[1:]
    with pytest.raises(ValueError, match="page-meta-mismatch"):
        handoff.build(data, wrong, manifest_fields=fields(files))
    with pytest.raises(ValueError, match="duplicate-handoff-path"):
        handoff.build(data, plan + [plan[0]], manifest_fields=fields(files))
    with pytest.raises(ValueError, match="manifest-fields"):
        handoff.build(data, plan, manifest_fields={"releaseId": "rel-1"})
    escape = [dict(plan[0], target="../x.tsx")] + plan[1:]
    with pytest.raises(ValueError):
        handoff.build(data, escape, manifest_fields=fields(files))


def test_packaged_apply_script_matches_its_manifest():
    assert handoff.apply_script() == (ROOT / "design_loop" / "resources" / "convention-apply.cjs").read_bytes()


# ---- node-enabled: the extracted project builds and tests on its own; the convention layout type-checks -----------

@pytest.fixture
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and os.path.isfile(CHROMIUM):
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", CHROMIUM)


def run(command, cwd, timeout=240):
    env = {k: v for k, v in os.environ.items() if k != "NODE_TEST_CONTEXT"}
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


@needs
def test_extracted_handoff_project_builds_tests_and_applies_the_convention(local_browser):
    from fixtures.make_design_pass import chain
    result = chain(convention=CONV)
    build, registry = result["build"], result["registry"]
    release_zip = base64.b64decode(build["projectZipBase64"])
    files = handoff.read_zip(release_zip)
    assert handoff.source_hash(files) == build["sourceHash"]                  # the Python mirror of hashFiles
    assert {n: files[n].decode() for n in result["files"]} == result["files"]  # release source == generated files
    assert any(r["id"].startswith("visible-") for r in json.loads(files["test/contract.json"])["rules"])
    plan = layout(CONV, registry, result["screens"])
    out, _ = handoff.build(release_zip, plan, manifest_fields={
        "approvalHash": "a" * 64, "releaseId": "rel-1", "sourceHash": build["sourceHash"],
        "bundleHash": build["bundleHash"]})
    with tempfile.TemporaryDirectory() as directory:
        zipfile.ZipFile(io.BytesIO(out)).extractall(directory)
        root = Path(directory)
        proj = root / "project"
        for command in (["npm", "ci", "--prefer-offline", "--ignore-scripts", "--no-audit", "--no-fund"],
                        ["npm", "run", "typecheck"], ["npm", "run", "build"], ["npm", "test"]):
            done = run(command, proj)
            assert done.returncode == 0, (command, done.stdout[-3000:], done.stderr[-3000:])
        assert "visible-" in done.stdout                                     # the E12b visibility rules ran
        applied = run(["node", "convention/apply.cjs", str(root / "out")], root)
        assert applied.returncode == 0, (applied.stdout[-3000:], applied.stderr[-3000:])
        relocated = [line.split(": ", 1)[1] for line in applied.stdout.splitlines() if line.startswith("relocated: ")]
        assert sorted(relocated) == sorted(e["target"] for e in plan)
        listed = {Path(line).resolve() for line in applied.stdout.splitlines() if line.startswith("/")}
        assert all((root / "out" / target).resolve() in listed for target in relocated)   # the check covered them
        page = root / "out" / next(e["target"] for e in plan if e["screen"] == "amount" and e["state"] == "default")
        assert "from '../logic/types'" in page.read_text() and (root / "out" / "logic" / "types.ts").is_file()
        page.write_text(page.read_text() + "\nconst broken: number = 'x';\n")
        check = run(["npx", "--no-install", "tsc", "--noEmit", "-p", str(root / "out" / "convention" / "tsconfig.json")],
                    proj)
        assert check.returncode != 0 and page.name in check.stdout + check.stderr


def test_handoff_and_convention_import_only_stdlib_and_pure_schema():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for name in ("handoff.py", "convention.py"):
        for item in ast.walk(ast.parse((root / name).read_text(encoding="utf-8"))):
            if isinstance(item, ast.ImportFrom) and item.level == 0:
                assert item.module in {"workspace.ontology_schema", "workspace.ontology_ux"} \
                    or item.module.split(".")[0] in sys.stdlib_module_names, (name, item.module)
            elif isinstance(item, ast.Import):
                assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names), name
