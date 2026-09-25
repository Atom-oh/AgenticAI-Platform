# platform/tests/test_design_engine_e2e.py
"""Task E19: one offline chain from the ontology to the handoff, with every hash link asserted."""
import base64
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT, SEED, knowledge
from design_loop import handoff
from design_loop.composition import digest as composition_digest
from design_loop.contract import derive, missing_targets
from design_loop.convention import Convention, Registry, layout
from design_loop.flow import build_flow, enumerate_cases, expected, flow_issues
from design_loop.prd_extract import bindings, extract_prd
from design_loop.react_project import project
from design_loop.verify_graph import verify
from fixtures.make_design_pass import seed_screens
from test_design_prd_extract import GOOD, OK, PAGES

CHROMIUM = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
JUDGE = {"llm_judge": lambda item, ctx: {"verdict": "pass", "evidence": "ok"}, "normalize": OK}


@pytest.fixture
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and os.path.isfile(CHROMIUM):
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", CHROMIUM)


def test_offline_engine_chain_from_ontology_to_handoff(local_browser):
    # 1. seed knowledge
    k = knowledge()
    assert k.complete
    # 2. PRD extraction with a fake generator returning GOOD (behind the verify-only normalization hook)
    extracted = extract_prd(PAGES, {"generate": lambda s, u, t: json.dumps(GOOD, ensure_ascii=False), "normalize": OK},
                            model="fake")
    assert extracted["issues"] == [] and extracted["prd"] == GOOD
    prd = extracted["prd"]
    values = bindings(prd)
    # 3. flow and the independent expectation
    flow = build_flow(k, "savings-signup")
    expectation = expected(prd, k)
    assert flow_issues(flow, expectation, k) == []
    cases = enumerate_cases(expectation["conditions"])["cases"]
    # 4. fill every screen and state
    screens = seed_screens(k, flow, values, expectation)
    assert set(screens) == {(s, "default") for s in flow["screens"]} | {("eligibility", "ineligible"),
                                                                           ("ineligible", "ineligible")}
    digests = {key: composition_digest(c) for key, c in screens.items()}
    # 7 (before compile). the contract is composition-independent and must be inside the compiled project
    convention = Convention.load(SEED / "convention.json")
    registry = Registry(k, convention=convention).register_flow(flow)
    for s in flow["screens"]:
        registry.assign(s)
    needs_node = (ROOT / "react-kit" / "node_modules").exists() and shutil.which("node")
    catalog = None
    if needs_node:
        from design_loop.react_project import kit_catalog_hash
        catalog = kit_catalog_hash()
    derived = derive(prd, flow, k, registry, [], cases=cases, criteria={"catalogHash": catalog} if catalog else None)
    contract = derived["contract"]
    assert derived["findings"] == [] and contract is not None and len(contract["rules"]) == 16
    assert missing_targets(contract, screens, k, registry) == []
    # 5. React project with the customer screen ids compiled into the page meta
    meta = {key: convention.meta(k, registry, flow, key[0], key[1], c) for key, c in screens.items()}
    files = project(flow, screens, k, values, cases=cases, registry=registry, meta=meta)
    assert project(flow, copy.deepcopy(screens), k, values, cases=cases, registry=registry, meta=meta) == files
    assert {key: composition_digest(c) for key, c in screens.items()} == digests       # generation did not mutate
    if not needs_node:
        pytest.skip("react-kit node_modules or node unavailable")
    # 6. compile
    from design_loop.evidence import assemble
    from design_loop.react_project import compile_local, compile_request
    build = compile_local(compile_request(files, catalog_hash=catalog, contract=contract))
    assert build["ok"], build.get("diagnostics")
    # 8. the real Browser verifier on the compiled bundle
    from workspace.browser import evaluate_bundle
    dist = {name: base64.b64decode(value) for name, value in build["files"].items()}
    browser = evaluate_bundle(dist, contract, expected_hash=build["bundleHash"])
    assert browser["passed"], ([c for c in browser["checks"] if c["status"] != "pass"], browser["blockingFindings"])
    report = assemble(build, browser, contract=contract)
    # 8b. the large-text second pass (V-02) over the same bundle and contract
    large = evaluate_bundle(dist, contract, expected_hash=build["bundleHash"], text_scale=2)
    assert large["largeText"]["status"] == "pass" and large["passed"], (large["largeText"], large["blockingFindings"])
    large_report = assemble(build, large, contract=contract)
    # 9. verification graph: pass and approvable with the full design checklist
    bundle = {"prd": prd, "flow": flow, "expectation": expectation, "screens": screens, "binding_values": values,
              "contract": contract, "browser_report": report, "large_text_report": large_report,
              "build": {"bundleHash": build["bundleHash"]},
              "registry": registry}
    result = verify(bundle, k, JUDGE)
    assert result["verdict"] == "pass" and result["approvable"] and not result["unavailable"], result["roles"]
    # 10. handoff with the release source equal to the generated files
    release_zip = base64.b64decode(build["projectZipBase64"])
    release = handoff.read_zip(release_zip)
    assert {name: release[name].decode("utf-8") for name in files} == files
    out, sha = handoff.build(release_zip, layout(convention, registry, screens), manifest_fields={
        "approvalHash": "a" * 64, "releaseId": "rel-e2e", "sourceHash": build["sourceHash"],
        "bundleHash": report["bundleHash"]}, verification_report={"verdict": result["verdict"]})
    with zipfile.ZipFile(io.BytesIO(out)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        screens_json = json.loads(archive.read("convention/screens.json"))
        packaged = {n[len("project/"):]: archive.read(n) for n in archive.namelist() if n.startswith("project/")}
    # hash links: composition digest -> source hash -> bundle hash -> handoff manifest
    assert packaged == release
    assert handoff.source_hash(packaged) == build["sourceHash"] == report["sourceHash"] == manifest["sourceHash"]
    assert build["bundleHash"] == browser["bundleHash"] == report["bundleHash"] == manifest["bundleHash"]
    assert manifest["sourceZipSha256"] == hashlib.sha256(release_zip).hexdigest() and sha == hashlib.sha256(out).hexdigest()
    assert report["artifactSha256"] == hashlib.sha256(build["previewHtml"].encode()).hexdigest()
    for source, entry in screens_json["entries"].items():
        assert entry["sha256"] == hashlib.sha256(packaged[source[len("project/"):]]).hexdigest()
    # a one-prop composition change reaches a different source hash (the source hash covers the compositions)
    changed = copy.deepcopy(screens)
    changed[("intro", "default")]["slots"]["header"][0]["props"]["children"] = "다른 제목"
    assert composition_digest(changed[("intro", "default")]) != digests[("intro", "default")]
    other = project(flow, changed, k, values, cases=cases, registry=registry, meta=meta)
    swapped = {**release, **{name: text.encode("utf-8") for name, text in other.items()}}
    assert handoff.source_hash(swapped) != build["sourceHash"]
