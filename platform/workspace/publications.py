"""Shared-publication authority and the `published-asset` source adapter.

AGENTCORE_CONTRACT "Shared-publication authority": the source owner proposes an
exact-revision publication of approved origin nodes; a designated publisher who
holds the IAM-administered `design_publish`/`policy_publish` capability *and*
current origin source authority approves it; a destination project owner accepts
a grant naming the publication revision and permitted roles. Effective access is
the intersection of current destination membership, the active grant and the
current upstream origin source audience. Withdrawal is prospective: new
resolution is denied immediately and delivered derivatives are recorded as
recall-needed metadata, never claimed as recalled.

Publications live in one deployment partition keyed by an opaque ID; every read
checks origin membership or an active destination grant first, so a record the
caller cannot use is indistinguishable from a missing one.
"""
from __future__ import annotations

import re

from intake.records import INTAKE_OWNER
from workbench.service import Service, fail, fields, public
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.ontology_sources import PROJECT_AUDIENCE

PUBLICATION_OWNER = "publication:deployment"
CAPABILITIES = {"design": "design_publish", "policy": "policy_publish"}
# The project roles that own each source class (publishing-handoff/1: designers
# own UX approval, planners own published product/rule guidance).
SOURCE_OWNERS = {"design": frozenset({"owner", "designer"}), "policy": frozenset({"owner", "planner"})}
NODE_TYPES = {"design": frozenset((*schema.LEVELS, "Component", "Asset")),
              "policy": frozenset({"PolicyRule", "Product"})}
ROLES = ("owner", "planner", "designer", "developer")
MAX_NODES = 50
MAX_HISTORY = 20
MAX_GRANTS = 200
MAX_CAPABILITIES = 1000
# Upstream source kinds whose origin audience can be rechecked without the
# destination actor holding origin membership. Everything else is unpublishable.
PUBLISHABLE_SOURCES = frozenset({"asset", "document-revision", "product-guideline", "package"})
_REVISION = re.compile(r"[1-9][0-9]{0,8}\Z")


def _not_found():
    fail(404, "not-found", "공유 게시물을 찾을 수 없거나 읽을 권한이 없습니다.")


def grant_id(publication_id, revision):
    return "pg-" + schema.digest([publication_id, revision])[:40]


def published_asset_reference(publication, grant):
    return {"sourceKind": "published-asset", "sourceId": publication["id"],
            "revision": str(publication["revision"]), "sha256": publication["hash"],
            "audienceRevision": str(grant["revision"])}


def _write(owner, kind, item, version=None):
    return {"owner": owner, "kind": kind, "item": item, "expected_version": version}


def _publication(storage, identifier):
    try:
        schema._identifier(identifier)
    except ValueError:
        _not_found()
    return storage.get(PUBLICATION_OWNER, "publication", identifier)


def _origin_publication(ctx, identifier):
    """A publication of the caller's current project; foreign or missing are identical."""
    ctx.fresh()
    record = _publication(ctx.storage, identifier)
    if not record or record.get("originProject") != ctx.project_id:
        _not_found()
    return record


def view(record):
    """Public metadata: no private keys, destination list or recall routing."""
    value = {key: item for key, item in record.items() if key not in ("grants", "recallNeeded", "history")}
    value["recallNeeded"] = bool(record.get("recallNeeded"))
    return public(value)


def _hash(record):
    return schema.digest({key: record[key] for key in ("id", "originProject", "kind", "revision", "nodes")})


def _bindings(node_ids, revision_bindings):
    if (not isinstance(node_ids, list) or not 1 <= len(node_ids) <= MAX_NODES
            or len(set(node_ids)) != len(node_ids) or not isinstance(revision_bindings, dict)
            or set(revision_bindings) != set(node_ids)):
        fail(400, "invalid-input", "게시할 노드와 리비전 기준을 정확히 지정하세요.")
    for identifier in node_ids:
        try:
            schema._identifier(identifier)
        except ValueError:
            fail(400, "invalid-input", "게시할 노드 ID가 올바르지 않습니다.")
        binding = revision_bindings[identifier]
        if (not isinstance(binding, dict) or set(binding) != {"revision", "contentHash"}
                or type(binding["revision"]) is not int or binding["revision"] < 1
                or not isinstance(binding["contentHash"], str) or not re.fullmatch(r"[a-f0-9]{64}", binding["contentHash"])):
            fail(400, "invalid-input", "노드 리비전 기준 형식이 올바르지 않습니다.")
    return sorted(node_ids)


def _origin_nodes(ctx, kind, node_ids, revision_bindings, *, denied):
    """Read exact approved origin nodes through current source authority."""
    from workspace.ontology_store import Ontology
    ontology = Ontology(ctx)
    visible = {node["id"]: node for node in ontology.read(node_ids)["nodes"]}
    if set(visible) != set(node_ids):
        denied()
    nodes = []
    for identifier in node_ids:
        node, binding = visible[identifier], revision_bindings[identifier]
        if (node["revision"] != binding["revision"] or node["contentHash"] != binding["contentHash"]
                or node["reviewState"] != "approved" or node["tombstone"]):
            fail(409, "publication-binding-stale", "게시할 노드의 승인 리비전이 현재와 다릅니다.")
        if node["type"] not in NODE_TYPES[kind] or node["scope"] != {"kind": "project", "projectId": ctx.project_id}:
            fail(422, "publication-node-kind", "이 게시 유형에 포함할 수 없는 노드입니다.")
        for ref in node["sourceRefs"]:
            if ref["sourceKind"] not in PUBLISHABLE_SOURCES or "allowedRoles" in ref:
                fail(409, "publication-restricted-source", "역할 제한 또는 공유 불가 원본은 조직에 게시할 수 없습니다.")
        nodes.append(node)
    return nodes, ontology.sources.recheck()


def propose(ctx, *, kind, node_ids, revision_bindings):
    """Source owner proposes an exact-revision publication; a changed binding is a new revision."""
    if kind not in CAPABILITIES:
        fail(400, "invalid-input", "게시 유형은 design 또는 policy입니다.")
    ctx.fresh()
    if ctx.scope["role"] not in SOURCE_OWNERS[kind]:
        fail(403, "publication-forbidden", "원본 소유 역할만 게시를 제안할 수 있습니다.")
    node_ids = _bindings(node_ids, revision_bindings)

    def denied():
        _not_found()
    nodes, checks = _origin_nodes(ctx, kind, node_ids, revision_bindings, denied=denied)
    summary = [{"id": n["id"], "revision": n["revision"], "contentHash": n["contentHash"],
                "type": n["type"], "sourceRefs": n["sourceRefs"]} for n in nodes]
    identifier = "pub-" + schema.digest([ctx.project_id, kind, node_ids])[:40]
    previous = ctx.storage.get(PUBLICATION_OWNER, "publication", identifier)
    if previous:
        if previous["status"] == "withdrawn":
            fail(409, "publication-withdrawn", "철회된 게시물은 다시 제안할 수 없습니다. 새 게시로 제안하세요.")
        if previous["nodes"] == summary:
            return view(previous)
    revision = previous["revision"] + 1 if previous else 1
    history = list((previous or {}).get("history", []))
    record = {"id": identifier, "projectId": ctx.project_id, "originProject": ctx.project_id, "kind": kind,
              "revision": revision, "nodes": summary, "status": "proposed", "proposedBy": ctx.actor,
              "grants": list((previous or {}).get("grants", [])), "history": history[-MAX_HISTORY:]}
    record["hash"] = _hash(record)
    saved = ctx.commit([_write(PUBLICATION_OWNER, "publication", record, previous["version"] if previous else None)],
                       checks=checks)[0]
    return view(saved)


def _capabilities(storage):
    from intake import records
    rows, cursor = [], None
    while True:
        page = storage.list_page(INTAKE_OWNER, "capability", limit=100, cursor=cursor)
        for row in page["items"]:
            try:
                rows.append(records.validate("capability", row))
            except ValueError:
                continue
        cursor = page.get("cursor")
        if not cursor:
            return rows
        if len(rows) >= MAX_CAPABILITIES:
            fail(503, "publication-capability-limit", "게시 권한 기록을 확인하지 못했습니다.")


def current_capability(storage, actor, name):
    """A current IAM-administered capability of this verified actor, or None."""
    from intake import records
    now = storage.clock()
    for row in _capabilities(storage):
        if row["actor"] == actor and row["name"] == name and records.is_current(row, now):
            return row
    return None


def approve(ctx, publication_id):
    """Capability holder with current origin source authority publishes the exact proposed revision."""
    record = _origin_publication(ctx, publication_id)
    kind = record["kind"]
    if record["status"] == "withdrawn":
        fail(409, "publication-withdrawn", "철회된 게시물입니다.")
    if record["status"] == "published":
        return view(record)
    capability = current_capability(ctx.storage, ctx.actor, CAPABILITIES[kind])
    if capability is None:
        fail(403, "publication-capability-required", "조직 게시 권한이 필요합니다.")
    if ctx.scope["role"] not in SOURCE_OWNERS[kind]:
        fail(403, "publication-authority-required", "게시할 원본의 소유 권한이 필요합니다.")

    def denied():
        fail(403, "publication-authority-required", "게시할 원본을 현재 권한으로 읽을 수 없습니다.")
    node_ids = [n["id"] for n in record["nodes"]]
    bindings = {n["id"]: {"revision": n["revision"], "contentHash": n["contentHash"]} for n in record["nodes"]}
    try:
        nodes, checks = _origin_nodes(ctx, kind, node_ids, bindings, denied=denied)
    except CollaborationError as error:
        if error.status in (401,) or error.code in ("publication-binding-stale", "publication-authority-required"):
            raise
        fail(403, "publication-authority-required", "게시할 원본을 현재 권한으로 확인하지 못했습니다.")
    snapshot = schema.canonical({"publicationId": record["id"], "revision": record["revision"],
                                 "hash": record["hash"], "nodes": nodes})
    key = ctx.storage.key_for(PUBLICATION_OWNER, "publication", record["id"], f"revisions/{record['revision']}.json")
    info = ctx.storage.put_blob_once(key, snapshot, "application/json")
    entry = {"revision": record["revision"], "hash": record["hash"], "snapshotKey": key,
             "snapshotSha256": info["sha256"]}
    updated = {**{k: v for k, v in record.items() if k not in ("version", "createdAt", "updatedAt")},
               "status": "published", "approvedBy": ctx.actor, "approvedAt": ctx.storage.clock(),
               "capability": {"id": capability["id"], "revision": capability["revision"]},
               "snapshotKey": key, "snapshotSha256": info["sha256"],
               "history": [*record.get("history", []), entry][-MAX_HISTORY:]}
    checks = [*checks, {"owner": INTAKE_OWNER, "kind": "capability", "id": capability["id"],
                        "version": capability["version"]}]
    saved = ctx.commit([_write(PUBLICATION_OWNER, "publication", updated, record["version"])], checks=checks)[0]
    return view(saved)


def grant(ctx, publication_id, *, roles):
    """The destination project owner accepts an exact publication revision for named roles."""
    ctx.fresh()
    if ctx.scope["role"] != "owner":
        fail(403, "forbidden", "대상 프로젝트 소유자만 게시물 사용을 수락할 수 있습니다.")
    if (not isinstance(roles, list) or not roles or len(set(roles)) != len(roles)
            or any(role not in ROLES for role in roles)):
        fail(400, "invalid-input", "허용할 프로젝트 역할을 지정하세요.")
    record = _publication(ctx.storage, publication_id)
    if not record or record.get("status") != "published" or record.get("originProject") == ctx.project_id:
        _not_found()
    identifier = grant_id(record["id"], record["revision"])
    previous = ctx.storage.get(ctx.owner, "pub_grant", identifier)
    roles = sorted(roles)
    if previous and previous["status"] == "active" and previous["roles"] == roles:
        return public(previous)
    item = {"id": identifier, "projectId": ctx.project_id, "destinationProject": ctx.project_id,
            "publicationId": record["id"], "publicationRevision": record["revision"],
            "publicationHash": record["hash"], "originProject": record["originProject"], "roles": roles,
            "status": "active", "revision": previous["revision"] + 1 if previous else 1, "acceptedBy": ctx.actor}
    destinations = [row for row in record.get("grants", []) if row.get("grantId") != identifier
                    or row.get("destinationProject") != ctx.project_id]
    destinations.append({"destinationProject": ctx.project_id, "grantId": identifier,
                         "publicationRevision": record["revision"]})
    if len(destinations) > MAX_GRANTS:
        fail(422, "publication-grant-limit", "게시물 수락 한도를 초과했습니다.")
    publication = {**{k: v for k, v in record.items() if k not in ("version", "createdAt", "updatedAt")},
                   "grants": destinations}
    saved = ctx.commit([ctx.write("pub_grant", item, previous["version"] if previous else None),
                        _write(PUBLICATION_OWNER, "publication", publication, record["version"])])[0]
    return public(saved)


def withdraw(ctx, publication_id):
    """Publisher or origin owner withdraws; every grant is ineffective from the next access."""
    record = _origin_publication(ctx, publication_id)
    if ctx.scope["role"] != "owner" and ctx.actor != record.get("approvedBy"):
        fail(403, "forbidden", "게시자 또는 원본 프로젝트 소유자만 철회할 수 있습니다.")
    if record["status"] == "withdrawn":
        return view(record)
    recall = [{"destinationProject": row["destinationProject"], "grantId": row["grantId"],
               "publicationRevision": row["publicationRevision"]} for row in record.get("grants", [])]
    updated = {**{k: v for k, v in record.items() if k not in ("version", "createdAt", "updatedAt")},
               "status": "withdrawn", "withdrawnBy": ctx.actor, "withdrawnAt": ctx.storage.clock(),
               "recallNeeded": recall}
    saved = ctx.commit([_write(PUBLICATION_OWNER, "publication", updated, record["version"])])[0]
    return view(saved)


def _visible_record(ctx, identifier):
    ctx.fresh()
    record = _publication(ctx.storage, identifier)
    if record and record.get("originProject") == ctx.project_id:
        return record
    if record:
        row = ctx.storage.get(ctx.owner, "pub_grant", grant_id(record["id"], record["revision"]))
        if (row and row.get("projectId") == ctx.project_id and row.get("status") == "active"
                and ctx.scope["role"] in row.get("roles", [])):
            return record
    _not_found()


def impact(ctx, publication_id):
    """Cross-project dependents the caller may read; hidden projects add no IDs or counts (AUTH-07)."""
    from workspace.ontology_store import Ontology
    record = _origin_publication(ctx, publication_id)
    dependents = []
    for row in sorted(record.get("grants", []), key=lambda item: item["destinationProject"]):
        try:
            scope = ctx.collaboration.resolve_scope(ctx.actor, row["destinationProject"],
                                                    ctx.scope.get("authorizationExpiresAt"))
        except CollaborationError as error:
            if error.status == 401:
                raise
            continue
        grant_row = ctx.storage.get(scope["owner"], "pub_grant", row["grantId"])
        if not grant_row or grant_row.get("status") != "active":
            continue
        reference = {"sourceKind": "published-asset", "sourceId": record["id"],
                     "revision": str(grant_row["publicationRevision"]), "sha256": grant_row["publicationHash"],
                     "audienceRevision": str(grant_row["revision"])}
        try:
            nodes = Ontology(Service(ctx.host, scope, ctx.claims)).source_nodes(reference)
        except CollaborationError as error:
            if error.status == 401:
                raise
            continue
        if nodes:
            dependents.append({"projectId": row["destinationProject"], "nodeIds": nodes})
    ctx.fresh()
    return {"publicationId": record["id"], "dependents": dependents,
            "coverage": {"complete": False, "unknown": ["restricted-or-unmapped"]}}


# published-asset source adapter ------------------------------------------------

def _upstream(storage, origin, ref):
    """The origin source's current audience still admits organization reuse; returns its record."""
    owner = f"project:{origin}"
    kind = ref["sourceKind"]
    if kind == "asset":
        row = storage.get(owner, "asset", ref["sourceId"])
        if (not row or row.get("projectId") != origin or row.get("accessRevoked") or row.get("tombstone")
                or row.get("status") == "deleted" or row.get("uploadStatus") != "stored"
                or row.get("sha256") != ref["sha256"] or str(row.get("importRevision", 1)) != ref["revision"]
                or ref["audienceRevision"] != PROJECT_AUDIENCE):
            return None
        return [("asset", row)]
    if kind == "document-revision":
        document = storage.get(owner, "document", ref["sourceId"])
        revision = storage.get(owner, "docrevision", ref["revision"]) if ref["revision"].startswith(
            ref["sourceId"] + "--") else None
        if (not document or not revision or document.get("projectId") != origin
                or document.get("status") != "active" or revision.get("documentId") != document["id"]
                or revision.get("status") != "approved" or revision.get("sha256") != ref["sha256"]
                or str(document.get("aclVersion")) != ref["audienceRevision"]
                or sorted(document.get("readRoles", [])) != sorted(ROLES)):
            return None
        return [("document", document), ("docrevision", revision)]
    if kind == "product-guideline":
        guide = storage.get(owner, "guideline", ref["sourceId"])
        if (not guide or guide.get("projectId") != origin or guide.get("status") != "published"
                or guide.get("sha256") != ref["sha256"] or str(guide.get("revision")) != ref["revision"]
                or ref["audienceRevision"] != PROJECT_AUDIENCE):
            return None
        return [("guideline", guide)]
    if kind == "package":
        from workspace.component_catalog import read_catalog
        catalog = read_catalog()
        if ref["sourceId"] != catalog["id"] or ref["sha256"] != catalog["hash"]:
            return None
        return []
    return None


def _snapshot(storage, entry):
    key, expected = entry.get("snapshotKey"), entry.get("snapshotSha256")
    if not key or not storage.owns_key(PUBLICATION_OWNER, key):
        fail(409, "ontology-source-integrity", "게시 스냅샷을 확인하지 못했습니다.")
    import hashlib
    import json
    info = storage.blob_info(key)
    if info["sha256"] != expected or info["size"] > 4_000_000:
        fail(409, "ontology-source-integrity", "게시 스냅샷의 크기 또는 해시가 다릅니다.")
    raw = storage.get_blob(key, length=4_000_000)
    if hashlib.sha256(raw).hexdigest() != expected:
        fail(409, "ontology-source-integrity", "게시 스냅샷 바이트를 확인하지 못했습니다.")
    return raw, json.loads(raw)


def resolve_published(sources, ref, *, historical=False, text=False):
    """Destination resolution: active grant for this role ∩ published revision ∩ current upstream."""
    ctx, storage = sources.ctx, sources.storage
    if not _REVISION.fullmatch(ref["revision"]) or not _REVISION.fullmatch(ref["audienceRevision"]):
        _not_found()
    revision = int(ref["revision"])
    row = storage.get(ctx.owner, "pub_grant", grant_id(ref["sourceId"], revision))
    if (not row or row.get("projectId") != ctx.project_id or row.get("publicationId") != ref["sourceId"]
            or row.get("publicationRevision") != revision or row.get("status") != "active"
            or ctx.scope["role"] not in row.get("roles", [])):
        _not_found()
    record = storage.get(PUBLICATION_OWNER, "publication", ref["sourceId"])
    if not record or record.get("originProject") != row.get("originProject"):
        _not_found()
    if str(row["revision"]) != ref["audienceRevision"] or row.get("publicationHash") != ref["sha256"]:
        fail(409, "ontology-source-stale", "게시물 수락 기준이 변경되었습니다. 새 수락을 확인하세요.")
    entry = next((item for item in record.get("history", []) if item.get("revision") == revision), None)
    if not entry or entry.get("hash") != ref["sha256"]:
        fail(409, "source-changed", "게시물 리비전의 해시가 다릅니다.")
    sources._remember("pub_grant", row)
    sources._remember_owned(PUBLICATION_OWNER, "publication", record)
    raw, snapshot = _snapshot(storage, entry)
    for node in snapshot.get("nodes", []):
        for source in node.get("sourceRefs", []):
            upstream = _upstream(storage, record["originProject"], schema.source_ref(source))
            if upstream is None:
                if historical:
                    _not_found()
                fail(409, "source-upstream-revoked", "원본 프로젝트의 읽기 범위가 변경되어 게시물을 사용할 수 없습니다.")
            for kind, item in upstream:
                sources._remember_owned(f"project:{record['originProject']}", kind, item)
    if not historical:
        if record["status"] == "withdrawn":
            fail(409, "source-withdrawn", "철회된 게시물입니다.")
        if record["revision"] != revision or record["status"] != "published":
            fail(409, "source-superseded", "새 게시 리비전으로 대체되었습니다. 새 수락이 필요합니다.")
        if record["hash"] != ref["sha256"]:
            fail(409, "source-changed", "게시물 리비전의 해시가 다릅니다.")
    return {"record": view(record) | {"revision": revision, "hash": entry["hash"]},
            "text": raw.decode("utf-8") if text else None}


def route(host, scope, claims, method, parts, body, query):
    ctx = Service(host, scope, claims)
    if parts == [] and method == "POST":
        fields(body, {"kind", "nodeIds", "revisionBindings"})
        return 201, {"publication": propose(ctx, kind=body.get("kind"), node_ids=body.get("nodeIds"),
                                            revision_bindings=body.get("revisionBindings"))}
    if parts == [] and method == "GET":
        ctx.fresh()
        from workbench.service import limit
        if query.get("view", "origin") == "granted":
            page = ctx.storage.list_page(ctx.owner, "pub_grant", limit=limit(query), cursor=query.get("cursor"))
            items = [public(row) for row in page["items"] if row.get("projectId") == ctx.project_id]
            return 200, {"grants": items, **({"cursor": page["cursor"]} if page.get("cursor") else {})}
        if query.get("view", "origin") != "origin":
            fail(400, "invalid-input", "view는 origin 또는 granted입니다.")
        page = ctx.storage.list_page(PUBLICATION_OWNER, "publication", limit=limit(query),
                                     cursor=query.get("cursor"))
        items = [view(row) for row in page["items"] if row.get("originProject") == ctx.project_id]
        return 200, {"publications": items, **({"cursor": page["cursor"]} if page.get("cursor") else {})}
    if len(parts) == 1 and method == "GET":
        return 200, {"publication": view(_visible_record(ctx, parts[0]))}
    if len(parts) == 2 and method == "GET" and parts[1] == "impact":
        return 200, impact(ctx, parts[0])
    if len(parts) == 2 and method == "POST":
        if parts[1] == "approve":
            fields(body, set())
            return 200, {"publication": approve(ctx, parts[0])}
        if parts[1] == "withdraw":
            fields(body, set())
            return 200, {"publication": withdraw(ctx, parts[0])}
        if parts[1] == "grants":
            fields(body, {"roles"})
            row = grant(ctx, parts[0], roles=body.get("roles"))
            record = _publication(ctx.storage, parts[0])
            return 201, {"grant": row, "reference": published_asset_reference(record, row)}
    fail(404, "not-found", "Route not found")
