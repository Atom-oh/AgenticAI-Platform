"""Private document flows through access-token authentication and real CAS storage."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_http import FakeLambda
from test_workspace_storage import FakeS3, FakeTable
from workspace.http import WorkspaceAPI
from workspace.storage import Storage

CHUNK = 2 * 1024 * 1024


class DocumentHost(WorkspaceAPI):
    """Use the real JWT/scope boundary while parent owns workspace routing."""

    def _route(self, owner, method, parts, event, query, scope=None, claims=None):
        if parts[0] == "documents":
            from documents.api import handle
            return handle(self, scope, method, parts, event, query)
        return super()._route(owner, method, parts, event, query, scope=scope, claims=claims)


@pytest.fixture
def api():
    from graph.store import LocalGraphStore, Node
    host = DocumentHost(
        storage=Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test"),
        lambda_client=FakeLambda(), worker_fn="worker",
        directory=lambda q: [{"sub": x, "displayName": x} for x in ("alice", "bob", "carol", "dana")],
    )
    host.graph_store = LocalGraphStore()
    host.graph_store.upsert_nodes([
        Node("REG-1", "Regulation", {"title": "Synthetic regulation"}),
        Node("DOC-1", "Document", {"title": "Synthetic policy"}),
        Node("SCREEN-1", "ScreenMeta", {"title": "Not a document"}),
    ])
    return host


def call(api, method, path, body=None, actor="alice", project=None, query=None, token="access"):
    event = {"rawPath": "/studio-api" + path, "headers": {},
             "requestContext": {"http": {"method": method},
                                "authorizer": {"jwt": {"claims": {"sub": actor, "token_use": token}}}},
             "queryStringParameters": query or {}}
    if project:
        event["headers"]["X-Workspace-Project"] = project
    if isinstance(body, bytes):
        event.update(body=base64.b64encode(body).decode(), isBase64Encoded=True)
    elif body is not None:
        event["body"] = json.dumps(body)
    result = api.handle(event)
    payload = base64.b64decode(result["body"]) if result.get("isBase64Encoded") else json.loads(result["body"])
    return result["statusCode"], payload, result.get("headers", {})


def project(api):
    status, payload, _ = call(api, "POST", "/projects", {"name": "Synthetic team", "requestId": "team"})
    assert status == 201, payload
    row = payload["project"]
    status, payload, _ = call(api, "PUT", f"/projects/{row['id']}/members", {
        "version": row["version"], "members": {
            "alice": {"role": "owner"}, "bob": {"role": "planner"},
            "carol": {"role": "designer"}, "dana": {"role": "developer"},
        },
    })
    assert status == 200, payload
    return row["id"]


def begin(api, data=b"  Synthetic literal 10.00%.\r\n\r\nSecond.\n", name="policy.txt",
          request="first", actor="alice", project=None, **fields):
    body = {"requestId": request, "title": "Synthetic policy", "kind": "policy", "name": name,
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **fields}
    status, result, _ = call(api, "POST", "/documents", body, actor=actor, project=project)
    assert status == 201, result
    return result


def path(result):
    return f"/documents/{result['document']['id']}/revisions/{result['revision']['id']}"


def upload(api, data=b"  Synthetic literal 10.00%.\r\n\r\nSecond.\n", actor="alice", project=None, **fields):
    result = begin(api, data=data, actor=actor, project=project, **fields)
    for index in range((len(data) + CHUNK - 1) // CHUNK):
        status, payload, _ = call(api, "PUT", path(result) + f"/parts/{index}",
                                 data[index * CHUNK:(index + 1) * CHUNK], actor=actor, project=project)
        assert status == 200, payload
    status, payload, _ = call(api, "POST", path(result) + "/complete", {}, actor=actor, project=project)
    assert status == 202, payload
    return payload


def finalize(api, result, actor="alice", project=None):
    from documents.intake import finalize as process
    owner = f"project:{project}" if project else actor
    job = api.storage.claim_job(owner, result["job"]["id"])
    worker = SimpleNamespace(storage=api.storage, collaboration=api.collaboration)
    process(worker, owner, job)
    status, payload, _ = call(api, "GET", path(result), actor=actor, project=project)
    assert status == 200, payload
    return payload


def approve(api, result, actor="alice", project=None):
    status, result, _ = call(api, "POST", path(result) + "/submit",
                             {"version": result["revision"]["version"]}, actor=actor, project=project)
    assert status == 200, result
    status, result, _ = call(api, "POST", path(result) + "/review",
                             {"version": result["revision"]["version"], "decision": "approved", "note": "Reviewed"},
                             actor=actor, project=project)
    return status, result


def test_create_is_idempotent_private_and_access_token_only(api):
    first = begin(api)
    assert begin(api)["revision"]["id"] == first["revision"]["id"]
    assert len(api.storage.list("alice", "document")) == 1
    assert api.storage.list("alice", "asset") == []
    assert "requestHash" not in repr(first) and "originalKey" not in repr(first) and "'parts'" not in repr(first)
    assert call(api, "GET", "/documents/config", token="id")[0] == 401
    changed = {"requestId": "first", "title": "Changed", "kind": "policy", "name": "policy.txt",
               "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    assert call(api, "POST", "/documents", changed)[0] == 409
    for actor in ("foreign", "bob"):
        status, payload, _ = call(api, "GET", f"/documents/{first['document']['id']}", actor=actor)
        assert status in (403, 404)
        assert "originalKey" not in repr(payload)


@pytest.mark.parametrize("fields", [
    {"name": "program.py"}, {"name": "data.json"}, {"size": 20 * 1024 * 1024 + 1},
    {"size": 0}, {"sha256": "not-a-hash"}, {"readRoles": ["designer"]},
    {"readRoles": ["owner", "unknown"]}, {"provenance": "synthetic_sample"},
    {"graphRef": "SCREEN-1"}, {"graphRef": "REG-missing"}, {"effectiveDate": "2026-02-30"},
])
def test_invalid_registration_never_writes(api, fields):
    body = {"requestId": "bad", "title": "Synthetic", "kind": "policy", "name": "a.txt",
            "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(), **fields}
    assert call(api, "POST", "/documents", body)[0] == 400
    assert api.storage.list("alice", "document") == []
    assert api.storage.s3().puts == []


def test_restricted_document_is_absent_from_listing_search_download_and_foreign_scope(api):
    pid = project(api)
    result = finalize(api, upload(api, project=pid, readRoles=["owner", "planner"]), project=pid)
    for actor in ("carol", "dana"):
        status, payload, _ = call(api, "GET", "/documents", actor=actor, project=pid, query={"q": "Synthetic"})
        assert status == 200 and payload["documents"] == []
        for endpoint in (f"/documents/{result['document']['id']}", path(result), path(result) + "/blob"):
            assert call(api, "GET", endpoint, actor=actor, project=pid)[0] in (403, 404)
    assert call(api, "GET", path(result), actor="bob", project=pid)[0] == 200
    assert call(api, "GET", path(result), actor="alice")[0] in (403, 404)
    assert call(api, "GET", path(result), actor="foreign", project=pid)[0] == 403


def test_creator_edit_right_does_not_grant_review_or_permissions(api):
    pid = project(api)
    result = finalize(api, upload(api, actor="carol", project=pid), actor="carol", project=pid)
    detail = call(api, "GET", f"/documents/{result['document']['id']}", actor="carol", project=pid)[1]
    assert detail["capabilities"] == {"read": True, "edit": True, "review": False, "manage": False}
    assert call(api, "PUT", f"/documents/{result['document']['id']}/permissions",
                {"version": result["document"]["version"], "readRoles": ["owner"]},
                actor="carol", project=pid)[0] == 403
    status, submitted, _ = call(api, "POST", path(result) + "/submit",
                                {"version": result["revision"]["version"]}, actor="carol", project=pid)
    assert status == 200
    assert call(api, "POST", path(result) + "/review", {"version": submitted["revision"]["version"],
                "decision": "approved", "note": ""}, actor="carol", project=pid)[0] == 403


def test_immutable_parts_exact_retry_changed_retry_and_concurrent_winner(api):
    result = begin(api, data=b"abc")
    endpoint = path(result) + "/parts/0"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda data: call(api, "PUT", endpoint, data), (b"abc", b"xyz")))
    assert sorted(row[0] for row in responses) == [200, 409]
    winning = b"abc" if responses[0][0] == 200 else b"xyz"
    assert call(api, "PUT", endpoint, winning)[0] == 200
    assert call(api, "PUT", path(result) + "/parts/1", b"abc")[0] == 409
    assert call(api, "PUT", endpoint, b"ab")[0] == 409
    assert call(api, "POST", path(result) + "/complete", {})[0] == 202
    assert call(api, "PUT", endpoint, winning)[0] == 409


def test_complete_retry_keeps_one_job_and_trusted_actor(api):
    result = upload(api, actor="alice")
    status, second, _ = call(api, "POST", path(result) + "/complete", {"owner": "foreign", "actorId": "foreign"})
    assert status == 202
    assert second["job"]["id"] == result["job"]["id"]
    job = api.storage.get("alice", "job", result["job"]["id"])
    assert job["input"] == {"actorId": "alice", "projectId": None,
                            "documentId": result["document"]["id"], "revisionId": result["revision"]["id"]}


def test_original_attachment_and_literal_paragraphs_have_separate_hashes(api):
    data = "  Literal **markdown** 10.00%\r\n\r\n가나다 <script>literal</script>\n".encode()
    result = finalize(api, upload(api, data=data, name="terms.md"))
    assert "".join(row["text"] for row in result["paragraphs"]).encode() == data
    assert result["revision"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["revision"]["textHash"] != result["revision"]["sha256"]
    status, blob, headers = call(api, "GET", path(result) + "/blob")
    assert status == 200 and blob == data
    assert headers["Cache-Control"] == "private, no-store"
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["X-SHA256"] == hashlib.sha256(data).hexdigest()
    assert call(api, "GET", path(result) + "/blob", query={"offset": str(len(data))})[0] == 416


def test_approval_binding_is_unique_atomic_and_archive_releases_it(api):
    from documents.library import Library
    a = finalize(api, upload(api, graphRef="REG-1"))
    status, a = approve(api, a)
    assert status == 200, a
    library = Library(api, api.collaboration.resolve_scope("alice", None))
    assert library.binding("REG-1")[1]["id"] == a["revision"]["id"]
    b = finalize(api, upload(api, request="second", graphRef="REG-1"))
    status, denied = approve(api, b)
    assert status == 409, denied
    stored_b = api.storage.get("alice", "document", b["document"]["id"])
    assert stored_b["approvedRevisionId"] is None
    status, archived, _ = call(api, "POST", f"/documents/{a['document']['id']}/archive",
                              {"version": a["document"]["version"]})
    assert status == 200, archived
    assert library.binding("REG-1") is None
    current = call(api, "GET", path(b))[1]
    status, payload, _ = call(api, "POST", path(b) + "/review",
        {"version": current["revision"]["version"], "decision": "approved", "note": "Reviewed"})
    assert status == 200, payload


def test_new_revision_keeps_original_approval_and_prior_bytes(api):
    from documents.library import Library
    first = finalize(api, upload(api, graphRef="REG-1"))
    status, first = approve(api, first)
    assert status == 200
    endpoint = f"/documents/{first['document']['id']}/revisions"
    data = b"Revision two."
    body = {"requestId": "rev2", "version": first["document"]["version"], "name": "policy.txt",
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    status, second, _ = call(api, "POST", endpoint, body)
    assert status == 201, second
    assert second["revision"]["revision"] == 2
    assert call(api, "POST", endpoint, body)[1]["revision"]["id"] == second["revision"]["id"]
    assert call(api, "POST", endpoint, {**body, "requestId": "stale"})[0] == 409
    assert call(api, "PUT", path(second) + "/parts/0", data)[0] == 200
    completed = call(api, "POST", path(second) + "/complete", {})[1]
    second = finalize(api, completed)
    assert second["document"]["approvedRevisionId"] == first["revision"]["id"]
    status, second = approve(api, second)
    assert status == 200
    library = Library(api, api.collaboration.resolve_scope("alice", None))
    assert library.binding("REG-1")[1]["id"] == second["revision"]["id"]
    assert call(api, "GET", path(first))[1]["revision"]["sha256"] == first["revision"]["sha256"]
    assert call(api, "GET", path(first) + "/blob")[1] != data


def test_histories_and_source_cursors_cannot_cross_documents(api):
    first = finalize(api, upload(api, data=("x" * 110_001).encode()))
    second = finalize(api, upload(api, request="second"))
    assert len(first["paragraphs"]) == 50 and first["totalParagraphs"] == 56
    status, tail, _ = call(api, "GET", path(first), query={"cursor": first["cursor"]})
    assert status == 200 and len(tail["paragraphs"]) == 6
    assert call(api, "GET", path(second), query={"cursor": first["cursor"]})[0] == 400
    selected = call(api, "GET", path(first), query={"paragraph": "p000056"})[1]
    assert any(p["id"] == "p000056" for p in selected["paragraphs"])
    assert call(api, "GET", path(first), query={"paragraph": "p999999"})[0] == 404
    api.storage.table().page_size = 1
    events = call(api, "GET", f"/documents/{first['document']['id']}/activity")[1]
    assert all(row["documentId"] == first["document"]["id"] for row in events["events"])
    assert call(api, "GET", f"/documents/{second['document']['id']}/activity",
                query={"cursor": events["cursor"]})[0] == 400
    detail = call(api, "GET", f"/documents/{first['document']['id']}")[1]
    assert [r["id"] for r in detail["revisions"]] == [first["revision"]["id"]]


def revoke(api, pid, actor="bob"):
    owner = f"project:{pid}"
    row = api.storage.get(owner, "project", pid)
    api.storage.put(owner, "project", {**row, "members": {
        key: value for key, value in row["members"].items() if key != actor}}, row["version"])


def test_revocation_at_approval_transaction_blocks_publication(api):
    pid = project(api)
    result = finalize(api, upload(api, actor="bob", project=pid, graphRef="REG-1"), actor="bob", project=pid)
    submitted = call(api, "POST", path(result) + "/submit",
                     {"version": result["revision"]["version"]}, actor="bob", project=pid)[1]
    api.storage.table().before_transaction = lambda: revoke(api, pid)
    status, _, _ = call(api, "POST", path(result) + "/review", {
        "version": submitted["revision"]["version"], "decision": "approved", "note": ""},
        actor="bob", project=pid)
    assert status in (403, 409)
    saved = api.storage.get(f"project:{pid}", "document", result["document"]["id"])
    assert saved["approvedRevisionId"] is None


def test_library_commit_rechecks_read_role_and_fences_acl(api):
    from documents.errors import DocumentError
    from documents.library import Library
    from workspace.storage import Conflict
    pid = project(api)
    result = finalize(api, upload(api, project=pid), project=pid)
    library = Library(api, api.collaboration.resolve_scope("bob", pid))
    document = library.document(result["document"]["id"])

    def restrict():
        row = api.storage.get(f"project:{pid}", "document", document["id"])
        api.storage.put(f"project:{pid}", "document",
                        {**row, "readRoles": ["owner"], "aclVersion": row["aclVersion"] + 1}, row["version"])

    api.storage.table().before_transaction = restrict
    with pytest.raises((DocumentError, Conflict)):
        library.commit([{"owner": f"project:{pid}", "kind": "docanalysis",
                         "item": {"id": "private-result", "status": "completed"}}], documents=[document])
    assert api.storage.get(f"project:{pid}", "docanalysis", "private-result") is None
    with pytest.raises(DocumentError):
        library.document(document["id"])


def test_integrity_checks_block_changed_projection_and_cross_revision_key(api):
    from documents.errors import DocumentError
    from documents.library import Library
    result = finalize(api, upload(api))
    lib = Library(api, api.collaboration.resolve_scope("alice", None))
    doc = lib.document(result["document"]["id"])
    rev = lib.revision(doc, result["revision"]["id"])
    projection = lib.projection(doc, rev)
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == rev["textHash"]
    original = api.storage.get_blob(rev["projectionKey"])
    api.storage.put_blob(rev["projectionKey"], original.replace(b"Synthetic", b"Untrusted"), "application/json")
    with pytest.raises(DocumentError):
        lib.projection(doc, rev)
    assert call(api, "GET", path(result))[0] == 409


def test_authorize_job_rechecks_current_acl(api):
    from documents.errors import DocumentError
    from documents.library import authorize_job
    pid = project(api)
    result = upload(api, actor="bob", project=pid)
    job = api.storage.get(f"project:{pid}", "job", result["job"]["id"])
    scope = api.collaboration.resolve_scope("bob", pid)
    authorize_job(api, scope, job)
    doc = api.storage.get(f"project:{pid}", "document", result["document"]["id"])
    assert call(api, "PUT", f"/documents/{doc['id']}/permissions",
                {"version": doc["version"], "readRoles": ["owner"]}, project=pid)[0] == 200
    with pytest.raises(DocumentError):
        authorize_job(api, scope, job)


def test_identical_concurrent_revision_registration_returns_same_revision(api, monkeypatch):
    first = begin(api)
    body = {"requestId": "second", "version": first["document"]["version"], "name": "policy.txt",
            "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    barrier = threading.Barrier(2)
    original = api.storage.put_many

    def coordinated(writes, checks=None):
        if any(w["kind"] == "docrevision" and w["item"].get("revision") == 2 for w in writes):
            barrier.wait(timeout=5)
        return original(writes, checks)

    monkeypatch.setattr(api.storage, "put_many", coordinated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(api, "POST", f"/documents/{first['document']['id']}/revisions", body), range(2)))
    assert [row[0] for row in responses] == [201, 201]
    assert responses[0][1]["revision"]["id"] == responses[1][1]["revision"]["id"]


def test_identical_concurrent_complete_returns_same_job(api, monkeypatch):
    first = begin(api, data=b"abc")
    assert call(api, "PUT", path(first) + "/parts/0", b"abc")[0] == 200
    barrier = threading.Barrier(2)
    original = api.storage.put_many

    def coordinated(writes, checks=None):
        if any(w["kind"] == "job" and w.get("expected_version") is None for w in writes):
            barrier.wait(timeout=5)
        return original(writes, checks)

    monkeypatch.setattr(api.storage, "put_many", coordinated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(api, "POST", path(first) + "/complete", {}), range(2)))
    assert [row[0] for row in responses] == [202, 202]
    assert responses[0][1]["job"]["id"] == responses[1][1]["job"]["id"]


def test_document_and_revision_limits_include_concurrent_creates(api):
    for i in range(199):
        begin(api, request=f"doc-{i}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda i: call(api, "POST", "/documents", {
            "requestId": f"last-{i}", "title": "Synthetic", "kind": "policy", "name": "a.txt",
            "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(),
        }), range(2)))
    assert sorted(row[0] for row in responses) == [201, 409]
    first = begin(api, request="doc-0")
    document = first["document"]
    for i in range(2, 101):
        status, result, _ = call(api, "POST", f"/documents/{document['id']}/revisions", {
            "requestId": f"revision-{i}", "version": document["version"], "name": "a.txt",
            "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(),
        })
        assert status == 201, result
        document = result["document"]
    assert call(api, "POST", f"/documents/{document['id']}/revisions", {
        "requestId": "overflow", "version": document["version"], "name": "a.txt",
        "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(),
    })[0] == 409


def test_revocation_at_final_approval_write_is_transactional(api, monkeypatch):
    pid = project(api)
    result = finalize(api, upload(api, project=pid), project=pid)
    submitted = call(api, "POST", path(result) + "/submit",
                     {"version": result["revision"]["version"]}, project=pid)[1]
    original = api.storage.put_many
    raced = []

    def coordinated(writes, checks=None):
        if any(w["kind"] == "docrevision" and w["item"].get("status") == "approved" for w in writes):
            raced.append(True)
            api.storage.table().before_transaction = lambda: revoke(api, pid)
        return original(writes, checks)

    monkeypatch.setattr(api.storage, "put_many", coordinated)
    status, _, _ = call(api, "POST", path(result) + "/review", {
        "version": submitted["revision"]["version"], "decision": "approved", "note": ""},
        actor="bob", project=pid)
    assert raced and status == 409
    assert api.storage.get(f"project:{pid}", "document", result["document"]["id"])["approvedRevisionId"] is None


def test_download_revocation_after_blob_read_returns_no_bytes(api, monkeypatch):
    pid = project(api)
    result = finalize(api, upload(api, project=pid), project=pid)
    original = api.storage.get_blob

    def coordinated(key, **kwargs):
        data = original(key, **kwargs)
        revoke(api, pid)
        return data

    monkeypatch.setattr(api.storage, "get_blob", coordinated)
    status, payload, _ = call(api, "GET", path(result) + "/blob", actor="bob", project=pid)
    assert status == 403 and isinstance(payload, dict)
    assert "Synthetic literal" not in repr(payload)


def test_original_corruption_blocks_verified_projection(api):
    from documents.errors import DocumentError
    from documents.library import Library
    result = finalize(api, upload(api))
    library = Library(api, api.collaboration.resolve_scope("alice", None))
    document = library.document(result["document"]["id"])
    revision = library.revision(document, result["revision"]["id"])
    api.storage.put_blob(revision["originalKey"], b"Changed original", "application/octet-stream")
    with pytest.raises(DocumentError):
        library.projection(document, revision)


def test_dispatch_failure_can_retry_without_replacing_content(api):
    result = begin(api, data=b"abc")
    assert call(api, "PUT", path(result) + "/parts/0", b"abc")[0] == 200
    api.lambda_client.fail = True
    assert call(api, "POST", path(result) + "/complete", {})[0] == 503
    api.lambda_client.fail = False
    status, result, _ = call(api, "POST", path(result) + "/complete", {})
    assert status == 202 and result["revision"]["status"] == "processing"
    finished = finalize(api, result)
    assert finished["revision"]["status"] == "draft"


def test_multipart_original_download_obeys_private_blob_protocol(api):
    data = b"x" * CHUNK + b"literal tail"
    result = finalize(api, upload(api, data=data))
    first = call(api, "GET", path(result) + "/blob")
    last = call(api, "GET", path(result) + "/blob", query={"offset": str(CHUNK)})
    assert first[0] == last[0] == 200
    assert first[1] + last[1] == data
    assert int(first[2]["X-Chunk-Size"]) == CHUNK
    assert int(last[2]["X-Total-Size"]) == len(data)
    assert last[2]["X-SHA256"] == hashlib.sha256(data).hexdigest()


def test_config_and_references_expose_real_backend_and_registration_limits(api):
    status, config, _ = call(api, "GET", "/documents/config")
    assert status == 200 and config["maxFileBytes"] == 20 * 1024 * 1024
    assert config["chunkBytes"] == CHUNK
    status, result, _ = call(api, "GET", "/documents/references")
    assert status == 200 and result["backend"] == "local"
    assert {row["id"] for row in result["references"]} == {"REG-1", "DOC-1"}
    assert "demonstration" in result["note"] and "unlinked" in result["note"]


def test_raw_document_job_is_creator_only_even_for_project_owner(api):
    from documents.errors import DocumentError
    from documents.library import authorize_job
    pid = project(api)
    result = upload(api, actor="bob", project=pid)
    job = api.storage.get(f"project:{pid}", "job", result["job"]["id"])
    with pytest.raises(DocumentError) as error:
        authorize_job(api, api.collaboration.resolve_scope("alice", pid), job)
    assert error.value.status == 403


def test_server_only_sample_provenance_cannot_be_requested_from_http(api):
    from documents.api import handle
    body = {"requestId": "sample", "title": "Synthetic source", "kind": "policy", "name": "sample.txt",
            "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    scope = api.collaboration.resolve_scope("alice", None)
    response = handle(api, scope, "POST", ["documents"], {"body": json.dumps(body)}, {}, trusted_sample=True)
    assert response["statusCode"] == 201
    sample = json.loads(response["body"])
    assert sample["document"]["provenance"] == "synthetic_sample"
    assert sample["document"]["approvedRevisionId"] is None
    assert call(api, "POST", "/documents", body)[0] == 409
    regular = begin(api, request="untrusted-sample", trusted_sample=True)
    assert regular["document"]["provenance"] == "uploaded"


def test_commit_does_not_refresh_a_previously_authorized_project_snapshot(api):
    from documents.errors import DocumentError
    from documents.library import Library
    from workspace.storage import Conflict
    pid = project(api)
    result = begin(api, project=pid)
    library = Library(api, api.collaboration.resolve_scope("bob", pid))
    document = library.document(result["document"]["id"], "edit")
    # An unrelated membership edit changes the snapshot, even though Bob may
    # still edit. Restart authorization instead of silently fencing a new scope.
    revoke(api, pid, actor="dana")
    with pytest.raises((Conflict, DocumentError)):
        library.commit([library.write("docanalysis", {"id": "stale-authority"})], [document])
    assert api.storage.get(f"project:{pid}", "docanalysis", "stale-authority") is None


def test_older_pending_revision_cannot_replace_a_newer_approved_ordinal(api):
    first = finalize(api, upload(api, graphRef="REG-1"))
    assert call(api, "POST", path(first) + "/submit", {"version": first["revision"]["version"]})[0] == 200
    status, second, _ = call(api, "POST", f"/documents/{first['document']['id']}/revisions", {
        "requestId": "newer", "version": first["document"]["version"], "name": "a.txt",
        "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(),
    })
    assert status == 201, second
    assert call(api, "PUT", path(second) + "/parts/0", b"abc")[0] == 200
    second = finalize(api, call(api, "POST", path(second) + "/complete", {})[1])
    assert approve(api, second)[0] == 200
    current = call(api, "GET", path(first))[1]
    status, payload, _ = call(api, "POST", path(first) + "/review", {
        "version": current["revision"]["version"], "decision": "approved", "note": "Old pending review",
    })
    assert status == 409, payload
    assert api.storage.get("alice", "document", first["document"]["id"])["approvedRevisionId"] == second["revision"]["id"]


def test_native_workspace_routes_and_worker_publish_private_intake_without_model(api):
    from workspace.worker import Worker
    native = WorkspaceAPI(storage=api.storage, lambda_client=api.lambda_client, worker_fn="worker",
                          collaboration=api.collaboration)
    native.graph_store = api.graph_store
    pid = project(native)
    result = upload(native, actor="bob", project=pid)

    def forbidden_model(*args, **kwargs):
        pytest.fail("Document intake must not call a model")

    worker = Worker(storage=api.storage, model_call=forbidden_model, ocr=forbidden_model)
    outcome = worker.handle({"owner": f"project:{pid}", "jobId": result["job"]["id"]})
    assert outcome["status"] == "completed"
    source = call(native, "GET", path(result), actor="bob", project=pid)
    assert source[0] == 200 and source[1]["revision"]["status"] == "draft"
    job_path = f"/jobs/{result['job']['id']}"
    assert call(native, "GET", job_path, actor="bob", project=pid)[0] == 200
    assert call(native, "GET", job_path, actor="alice", project=pid)[0] == 403


def test_sample_document_replacement_requires_new_uploaded_document(api):
    from documents.api import handle
    original = b"Synthetic server sample."
    body = {"requestId": "immutable-sample", "title": "Synthetic", "kind": "policy", "name": "sample.txt",
            "size": len(original), "sha256": hashlib.sha256(original).hexdigest()}
    response = handle(api, api.collaboration.resolve_scope("alice", None), "POST", ["documents"],
                      {"body": json.dumps(body)}, {}, trusted_sample=True)
    assert response["statusCode"] == 201
    sample = json.loads(response["body"])
    assert call(api, "PUT", path(sample) + "/parts/0", original)[0] == 200
    sample = finalize(api, call(api, "POST", path(sample) + "/complete", {})[1])
    before = api.storage.get("alice", "document", sample["document"]["id"])
    version_before = api.storage.get("alice", "docrevision", sample["revision"]["id"])
    status, response, _ = call(api, "POST", f"/documents/{before['id']}/revisions", {
        "requestId": "user-replacement", "version": before["version"], "name": "replacement.txt",
        "size": 3, "sha256": hashlib.sha256(b"new").hexdigest(),
    })
    assert status == 409 and response["code"] == "sample-immutable"
    assert api.storage.get("alice", "document", before["id"]) == before
    assert api.storage.get("alice", "docrevision", sample["revision"]["id"]) == version_before
    assert call(api, "GET", path(sample) + "/blob")[1] == original
    replacement = begin(api, request="new-uploaded-document", data=b"new")
    assert replacement["document"]["provenance"] == "uploaded"
    assert replacement["document"]["id"] != before["id"]


@pytest.mark.parametrize("state", ["queued", "dispatch-failed", "completed"])
def test_complete_replay_applies_creator_policy_before_return_or_retry(api, state):
    pid = project(api)
    result = upload(api, actor="bob", project=pid)
    owner = f"project:{pid}"
    job = api.storage.get(owner, "job", result["job"]["id"])
    if state == "dispatch-failed":
        job = api.storage.put(owner, "job", {**job, "status": "failed", "errorCode": "dispatch-failed"}, job["version"])
    elif state == "completed":
        finalize(api, result, actor="bob", project=pid)
        job = api.storage.get(owner, "job", result["job"]["id"])
    invocations = len(api.lambda_client.calls)
    status, response, _ = call(api, "POST", path(result) + "/complete", {}, actor="alice", project=pid)
    assert status == 403 and "job" not in response
    assert api.storage.get(owner, "job", job["id"]) == job
    assert len(api.lambda_client.calls) == invocations
    status, own, _ = call(api, "POST", path(result) + "/complete", {}, actor="bob", project=pid)
    assert status == 202 and own["job"]["id"] == job["id"]


@pytest.mark.parametrize("actor", ["bob", "carol", "dana"])
def test_trusted_sample_creation_requires_owner_without_changing_normal_upload_rights(api, actor):
    from documents.api import handle
    pid = project(api)
    body = {"requestId": "owner-only-sample", "title": "Synthetic", "kind": "policy", "name": "sample.txt",
            "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    response = handle(api, api.collaboration.resolve_scope(actor, pid), "POST", ["documents"],
                      {"body": json.dumps(body)}, {}, trusted_sample=True)
    assert response["statusCode"] == 403
    assert api.storage.list(f"project:{pid}", "document") == []
    ordinary = begin(api, actor=actor, project=pid)
    assert ordinary["document"]["provenance"] == "uploaded"


def test_trusted_sample_owner_check_is_fenced_against_mid_create_downgrade(api, monkeypatch):
    from documents.api import handle
    pid = project(api)
    owner = f"project:{pid}"
    original = api.graph_store.get_node

    def downgrade_after_role_check(identifier):
        row = api.storage.get(owner, "project", pid)
        members = {**row["members"], "alice": {"role": "planner"}, "bob": {"role": "owner"}}
        api.storage.put(owner, "project", {**row, "members": members}, row["version"])
        return original(identifier)

    monkeypatch.setattr(api.graph_store, "get_node", downgrade_after_role_check)
    body = {"requestId": "raced-sample", "title": "Synthetic", "kind": "policy", "name": "sample.txt",
            "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(), "graphRef": "REG-1"}
    response = handle(api, api.collaboration.resolve_scope("alice", pid), "POST", ["documents"],
                      {"body": json.dumps(body)}, {}, trusted_sample=True)
    assert response["statusCode"] in (403, 409)
    assert api.storage.list(owner, "document") == []


def seed_list_document(api, project_id, identifier, roles=("owner", "planner")):
    """Valid metadata-only registration for paging/authority tests."""
    return api.storage.put("project:" + project_id, "document", {
        "id": identifier, "title": "Matching synthetic source", "kind": "policy",
        "graphRef": None, "createdBy": "alice", "projectId": project_id,
        "readRoles": list(roles), "aclVersion": 1, "status": "active",
        "latestRevisionId": identifier + "--r1", "approvedRevisionId": None,
        "provenance": "uploaded", "revisionCount": 1,
    })


def test_hidden_only_document_listing_has_no_cursor(api):
    pid = project(api)
    for i in range(61):
        seed_list_document(api, pid, f"hidden-{i:03}", ("owner",))
    status, payload, _ = call(api, "GET", "/documents", actor="bob", project=pid)
    assert status == 200 and payload == {"documents": []}


def test_document_paging_uses_only_returned_visible_ids(api):
    pid = project(api)
    for i in range(120):
        seed_list_document(api, pid, f"page-{i:03}", ("owner",) if i % 2 else ("owner", "planner"))
    api.storage.table().page_size = 7
    seen, cursor = [], None
    while True:
        status, payload, _ = call(api, "GET", "/documents", actor="bob", project=pid,
                                  query={"q": "matching", **({"cursor": cursor} if cursor else {})})
        assert status == 200
        ids = [row["id"] for row in payload["documents"]]
        if not seen:
            assert len(ids) == 50
        seen.extend(ids)
        cursor = payload.get("cursor")
        if not cursor:
            break
        decoded = json.loads(base64.urlsafe_b64decode(cursor))
        assert decoded.get("key", decoded)["sk"] == "document#" + ids[-1]
    assert seen == [f"page-{i:03}" for i in range(0, 120, 2)]


def test_document_lookahead_is_in_the_final_permission_fence(api):
    pid = project(api)
    for i in range(51):
        seed_list_document(api, pid, f"visible-{i:03}")
    def restrict_lookahead():
        row = api.storage.get("project:" + pid, "document", "visible-050")
        api.storage.put("project:" + pid, "document", {**row, "readRoles": ["owner"]}, row["version"])
    api.storage.table().before_transaction = restrict_lookahead
    status, payload, _ = call(api, "GET", "/documents", actor="bob", project=pid)
    assert status in (403, 409) and "cursor" not in payload and "documents" not in payload


def test_document_scan_bound_fails_without_exposing_hidden_continuation(api, monkeypatch):
    import documents.library as library
    pid = project(api)
    for i in range(6):
        seed_list_document(api, pid, f"secret-{i}", ("owner",))
    monkeypatch.setattr(library, "MAX_LIST_SCAN", 3, raising=False)
    status, payload, _ = call(api, "GET", "/documents", actor="bob", project=pid)
    assert status == 503 and payload["code"] == "list-scan-limit"
    assert "secret-" not in repr(payload) and "cursor" not in payload


@pytest.mark.parametrize("first_read", ["job", "source", "detail"])
def test_stale_document_intake_reconciles_job_and_revision_atomically(api, first_read):
    result = upload(api)
    job = api.storage.claim_job("alice", result["job"]["id"])
    before = api.storage.get("alice", "docrevision", result["revision"]["id"])
    api.storage.clock = lambda: job["updatedAt"] + 17 * 60_000
    endpoint = "/jobs/" + job["id"] if first_read == "job" else path(result) if first_read == "source" else "/documents/" + result["document"]["id"]
    status, _, _ = call(api, "GET", endpoint)
    assert status == 200
    saved_job = api.storage.get("alice", "job", job["id"])
    saved_revision = api.storage.get("alice", "docrevision", before["id"])
    assert saved_job["status"] == saved_revision["status"] == "failed"
    assert saved_job["errorCode"] == "job-timeout"
    assert saved_revision["parseStatus"] == "failed"
    assert any({entry["Put"]["Item"]["sk"] for entry in tx["TransactItems"] if "Put" in entry}
               == {"job#" + job["id"], "docrevision#" + before["id"]}
               for tx in api.storage.table().transactions)
    invocations = len(api.lambda_client.calls)
    status, replay, _ = call(api, "POST", path(result) + "/complete", {})
    assert status == 202 and replay["job"]["status"] == replay["revision"]["status"] == "failed"
    assert len(api.lambda_client.calls) == invocations


@pytest.mark.parametrize("missing", [False, True], ids=["legacy-timeout", "expired-job"])
def test_source_read_repairs_pending_target_after_job_expiry(api, missing):
    result = upload(api)
    job = api.storage.get("alice", "job", result["job"]["id"])
    if missing:
        key = api.storage._key("alice", "job", job["id"])
        del api.storage.table().items[(key["pk"], key["sk"])]
    else:
        api.storage.put("alice", "job", {**job, "status": "failed", "errorCode": "job-timeout",
                                        "stopReason": "timeout"}, job["version"])
    status, source, _ = call(api, "GET", path(result))
    assert status == 200 and source["revision"]["status"] == "failed"
    if missing:
        assert call(api, "GET", "/jobs/" + job["id"])[0] == 404
        assert call(api, "POST", path(result) + "/complete", {})[0] == 409
        assert api.storage.get("alice", "job", job["id"]) is None


def test_document_expiry_loses_to_a_fresh_heartbeat(api):
    result = upload(api)
    job = api.storage.claim_job("alice", result["job"]["id"])
    api.storage.clock = lambda: job["updatedAt"] + 17 * 60_000
    def heartbeat():
        current = api.storage.get("alice", "job", job["id"])
        api.storage.put("alice", "job", {**current, "progress": 75}, current["version"])
    api.storage.table().before_transaction = heartbeat
    status, _, _ = call(api, "GET", "/jobs/" + job["id"])
    assert status in (200, 409)
    assert api.storage.get("alice", "job", job["id"])["status"] == "running"
    assert api.storage.get("alice", "docrevision", result["revision"]["id"])["status"] == "processing"
    assert call(api, "GET", "/jobs/" + job["id"])[1]["job"]["status"] == "running"


def test_document_expiry_never_overwrites_a_concurrent_completion(api):
    from documents.intake import finalize as process
    result = upload(api)
    job = api.storage.claim_job("alice", result["job"]["id"])
    api.storage.clock = lambda: job["updatedAt"] + 17 * 60_000
    api.storage.table().before_transaction = lambda: process(
        SimpleNamespace(storage=api.storage, collaboration=api.collaboration), "alice", job)
    status, _, _ = call(api, "GET", "/jobs/" + job["id"])
    assert status in (200, 409)
    assert api.storage.get("alice", "job", job["id"])["status"] == "completed"
    assert api.storage.get("alice", "docrevision", result["revision"]["id"])["status"] == "draft"


def test_timeout_job_get_keeps_creator_and_source_access_checks(api):
    pid = project(api)
    result = upload(api, actor="bob", project=pid)
    owner = "project:" + pid
    job = api.storage.get(owner, "job", result["job"]["id"])
    api.storage.clock = lambda: job["updatedAt"] + 17 * 60_000
    assert call(api, "GET", "/jobs/" + job["id"], project=pid)[0] == 403
    assert api.storage.get(owner, "job", job["id"]) == job
    doc = api.storage.get(owner, "document", result["document"]["id"])
    api.storage.put(owner, "document", {**doc, "readRoles": ["owner"]}, doc["version"])
    assert call(api, "GET", "/jobs/" + job["id"], actor="bob", project=pid)[0] == 403
    assert api.storage.get(owner, "job", job["id"]) == job


def test_failed_job_repairs_target_when_worker_second_failure_write_is_interrupted(api, monkeypatch):
    from workspace.worker import Worker
    import documents.intake
    result = upload(api)
    worker = Worker(storage=api.storage)
    def operation_fails(*args, **kwargs):
        raise RuntimeError("synthetic operation failure")
    monkeypatch.setattr(documents.intake, "finalize", operation_fails)
    update = worker._update
    def second_write_fails(owner, kind, identifier, **fields):
        if kind == "docrevision":
            raise OSError("synthetic interrupted target write")
        return update(owner, kind, identifier, **fields)
    monkeypatch.setattr(worker, "_update", second_write_fails)
    outcome = worker.handle({"owner": "alice", "jobId": result["job"]["id"]})
    assert outcome["status"] == "failed" and outcome["failurePersisted"] is True
    failed = api.storage.get("alice", "job", result["job"]["id"])
    assert failed["status"] == "failed" and failed.get("errorCode") != "job-timeout"
    revision = api.storage.get("alice", "docrevision", result["revision"]["id"])
    assert revision["status"] == "failed"
    assert any({entry["Put"]["Item"]["sk"] for entry in tx["TransactItems"] if "Put" in entry}
               == {"job#" + failed["id"], "docrevision#" + revision["id"]}
               for tx in api.storage.table().transactions)
    # Repair the legacy interrupted-write state without relabelling its error.
    api.storage.put("alice", "docrevision", {**revision, "status": "processing"}, revision["version"])
    status, value, _ = call(api, "GET", path(result))
    assert status == 200 and value["revision"]["status"] == "failed"
    repaired = api.storage.get("alice", "job", failed["id"])
    assert repaired["status"] == "failed" and repaired.get("errorCode") == failed.get("errorCode")
    assert repaired.get("stopReason") == failed.get("stopReason")
    assert repaired["error"] == failed["error"]
