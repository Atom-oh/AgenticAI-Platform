import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_collaboration import DRAFT, PEOPLE
from test_workspace_http import FakeLambda
from test_workspace_storage import FakeS3, FakeTable
from workspace.http import WorkspaceAPI
from workspace.storage import Storage


def make_api():
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test")
    return WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker",
                        directory=lambda query: [person for person in PEOPLE if query in person["sub"]])


def request(api, method, path, body=None, actor="alice", project=None, query=None):
    event = {"rawPath": "/studio-api" + path, "requestContext": {
        "http": {"method": method}, "authorizer": {"jwt": {"claims": {"sub": actor, "token_use": "access"}}}},
        "headers": {"X-Workspace-Project": project} if project else {},
        "queryStringParameters": query or {}, "body": json.dumps(body or {})}
    result = api.handle(event)
    return result["statusCode"], json.loads(result["body"])


def shared(api):
    status, payload = request(api, "POST", "/projects", {"name": "Shared design", "requestId": "new-project"})
    assert status == 201, payload
    project = payload["project"]
    members = {person["sub"]: {"role": role, "displayName": person["displayName"]} for person, role in
               zip(PEOPLE, ["owner", "planner", "designer", "developer"])}
    status, payload = request(api, "PUT", f"/projects/{project['id']}/members",
                              {"version": project["version"], "members": members})
    assert status == 200, payload
    return payload["project"]


def test_project_header_does_not_grant_membership_or_mix_personal_files():
    api = make_api()
    project = shared(api)
    body = {"name": "guide.txt", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(), "purpose": "guide"}
    status, payload = request(api, "POST", "/assets", body, project=project["id"])
    assert status == 201, payload
    asset = payload["asset"]
    assert api.storage.get("project:" + project["id"], "asset", asset["id"])
    assert api.storage.get("alice", "asset", asset["id"]) is None
    assert request(api, "GET", "/assets/" + asset["id"], actor="bob", project=project["id"])[0] == 200
    assert request(api, "GET", "/assets/" + asset["id"], actor="mallory", project=project["id"])[0] == 403
    assert request(api, "GET", "/assets/" + asset["id"], actor="alice")[0] == 404


def test_project_roles_are_enforced_before_generation_or_product_mutation():
    api = make_api()
    project = shared(api)
    assert request(api, "POST", "/runs", {}, actor="bob", project=project["id"])[0] == 403
    assert request(api, "POST", "/runs", {}, actor="dana", project=project["id"])[0] == 403
    assert request(api, "POST", "/products", {}, actor="carol", project=project["id"])[0] == 403
    assert api.lambda_client.calls == []


def test_approval_records_the_actor_not_the_shared_storage_owner():
    api = make_api()
    project = shared(api)
    status, payload = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    assert status == 201, payload
    product = payload["product"]
    assert request(api, "POST", f"/products/{product['id']}/publish", {"version": product["version"]},
                   actor="bob", project=project["id"])[0] == 200
    body = {"productId": product["id"], "title": "Required control", "rules": [{"id": "R1", "title": "Next exists",
            "steps": [{"action": "expectVisible", "target": "next", "value": True}]},
            {"id": "Notice", "title": "필수 안내", "steps": [{"action": "expectText", "target": "notice-consent",
             "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}]}
    status, payload = request(api, "POST", "/contracts", body, actor="carol", project=project["id"])
    assert status == 201, payload
    contract = payload["contract"]
    status, payload = request(api, "POST", f"/contracts/{contract['id']}/approve",
                              {"version": contract["version"], "actor": "mallory"}, actor="carol", project=project["id"])
    assert status == 200, payload
    assert payload["contract"]["approval"]["actor"] == "carol"
    assert payload["contract"]["productId"] == product["id"]
    assert payload["contract"]["guidelineAssetId"] in payload["contract"]["assetIds"]
    assert request(api, "DELETE", "/assets/" + payload["contract"]["guidelineAssetId"],
                   actor="carol", project=project["id"])[0] == 409
    assert request(api, "GET", "/config", actor="carol", project=project["id"])[1]["actorId"] == "carol"


def test_guideline_change_during_approval_cannot_cross_the_project_fence(monkeypatch):
    api = make_api()
    project = shared(api)
    _, created = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = created["product"]
    _, published = request(api, "POST", f"/products/{product['id']}/publish",
                           {"version": product["version"]}, actor="bob", project=project["id"])
    product = published["product"]
    body = {"productId": product["id"], "title": "Rule", "rules": [{"id": "R1", "title": "Next",
            "steps": [{"action": "expectVisible", "target": "next", "value": True}]},
            {"id": "Notice", "title": "필수 안내", "steps": [{"action": "expectText", "target": "notice-consent",
             "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}]}
    _, created = request(api, "POST", "/contracts", body, actor="carol", project=project["id"])
    contract = created["contract"]
    validate = api._validated_contract
    changed = False

    def interleaved(owner, data):
        nonlocal changed
        result = validate(owner, data)
        if not changed:
            changed = True
            status, updated = request(api, "PUT", f"/products/{product['id']}",
                                      {"version": product["version"], "description": "Updated planning criterion"},
                                      actor="bob", project=project["id"])
            assert status == 200
            assert request(api, "POST", f"/products/{product['id']}/publish",
                           {"version": updated["product"]["version"]}, actor="bob", project=project["id"])[0] == 200
        return result

    monkeypatch.setattr(api, "_validated_contract", interleaved)
    assert request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                   actor="carol", project=project["id"])[0] == 409
    assert api.storage.get("project:" + project["id"], "contract", contract["id"])["status"] == "draft"
