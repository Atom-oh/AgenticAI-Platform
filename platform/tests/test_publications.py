"""Shared-publication authority and the `published-asset` adapter (B0 sharing, Task H3).

Acceptance: AUTH-07 (cross-project impact disclosure), AUTH-08 (withdrawal and
upstream restriction), ONT-04/09 (source kind, pagination), HAND-02-05 source
bindings. Real Storage/CAS, document library, canonical ontology and the
IAM-only intake administration entry point.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_support import api, approved, call, document_ref  # noqa: F401,E402
from test_documents_library import approve as approve_document, finalize as finalize_document, upload  # noqa: E402
from test_ontology_schema import node  # noqa: E402
from intake import admin_handler, records  # noqa: E402
from intake.records import INTAKE_OWNER  # noqa: E402
from workbench.service import Service  # noqa: E402
from workspace import publications  # noqa: E402
from workspace.collaboration import CollaborationError  # noqa: E402
from workspace.ontology_sources import Sources  # noqa: E402
from workspace.ontology_store import Ontology  # noqa: E402

DAY = 86_400_000
LAMBDA = SimpleNamespace(invoked_function_arn="arn:aws:lambda:ap-northeast-2:000000000000:function:IntakeAdminFn")
MEMBERS = {"alice": "owner", "bob": "planner", "carol": "designer", "dana": "developer"}


def new_project(api, request, members):
    status, payload, _ = call(api, "POST", "/projects", {"name": request, "requestId": request},
                              actor=next(actor for actor, role in members.items() if role == "owner"))
    assert status == 201, payload
    row = payload["project"]
    owner = next(actor for actor, role in members.items() if role == "owner")
    status, payload, _ = call(api, "PUT", f"/projects/{row['id']}/members", {
        "version": row["version"], "members": {actor: {"role": role} for actor, role in members.items()}},
        actor=owner)
    assert status == 200, payload
    return row["id"]


def ctx(api, actor, pid):
    return Service(api, api.collaboration.resolve_scope(actor, pid), {"sub": actor, "exp": int(time.time()) + 3600})


def admin(api, event):
    result = admin_handler.handler({"operator": "security-operator", **event}, LAMBDA)
    assert result.get("ok"), result
    return result["record"]


def capability(api, actor, name="design_publish", identifier=None):
    return admin(api, {"op": "grant_capability", "record": {
        "id": identifier or f"cap-{actor}-{name.replace('_', '-')}", "actor": actor, "name": name,
        "expiresAt": api.storage.clock() + 30 * DAY}})


def share(api, pid, ref, expected=None):
    """IAM-only organization-sharing source policy for one exact source revision."""
    source = {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256")}
    return admin(api, {"op": "grant_sharing", **({"expectedRevision": expected} if expected else {}), "record": {
        "id": records.sharing_policy_id(pid, source), "projectId": pid, "source": source,
        "audience": "organization", "expiresAt": api.storage.clock() + 30 * DAY}})


def approved_nodes(api, pid, *, documents=1, request="collection", extra_refs=()):
    """Seed approved design nodes whose sources are approved document revisions."""
    refs = [approved(api, pid, f"Synthetic design source {i}.\n".encode(), name=f"design-{i}.txt",
                     request=f"{request}-{i}") for i in range(documents)]
    nodes = [node(f"atom-{i}", "Atom", project=pid, sourceRefs=[ref, *extra_refs]) for i, ref in enumerate(refs)]
    graph = {"schemaVersion": 1, "projectId": pid, "nodes": nodes, "edges": []}
    result = Ontology(ctx(api, "alice", pid)).publish_candidate("collection", graph, expected_generation=None,
                                                                request_id=request)
    ids = [result["identities"][n["id"]] for n in nodes]
    generation = result["generation"]
    for identifier in ids:
        for decision in ("reviewed", "approved"):
            reviewed = Ontology(ctx(api, "carol", pid)).review_node(
                identifier, expected_generation=generation, revision=1, decision=decision,
                reason="checked", request_id=f"{identifier[:20]}-{decision}")
            generation = reviewed["generation"]
    current = Ontology(ctx(api, "alice", pid)).read(ids)["nodes"]
    return {n["id"]: n for n in current}, refs


def bindings(nodes):
    return {identifier: {"revision": n["revision"], "contentHash": n["contentHash"]} for identifier, n in nodes.items()}


@pytest.fixture
def org(api, monkeypatch):
    monkeypatch.setattr(admin_handler, "storage_factory", lambda: api.storage)
    people = ("alice", "bob", "carol", "dana", "erin", "frank", "gina", "hank", "ivy")
    api.collaboration.directory = lambda q: [{"sub": x, "displayName": x} for x in people if q in x]
    origin = new_project(api, "origin", MEMBERS)
    dest = new_project(api, "dest", {"erin": "owner", "frank": "planner", "gina": "designer"})
    hidden = new_project(api, "hidden", {"hank": "owner", "ivy": "designer"})
    nodes, refs = approved_nodes(api, origin)
    for ref in refs:
        share(api, origin, ref)
    return SimpleNamespace(api=api, origin=origin, dest=dest, hidden=hidden, nodes=nodes, refs=refs)


def denied(fn, *args, **kwargs):
    with pytest.raises(CollaborationError) as error:
        fn(*args, **kwargs)
    return error.value.status, error.value.code


def proposed(org, actor="carol"):
    return publications.propose(ctx(org.api, actor, org.origin), kind="design", node_ids=sorted(org.nodes),
                                revision_bindings=bindings(org.nodes))


def published(org, publisher="carol"):
    pub = proposed(org)
    capability(org.api, publisher)
    return publications.approve(ctx(org.api, publisher, org.origin), pub["id"])


def granted(org, pub, roles=("designer",), dest=None, owner="erin"):
    return publications.grant(ctx(org.api, owner, dest or org.dest), pub["id"], roles=list(roles))


def test_propose_approve_grant_and_resolve_published_asset(org):
    pub = proposed(org)
    assert pub["status"] == "proposed" and pub["originProject"] == org.origin and pub["revision"] == 1
    assert [n["id"] for n in pub["nodes"]] == sorted(org.nodes)
    assert denied(publications.propose, ctx(org.api, "bob", org.origin), kind="design",
                  node_ids=sorted(org.nodes), revision_bindings=bindings(org.nodes))[0] == 403
    capability(org.api, "carol")
    pub = publications.approve(ctx(org.api, "carol", org.origin), pub["id"])
    assert pub["status"] == "published" and pub["approvedBy"] == "carol"
    grant = granted(org, pub)
    ref = publications.published_asset_reference(pub, grant)
    assert ref == {"sourceKind": "published-asset", "sourceId": pub["id"], "revision": "1",
                   "sha256": pub["hash"], "audienceRevision": "1"}
    value = Sources(ctx(org.api, "gina", org.dest)).resolve(ref, text=True)
    assert value["kind"] == "published-asset" and value["record"]["id"] == pub["id"]
    assert {n["id"] for n in json.loads(value["text"])["nodes"]} == set(org.nodes)


def test_project_owner_without_capability_cannot_approve(org):
    pub = publications.propose(ctx(org.api, "alice", org.origin), kind="design", node_ids=sorted(org.nodes),
                               revision_bindings=bindings(org.nodes))
    assert denied(publications.approve, ctx(org.api, "alice", org.origin), pub["id"]) == (
        403, "publication-capability-required")
    # A project owner has no API path to self-grant: the capability route does not exist.
    status, payload, _ = call(org.api, "POST", "/publications/capabilities",
                              {"name": "design_publish", "actor": "alice"}, actor="alice", project=org.origin)
    assert status in (403, 404) and org.api.storage.list(INTAKE_OWNER, "capability") == []
    # The in-app operator group confers nothing either.
    claims = {"sub": "alice", "exp": int(time.time()) + 3600, "cognito:groups": ["platform-operators", "admin"]}
    scope = org.api.collaboration.resolve_scope("alice", org.origin)
    assert denied(publications.approve, Service(org.api, scope, claims), pub["id"])[1] == \
        "publication-capability-required"


def test_capability_holder_without_origin_source_authority_cannot_approve(org):
    pub = proposed(org)
    capability(org.api, "bob")
    # bob is an origin member but a planner: no design source authority.
    assert denied(publications.approve, ctx(org.api, "bob", org.origin), pub["id"]) == (
        403, "publication-authority-required")
    capability(org.api, "gina")
    # gina holds the capability but is not an origin member at all.
    status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/approve", {}, actor="gina",
                              project=org.origin)
    assert status == 403
    # From her own project the origin publication is indistinguishable from a missing one.
    assert denied(publications.approve, ctx(org.api, "gina", org.dest), pub["id"]) == (404, "not-found")
    assert denied(publications.approve, ctx(org.api, "gina", org.dest), "pub-absent") == (404, "not-found")
    # A revoked capability no longer approves.
    cap = org.api.storage.get(INTAKE_OWNER, "capability", "cap-carol-design-publish")
    assert cap is None
    capability(org.api, "carol")
    admin(org.api, {"op": "revoke_capability", "id": "cap-carol-design-publish", "expectedRevision": 1})
    assert denied(publications.approve, ctx(org.api, "carol", org.origin), pub["id"]) == (
        403, "publication-capability-required")


def test_origin_source_restriction_at_approval_blocks_publication(org):
    pub = proposed(org)
    capability(org.api, "carol")
    ref = org.refs[0]
    document = org.api.storage.get(f"project:{org.origin}", "document", ref["sourceId"])
    status, payload, _ = call(org.api, "PUT", f"/documents/{document['id']}/permissions",
                              {"version": document["version"], "readRoles": ["owner", "planner"]},
                              actor="alice", project=org.origin)
    assert status == 200, payload
    assert denied(publications.approve, ctx(org.api, "carol", org.origin), pub["id"])[0] in (403, 409)


def test_wrong_role_destination_read_is_denied(org):
    pub = published(org)
    grant = granted(org, pub, roles=("designer",))
    ref = publications.published_asset_reference(pub, grant)
    assert denied(Sources(ctx(org.api, "frank", org.dest)).resolve, ref) == (404, "not-found")
    assert denied(Sources(ctx(org.api, "frank", org.dest)).authorize, ref) == (404, "not-found")
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(ref)["record"]["id"] == pub["id"]
    # Non-owners cannot accept grants; the hidden project cannot read the destination grant.
    assert denied(publications.grant, ctx(org.api, "gina", org.dest), pub["id"], roles=["planner"])[0] == 403
    assert denied(Sources(ctx(org.api, "ivy", org.hidden)).resolve, ref) == (404, "not-found")


def test_withdrawal_immediately_denies_new_resolution(org):
    pub = published(org)
    grant = granted(org, pub)
    ref = publications.published_asset_reference(pub, grant)
    reader = Sources(ctx(org.api, "gina", org.dest))
    reader.resolve(ref)
    withdrawn = publications.withdraw(ctx(org.api, "alice", org.origin), pub["id"])
    assert withdrawn["status"] == "withdrawn" and withdrawn["recallNeeded"] is True
    assert "grants" not in withdrawn and org.dest not in json.dumps(withdrawn)
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, ref) == (409, "source-withdrawn")
    with pytest.raises(CollaborationError):
        reader.recheck()
    # Historical metadata remains while the upstream is still readable.
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(ref, historical=True)["record"]["status"] == "withdrawn"
    assert denied(publications.grant, ctx(org.api, "erin", org.dest), pub["id"], roles=["designer"]) == (
        404, "not-found")
    assert denied(publications.withdraw, ctx(org.api, "bob", org.origin), pub["id"])[0] == 403


def test_upstream_restriction_overrides_a_grant(org):
    pub = published(org)
    grant = granted(org, pub)
    ref = publications.published_asset_reference(pub, grant)
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(ref)
    source = org.refs[0]
    document = org.api.storage.get(f"project:{org.origin}", "document", source["sourceId"])
    status, payload, _ = call(org.api, "PUT", f"/documents/{document['id']}/permissions",
                              {"version": document["version"], "readRoles": ["owner", "designer"]},
                              actor="alice", project=org.origin)
    assert status == 200, payload
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, ref) == (409, "source-upstream-revoked")
    assert denied(Sources(ctx(org.api, "gina", org.dest)).authorize, ref) == (404, "not-found")


def revise_node(org):
    """Publish a new revision of the single origin node and approve it again."""
    pid, ref = org.origin, org.refs[0]
    current = Ontology(ctx(org.api, "alice", pid)).current()
    graph = {"schemaVersion": 1, "projectId": pid, "edges": [],
             "nodes": [node("atom-0", "Atom", project=pid, sourceRefs=[ref], title="revised", revision=2)]}
    result = Ontology(ctx(org.api, "alice", pid)).publish_candidate(
        "collection", graph, expected_generation=current["generation"], request_id="collection-r2")
    identifier, generation = result["identities"]["atom-0"], result["generation"]
    for decision in ("reviewed", "approved"):
        generation = Ontology(ctx(org.api, "carol", pid)).review_node(
            identifier, expected_generation=generation, revision=2, decision=decision, reason="checked",
            request_id=f"r2-{decision}")["generation"]
    return {n["id"]: n for n in Ontology(ctx(org.api, "alice", pid)).read([identifier])["nodes"]}


def test_new_publication_revision_requires_a_new_grant(org):
    pub = published(org)
    grant = granted(org, pub)
    old = publications.published_asset_reference(pub, grant)
    nodes = revise_node(org)
    # The published revision is an immutable snapshot: origin edits alone do not change it.
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(old)["record"]["revision"] == 1
    assert denied(publications.propose, ctx(org.api, "carol", org.origin), kind="design",
                  node_ids=sorted(nodes), revision_bindings=bindings(org.nodes))[1] == "publication-binding-stale"
    pub2 = publications.propose(ctx(org.api, "carol", org.origin), kind="design", node_ids=sorted(nodes),
                                revision_bindings=bindings(nodes))
    assert pub2["id"] == pub["id"] and pub2["revision"] == 2 and pub2["status"] == "proposed"
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, old) == (409, "source-superseded")
    pub2 = publications.approve(ctx(org.api, "carol", org.origin), pub2["id"])
    assert pub2["revision"] == 2 and pub2["status"] == "published"
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, old)[1] == "source-superseded"
    guessed = {**old, "revision": "2", "sha256": pub2["hash"]}
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, guessed) == (404, "not-found")
    renewed = granted(org, pub2)
    assert renewed["publicationRevision"] == 2 and renewed["id"] != grant["id"]
    ref = publications.published_asset_reference(pub2, renewed)
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(ref)["record"]["revision"] == 2
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(old, historical=True)["historical"] is True


def test_cross_project_impact_hides_unauthorized_node_ids_and_counts(org):
    pub = published(org)
    visible = granted(org, pub, roles=("owner", "designer"))
    hidden_grant = granted(org, pub, roles=("owner", "designer"), dest=org.hidden, owner="hank")
    # alice joins the visible destination only.
    owner = f"project:{org.dest}"
    project = org.api.storage.get(owner, "project", org.dest)
    status, payload, _ = call(org.api, "PUT", f"/projects/{org.dest}/members", {
        "version": project["version"], "members": {**{a: {"role": m["role"]} for a, m in project["members"].items()},
                                                   "alice": {"role": "designer"}}}, actor="erin")
    assert status == 200, payload

    def dependents(pid, actor, grant, count):
        ref = publications.published_asset_reference(pub, grant)
        graph = {"schemaVersion": 1, "projectId": pid,
                 "nodes": [node(f"screen-{pid[:8]}-{i}", "Screen", project=pid, sourceRefs=[ref])
                           for i in range(count)],
                 "edges": []}
        result = Ontology(ctx(org.api, actor, pid)).publish_candidate(
            "collection", graph, expected_generation=None, request_id=f"uses-{count}")
        return sorted(result["identities"].values())

    shown = dependents(org.dest, "gina", visible, 1)
    concealed = dependents(org.hidden, "ivy", hidden_grant, 3)
    report = publications.impact(ctx(org.api, "alice", org.origin), pub["id"])
    text = json.dumps(report)
    assert report["coverage"] == {"complete": False, "unknown": ["restricted-or-unmapped"]}
    assert report["dependents"] == [{"projectId": org.dest, "nodeIds": shown}]
    assert org.hidden not in text
    assert not any(identifier in text for identifier in concealed)
    # No exact totals or hidden-project counters: only authorized dependents and the boundary.
    assert set(report) == {"publicationId", "dependents", "coverage"}
    assert denied(publications.impact, ctx(org.api, "gina", org.dest), pub["id"]) == (404, "not-found")


def test_publication_routes_and_pagination(org):
    capability(org.api, "carol")
    body = {"kind": "design", "nodeIds": sorted(org.nodes), "revisionBindings": bindings(org.nodes)}
    status, payload, _ = call(org.api, "POST", "/publications", body, actor="carol", project=org.origin)
    assert status == 201, payload
    pub = payload["publication"]
    assert "snapshotKey" not in json.dumps(payload)
    status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/approve", {}, actor="carol",
                              project=org.origin)
    assert status == 200 and payload["publication"]["status"] == "published", payload
    status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/grants", {"roles": ["designer"]},
                              actor="erin", project=org.dest)
    assert status == 201 and payload["reference"]["sourceKind"] == "published-asset", payload
    status, listed, _ = call(org.api, "GET", "/publications", actor="dana", project=org.origin)
    assert status == 200 and [p["id"] for p in listed["publications"]] == [pub["id"]]
    status, listed, _ = call(org.api, "GET", "/publications", actor="gina", project=org.dest,
                             query={"view": "granted"})
    assert status == 200 and [g["publicationId"] for g in listed["grants"]] == [pub["id"]]
    status, listed, _ = call(org.api, "GET", "/publications", actor="ivy", project=org.hidden)
    assert status == 200 and listed["publications"] == []
    assert call(org.api, "GET", f"/publications/{pub['id']}", actor="ivy", project=org.hidden)[0] == 404
    status, detail, _ = call(org.api, "GET", f"/publications/{pub['id']}", actor="gina", project=org.dest)
    assert status == 200 and detail["publication"]["id"] == pub["id"]
    status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/withdraw", {}, actor="carol",
                              project=org.origin)
    assert status == 200 and payload["publication"]["status"] == "withdrawn"
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}/impact", actor="alice",
                              project=org.origin)
    assert status == 200 and payload["coverage"]["unknown"] == ["restricted-or-unmapped"]


def test_capability_records_are_closed_and_iam_only(org):
    record = capability(org.api, "carol")
    assert record["status"] == "active" and records.validate("capability", record)
    bad = admin_handler.handler({"operator": "security-operator", "op": "grant_capability", "record": {
        "id": "cap-x", "actor": "carol", "name": "org_admin", "expiresAt": org.api.storage.clock() + DAY}}, LAMBDA)
    assert bad == {"error": "invalid-record"}
    shaped = admin_handler.handler({"operator": "security-operator", "op": "grant_capability", "headers": {},
                                    "record": {"id": "cap-y", "actor": "carol", "name": "design_publish",
                                               "expiresAt": org.api.storage.clock() + DAY}}, LAMBDA)
    assert shaped == {"error": "forbidden-transport"}
    audits = {(a["op"], a["kind"]) for a in org.api.storage.list(INTAKE_OWNER, "adm_audit")}
    assert ("grant_capability", "capability") in audits


# Fix round 1 (PR #29 review) ------------------------------------------------

def test_capability_expiry_is_rechecked_immediately_before_commit(org, monkeypatch):
    """Finding 7: expiry changes no version, so the commit guard rechecks currency itself."""
    pub = proposed(org)
    storage = org.api.storage
    now = [storage.clock()]
    monkeypatch.setattr(storage, "clock", lambda: now[0])
    admin(org.api, {"op": "grant_capability", "record": {
        "id": "cap-carol-short", "actor": "carol", "name": "design_publish", "expiresAt": now[0] + 60_000}})
    original = storage.put_blob_once

    def slow_snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] += 120_000  # the capability expires while the snapshot is stored
        return result
    monkeypatch.setattr(storage, "put_blob_once", slow_snapshot)
    assert denied(publications.approve, ctx(org.api, "carol", org.origin), pub["id"]) == (
        403, "publication-capability-required")
    assert storage.get(publications.PUBLICATION_OWNER, "publication", pub["id"])["status"] == "proposed"


def test_historical_resolution_returns_the_authorized_revision_not_the_current_record(org):
    """Finding 5: metadata comes from the granted revision's snapshot and approval history."""
    pub = published(org)
    grant = granted(org, pub)
    old = publications.published_asset_reference(pub, grant)
    first = {n["id"]: (n["revision"], n["contentHash"]) for n in pub["nodes"]}
    nodes = revise_node(org)
    pub2 = publications.propose(ctx(org.api, "carol", org.origin), kind="design", node_ids=sorted(nodes),
                                revision_bindings=bindings(nodes))
    assert pub2["revision"] == 2 and pub2["hash"] != pub["hash"]
    for stage in ("proposed", "published"):
        record = Sources(ctx(org.api, "gina", org.dest)).resolve(old, historical=True)["record"]
        assert record["revision"] == 1 and record["hash"] == pub["hash"]
        assert {n["id"]: (n["revision"], n["contentHash"]) for n in record["nodes"]} == first
        assert record["approvedBy"] == "carol" and record["status"] == "superseded"
        assert pub2["hash"] not in json.dumps(record)
        if stage == "proposed":
            pub2 = publications.approve(ctx(org.api, "carol", org.origin), pub2["id"])


def restrict(org, roles):
    source = org.refs[0]
    document = org.api.storage.get(f"project:{org.origin}", "document", source["sourceId"])
    status, payload, _ = call(org.api, "PUT", f"/documents/{document['id']}/permissions",
                              {"version": document["version"], "readRoles": roles}, actor="alice", project=org.origin)
    assert status == 200, payload


def test_destination_detail_applies_current_upstream_authorization(org):
    """Finding 3: destination detail is the adapter's grant ∩ upstream intersection."""
    pub = published(org)
    granted(org, pub)
    assert call(org.api, "GET", f"/publications/{pub['id']}", actor="gina", project=org.dest)[0] == 200
    restrict(org, ["owner", "designer"])
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}", actor="gina", project=org.dest)
    assert status == 404 and org.refs[0]["sourceId"] not in json.dumps(payload)
    status, listed, _ = call(org.api, "GET", "/publications", actor="gina", project=org.dest,
                             query={"view": "granted"})
    assert status == 200 and listed["grants"] == []


def test_origin_views_hide_publications_whose_sources_the_caller_cannot_read(org):
    """Finding 3: origin detail/listing apply the caller's current source authorization."""
    pub = published(org)
    status, listed, _ = call(org.api, "GET", "/publications", actor="carol", project=org.origin)
    assert status == 200 and [p["id"] for p in listed["publications"]] == [pub["id"]]
    restrict(org, ["owner", "planner"])
    status, listed, _ = call(org.api, "GET", "/publications", actor="carol", project=org.origin)
    assert status == 200 and listed["publications"] == [] and org.refs[0]["sourceId"] not in json.dumps(listed)
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}", actor="carol", project=org.origin)
    assert status == 404 and org.refs[0]["sourceId"] not in json.dumps(payload)


def test_granted_view_omits_grants_that_exclude_the_callers_role(org):
    """Finding 3: `view=granted` shows only grants naming the caller's current role."""
    pub = published(org)
    granted(org, pub, roles=("designer",))
    status, listed, _ = call(org.api, "GET", "/publications", actor="frank", project=org.dest,
                             query={"view": "granted"})
    assert status == 200 and listed["grants"] == [] and pub["id"] not in json.dumps(listed)
    status, listed, _ = call(org.api, "GET", "/publications", actor="erin", project=org.dest,
                             query={"view": "granted"})
    assert status == 200 and listed["grants"] == []
    status, listed, _ = call(org.api, "GET", "/publications", actor="gina", project=org.dest,
                             query={"view": "granted"})
    assert status == 200 and [g["publicationId"] for g in listed["grants"]] == [pub["id"]]


@pytest.fixture
def org_two(api, monkeypatch):
    monkeypatch.setattr(admin_handler, "storage_factory", lambda: api.storage)
    people = ("alice", "bob", "carol", "dana", "erin", "frank", "gina", "hank", "ivy")
    api.collaboration.directory = lambda q: [{"sub": x, "displayName": x} for x in people if q in x]
    origin = new_project(api, "origin", MEMBERS)
    dest = new_project(api, "dest", {"erin": "owner", "frank": "planner", "gina": "designer"})
    hidden = new_project(api, "hidden", {"hank": "owner", "ivy": "designer"})
    nodes, refs = approved_nodes(api, origin, documents=2)
    for ref in refs:
        share(api, origin, ref)
    return SimpleNamespace(api=api, origin=origin, dest=dest, hidden=hidden, nodes=nodes, refs=refs)


def leaks(cursor, *secrets):
    """True if a returned cursor exposes storage keys or any record identifier."""
    import base64
    import binascii
    texts = [cursor]
    try:
        texts.append(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8", "replace"))
    except (binascii.Error, ValueError):
        pass
    return any(marker in text for text in texts for marker in ("publication#", "pub_grant#", "pk", *secrets))


def test_publication_pagination_uses_opaque_scope_bound_cursors(org_two):
    """Finding 4: continuation never embeds record keys and is bound to caller and view."""
    org = org_two
    capability(org.api, "carol")
    pubs = []
    for identifier in sorted(org.nodes):
        pub = publications.propose(ctx(org.api, "carol", org.origin), kind="design", node_ids=[identifier],
                                   revision_bindings={identifier: bindings(org.nodes)[identifier]})
        pubs.append(publications.approve(ctx(org.api, "carol", org.origin), pub["id"]))
        granted(org, pubs[-1])
    ids = {p["id"] for p in pubs}
    # An unrelated project sees nothing and receives no continuation derived from hidden records.
    status, listed, _ = call(org.api, "GET", "/publications", actor="ivy", project=org.hidden, query={"limit": "1"})
    assert status == 200 and listed["publications"] == [] and not leaks(listed.get("cursor", ""), *ids)
    for actor, pid, view, field in (("carol", org.origin, "origin", "publications"),
                                    ("gina", org.dest, "granted", "grants")):
        status, first, _ = call(org.api, "GET", "/publications", actor=actor, project=pid,
                                query={"view": view, "limit": "1"})
        assert status == 200 and len(first[field]) == 1 and first.get("cursor"), first
        assert not leaks(first["cursor"], *ids)
        status, second, _ = call(org.api, "GET", "/publications", actor=actor, project=pid,
                                 query={"view": view, "limit": "1", "cursor": first["cursor"]})
        assert status == 200 and len(second[field]) == 1 and second[field] != first[field]
        # The cursor is bound to actor, project, view and limit.
        assert call(org.api, "GET", "/publications", actor=actor, project=pid,
                    query={"view": view, "limit": "2", "cursor": first["cursor"]})[0] == 409
        assert call(org.api, "GET", "/publications", actor="hank", project=org.hidden,
                    query={"view": view, "limit": "1", "cursor": first["cursor"]})[0] == 409


def test_organization_publication_requires_an_explicit_sharing_policy_per_source(org):
    """Finding 8: project-wide audience alone is not organization-sharing permission."""
    policy = records.sharing_policy_id(org.origin, {k: org.refs[0][k] for k in ("sourceKind", "sourceId", "revision",
                                                                                 "sha256")})
    admin(org.api, {"op": "revoke_sharing", "id": policy, "expectedRevision": 1})
    assert denied(proposed, org) == (409, "publication-source-policy-required")
    # A project owner has no route to create one; only the IAM-only admin entry point does.
    status, _, _ = call(org.api, "POST", "/publications/sharing", {"sourceId": org.refs[0]["sourceId"]},
                        actor="alice", project=org.origin)
    assert status in (400, 403, 404)
    renewed = share(org.api, org.origin, org.refs[0], expected=2)
    assert renewed["revision"] == 3 and renewed["status"] == "active"
    pub = published(org)
    assert pub["sharing"] == [{"policyId": policy, "revision": 3}]
    grant = granted(org, pub)
    ref = publications.published_asset_reference(pub, grant)
    assert Sources(ctx(org.api, "gina", org.dest)).resolve(ref)
    reader = Sources(ctx(org.api, "gina", org.dest))
    reader.resolve(ref)
    admin(org.api, {"op": "revoke_sharing", "id": policy, "expectedRevision": 3})
    with pytest.raises(CollaborationError):
        reader.recheck()
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, ref) == (409, "source-upstream-revoked")
    assert denied(Sources(ctx(org.api, "gina", org.dest)).authorize, ref) == (404, "not-found")
    assert call(org.api, "GET", f"/publications/{pub['id']}", actor="gina", project=org.dest)[0] == 404
    # A re-issued policy is a new version: the approved revision stays bound to the revoked one.
    share(org.api, org.origin, org.refs[0], expected=4)
    assert denied(Sources(ctx(org.api, "gina", org.dest)).resolve, ref) == (409, "source-upstream-revoked")


def test_sharing_policy_expiry_is_rechecked_at_approval_commit(org, monkeypatch):
    """Finding 8/7: the sharing policy is fenced and its expiry rechecked before submission."""
    storage = org.api.storage
    now = [storage.clock()]
    monkeypatch.setattr(storage, "clock", lambda: now[0])
    source = {k: org.refs[0][k] for k in ("sourceKind", "sourceId", "revision", "sha256")}
    policy = records.sharing_policy_id(org.origin, source)
    admin(org.api, {"op": "grant_sharing", "expectedRevision": 1, "record": {
        "id": policy, "projectId": org.origin, "source": source, "audience": "organization",
        "expiresAt": now[0] + 60_000}})
    pub = proposed(org)
    capability(org.api, "carol")
    original = storage.put_blob_once

    def slow_snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] += 120_000
        return result
    monkeypatch.setattr(storage, "put_blob_once", slow_snapshot)
    assert denied(publications.approve, ctx(org.api, "carol", org.origin), pub["id"]) == (
        409, "source-upstream-revoked")


# Fix round 2 (PR #29 review 2) ----------------------------------------------

def package_ref():
    from workspace.component_catalog import read_catalog
    catalog = read_catalog()
    return {"sourceKind": "package", "sourceId": catalog["id"], "revision": catalog["version"],
            "sha256": catalog["hash"], "audienceRevision": "platform-package-v1"}


def test_published_package_sources_join_the_package_recheck_set(api, monkeypatch):
    """Review 2 #9: publication package checks populate `package_hashes`."""
    from workspace import component_catalog
    monkeypatch.setattr(admin_handler, "storage_factory", lambda: api.storage)
    people = ("alice", "bob", "carol", "dana", "erin", "frank", "gina")
    api.collaboration.directory = lambda q: [{"sub": x, "displayName": x} for x in people if q in x]
    origin = new_project(api, "origin", MEMBERS)
    dest = new_project(api, "dest", {"erin": "owner", "frank": "planner", "gina": "designer"})
    nodes, refs = approved_nodes(api, origin, extra_refs=[package_ref()])
    for ref in refs:
        share(api, origin, ref)
    org = SimpleNamespace(api=api, origin=origin, dest=dest, nodes=nodes, refs=refs)
    pub = published(org)
    ref = publications.published_asset_reference(pub, granted(org, pub))
    reader = Sources(ctx(api, "gina", dest))
    reader.resolve(ref)
    assert reader.package_hashes
    original = component_catalog.read_catalog()
    monkeypatch.setattr(component_catalog, "read_catalog", lambda: {**original, "hash": "f" * 64})
    assert denied(reader.recheck) == (409, "ontology-package-stale")


from test_intake_admission import env  # noqa: E402,F401
from test_intake_transcription import chain, doc_ref, library_review  # noqa: E402,F401


def test_shared_transcription_is_fenced_through_its_image_lineage(chain):
    """Review 2 #2: publication reads validate transcriptionOf -> image admission lineage."""
    api = chain.api
    people = ("alice", "bob", "carol", "dana", "erin", "frank", "gina")
    api.collaboration.directory = lambda q: [{"sub": x, "displayName": x} for x in people if q in x]
    assert library_review(chain, "bob")[0] == 200
    ref = doc_ref(chain)
    graph = {"schemaVersion": 1, "projectId": chain.pid, "edges": [],
             "nodes": [node("atom-t", "Atom", project=chain.pid, sourceRefs=[ref])]}
    result = Ontology(ctx(api, "alice", chain.pid)).publish_candidate("collection", graph, expected_generation=None,
                                                                      request_id="transcribed")
    identifier, generation = result["identities"]["atom-t"], result["generation"]
    for decision in ("reviewed", "approved"):
        generation = Ontology(ctx(api, "carol", chain.pid)).review_node(
            identifier, expected_generation=generation, revision=1, decision=decision, reason="checked",
            request_id=f"t-{decision}")["generation"]
    nodes = {n["id"]: n for n in Ontology(ctx(api, "alice", chain.pid)).read([identifier])["nodes"]}
    share(api, chain.pid, ref)
    dest = new_project(api, "dest", {"erin": "owner", "gina": "designer"})
    org = SimpleNamespace(api=api, origin=chain.pid, dest=dest, nodes=nodes, refs=[ref])
    pub = published(org)
    published_ref = publications.published_asset_reference(pub, granted(org, pub))
    reader = Sources(ctx(api, "gina", dest))
    assert reader.resolve(published_ref, text=True)["text"]
    assert ("intake:deployment", "adm_grant", "grant-dana") in set(reader.observed)
    chain.admin({"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1})
    with pytest.raises(CollaborationError):
        reader.recheck()
    assert denied(Sources(ctx(api, "gina", dest)).resolve, published_ref) == (409, "source-upstream-revoked")
    assert denied(Sources(ctx(api, "gina", dest)).authorize, published_ref) == (404, "not-found")


def test_replayed_approve_and_withdraw_apply_current_source_authorization(org):
    """Review 2 #4: idempotent replay responses never disclose restricted references."""
    pub = published(org)
    restrict(org, ["owner", "planner"])
    secret = org.refs[0]["sourceId"]
    status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/approve", {}, actor="carol",
                              project=org.origin)
    assert status in (403, 404) and secret not in json.dumps(payload)
    for _ in range(2):
        status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/withdraw", {}, actor="alice",
                                  project=org.origin)
        assert status == 200 and payload["publication"]["status"] == "withdrawn"
        assert secret not in json.dumps(payload) and "nodes" not in payload["publication"]


@pytest.mark.parametrize("stage", ["propose", "approve"])
def test_ontology_change_after_the_node_read_fails_the_publication_commit(org, monkeypatch, stage):
    """Review 2 #8: proposal/approval transactions fence the reviewed ontology manifest."""
    pub = proposed(org) if stage == "approve" else None
    capability(org.api, "carol")
    original = Ontology.read
    armed = {"on": True}

    def racing(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if armed["on"]:
            armed["on"] = False
            identifier = sorted(org.nodes)[0]
            Ontology(ctx(org.api, "carol", org.origin)).review_node(
                identifier, expected_generation=result["generation"], revision=org.nodes[identifier]["revision"],
                decision="deprecated", reason="retired", request_id="race-deprecate")
        return result
    monkeypatch.setattr(Ontology, "read", racing)
    if stage == "propose":
        assert denied(proposed, org)[0] == 409
        assert org.api.storage.list(publications.PUBLICATION_OWNER, "publication") == []
    else:
        assert denied(publications.approve, ctx(org.api, "carol", org.origin), pub["id"])[0] == 409
        assert org.api.storage.get(publications.PUBLICATION_OWNER, "publication", pub["id"])["status"] == "proposed"


def transcription_publication(chain):
    api = chain.api
    people = ("alice", "bob", "carol", "dana", "erin", "frank", "gina")
    api.collaboration.directory = lambda q: [{"sub": x, "displayName": x} for x in people if q in x]
    assert library_review(chain, "bob")[0] == 200
    ref = doc_ref(chain)
    graph = {"schemaVersion": 1, "projectId": chain.pid, "edges": [],
             "nodes": [node("atom-t", "Atom", project=chain.pid, sourceRefs=[ref])]}
    result = Ontology(ctx(api, "alice", chain.pid)).publish_candidate("collection", graph, expected_generation=None,
                                                                      request_id="transcribed")
    identifier, generation = result["identities"]["atom-t"], result["generation"]
    for decision in ("reviewed", "approved"):
        generation = Ontology(ctx(api, "carol", chain.pid)).review_node(
            identifier, expected_generation=generation, revision=1, decision=decision, reason="checked",
            request_id=f"t-{decision}")["generation"]
    nodes = {n["id"]: n for n in Ontology(ctx(api, "alice", chain.pid)).read([identifier])["nodes"]}
    share(api, chain.pid, ref)
    dest = new_project(api, "dest", {"erin": "owner", "gina": "designer"})
    org = SimpleNamespace(api=api, origin=chain.pid, dest=dest, nodes=nodes, refs=[ref])
    pub = published(org)
    return dest, publications.published_asset_reference(pub, granted(org, pub))


@pytest.mark.parametrize("change", [{"archived": True}, {"uploadStatus": "uploading"}, {"size": 1}])
def test_shared_transcription_applies_full_image_source_currency(chain, change):
    """Review 3 #4: the original image must pass the complete current-asset checks."""
    dest, ref = transcription_publication(chain)
    reader = Sources(ctx(chain.api, "gina", dest))
    assert reader.resolve(ref, text=True)["text"]
    owner = chain.chain["owner"]
    asset = chain.api.storage.get(owner, "asset", chain.chain["ref"]["sourceId"])
    chain.api.storage.put(owner, "asset", {**asset, **change}, asset["version"])
    with pytest.raises(CollaborationError):
        reader.recheck()
    assert denied(Sources(ctx(chain.api, "gina", dest)).resolve, ref) == (409, "source-upstream-revoked")


def test_publication_listing_rechecks_every_rows_observations_before_responding(org_two, monkeypatch):
    """Review 3 #3: aggregate observations are rechecked once the complete response is ready."""
    org = org_two
    capability(org.api, "carol")
    for identifier in sorted(org.nodes):
        pub = publications.propose(ctx(org.api, "carol", org.origin), kind="design", node_ids=[identifier],
                                   revision_bindings={identifier: bindings(org.nodes)[identifier]})
        publications.approve(ctx(org.api, "carol", org.origin), pub["id"])
    original = publications._origin_readable
    calls = {"n": 0}

    def restricting(ctx_, record, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            for ref in org.refs:
                restrict(SimpleNamespace(api=org.api, origin=org.origin, refs=[ref]), ["owner", "planner"])
        return original(ctx_, record, *args, **kwargs)
    monkeypatch.setattr(publications, "_origin_readable", restricting)
    status, listed, _ = call(org.api, "GET", "/publications", actor="carol", project=org.origin)
    assert calls["n"] >= 2
    assert status == 409 or not any(ref["sourceId"] in json.dumps(listed) for ref in org.refs), listed



# Fix round 4 (PR #29 review 4) ----------------------------------------------

def test_grant_for_an_inaccessible_publication_equals_a_missing_one(org):
    """Review 4 #5: creation and replay require current upstream sharing eligibility."""
    pub = published(org)
    body = {"roles": ["designer"]}
    status, created, _ = call(org.api, "POST", f"/publications/{pub['id']}/grants", body, actor="erin",
                              project=org.dest)
    assert status == 201, created
    restrict(org, ["owner", "designer"])
    missing = call(org.api, "POST", "/publications/pub-absent/grants", body, actor="erin", project=org.dest)[:2]
    # Replay of the accepted grant and a new acceptance for other roles are both missing.
    for roles in (["designer"], ["designer", "planner"]):
        status, payload, _ = call(org.api, "POST", f"/publications/{pub['id']}/grants", {"roles": roles},
                                  actor="erin", project=org.dest)
        assert (status, payload) == missing, payload
        assert org.origin not in json.dumps(payload) and pub["hash"] not in json.dumps(payload)
    assert denied(granted, org, pub) == (404, "not-found")
    rows = org.api.storage.list(f"project:{org.hidden}", "pub_grant")
    assert denied(granted, org, pub, dest=org.hidden, owner="hank") == (404, "not-found")
    assert org.api.storage.list(f"project:{org.hidden}", "pub_grant") == rows == []



def test_cross_project_impact_rechecks_every_destination_reader(org, monkeypatch):
    """Review 4 #6: every contributing destination scope is rechecked before responding."""
    pub = published(org)
    grants = {org.dest: granted(org, pub, roles=("owner", "designer")),
              org.hidden: granted(org, pub, roles=("owner", "designer"), dest=org.hidden, owner="hank")}
    owners = {org.dest: "erin", org.hidden: "hank"}
    for pid, owner in owners.items():
        project = org.api.storage.get(f"project:{pid}", "project", pid)
        status, payload, _ = call(org.api, "PUT", f"/projects/{pid}/members", {
            "version": project["version"], "members": {**{a: {"role": m["role"]} for a, m in project["members"].items()},
                                                       "alice": {"role": "designer"}}}, actor=owner)
        assert status == 200, payload
        ref = publications.published_asset_reference(pub, grants[pid])
        graph = {"schemaVersion": 1, "projectId": pid, "edges": [],
                 "nodes": [node(f"screen-{pid[:8]}", "Screen", project=pid, sourceRefs=[ref])]}
        Ontology(ctx(org.api, owner, pid)).publish_candidate("collection", graph, expected_generation=None,
                                                             request_id=f"uses-{pid[:8]}")
    assert len(publications.impact(ctx(org.api, "alice", org.origin), pub["id"])["dependents"]) == 2
    original = Ontology.source_nodes
    seen = []

    def removing(self, reference, **kwargs):
        result = original(self, reference, **kwargs)
        seen.append(self.ctx.project_id)
        if len(seen) == 2:
            first = seen[0]
            project = org.api.storage.get(f"project:{first}", "project", first)
            members = {a: {"role": m["role"]} for a, m in project["members"].items() if a != "alice"}
            status, payload, _ = call(org.api, "PUT", f"/projects/{first}/members",
                                      {"version": project["version"], "members": members}, actor=owners[first])
            assert status == 200, payload
        return result
    monkeypatch.setattr(Ontology, "source_nodes", removing)
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}/impact", actor="alice", project=org.origin)
    assert len(seen) == 2
    assert status != 200 or seen[0] not in json.dumps(payload), payload
    # Direct callers get the same guarantee: impact itself rechecks every destination reader.
    for pid, owner in owners.items():
        project = org.api.storage.get(f"project:{pid}", "project", pid)
        if "alice" not in project["members"]:
            call(org.api, "PUT", f"/projects/{pid}/members", {"version": project["version"], "members": {
                **{a: {"role": m["role"]} for a, m in project["members"].items()}, "alice": {"role": "designer"}}},
                actor=owner)
    seen.clear()
    with pytest.raises(CollaborationError):
        publications.impact(ctx(org.api, "alice", org.origin), pub["id"])
    assert len(seen) == 2


# Fix round 5 (PR #29 review 5) -----------------------------------------------

def _replacement_revision(org, ref):
    """Revise the single origin node to bind a different (replacement) source, as
    revision 2, leaving the original source (`org.refs[0]`) free to be restricted
    independently of the new revision."""
    pid = org.origin
    current = Ontology(ctx(org.api, "alice", pid)).current()
    graph = {"schemaVersion": 1, "projectId": pid, "edges": [],
             "nodes": [node("atom-0", "Atom", project=pid, sourceRefs=[ref], title="replacement", revision=2)]}
    result = Ontology(ctx(org.api, "alice", pid)).publish_candidate(
        "collection", graph, expected_generation=current["generation"], request_id="collection-replacement")
    identifier, generation = result["identities"]["atom-0"], result["generation"]
    for decision in ("reviewed", "approved"):
        generation = Ontology(ctx(org.api, "carol", pid)).review_node(
            identifier, expected_generation=generation, revision=2, decision=decision, reason="checked",
            request_id=f"replacement-{decision}")["generation"]
    return {n["id"]: n for n in Ontology(ctx(org.api, "alice", pid)).read([identifier])["nodes"]}


def test_publication_detail_authorizes_the_exact_returned_revision(org, monkeypatch):
    """Review 5 #5: the gate must authorize the payload's OWN revision, not whatever the
    record has become by the time it checks. Reproduced: detail builds its response from
    revision 1; before the gate's final authorization re-fetches the record, revision 2
    (bound to a replacement source) is proposed and approved, and revision 1's original
    source is restricted. The stale revision-1 payload must not be released with 200, even
    though the now-current revision 2 remains genuinely accessible."""
    pub = published(org)
    replacement = approved(org.api, org.origin, b"Replacement design source.\n", name="design-1.txt",
                           request="design-replacement")
    share(org.api, org.origin, replacement)
    revised = _replacement_revision(org, replacement)
    original_view = publications.view
    armed = {"on": True}

    def racing(record):
        result = original_view(record)
        if armed["on"] and record.get("revision") == 1:
            armed["on"] = False
            pub2 = publications.propose(ctx(org.api, "carol", org.origin), kind="design",
                                        node_ids=sorted(revised), revision_bindings=bindings(revised))
            publications.approve(ctx(org.api, "carol", org.origin), pub2["id"])
            restrict(org, ["owner", "planner"])  # excludes carol (designer) from the original source
        return result
    monkeypatch.setattr(publications, "view", racing)
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}", actor="carol", project=org.origin)
    assert status == 404, payload
    # Revision 2 (bound to the replacement source, never restricted) is genuinely reachable.
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}", actor="carol", project=org.origin)
    assert status == 200 and payload["publication"]["revision"] == 2, payload


def test_impact_fences_each_destinations_exact_ontology_generation(org, monkeypatch):
    """Review 6 #1: impact retains each destination's Sources reader but not its exact
    manifest generation, so a republish that swaps a node's source for a still-existing
    but now owner-only-restricted one (no membership or grant change at all) goes
    undetected by Sources.recheck() alone. Reproduced: republish the first destination
    while inspecting the second; the first destination's node becomes invisible to alice
    (a designer there), but impact's cached result for it was read before the swap."""
    pub = published(org)
    grants = {org.dest: granted(org, pub, roles=("owner", "designer")),
              org.hidden: granted(org, pub, roles=("owner", "designer"), dest=org.hidden, owner="hank")}
    owners = {org.dest: "erin", org.hidden: "hank"}
    for pid, owner in owners.items():
        project = org.api.storage.get(f"project:{pid}", "project", pid)
        status, payload, _ = call(org.api, "PUT", f"/projects/{pid}/members", {
            "version": project["version"], "members": {**{a: {"role": m["role"]} for a, m in project["members"].items()},
                                                       "alice": {"role": "designer"}}}, actor=owner)
        assert status == 200, payload
        ref = publications.published_asset_reference(pub, grants[pid])
        graph = {"schemaVersion": 1, "projectId": pid, "edges": [],
                 "nodes": [node(f"screen-{pid[:8]}", "Screen", project=pid, sourceRefs=[ref])]}
        Ontology(ctx(org.api, owner, pid)).publish_candidate("collection", graph, expected_generation=None,
                                                             request_id=f"uses-{pid[:8]}")
    original = Ontology.source_nodes
    seen = []

    def racing(self, reference, **kwargs):
        result = original(self, reference, **kwargs)
        seen.append(self.ctx.project_id)
        if len(seen) == 2:
            first = seen[0]
            # `approved()` always finalizes/approves as "alice", who only holds
            # "designer" here; do the upload/finalize/approve as the destination owner.
            uploaded = upload(org.api, data=b"Owner-only source.\n", actor=owners[first], project=first,
                             name="owner-only.txt", request="owner-only")
            finalized = finalize_document(org.api, uploaded, actor=owners[first], project=first)
            status, _ = approve_document(org.api, finalized, actor=owners[first], project=first)
            assert status == 200
            document = org.api.storage.get(f"project:{first}", "document", uploaded["document"]["id"])
            status, payload, _ = call(org.api, "PUT", f"/documents/{document['id']}/permissions",
                                      {"version": document["version"], "readRoles": ["owner"]},
                                      actor=owners[first], project=first)
            assert status == 200, payload
            # The sourceRef's audienceRevision must match the ACL bump above, or
            # publish_candidate itself (correctly) rejects it as a changed source.
            restricted = document_ref(org.api, first, uploaded)
            current = Ontology(ctx(org.api, owners[first], first)).current()
            graph = {"schemaVersion": 1, "projectId": first, "edges": [],
                     "nodes": [node(f"screen-{first[:8]}", "Screen", project=first, sourceRefs=[restricted],
                                    revision=2)]}
            Ontology(ctx(org.api, owners[first], first)).publish_candidate(
                "collection", graph, expected_generation=current["generation"], request_id=f"restrict-{first[:8]}")
        return result
    monkeypatch.setattr(Ontology, "source_nodes", racing)
    status, payload, _ = call(org.api, "GET", f"/publications/{pub['id']}/impact", actor="alice", project=org.origin)
    assert len(seen) == 2
    assert status != 200 or seen[0] not in json.dumps(payload), payload


def test_impact_reads_the_manifest_generation_atomically_with_source_nodes(org, monkeypatch):
    """Review 7 #2 (round-6 #1, partial): impact() read the fenced generation via a
    SEPARATE `ontology.current()` call made AFTER `source_nodes()` had already read
    the manifest -- a gap between the two reads (not just across destination
    iterations, which the round-6 fix does catch) that an intervening republish could
    still slip through. The generation must come from source_nodes()'s own read
    (`Ontology._last_generation`, re-confirmed current by its own internal `_recheck`
    before it returns), never a second, independent read. Verified structurally: with
    the fix, `Ontology.current` is called exactly once per destination -- never a
    second time -- so there is no gap left to race at all."""
    pub = published(org)
    grant = granted(org, pub, roles=("owner", "designer"))
    ref = publications.published_asset_reference(pub, grant)
    graph = {"schemaVersion": 1, "projectId": org.dest, "edges": [],
             "nodes": [node("screen-dest", "Screen", project=org.dest, sourceRefs=[ref])]}
    Ontology(ctx(org.api, "erin", org.dest)).publish_candidate("collection", graph, expected_generation=None,
                                                               request_id="uses-dest")
    # alice (the impact caller, from origin) must also be a member of the
    # destination for it to contribute -- matching the existing impact tests' setup.
    project = org.api.storage.get(f"project:{org.dest}", "project", org.dest)
    status, payload, _ = call(org.api, "PUT", f"/projects/{org.dest}/members", {
        "version": project["version"], "members": {**{a: {"role": m["role"]} for a, m in project["members"].items()},
                                                   "alice": {"role": "designer"}}}, actor="erin")
    assert status == 200, payload
    calls = []
    original_current = Ontology.current

    def counting(self):
        calls.append(self)
        result = original_current(self)
        if len(calls) > 1:
            # A stale/racy second read would see a different (bumped) generation here;
            # the fix must never reach this branch at all.
            result = {**result, "generation": "0" * 64}
        return result
    monkeypatch.setattr(Ontology, "current", counting)
    report = publications.impact(ctx(org.api, "alice", org.origin), pub["id"])
    assert len(report["dependents"]) == 1 and report["dependents"][0]["projectId"] == org.dest
    assert len(report["dependents"][0]["nodeIds"]) == 1, report
    assert len(calls) == 1, "source_nodes and the caller must share the same manifest read"
