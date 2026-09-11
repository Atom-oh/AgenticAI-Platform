"""Canonical project membership and immutable, privately persisted guidelines.

The caller supplies a verified actor, never a storage owner from request data.
Membership indexes are discovery hints only. All project writes include a CAS on
the canonical project so concurrent revocation also cancels the operation.
Ontology projections use Node/Edge records persisted in S3; no in-memory graph
or unconfigured Neptune service is represented as durable storage.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
import uuid
from dataclasses import asdict

from graph.store import Edge, Node
from workspace.storage import Conflict

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_ROLES = frozenset({"owner", "planner", "designer", "developer"})
_ALL = _ROLES
_ACTIONS = {
    "read": _ALL, "discuss": _ALL, "upload": _ALL,
    "members": {"owner"}, "people": {"owner"},
    "edit_product": {"owner", "planner"}, "publish": {"owner", "planner"},
    "edit_rules": {"owner", "planner", "designer"},
    "generate": {"owner", "designer"}, "approve": {"owner", "designer"},
    "release": {"owner", "designer", "developer"},
    "export": {"owner", "developer"},
}
_FIELDS = ("title", "description", "conditions", "steps", "notices")
_ANCHORS = frozenset({"productId", "guidelineId", "runId", "round", "pageId"})
MAX_MEMBERS = 40  # Old+new membership indexes and project fit one transaction.
MAX_DRAFT_BYTES = 60_000
MAX_PROJECTION_BYTES = 1024 * 1024


class CollaborationError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _invalid(message):
    raise CollaborationError(400, "invalid-input", message)


def _text(value, name, maximum, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
            or (not empty and not value.strip())):
        _invalid(f"Invalid {name}")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid(f"Invalid {name}")
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _invalid("Invalid resource identifier")
    return value


def _actor(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or value.startswith("project:") or any(ord(c) < 32 for c in value)):
        raise CollaborationError(401, "unauthorized", "An authenticated user is required")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise CollaborationError(401, "unauthorized", "An authenticated user is required")
    return value


def _version(value):
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        _invalid("A positive integer version is required")
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _request(value):
    if not isinstance(value, str) or not _REQUEST.fullmatch(value):
        _invalid("A bounded requestId is required")
    return value


def _public(value):
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()
                if key not in ("pk", "sk", "owner", "requestHash") and not key.endswith("Key")}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _draft(body, previous=None):
    data = {field: copy.deepcopy(body.get(field, (previous or {}).get(field, [] if field in _FIELDS[2:] else "")))
            for field in _FIELDS}
    _text(data["title"], "title", 180)
    _text(data["description"], "description", 12_000, empty=True)
    for field in _FIELDS[2:]:
        values = data[field]
        if not isinstance(values, list) or len(values) > 50:
            _invalid(f"{field} must contain at most 50 entries")
        seen, clean = set(), []
        for value in values:
            if not isinstance(value, dict):
                _invalid(f"Invalid {field} entry")
            identifier = _id(value.get("id"))
            if identifier in seen:
                _invalid(f"Duplicate {field} identifier")
            seen.add(identifier)
            row = {"id": identifier}
            if field == "conditions":
                row["text"] = _text(value.get("text"), "condition text", 4000)
            else:
                row["title"] = _text(value.get("title"), f"{field} title", 180)
                key = "description" if field == "steps" else "content"
                row[key] = _text(value.get(key, ""), key, 4000, empty=field == "steps")
                if field == "notices":
                    if type(value.get("required")) is not bool:
                        _invalid("Notice required must be a boolean")
                    row["required"] = value["required"]
            clean.append(row)
        data[field] = clean
    if len(_json(data)) > MAX_DRAFT_BYTES:
        _invalid("The product draft exceeds the publication size limit")
    return data


def _guide_text(draft):
    sections = [draft["title"], draft["description"]]
    for heading, field in (("조건", "conditions"), ("절차", "steps"), ("안내", "notices")):
        lines = []
        for entry in draft[field]:
            text = entry["text"] if field == "conditions" else (
                entry["title"] + "\n" + entry.get("description", entry.get("content", "")))
            lines.append(f"[{entry['id']}] {text}")
            if field == "notices":
                lines.append("필수" if entry["required"] else "선택")
        if lines:
            sections.append(heading + "\n" + "\n\n".join(lines))
    return "\n\n".join(sections)


def _projection(project_id, product_id, guideline_id, revision, draft):
    nodes, edges = [], []

    def node(label, identifier, source_text, **props):
        node_id = f"{guideline_id}:{label}:{identifier}"
        source = {"projectId": project_id, "productId": product_id, "guidelineId": guideline_id,
                  "revision": revision, "text": source_text}
        nodes.append(asdict(Node(node_id, label, {**props, "source": source})))
        return node_id

    def edge(src, rel, dst):
        edges.append(asdict(Edge(src, rel, dst, {"guidelineId": guideline_id, "revision": revision})))

    root = node("Product", product_id, draft["description"] or draft["title"],
                title=draft["title"], description=draft["description"])
    for condition in draft["conditions"]:
        target = node("Condition", condition["id"], condition["text"], **condition)
        edge(root, "HAS_CONDITION", target)
    previous = None
    for order, step in enumerate(draft["steps"], 1):
        target = node("Procedure", step["id"], step["title"] + "\n" + step["description"],
                      **step, order=order)
        edge(root, "HAS_PROCEDURE", target)
        if previous:
            edge(previous, "NEXT", target)
        previous = target
    page_ids = set()
    for notice in draft["notices"]:
        page_id = f"notice-{notice['id']}"
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", page_id):
            page_id = "notice-" + hashlib.sha256(notice["id"].encode("utf-8")).hexdigest()[:24]
        if page_id in page_ids:
            _invalid("Notice page identifiers collide; use distinct notice IDs")
        page_ids.add(page_id)
        source = notice["title"] + "\n" + notice["content"]
        policy = node("PolicyRule", notice["id"], source, **notice)
        screen = node("ScreenMeta", notice["id"], source, pageId=page_id,
                      title=notice["title"], content=notice["content"], purpose="notice", proposed=True)
        edge(root, "HAS_POLICY_RULE", policy)
        edge(root, "HAS_SCREEN", screen)
        edge(policy, "CONSTRAINS", screen)
    projection = {"schemaVersion": 1, "projectId": project_id, "productId": product_id,
                  "guidelineId": guideline_id, "revision": revision, "nodes": nodes, "edges": edges}
    projection["hash"] = _hash(projection)
    if len(_json(projection)) > MAX_PROJECTION_BYTES:
        _invalid("The ontology projection exceeds the publication size limit")
    return projection


class Collaboration:
    def __init__(self, storage, directory=None):
        self.storage, self.directory = storage, directory

    def resolve_scope(self, actor, project_id):
        actor = _actor(actor)
        if project_id is None:
            return {"owner": actor, "actor": actor, "project": None, "role": "owner"}
        identifier = _id(project_id)
        owner = f"project:{identifier}"
        project = self.storage.get(owner, "project", identifier)
        members = project.get("members", {}) if project else {}
        member = members.get(actor) if isinstance(members, dict) else None
        if (not project or project.get("status") != "active" or not isinstance(member, dict)
                or member.get("role") not in _ROLES):
            raise CollaborationError(403, "forbidden", "Current project membership is required")
        return {"owner": owner, "actor": actor, "project": project, "role": member["role"]}

    def require(self, scope, action):
        """Re-resolve canonical authority; return the fresh scope for CAS writes."""
        if not isinstance(scope, dict):
            raise CollaborationError(403, "forbidden", "Invalid workspace scope")
        project = scope.get("project")
        if project is not None and not isinstance(project, dict):
            raise CollaborationError(403, "forbidden", "Invalid project scope")
        fresh = self.resolve_scope(scope.get("actor"), project.get("id") if project else None)
        if fresh["owner"] != scope.get("owner") or fresh["role"] not in _ACTIONS.get(action, ()):
            raise CollaborationError(403, "forbidden", "This action is not allowed for the current role")
        return fresh

    def _get(self, scope, kind, identifier):
        record = self.storage.get(scope["owner"], kind, _id(identifier))
        if not record or record.get("projectId") != scope["project"]["id"]:
            raise CollaborationError(404, "not-found", "Project resource not found")
        return record

    @staticmethod
    def _write(owner, kind, item, version=None):
        return {"owner": owner, "kind": kind, "item": item, "expected_version": version}

    def _commit(self, scope, writes):
        # A write fence, not a membership index check: revocation that wins this
        # project CAS cancels the entire operation, including publication.
        project = scope["project"]
        fence = self._write(scope["owner"], "project", project, project["version"])
        try:
            return self.storage.put_many([*writes, fence])[:-1]
        except Conflict as error:
            raise CollaborationError(409, "conflict", "The project or resource changed; reload and retry") from error

    def _people(self, query):
        query = _text(query, "people query", 200)
        if self.directory is None:
            raise CollaborationError(503, "directory-unavailable", "User lookup is not configured")
        found, seen = [], set()
        for record in itertools.islice(self.directory(query), 100):
            if not isinstance(record, dict):
                continue
            sub = _actor(record.get("sub"))
            if sub in seen:
                continue
            seen.add(sub)
            found.append({"sub": sub, "displayName": _text(record.get("displayName", sub), "display name", 180)})
            if len(found) == 20:
                break
        return found

    def handle(self, method, parts, body, query, actor, project_id):
        if not parts or parts[0] not in ("projects", "products", "comments"):
            return None
        _actor(actor)
        if not isinstance(body, dict) or not isinstance(query, dict):
            _invalid("Request body and query must be objects")
        try:
            status, result = self._handle(method.upper(), parts, body, query, actor, project_id)
            return status, _public(result)
        except Conflict as error:
            raise CollaborationError(409, "conflict", "The resource changed; reload and retry") from error
        except ValueError as error:
            raise CollaborationError(400, "invalid-input", "Invalid resource data") from error

    def _handle(self, method, parts, body, query, actor, project_id):
        if parts[0] == "projects":
            return self._projects(method, parts, body, query, actor)
        scope = self.require(self.resolve_scope(actor, project_id), "read")
        if scope["project"] is None:
            raise CollaborationError(400, "project-required", "Select a project first")
        if parts[0] == "products":
            return self._products(method, parts, body, query, scope)
        if parts == ["comments"]:
            if method == "POST":
                return self._comment(scope, body)
            if method == "GET":
                anchor = self._anchor(scope, {key: value for key, value in query.items() if key in _ANCHORS}) if any(
                    key in query for key in _ANCHORS) else {}
                page = self.storage.list_page(scope["owner"], "comment", cursor=query.get("cursor"))
                comments = [row for row in page["items"]
                            if row.get("projectId") == scope["project"]["id"]
                            and all(row.get("anchor", {}).get(key) == value for key, value in anchor.items())]
                return 200, {"comments": comments, **self._cursor(page)}
        raise CollaborationError(404, "not-found", "Route not found")

    @staticmethod
    def _cursor(page):
        return {"cursor": page["cursor"]} if page.get("cursor") else {}

    def _projects(self, method, parts, body, query, actor):
        if parts == ["projects"]:
            if method == "POST":
                return self._create_project(actor, body)
            if method == "GET":
                page = self.storage.list_page(actor, "membership", cursor=query.get("cursor"))
                projects = []
                for member in page["items"]:
                    if member.get("status") != "active":
                        continue
                    try:
                        scope = self.resolve_scope(actor, member["projectId"])
                    except CollaborationError as error:
                        if error.status == 403:
                            continue
                        raise
                    projects.append(scope["project"])
                return 200, {"projects": projects, **self._cursor(page)}
        if len(parts) >= 2:
            scope = self.resolve_scope(actor, parts[1])
            if len(parts) == 2 and method == "GET":
                return 200, {"project": scope["project"]}
            if len(parts) == 3 and parts[2] == "members" and method == "PUT":
                return self._members(self.require(scope, "members"), body)
            if len(parts) == 3 and parts[2] == "people" and method == "GET":
                self.require(scope, "people")
                return 200, {"people": self._people(query.get("q"))}
        raise CollaborationError(404, "not-found", "Route not found")

    def _create_project(self, actor, body):
        name = _text(body.get("name"), "project name", 180)
        identifier = "p-" + _hash([actor, _request(body.get("requestId"))])[:48]
        owner, fingerprint = f"project:{identifier}", _hash({"name": name})
        existing = self.storage.get(owner, "project", identifier)
        if existing:
            project = self.resolve_scope(actor, identifier)["project"]
            if project.get("requestHash") != fingerprint:
                raise CollaborationError(409, "request-changed", "This requestId has different input")
            return 200, {"project": project}
        display_name = actor
        if self.directory is not None:
            try:
                display_name = next((person["displayName"] for person in self._people(actor)
                                     if person["sub"] == actor), actor)
            except Exception:
                # The directory supplies presentation data here, not authority.
                # An unavailable owner profile must not block project creation.
                pass
        member = {"role": "owner", "displayName": display_name}
        project = {"id": identifier, "name": name, "members": {actor: member}, "status": "active",
                   "createdBy": actor, "updatedBy": actor, "requestHash": fingerprint}
        writes = [self._write(owner, "project", project),
                  self._write(actor, "membership", {"id": identifier, "projectId": identifier,
                                                   **member, "status": "active"})]
        try:
            saved = self.storage.put_many(writes)[0]
        except Conflict:
            saved = self.resolve_scope(actor, identifier)["project"]
            if saved.get("requestHash") != fingerprint:
                raise CollaborationError(409, "request-changed", "This requestId has different input")
            return 200, {"project": saved}
        return 201, {"project": saved}

    def _members(self, scope, body):
        project, actor = scope["project"], scope["actor"]
        version = _version(body.get("version"))
        if version != project["version"]:
            raise Conflict("Project changed")
        incoming = body.get("members")
        if not isinstance(incoming, dict) or not 1 <= len(incoming) <= MAX_MEMBERS:
            _invalid(f"A project requires between 1 and {MAX_MEMBERS} members")
        members = {}
        for sub, value in incoming.items():
            _actor(sub)
            if not isinstance(value, dict) or value.get("role") not in _ROLES:
                _invalid("Invalid member role")
            previous = project["members"].get(sub)
            if previous is None:
                matches = [person for person in self._people(sub) if person["sub"] == sub]
                if not matches:
                    _invalid("The invited user does not exist in the configured directory")
                display = matches[0]["displayName"]
            else:
                display = previous["displayName"]
            members[sub] = {"role": value["role"], "displayName": display}
        if not any(member["role"] == "owner" for member in members.values()):
            _invalid("A project must retain at least one owner")
        writes = [self._write(scope["owner"], "project", {
            **project, "members": members, "updatedBy": actor}, version)]
        for sub in sorted(set(project["members"]) | set(members)):
            previous = self.storage.get(sub, "membership", project["id"])
            member = members.get(sub, project["members"].get(sub))
            item = {"id": project["id"], "projectId": project["id"], **member,
                    "status": "active" if sub in members else "revoked"}
            writes.append(self._write(sub, "membership", item, previous["version"] if previous else None))
        return 200, {"project": self.storage.put_many(writes)[0]}

    def _products(self, method, parts, body, query, scope):
        if parts == ["products"]:
            if method == "GET":
                page = self.storage.list_page(scope["owner"], "product", cursor=query.get("cursor"))
                return 200, {"products": page["items"], **self._cursor(page)}
            if method == "POST":
                scope = self.require(scope, "edit_product")
                draft = _draft(body)
                identifier = ("product-" + _hash([scope["actor"], _request(body["requestId"])])[:48]
                              if "requestId" in body else uuid.uuid4().hex)
                fingerprint = _hash(draft)
                previous = self.storage.get(scope["owner"], "product", identifier)
                if previous:
                    if previous.get("requestHash") != fingerprint:
                        raise CollaborationError(409, "request-changed", "This requestId has different input")
                    return 200, {"product": previous}
                product = {**draft, "id": identifier, "projectId": scope["project"]["id"], "status": "draft",
                           "createdBy": scope["actor"], "updatedBy": scope["actor"],
                           "publishedGuidelineId": None, "publishedRevision": 0, "requestHash": fingerprint}
                result = self._commit(scope, [self._write(scope["owner"], "product", product)])[0]
                return 201, {"product": result}
        if len(parts) < 2:
            raise CollaborationError(404, "not-found", "Route not found")
        product = self._get(scope, "product", parts[1])
        if len(parts) == 2:
            if method == "GET":
                return 200, {"product": product}
            if method == "PUT":
                scope = self.require(scope, "edit_product")
                version = _version(body.get("version"))
                if version != product["version"]:
                    raise Conflict("Product changed")
                changed = {**product, **_draft(body, product), "status": "draft", "updatedBy": scope["actor"]}
                result = self._commit(scope, [self._write(scope["owner"], "product", changed, version)])[0]
                return 200, {"product": result}
        if len(parts) == 3:
            if parts[2] == "publish" and method == "POST":
                return self._publish(self.require(scope, "publish"), product, _version(body.get("version")))
            if parts[2] == "ontology" and method == "GET":
                context = self._context(scope, product, query.get("revision") or product.get("publishedGuidelineId"))
                return 200, {"ontology": context["ontology"]}
            if parts[2] == "impact" and method == "GET":
                page = self.storage.list_page(scope["owner"], "run", cursor=query.get("cursor"))
                current = product.get("publishedGuidelineId")
                affected = []
                for run in page["items"]:
                    if run.get("productId") != product["id"]:
                        continue
                    if (run.get("projectId") != scope["project"]["id"] or not current
                            or run.get("guidelineId") != current
                            or run.get("ontologyHash") != product.get("ontologyHash")):
                        affected.append({key: run[key] for key in (
                            "id", "status", "projectId", "productId", "guidelineId", "approval") if key in run}
                                        | {"needsRevalidation": True})
                return 200, {"currentGuidelineId": current, "affectedRuns": affected, **self._cursor(page)}
        raise CollaborationError(404, "not-found", "Route not found")

    def _publish(self, scope, product, version):
        if version != product["version"]:
            if product.get("publishedFromVersion") == version and product["version"] == version + 1:
                return 200, self.published_context(scope, product["id"])
            raise Conflict("Product changed")
        if product.get("status") == "published":
            return 200, self.published_context(scope, product["id"])
        draft = _draft(product)
        owner, project_id = scope["owner"], scope["project"]["id"]
        revision = product["publishedRevision"] + 1
        guideline_id = "g-" + _hash([project_id, product["id"], version, draft])[:48]
        asset_id = "guide-" + guideline_id[2:]
        text = _guide_text(draft)
        raw = text.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        ontology = _projection(project_id, product["id"], guideline_id, revision, draft)
        original_key = self.storage.key_for(owner, "asset", asset_id, "original.txt")
        analysis_key = self.storage.key_for(owner, "asset", asset_id, "analysis.json")
        graph_key = self.storage.key_for(owner, "ontology", guideline_id, "projection.json")
        graph_bytes = _json(ontology)
        analysis = {"format": "txt", "text": text, "parseStatus": "complete", "pages": 1,
                    "warnings": [], "resources": [], "previews": []}
        self.storage.put_blob_once(original_key, raw, "application/octet-stream")
        self.storage.put_blob_once(analysis_key, _json(analysis), "application/json")
        self.storage.put_blob_once(graph_key, graph_bytes, "application/json")
        guideline = {"id": guideline_id, "projectId": project_id, "productId": product["id"],
                     "revision": revision, "title": draft["title"], "text": text, "sha256": digest,
                     "draftHash": _hash(draft), "assetId": asset_id, "ontologyHash": ontology["hash"],
                     "sourceVersion": version, "publishedBy": scope["actor"], "status": "published"}
        asset = {"id": asset_id, "projectId": project_id, "productId": product["id"], "guidelineId": guideline_id,
                 "name": f"guideline-{revision}.txt", "purpose": "guide", "system": True,
                 "size": len(raw), "sha256": digest, "originalKey": original_key, "analysisKey": analysis_key,
                 "status": "stored", "uploadStatus": "stored", "parseStatus": "complete",
                 "importRevision": revision, "lineageId": product.get("guideLineageId") or asset_id,
                 "parentId": product.get("guideAssetId"), "previews": [], "warnings": [], "archived": False,
                 "createdBy": scope["actor"]}
        metadata = {"id": guideline_id, "projectId": project_id, "productId": product["id"],
                    "guidelineId": guideline_id, "revision": revision, "hash": ontology["hash"],
                    "sha256": hashlib.sha256(graph_bytes).hexdigest(), "graphKey": graph_key, "status": "published"}
        updated = {**product, "status": "published", "publishedGuidelineId": guideline_id,
                   "publishedRevision": revision, "publishedFromVersion": version, "ontologyHash": ontology["hash"],
                   "guideAssetId": asset_id, "guideLineageId": asset["lineageId"], "updatedBy": scope["actor"]}
        records = self._commit(scope, [self._write(owner, "product", updated, version),
                                       self._write(owner, "guideline", guideline),
                                       self._write(owner, "ontology", metadata),
                                       self._write(owner, "asset", asset)])
        return 200, {"product": records[0], "guideline": records[1], "ontology": ontology, "assetId": asset_id}

    def _read(self, owner, key, limit, expected_sha):
        if not key or not self.storage.owns_key(owner, key):
            raise CollaborationError(409, "guideline-unavailable", "Private guideline evidence is unavailable")
        info = self.storage.blob_info(key)
        if info["size"] > limit:
            raise CollaborationError(409, "guideline-unavailable", "Guideline evidence exceeds the read limit")
        data = self.storage.get_blob(key)
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise CollaborationError(409, "guideline-changed", "Stored guideline evidence no longer matches")
        return data

    def _context(self, scope, product, guideline_id):
        if not guideline_id:
            raise CollaborationError(409, "guideline-required", "Publish a product guideline first")
        try:
            guideline = self._get(scope, "guideline", guideline_id)
            metadata = self._get(scope, "ontology", guideline_id)
            asset = self._get(scope, "asset", guideline["assetId"])
            if (any(record.get("productId") != product["id"] for record in (guideline, metadata, asset))
                    or metadata.get("guidelineId") != guideline["id"]
                    or metadata.get("revision") != guideline["revision"]
                    or asset.get("guidelineId") != guideline["id"] or asset.get("system") is not True
                    or asset.get("uploadStatus") != "stored" or asset.get("sha256") != guideline["sha256"]):
                raise CollaborationError(409, "guideline-changed", "Guideline identity is inconsistent")
            raw = self._read(scope["owner"], asset["originalKey"], MAX_PROJECTION_BYTES, guideline["sha256"])
            if raw.decode("utf-8") != guideline["text"] or len(raw) != asset["size"]:
                raise CollaborationError(409, "guideline-changed", "Guideline source does not match")
            ontology = json.loads(self._read(scope["owner"], metadata["graphKey"],
                                             MAX_PROJECTION_BYTES, metadata["sha256"]))
            if (not isinstance(ontology, dict) or ontology.get("schemaVersion") != 1
                    or ontology.get("projectId") != scope["project"]["id"]
                    or ontology.get("productId") != product["id"] or ontology.get("guidelineId") != guideline_id
                    or ontology.get("revision") != guideline["revision"]
                    or ontology.get("hash") != guideline["ontologyHash"]
                    or ontology.get("hash") != metadata["hash"]
                    or _hash({key: value for key, value in ontology.items() if key != "hash"}) != metadata["hash"]):
                raise CollaborationError(409, "guideline-changed", "Ontology identity is inconsistent")
        except (KeyError, ValueError, FileNotFoundError, TypeError) as error:
            raise CollaborationError(409, "guideline-unavailable", "Saved guideline evidence is unavailable") from error
        return {"product": product, "guideline": guideline, "ontology": ontology, "assetId": asset["id"]}

    def published_context(self, scope, product_id):
        scope = self.require(scope, "read")
        if scope["project"] is None:
            raise CollaborationError(400, "project-required", "Select a project first")
        product = self._get(scope, "product", product_id)
        return self._context(scope, product, product.get("publishedGuidelineId"))

    def is_current(self, scope, run):
        scope = self.require(scope, "read")
        if not isinstance(run, dict):
            return False
        snapshot = run.get("contract", {})
        if not isinstance(snapshot, dict) or any(
                key in snapshot and snapshot[key] != run.get(key)
                for key in ("projectId", "productId", "guidelineId", "ontologyHash")):
            return False
        project_id = scope["project"]["id"] if scope["project"] else None
        if run.get("projectId") != project_id:
            return False
        if not run.get("productId"):
            return not run.get("guidelineId") and not run.get("ontologyHash")
        if not project_id or not run.get("guidelineId") or not run.get("ontologyHash"):
            return False
        try:
            product = self._get(scope, "product", run["productId"])
            if (product.get("publishedGuidelineId") != run["guidelineId"]
                    or product.get("ontologyHash") != run["ontologyHash"]):
                return False
            context = self._context(scope, product, run["guidelineId"])
            return context["ontology"]["hash"] == run["ontologyHash"]
        except CollaborationError as error:
            if error.status in (401, 403):
                raise
            return False

    def _anchor(self, scope, data):
        if not isinstance(data, dict) or not data or set(data) - _ANCHORS:
            _invalid("A valid discussion anchor is required")
        anchor = {}
        for key, value in data.items():
            if key == "round":
                if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}", value):
                    value = int(value)
                anchor[key] = _version(value)
            else:
                anchor[key] = _id(value)
        if not any(key in anchor for key in ("productId", "guidelineId", "runId")):
            _invalid("A discussion must reference a stored product, guideline, or run")
        run = self._get(scope, "run", anchor["runId"]) if "runId" in anchor else None
        if run:
            for key in ("productId", "guidelineId"):
                value = run.get(key)
                if key in anchor and anchor[key] != value:
                    _invalid("Discussion resources belong to different revisions")
                if value:
                    anchor[key] = value
        if "guidelineId" in anchor:
            guideline = self._get(scope, "guideline", anchor["guidelineId"])
            if "productId" in anchor and anchor["productId"] != guideline["productId"]:
                _invalid("The guideline belongs to a different product")
            anchor["productId"] = guideline["productId"]
        if "productId" in anchor:
            self._get(scope, "product", anchor["productId"])
        if "round" in anchor or "pageId" in anchor:
            if not run:
                _invalid("Round and page comments require a stored run")
            rounds = run.get("rounds", [])
            if "round" in anchor:
                rounds = [row for row in rounds if row.get("number") == anchor["round"]]
                if len(rounds) != 1:
                    _invalid("The discussion round is unavailable")
            if "pageId" in anchor:
                pages = [page.get("pageId") for row in rounds for page in row.get("pageSources", [])]
                if "round" not in anchor:
                    pages.extend(page.get("pageId") for page in run.get("pageSources", []))
                if anchor["pageId"] not in pages:
                    _invalid("The discussion page is unavailable")
        return anchor

    def _comment(self, scope, body):
        scope = self.require(scope, "discuss")
        text = _text(body.get("text"), "comment text", 4000)
        anchor = self._anchor(scope, body.get("anchor"))
        identifier = "c-" + _hash([scope["actor"], _request(body.get("requestId"))])[:48]
        fingerprint = _hash({"text": text, "anchor": anchor})
        previous = self.storage.get(scope["owner"], "comment", identifier)
        if previous:
            if previous.get("requestHash") != fingerprint or previous.get("author") != scope["actor"]:
                raise CollaborationError(409, "request-changed", "This requestId has different input")
            return 200, {"comment": previous}
        now = self.storage.clock()
        comment = {"id": identifier, "projectId": scope["project"]["id"], "text": text, "anchor": anchor,
                   "author": scope["actor"], "status": "active", "requestHash": fingerprint,
                   "history": [{"author": scope["actor"], "text": text, "at": now}]}
        try:
            result = self._commit(scope, [self._write(scope["owner"], "comment", comment)])[0]
        except CollaborationError as error:
            if error.status != 409:
                raise
            # Recheck membership even for a concurrent idempotent retry.
            self.require(scope, "discuss")
            previous = self.storage.get(scope["owner"], "comment", identifier)
            if not previous or previous.get("requestHash") != fingerprint or previous.get("author") != scope["actor"]:
                raise
            return 200, {"comment": previous}
        return 201, {"comment": result}
