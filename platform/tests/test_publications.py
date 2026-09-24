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
from intake_support import api, approved, call  # noqa: F401,E402
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


def approved_nodes(api, pid, *, documents=1, request="collection"):
    """Seed approved design nodes whose sources are approved document revisions."""
    refs = [approved(api, pid, f"Synthetic design source {i}.\n".encode(), name=f"design-{i}.txt",
                     request=f"{request}-{i}") for i in range(documents)]
    nodes = [node(f"atom-{i}", "Atom", project=pid, sourceRefs=[ref]) for i, ref in enumerate(refs)]
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
