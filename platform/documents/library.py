"""Canonical document authority, immutable source verification and CAS fences."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import PurePosixPath

from documents.errors import DocumentError
from workspace.collaboration import Collaboration, CollaborationError
from workspace.storage import Conflict

ROLES = ("owner", "planner", "designer", "developer")
MAX_FILE_BYTES = 20 * 1024 * 1024
CHUNK_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 200
MAX_REVISIONS = 100
MAX_LIST_SCAN = 5000
MAX_PAGES = 200
MAX_TEXT_CHARS = 400_000
MAX_PARAGRAPH_CHARS = 2_000
PAGE_PARAGRAPHS = 50
# At 400,000 characters, many short literal paragraphs carry more JSON/hash
# overhead than long paragraphs. Preserve those paragraphs within S3's 50 MiB cap.
MAX_PROJECTION_BYTES = 32 * 1024 * 1024
EXTENSIONS = ("pdf", "html", "htm", "md", "markdown", "txt")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_DOCUMENT_FIELDS = frozenset({
    "id", "version", "title", "kind", "graphRef", "createdBy", "projectId", "readRoles",
    "aclVersion", "status", "latestRevisionId", "approvedRevisionId", "createdAt", "updatedAt", "provenance",
})
_REVISION_FIELDS = frozenset({
    "id", "documentId", "version", "revision", "name", "size", "sha256", "versionLabel", "effectiveDate",
    "createdBy", "status", "parseStatus", "textHash", "paragraphCount", "pages", "warnings", "createdAt",
    "updatedAt", "submittedBy", "submittedAt", "reviewedBy", "reviewedAt", "reviewNote", "review",
    "completedAt", "error", "jobId", "transcriptionOf",
})


def invalid(message="Invalid document input"):
    raise DocumentError(400, "invalid-input", message)


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        invalid("Invalid document identifier")
    return value


def text(value, name, maximum, empty=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not empty and not value.strip()):
        invalid(f"Invalid {name}")
    try:
        value.encode("utf-8")
    except UnicodeError:
        invalid(f"Invalid {name}")
    return value


def version(value):
    if type(value) is not int or value < 1:
        invalid("A positive metadata version is required")
    return value


def request_id(value):
    if not isinstance(value, str) or not _REQUEST.fullmatch(value):
        invalid("A valid requestId is required")
    return value


def read_roles(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= len(ROLES)
            or any(not isinstance(role, str) or role not in ROLES for role in value)
            or len(set(value)) != len(value) or "owner" not in value):
        invalid("readRoles must contain owner and only known, unique roles")
    return [role for role in ROLES if role in value]


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def binding_id(graph_ref):
    return "binding-" + hashlib.sha256(text(graph_ref, "graphRef", 128).encode()).hexdigest()


def file_fields(body):
    name = text(body.get("name"), "filename", 240)
    if "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
        invalid("Invalid filename")
    if PurePosixPath(name).suffix.lower().lstrip(".") not in EXTENSIONS:
        invalid("Unsupported document format")
    size = body.get("size")
    if type(size) is not int or not 1 <= size <= MAX_FILE_BYTES:
        invalid("Original size must be between 1 byte and 20 MiB")
    digest = body.get("sha256")
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        invalid("Invalid original SHA-256")
    effective = body.get("effectiveDate")
    if effective is not None:
        try:
            if not isinstance(effective, str) or date.fromisoformat(effective).isoformat() != effective:
                raise ValueError()
        except ValueError:
            invalid("Invalid effectiveDate")
    return {"name": name, "size": size, "sha256": digest,
            "versionLabel": text(body.get("versionLabel", ""), "versionLabel", 120, empty=True),
            "effectiveDate": effective}


def public_document(document):
    return {key: value for key, value in document.items() if key in _DOCUMENT_FIELDS}


def public_revision(revision):
    return {key: value for key, value in revision.items() if key in _REVISION_FIELDS}


def graph_store(host):
    store = getattr(host, "graph_store", None)
    if store is not None:
        return store() if callable(store) else store
    from graph.store import get_store
    return get_store()


def cursor_encode(identity, offset):
    return base64.urlsafe_b64encode(canonical_json({"identity": identity, "offset": offset})).decode()


def cursor_offset(value, identity):
    try:
        if not isinstance(value, str) or len(value) > 1500:
            raise ValueError()
        data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if (not isinstance(data, dict) or set(data) != {"identity", "offset"} or data["identity"] != identity
                or type(data["offset"]) is not int or data["offset"] < 0):
            raise ValueError()
        return data["offset"]
    except (ValueError, TypeError, UnicodeError):
        invalid("Invalid source cursor")


class Library:
    def __init__(self, host, scope):
        self.host, self.storage, self.scope = host, host.storage, scope
        self.collaboration = getattr(host, "collaboration", None) or Collaboration(self.storage)
        self._actions = {}
        self._scope_snapshot = None
        # Upstream records read while validating `transcriptionOf` lineage; they
        # join every commit/assert_current fence as version checks.
        self._upstream = []

    def fresh(self):
        try:
            current = self.collaboration.require(self.scope, "read")
        except CollaborationError as error:
            raise DocumentError(error.status, error.code, error.message) from None
        project = current.get("project")
        snapshot = (current["owner"], current["actor"], current["role"],
                    project["id"] if project else None, project["version"] if project else None)
        if self._scope_snapshot is not None and snapshot != self._scope_snapshot:
            raise Conflict("Project authority changed; restart authorization")
        self.scope, self._scope_snapshot = current, snapshot
        return self.scope

    @property
    def owner(self):
        return self.scope["owner"]

    @property
    def project_id(self):
        project = self.scope.get("project")
        return project["id"] if project else None

    def _authorize(self, document, action):
        if not document or document.get("projectId") != self.project_id:
            raise DocumentError(404, "not-found", "Document not found")
        if self.scope["role"] not in document.get("readRoles", ()):
            raise DocumentError(403, "forbidden", "Current document access is required")
        allowed = self.capabilities(document)
        if not allowed.get(action):
            raise DocumentError(403, "forbidden", "This document action is not allowed")
        if action != "read" and document.get("status") != "active":
            raise DocumentError(409, "archived", "Archived documents cannot be changed")
        return document

    def capabilities(self, document):
        role = self.scope["role"]
        can_read = role in document.get("readRoles", ())
        active = document.get("status") == "active"
        return {"read": can_read,
                "edit": can_read and active and (role in ("owner", "planner") or document.get("createdBy") == self.scope["actor"]),
                "review": can_read and active and role in ("owner", "planner"),
                "manage": can_read and active and role == "owner"}

    def document(self, document_id, action="read"):
        self.fresh()
        document = self.storage.get(self.owner, "document", identifier(document_id))
        self._authorize(document, action)
        # Keep all obligations: a later read must not erase an earlier edit check.
        self._actions.setdefault(document_id, set()).add(action)
        return document

    def revision(self, document, revision_id, approved=False):
        current = self.document(document["id"])
        if current["version"] != document["version"]:
            raise Conflict("Document changed")
        revision_id = identifier(revision_id)
        if not revision_id.startswith(document["id"] + "--"):
            raise DocumentError(404, "not-found", "Document revision not found")
        revision = self.storage.get(self.owner, "docrevision", revision_id)
        if not revision or revision.get("documentId") != document["id"]:
            raise DocumentError(404, "not-found", "Document revision not found")
        if approved and (current.get("status") != "active" or current.get("approvedRevisionId") != revision_id
                         or revision.get("status") != "approved" or revision.get("parseStatus") != "complete"):
            raise DocumentError(409, "source-not-approved", "The selected source is not currently approved")
        if revision.get("transcriptionOf") is not None:
            self._lineage(revision)
        return revision

    def _lineage(self, revision):
        """A transcription revision stays readable only while its whole lineage is current:
        the transcription admission, its reviewer grant, the image admission, the image
        reviewer grant, the policy and the image source. Every record read joins the fence."""
        from intake import admission
        lineage = revision["transcriptionOf"]
        observed = []
        try:
            # Both admissions are bound: the reviewer-validated transcription (whose
            # verification recursively verifies its image) and the image itself.
            bound = lineage["transcription"]
            transcribed = admission.verify(self.host, self.scope, bound["decisionId"], observe=observed)
            if (transcribed["revision"] != bound["decisionRevision"]
                    or transcribed["artifact"]["kind"] != "diagram-transcription"
                    or transcribed["derivation"]["derivativeHash"] != bound["artifactHash"]
                    or transcribed["lineage"] != {"decisionId": lineage["decisionId"],
                                                  "decisionRevision": lineage["decisionRevision"]}):
                raise admission.AdmissionError("source-changed")
            decision = admission.verify(self.host, self.scope, lineage["decisionId"], observe=observed)
            if (decision["revision"] != lineage["decisionRevision"] or decision["artifact"]["kind"] != "image"
                    or decision["artifact"]["vision"]["sha256"] != lineage["visionSha256"]
                    or decision["artifact"]["sha256"] != lineage["normalizedImageHash"]
                    or decision["source"] != lineage["sourceRef"]):
                raise admission.AdmissionError("source-changed")
        except (admission.AdmissionError, CollaborationError, DocumentError, KeyError, TypeError, ValueError):
            raise DocumentError(409, "source-upstream-revoked",
                                "The transcription's original image or its admission is no longer current") from None
        for check in observed:
            prior = next((c for c in self._upstream if (c["owner"], c["kind"], c["id"]) ==
                          (check["owner"], check["kind"], check["id"])), None)
            if prior and prior != check:
                raise Conflict("Transcription lineage changed")
            if prior is None:
                self._upstream.append(check)

    def blob_key(self, revision, suffix):
        """Do not accept merely owner-prefixed addresses for a different source."""
        return self.storage.key_for(self.owner, "docrevision", identifier(revision["id"]), suffix)

    def read_blob(self, revision, field, suffix, maximum, expected_hash):
        key = self.blob_key(revision, suffix)
        if revision.get(field) != key:
            raise DocumentError(409, "source-integrity", "Private source address does not match its revision")
        try:
            info = self.storage.blob_info(key)
            if info["size"] > maximum or info["sha256"] != expected_hash:
                raise ValueError()
            data = self.storage.get_blob(key, length=maximum)
            if len(data) != info["size"] or hashlib.sha256(data).hexdigest() != expected_hash:
                raise ValueError()
        except (ValueError, FileNotFoundError):
            raise DocumentError(409, "source-integrity", "Private source integrity could not be verified") from None
        return data

    def projection(self, document, revision):
        current = self.revision(document, revision["id"])
        if current["version"] != revision["version"]:
            raise Conflict("Revision changed")
        if not _SHA.fullmatch(str(revision.get("textHash", ""))):
            raise DocumentError(409, "source-not-ready", "Extraction is not available for this revision")
        original = self.read_blob(revision, "originalKey", "original", MAX_FILE_BYTES, revision["sha256"])
        if len(original) != revision["size"]:
            raise DocumentError(409, "source-integrity", "Original size does not match the revision")
        data = self.read_blob(revision, "projectionKey", "projection.json", MAX_PROJECTION_BYTES, revision["textHash"])
        try:
            projection = json.loads(data)
            if (not isinstance(projection, dict)
                    or set(projection) != {"schemaVersion", "paragraphs", "pages", "parseStatus", "warnings"}
                    or projection["schemaVersion"] != 1 or canonical_json(projection) != data
                    or projection["parseStatus"] != revision["parseStatus"]
                    or projection["pages"] != revision["pages"] or projection["warnings"] != revision["warnings"]
                    or not isinstance(projection["paragraphs"], list)
                    or len(projection["paragraphs"]) != revision["paragraphCount"]):
                raise ValueError()
            chars = 0
            for i, paragraph in enumerate(projection["paragraphs"], 1):
                if (not isinstance(paragraph, dict) or set(paragraph) != {"id", "text", "page", "sha256"}
                        or paragraph["id"] != f"p{i:06d}" or not isinstance(paragraph["text"], str)
                        or not 1 <= len(paragraph["text"]) <= MAX_PARAGRAPH_CHARS
                        or hashlib.sha256(paragraph["text"].encode()).hexdigest() != paragraph["sha256"]
                        or (paragraph["page"] is not None and
                            (type(paragraph["page"]) is not int or not 1 <= paragraph["page"] <= MAX_PAGES))):
                    raise ValueError()
                chars += len(paragraph["text"])
            if chars > MAX_TEXT_CHARS:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise DocumentError(409, "source-integrity", "Extraction projection is invalid") from None
        self.assert_current([document])
        return projection

    def binding(self, graph_ref):
        self.fresh()
        row = self.storage.get(self.owner, "docbinding", binding_id(graph_ref))
        if not row or row.get("status") != "active":
            return None
        document = self.document(row["documentId"])
        revision = self.revision(document, row["revisionId"], approved=True)
        if (row.get("graphRef") != graph_ref or document.get("graphRef") != graph_ref
                or row.get("sha256") != revision.get("sha256") or row.get("textHash") != revision.get("textHash")):
            raise DocumentError(409, "binding-changed", "Approved source binding does not match the revision")
        return document, revision

    def checks(self, documents=()):
        self.fresh()
        checks = []
        if self.scope.get("project"):
            project = self.scope["project"]
            checks.append({"owner": self.owner, "kind": "project", "id": project["id"], "version": project["version"]})
        seen = set()
        for document in documents:
            current = self.storage.get(self.owner, "document", identifier(document["id"]))
            for action in self._actions.get(document["id"], {"read"}):
                self._authorize(current, action)
            if current["version"] != document["version"]:
                raise Conflict("Document authority changed")
            if document["id"] not in seen:
                checks.append({"owner": self.owner, "kind": "document", "id": document["id"], "version": document["version"]})
                seen.add(document["id"])
        present = {(c["owner"], c["kind"], c["id"]) for c in checks}
        for check in self._upstream:
            if (check["owner"], check["kind"], check["id"]) not in present:
                checks.append(dict(check))
                present.add((check["owner"], check["kind"], check["id"]))
        return checks

    def commit(self, writes, documents=(), extra_checks=(), guard=None):
        checks = self.checks(documents)
        present = {(c["owner"], c["kind"], c["id"]): c for c in checks}
        for check in extra_checks:
            identity = (check["owner"], check["kind"], check["id"])
            if identity in present:
                if present[identity]["version"] != check["version"]:
                    raise Conflict("Authority changed between observations")
                continue
            present[identity] = dict(check)
            checks.append(dict(check))
        identities = {(w["owner"], w["kind"], w["item"]["id"]): w for w in writes}
        remaining = []
        for check in checks:
            written = identities.get((check["owner"], check["kind"], check["id"]))
            if written:
                if written.get("expected_version") != check["version"]:
                    raise Conflict("Authority write must match the authorized version")
            else:
                remaining.append(check)
        return self.storage.put_many(writes, checks=remaining, **self._guard(guard))

    def assert_current(self, documents=()):
        """A read has a linearization point after bytes were read, without a write."""
        checks = self.checks(documents)
        if checks:
            self.storage.put_many([], checks=checks, **self._guard())

    def _guard(self, extra=None):
        """Transcription lineage adds an upstream-expiry guard to every commit attempt;
        `extra` (a caller's final authority recheck) runs with it on every attempt."""
        guards = ([self._upstream_current] if self._upstream else []) + ([extra] if extra else [])
        if not guards:
            return {}

        def before_attempt():
            for guard in guards:
                guard()
        return {"before_attempt": before_attempt}

    def _upstream_current(self):
        """Version fences cannot see expiry: before every commit attempt (including
        retries) each upstream admission record must still be schema-valid, current
        (status and expiry at the present clock) and at its observed version."""
        from intake import records
        kinds = ("adm_decision", "adm_policy", "adm_provenance", "adm_grant")
        for check in self._upstream:
            if check["kind"] not in kinds:
                continue
            row = self.storage.get(check["owner"], check["kind"], check["id"])
            try:
                row = records.validate(check["kind"], row) if row else None
            except ValueError:
                row = None
            if not row or row.get("version") != check["version"] or not records.is_current(row, self.storage.clock()):
                raise DocumentError(409, "source-upstream-revoked",
                                    "The transcription's original image or its admission is no longer current")

    def write(self, kind, item, expected_version=None):
        return {"owner": self.owner, "kind": kind, "item": item, "expected_version": expected_version}

    def audit(self, document, action, revision=None, **fields):
        event = {"id": f"{document['id']}--{self.storage.clock():016d}-{uuid.uuid4().hex[:16]}",
                 "documentId": document["id"], "actorId": self.scope["actor"], "action": action,
                 "projectId": self.project_id, **fields}
        if revision:
            event["revisionId"] = revision["id"]
        return self.write("docaudit", event)


def prepare_transcription(host, scope, decision):
    """Trusted server adapter: the library writes that publish a reviewer-validated
    transcription as an in-review MD revision with server-owned `transcriptionOf`.

    Callable only from `intake.review`, which commits these writes in the same
    transaction as the decision's approval (`Library.commit`). `decision` is the
    sealed, about-to-be-admitted transcription decision; its image lineage is
    verified here and fenced into the returned library. The public document API
    never accepts caller-supplied provenance or lineage.
    """
    import sys
    caller = sys._getframe(1).f_globals.get("__name__")
    if caller != "intake.review":
        raise PermissionError("prepare_transcription is reserved for intake.review")
    from documents.intake import extract
    from intake import admission, inspect, records, transcription
    from workspace.ontology_sources import Sources
    library = Library(host, scope)
    library.fresh()
    try:
        decision = records.validate("adm_decision", decision)
    except ValueError:
        raise DocumentError(409, "transcription-not-admitted", "Only an admitted transcription can be published") from None
    if (decision["status"] != "admitted" or decision["artifact"]["kind"] != "diagram-transcription"
            or decision["projectId"] != library.project_id):
        raise DocumentError(409, "transcription-not-admitted", "Only an admitted transcription can be published")
    reader = Sources(inspect.context(host, library.scope))
    try:
        observed = admission._lineage_checks(host, library.scope, decision, reader, None)
        observed = [*observed, *admission._final_recheck(host, reader, observed)]
        image = admission._load(host, library.scope, decision["lineage"]["decisionId"])
    except admission.AdmissionError:
        raise DocumentError(409, "source-upstream-revoked",
                            "The transcription's original image or its admission is no longer current") from None
    for check in observed:
        if all((c["owner"], c["kind"], c["id"]) != (check["owner"], check["kind"], check["id"])
               for c in library._upstream):
            library._upstream.append(dict(check))
    value = transcription.read(host, library.scope, decision)
    data = transcription.markdown(value)
    did = "d-" + fingerprint(["intake-transcription", decision["id"]])[:40]
    rid = did + "--r000001"
    projection = extract("transcription.md", data)
    if projection["parseStatus"] != "complete":
        raise DocumentError(409, "extraction-limit", "The transcription could not be extracted")
    encoded = canonical_json(projection)
    original_key = library.storage.key_for(library.owner, "docrevision", rid, "original")
    projection_key = library.storage.key_for(library.owner, "docrevision", rid, "projection.json")
    # Immutable content-addressed blobs; an aborted commit leaves them unreferenced.
    library.storage.put_blob_once(original_key, data, "application/octet-stream")
    library.storage.put_blob_once(projection_key, encoded, "application/json")
    now = library.storage.clock()
    placed = decision["artifact"]["region"]
    lineage = {"sourceRef": dict(image["source"]), "decisionId": image["id"], "decisionRevision": image["revision"],
               "transcription": {"decisionId": decision["id"], "decisionRevision": decision["revision"],
                                 "artifactHash": decision["derivation"]["derivativeHash"]},
               "visionSha256": image["artifact"]["vision"]["sha256"],
               "normalizedImageHash": image["artifact"]["sha256"],
               "region": {k: placed[k] for k in ("left", "top", "width", "height")}}
    # An asset source is project-wide (PROJECT_AUDIENCE): its audience is every role.
    roles = list(ROLES)
    digest = fingerprint({"decision": decision["id"], "revision": decision["revision"],
                          "sha256": hashlib.sha256(data).hexdigest()})
    document = {"id": did, "title": "가이드 도식 전사", "kind": "guide-transcription", "graphRef": None,
                "readRoles": roles, "createdBy": library.scope["actor"], "projectId": library.project_id,
                "aclVersion": 1, "status": "active", "latestRevisionId": rid, "approvedRevisionId": None,
                "provenance": "intake-transcription", "revisionCount": 1, "requestHash": digest}
    revision = {"id": rid, "documentId": did, "revision": 1, "name": "transcription.md", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "versionLabel": "", "effectiveDate": None,
                "createdBy": library.scope["actor"], "status": "in_review", "parseStatus": projection["parseStatus"],
                "textHash": hashlib.sha256(encoded).hexdigest(), "paragraphCount": len(projection["paragraphs"]),
                "pages": projection["pages"], "warnings": projection["warnings"], "parts": {}, "partCount": 0,
                "requestHash": digest, "originalKey": original_key, "projectionKey": projection_key,
                "completedAt": now, "submittedBy": library.scope["actor"], "submittedAt": now,
                "transcriptionOf": lineage}
    quota = library.storage.get(library.owner, "docbinding", "collection-quota")
    count = quota["count"] if quota else 0
    if count >= MAX_DOCUMENTS:
        raise DocumentError(409, "collection-full", "A collection can contain at most 200 documents")
    return library, [
        library.write("document", document), library.write("docrevision", revision),
        library.write("docbinding", {"id": "collection-quota", "count": count + 1, "status": "internal"},
                      quota["version"] if quota else None),
        library.audit(document, "transcription-published", revision, decisionId=decision["id"]),
    ]


def authorize_job(host, scope, job):
    """Reauthorize private targets before generic workspace job serialization."""
    task = job.get("task")
    if task not in ("document-finalize", "document-analysis"):
        return
    library = Library(host, scope)
    library.fresh()
    data = job.get("input", {})
    if not isinstance(data, dict) or data.get("projectId") != library.project_id:
        raise DocumentError(403, "forbidden", "This job belongs to a different collection")
    if data.get("actorId") != library.scope["actor"]:
        raise DocumentError(403, "forbidden", "Only the job creator may read the raw job record")
    if task == "document-finalize":
        document = library.document(data.get("documentId"))
        library.revision(document, data.get("revisionId"))
        library.assert_current([document])
    else:
        # The analysis owner supplies source/result validation in its own module.
        from documents.analysis import authorize_analysis
        authorize_analysis(host, scope, data.get("analysisId"))


def visible_page(library, kind, select, limit, cursor=None):
    """A continuation exists only after finding a further authorized match."""
    visible, scanned, seen = [], 0, set()
    while True:
        page = library.storage.list_page(library.owner, kind, limit=min(50, MAX_LIST_SCAN - scanned), cursor=cursor)
        for row in page["items"]:
            scanned += 1
            item = select(row)
            if item is not None:
                visible.append(item)
                if len(visible) > limit:
                    return visible, library.storage.cursor_after(library.owner, kind, visible[limit - 1]["id"])
        cursor = page.get("cursor")
        if not cursor:
            return visible, None
        if scanned >= MAX_LIST_SCAN or cursor in seen:
            raise DocumentError(503, "list-scan-limit", "목록을 한 번에 확인하지 못했습니다. 범위를 줄이거나 다시 조회하세요.")
        seen.add(cursor)
