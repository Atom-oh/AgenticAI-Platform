"""Shared fixtures for private-intake tests: real library, Storage/CAS and scopes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_documents_library import api, approve, call, finalize, project, upload  # noqa: E402,F401


def scope(api, actor, pid):
    return api.collaboration.resolve_scope(actor, pid)


def document_ref(api, pid, result):
    owner = f"project:{pid}"
    document = api.storage.get(owner, "document", result["document"]["id"])
    revision = api.storage.get(owner, "docrevision", result["revision"]["id"])
    return {"sourceKind": "document-revision", "sourceId": document["id"], "revision": revision["id"],
            "sha256": revision["sha256"], "audienceRevision": str(document["aclVersion"])}


def approved(api, pid, data, *, name="policy.txt", request="first", **fields):
    result = upload(api, data=data, project=pid, name=name, request=request, **fields)
    finalized = finalize(api, result, project=pid)
    status, payload = approve(api, finalized, project=pid)
    assert status == 200, payload
    return document_ref(api, pid, result)


def draft(api, pid, data, *, name="draft.txt", request="draft", **fields):
    result = upload(api, data=data, project=pid, name=name, request=request, **fields)
    finalize(api, result, project=pid)
    return document_ref(api, pid, result)
