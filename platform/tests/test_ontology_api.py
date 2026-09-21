import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_store import candidate
from workspace.ontology_sources import asset_reference


def call(wb, method, path, body=None, actor="alice", project=None, authenticated=True):
    event = {"rawPath": "/studio-api/ontology" + path, "headers": {
        "X-Workspace-Project": project or wb.project["id"]}, "requestContext": {"http": {"method": method}}}
    if authenticated:
        event["requestContext"]["authorizer"] = {"jwt": {"claims": {
            "sub": actor, "token_use": "access", "exp": wb.now // 1000 + 3600}}}
    if body is not None:
        event["body"] = json.dumps(body)
    result = wb.api.handle(event)
    return result["statusCode"], json.loads(result["body"])


def setup_graph(wb):
    value = candidate(wb)
    status, published = call(wb, "POST", "/partitions", {
        "name": "example", "requestId": "first", "expectedGeneration": None, "graph": value})
    assert status == 201, published
    return published


def test_api_uses_real_jwt_and_project_membership_boundary(wb):
    assert call(wb, "GET", "/schema", authenticated=False)[0] == 401
    assert call(wb, "GET", "/schema", actor="outsider")[0] == 403
    code, value = call(wb, "GET", "/schema")
    assert code == 200 and len(value["levels"]) == 8
    assert value["backend"] == "workspace-project-ontology"
    assert value["analyzerConfigured"] is False


def test_source_change_returns_real_reverse_path_and_generation(wb):
    published = setup_graph(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    status, result = call(wb, "POST", "/impact", {"changeId": "image-change", "kind": "asset",
        "oldSource": asset_reference(row), "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert {item["title"] for item in result["items"]} == {"image", "control"}
    assert result["coverage"]["complete"] is False
    assert all(item["evidenceKind"] == "candidate" for item in result["items"])
    status, context = call(wb, "POST", "/context", {"nodeIds": [published["identities"]["image"]]})
    assert status == 200 and len(context["nodes"]) == 2 and context["hash"]


def test_human_review_requires_role_exact_versions_and_prior_review(wb):
    published = setup_graph(wb)
    identifier = published["identities"]["control"]
    request = {"requestId": "review-1", "expectedGeneration": published["generation"],
               "revision": 1, "decision": "approved", "reason": "Synthetic review"}
    assert call(wb, "POST", f"/nodes/{identifier}/review", request)[0] == 409
    request["decision"] = "reviewed"
    status, reviewed = call(wb, "POST", f"/nodes/{identifier}/review", request)
    assert status == 200, reviewed
    assert reviewed["node"]["reviewState"] == "reviewed"
    stale = {**request, "requestId": "stale"}
    assert call(wb, "POST", f"/nodes/{identifier}/review", stale)[0] == 409
    approved = {**request, "requestId": "approve", "decision": "approved", "revision": 2,
                "expectedGeneration": reviewed["generation"]}
    status, result = call(wb, "POST", f"/nodes/{identifier}/review", approved)
    assert status == 200, result
    assert result["node"]["reviewState"] == "approved" and result["review"]["actor"] == "alice"
    assert result["review"]["sourceRefs"]


def test_bad_fields_and_forged_actor_are_not_project_authority(wb):
    value = candidate(wb)
    status, _ = call(wb, "POST", "/partitions", {
        "name": "example", "requestId": "first", "graph": value, "actor": "admin"})
    assert status == 400
    assert call(wb, "POST", "/context", {"nodeIds": [], "projectId": "other"})[0] == 400


def test_archived_image_still_identifies_readable_dependents_as_historical_impact(wb):
    published = setup_graph(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    old = asset_reference(row)
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    status, result = call(wb, "POST", "/impact", {"changeId": "archived-image", "kind": "asset",
        "oldSource": old, "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert "control" in {item["title"] for item in result["items"]}
    assert "historical-source-revisions" in result["coverage"]["unknown"]
    assert call(wb, "POST", "/context", {"nodeIds": [published["identities"]["image"]]})[0] == 409


def test_reviewed_node_keeps_cross_partition_dependents_as_stale_witnesses(wb):
    from test_ontology_sources import asset
    from test_ontology_schema import node, edge
    published = setup_graph(wb)
    source = asset(wb, "screen-source")
    external = published["identities"]["control"]
    graph = {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": [
        node("screen", "Screen", project=wb.project["id"], sourceRefs=[asset_reference(source)])],
        "edges": [edge("cross", "screen", external, sourceRefs=[asset_reference(source)])]}
    status, added = call(wb, "POST", "/partitions", {"name": "screens", "requestId": "cross",
        "expectedGeneration": published["generation"], "graph": graph})
    assert status == 201, added
    status, reviewed = call(wb, "POST", f"/nodes/{external}/review", {
        "requestId": "review-cross", "expectedGeneration": added["generation"], "revision": 1,
        "decision": "reviewed", "reason": "Synthetic review"})
    assert status == 200, reviewed
    status, impact = call(wb, "POST", "/impact", {"changeId": "review-impact", "kind": "design",
        "nodeIds": [external], "expectedGeneration": reviewed["generation"]})
    assert status == 200, impact
    screen = next(item for item in impact["items"] if item["title"] == "screen")
    assert screen["staleWitness"] and screen["evidenceKind"] == "candidate"
    assert len(screen["witnessPath"]) == 2


def test_partition_removal_preserves_past_impact_and_does_not_reuse_revisions(wb):
    from workspace.ontology_store import Ontology
    from test_ontology_sources import context
    original = candidate(wb)
    status, first = call(wb, "POST", "/partitions", {
        "name": "lifecycle", "requestId": "initial", "expectedGeneration": None, "graph": original})
    assert status == 201
    reduced = {**original, "nodes": [original["nodes"][1]], "edges": []}
    status, removed = call(wb, "POST", "/partitions", {
        "name": "lifecycle", "requestId": "remove", "expectedGeneration": first["generation"], "graph": reduced})
    assert status == 201, removed
    status, result = call(wb, "POST", "/impact", {"changeId": "removed-image", "kind": "asset",
        "oldSource": original["nodes"][0]["sourceRefs"][0], "expectedGeneration": removed["generation"]})
    assert status == 200, result
    dependent = next(item for item in result["items"] if item["title"] == "control")
    assert dependent["staleWitness"] and dependent["evidenceKind"] == "candidate"
    assert {n["title"] for n in Ontology(context(wb)).read()["nodes"]} == {"control"}
    status, restored = call(wb, "POST", "/partitions", {
        "name": "lifecycle", "requestId": "reintroduce", "expectedGeneration": removed["generation"], "graph": original})
    assert status == 201, restored
    image = next(n for n in Ontology(context(wb)).read()["nodes"] if n["title"] == "image")
    assert image["id"] == first["identities"]["image"] and image["revision"] == 3


def test_revoked_source_is_an_opaque_boundary_not_a_leaked_witness(wb):
    published = setup_graph(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    old = asset_reference(row)
    wb.storage.put(wb.owner, "asset", {**row, "accessRevoked": True}, row["version"])
    status, result = call(wb, "POST", "/impact", {"changeId": "revoked-image", "kind": "asset",
        "oldSource": old, "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert [item["title"] for item in result["items"]] == ["control"]
    assert published["identities"]["image"] not in json.dumps(result)
    assert "restricted-source-boundary" in result["coverage"]["unknown"]


def test_malformed_impact_seed_types_are_client_errors(wb):
    published = setup_graph(wb)
    for bad in (None, 7, "not-a-list", [None]):
        assert call(wb, "POST", "/impact", {"changeId": "bad", "kind": "asset",
            "nodeIds": bad, "expectedGeneration": published["generation"]})[0] == 400
