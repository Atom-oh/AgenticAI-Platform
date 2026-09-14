"""Server-owned sample originals are private, idempotent and never auto-approved."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_documents_library import api, call, project


def test_catalog_explains_every_template_without_installing_documents(api):
    host = native(api)
    status, catalog, _ = call(host, "GET", "/documents/samples")
    assert status == 200, catalog
    assert catalog["schemaVersion"] == 1
    assert [(row["graphRef"], row["kind"]) for row in catalog["samples"]] == [
        ("REG-LN-001", "regulation"), ("DOC-000", "policy"), ("DOC-001", "report"),
        ("DOC-002", "specification"), ("DOC-003", "report"), ("DOC-004", "notice"), ("DOC-005", "guide"),
    ]
    report = catalog["samples"][2]
    assert report["title"] == "[합성 예제] 전세대출 상품 심의 보고"
    assert report["name"] == "DOC-001-sample.md"
    assert [section["title"] for section in report["sections"]] == ["검토 항목", "확인 원칙"]
    assert "상품 심의에서는 담보 기준과 상품 설명이 일치하는지" in report["sections"][0]["text"]
    assert "심의 완료로 처리하지 않습니다" in report["sections"][1]["text"]
    assert "실제 은행 내규" in catalog["notice"]
    assert host.storage.list("alice", "document") == []
    assert host.storage.list("alice", "job") == []
    assert host.lambda_client.calls == []


def test_catalog_is_scoped_but_does_not_require_sample_install_permission(api):
    host = native(api)
    team = project(host)
    assert call(host, "GET", "/documents/samples", actor="bob", project=team)[0] == 200
    assert call(host, "GET", "/documents/samples", actor="outsider", project=team)[0] == 403
    assert call(host, "POST", "/documents/samples", {"requestId": "examples"},
                actor="bob", project=team)[0] == 403
    assert host.storage.list("project:" + team, "document") == []


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
    catalog_status, catalog, _ = call(host, "GET", "/documents/samples")
    assert catalog_status == 200
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
        template = next(row for row in catalog["samples"] if row["graphRef"] == doc["graphRef"])
        revision = current["revisions"][0]
        assert revision["sha256"] == template["sha256"]
        assert revision["name"] == template["name"]
        status, source, _ = call(host, "GET", f"/documents/{doc['id']}/revisions/{doc['latestRevisionId']}")
        assert status == 200 and any("합성" in paragraph["text"] for paragraph in source["paragraphs"])
        text = "".join(paragraph["text"] for paragraph in source["paragraphs"])
        assert all(section["text"] in text for section in template["sections"])
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
