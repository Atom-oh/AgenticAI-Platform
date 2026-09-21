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
