"""Route inventory for the single response-layer authorization gate (PR #29 review 4).

Every project-scoped workspace route must be registered with the gate in
`workspace.http.ROUTES`, declaring the authorized views its JSON or blob
responses are serialized through; unregistered paths are never dispatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workspace import http  # noqa: E402

# The workspace prefixes whose resources carry source-bound project content.
GATED_PREFIXES = {"assets", "contracts", "runs", "releases", "batches", "jobs", "publications"}


def test_every_project_scoped_route_is_registered_with_the_gate():
    seen = set()
    for route in http.ROUTES:
        assert route.method in {"GET", "POST", "PUT", "DELETE"}
        key = (route.method, route.pattern)
        assert key not in seen, key
        seen.add(key)
        if route.pattern[0] in GATED_PREFIXES or route.pattern[0] in http.DELEGATED:
            assert not route.public, route
            # Something authorizes the response: a gated resource, response views or a blob view.
            assert route.resource or route.views or route.blob, route
        else:
            assert route.public, route
        for key_name, view in route.views.items():
            assert view in http.ResponseGate.VIEWS, (route, key_name, view)
        if route.blob:
            assert route.blob in http.ResponseGate.BLOBS, route
        if route.resource:
            assert route.resource in http.ResponseGate.VIEWS, route
    assert {route.pattern[0] for route in http.ROUTES} >= GATED_PREFIXES


def test_every_top_level_prefix_is_gated_or_explicitly_delegated():
    # Delegated modules own their authority and are listed by name, never by fallthrough.
    assert set(http.DELEGATED) == {"projects", "products", "comments", "ontology", "intake", "workbench",
                                   "documents", "impact-analyses"}
    # Delegated routes that return run/round-derived metadata are registered with the gate.
    registered = {(route.method, route.pattern): route for route in http.ROUTES}
    for key, view in ((("GET", ("products", ":id", "impact")), "affectedRuns"),
                      (("GET", ("comments",)), "comments"), (("POST", ("comments",)), "comment")):
        assert key in registered and view in registered[key].views, key


# Workspace record kinds whose content is run/round/source-derived. A delegated module
# that reads one is allowed only in the functions named here, each served exclusively
# by a route registered with the gate (or independently self-authorized before disclosure).
_WORKSPACE_KINDS = {"run", "release", "contract", "gitexport", "batch", "job"}
_ALLOWED_READS = {
    # `_anchor` resolves run anchors of comments; GET/POST /comments are gated routes
    # (round="anchor" and the `comment` view authorize every run and round).
    ("workspace/collaboration.py", "_anchor"),
    # Workbench batch detail reads its own queue job status only, for a wb_batch record
    # already fetched/paged in the caller's own project scope (no foreign run content).
    ("workbench/api.py", "_list"), ("workbench/api.py", "_route"),
    # documents/api.py:_complete calls authorize_job() on the job it reads before ever
    # returning it -- the same creator/source authority GET /jobs applies.
    ("documents/api.py", "_complete"),
    # documents/analysis.py:_create's analysis id is fingerprinted from the requesting
    # actor, so the job it reads back is inherently that actor's own; it also reruns
    # authorize_analysis before returning. _publish and process_analysis are worker-side
    # consistency rechecks (job status/input match) before a commit, never serialized to
    # an HTTP caller.
    ("documents/analysis.py", "_create"), ("documents/analysis.py", "_publish"),
    ("documents/analysis.py", "process_analysis"),
}


def _is_reader_call(node):
    """A call that resolves a stored record by kind: `<...>.storage.get/list_page(...)`,
    `ctx.get/page/bounded_list/list_page(...)`, or `self._get(...)` (collaboration's own
    resource lookup). Excludes an ordinary `dict.get("run", ...)` on a domain object that
    merely happens to share a kind's name (e.g. a `run` or `job` variable)."""
    import ast
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    attr, receiver = node.func.attr, node.func.value
    if attr not in {"get", "list_page", "list", "_get", "page", "bounded_list"}:
        return False
    if isinstance(receiver, ast.Attribute) and receiver.attr == "storage":
        return True
    if isinstance(receiver, ast.Name) and receiver.id == "ctx":
        return True
    return isinstance(receiver, ast.Name) and receiver.id == "self" and attr == "_get"


def _kind_reads(path):
    import ast
    tree = ast.parse(Path(path).read_text())
    reads = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if _is_reader_call(node) and any(
                    isinstance(arg, ast.Constant) and arg.value in _WORKSPACE_KINDS for arg in node.args):
                reads.add(function.name)
    return reads


def test_delegated_modules_read_run_derived_records_only_through_gated_routes():
    root = Path(__file__).resolve().parents[1]
    modules = {"workspace/collaboration.py", "workspace/ontology_api.py", "intake/review.py", "workbench/api.py",
               "workbench/business.py", "documents/api.py", "documents/analysis.py"}
    for module in sorted(modules):
        for function in sorted(_kind_reads(root / module)):
            assert (module, function) in _ALLOWED_READS, (
                f"{module}:{function} reads a run-derived workspace record outside a gated route; "
                "register the route in workspace.http.ROUTES with an authorized view")


def test_unregistered_paths_are_never_dispatched(monkeypatch):
    api = http.WorkspaceAPI.__new__(http.WorkspaceAPI)
    for method, parts in (("GET", ["runs", "r1", "secret"]), ("POST", ["runs", "r1", "blob"]),
                          ("PATCH", ["contracts", "c1"]), ("GET", ["publications", "p", "grants", "g"]),
                          ("GET", ["exports"]), ("DELETE", ["runs", "r1"])):
        assert http.match_route(method, parts) is None, (method, parts)
    route, params = http.match_route("GET", ["runs", "r1", "blob"])
    assert route.blob == "run-round" and params == {"id": "r1"}


def test_responses_with_undeclared_fields_fail_closed():
    route, _ = http.match_route("GET", ["contracts", "c1"])
    gate = http.ResponseGate(None, None, None, route)
    response = http._json(200, {"contract": {"id": "c1"}, "leaked": {"id": "x"}})
    try:
        gate.finish(response)
    except http.HTTPError as error:
        assert error.status == 503
    else:
        raise AssertionError("an undeclared response field must not be released")
