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
        if route.pattern[0] in GATED_PREFIXES:
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
    prefixes = {route.pattern[0] for route in http.ROUTES}
    assert prefixes.isdisjoint(http.DELEGATED)
    # Delegated modules own their authority and are listed by name, never by fallthrough.
    assert set(http.DELEGATED) == {"projects", "products", "comments", "ontology", "intake", "workbench",
                                   "documents", "impact-analyses"}


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
