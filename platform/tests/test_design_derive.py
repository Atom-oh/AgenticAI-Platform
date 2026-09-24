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
