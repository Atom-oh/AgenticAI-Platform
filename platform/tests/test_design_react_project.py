# platform/tests/test_design_react_project.py
"""Task E12: react-kit project generation from compositions and flow (R-01, R-02; Codex #13, #15)."""
import base64
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT, knowledge, published_knowledge
from design_loop.convention import Registry
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.gui import fill, generate_states, states_for
from design_loop.prd_extract import bindings
from design_loop.react_project import compile_local, compile_request, project
from design_loop.route import strategy
from test_design_gui import error_state_seed
from test_design_prd_extract import GOOD
from test_workbench_core import wb  # noqa: F401  (fixture)

K = knowledge()
needs = pytest.mark.skipif(not (ROOT / "react-kit" / "node_modules").exists() or not shutil.which("node"), reason="react-kit")
CHROMIUM = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"


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
    catalog = json.loads((ROOT / "react-kit" / "catalog.json").read_text())  # noqa: F841
    from design_loop.react_project import kit_catalog_hash
    out = compile_local(compile_request(build(), catalog_hash=kit_catalog_hash(), contract=None))
    assert out["ok"], out.get("diagnostics")
    assert all(out["gates"][g]["status"] == "pass" for g in ("policy", "types", "build", "components"))


@needs
def test_injected_quote_in_copy_still_compiles():
    files = build()
    files["src/pages/intro.tsx"] = files["src/pages/intro.tsx"].replace('title={"', 'title={"Say \\"yes\\" ', 1)
    assert compile_local(compile_request(files, catalog_hash=__import__("design_loop.react_project", fromlist=["x"]).kit_catalog_hash(), contract=None))["ok"]


# ---- additional interface checks ----------------------------------------------------------------------------------

@needs
def test_a_string_typed_summary_mapping_fails_the_types_gate():
    from design_loop.react_project import asset_gallery, kit_catalog_hash
    k = copy.deepcopy(K)
    k.assets["rate-summary"]["code"]["props"]["items"] = {"type": "string", "required": True}
    out = compile_local(compile_request(asset_gallery(k, bindings(GOOD)), catalog_hash=kit_catalog_hash(), contract=None))
    assert out["gates"]["types"]["status"] == "fail"


def test_pages_emit_literal_page_id_expression_attributes_and_trusted_adapters():
    files = build()
    intro = files["src/pages/intro.tsx"]
    assert '<Screen pageId="intro" testId={"intro"}' in intro
    assert "export default function IntroPage({ data, form, setField, go, caseState }: PageProps)" in intro
    assert "export const meta = { sid: " in intro and "as const;" in intro
    assert 'items={data["product.summaryItems"]}' in intro
    assert 'onClick={() => go("next")}' in intro and "k1-cta" in intro
    amount = files["src/pages/amount.tsx"]
    assert re.search(r'value=\{fieldText\(form, "k\d+-f1"\)\} onChange=\{v => setField\("k\d+-f1", v\)\}', amount)
    assert "disabled={!ready}" in amount and "const ready = filled(form," in amount
    terms = files["src/pages/terms.tsx"]
    assert re.search(r'checked=\{fieldFlag\(form, "k\d+-f1"\)\}', terms)
    evidence = files["src/pages/evidence-auto.tsx"]
    assert 'visible({ when: "cond:autoTransfer", negate: false }, caseState) && (' in evidence
    assert 'onClick={() => go("finish")}' in files["src/pages/done.tsx"]
    app = files["src/App.tsx"]
    assert 'testId={"case-select"} label={"검증용 케이스"}' in app and '"flow-finished"' in app
    assert json.loads(files["src/logic/data.ts"].split("export const data: Data = ", 1)[1].split(";\n", 1)[0]) \
        == dict(sorted(bindings(GOOD).items()))


def test_registry_page_ids_keys_and_bounded_test_ids():
    k = copy.deepcopy(K)
    long_id = "notice-" + "a" * 57                               # 64 characters, a published notice page id
    k.screens["done"]["pageId"] = long_id
    k.screens["confirm"]["pageId"] = "Not_A_Kit_Id"
    r = Registry(k)
    assert r.page_id("done") == long_id                          # unchanged (AX1)
    assert re.fullmatch(r"p-[0-9a-f]{12}", r.page_id("confirm"))
    assert r.page_key("done") == "k1" and r.page_key("confirm") == "k2" and r.page_key("done") == "k1"
    assert r.state_page_id("done", "error") == "k1--error"
    ids = r.test_ids("amount")
    assert ids["cta"] == "k3-cta" and list(ids["fields"].values()) == ["k3-f1"]
    assert all(len(v) <= 50 for v in ids["nodes"].values())
    again = Registry(k, r.to_json())                             # persisted allocation is stable
    assert again.test_ids("amount") == ids and again.page_id("done") == long_id
    k.screens["intro"]["pageId"] = long_id                       # collision -> numeric suffix
    assert Registry(k, r.to_json()).page_id("intro") == long_id[:62] + "-2"


def test_state_pages_use_the_short_key_and_overflow_raises():
    flow = build_flow(K, "savings-signup")
    screens = {(s, "default"): fill(K, flow, s, bindings(GOOD)) for s in flow["screens"]}
    [state] = generate_states(screens[("eligibility", "default")], ["ineligible"], K, {}, strategy=strategy("structured"),
                              flow=flow, binding_paths=frozenset(bindings(GOOD)))["states"]
    screens[("eligibility", "ineligible")] = state["composition"]
    registry = Registry(K)
    files = project(flow, screens, K, bindings(GOOD), cases=[{"eligible": True, "autoTransfer": True}], registry=registry)
    key = registry.page_key("eligibility")
    path = f"src/pages/{key}--ineligible.tsx"
    assert path in files and f'<Screen pageId="{key}--ineligible"' in files[path]
    big = dict(screens)
    for i, st in enumerate(("error", "empty", "loading", "done", "zero", "many")):
        for s in flow["screens"][:2]:
            big[(s, st)] = {**screens[(s, "default")], "state": st}
    with pytest.raises(ValueError, match="project-file-limit"):
        project(flow, big, K, bindings(GOOD), cases=[{"eligible": True, "autoTransfer": True}])


# ---- node-enabled: canonical ids, state page, Browser and exported npm test ---------------------------------------

@pytest.fixture
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and os.path.isfile(CHROMIUM):
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", CHROMIUM)


def dist(out):
    return {name: base64.b64decode(value) for name, value in out["files"].items()}


def journey_contract(registry, flow, first, second, *, state_page=None):
    rules = [{"id": "R1", "title": "첫 화면에서 다음 화면으로 이동", "steps": [
        {"action": "select", "target": "case-select", "value": f"0:{first}:default"},
        {"action": "expectVisible", "target": registry.page_id(first), "value": True},
        {"action": "click", "target": registry.cta_id(first)},
        {"action": "expectVisible", "target": registry.page_id(second), "value": True}]}]
    if state_page:
        screen, state = state_page
        rules.append({"id": "R2", "title": "상태 화면 표시", "steps": [
            {"action": "select", "target": "case-select", "value": f"0:{screen}:{state}"},
            {"action": "expectVisible", "target": registry.state_page_id(screen, state), "value": True}]})
    return {"title": "생성 프로젝트 확인", "rules": rules}


@needs
def test_canonical_published_project_with_a_state_page_compiles_and_renders(wb, local_browser):  # noqa: F811
    from design_loop.react_project import kit_catalog_hash
    from workspace.browser import evaluate_bundle
    k, ids = published_knowledge(wb, raw=error_state_seed())
    flow = build_flow(k, ids["savings-signup"])
    values = bindings(GOOD)
    screens = {(s, "default"): fill(k, flow, s, values) for s in flow["screens"]}
    amount = ids["amount"]
    [error] = generate_states(screens[(amount, "default")], states_for(amount, k, flow, expected(GOOD, k)), k, {},
                              strategy=strategy("structured"), flow=flow, binding_paths=frozenset(values))["states"]
    screens[(amount, "error")] = error["composition"]
    registry = Registry(k)
    files = project(flow, screens, k, values, cases=enumerate_cases(expected(GOOD, k)["conditions"])["cases"],
                    registry=registry)
    assert len(files) <= 24 and all(len(p) < 80 for p in files)
    assert f"src/pages/{registry.page_key(amount)}--error.tsx" in files
    contract = journey_contract(registry, flow, ids["intro"], ids["eligibility"], state_page=(amount, "error"))
    out = compile_local(compile_request(files, catalog_hash=kit_catalog_hash(), contract=contract))
    assert out["ok"], out.get("diagnostics")
    report = evaluate_bundle(dist(out), contract)
    assert report["functionalStatus"] == "pass", report["checks"]
    assert report["accessibility"]["status"] == "pass", report["accessibility"]
    assert report["passed"], report["blockingFindings"]


@needs
@pytest.mark.parametrize("length", [40, 41, 64])
def test_long_published_page_ids_compile_and_verify(length, local_browser):
    from design_loop.react_project import kit_catalog_hash
    from workspace.browser import evaluate_bundle
    k = copy.deepcopy(K)
    page_id = "notice-" + "x" * (length - len("notice-"))
    k.screens["eligibility"]["pageId"] = page_id
    flow = build_flow(k, "savings-signup")
    screens = {(s, "default"): fill(k, flow, s, bindings(GOOD)) for s in flow["screens"]}
    registry = Registry(k)
    files = project(flow, screens, k, bindings(GOOD), cases=[{"eligible": True, "autoTransfer": False}], registry=registry)
    assert registry.page_id("eligibility") == page_id and f"src/pages/{page_id}.tsx" in files
    contract = journey_contract(registry, flow, "intro", "eligibility")
    out = compile_local(compile_request(files, catalog_hash=kit_catalog_hash(), contract=contract))
    assert out["ok"], out.get("diagnostics")
    assert evaluate_bundle(dist(out), contract)["passed"]


@needs
def test_single_case_multi_screen_project_passes_browser_and_exported_npm_test(local_browser):
    from design_loop.react_project import kit_catalog_hash
    from workspace.browser import evaluate_bundle
    flow = build_flow(K, "savings-signup")
    screens = {(s, "default"): fill(K, flow, s, bindings(GOOD)) for s in flow["screens"]}
    registry = Registry(K)
    files = project(flow, screens, K, bindings(GOOD), cases=[{"eligible": True, "autoTransfer": True}], registry=registry)
    contract = journey_contract(registry, flow, "confirm", "done")
    contract["rules"].append({"id": "R3", "title": "마지막 화면에서 절차 완료", "steps": [
        {"action": "select", "target": "case-select", "value": "0:done:default"},
        {"action": "click", "target": registry.cta_id("done")},
        {"action": "expectVisible", "target": "flow-finished", "value": True}]})
    out = compile_local(compile_request(files, catalog_hash=kit_catalog_hash(), contract=contract))
    assert out["ok"], out.get("diagnostics")
    report = evaluate_bundle(dist(out), contract)
    assert report["passed"], (report["checks"], report["blockingFindings"])
    with tempfile.TemporaryDirectory() as directory:
        zipfile.ZipFile(io.BytesIO(base64.b64decode(out["projectZipBase64"]))).extractall(directory)
        os.symlink(ROOT / "react-kit" / "node_modules", Path(directory) / "node_modules")
        env = {k: v for k, v in os.environ.items() if k != "NODE_TEST_CONTEXT"}
        for command in (["npm", "run", "typecheck"], ["npm", "run", "build"], ["npm", "test"]):
            done = subprocess.run(command, cwd=directory, env=env, capture_output=True, text=True, timeout=180)
            assert done.returncode == 0, (command, done.stdout[-2000:], done.stderr[-2000:])


def test_codegen_modules_import_only_stdlib_and_pure_schema():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for name in ("react_project", "convention", "local_runner"):
        for item in ast.walk(ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(item, ast.ImportFrom) and item.level == 0:
                assert item.module in {"workspace.ontology_schema", "workspace.ontology_ux"} \
                    or item.module.split(".")[0] in sys.stdlib_module_names, (name, item.module)
            elif isinstance(item, ast.Import):
                assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names), name
