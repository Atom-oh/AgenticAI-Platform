"""Source-bound persistent discussion through the production design API."""
import copy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_design_api import api, env, design_state
from test_design_execution import pipeline, ready, execute
from intake.admission import AdmissionError
from workbench.service import Service
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.design_api import decide, read_object, route


@pytest.fixture
def discussion_state(design_state):
    ctx, initial, prd = design_state
    decide(ctx, initial["id"], "prd", {"version": initial["version"], "prd": prd, "hash": schema.digest(prd)})
    design = ctx.get("design", initial["id"])
    flow = read_object(ctx, design["flow"])
    anchors = [
        {"kind": "prd", "id": "prd", "sha256": design["prd"]["sha256"]},
        {"kind": "flow-node", "id": flow["flow"]["screens"][0], "sha256": design["flow"]["sha256"]},
        {"kind": "rule", "id": flow["contract"]["rules"][0]["id"], "sha256": design["flow"]["sha256"]},
    ]
    return ctx, design, anchors


def call(ctx, design, method, *, anchor, body=None, query=None):
    return route(ctx.host, ctx.scope, ctx.claims, method, [design["id"], "comments"],
                 body or {}, {"anchor": json.dumps(anchor), **(query or {})} if method == "GET" else {})


@pytest.mark.parametrize("index", [0, 1, 2])
def test_discussion_is_immutable_exact_and_does_not_approve_or_change_design(discussion_state, index):
    ctx, design, anchors = discussion_state
    anchor = anchors[index]
    before = copy.deepcopy(ctx.get("design", design["id"]))
    body = {"requestId": "discussion", "anchor": anchor, "text": "이 조건의 원문 인용을 함께 확인해주세요."}
    status, first = call(ctx, design, "POST", anchor=anchor, body=body)
    assert status == 201 and first["comment"]["author"] == ctx.actor
    assert first["comment"]["anchor"] == {"designId": design["id"], **anchor}
    status, repeated = call(ctx, design, "POST", anchor=anchor, body=body)
    assert status == 200 and repeated == first
    status, listed = call(ctx, design, "GET", anchor=anchor)
    assert status == 200 and listed["comments"] == [first["comment"]]
    assert ctx.get("design", design["id"]) == before
    assert "workspace/" not in json.dumps(listed)
    _, other = call(ctx, design, "GET", anchor=anchors[(index + 1) % 3])
    assert other["comments"] == []
    with pytest.raises(CollaborationError) as error:
        call(ctx, design, "POST", anchor=anchor, body={**body, "text": "변경된 요청"})
    assert error.value.code == "request-changed"


def test_legacy_discussion_listing_cannot_serve_design_comments(discussion_state, env):
    ctx, design, anchors = discussion_state
    _, posted = call(ctx, design, "POST", anchor=anchors[0], body={
        "requestId": "design-private", "anchor": anchors[0], "text": "설계 전용 의견"})
    status, payload = env.http("GET", "/comments")
    assert status == 200 and posted["comment"]["id"] not in [item["id"] for item in payload["comments"]]
    direct = ctx.collaboration.handle("GET", ["comments"], {}, {}, ctx.actor, ctx.project_id)
    assert posted["comment"]["id"] not in [item["id"] for item in direct[1]["comments"]]


@pytest.mark.parametrize("mutation", ["hash", "target", "unknown", "kind", "oversized"])
def test_invalid_or_stale_anchor_cannot_create_a_comment(discussion_state, mutation):
    ctx, design, anchors = discussion_state
    anchor = dict(anchors[1])
    if mutation == "hash":
        anchor["sha256"] = "0" * 64
    elif mutation == "target":
        anchor["id"] = "outside-screen"
    elif mutation == "unknown":
        anchor["key"] = "arbitrary-private-storage-key"
    elif mutation == "kind":
        anchor["kind"] = "arbitrary"
    body = {"requestId": "denied", "anchor": anchor, "text": "의견" if mutation != "oversized" else "가" * 4001}
    with pytest.raises((CollaborationError, ValueError)):
        call(ctx, design, "POST", anchor=anchor, body=body)
    assert ctx.storage.list(ctx.owner, "comment") == []


def test_source_withdrawal_denies_new_read_and_idempotent_replay(discussion_state, env):
    ctx, design, anchors = discussion_state
    body = {"requestId": "withdrawal", "anchor": anchors[0], "text": "현재 근거에 의견"}
    _, posted = call(ctx, design, "POST", anchor=anchors[0], body=body)
    from intake.records import INTAKE_OWNER
    grant = ctx.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    for method, code in [("GET", "not-found"), ("POST", "grant-revoked")]:
        with pytest.raises((CollaborationError, AdmissionError)) as error:
            call(ctx, design, method, anchor=anchors[0], body=body)
        assert error.value.code == code
    assert ctx.storage.get(ctx.owner, "comment", posted["comment"]["id"])["text"] == body["text"]


def test_comment_cursor_is_bound_to_exact_anchor_and_actor(discussion_state):
    ctx, design, anchors = discussion_state
    for number in range(7):
        call(ctx, design, "POST", anchor=anchors[0], body={
            "requestId": f"page-{number}", "anchor": anchors[0], "text": f"의견 {number}"})
    _, first = call(ctx, design, "GET", anchor=anchors[0])
    assert len(first["comments"]) == 5 and first["cursor"]
    _, last = call(ctx, design, "GET", anchor=anchors[0], query={"cursor": first["cursor"]})
    assert len(last["comments"]) == 2 and not last["cursor"]
    assert len({item["id"] for item in first["comments"] + last["comments"]}) == 7
    with pytest.raises(CollaborationError) as error:
        call(ctx, design, "GET", anchor=anchors[1], query={"cursor": first["cursor"]})
    assert error.value.status == 404
    bob = Service(ctx.host, ctx.collaboration.resolve_scope("bob", ctx.project_id),
                  {"sub": "bob", "exp": ctx.claims["exp"]})
    with pytest.raises(CollaborationError):
        call(bob, design, "GET", anchor=anchors[0], query={"cursor": first["cursor"]})


def test_membership_changed_before_commit_refuses_comment(discussion_state, monkeypatch):
    ctx, design, anchors = discussion_state
    original = ctx.storage.put_many
    injected = False

    def race(writes, *args, **kwargs):
        nonlocal injected
        if not injected and any(item["kind"] == "comment" for item in writes):
            injected = True
            project = ctx.storage.get(ctx.owner, "project", ctx.project_id)
            project["members"]["alice"]["role"] = "developer"
            ctx.storage.put(ctx.owner, "project", project, expected_version=project["version"])
        return original(writes, *args, **kwargs)

    monkeypatch.setattr(ctx.storage, "put_many", race)
    with pytest.raises(CollaborationError) as error:
        call(ctx, design, "POST", anchor=anchors[0], body={
            "requestId": "race", "anchor": anchors[0], "text": "동시 변경"})
    assert error.value.code == "design-comment-changed"
    assert ctx.storage.list(ctx.owner, "comment") == []


@pytest.mark.parametrize("actor", ["bob", "carol", "dana"])
def test_project_planner_designer_and_developer_can_discuss_current_evidence(discussion_state, actor):
    ctx, design, anchors = discussion_state
    member = Service(ctx.host, ctx.collaboration.resolve_scope(actor, ctx.project_id),
                     {"sub": actor, "exp": ctx.claims["exp"]})
    status, posted = call(member, design, "POST", anchor=anchors[0], body={
        "requestId": "role-comment", "anchor": anchors[0], "text": "역할별 확인 의견"})
    assert status == 201 and posted["comment"]["author"] == actor
    _, listed = call(ctx, design, "GET", anchor=anchors[0])
    assert listed["comments"] == [posted["comment"]]


def test_actual_react_screen_discussion_binds_verified_bytes_without_paid_work(pipeline):
    state = pipeline
    ready(state)
    outcome, _ = execute(state, "generate")
    assert outcome["status"] == "succeeded"
    design = state.ctx.get("design", state.design["id"])
    variant = design["variants"][0]
    artifact = state.ctx.get("wb_artifact", variant["artifactId"])
    result = read_object(state.ctx, artifact["result"])
    anchor = {"kind": "screen", "id": result["generated"]["screens"][0]["screenId"],
              **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")}}
    before = len(state.models), state.compiles, state.browsers, copy.deepcopy(design)
    _, posted = call(state.ctx, design, "POST", anchor=anchor, body={
        "requestId": "screen-discussion", "anchor": anchor, "text": "이 화면의 안내를 검토해주세요."})
    _, listed = call(state.ctx, design, "GET", anchor=anchor)
    assert listed["comments"] == [posted["comment"]]
    for key, value in [("sourceHash", "0" * 64), ("bundleHash", "0" * 64), ("id", "outside-screen")]:
        with pytest.raises(CollaborationError) as error:
            call(state.ctx, design, "POST", anchor=anchor, body={
                "requestId": "screen-invalid", "anchor": {**anchor, key: value}, "text": "다른 근거"})
        assert error.value.code == ("not-found" if key == "id" else "design-comment-stale")
    assert (len(state.models), state.compiles, state.browsers, state.ctx.get("design", design["id"])) == before
