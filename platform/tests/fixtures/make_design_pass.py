"""Regenerate `tests/fixtures/design_pass_report.json` from the E19 chain (engine plan, Task E16).

A real compile (`react-kit/compile.cjs`) and a real `workspace.browser.evaluate_bundle` of the seed project, joined
by `design_loop.evidence.assemble`. The stored report is the assembled report with only the large binary payloads
(`screenshotBase64`, `diffBase64`, `build.projectZipBase64`, `build.distZipBase64`) removed; none of them is read by
`react_report_passes` or `_run_approve`'s field comparison.

Run from `platform/` with the Python 3.12 venv and AXE_PATH set:
    python tests/fixtures/make_design_pass.py
Regenerate whenever the kit catalog hash, the seed or the contract derivation changes.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

OUT = HERE / "design_pass_report.json"
HEAVY = ("screenshotBase64", "diffBase64")
HEAVY_BUILD = ("projectZipBase64", "distZipBase64")


def seed_screens(k, flow, values, expectation, variant="a"):
    """Default compositions (fill, or a structured layout variant) plus every `states_for` state page."""
    from design_loop.gui import fill, generate_screen, generate_states, states_for
    from design_loop.route import strategy
    screens = {}
    for s in flow["screens"]:
        if variant == "a":
            screens[(s, "default")] = fill(k, flow, s, values)
        else:
            out = generate_screen(s, k, flow, {}, strategy=strategy("structured"), binding_paths=frozenset(values))
            screens[(s, "default")] = next(v for v in out["variants"] if v["variant"] == variant)["composition"]
    for s in flow["screens"]:
        for st in states_for(s, k, flow, expectation):
            if st != "default":
                [state] = generate_states(screens[(s, "default")], [st], k, {}, strategy=strategy("structured"),
                                          flow=flow, binding_paths=frozenset(values))["states"]
                screens[(s, st)] = state["composition"]
    return screens


def chain(k=None, prd=None, *, screens=None, criteria=None, convention=None):
    """Seed knowledge -> flow -> compositions -> project -> compile -> contract -> Browser -> assembled report."""
    from design_fixtures import knowledge
    from design_loop.contract import derive
    from design_loop.convention import Registry
    from design_loop.evidence import assemble
    from design_loop.flow import build_flow, enumerate_cases, expected
    from design_loop.prd_extract import bindings
    from design_loop.react_project import compile_local, compile_request, kit_catalog_hash, project
    from test_design_prd_extract import GOOD
    from workspace.browser import evaluate_bundle
    k = k or knowledge()
    prd = prd or GOOD
    values = bindings(prd)
    flow = build_flow(k, "savings-signup")
    expectation = expected(prd, k)
    cases = enumerate_cases(expectation["conditions"])["cases"]
    registry = Registry(k, convention=convention).register_flow(flow)
    catalog = kit_catalog_hash()
    derived = derive(prd, flow, k, registry, [], cases=cases, criteria={"catalogHash": catalog, **(criteria or {})})
    contract = derived["contract"]
    if contract is None:
        raise RuntimeError(f"contract not derived: {derived['findings']}")
    screens = screens or seed_screens(k, flow, values, expectation)
    meta = None
    if convention is not None:                       # customer screen ids in the compiled page meta (E17)
        for s in flow["screens"]:
            registry.assign(s)
        meta = {key: convention.meta(k, registry, flow, key[0], key[1], c) for key, c in screens.items()}
    files = project(flow, screens, k, values, cases=cases, registry=registry, meta=meta)
    build = compile_local(compile_request(files, catalog_hash=catalog, contract=contract))
    if not build.get("ok"):
        raise RuntimeError(f"compile failed: {build.get('diagnostics')}")
    dist = {name: base64.b64decode(value) for name, value in build["files"].items()}
    browser = evaluate_bundle(dist, contract, expected_hash=build["bundleHash"])
    report = assemble(build, browser, contract=contract)
    return {"k": k, "prd": prd, "values": values, "flow": flow, "expectation": expectation, "cases": cases,
            "registry": registry, "contract": contract, "screens": screens, "files": files, "build": build, "meta": meta,
            "browser": browser, "report": report}


def slim(report):
    out = {key: value for key, value in report.items() if key not in HEAVY}
    out["build"] = {key: value for key, value in report["build"].items() if key not in HEAVY_BUILD}
    return out


def main():
    result = chain()
    report = slim(result["report"])
    if not report.get("passed"):
        raise SystemExit(f"seed report did not pass: {report.get('blockingFindings')}")
    OUT.write_text(json.dumps({"contract": result["contract"], "report": report}, ensure_ascii=False, indent=1,
                              sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(HERE.parents[1])}: {len(result['contract']['rules'])} rules, "
          f"bundle {report['bundleHash'][:12]}")


if __name__ == "__main__":
    main()
