"""Workbench integration tests use real Storage/CAS and bounded private blobs."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspace_collaboration import project_with_members, setup
from test_workspace_http import FakeLambda
from workspace.collaboration import CollaborationError
from workspace.http import WorkspaceAPI


@pytest.fixture
def wb(monkeypatch):
    # Simulate the additive Storage integration owned by the parent task.
    import workspace.storage as storage_module
    monkeypatch.setattr(storage_module, "KINDS", storage_module.KINDS | {
        "wb_source", "wb_batch", "wb_index", "wb_change", "wb_task", "wb_skill",
        "wb_artifact", "wb_pension", "wb_report", "wb_tool"})
    collab, storage = setup()
    project = project_with_members(collab)
    api = WorkspaceAPI(storage=storage, collaboration=collab, worker_fn="worker", lambda_client=FakeLambda())
    # Most focused tests deliberately exercise an unconfigured runtime. Tests
    # below install the real runtime and replace only its physical model call.
    api.workbench_model = api.workbench_gate = api.workbench_evaluator = None
    return SimpleNamespace(api=api, storage=storage, collab=collab, project=project,
                           owner="project:" + project["id"], now=1_800_000_000_000)


def call(wb, method, path, body=None, actor="alice", groups=None, query=None):
    from workbench.api import route
    claims = {"sub": actor, "token_use": "access", "exp": wb.now // 1000 + 3600,
              "cognito:groups": groups or []}
    scope = wb.collab.resolve_scope(actor, wb.project["id"])
    return route(wb.api, scope, claims, method, path.split("/"), body or {}, query or {})[1]


def source(wb, request="source", **values):
    return call(wb, "POST", "sources", {
        "requestId": request, "name": "테스트 자료", "kind": "snapshot", **values})["source"]


def documents():
    return [
        {"id": "guide", "title": "Synthetic withdrawal guidance", "revision": "r1",
         "content": "withdrawal limit confirmation notice",
         "entities": [
             {"id": "guide", "label": "Guideline", "title": "Withdrawal", "version": "r1"},
             {"id": "screen", "label": "Screen", "title": "Withdrawal screen", "version": "1"},
             {"id": "api", "label": "API", "title": "Withdrawal API", "version": "1"},
             {"id": "owner", "label": "Role", "title": "Designer", "version": "1"}],
         "relations": [
             {"src": "screen", "rel": "IMPLEMENTS", "dst": "guide"},
             {"src": "api", "rel": "DEPENDS_ON", "dst": "screen"},
             {"src": "owner", "rel": "OWNS", "dst": "screen"}]},
        {"id": "unrelated", "title": "Synthetic pension investment", "revision": "r1",
         "content": "pension investment retirement savings allocation"}]


def queue(wb, src, docs=None, request="batch"):
    return call(wb, "POST", f"sources/{src['id']}/batches",
                {"requestId": request, "documents": documents() if docs is None else docs})


def run(wb, payload):
    from workbench.worker import process
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab)
    for name in ("workbench_connections", "workbench_collector", "workbench_model",
                 "workbench_gate", "workbench_evaluator", "workbench_embeddings"):
        if hasattr(wb.api, name):
            setattr(worker, name, getattr(wb.api, name))
    for name in ("workbench_executor", "workbench_models", "workbench_token_provider", "workbench_effective_acl"):
        if hasattr(wb.api, name):
            setattr(worker, name, getattr(wb.api, name))
    job = wb.storage.claim_job(wb.owner, payload["job"]["id"])
    return process(worker, wb.owner, job or payload["job"])


def indexed(wb):
    src = source(wb)
    result = queue(wb, src)
    run(wb, result)
    return src, result


def test_client_roles_do_not_grant_operator_and_external_connections_are_allowlisted(wb):
    with pytest.raises(CollaborationError) as err:
        source(wb, kind="confluence", owner="admin", role="operator", connectionId="secret")
    assert err.value.status in (400, 403)
    with pytest.raises(CollaborationError) as err:
        call(wb, "POST", "sources", {"requestId": "external", "name": "C", "kind": "confluence",
                                   "connectionId": "missing"}, groups=["platform-operators"])
    assert err.value.code == "connection-not-configured"
    assert not call(wb, "GET", "overview")["operator"]
    assert call(wb, "GET", "overview", groups='["platform-operators"]')["operator"]


def test_source_creation_is_idempotent_and_never_trusts_scope_or_credentials(wb):
    first = source(wb)
    assert source(wb)["id"] == first["id"]
    with pytest.raises(CollaborationError) as err:
        source(wb, name="Changed")
    assert err.value.status == 409
    with pytest.raises(CollaborationError):
        source(wb, request="unsafe", scope={"url": "http://169.254.169.254", "token": "secret"})
    assert len(call(wb, "GET", "sources")["items"]) == 1


def test_project_revocation_between_read_and_commit_cancels_all_writes(wb):
    def revoke():
        project = wb.storage.get(wb.owner, "project", wb.project["id"])
        members = {k: v for k, v in project["members"].items() if k != "bob"}
        wb.storage.put(wb.owner, "project", {**project, "members": members}, project["version"])
    wb.storage.table().before_transaction = revoke
    with pytest.raises(CollaborationError) as err:
        call(wb, "POST", "sources", {"requestId": "revoke", "name": "X", "kind": "snapshot"}, actor="bob")
    assert err.value.status == 409
    assert wb.storage.list(wb.owner, "wb_source") == []


def test_examples_create_private_product_and_real_queryable_graph_idempotently(wb):
    first = call(wb, "POST", "examples", {"requestId": "example"})
    assert call(wb, "POST", "examples", {"requestId": "example"}) == first
    assert wb.storage.get(wb.owner, "product", first["productId"])
    result = call(wb, "GET", "knowledge", query={"q": "출금"})
    assert result["items"] and result["backend"]["vector"] == "private-artifact-exact-cosine"
    graph = call(wb, "GET", "dependencies")
    assert graph["nodes"] and graph["edges"]
    assert all(edge["provenance"] == "synthetic-fixture" for edge in graph["edges"])
    assert all(row["pk"].startswith("owner#") for row in wb.storage.table().items.values())


def test_confluence_collector_rejects_unapproved_profiles_and_bounds_pagination(wb):
    from workbench.collectors import collect
    calls = []
    def transport(profile, path, params):
        calls.append((path, params))
        if "/restriction/" in path:
            return {"restrictions": {"user": {"size": 0, "results": []},
                                     "group": {"size": 0, "results": []}}}
        return {"results": [{"id": "123", "title": "Guide", "version": {"number": 3}, "ancestors": [],
                              "body": {"storage": {"value": "<p>withdrawal</p>"}}}],
                "_links": {"next": "https://attacker.invalid/secret"}}
    profile = {"kind": "confluence", "approved": True, "baseUrl": "https://wiki.example.test",
               "spaceKey": "DEMO", "allowedProjectIds": [wb.project["id"]],
               "allowedRoles": ["owner", "planner"], "maxPages": 1}
    result = collect(profile, transport=transport)
    assert result["complete"] is False
    assert result["documents"][0]["content"] == "withdrawal"
    assert result["documents"][0]["allowedRoles"] == ["owner", "planner"]
    assert len(calls) == 2 and all("attacker" not in path for path, _ in calls)
    with pytest.raises(CollaborationError):
        collect({**profile, "approved": False}, transport=transport)


def test_core_route_delegates_parent_business_and_worker_pension_operation(wb, monkeypatch):
    from workbench import business
    from workbench.worker import process
    personas = call(wb, "GET", "pension/personas")["personas"]
    assert personas and personas[0]["id"] == "starter"
    session = call(wb, "POST", "pension/sessions", {"requestId": "pension", "personaId": "starter"})["session"]
    assert session["facts"]["totalBalance"] == 25_000_000
    # Only replace the parent job handler; dispatch selection belongs to this module.
    monkeypatch.setattr(business, "process", lambda worker, owner, job: {
        "sessionId": job["input"]["sessionId"], "owner": owner})
    job = {"task": "workbench", "input": {"operation": "pension_ask", "sessionId": session["id"]}}
    assert process(SimpleNamespace(storage=wb.storage), wb.owner, job)["sessionId"] == session["id"]


def test_external_scope_is_not_silently_accepted_or_persisted(wb):
    wb.api.workbench_connections = {"wiki": {
        "kind": "confluence", "approved": True, "baseUrl": "https://wiki.example.test", "spaceKey": "DEMO",
        "allowedProjectIds": [wb.project["id"]], "allowedRoles": ["owner", "planner"]}}
    with pytest.raises(CollaborationError) as err:
        call(wb, "POST", "sources", {"requestId": "url", "name": "Wiki", "kind": "confluence",
             "connectionId": "wiki", "scope": {"url": "https://attacker.invalid", "token": "private"}},
             groups=["admin"])
    assert err.value.status == 400


def test_external_collector_enforces_profile_roles_and_rechecks_configuration(wb):
    profile = {"kind": "confluence", "approved": True, "baseUrl": "https://wiki.example.test", "spaceKey": "DEMO",
               "allowedProjectIds": [wb.project["id"]], "allowedRoles": ["owner", "planner"]}
    wb.api.workbench_connections = {"wiki": profile}
    src = call(wb, "POST", "sources", {"requestId": "wiki", "name": "Wiki", "kind": "confluence",
                                     "connectionId": "wiki"}, groups=["admin"])["source"]
    wb.api.workbench_collector = lambda **kw: {"documents": [
        {**documents()[0], "allowedRoles": ["owner", "planner"]}], "complete": False}
    payload = call(wb, "POST", f"sources/{src['id']}/batches", {"requestId": "external"}, groups=["admin"])
    run(wb, payload)
    assert call(wb, "GET", "knowledge")["items"]
    assert call(wb, "GET", "knowledge")["coverage"]["complete"] is False
    assert call(wb, "GET", "knowledge", actor="carol")["items"] == []
    profile["approved"] = False
    assert call(wb, "GET", "knowledge")["items"] == []


def test_sources_replay_does_not_create_a_second_job_and_reports_current_status(wb):
    src = source(wb)
    first = queue(wb, src)
    run(wb, first)
    second = queue(wb, src)
    assert second["batch"]["id"] == first["batch"]["id"]
    assert second["batch"]["status"] == "completed"
    assert len(wb.storage.list(wb.owner, "job")) == 1


def test_confluence_ambiguous_or_restricted_acl_is_not_indexed_for_any_role(wb):
    from workbench.collectors import collect
    profile = {"kind": "confluence", "approved": True, "baseUrl": "https://wiki.example.test",
               "spaceKey": "DEMO", "allowedRoles": ["owner", "planner"], "maxPages": 1}
    def transport(profile, path, params):
        if "/restriction/" in path:
            return {"restrictions": {"user": {"size": 1, "results": [{"accountId": "restricted"}]},
                                     "group": {"size": 0, "results": []}}}
        return {"results": [{"id": "123", "title": "Restricted", "version": {"number": 1}, "ancestors": [],
                             "body": {"storage": {"value": "<p>Secret</p>"}}}], "_links": {}}
    result = collect(profile, transport=transport)
    assert result["documents"][0]["allowedRoles"] == []


def test_confluence_inherited_restrictions_and_missing_ancestors_fail_closed(wb):
    from workbench.collectors import collect
    profile = {"kind": "confluence", "approved": True, "baseUrl": "https://wiki.example.test",
               "spaceKey": "DEMO", "allowedRoles": ["owner", "planner"], "maxPages": 1}
    def transport(profile, path, params):
        if "/restriction/" in path:
            users = [{"accountId": "restricted"}] if "/9/" in path else []
            return {"restrictions": {"user": {"size": len(users), "results": users},
                                     "group": {"size": 0, "results": []}}}
        return {"results": [
            {"id": "123", "title": "Inherited private", "version": {"number": 1}, "ancestors": [{"id": "9"}],
             "workbenchAllowedRoles": ["owner"], "body": {"storage": {"value": "private"}}},
            {"id": "124", "title": "No ancestors proof", "version": {"number": 1},
             "body": {"storage": {"value": "ambiguous"}}}], "_links": {}}
    result = collect(profile, transport=transport)
    assert all(doc["allowedRoles"] == [] for doc in result["documents"])


def test_confluence_trusted_effective_acl_resolver_can_handle_restricted_groups(wb):
    from workbench.collectors import collect
    profile = {"kind": "confluence", "approved": True, "baseUrl": "https://wiki.example.test",
               "spaceKey": "DEMO", "allowedRoles": ["owner", "planner"], "maxPages": 1}
    def transport(profile, path, params):
        return {"results": [{"id": "123", "title": "Resolved", "version": {"number": 1},
                             "body": {"storage": {"value": "resolved"}}}], "_links": {}}
    result = collect(profile, transport=transport, effective_acl_resolver=lambda **kw: {
        "effective": True, "allowedRoles": ["planner"]})
    assert result["documents"][0]["allowedRoles"] == ["planner"]


def test_secret_reference_uses_existing_provider_without_accepting_raw_credentials(wb, monkeypatch):
    from workbench.collectors import server_token
    from workspace import git_service
    received = []
    monkeypatch.setattr(git_service, "secret_token", lambda value: received.append(value) or "test-token")
    arn = "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:workbench-test"
    assert server_token(arn) == "test-token"
    assert received == [{"secretArn": arn}]
    with pytest.raises(CollaborationError):
        server_token("raw-token")


def test_git_collector_indexes_only_configured_immutable_paths_with_verified_bytes(wb):
    import base64
    import hashlib
    from workbench.collectors import collect
    raw = b"# Fixture\nwithdrawal guidance\n"
    blob_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
    profile = {"kind": "git", "approved": True, "baseUrl": "https://api.github.com",
               "provider": "github", "repository": "fictional/repo", "ref": "a" * 40,
               "paths": ["docs/guide.md"], "allowedRoles": ["owner", "developer"]}
    calls = []
    def transport(profile, path, params):
        calls.append((path, params))
        return {"type": "file", "path": "docs/guide.md", "encoding": "base64", "size": len(raw),
                "sha": blob_sha, "content": base64.b64encode(raw).decode()}
    result = collect(profile, transport=transport)
    assert result["complete"] is True and result["documents"][0]["content"] == raw.decode()
    assert result["documents"][0]["revision"] == "a" * 40
    assert calls == [("/repos/fictional/repo/contents/docs/guide.md", {"ref": "a" * 40})]
    with pytest.raises(CollaborationError):
        collect({**profile, "ref": "main"}, transport=transport)
    with pytest.raises(CollaborationError):
        collect({**profile, "paths": ["../../secret"]}, transport=transport)


def test_worker_persists_failed_batch_status_without_republishing_source(wb, monkeypatch):
    src = source(wb)
    payload = queue(wb, src)
    def failed(*args, **kwargs):
        raise RuntimeError("raw confidential SDK detail")
    monkeypatch.setattr(wb.storage, "put_blob_once", failed)
    with pytest.raises(CollaborationError):
        run(wb, payload)
    batch = wb.storage.get(wb.owner, "wb_batch", payload["batch"]["id"])
    assert batch["status"] == "failed" and "confidential" not in batch["error"]
    assert wb.storage.get(wb.owner, "wb_index", "current") is None


def test_parent_http_and_worker_complete_real_snapshot_end_to_end(wb):
    from workspace.worker import Worker
    # Exercise actual parent routing and job completion rather than the direct
    # module helper used by focused authorization tests.
    def request(method, path, body):
        result = wb.api.handle({"rawPath": "/studio-api/workbench/" + path,
            "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": {
                "sub": "alice", "token_use": "access", "exp": wb.now // 1000 + 3600}}}},
            "headers": {"X-Workspace-Project": wb.project["id"]}, "body": json.dumps(body)})
        return result["statusCode"], json.loads(result["body"])
    status, result = request("POST", "sources", {"requestId": "http", "name": "HTTP fixture", "kind": "snapshot"})
    assert status == 201
    status, queued = request("POST", f"sources/{result['source']['id']}/batches",
                            {"requestId": "http-batch", "documents": documents()})
    assert status == 202
    worker = Worker(storage=wb.storage)
    assert worker.handle({"owner": wb.owner, "jobId": queued["job"]["id"]})["status"] == "completed"
    assert wb.storage.get(wb.owner, "job", queued["job"]["id"])["status"] == "completed"
    status, result = request("GET", "knowledge", {})
    assert status == 200 and len(result["items"]) == 2
    assert "vectorKey" not in json.dumps(result)


def test_dispatch_failure_updates_batch_and_unconfigured_worker_does_not_invalidate_index(wb):
    src, _ = indexed(wb)
    wb.api.worker_fn = ""
    with pytest.raises(Exception):
        queue(wb, src, request="not-ready")
    assert len(call(wb, "GET", "knowledge")["items"]) == 2
    wb.api.worker_fn = "worker"
    wb.api.lambda_client = SimpleNamespace(invoke=lambda **kw: {"StatusCode": 500})
    with pytest.raises(Exception):
        queue(wb, src, request="dispatch-failure")
    failed = [b for b in wb.storage.list(wb.owner, "wb_batch") if b.get("status") == "failed"]
    assert len(failed) == 1 and failed[0]["errorCode"] == "dispatch-failed"


def test_external_acl_expiry_is_measured_from_collection_start_not_end(wb):
    wb.api.workbench_connections = {"wiki": {"kind": "confluence", "approved": True,
        "baseUrl": "https://wiki.example.test", "spaceKey": "DEMO",
        "allowedProjectIds": [wb.project["id"]], "allowedRoles": ["owner"]}}
    src = call(wb, "POST", "sources", {"requestId": "age", "name": "Wiki", "kind": "confluence",
        "connectionId": "wiki"}, groups=["admin"])["source"]
    started = wb.now
    def collector(**kw):
        wb.now += 60_000
        wb.storage.clock = lambda: wb.now
        return {"documents": [{**documents()[1], "allowedRoles": ["owner"]}], "complete": True}
    wb.api.workbench_collector = collector
    job = call(wb, "POST", f"sources/{src['id']}/batches", {"requestId": "age-batch"}, groups=["admin"])
    run(wb, job)
    assert wb.storage.get(wb.owner, "wb_source", src["id"])["accessExpiresAt"] == started + 300_000
