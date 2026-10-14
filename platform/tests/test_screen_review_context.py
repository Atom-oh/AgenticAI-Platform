"""Direct approval evidence stays bounded independently of a large project closure."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_screen_derivation import derivation, integrated, env, api
from workspace import ontology_schema as schema
from workspace.ontology_store import Ontology
from workspace.ontology_api import route
from workspace.collaboration import CollaborationError


def test_large_dependency_closure_does_not_hide_direct_screen_review(derivation):
    state = derivation
    store = Ontology(state.ctx)
    target = state.ids["screen-first"]
    source = state.ctx.get("wb_artifact", state.artifact_id)["sourceRefs"][0]
    base = {"scope": {"kind": "project", "projectId": state.ctx.project_id}, "revision": 1,
            "sourceRefs": [source], "provenance": "declared", "reviewState": "candidate", "tombstone": False}
    nodes = [schema.seal({**base, "id": f"other-screen-{i}", "type": "Screen", "title": f"다른 화면 {i}"})
             for i in range(55)]
    edges = [schema.seal({"id": f"other-use-{i}", "type": "USES", "src": {"id": target, "revision": 1},
             "dst": {"id": node["id"], "revision": 1}, "sourceRefs": [source], "provenance": "declared",
             "reviewState": "candidate", "tombstone": False}) for i, node in enumerate(nodes)]
    store.publish_candidate("large-neighborhood", {"schemaVersion": 1, "projectId": state.ctx.project_id,
        "nodes": nodes, "edges": edges}, expected_generation=store.current()["generation"], request_id="large-neighborhood")
    assert store.context([target])["coverage"]["truncated"] is True
    status, view = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET",
        ["nodes", target, "review-context"], {}, {})
    assert status == 200 and view["coverage"]["complete"] is True
    assert len(view["edges"]) == 1 and len(view["nodes"]) == 2
    assert view["edges"][0]["src"]["id"] == state.code["src/First.tsx"]["id"]
    assert view["generation"] == store.current()["generation"]


def test_review_context_hides_withdrawn_sources_and_requires_screen(derivation):
    from intake.records import INTAKE_OWNER
    state = derivation
    with pytest.raises(CollaborationError) as error:
        Ontology(state.ctx).review_context(state.code["src/First.tsx"]["id"])
    assert error.value.status == 404
    grant = state.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    state.env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    with pytest.raises(CollaborationError):
        Ontology(state.ctx).review_context(state.ids["screen-first"])
