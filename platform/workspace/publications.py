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
from workspace.ontology_sources import _AUTHORITY_CODES, PROJECT_AUDIENCE, Sources, authority_identity

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


def origin_prefix(project_id):
    """Publication IDs start with a per-origin prefix so an origin listing never scans other projects."""
    return "pub-" + schema.digest(["publication-origin", project_id])[:12] + "-"


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
    return schema.digest({**{key: record[key] for key in ("id", "originProject", "kind", "revision", "nodes")},
                          "sharing": record.get("sharing", [])})


def _policy_source(ref):
    return {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256")}


def sharing_policy(storage, origin, ref):
    """The current IAM-administered organization-sharing policy for this exact origin source, or None.

    Project-wide audience is not organization permission (AGENTCORE_CONTRACT
    "Shared-publication authority"): each source needs its own policy record.
    """
    from intake import records
    source = _policy_source(ref)
    if source["sourceKind"] not in records.SHARING_SOURCE_KINDS:
        return None
    try:
        row = storage.get(INTAKE_OWNER, "adm_sharing", records.sharing_policy_id(origin, source))
        row = records.validate("adm_sharing", row) if row else None
    except ValueError:
        return None
    if (not row or row["projectId"] != origin or row["source"] != source or row["audience"] != "organization"
            or not records.is_current(row, storage.clock())):
        return None
    return row


def _sharing_bindings(storage, origin, nodes):
    """Every non-package node source needs a current sharing policy; returns bindings and fences."""
    policies = {}
    for node in nodes:
        for ref in node["sourceRefs"]:
            if ref["sourceKind"] == "package":
                continue  # The platform component package is platform-wide, not project-owned content.
            row = sharing_policy(storage, origin, ref)
            if row is None:
                fail(409, "publication-source-policy-required",
                     "조직 공유가 승인되지 않은 원본은 게시할 수 없습니다. 원본별 조직 공유 정책이 필요합니다.")
            policies[row["id"]] = row
    bindings = [{"policyId": row["id"], "revision": row["revision"]} for row in
                sorted(policies.values(), key=lambda item: item["id"])]
    checks = [{"owner": INTAKE_OWNER, "kind": "adm_sharing", "id": row["id"], "version": row["version"]}
              for row in policies.values()]
    return bindings, checks


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
    checks = ontology.sources.recheck()
    sharing, fences = _sharing_bindings(ctx.storage, ctx.project_id, nodes)
    return nodes, [*checks, *fences], sharing


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
    nodes, checks, sharing = _origin_nodes(ctx, kind, node_ids, revision_bindings, denied=denied)
    summary = [{"id": n["id"], "revision": n["revision"], "contentHash": n["contentHash"],
                "type": n["type"], "sourceRefs": n["sourceRefs"]} for n in nodes]
    identifier = origin_prefix(ctx.project_id) + schema.digest([ctx.project_id, kind, node_ids])[:32]
    previous = ctx.storage.get(PUBLICATION_OWNER, "publication", identifier)
    if previous:
        if previous["status"] == "withdrawn":
            fail(409, "publication-withdrawn", "철회된 게시물은 다시 제안할 수 없습니다. 새 게시로 제안하세요.")
        if previous["nodes"] == summary and previous.get("sharing") == sharing:
            return view(previous)
    revision = previous["revision"] + 1 if previous else 1
    history = list((previous or {}).get("history", []))
    record = {"id": identifier, "projectId": ctx.project_id, "originProject": ctx.project_id, "kind": kind,
              "revision": revision, "nodes": summary, "sharing": sharing, "status": "proposed",
              "proposedBy": ctx.actor,
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
        nodes, checks, sharing = _origin_nodes(ctx, kind, node_ids, bindings, denied=denied)
    except CollaborationError as error:
        if error.status in (401,) or error.code in ("publication-binding-stale", "publication-authority-required",
                                                   "publication-source-policy-required"):
            raise
        fail(403, "publication-authority-required", "게시할 원본을 현재 권한으로 확인하지 못했습니다.")
    if sharing != record.get("sharing"):
        fail(409, "publication-binding-stale", "원본의 조직 공유 정책이 제안 이후 변경되었습니다. 다시 제안하세요.")
    snapshot = schema.canonical({"publicationId": record["id"], "revision": record["revision"],
                                 "hash": record["hash"], "nodes": nodes, "sharing": sharing})
    key = ctx.storage.key_for(PUBLICATION_OWNER, "publication", record["id"], f"revisions/{record['revision']}.json")
    info = ctx.storage.put_blob_once(key, snapshot, "application/json")
    approved_at = ctx.storage.clock()
    # Revision-specific approval history: historical resolution never reads the current record's metadata.
    entry = {"revision": record["revision"], "hash": record["hash"], "snapshotKey": key,
             "snapshotSha256": info["sha256"], "approvedBy": ctx.actor, "approvedAt": approved_at,
             "capability": {"id": capability["id"], "revision": capability["revision"]}, "sharing": sharing}
    updated = {**{k: v for k, v in record.items() if k not in ("version", "createdAt", "updatedAt")},
               "status": "published", "approvedBy": ctx.actor, "approvedAt": approved_at,
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
    result = view(saved)
    if not _origin_readable(ctx, saved):
        # Withdrawal is protective and allowed; bound source references stay hidden from this caller.
        result.pop("nodes", None)
    return result


def _passes(check):
    """Run one authorization; only expiry and frozen-authority failures propagate."""
    try:
        check()
        return True
    except CollaborationError as error:
        if error.status == 401 or error.code in _AUTHORITY_CODES:
            raise
        return False


def _origin_readable(ctx, record):
    """An origin member sees a publication only while every recorded source is readable to them now."""
    refs = {}
    for node in record.get("nodes", []):
        for ref in node.get("sourceRefs", []):
            refs[authority_identity(ref)] = ref

    def check():
        sources = Sources(ctx, max_records=4 * MAX_NODES + 10, max_sources=MAX_NODES * 4)
        for ref in refs.values():
            sources.authorize(ref)
        sources.recheck()
    return _passes(check)


def _grant_reference(row):
    return {"sourceKind": "published-asset", "sourceId": row["publicationId"],
            "revision": str(row["publicationRevision"]), "sha256": row["publicationHash"],
            "audienceRevision": str(row["revision"])}


def _granted_view(ctx, row):
    """A destination grant's revision metadata: grant ∩ caller role ∩ current upstream, else None."""
    if (not isinstance(row, dict) or row.get("projectId") != ctx.project_id or row.get("status") != "active"
            or ctx.scope["role"] not in row.get("roles", [])):
        return None
    result = {}

    def check():
        sources = Sources(ctx)
        result["record"] = resolve_published(sources, _grant_reference(row), historical=True)["record"]
        sources.recheck()
    return result["record"] if _passes(check) else None


def _detail(ctx, identifier):
    """Origin members with current source access, or a destination role an active grant names."""
    ctx.fresh()
    record = _publication(ctx.storage, identifier)
    if not record:
        _not_found()
    if record.get("originProject") == ctx.project_id:
        if _origin_readable(ctx, record):
            return view(record)
        _not_found()
    revisions = sorted({record["revision"], *(entry["revision"] for entry in record.get("history", []))},
                       reverse=True)
    for revision in revisions:
        row = ctx.storage.get(ctx.owner, "pub_grant", grant_id(record["id"], revision))
        if row and row.get("status") == "active" and ctx.scope["role"] in row.get("roles", []):
            value = _granted_view(ctx, row)
            if value is None:
                break
            return value
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

def _admitted(storage, origin, decision_id, revision, artifact_kind):
    """One admitted decision of the origin project with its current policy, grant/provenance.

    A record-level check that needs no origin membership: the decision must be
    schema-valid, admitted and unexpired at the bound revision, and every
    administration record it depends on current. Returns fences or None.
    """
    from intake import admission, records
    owner, now = f"project:{origin}", storage.clock()
    try:
        decision = storage.get(owner, "adm_decision", decision_id)
        decision = records.validate("adm_decision", decision) if decision else None
    except ValueError:
        return None
    if (not decision or decision["projectId"] != origin or decision["status"] != "admitted"
            or not records.is_current(decision, now) or decision["revision"] != revision
            or decision["artifact"]["kind"] != artifact_kind):
        return None
    try:
        policy = admission._policy_current(storage, decision, origin)
    except admission.AdmissionError:
        return None
    fences = [(owner, "adm_decision", decision), (INTAKE_OWNER, "adm_policy", policy)]
    if "provenance" in decision:
        provenance = admission._admin_record(storage, "adm_provenance", decision["provenance"]["id"])
        if (not provenance or provenance["revision"] != decision["provenance"]["revision"]
                or not admission._provenance_ok(provenance, policy, decision["source"], origin,
                                                decision["dataClass"], now)):
            return None
        fences.append((INTAKE_OWNER, "adm_provenance", provenance))
    if "review" in decision:
        grant = admission._admin_record(storage, "adm_grant", decision["review"]["grantId"])
        if (not grant or grant["revision"] != decision["review"]["grantRevision"]
                or not admission._grant_ok(grant, decision["review"]["actor"], policy, origin, now)):
            return None
        fences.append((INTAKE_OWNER, "adm_grant", grant))
    return decision, fences


def _transcription_lineage(storage, origin, revision):
    """A transcription revision is shareable only while its whole image lineage is current.

    The same bindings as `Library._lineage`: the reviewer-validated transcription
    admission, the image admission it derives from, their policies and reviewer
    grants, and the original image asset. Returns every record to fence, or None.
    """
    lineage = revision.get("transcriptionOf")
    if not isinstance(lineage, dict) or not isinstance(lineage.get("transcription"), dict):
        return None
    bound = lineage["transcription"]
    transcribed = _admitted(storage, origin, bound.get("decisionId"), bound.get("decisionRevision"),
                            "diagram-transcription")
    image = _admitted(storage, origin, lineage.get("decisionId"), lineage.get("decisionRevision"), "image")
    if transcribed is None or image is None:
        return None
    (transcription, first), (picture, second) = transcribed, image
    if (transcription["derivation"]["derivativeHash"] != bound.get("artifactHash")
            or transcription.get("lineage") != {"decisionId": picture["id"], "decisionRevision": picture["revision"]}
            or picture["artifact"].get("vision", {}).get("sha256") != lineage.get("visionSha256")
            or dict(picture["source"]) != lineage.get("sourceRef")):
        return None
    source = picture["source"]
    asset = storage.get(f"project:{origin}", "asset", source["sourceId"]) if source["sourceKind"] == "asset" else None
    if (source["sourceKind"] != "asset" or not asset or asset.get("accessRevoked") or asset.get("tombstone")
            or asset.get("status") == "deleted" or asset.get("sha256") != source["sha256"]):
        return None
    return [*first, *second, (f"project:{origin}", "asset", asset)]


def _upstream(storage, origin, ref):
    """The origin source's current audience still admits organization reuse.

    Returns `(owner, kind, record)` fences, or None when the upstream is restricted.
    """
    owner = f"project:{origin}"
    kind = ref["sourceKind"]
    if kind == "asset":
        row = storage.get(owner, "asset", ref["sourceId"])
        if (not row or row.get("projectId") != origin or row.get("accessRevoked") or row.get("tombstone")
                or row.get("status") == "deleted" or row.get("uploadStatus") != "stored"
                or row.get("sha256") != ref["sha256"] or str(row.get("importRevision", 1)) != ref["revision"]
                or ref["audienceRevision"] != PROJECT_AUDIENCE):
            return None
        return [(owner, "asset", row)]
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
        fences = [(owner, "document", document), (owner, "docrevision", revision)]
        if "transcriptionOf" in revision:
            lineage = _transcription_lineage(storage, origin, revision)
            if lineage is None:
                return None
            fences.extend(lineage)
        return fences
    if kind == "product-guideline":
        guide = storage.get(owner, "guideline", ref["sourceId"])
        if (not guide or guide.get("projectId") != origin or guide.get("status") != "published"
                or guide.get("sha256") != ref["sha256"] or str(guide.get("revision")) != ref["revision"]
                or ref["audienceRevision"] != PROJECT_AUDIENCE):
            return None
        return [(owner, "guideline", guide)]
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
    snapshot = json.loads(raw)
    if (not isinstance(snapshot, dict) or snapshot.get("revision") != entry.get("revision")
            or snapshot.get("hash") != entry.get("hash") or not isinstance(snapshot.get("nodes"), list)):
        fail(409, "ontology-source-integrity", "게시 스냅샷이 리비전 기록과 다릅니다.")
    return raw, snapshot


def _revision_view(record, entry, snapshot):
    """Public metadata of exactly one published revision, built from its snapshot and approval entry."""
    revision = entry["revision"]
    if record["revision"] == revision and record["status"] == "published":
        status = "published"
    elif record["status"] == "withdrawn":
        status = "withdrawn"
    else:
        status = "superseded"
    nodes = [{"id": n["id"], "revision": n["revision"], "contentHash": n["contentHash"], "type": n["type"],
              "sourceRefs": n["sourceRefs"]} for n in snapshot["nodes"]]
    return public({"id": record["id"], "originProject": record["originProject"], "kind": record["kind"],
                   "revision": revision, "hash": entry["hash"], "nodes": nodes, "status": status,
                   "approvedBy": entry.get("approvedBy"), "approvedAt": entry.get("approvedAt"),
                   "sharing": entry.get("sharing", []),
                   "recallNeeded": bool(record.get("recallNeeded"))})


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
    if snapshot.get("publicationId") != record["id"]:
        fail(409, "ontology-source-integrity", "게시 스냅샷이 게시물과 다릅니다.")
    bound = {item.get("policyId"): item.get("revision") for item in entry.get("sharing", [])
             if isinstance(item, dict)}
    if entry.get("sharing") != snapshot.get("sharing"):
        fail(409, "ontology-source-integrity", "게시 스냅샷의 공유 정책 기록이 다릅니다.")
    for node in snapshot.get("nodes", []):
        for source in node.get("sourceRefs", []):
            upstream_ref = schema.source_ref(source)
            upstream = _upstream(storage, record["originProject"], upstream_ref)
            if upstream is not None and upstream_ref["sourceKind"] == "package":
                sources.package_hashes.add(upstream_ref["sha256"])
            if upstream is not None and upstream_ref["sourceKind"] != "package":
                # The versioned organization-sharing policy bound at approval must still be current.
                policy = sharing_policy(storage, record["originProject"], upstream_ref)
                if policy is None or bound.get(policy["id"]) != policy["revision"]:
                    upstream = None
                else:
                    sources._remember_owned(INTAKE_OWNER, "adm_sharing", policy)
            if upstream is None:
                if historical:
                    _not_found()
                fail(409, "source-upstream-revoked", "원본 프로젝트의 읽기 범위가 변경되어 게시물을 사용할 수 없습니다.")
            for owner, kind, item in upstream:
                sources._remember_owned(owner, kind, item)
    if not historical:
        if record["status"] == "withdrawn":
            fail(409, "source-withdrawn", "철회된 게시물입니다.")
        if record["revision"] != revision or record["status"] != "published":
            fail(409, "source-superseded", "새 게시 리비전으로 대체되었습니다. 새 수락이 필요합니다.")
        if record["hash"] != ref["sha256"]:
            fail(409, "source-changed", "게시물 리비전의 해시가 다릅니다.")
    return {"record": _revision_view(record, entry, snapshot), "text": raw.decode("utf-8") if text else None}


def _authorized_page(ctx, query, view_name, owner, kind, prefix, include):
    from workspace.ontology_sources import authorized_page
    return authorized_page(ctx, query, view_name, owner, kind, prefix, include, purpose="publications",
                           token="pubcur", stale_code="publication-cursor-stale")


def route(host, scope, claims, method, parts, body, query):
    ctx = Service(host, scope, claims)
    if parts == [] and method == "POST":
        fields(body, {"kind", "nodeIds", "revisionBindings"})
        return 201, {"publication": propose(ctx, kind=body.get("kind"), node_ids=body.get("nodeIds"),
                                            revision_bindings=body.get("revisionBindings"))}
    if parts == [] and method == "GET":
        ctx.fresh()
        if query.get("view", "origin") == "granted":
            page = _authorized_page(ctx, query, "granted", ctx.owner, "pub_grant", "",
                                    lambda row: public(row) if _granted_view(ctx, row) is not None else None)
            return 200, {"grants": page["items"], **({"cursor": page["cursor"]} if "cursor" in page else {})}
        if query.get("view", "origin") != "origin":
            fail(400, "invalid-input", "view는 origin 또는 granted입니다.")
        page = _authorized_page(
            ctx, query, "origin", PUBLICATION_OWNER, "publication", origin_prefix(ctx.project_id),
            lambda row: view(row) if row.get("originProject") == ctx.project_id and _origin_readable(ctx, row) else None)
        return 200, {"publications": page["items"], **({"cursor": page["cursor"]} if "cursor" in page else {})}
    if len(parts) == 1 and method == "GET":
        return 200, {"publication": _detail(ctx, parts[0])}
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
