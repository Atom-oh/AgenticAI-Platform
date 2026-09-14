"""Document HTTP operations; the workspace host has already verified JWT identity."""
from __future__ import annotations

import base64
import hashlib
import math
from urllib.parse import quote

from documents.errors import DocumentError
from documents.library import (
    CHUNK_BYTES, EXTENSIONS, MAX_DOCUMENTS, MAX_FILE_BYTES, MAX_REVISIONS, PAGE_PARAGRAPHS, ROLES,
    Library, authorize_job, binding_id, cursor_encode, cursor_offset, file_fields, fingerprint, graph_store,
    identifier, invalid, public_document, public_revision, read_roles, request_id, text, version,
)
from workspace.collaboration import CollaborationError
from workspace.http import HTTPError, _body, _json
from workspace.storage import Conflict


def _pair(document, revision, **extra):
    return {"document": public_document(document), "revision": public_revision(revision), **extra}


def _revision(library, document, rid, fields, ordinal, digest):
    return {"id": rid, "documentId": document["id"], "revision": ordinal, **fields,
            "createdBy": library.scope["actor"], "status": "uploading", "parseStatus": "pending",
            "textHash": None, "paragraphCount": 0, "pages": 0, "warnings": [],
            "parts": {}, "partCount": math.ceil(fields["size"] / CHUNK_BYTES), "requestHash": digest}


def _create(library, body, *, trusted_sample=False):
    library.fresh()
    if trusted_sample is True and library.scope["role"] != "owner":
        raise DocumentError(403, "forbidden", "Only an owner can install synthetic sample documents")
    request = request_id(body.get("requestId"))
    fields = file_fields(body)
    if "provenance" in body:
        invalid("Provenance is assigned by the server")
    roles = read_roles(body.get("readRoles", list(ROLES)))
    if library.scope["role"] != "owner" and roles != list(ROLES):
        raise DocumentError(403, "forbidden", "Only an owner can restrict document permissions")
    graph_ref = body.get("graphRef")
    if graph_ref is not None:
        graph_ref = identifier(graph_ref)
        node = graph_store(library.host).get_node(graph_ref)
        if not node or node.label not in ("Regulation", "Document"):
            invalid("graphRef must select an existing Regulation or Document")
    metadata = {"title": text(body.get("title"), "title", 240), "kind": text(body.get("kind"), "kind", 80),
                "graphRef": graph_ref, "readRoles": roles}
    provenance = "synthetic_sample" if trusted_sample is True else "uploaded"
    digest = fingerprint({**fields, **metadata, "provenance": provenance})
    did = "d-" + fingerprint(["document", library.scope["actor"], request])[:40]
    rid = did + "--r000001"
    existing = library.storage.get(library.owner, "document", did)
    if existing:
        document = library.document(did)
        if document.get("requestHash") != digest:
            raise DocumentError(409, "request-changed", "This requestId was used with different input")
        revision = library.revision(document, rid)
        library.assert_current([document])
        return _json(201, _pair(document, revision, chunkBytes=CHUNK_BYTES))
    document = {"id": did, **metadata, "createdBy": library.scope["actor"], "projectId": library.project_id,
                "aclVersion": 1, "status": "active", "latestRevisionId": rid, "approvedRevisionId": None,
                "provenance": provenance, "revisionCount": 1, "requestHash": digest}
    revision = _revision(library, document, rid, fields, 1, digest)
    # A private counter serializes concurrent creates at the collection limit.
    quota = library.storage.get(library.owner, "docbinding", "collection-quota")
    count = quota["count"] if quota else 0
    if count >= MAX_DOCUMENTS:
        raise DocumentError(409, "collection-full", "A collection can contain at most 200 documents")
    writes = [library.write("document", document), library.write("docrevision", revision),
              library.write("docbinding", {"id": "collection-quota", "count": count + 1, "status": "internal"},
                            quota["version"] if quota else None), library.audit(document, "created", revision)]
    saved = library.commit(writes)
    return _json(201, _pair(saved[0], saved[1], chunkBytes=CHUNK_BYTES))


def _new_revision(library, document, body):
    if document.get("provenance") == "synthetic_sample":
        raise DocumentError(409, "sample-immutable",
                            "Create a new uploaded document to replace a synthetic sample")
    request = request_id(body.get("requestId"))
    displayed_version = version(body.get("version"))
    fields = file_fields(body)
    digest = fingerprint(fields)
    rid = document["id"] + "--r-" + fingerprint([library.scope["actor"], request])[:24]
    existing = library.storage.get(library.owner, "docrevision", rid)
    if existing:
        if existing.get("requestHash") != digest:
            raise DocumentError(409, "request-changed", "This revision requestId has different input")
        library.assert_current([document])
        return _json(201, _pair(document, existing, chunkBytes=CHUNK_BYTES))
    if displayed_version != document["version"]:
        raise Conflict("Document changed")
    ordinal = document["revisionCount"] + 1
    if ordinal > MAX_REVISIONS:
        raise DocumentError(409, "revision-limit", "A document can contain at most 100 revisions")
    revision = _revision(library, document, rid, fields, ordinal, digest)
    saved = library.commit([
        library.write("document", {**document, "latestRevisionId": rid, "revisionCount": ordinal}, document["version"]),
        library.write("docrevision", revision), library.audit(document, "revision-created", revision),
    ], [document])
    return _json(201, _pair(saved[0], saved[1], chunkBytes=CHUNK_BYTES))


def _upload_part(library, document, revision, index, data):
    expected = min(CHUNK_BYTES, revision["size"] - index * CHUNK_BYTES)
    if index >= revision["partCount"] or len(data) != expected:
        raise DocumentError(409, "part-size-mismatch", "Part index or size differs from the declared original")
    digest = hashlib.sha256(data).hexdigest()
    key = library.blob_key(revision, f"parts/{index}")
    slot = str(index)
    for _ in range(8):
        document = library.document(document["id"], "edit")
        revision = library.revision(document, revision["id"])
        if revision["status"] != "uploading":
            raise DocumentError(409, "upload-closed", "This revision no longer accepts upload parts")
        part = revision.get("parts", {}).get(slot)
        if part:
            if part["sha256"] != digest or part["size"] != len(data) or part["key"] != key:
                raise DocumentError(409, "part-changed", "An immutable part cannot be replaced")
            library.assert_current([document])
            return _json(200, {"revision": public_revision(revision), "index": index, "sha256": digest})
        # Index-addressed immutable storage is the winner election for part bytes.
        library.storage.put_blob_once(key, data, "application/octet-stream")
        part = {"key": key, "size": len(data), "sha256": digest, "status": "stored"}
        try:
            saved = library.commit([
                library.write("docrevision", {**revision, "parts": {**revision["parts"], slot: part}},
                              revision["version"]),
            ], [document])[0]
            return _json(200, {"revision": public_revision(saved), "index": index, "sha256": digest})
        except Conflict:
            continue
    raise Conflict("Upload manifest is busy")


def _complete(library, document, revision):
    host = library.host
    if revision.get("jobId"):
        initial = library.storage.get(library.owner, "job", revision["jobId"])
        if initial:
            authorize_job(host, library.scope, initial)
        from documents.jobs import reconcile
        revision = reconcile(host, library.scope, "docrevision", revision, library=library, documents=[document])
        job = library.storage.get(library.owner, "job", revision["jobId"])
        if not job:
            raise DocumentError(409, "job-unavailable", "The retained revision cannot replay an expired job")
        # Raw job disclosure and dispatch retries have the same creator/source
        # authority as GET /jobs, even for another document editor or owner.
        authorize_job(host, library.scope, job)
        if job.get("errorCode") == "dispatch-failed" and not job.get("startedAt"):
            saved = library.commit([
                library.write("job", {**job, "status": "queued", "error": None, "errorCode": None}, job["version"]),
                library.write("docrevision", {**revision, "status": "processing", "error": None}, revision["version"]),
            ], [document])
            job, revision = saved
        library.assert_current([document])
        host._invoke(library.owner, job)
        return _json(202, _pair(document, revision, job=job))
    if revision["status"] != "uploading":
        raise DocumentError(409, "upload-closed", "This revision cannot be finalized")
    host._worker_ready()
    for index in range(revision["partCount"]):
        part = revision.get("parts", {}).get(str(index), {})
        key = library.blob_key(revision, f"parts/{index}")
        if part.get("status") != "stored" or part.get("key") != key:
            raise DocumentError(409, "parts-missing", "Upload every part before finalizing")
        info = library.storage.blob_info(key)
        if info["sha256"] != part["sha256"] or info["size"] != min(CHUNK_BYTES, revision["size"] - index * CHUNK_BYTES):
            raise DocumentError(409, "part-changed", "A stored part does not match the immutable manifest")
    job_id = "document-finalize-" + fingerprint(revision["id"])[:40]
    job = {"id": job_id, "task": "document-finalize", "status": "queued", "progress": 0,
           "input": {"actorId": library.scope["actor"], "projectId": library.project_id,
                     "documentId": document["id"], "revisionId": revision["id"]}}
    saved = library.commit([
        library.write("docrevision", {**revision, "status": "processing", "jobId": job_id}, revision["version"]),
        library.write("job", job), library.audit(document, "intake-queued", revision),
    ], [document])
    revision, job = saved[:2]
    host._invoke(library.owner, job)
    return _json(202, _pair(document, revision, job=job))


def _submit(library, document, revision, body):
    if version(body.get("version")) != revision["version"]:
        raise Conflict("Revision changed")
    if revision["status"] not in ("draft", "rejected") or revision.get("parseStatus") != "complete":
        raise DocumentError(409, "source-not-ready", "Only a completely extracted draft can be submitted")
    library.projection(document, revision)
    saved = library.commit([
        library.write("docrevision", {**revision, "status": "in_review", "submittedBy": library.scope["actor"],
                                      "submittedAt": library.storage.clock()}, revision["version"]),
        library.audit(document, "submitted", revision),
    ], [document])[0]
    return _json(200, _pair(document, saved))


def _review(library, document, revision, body):
    if version(body.get("version")) != revision["version"]:
        raise Conflict("Revision changed")
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        invalid("Choose approved or rejected")
    note = text(body.get("note", ""), "review note", 4000, empty=True)
    if revision["status"] != "in_review":
        raise DocumentError(409, "review-not-ready", "Submit this revision before reviewing it")
    writes = []
    updated_document = document
    reviewed = {**revision, "status": decision, "reviewedBy": library.scope["actor"],
                "reviewedAt": library.storage.clock(), "reviewNote": note}
    if decision == "approved":
        if revision.get("parseStatus") != "complete":
            raise DocumentError(409, "source-not-ready", "Incomplete extraction cannot be approved")
        if document.get("approvedRevisionId"):
            approved = library.revision(document, document["approvedRevisionId"], approved=True)
            if approved["revision"] > revision["revision"]:
                raise DocumentError(409, "older-revision", "An older revision cannot replace a newer approved revision")
        library.projection(document, revision)
        updated_document = {**document, "approvedRevisionId": revision["id"]}
        reviewed["review"] = {"decision": decision, "actorId": library.scope["actor"], "note": note,
                              "sha256": revision["sha256"], "textHash": revision["textHash"],
                              "revisionId": revision["id"], "aclVersion": document["aclVersion"],
                              "at": reviewed["reviewedAt"]}
        graph_ref = document.get("graphRef")
        if graph_ref:
            bid = binding_id(graph_ref)
            current = library.storage.get(library.owner, "docbinding", bid)
            if current and current["status"] == "active" and current["documentId"] != document["id"]:
                raise DocumentError(409, "binding-in-use", "This reference already has another approved document")
            binding = {"id": bid, "graphRef": graph_ref, "documentId": document["id"],
                       "revisionId": revision["id"], "sha256": revision["sha256"],
                       "textHash": revision["textHash"], "status": "active"}
            writes.append(library.write("docbinding", binding, current["version"] if current else None))
    saved = library.commit([
        library.write("document", updated_document, document["version"]),
        library.write("docrevision", reviewed, revision["version"]), *writes,
        library.audit(document, decision, revision, note=note, sha256=revision["sha256"], textHash=revision["textHash"]),
    ], [document])
    return _json(200, _pair(saved[0], saved[1]))


def _permissions(library, document, body):
    if version(body.get("version")) != document["version"]:
        raise Conflict("Document changed")
    roles = read_roles(body.get("readRoles"))
    saved = library.commit([
        library.write("document", {**document, "readRoles": roles, "aclVersion": document["aclVersion"] + 1},
                      document["version"]),
        library.audit(document, "permissions-changed", readRoles=roles, aclVersion=document["aclVersion"] + 1),
    ], [document])[0]
    return _json(200, {"document": public_document(saved)})


def _archive(library, document, body):
    if version(body.get("version")) != document["version"]:
        raise Conflict("Document changed")
    writes = [library.write("document", {**document, "status": "archived"}, document["version"])]
    if document.get("graphRef"):
        binding = library.storage.get(library.owner, "docbinding", binding_id(document["graphRef"]))
        if binding and binding.get("documentId") == document["id"] and binding["status"] == "active":
            writes.append(library.write("docbinding", {**binding, "status": "inactive"}, binding["version"]))
    writes.append(library.audit(document, "archived"))
    saved = library.commit(writes, [document])[0]
    return _json(200, {"document": public_document(saved)})


def _detail(library, document):
    from documents.jobs import reconcile
    revisions, cursor = [], None
    while True:
        page = library.storage.list_page(library.owner, "docrevision", cursor=cursor, prefix=document["id"] + "--")
        revisions.extend(public_revision(reconcile(library.host, library.scope, "docrevision", row,
                         library=library, documents=[document]))
                         for row in page["items"] if row.get("documentId") == document["id"])
        cursor = page.get("cursor")
        if not cursor or len(revisions) >= MAX_REVISIONS:
            break
    library.assert_current([document])
    return _json(200, {"document": public_document(document),
                       "revisions": sorted(revisions, key=lambda row: row["revision"]),
                       "capabilities": library.capabilities(document)})


def _source(library, document, revision, query):
    from documents.jobs import reconcile
    revision = reconcile(library.host, library.scope, "docrevision", revision, library=library, documents=[document])
    if revision.get("textHash"):
        projection = library.projection(document, revision)
        paragraphs = projection["paragraphs"]
    else:
        paragraphs = []
    identity = fingerprint([library.owner, document["id"], revision["id"], revision.get("textHash")])
    offset = 0
    if query.get("paragraph"):
        if query.get("cursor"):
            invalid("Select a paragraph or a cursor, not both")
        found = next((i for i, p in enumerate(paragraphs) if p["id"] == query["paragraph"]), None)
        if found is None:
            raise DocumentError(404, "paragraph-not-found", "The selected paragraph is not in this revision")
        offset = max(0, found - PAGE_PARAGRAPHS // 2)
    elif query.get("cursor"):
        offset = cursor_offset(query["cursor"], identity)
        if offset >= len(paragraphs):
            invalid("Source cursor is outside the extraction")
    payload = _pair(document, revision, paragraphs=paragraphs[offset:offset + PAGE_PARAGRAPHS],
                    totalParagraphs=len(paragraphs))
    if offset + PAGE_PARAGRAPHS < len(paragraphs):
        payload["cursor"] = cursor_encode(identity, offset + PAGE_PARAGRAPHS)
    library.assert_current([document])
    return _json(200, payload)


def _download(library, document, revision, query):
    if query.get("kind", "original") != "original":
        invalid("Only original downloads are supported")
    raw_offset = query.get("offset", "0")
    if not isinstance(raw_offset, str) or not raw_offset.isascii() or not raw_offset.isdigit() or len(raw_offset) > 10:
        invalid("Invalid source offset")
    offset = int(raw_offset)
    if not revision.get("originalKey"):
        raise DocumentError(409, "source-not-ready", "Original storage is not complete")
    if offset >= revision["size"]:
        raise DocumentError(416, "invalid-range", "Offset is outside the original")
    # Verify the whole bounded original, including actual bytes, before serving a slice.
    data = library.read_blob(revision, "originalKey", "original", MAX_FILE_BYTES, revision["sha256"])
    if len(data) != revision["size"]:
        raise DocumentError(409, "source-integrity", "Original size does not match the revision")
    library.assert_current([document])
    chunk = data[offset:offset + CHUNK_BYTES]
    mime = "application/octet-stream"
    return {"statusCode": 200, "isBase64Encoded": True, "body": base64.b64encode(chunk).decode(),
            "headers": {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                        "Content-Type": mime, "X-Content-Type": mime, "X-Total-Size": str(len(data)),
                        "X-Chunk-Size": str(len(chunk)), "X-SHA256": revision["sha256"],
                        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(revision['name'], safe='')}",
                        "Content-Security-Policy": "default-src 'none'; sandbox"}}


def _list(library, query):
    from documents.library import visible_page
    term = text(query.get("q", ""), "search", 240, empty=True).casefold()
    def select(row):
        try:
            document = library.document(row["id"])
        except DocumentError as error:
            if error.status in (403, 404):
                return None
            raise
        if not term or term in (document["title"] + " " + (document.get("graphRef") or "")).casefold():
            return document
        return None
    documents, cursor = visible_page(library, "document", select, 50, query.get("cursor"))
    library.assert_current(documents)
    return _json(200, {"documents": [public_document(d) for d in documents[:50]],
                       **({"cursor": cursor} if cursor else {})})


def _references(library):
    store = graph_store(library.host)
    rows = []
    for label in ("Regulation", "Document"):
        for node in store.find_by_label(label):
            rows.append({"id": node.id, "label": label, "title": node.props.get("title") or node.props.get("name") or node.id,
                         **{k: node.props[k] for k in ("version", "effectiveDate") if k in node.props}})
    library.assert_current()
    return _json(200, {"references": rows, "backend": store.name,
                       "note": "Shared demonstration ontology metadata; original sources are unlinked until privately registered and approved."})


def _handle(library, method, parts, event, query, *, trusted_sample=False):
    library.fresh()
    if parts == ["documents", "config"] and method == "GET":
        return _json(200, {"roles": list(ROLES), "maxFileBytes": MAX_FILE_BYTES, "chunkBytes": CHUNK_BYTES,
                           "extensions": list(EXTENSIONS), "role": library.scope["role"],
                           "actorId": library.scope["actor"], "projectId": library.project_id})
    if parts == ["documents", "references"] and method == "GET":
        return _references(library)
    if parts == ["documents"]:
        if method == "GET":
            return _list(library, query)
        if method == "POST":
            # Idempotent creates losing a quota/id race are safely retried.
            body = _body(event)
            for attempt in range(4):
                try:
                    return _create(library, body, trusted_sample=trusted_sample)
                except Conflict:
                    if attempt == 3:
                        raise
    if len(parts) < 2:
        raise DocumentError(404, "not-found", "Document route not found")
    action = "read" if method == "GET" else "manage" if parts[-1] == "permissions" else "review" if parts[-1] == "review" else "edit"
    document = library.document(parts[1], action)
    if len(parts) == 2 and method == "GET":
        return _detail(library, document)
    if len(parts) == 3:
        if parts[2] == "revisions" and method == "POST":
            body = _body(event)
            try:
                return _new_revision(library, document, body)
            except Conflict:
                # A concurrent identical registration may already have won.
                # A different request still fails its displayed version check.
                return _new_revision(library, library.document(document["id"], "edit"), body)
        if parts[2] == "permissions" and method == "PUT":
            return _permissions(library, document, _body(event))
        if parts[2] == "archive" and method == "POST":
            return _archive(library, document, _body(event))
        if parts[2] == "activity" and method == "GET":
            page = library.storage.list_page(library.owner, "docaudit", limit=50, cursor=query.get("cursor"),
                                             prefix=document["id"] + "--")
            library.assert_current([document])
            return _json(200, {"events": page["items"], **({"cursor": page["cursor"]} if page.get("cursor") else {})})
    if len(parts) >= 4 and parts[2] == "revisions":
        revision = library.revision(document, parts[3])
        if len(parts) == 4 and method == "GET":
            return _source(library, document, revision, query)
        if len(parts) == 6 and parts[4] == "parts" and method == "PUT":
            if not parts[5].isascii() or not parts[5].isdigit() or len(parts[5]) > 3:
                invalid("Invalid part index")
            return _upload_part(library, document, revision, int(parts[5]), _body(event, binary=True))
        if len(parts) == 5:
            if parts[4] == "complete" and method == "POST":
                _body(event)
                try:
                    return _complete(library, document, revision)
                except Conflict:
                    document = library.document(document["id"], "edit")
                    revision = library.revision(document, revision["id"])
                    if not revision.get("jobId"):
                        raise
                    return _complete(library, document, revision)
            if parts[4] == "blob" and method == "GET":
                return _download(library, document, revision, query)
            if parts[4] == "submit" and method == "POST":
                return _submit(library, document, revision, _body(event))
            if parts[4] == "review" and method == "POST":
                return _review(library, document, revision, _body(event))
    raise DocumentError(404, "not-found", "Document route not found")


def handle(host, scope, method, parts, event, query, *, trusted_sample=False):
    try:
        return _handle(Library(host, scope), method, parts, event, query, trusted_sample=trusted_sample)
    except (DocumentError, HTTPError, CollaborationError) as error:
        return _json(error.status, {"error": error.message, "code": error.code})
    except Conflict:
        return _json(409, {"error": "The document or authority changed; reload and retry.", "code": "conflict"})
    except FileNotFoundError:
        return _json(404, {"error": "The private source is unavailable.", "code": "not-found"})
    except ValueError:
        return _json(400, {"error": "Invalid document request.", "code": "invalid-input"})
