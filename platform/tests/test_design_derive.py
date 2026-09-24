# platform/tests/test_design_derive.py
import hashlib, shutil, sys  # noqa: E401
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT  # noqa: E402
from workspace import ontology_schema as schema  # noqa: E402
from design_loop.derive import (analysis_request, candidate_graph, jsx_sequences, mine,  # noqa: E402
                                resolver_profile, unresolved_uses)
from design_loop.local_runner import RunnerError, run_node  # noqa: E402

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
# Server-configured resolver profile, verified against the (synthetic) platform package registry (review round 8, AB2).
REGISTRY = {"@demo/kit": {"version": "1.0.0", "sha256": "a" * 64}}
_BODY = {"id": "demo-profile", "revision": "1", "aliases": {}, "packages": {"@demo/kit": {"version": "1.0.0", "sha256": "a" * 64}}}
PROFILE = resolver_profile({**_BODY, "hash": schema.digest(_BODY)}, REGISTRY)


def _files(files=FILES):
    return [{"path": p, "kind": "code", "text": t, "sha256": hashlib.sha256(t.encode()).hexdigest()} for p, t in files.items()]


def analyze(files=FILES):
    return run_node(ANALYZER, analysis_request({"files": _files(files)}, PROFILE))


@needs
def test_same_line_order_is_source_order_not_alphabetical():
    seq = jsx_sequences(analyze(), profile=PROFILE)["a/one.tsx"]
    assert [u["symbol"] for u in seq] == ["Title", "RateBadge", "InfoRow", "Button"]


@needs
def test_default_and_named_imports_have_distinct_identity():
    seq = jsx_sequences(analyze(), profile=PROFILE)["b/four.tsx"]
    assert seq[-1]["identity"] == "@demo/kit#default" and seq[1]["identity"] == "@demo/kit#Button"


@needs
def test_mine_repeated_triplet_and_exclude_brand():
    cands = mine(jsx_sequences(analyze(), profile=PROFILE), min_support=3, exclude=frozenset({"@demo/kit#Logo"}))
    top = cands[0]
    assert [i.split("#")[1] for i in top["identities"]] == ["Title", "RateBadge", "InfoRow"] and top["support"] == 3
    assert all("@demo/kit#Logo" not in c["identities"] for c in cands)


@needs
def test_candidate_graph_is_contract_valid_with_string_revisions():
    ref = {"sourceKind": "asset", "sourceId": "code-1", "revision": "1", "sha256": "b" * 64, "audienceRevision": "1"}
    g = candidate_graph(mine(jsx_sequences(analyze(), profile=PROFILE), min_support=3), project_id="p1", source_ref=ref)
    assert g["nodes"] and all(n["reviewState"] == "candidate" and n["provenance"] == "parser-extracted" for n in g["nodes"])
    schema.validate_graph({"schemaVersion": 1, "projectId": "p1", "nodes": g["nodes"], "edges": g["edges"]})


# --- Resolver authority (review round 8, AB2) ---

def test_forged_package_hash_is_rejected_at_profile_validation():
    forged = {**_BODY, "packages": {"@demo/kit": {"version": "1.0.0", "sha256": "c" * 64}}}
    with pytest.raises(ValueError):
        resolver_profile({**forged, "hash": schema.digest(forged)}, REGISTRY)
    with pytest.raises(ValueError):                      # profile hash must bind its exact contents
        resolver_profile({**_BODY, "hash": "d" * 64}, REGISTRY)
    unregistered = {**_BODY, "packages": {"@other/kit": {"version": "1.0.0", "sha256": "a" * 64}}}
    with pytest.raises(ValueError):
        resolver_profile({**unregistered, "hash": schema.digest(unregistered)}, REGISTRY)


def test_request_supplied_resolver_override_is_rejected():
    with pytest.raises(ValueError):
        analysis_request({"files": _files(), "resolver": {"aliases": {"@demo/kit": "a/one.tsx"}, "packages": {}}}, PROFILE)
    request = analysis_request({"files": _files()}, PROFILE)
    assert request["resolver"] == {"aliases": {}, "packages": PROFILE["packages"], "jsonAssetFields": []}


def _use(symbol, package="@demo/kit", sha="a" * 64, column=0):
    return {"path": "x.tsx", "kind": "jsx-use", "symbol": symbol, "localName": symbol, "line": 1, "column": column,
            "resolution": {"status": "approved-package", "package": package, "version": "1.0.0", "sha256": sha}}


def test_unregistered_or_changed_package_uses_are_not_counted_as_resolved():
    analysis = {"references": [_use("Title"), _use("Other", package="@other/kit", column=1),
                               _use("Forged", sha="e" * 64, column=2)]}
    assert [u["symbol"] for u in jsx_sequences(analysis, profile=PROFILE)["x.tsx"]] == ["Title"]
    assert {u["symbol"] for u in unresolved_uses(analysis, profile=PROFILE)} == {"Other", "Forged"}
    assert jsx_sequences(analysis) == {}                 # no frozen profile: approved-package is never resolved
    assert len(unresolved_uses(analysis)) == 3


@needs
def test_runner_failure_is_an_explicit_error():
    with pytest.raises(RunnerError):
        run_node(ANALYZER, {"schemaVersion": 2, "files": []})


# --- Task E5a: PageTemplate reverse-derivation (O-03; review round 19, AM1) ---

from types import SimpleNamespace  # noqa: E402

from test_workbench_core import wb  # noqa: E402,F401  (fixture)

from workspace.ontology_ux import validate_ux_model  # noqa: E402


def _kit(names):
    return "import { " + ", ".join(sorted(set(names))) + " } from '@demo/kit';\n"


def _page(root, regions, name="P"):
    body = "".join(f"<{r}/>" for r in regions)
    return _kit([root, *regions]) + f"export const {name} = () => <{root}>{body}</{root}>;"


def _profile_for(names):
    return PROFILE   # every @demo/kit symbol resolves through the one frozen package


def _assets(names):
    """A minimal Knowledge-shaped view: existing asset nodes whose code layer names a kit export."""
    return SimpleNamespace(assets={f"asset-{n.lower()}": {"id": f"asset-{n.lower()}", "level": "Organism",
                                                         "code": {"importPath": "@demo/kit", "exportName": n}}
                                   for n in names})


def _structures(files):
    from design_loop.derive import page_structures
    return page_structures(analyze(files), profile=PROFILE)


REF = {"sourceKind": "asset", "sourceId": "code-1", "revision": "1", "sha256": "b" * 64, "audienceRevision": "1"}


@needs
def test_page_structures_use_direct_children_of_the_root():
    s = _structures({"p/a.tsx": _kit(["Screen", "Header", "Stack", "Title", "Footer"])
                     + "export const A = () => <Screen><Header/><Stack><Title/></Stack><Footer/></Screen>;"})
    assert s["p/a.tsx"] == {"root": "@demo/kit#Screen",
                            "regions": ["@demo/kit#Header", "@demo/kit#Stack", "@demo/kit#Footer"]}


@needs
def test_three_files_share_a_template_with_three_valid_slots():
    from design_loop.derive import mine_templates, resolve_identities
    files = {f"p/{i}.tsx": _page("Screen", ["Header", "Stack", "Footer"]) for i in range(3)}
    mined = mine_templates(_structures(files))
    assert len(mined) == 1 and mined[0]["support"] == 3
    mapping = resolve_identities({i for c in mined for i in c["identities"]}, _assets(["Header", "Stack", "Footer"]))
    g = candidate_graph(mined, project_id="p1", source_ref=REF, identities=mapping)
    tpl = [n for n in g["nodes"] if n["type"] == "PageTemplate"]
    assert len(tpl) == 1
    ux = tpl[0]["properties"]["uxModel"]
    assert ux["slots"] == {"slot1": {"required": True, "allowed": ["asset-header"]},
                           "slot2": {"required": True, "allowed": ["asset-stack"]},
                           "slot3": {"required": True, "allowed": ["asset-footer"]}}
    assert ux["layers"]["code"]["props"] == {f"slot{i}": {"type": "node", "required": True} for i in (1, 2, 3)}
    validate_ux_model(ux, "PageTemplate")
    assert [r["location"] for r in tpl[0]["sourceRefs"]] == [{"path": f"p/{i}.tsx", "exportName": "Screen"} for i in range(3)]
    external = [schema.seal({"id": a, "scope": {"kind": "project", "projectId": "p1"}, "type": "Organism", "title": a,
                             "revision": 1, "sourceRefs": [REF], "provenance": "declared", "reviewState": "candidate",
                             "tombstone": False}) for a in ("asset-header", "asset-stack", "asset-footer")]
    schema.validate_graph({"schemaVersion": 1, "projectId": "p1", "nodes": g["nodes"] + external, "edges": g["edges"]})


@needs
def test_different_region_counts_do_not_merge():
    from design_loop.derive import mine_templates
    files = {"p/a.tsx": _page("Screen", ["Header", "Footer"]), "p/b.tsx": _page("Screen", ["Header", "Footer"]),
             "p/c.tsx": _page("Screen", ["Header", "Stack", "Footer"])}
    mined = mine_templates(_structures(files))
    assert [len(c["regions"]) for c in mined] == [2] and mined[0]["usage"] == ["p/a.tsx", "p/b.tsx"]


@needs
def test_capacity_twelve_regions_template_thirteen_coverage_note():
    from design_loop.derive import mine_templates, resolve_identities
    names = [f"R{i}" for i in range(1, 14)]
    files = {**{f"t12/{i}.tsx": _page("Screen", names[:12]) for i in range(2)},
             **{f"t13/{i}.tsx": _page("Screen", names) for i in range(2)}}
    mined = mine_templates(_structures(files))
    assert [len(c["regions"]) for c in mined] == [12]
    assert "template-capacity" in mined.coverage["unknown"]
    assert mined.coverage["notes"] == [{"reason": "template-capacity", "regions": 13, "files": 2}]
    mapping = resolve_identities({i for c in mined for i in c["identities"]}, _assets(names))
    g = candidate_graph(mined, project_id="p1", source_ref=REF, identities=mapping)
    (tpl,) = [n for n in g["nodes"] if n["type"] == "PageTemplate"]
    assert len(tpl["properties"]["uxModel"]["slots"]) == 12
    assert "template-capacity" in g["coverage"]["unknown"]


def test_identity_resolution_is_exact_unique_and_never_guesses():
    from design_loop.derive import resolve_identities
    k = SimpleNamespace(assets={
        "a": {"code": {"importPath": "@demo/kit", "exportName": "Header"}},
        "b": {"code": {"importPath": "@demo/kit", "exportName": "Twin"}},
        "c": {"code": {"importPath": "@demo/kit", "exportName": "Twin"}},
        "d": {"code": None}})
    got = resolve_identities({"@demo/kit#Header", "@demo/kit#Twin", "@demo/kit#default", "x.tsx#Local", "cand-local"},
                             k, local={"x.tsx#Local": "cand-0123456789abcdef01234567"})
    assert got == {"@demo/kit#Header": "a", "x.tsx#Local": "cand-0123456789abcdef01234567"}


def _publishable(wb, graph, screens):
    from test_ontology_sources import asset
    from workspace.ontology_sources import asset_reference
    ref = asset_reference(asset(wb, "screens-code"))
    project = wb.project["id"]

    def reown(value):
        value = {k: v for k, v in value.items() if k != "contentHash"}
        if "scope" in value:
            value["scope"] = {"kind": "project", "projectId": project}
        value["sourceRefs"] = [{**ref, **({"location": r["location"]} if "location" in r else {})} for r in value["sourceRefs"]]
        return schema.seal(value)
    return {"schemaVersion": 1, "projectId": project,
            "nodes": [reown(n) for n in graph["nodes"]] + [reown(n) for n in screens],
            "edges": [reown(e) for e in graph["edges"]]}, ref


def _local(identifier, kind, **extra):
    return schema.seal({"id": identifier, "scope": {"kind": "project", "projectId": "p1"}, "type": kind,
                        "title": identifier, "revision": 1, "sourceRefs": [REF], "provenance": "declared",
                        "reviewState": "candidate", "tombstone": False, **extra})


@needs
def test_mined_templates_publish_with_partial_and_unmapped_structures(wb):
    """Real analyzer → mine_templates → resolve_identities → candidate_graph → publish_candidate."""
    from design_loop.derive import mine_templates, resolve_identities
    from workspace.ontology_store import Ontology
    from test_ontology_sources import context
    files = {**{f"ok/{i}.tsx": _page("Screen", ["Header", "Stack", "Footer"]) for i in range(2)},
             **{f"part/{i}.tsx": _page("Panel", ["Header", "Mystery", "Footer"]) for i in range(2)},
             **{f"none/{i}.tsx": _page("Grid", ["Ghost", "Phantom"]) for i in range(2)},
             "mol/a.tsx": _kit(["Title", "RateBadge"]) + "export const M = () => <><Title/><RateBadge/></>;",
             "mol/b.tsx": _kit(["Title", "RateBadge"]) + "export const N = () => <><Title/><RateBadge/></>;"}
    analysis = analyze(files)
    from design_loop.derive import page_structures
    mined = mine_templates(page_structures(analysis, profile=PROFILE))
    molecules = mine(jsx_sequences(analysis, profile=PROFILE))
    known = _assets(["Header", "Stack", "Footer"])
    mapping = resolve_identities({i for c in mined for i in c["identities"]}, known)
    screens = {"ok/0.tsx": "screen-ok0", "ok/1.tsx": "screen-ok1", "part/0.tsx": "screen-part0"}
    g = candidate_graph([*molecules, *mined], project_id="p1", source_ref=REF, identities=mapping, screens=screens)
    templates = {n["title"]: n for n in g["nodes"] if n["type"] == "PageTemplate"}
    assert len(templates) == 2 and "unmapped-template-structure" in g["coverage"]["unknown"]
    part = next(n for n in templates.values() if n["properties"]["uxModel"]["layers"]["code"]["exportName"] == "Panel")
    assert set(part["properties"]["uxModel"]["slots"]) == {"slot1", "slot3"}
    assert set(part["properties"]["uxModel"]["layers"]["code"]["props"]) == {"slot1", "slot3"}
    assert "unmapped region at position 2" in part["properties"]["description"]
    assert "part/1.tsx" in part["properties"]["description"] and part["properties"]["usageIds"] == ["screen-part0"]
    assert any(n["type"] == "Molecule" for n in g["nodes"])
    composes = [e for e in g["edges"] if e["type"] == "COMPOSES"]
    assert {e["src"]["id"] for e in composes} == {"screen-ok0", "screen-ok1", "screen-part0"}
    extra = [_local(a, "Organism") for a in ("asset-header", "asset-stack", "asset-footer")]
    extra += [_local(s, "Screen", properties={"pageId": s}) for s in screens.values()]
    graph, _ = _publishable(wb, g, extra)
    ontology = Ontology(context(wb))
    published = ontology.publish_candidate("mined", graph, expected_generation=None, request_id="mined-1")
    ids = published["identities"]
    stored = {n["title"]: n for n in ontology.read([ids[n["id"]] for n in g["nodes"] if n["type"] == "PageTemplate"])["nodes"]}
    ok = next(n for n in stored.values() if n["properties"]["uxModel"]["layers"]["code"]["exportName"] == "Screen")
    assert ok["properties"]["uxModel"]["slots"]["slot1"]["allowed"] == [ids["asset-header"]]
    assert sorted(ok["properties"]["usageIds"]) == sorted([ids["screen-ok0"], ids["screen-ok1"]])
    assert ok["reviewState"] == "candidate" and ok["provenance"] == "declared"
