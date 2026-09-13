"""Server-owned sample originals are private, idempotent and never auto-approved."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_documents_library import api, call, project


def native(api):
    from documents.samples import SAMPLES
    from graph.store import Node
    from workspace.http import WorkspaceAPI
    host = WorkspaceAPI(storage=api.storage, collaboration=api.collaboration,
                        lambda_client=api.lambda_client, worker_fn="worker")
    host.graph_store = api.graph_store
    host.graph_store.upsert_nodes([Node(ref, "Regulation" if ref.startswith("REG-") else "Document",
                                       {"title": title, "code": ref}) for ref, title, *_ in SAMPLES])
    return host


def test_sample_installer_keeps_real_originals_and_drafts_without_models(api):
    from documents.intake import finalize
    host = native(api)
    status, created, _ = call(host, "POST", "/documents/samples", {"requestId": "examples"})
    assert status == 202, created
    assert len(created["documents"]) == len(created["jobs"]) == 7
    assert all(doc["provenance"] == "synthetic_sample" and not doc["approvedRevisionId"]
               for doc in created["documents"])
    assert len(host.lambda_client.calls) == 7
    for item in created["jobs"]:
        job = host.storage.claim_job("alice", item["id"])
        finalize(SimpleNamespace(storage=host.storage, collaboration=host.collaboration), "alice", job)
    for doc in created["documents"]:
        status, current, _ = call(host, "GET", "/documents/" + doc["id"])
        assert status == 200
        assert current["revisions"][0]["status"] == "draft"
        assert current["document"]["approvedRevisionId"] is None
        status, source, _ = call(host, "GET", f"/documents/{doc['id']}/revisions/{doc['latestRevisionId']}")
        assert status == 200 and any("합성" in paragraph["text"] for paragraph in source["paragraphs"])
    status, repeated, _ = call(host, "POST", "/documents/samples", {"requestId": "examples"})
    assert status == 202
    assert [d["id"] for d in repeated["documents"]] == [d["id"] for d in created["documents"]]
    assert len(host.lambda_client.calls) == 7


def test_sample_install_requires_collection_owner_and_does_not_accept_custom_content(api):
    host = native(api); team = project(host)
    assert call(host, "POST", "/documents/samples", {"requestId": "examples"},
                actor="bob", project=team)[0] == 403
    assert call(host, "POST", "/documents/samples", {"requestId": "examples", "content": "not a server sample"},
                project=team)[0] == 400
    assert host.storage.list("project:" + team, "document") == []
