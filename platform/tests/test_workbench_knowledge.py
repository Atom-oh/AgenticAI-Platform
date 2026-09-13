import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb, call, source, documents, queue, run, indexed
from workspace.collaboration import CollaborationError


def test_snapshot_writes_numeric_vectors_and_typed_graph_then_queries_them(wb):
    src, payload = indexed(wb)
    result = call(wb, "GET", "knowledge", query={"q": "withdrawal confirmation"})
    assert result["items"][0]["title"] == "Synthetic withdrawal guidance"
    assert result["items"][0]["score"] > 0
    assert result["backend"]["embedding"] == "local-hashing-256-v1"
    manifest = wb.storage.get(wb.owner, "wb_index", "current")
    projection = manifest["sources"][src["id"]]
    vectors = json.loads(wb.storage.get_blob(projection["vectorKey"]))
    assert len(vectors["rows"]) == 2
    assert len(vectors["rows"][0]["vector"]) == 256
    assert any(float(x) != 0 for x in vectors["rows"][0]["vector"])
    detail = call(wb, "GET", "knowledge/" + result["items"][0]["id"])
    assert detail["document"]["content"] == "withdrawal limit confirmation notice"
    assert detail["evidence"]["revision"] == "r1"
    assert call(wb, "GET", f"batches/{payload['batch']['id']}")["batch"]["status"] == "completed"
    assert len(call(wb, "GET", "dependencies")["edges"]) == 3


def test_pending_replacement_and_tombstone_hide_old_vectors_and_graph(wb):
    src, _ = indexed(wb)
    old = call(wb, "GET", "knowledge")["items"][0]["id"]
    replacement = queue(wb, src, docs=[documents()[1]], request="replacement")
    assert call(wb, "GET", "knowledge")["items"] == []
    assert call(wb, "GET", "dependencies")["nodes"] == []
    with pytest.raises(CollaborationError):
        call(wb, "GET", "knowledge/" + old)
    run(wb, replacement)
    assert [x["title"] for x in call(wb, "GET", "knowledge")["items"]] == ["Synthetic pension investment"]
    current = wb.storage.get(wb.owner, "wb_source", src["id"])
    assert current["access"]["guide"]["tombstone"]


def test_document_acl_and_freshness_are_checked_on_every_read(wb):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner"]
    run(wb, queue(wb, src, docs))
    assert len(call(wb, "GET", "knowledge", actor="bob")["items"]) == 1
    assert call(wb, "GET", "dependencies", actor="bob")["nodes"] == []
    wb.now += 2 * 24 * 3600 * 1000
    wb.storage.clock = lambda: wb.now
    assert call(wb, "GET", "knowledge")["items"] == []
    assert call(wb, "GET", "knowledge")["coverage"]["complete"] is False


def test_failed_graph_write_keeps_previous_manifest_and_suppresses_stale_source(wb, monkeypatch):
    src, _ = indexed(wb)
    before = wb.storage.get(wb.owner, "wb_index", "current")
    payload = queue(wb, src, request="new")
    original = wb.storage.put_blob_once
    def fail_graph(key, data, mime):
        if key.endswith("/graph.json"):
            raise RuntimeError("private raw SDK content must not escape")
        return original(key, data, mime)
    monkeypatch.setattr(wb.storage, "put_blob_once", fail_graph)
    with pytest.raises(CollaborationError) as err:
        run(wb, payload)
    assert "private raw" not in str(err.value)
    assert wb.storage.get(wb.owner, "wb_index", "current") == before
    assert call(wb, "GET", "knowledge")["items"] == []


def test_worker_rechecks_revoked_actor_and_pinned_source_version(wb):
    src = source(wb)
    payload = queue(wb, src)
    queue(wb, src, request="newer")
    with pytest.raises(CollaborationError) as err:
        run(wb, payload)
    assert err.value.status == 409
    assert wb.storage.get(wb.owner, "wb_index", "current") is None
    latest = queue(wb, src, request="latest")
    wb.now += 3601 * 1000
    wb.storage.clock = lambda: wb.now
    with pytest.raises(CollaborationError) as err:
        run(wb, latest)
    assert err.value.status == 401


def test_snapshot_input_bounds_and_cursor_binding(wb):
    src = source(wb)
    with pytest.raises(CollaborationError):
        queue(wb, src, [documents()[0], documents()[0]])
    with pytest.raises(CollaborationError):
        queue(wb, src, [{**documents()[0], "content": "x" * 100_001}])
    run(wb, queue(wb, src))
    page = call(wb, "GET", "knowledge", query={"limit": "1"})
    assert len(page["items"]) == 1 and page["cursor"]
    second = call(wb, "GET", "knowledge", query={"limit": "1", "cursor": page["cursor"]})
    assert second["items"][0]["id"] != page["items"][0]["id"]
    with pytest.raises(CollaborationError):
        call(wb, "GET", "knowledge", query={"q": "different", "cursor": page["cursor"]})


def test_source_change_during_projection_write_cancels_publication(wb, monkeypatch):
    src = source(wb)
    payload = queue(wb, src)
    original = wb.storage.put_blob_once
    def change_source(key, data, mime):
        result = original(key, data, mime)
        if key.endswith("/graph.json"):
            row = wb.storage.get(wb.owner, "wb_source", src["id"])
            wb.storage.put(wb.owner, "wb_source", {**row, "permissionVersion": row["permissionVersion"] + 1}, row["version"])
        return result
    monkeypatch.setattr(wb.storage, "put_blob_once", change_source)
    with pytest.raises(CollaborationError):
        run(wb, payload)
    assert wb.storage.get(wb.owner, "wb_index", "current") is None


def test_search_rechecks_source_after_loading_vectors(wb, monkeypatch):
    src, _ = indexed(wb)
    original = wb.storage.get_blob
    def revoke_after_read(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key.endswith("/vectors.json"):
            row = wb.storage.get(wb.owner, "wb_source", src["id"])
            wb.storage.put(wb.owner, "wb_source", {**row, "permissionVersion": row["permissionVersion"] + 1}, row["version"])
        return data
    monkeypatch.setattr(wb.storage, "get_blob", revoke_after_read)
    with pytest.raises(CollaborationError):
        call(wb, "GET", "knowledge")


def test_declared_graph_has_immutable_manifest_and_cannot_upgrade_provenance(wb):
    indexed(wb)
    hit = call(wb, "GET", "knowledge")["items"][0]
    ref = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    body = {"requestId": "mapping", "sourceRef": ref,
            "nodes": [{"id": "declared", "label": "Test", "title": "Declared", "version": "1",
                       "provenance": "connector-extracted"}], "edges": []}
    result = call(wb, "POST", "dependencies", body)
    manifest = wb.storage.get(wb.owner, "wb_index", "current")
    stored = json.loads(wb.storage.get_blob(manifest["manifestKey"]))
    assert stored["generation"] == result["generation"]
    assert stored["declared"]
    node = next(x for x in call(wb, "GET", "dependencies")["nodes"] if x["id"] == "declared")
    assert node["provenance"] == "declared"


def test_corrupt_vector_artifact_is_rejected(wb):
    src, _ = indexed(wb)
    manifest = wb.storage.get(wb.owner, "wb_index", "current")
    key = manifest["sources"][src["id"]]["vectorKey"]
    wb.storage.put_blob(key, b'{"rows":[]}', "application/json")
    with pytest.raises(CollaborationError) as err:
        call(wb, "GET", "knowledge")
    assert err.value.code == "artifact-invalid"


@pytest.mark.parametrize("endpoint,suffix", [("knowledge", "/vectors.json"), ("dependencies", "/graph.json")])
def test_role_downgrade_during_read_cannot_disclose_previous_role_content(wb, monkeypatch, endpoint, suffix):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner"]
    run(wb, queue(wb, src, docs))
    original = wb.storage.get_blob
    def downgrade(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key.endswith(suffix):
            project = wb.storage.get(wb.owner, "project", wb.project["id"])
            project["members"]["alice"]["role"] = "planner"
            wb.storage.put(wb.owner, "project", project, project["version"])
        return data
    monkeypatch.setattr(wb.storage, "get_blob", downgrade)
    with pytest.raises(CollaborationError):
        call(wb, "GET", endpoint)


def test_graph_response_is_bounded_across_multiple_sources_with_explicit_incomplete_coverage(wb):
    for prefix in ("first", "second"):
        src = source(wb, request=prefix)
        doc = {"id": prefix, "title": "Bounded graph", "content": "", "revision": "r1",
               "entities": [{"id": f"{prefix}-{i}", "label": "Component", "title": f"Node {i}", "version": "1"}
                            for i in range(300)]}
        run(wb, queue(wb, src, [doc], request=prefix))
    graph = call(wb, "GET", "dependencies")
    assert len(graph["nodes"]) <= 500
    assert graph["coverage"]["truncated"] is True and graph["coverage"]["complete"] is False
