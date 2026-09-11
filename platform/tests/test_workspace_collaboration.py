"""Project authorization and publication with real storage and injected services."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_storage import FakeS3, FakeTable, TransactionFailure
from workspace.storage import Storage


PEOPLE = [
    {"sub": "alice", "displayName": "Alice"},
    {"sub": "bob", "displayName": "Bob"},
    {"sub": "carol", "displayName": "Carol"},
    {"sub": "dana", "displayName": "Dana"},
]
DRAFT = {
    "title": "정기 적금",
    "description": "매월 같은 금액을 납입합니다.",
    "conditions": [{"id": "amount", "text": "월 납입 금액은 1만원 이상입니다."}],
    "steps": [{"id": "entry", "title": "금액 입력", "description": "월 납입 금액을 입력하세요."},
              {"id": "confirm", "title": "확인", "description": "입력한 금액을 확인하세요."}],
    "notices": [{"id": "consent", "title": "필수 동의", "content": "약관에 동의해야 합니다.", "required": True}],
}


def setup():
    from workspace.collaboration import Collaboration
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test",
                      clock=lambda: 1_800_000_000_000)
    directory = lambda q: [person for person in PEOPLE if q.casefold() in
                           (person["sub"] + " " + person["displayName"]).casefold()]
    return Collaboration(storage, directory=directory), storage


def call(collab, method, path, body=None, actor="alice", project=None, query=None):
    result = collab.handle(method, path.split("/"), body or {}, query or {}, actor, project)
    assert result is not None
    return result[1]


def project_with_members(collab):
    project = call(collab, "POST", "projects", {"name": "Design", "requestId": "create-1"})["project"]
    members = {"alice": {"role": "owner", "displayName": "Alice"},
               "bob": {"role": "planner", "displayName": "Bob"},
               "carol": {"role": "designer", "displayName": "Carol"},
               "dana": {"role": "developer", "displayName": "Dana"}}
    return call(collab, "PUT", f"projects/{project['id']}/members",
                {"version": project["version"], "members": members})["project"]


def product(collab, project, actor="bob"):
    return call(collab, "POST", "products", DRAFT, actor=actor, project=project["id"])["product"]


def publish(collab, project, draft, actor="bob"):
    return call(collab, "POST", f"products/{draft['id']}/publish",
                {"version": draft["version"]}, actor=actor, project=project["id"])


def test_project_creation_is_atomic_idempotent_and_actor_scoped():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    body = {"name": "Shared", "requestId": "same", "owner": "bob", "members": {"bob": {"role": "owner"}}}
    status, payload = collab.handle("POST", ["projects"], body, {}, "alice", None)
    first = payload["project"]
    assert status == 201
    assert set(first["members"]) == {"alice"}
    assert first["createdBy"] == "alice"
    assert len(storage.table().transactions) == 1
    assert len(storage.table().transactions[0]["TransactItems"]) == 2
    assert call(collab, "POST", "projects", body)["project"]["id"] == first["id"]
    assert call(collab, "GET", "projects", actor="bob") == {"projects": []}
    assert call(collab, "GET", "projects")["projects"][0]["id"] == first["id"]
    with pytest.raises(CollaborationError) as error:
        call(collab, "POST", "projects", {**body, "name": "changed"})
    assert error.value.status == 409
    bob = call(collab, "POST", "projects", body, actor="bob")["project"]
    assert bob["id"] != first["id"]


def test_new_project_owner_uses_canonical_directory_name_in_project_and_index():
    collab, storage = setup()
    project = call(collab, "POST", "projects", {"name": "Shared", "requestId": "owner-name"})["project"]
    assert project["members"]["alice"] == {"role": "owner", "displayName": "Alice"}
    assert storage.get(f"project:{project['id']}", "project", project["id"])["members"]["alice"]["displayName"] == "Alice"
    assert storage.get("alice", "membership", project["id"])["displayName"] == "Alice"
    assert project["createdBy"] == "alice"


def test_new_project_owner_directory_match_must_use_exact_actor():
    collab, _ = setup()
    collab.directory = lambda q: [{"sub": "alice-other", "displayName": "Wrong person"}, *PEOPLE]
    project = call(collab, "POST", "projects", {"name": "Shared", "requestId": "exact-owner"})["project"]
    assert project["members"]["alice"]["displayName"] == "Alice"


@pytest.mark.parametrize("availability", ["not-configured", "unavailable", "actor-unavailable"])
def test_project_creation_falls_back_to_actor_when_owner_profile_is_unavailable(availability):
    collab, storage = setup()
    def unavailable(query):
        raise RuntimeError("Directory unavailable")
    collab.directory = {"not-configured": None, "unavailable": unavailable,
                        "actor-unavailable": lambda q: [{"sub": "alice-other", "displayName": "Wrong person"}]}[availability]
    body = {"name": "Shared", "requestId": "fallback-owner"}
    project = call(collab, "POST", "projects", body)["project"]
    assert project["members"]["alice"] == {"role": "owner", "displayName": "alice"}
    assert storage.get("alice", "membership", project["id"])["displayName"] == "alice"
    assert call(collab, "POST", "projects", body)["project"] == project


def test_failed_project_creation_leaves_no_membership_or_project():
    collab, storage = setup()
    def fail(**kwargs):
        raise RuntimeError("transaction unavailable")
    storage.table().meta.client.transact_write_items = fail
    with pytest.raises(RuntimeError):
        call(collab, "POST", "projects", {"name": "Shared", "requestId": "r"})
    assert storage.table().items == {}


def test_canonical_membership_overrides_stale_index_and_previously_resolved_scope():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    saved = collab.resolve_scope("bob", project["id"])
    assert saved["owner"] == f"project:{project['id']}"
    assert saved["actor"] == "bob" and saved["role"] == "planner"
    # Simulate a stale membership index (as opposed to the normal tombstone).
    canonical = storage.get(saved["owner"], "project", project["id"])
    del canonical["members"]["bob"]
    storage.put(saved["owner"], "project", canonical, canonical["version"])
    assert call(collab, "GET", "projects", actor="bob")["projects"] == []
    for operation in [
        lambda: collab.resolve_scope("bob", project["id"]),
        lambda: collab.require(saved, "publish"),
        lambda: call(collab, "GET", "products", actor="bob", project=project["id"]),
    ]:
        with pytest.raises(CollaborationError) as error:
            operation()
        assert error.value.status == 403


@pytest.mark.parametrize("actor,action,allowed", [
    ("alice", "members", True), ("bob", "members", False),
    ("bob", "edit_product", True), ("carol", "edit_product", False),
    ("bob", "publish", True), ("dana", "publish", False),
    ("carol", "edit_rules", True), ("bob", "edit_rules", True), ("dana", "edit_rules", False),
    ("carol", "generate", True), ("bob", "generate", False),
    ("carol", "approve", True), ("dana", "approve", False),
    ("dana", "export", True), ("bob", "export", False),
    ("alice", "release", True), ("carol", "release", True),
    ("dana", "release", True), ("bob", "release", False),
    ("dana", "discuss", True), ("dana", "upload", True),
])
def test_role_permissions_are_explicit_and_unknown_actions_fail_closed(actor, action, allowed):
    from workspace.collaboration import CollaborationError
    collab, _ = setup()
    project = project_with_members(collab)
    scope = collab.resolve_scope(actor, project["id"])
    if allowed:
        collab.require(scope, action)
    else:
        with pytest.raises(CollaborationError) as error:
            collab.require(scope, action)
        assert error.value.status == 403
    with pytest.raises(CollaborationError):
        collab.require(scope, "invented_action")


def test_personal_scope_retains_personal_actions_without_exposing_projects():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    personal = collab.resolve_scope("alice", None)
    assert personal == {"owner": "alice", "actor": "alice", "project": None, "role": "owner"}
    for action in ("upload", "edit_rules", "generate", "approve", "release", "export"):
        collab.require(personal, action)
    storage.put("alice", "asset", {"id": "private", "name": "Personal"})
    project = project_with_members(collab)
    shared = collab.resolve_scope("bob", project["id"])
    assert storage.list(shared["owner"], "asset") == []
    with pytest.raises(CollaborationError):
        call(collab, "GET", "products")
    with pytest.raises(CollaborationError):
        collab.require({**personal, "owner": shared["owner"]}, "read")


def test_member_changes_require_owner_cas_and_preserve_an_owner():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    body = {"version": project["version"], "members": project["members"]}
    with pytest.raises(CollaborationError) as error:
        call(collab, "PUT", f"projects/{project['id']}/members", body, actor="bob")
    assert error.value.status == 403
    with pytest.raises(CollaborationError):
        call(collab, "PUT", f"projects/{project['id']}/members", {**body, "version": 1})
    with pytest.raises(CollaborationError):
        call(collab, "PUT", f"projects/{project['id']}/members",
             {**body, "members": {"bob": {"role": "planner", "displayName": "Bob"}}})
    members = {key: value for key, value in project["members"].items() if key != "bob"}
    call(collab, "PUT", f"projects/{project['id']}/members", {**body, "members": members})
    assert call(collab, "GET", "projects", actor="bob")["projects"] == []
    assert storage.list("bob", "membership")[0]["status"] == "revoked"


def test_directory_lookup_is_owner_only_bounded_and_does_not_expose_extra_fields():
    from workspace.collaboration import CollaborationError
    collab, _ = setup()
    project = project_with_members(collab)
    looked_up = []
    def directory(q):
        looked_up.append(q)
        return [{"sub": f"person{i}", "displayName": "Person", "email": "private@example.test",
                 "secret": "never-return"} for i in range(100)]
    collab.directory = directory
    with pytest.raises(CollaborationError):
        call(collab, "GET", f"projects/{project['id']}/people", actor="bob", query={"q": "Person"})
    assert looked_up == []
    people = call(collab, "GET", f"projects/{project['id']}/people", query={"q": "Person"})["people"]
    assert len(people) == 20
    assert all(set(person) == {"sub", "displayName"} for person in people)
    with pytest.raises(CollaborationError):
        call(collab, "GET", f"projects/{project['id']}/people", query={"q": "x" * 201})


def test_product_drafts_are_project_scoped_validated_and_cas_protected():
    from workspace.collaboration import CollaborationError
    collab, _ = setup()
    project = project_with_members(collab)
    draft = product(collab, project)
    assert draft["conditions"] == DRAFT["conditions"]
    assert draft["createdBy"] == "bob" and draft["status"] == "draft"
    with pytest.raises(CollaborationError) as error:
        call(collab, "PUT", f"products/{draft['id']}", {"version": 1, "title": "Changed"},
             actor="dana", project=project["id"])
    assert error.value.status == 403
    changed = call(collab, "PUT", f"products/{draft['id']}", {"version": 1, "title": "Changed"},
                   actor="bob", project=project["id"])["product"]
    assert changed["version"] == 2 and changed["conditions"] == DRAFT["conditions"]
    with pytest.raises(CollaborationError) as error:
        call(collab, "PUT", f"products/{draft['id']}", {"version": 1, "title": "Stale"},
             actor="bob", project=project["id"])
    assert error.value.status == 409
    other = call(collab, "POST", "projects", {"name": "Other", "requestId": "other"})["project"]
    with pytest.raises(CollaborationError) as error:
        call(collab, "GET", f"products/{draft['id']}", project=other["id"])
    assert error.value.status == 404
    for invalid in [
        {"conditions": [DRAFT["conditions"][0]] * 2},
        {"conditions": [{"id": "../unsafe", "text": "text"}]},
        {"notices": [{**DRAFT["notices"][0], "required": "true"}]},
        {"description": "x" * 60_001},
    ]:
        with pytest.raises(CollaborationError):
            call(collab, "PUT", f"products/{draft['id']}", {"version": 2, **invalid},
                 actor="bob", project=project["id"])


def test_publication_persists_exact_typed_projection_and_private_system_guide():
    from workspace.collaboration import Collaboration
    collab, storage = setup()
    project = project_with_members(collab)
    draft = product(collab, project)
    result = publish(collab, project, draft)
    guideline, ontology = result["guideline"], result["ontology"]
    assert result["product"]["publishedGuidelineId"] == guideline["id"]
    assert result["product"]["publishedRevision"] == guideline["revision"] == 1
    assert guideline["publishedBy"] == "bob"
    assert {node["label"] for node in ontology["nodes"]} == {
        "Product", "Condition", "PolicyRule", "Procedure", "ScreenMeta"}
    nodes = {node["id"]: node for node in ontology["nodes"]}
    for edge in ontology["edges"]:
        assert edge["src"] in nodes and edge["dst"] in nodes
    for node in nodes.values():
        source = node["props"]["source"]
        assert source["guidelineId"] == guideline["id"] and source["revision"] == 1
        assert source["text"] in guideline["text"]
    condition = next(node for node in nodes.values() if node["label"] == "Condition")
    assert condition["props"]["text"] == DRAFT["conditions"][0]["text"]
    encoded = json.dumps({k: v for k, v in ontology.items() if k != "hash"},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    assert ontology["hash"] == hashlib.sha256(encoded).hexdigest()
    scope = collab.resolve_scope("carol", project["id"])
    asset = storage.get(scope["owner"], "asset", guideline["assetId"])
    assert asset["purpose"] == "guide" and asset["system"] is True
    assert asset["uploadStatus"] == "stored" and asset["parseStatus"] == "complete"
    assert storage.get_blob(asset["originalKey"]).decode() == guideline["text"]
    assert json.loads(storage.get_blob(asset["analysisKey"]))["text"] == guideline["text"]
    assert all("ACL" not in put and put["CacheControl"] == "private, no-store" for put in storage.s3().puts)
    assert all("Tagging" not in put for put in storage.s3().puts)
    # A new process uses the saved projection; no ephemeral GraphStore is required.
    reopened = Collaboration(storage)
    context = reopened.published_context(scope, draft["id"])
    assert context["ontology"] == ontology and context["assetId"] == asset["id"]
    assert call(reopened, "GET", f"products/{draft['id']}/ontology",
                actor="carol", project=project["id"])["ontology"] == ontology
    assert not any("ttl" in item for item in storage.table().items.values())


@pytest.mark.parametrize("notice_id,expected_page", [
    ("consent", "notice-consent"),
    ("a" * 57, "notice-" + "a" * 57),
    ("CONSENT", "notice-14a85890244de5dcae9c3183"),
    ("notice_id", "notice-6a49d5b37120bdf9bf53518b"),
    ("a" * 58, "notice-d5c039b748aa64665782974e"),
    ("a" * 128, "notice-6836cf13bac400e9105071cd"),
])
def test_notice_page_ids_are_react_safe_stable_and_preserve_source_identity(notice_id, expected_page):
    collab, _ = setup()
    project = project_with_members(collab)
    data = copy.deepcopy(DRAFT)
    data["notices"][0]["id"] = notice_id
    draft = call(collab, "POST", "products", data, actor="bob", project=project["id"])["product"]
    first = publish(collab, project, draft)
    edited = call(collab, "PUT", f"products/{draft['id']}", {
        "version": first["product"]["version"], "description": "Revised description"},
        actor="bob", project=project["id"])["product"]
    second = publish(collab, project, edited)
    for result in (first, second):
        screen = next(node for node in result["ontology"]["nodes"] if node["label"] == "ScreenMeta")
        policy = next(node for node in result["ontology"]["nodes"] if node["label"] == "PolicyRule")
        assert screen["props"]["pageId"] == expected_page
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", screen["props"]["pageId"])
        assert screen["id"] == f"{result['guideline']['id']}:ScreenMeta:{notice_id}"
        assert policy["props"]["id"] == notice_id
        assert screen["props"]["source"]["text"] == "필수 동의\n약관에 동의해야 합니다."
        assert f"[{notice_id}]" in result["guideline"]["text"]
        assert [row["id"] for row in result["product"]["notices"]] == [notice_id]
    assert first["guideline"]["id"] != second["guideline"]["id"]


def test_notice_page_normalization_cannot_alias_two_source_notices():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    data = copy.deepcopy(DRAFT)
    data["notices"] = [{**data["notices"][0], "id": identifier}
                       for identifier in ("CONSENT", "14a85890244de5dcae9c3183")]
    draft = call(collab, "POST", "products", data, actor="bob", project=project["id"])["product"]
    with pytest.raises(CollaborationError) as error:
        publish(collab, project, draft)
    assert error.value.status == 400
    assert storage.list(f"project:{project['id']}", "guideline") == []


def test_publication_failure_leaves_entire_previous_revision_current():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    first = publish(collab, project, product(collab, project))
    edited = call(collab, "PUT", f"products/{first['product']['id']}",
                  {"version": first["product"]["version"], "description": "새 설명"},
                  actor="bob", project=project["id"])["product"]
    before = copy.deepcopy(storage.table().items)
    def reject(**kwargs):
        raise TransactionFailure()
    storage.table().meta.client.transact_write_items = reject
    with pytest.raises(CollaborationError) as error:
        publish(collab, project, edited)
    assert error.value.status == 409
    assert storage.table().items == before
    assert len(storage.list(f"project:{project['id']}", "guideline")) == 1
    context = collab.published_context(collab.resolve_scope("carol", project["id"]), edited["id"])
    assert context["guideline"]["id"] == first["guideline"]["id"]


def test_revocation_during_publication_cannot_commit_under_stale_authority():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    draft = product(collab, project)
    owner = f"project:{project['id']}"
    def revoke():
        current = storage.get(owner, "project", project["id"])
        del current["members"]["bob"]
        storage.put(owner, "project", current, current["version"])
    storage.table().before_transaction = revoke
    with pytest.raises(CollaborationError):
        publish(collab, project, draft)
    assert storage.list(owner, "guideline") == []
    assert storage.list(owner, "ontology") == []
    assert storage.list(owner, "asset") == []
    assert storage.get(owner, "product", draft["id"])["publishedGuidelineId"] is None


def test_new_guideline_marks_bound_runs_stale_without_changing_historical_approval():
    collab, storage = setup()
    project = project_with_members(collab)
    first = publish(collab, project, product(collab, project))
    scope = collab.resolve_scope("carol", project["id"])
    run = storage.put(scope["owner"], "run", {
        "id": "r1", "status": "approved", "projectId": project["id"],
        "productId": first["product"]["id"], "guidelineId": first["guideline"]["id"],
        "ontologyHash": first["ontology"]["hash"], "approval": {"actor": "carol", "round": 1}})
    assert collab.is_current(scope, run) is True
    edit = call(collab, "PUT", f"products/{first['product']['id']}",
                {"version": first["product"]["version"], "description": "Changed"},
                actor="bob", project=project["id"])["product"]
    assert collab.is_current(scope, run) is True  # An unpublished draft is not a new standard.
    second = publish(collab, project, edit)
    assert second["guideline"]["revision"] == 2
    assert collab.is_current(scope, run) is False
    impact = call(collab, "GET", f"products/{edit['id']}/impact", project=project["id"])
    assert impact["currentGuidelineId"] == second["guideline"]["id"]
    assert [row["id"] for row in impact["affectedRuns"]] == ["r1"]
    assert storage.get(scope["owner"], "run", "r1") == run
    old = call(collab, "GET", f"products/{edit['id']}/ontology", project=project["id"],
               query={"revision": first["guideline"]["id"]})["ontology"]
    assert old == first["ontology"]


def test_missing_swapped_or_corrupt_ontology_cannot_be_current():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    result = publish(collab, project, product(collab, project))
    scope = collab.resolve_scope("carol", project["id"])
    run = {"projectId": project["id"], "productId": result["product"]["id"],
           "guidelineId": result["guideline"]["id"], "ontologyHash": result["ontology"]["hash"]}
    for bad in [
        {**run, "projectId": "foreign"}, {**run, "guidelineId": None},
        {**run, "ontologyHash": "0" * 64}, {**run, "productId": None},
    ]:
        assert collab.is_current(scope, bad) is False
    metadata = storage.get(scope["owner"], "ontology", result["guideline"]["id"])
    storage.s3().objects[(storage.bucket, metadata["graphKey"])]["data"] = b'{"nodes":[]}'
    assert collab.is_current(scope, run) is False
    with pytest.raises(CollaborationError):
        collab.published_context(scope, result["product"]["id"])


def test_comments_bind_author_and_anchor_and_are_idempotent_per_actor():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    draft = product(collab, project)
    body = {"requestId": "comment-1", "text": "조건을 확인했습니다.", "anchor": {"productId": draft["id"]},
            "author": "alice", "createdAt": 0}
    first = call(collab, "POST", "comments", body, actor="dana", project=project["id"])["comment"]
    assert first["author"] == "dana" and first["createdAt"] == storage.clock()
    assert first["anchor"] == {"productId": draft["id"]}
    retry = call(collab, "POST", "comments", body, actor="dana", project=project["id"])["comment"]
    assert retry == first
    with pytest.raises(CollaborationError) as error:
        call(collab, "POST", "comments", {**body, "text": "변경"}, actor="dana", project=project["id"])
    assert error.value.status == 409
    second = call(collab, "POST", "comments", body, actor="bob", project=project["id"])["comment"]
    assert second["id"] != first["id"]
    listed = call(collab, "GET", "comments", actor="carol", project=project["id"],
                  query={"productId": draft["id"]})["comments"]
    assert {row["author"] for row in listed} == {"bob", "dana"}
    assert all("requestHash" not in row for row in listed)
    for invalid in [
        {**body, "text": "x" * 4001}, {**body, "anchor": {"productId": "unknown"}},
        {**body, "anchor": {"pageId": "entry"}}, {**body, "anchor": {"productId": draft["id"], "round": True}},
        {**body, "anchor": {"owner": "alice", "productId": draft["id"]}},
    ]:
        with pytest.raises(CollaborationError):
            call(collab, "POST", "comments", invalid, actor="dana", project=project["id"])


def test_comment_round_page_and_guideline_cannot_be_swapped_between_products():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    first = publish(collab, project, product(collab, project))
    second = publish(collab, project, product(collab, project))
    scope = collab.resolve_scope("alice", project["id"])
    storage.put(scope["owner"], "run", {
        "id": "r1", "projectId": project["id"], "productId": first["product"]["id"],
        "guidelineId": first["guideline"]["id"],
        "rounds": [{"number": 1, "pageSources": [{"pageId": "entry", "path": "src/pages/entry.tsx"}]}]})
    body = {"requestId": "anchored", "text": "Page note",
            "anchor": {"runId": "r1", "round": 1, "pageId": "entry"}}
    comment = call(collab, "POST", "comments", body, project=project["id"])["comment"]
    assert comment["anchor"]["productId"] == first["product"]["id"]
    for anchor in [
        {**body["anchor"], "round": 2}, {**body["anchor"], "pageId": "not-rendered"},
        {**body["anchor"], "productId": second["product"]["id"]},
        {**body["anchor"], "guidelineId": second["guideline"]["id"]},
    ]:
        with pytest.raises(CollaborationError):
            call(collab, "POST", "comments", {**body, "anchor": anchor}, project=project["id"])


def test_unowned_routes_are_left_to_parent_http():
    collab, _ = setup()
    assert collab.handle("POST", ["runs"], {}, {}, "alice", None) is None
    assert collab.handle("GET", ["assets"], {}, {}, "alice", None) is None


def test_publication_retry_reuses_immutable_revision_and_does_not_rewrite_blobs():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    draft = product(collab, project)
    first = publish(collab, project, draft)
    writes = len(storage.s3().puts)
    repeat = publish(collab, project, draft)
    assert repeat == first
    assert len(storage.s3().puts) == writes
    updated = call(collab, "PUT", f"products/{draft['id']}",
                   {"version": first["product"]["version"], "title": "New"},
                   actor="bob", project=project["id"])["product"]
    with pytest.raises(CollaborationError) as error:
        publish(collab, project, draft)
    assert error.value.status == 409
    next_revision = publish(collab, project, updated)
    assert next_revision["guideline"]["revision"] == 2
    assert next_revision["guideline"]["id"] != first["guideline"]["id"]
    current = storage.get(f"project:{project['id']}", "guideline", first["guideline"]["id"])
    assert current == first["guideline"]


def test_blob_failure_cannot_publish_metadata():
    collab, storage = setup()
    project = project_with_members(collab)
    draft = product(collab, project)
    before = copy.deepcopy(storage.table().items)
    put = storage.s3().put_object
    def fail_projection(**kwargs):
        if kwargs["Key"].endswith("projection.json"):
            raise RuntimeError("S3 unavailable")
        return put(**kwargs)
    storage.s3().put_object = fail_projection
    with pytest.raises(RuntimeError):
        publish(collab, project, draft)
    assert storage.table().items == before
    assert storage.list(f"project:{project['id']}", "guideline") == []


def test_unknown_directory_user_cannot_be_added():
    from workspace.collaboration import CollaborationError
    collab, _ = setup()
    project = project_with_members(collab)
    with pytest.raises(CollaborationError) as error:
        call(collab, "PUT", f"projects/{project['id']}/members", {
            "version": project["version"], "members": {
                **project["members"], "not-a-user": {"role": "owner", "displayName": "Spoof"}}})
    assert error.value.status == 400
    collab.directory = None
    with pytest.raises(CollaborationError) as error:
        call(collab, "GET", f"projects/{project['id']}/people", query={"q": "Bob"})
    assert error.value.status == 503


def test_conflicting_contract_snapshot_cannot_appear_current():
    collab, _ = setup()
    project = project_with_members(collab)
    result = publish(collab, project, product(collab, project))
    scope = collab.resolve_scope("carol", project["id"])
    snapshot = {"projectId": project["id"], "productId": result["product"]["id"],
                "guidelineId": result["guideline"]["id"], "ontologyHash": result["ontology"]["hash"]}
    assert collab.is_current(scope, {**snapshot, "contract": snapshot}) is True
    for field in ("projectId", "productId", "guidelineId", "ontologyHash"):
        assert collab.is_current(scope, {**snapshot, "contract": {**snapshot, field: "swapped"}}) is False
    assert collab.is_current(scope, {"projectId": project["id"], "contract": snapshot}) is False


def test_list_pagination_preserves_project_and_comment_scope():
    from workspace.collaboration import CollaborationError
    collab, storage = setup()
    project = project_with_members(collab)
    drafts = [product(collab, project) for _ in range(3)]
    storage.table().page_size = 1
    page = call(collab, "GET", "products", project=project["id"])
    ids = [row["id"] for row in page["products"]]
    cursor = page["cursor"]
    other = call(collab, "POST", "projects", {"name": "Other", "requestId": "other"})["project"]
    with pytest.raises(CollaborationError):
        call(collab, "GET", "products", project=other["id"], query={"cursor": cursor})
    while cursor:
        page = call(collab, "GET", "products", project=project["id"], query={"cursor": cursor})
        ids.extend(row["id"] for row in page["products"])
        cursor = page.get("cursor")
    assert set(ids) == {draft["id"] for draft in drafts}
