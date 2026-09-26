import os
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_collaboration import DRAFT
from test_workspace_git_export import repo
from test_workspace_project_http import make_api, request, shared
import workspace.http as http_module
from workspace.git_service import connection_hash
from workspace.react_runtime import evaluate_react
from workspace.worker import Worker

APP = """import {useState} from 'react';
import {Screen,Stack,Text,Button} from '@studio/approved-ui';
export default function App(){
 const [notice,setNotice]=useState(false);
 if(notice) return <Screen pageId="notice-consent" testId="notice-consent"><Stack>
 <Text as="h1">필수 동의</Text><Text>약관에 동의해야 합니다.</Text>
 <Button label="이전" onClick={()=>setNotice(false)}/></Stack></Screen>;
 return <Screen pageId="entry"><Stack><Text as="h1">정기 적금</Text>
 <Button testId="guide-open" label="안내 확인" onClick={()=>setNotice(true)}/></Stack></Screen>;
}"""


def test_planning_ontology_drives_a_real_page_and_changed_guidelines_block_release(monkeypatch):
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    calls = []

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        calls.append(user)
        assert "Published product ontology" in user
        assert DRAFT["notices"][0]["content"] in user
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}

    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": "project-react"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    owner = "project:" + project["id"]
    assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
    run = api.storage.get(owner, "run", data["run"]["id"])
    row = run["rounds"][0]
    assert row["passed"], row
    report = json.loads(api.storage.get_blob(row["reportKey"]))
    assert report["checks"][0]["steps"][-1]["actualComponent"] == "Screen"
    approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
    assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="dana", project=project["id"])[0] == 403
    assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
    status, release = request(api, "POST", "/releases", {"runId": run["id"], "round": 1, "requestId": "developer-release"},
                              actor="dana", project=project["id"])
    assert status == 202, release
    assert worker.handle({"owner": owner, "jobId": release["job"]["id"]})["status"] == "completed"
    assert api.storage.get(owner, "release", release["release"]["id"])["status"] == "ready"
    assert len(calls) == 1
    assert request(api, "GET", "/runs/" + run["id"], actor="dana", project=project["id"])[1]["run"]["needsRevalidation"] is False
    _, changed = request(api, "PUT", f"/products/{product['id']}", {"version": product["version"], "description": "변경된 기준"},
                          actor="bob", project=project["id"])
    assert request(api, "POST", f"/products/{product['id']}/publish", {"version": changed["product"]["version"]},
                   actor="bob", project=project["id"])[0] == 200
    assert request(api, "POST", "/releases", {"runId": run["id"], "round": 1, "requestId": "stale-release"},
                   actor="dana", project=project["id"])[0] == 409
    assert request(api, "GET", "/runs/" + run["id"], actor="dana", project=project["id"])[1]["run"]["needsRevalidation"] is True
    _, history = request(api, "GET", "/runs", actor="dana", project=project["id"])
    assert next(item for item in history["runs"] if item["id"] == run["id"])["needsRevalidation"] is True
    _, impact = request(api, "GET", f"/products/{product['id']}/impact", actor="dana", project=project["id"])
    assert any(item["id"] == run["id"] for item in impact["affectedRuns"])


def test_revoked_run_input_is_excluded_from_product_impact_like_a_missing_run():
    """Review 5 #6: `Collaboration._products` used to return affected-run metadata (id,
    status, guidelineId) straight from storage. Reproduced: republish the product (making
    a run affected/stale) and then revoke that run's contract input; `GET /runs/:id`
    correctly denies it (404), but the unfixed `/products/:id/impact` still listed the
    run's ID, status and guideline reference with 200. The gated route must treat the
    two identically: a run the caller cannot read contributes nothing to the response."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": "impact-revoke"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    run = data["run"]
    assert run.get("productId") == product["id"]
    owner = "project:" + project["id"]
    # Republish the product: the run is now affected/stale.
    _, changed = request(api, "PUT", f"/products/{product['id']}", {"version": product["version"], "description": "변경된 기준"},
                          actor="bob", project=project["id"])
    assert request(api, "POST", f"/products/{product['id']}/publish", {"version": changed["product"]["version"]},
                   actor="bob", project=project["id"])[0] == 200
    _, before = request(api, "GET", f"/products/{product['id']}/impact", actor="dana", project=project["id"])
    assert any(item["id"] == run["id"] for item in before["affectedRuns"])
    # Revoke the contract's own input asset (the guideline asset the rule cites).
    asset = api.storage.get(owner, "asset", publication["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", "/runs/" + run["id"], actor="dana", project=project["id"])[0] == 404
    _, after = request(api, "GET", f"/products/{product['id']}/impact", actor="dana", project=project["id"])
    assert not any(item["id"] == run["id"] for item in after["affectedRuns"])
    assert run["id"] not in json.dumps(after)


def _queued_run(api, project, request_id):
    """A queued (unexecuted) run with a genuine approved contract/product lineage --
    enough for the response gate's `run_access`, without running a round."""
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": request_id},
                           actor="carol", project=project["id"])
    assert status == 202, data
    return publication, data["run"]


def test_comment_anchored_to_a_run_is_gated_by_run_visibility():
    """Review 5 #6: `GET`/`POST /comments` are now gated routes; a run anchor must be
    authorized the same way `GET /runs/:id` is -- reading or writing a comment anchored
    to a run the caller can no longer read must fail exactly like a missing run,
    identically for the anchor filter (GET) and a new comment's anchor (POST)."""
    api = make_api()
    project = shared(api)
    publication, run = _queued_run(api, project, "comment-gate")
    body = {"requestId": "comment-1", "text": "질문 있습니다.", "anchor": {"runId": run["id"]}}
    status, data = request(api, "POST", "/comments", body, actor="dana", project=project["id"])
    assert status == 201, data
    comment = data["comment"]
    status, data = request(api, "GET", "/comments", actor="dana", project=project["id"], query={"runId": run["id"]})
    assert status == 200 and [row["id"] for row in data["comments"]] == [comment["id"]]
    owner = "project:" + project["id"]
    asset = api.storage.get(owner, "asset", publication["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", "/runs/" + run["id"], actor="dana", project=project["id"])[0] == 404
    assert request(api, "GET", "/comments", actor="dana", project=project["id"], query={"runId": run["id"]})[0] == 404
    assert request(api, "POST", "/comments", {**body, "requestId": "comment-2"}, actor="dana",
                   project=project["id"])[0] == 404


def test_page_comment_inherits_its_produced_rounds_authorization():
    """Review 6 #2: a comment anchored to {runId, pageId} without an explicit `round`
    only checked run-level visibility (any anchor could name any page), never the
    specific round that actually produced the page. Reproduced: a failed round's page
    stays visible via /comments to a planner (not a ROUND_EDITOR), and a planner can
    still post a new one, though the round's own download denies both roles alike."""
    api = make_api()
    project = shared(api)
    _, run = _queued_run(api, project, "page-comment-gate")
    owner = "project:" + project["id"]
    stored = api.storage.get(owner, "run", run["id"])
    # A completed run with one failed (unapproved) round that produced a page.
    updated = {**stored, "status": "completed",
              "rounds": [{"number": 1, "passed": False,
                         "pageSources": [{"pageId": "notice-consent", "path": "src/pages/notice.tsx"}]}]}
    api.storage.put(owner, "run", updated, stored["version"])
    body = {"requestId": "page-comment-1", "text": "이 안내 페이지 확인했습니다.",
            "anchor": {"runId": run["id"], "pageId": "notice-consent"}}
    # carol is a designer (a ROUND_EDITOR): a failed round's own page is still hers to discuss.
    status, data = request(api, "POST", "/comments", body, actor="carol", project=project["id"])
    assert status == 201, data
    comment = data["comment"]
    status, data = request(api, "GET", "/comments", actor="carol", project=project["id"])
    assert status == 200 and comment["id"] in {row["id"] for row in data["comments"]}
    # bob is a planner (not a ROUND_EDITOR): the failed round is denied exactly like its
    # own download would be, both reading the existing comment and posting a new one.
    status, data = request(api, "GET", "/comments", actor="bob", project=project["id"])
    assert status == 200 and comment["id"] not in {row["id"] for row in data["comments"]}
    status, data = request(api, "POST", "/comments", {**body, "requestId": "page-comment-2"}, actor="bob",
                           project=project["id"])
    assert status == 404, data


def test_batch_authorizes_its_pinned_contract_revision_not_the_current_one():
    """Review 6 #3: batch authorization checked whatever the contract currently is,
    ignoring the batch's own pinned revision (and the run references it exposes).
    Reproduced: after the batch pins revision 1 (explicitly bound to a reference
    asset, alongside the mandatory guideline asset), edit the contract to drop that
    reference asset from `assetIds` (a new unapproved revision 2, guideline
    unchanged), then revoke it. Each run the batch created (still bound to the OLD
    pinned revision 1 and its full input set) now denies GET individually; batch
    detail and listing must deny identically, not still disclose runIds/baselineRunId/
    slots for a pin whose bound input no longer authorizes."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    ref_bytes = b"abc"
    status, data = request(api, "POST", "/assets", {"name": "reference.txt", "size": len(ref_bytes),
                           "sha256": hashlib.sha256(ref_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    reference = data["asset"]
    original = api.storage.key_for(owner, "asset", reference["id"], "original")
    api.storage.put_blob(original, ref_bytes, "text/plain")
    reference = api.storage.put(owner, "asset", {**reference, "status": "stored", "uploadStatus": "stored",
                                "parseStatus": "complete", "originalKey": original,
                                "projectId": project["id"]}, expected_version=reference["version"])
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내",
                           "rules": [rule], "assetIds": [reference["id"]]}, actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    assert reference["id"] in contract["assetIds"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/batches", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "mode": "guided", "variationCount": 2, "requestId": "batch-pin-gate"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    batch, runs = data["batch"], data["runs"]
    assert batch["runIds"] and batch["baselineRunId"] in batch["runIds"] and len(runs) == len(batch["runIds"])
    status, data = request(api, "GET", f"/batches/{batch['id']}", actor="dana", project=project["id"])
    assert status == 200 and data["batch"]["runIds"] == batch["runIds"]
    # Edit the contract to drop the reference asset from `assetIds` (guideline is
    # unchanged, so this edit is allowed) -- a new, unapproved draft revision 2.
    status, data = request(api, "PUT", f"/contracts/{contract['id']}", {"version": contract["version"],
                           "assetIds": []}, actor="carol", project=project["id"])
    assert status == 200 and data["contract"]["status"] == "draft", data
    assert reference["id"] not in data["contract"]["assetIds"]
    api.storage.put(owner, "asset", {**reference, "accessRevoked": True}, reference["version"])
    for run in runs:
        assert request(api, "GET", "/runs/" + run["id"], actor="dana", project=project["id"])[0] == 404
    status, payload = request(api, "GET", f"/batches/{batch['id']}", actor="dana", project=project["id"])
    assert status == 404, payload
    status, payload = request(api, "GET", "/batches", actor="dana", project=project["id"])
    assert status == 200 and batch["id"] not in {row["id"] for row in payload["batches"]}, payload


def test_comment_listing_never_forwards_a_raw_storage_cursor():
    """Review 6 #4: the gate filtered each comment row but forwarded Collaboration's
    raw storage `list_page` cursor unchanged; once every comment on a scanned page
    belongs to a run that has since become inaccessible, the visible list is empty but
    the cursor still encoded that page's last (hidden) comment's own storage key.
    Reproduced with 101 comments anchored to a run that is later revoked (the storage
    page size is 100, so the raw cursor after the first page names the 100th comment)."""
    api = make_api()
    project = shared(api)
    publication, run = _queued_run(api, project, "comment-cursor-gate")
    for index in range(101):
        body = {"requestId": f"leak-{index}", "text": f"질문 {index}", "anchor": {"runId": run["id"]}}
        status, data = request(api, "POST", "/comments", body, actor="dana", project=project["id"])
        assert status == 201, data
    owner = "project:" + project["id"]
    asset = api.storage.get(owner, "asset", publication["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", "/runs/" + run["id"], actor="dana", project=project["id"])[0] == 404
    status, data = request(api, "GET", "/comments", actor="dana", project=project["id"])
    assert status == 200 and data["comments"] == [], data
    assert "cursor" not in data, data
    # When a real continuation IS needed (enough still-visible comments to exceed one
    # page), the cursor is the opaque `commentcur-<hex>` token, never a raw storage key.
    _, visible_run = _queued_run(api, project, "comment-cursor-visible")
    for index in range(55):
        body = {"requestId": f"visible-{index}", "text": f"보이는 질문 {index}", "anchor": {"runId": visible_run["id"]}}
        status, data = request(api, "POST", "/comments", body, actor="dana", project=project["id"])
        assert status == 201, data
    status, data = request(api, "GET", "/comments", actor="dana", project=project["id"],
                           query={"runId": visible_run["id"]})
    assert status == 200 and len(data["comments"]) <= 50 and "cursor" in data, data
    assert re.fullmatch(r"commentcur-[a-f0-9]{48}", data["cursor"]), data["cursor"]


def test_child_asset_creation_authorizes_its_parent():
    """Review 6 #5: `POST /assets` resolved `parentId` with a bare storage read (no
    source authorization at all), so a revoked parent's lineage (importRevision,
    lineageId) was still extended and its id disclosed as `parentId` in the 201
    response, even though the parent's own `GET /assets/:id` already denies it (404).
    Reproduced: revoke the parent, then create a child citing it."""
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]
    parent_bytes = b"parent original bytes"
    status, data = request(api, "POST", "/assets", {"name": "parent.txt", "size": len(parent_bytes),
                           "sha256": hashlib.sha256(parent_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    parent = data["asset"]
    original = api.storage.key_for(owner, "asset", parent["id"], "original")
    api.storage.put_blob(original, parent_bytes, "text/plain")
    parent = api.storage.put(owner, "asset", {**parent, "status": "stored", "uploadStatus": "stored",
                             "parseStatus": "complete", "originalKey": original, "projectId": project["id"]},
                             expected_version=parent["version"])
    child_bytes = b"child original bytes"
    status, data = request(api, "POST", "/assets", {"name": "child.txt", "size": len(child_bytes),
                           "sha256": hashlib.sha256(child_bytes).hexdigest(), "purpose": "reference",
                           "parentId": parent["id"]}, actor="carol", project=project["id"])
    assert status == 201 and data["asset"]["parentId"] == parent["id"] and data["asset"]["importRevision"] == 2, data
    api.storage.put(owner, "asset", {**parent, "accessRevoked": True}, parent["version"])
    assert request(api, "GET", f"/assets/{parent['id']}", actor="carol", project=project["id"])[0] == 404
    status, data = request(api, "POST", "/assets", {"name": "child2.txt", "size": len(child_bytes),
                           "sha256": hashlib.sha256(child_bytes).hexdigest(), "purpose": "reference",
                           "parentId": parent["id"]}, actor="carol", project=project["id"])
    assert status == 404, data
    # A genuinely missing parent gets the identical response.
    status, missing = request(api, "POST", "/assets", {"name": "child3.txt", "size": len(child_bytes),
                              "sha256": hashlib.sha256(child_bytes).hexdigest(), "purpose": "reference",
                              "parentId": "asset-absent"}, actor="carol", project=project["id"])
    assert (status, missing) == (404, data)


def test_new_comment_on_an_ambiguous_page_must_name_its_exact_round():
    """Review 7 #3 (round-6 #2, partial), creation half: a page produced by more than
    one round (e.g. a repair) is ambiguous -- the anchor must be rejected unless the
    caller names the exact round, rather than silently binding to whichever round
    happens to match first. An unambiguous page (produced by exactly one round) is
    still accepted and its round persisted automatically."""
    api = make_api()
    project = shared(api)
    _, run = _queued_run(api, project, "ambiguous-page-create")
    owner = "project:" + project["id"]
    stored = api.storage.get(owner, "run", run["id"])
    updated = {**stored, "status": "completed",
              "rounds": [{"number": 1, "passed": True, "blockingFindings": [],
                         "pageSources": [{"pageId": "shared-page", "path": "src/pages/notice.tsx"}]},
                        {"number": 2, "passed": True, "blockingFindings": [],
                         "pageSources": [{"pageId": "shared-page", "path": "src/pages/notice.tsx"}]}]}
    api.storage.put(owner, "run", updated, stored["version"])
    ambiguous = {"requestId": "ambiguous-1", "text": "이 페이지 어느 라운드죠?",
                "anchor": {"runId": run["id"], "pageId": "shared-page"}}
    status, data = request(api, "POST", "/comments", ambiguous, actor="carol", project=project["id"])
    assert status == 400, data
    status, data = request(api, "POST", "/comments", {**ambiguous, "anchor": {**ambiguous["anchor"], "round": 2}},
                           actor="carol", project=project["id"])
    assert status == 201 and data["comment"]["anchor"]["round"] == 2, data


def test_ambiguous_page_comment_requires_every_producing_round_authorized():
    """Review 7 #3 (round-6 #2, partial), read half: a page produced by more than
    one round is ambiguous. New comments now reject that anchor outright (see
    test_new_comment_on_an_ambiguous_page_must_name_its_exact_round), so this only
    protects a comment stored WITHOUT a persisted round (as one created before this
    fix would be): it must be visible only while EVERY round that could have
    produced the page is authorized, never just whichever one a naive "first match"
    happened to pick. Reproduced: round 1 stays reviewable (visible to anyone) while
    round 2 (sharing the same page) fails -- a planner (not a ROUND_EDITOR) must be
    denied, not shown round 1's still-valid permission for an anchor that could
    equally have meant round 2."""
    api = make_api()
    project = shared(api)
    _, run = _queued_run(api, project, "ambiguous-page-read")
    owner = "project:" + project["id"]
    stored = api.storage.get(owner, "run", run["id"])
    updated = {**stored, "status": "completed",
              "rounds": [{"number": 1, "passed": True, "blockingFindings": [],
                         "pageSources": [{"pageId": "shared-page", "path": "src/pages/notice.tsx"}]},
                        {"number": 2, "passed": False,
                         "pageSources": [{"pageId": "shared-page", "path": "src/pages/notice.tsx"}]}]}
    api.storage.put(owner, "run", updated, stored["version"])
    # A legacy comment with no persisted round at all (predates this fix).
    legacy = {"id": "c-legacy-ambiguous", "projectId": project["id"], "text": "레거시 댓글",
             "anchor": {"runId": run["id"], "productId": None, "guidelineId": None, "pageId": "shared-page"},
             "author": "carol", "status": "active", "requestHash": "0" * 64, "history": []}
    api.storage.put(owner, "comment", {key: value for key, value in legacy.items() if value is not None})
    status, data = request(api, "GET", "/comments", actor="carol", project=project["id"])
    assert status == 200 and any(row["id"] == "c-legacy-ambiguous" for row in data["comments"])
    status, data = request(api, "GET", "/comments", actor="bob", project=project["id"])
    assert status == 200 and not any(row["id"] == "c-legacy-ambiguous" for row in data["comments"]), data


def test_contract_creation_response_authorizes_its_own_exact_revision(monkeypatch):
    """Review 7 #4 / review 8 #4 (regression fix): stored-record views
    (`ResponseGate._one` for any kind in `_STORED`) must authorize whatever the
    CURRENT record now is before releasing anything -- generalizing round 5 #5's
    exact-revision fence (built only for publications) to contracts and every
    other stored-record kind with the same build-then-authorize-a-different-
    version shape. But an ordinary version bump that the CURRENT record's own
    authorization still allows (review 8 #4) must not be rejected outright --
    only a version bump whose CURRENT authorization genuinely fails may 404.
    Reproduced here: after POST /contracts builds its 201 response from
    revision 1 (bound to a reference asset), concurrently edit the contract
    (an unrelated field, a new revision 2 that still cites the same asset) and
    revoke that asset, before the gate's own final per-field authorization
    runs. Revision 2 also cites the now-revoked asset, so its own
    authorization genuinely fails too -- the stale revision 1 body must not be
    released with 201 in that case."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    ref_bytes = b"contract reference bytes"
    status, data = request(api, "POST", "/assets", {"name": "reference.txt", "size": len(ref_bytes),
                           "sha256": hashlib.sha256(ref_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    reference = data["asset"]
    original = api.storage.key_for(owner, "asset", reference["id"], "original")
    api.storage.put_blob(original, ref_bytes, "text/plain")
    reference = api.storage.put(owner, "asset", {**reference, "status": "stored", "uploadStatus": "stored",
                                "parseStatus": "complete", "originalKey": original, "projectId": project["id"]},
                                expected_version=reference["version"])
    original_one = http_module.ResponseGate._one
    armed = {"on": True}

    def racing(self, view, item):
        if armed["on"] and view == "contract" and isinstance(item, dict) and item.get("assetIds"):
            armed["on"] = False
            # Unrelated field edit: revision 2 still cites `reference`, so its
            # own authorization must fail identically once that asset is
            # revoked below -- this is the "genuinely rejected" case, not an
            # ordinary authorized progress bump.
            status, edited = request(api, "PUT", f"/contracts/{item['id']}",
                                     {"version": item["version"], "title": "상품 안내 (수정)"},
                                     actor="carol", project=project["id"])
            assert status == 200, edited
            api.storage.put(owner, "asset", {**reference, "accessRevoked": True}, reference["version"])
        return original_one(self, view, item)
    monkeypatch.setattr(http_module.ResponseGate, "_one", racing)
    rule = {"id": "R1", "title": "확인", "steps": [{"action": "expectVisible", "target": "next", "value": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내",
                           "rules": [rule], "assetIds": [reference["id"]]}, actor="carol", project=project["id"])
    assert status == 404, data
    assert not armed["on"]


def test_ordinary_worker_progress_never_404s_a_just_submitted_run(monkeypatch):
    """Review 8 #4 (regression from review 7 #4): `ResponseGate._one`'s exact-
    revision fence must reject only when the CURRENT record's own authorization
    would deny the content being returned -- not any version bump at all. A
    real worker can win the race and call `Storage.claim_job` (queued ->
    running) between the response body being built and the gate's final
    per-field authorization; that is ordinary, fully-authorized progress on
    the exact same job the caller is entitled to see, and POST /runs must
    still return 202 with the job's current ("running") state, never 404."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    owner = "project:" + project["id"]
    original_one = http_module.ResponseGate._one
    armed = {"on": True}

    def racing(self, view, item):
        if armed["on"] and view == "job" and isinstance(item, dict) and item.get("task") == "run":
            armed["on"] = False
            claimed = self.api.storage.claim_job(owner, item["id"])
            assert claimed is not None and claimed["status"] == "running"
        return original_one(self, view, item)
    monkeypatch.setattr(http_module.ResponseGate, "_one", racing)
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": "raced-run"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    assert not armed["on"]
    assert data["job"]["status"] == "running"
    run = api.storage.get(owner, "run", data["run"]["id"])
    assert run["status"] == "queued"
    assert request(api, "GET", f"/jobs/{data['job']['id']}", actor="carol", project=project["id"])[0] == 200


def test_ordinary_worker_progress_never_leaks_private_storage_keys(monkeypatch):
    """PR #33 review 1 #1: the review-8 priority fix's `item = authorized`
    substituted the RAW stored record for the response gate's stale-item
    replacement -- but `item` (from the already-serialized response body) had
    already gone through `_public()` inside `_json()`, while `authorized` (read
    straight from storage) never had, and `finish()` never re-filters. Reproduced:
    claim_job races POST /contracts/propose's job-view serialization -- the
    successful 202 leaked assetSnapshots[].originalKey, assetSnapshots[].
    analysisKey and requestHash, private fields an ordinary GET (or the
    non-raced response) never exposes."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    asset_bytes = b"propose source bytes"
    status, data = request(api, "POST", "/assets", {"name": "source.txt", "size": len(asset_bytes),
                           "sha256": hashlib.sha256(asset_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    asset = data["asset"]
    original = api.storage.key_for(owner, "asset", asset["id"], "original")
    analysis = api.storage.key_for(owner, "asset", asset["id"], "analysis.json")
    api.storage.put_blob(original, asset_bytes, "text/plain")
    api.storage.put_blob(analysis, json.dumps({"text": "propose source bytes", "parseStatus": "complete"}).encode(),
                         "application/json")
    asset = api.storage.put(owner, "asset", {**asset, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original, "analysisKey": analysis,
                            "projectId": project["id"]}, expected_version=asset["version"])
    original_one = http_module.ResponseGate._one
    armed = {"on": True}

    def racing(self, view, item):
        if armed["on"] and view == "job" and isinstance(item, dict) and item.get("task") == "propose":
            armed["on"] = False
            claimed = self.api.storage.claim_job(owner, item["id"])
            assert claimed is not None and claimed["status"] == "running"
        return original_one(self, view, item)
    monkeypatch.setattr(http_module.ResponseGate, "_one", racing)
    status, data = request(api, "POST", "/contracts/propose", {"productId": product["id"], "assetIds": [asset["id"]],
                           "brief": "요약", "requestId": "raced-propose"}, actor="carol", project=project["id"])
    assert status == 202, data
    assert not armed["on"]
    job = data["job"]
    assert job["status"] == "running"
    blob = json.dumps(job, ensure_ascii=False)
    assert "originalKey" not in blob and "analysisKey" not in blob and "requestHash" not in blob, job
    assert job.get("input", {}).get("assetSnapshots"), job


def test_contract_creation_never_leaks_a_revoked_assets_text_via_quote_matching():
    """Review 7 #1 / review 8 #2: an explicit rule's quoted source text was read
    from, and compared against, the cited asset's OWN analysis text before the
    response gate -- or anything else -- authorized that asset's current access
    at all. A revoked asset's own GET already 404s, but submitting a matching
    quote through contract creation behaved differently from a non-matching one
    (only the non-match failed immediately, with 400 invalid-contract; a match
    let creation proceed), a content oracle letting a caller learn restricted
    text one probe at a time. Both must now fail identically and immediately
    (404 not-found -- review 8 #2 folded the readiness check's 409 into the
    same missing-resource response a revoked asset gets everywhere else, so this
    is no longer a distinguishable "exists but revoked" signal either), before
    any quote is ever compared."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    secret_text = "기밀 원본 문장입니다."
    asset_bytes = secret_text.encode()
    status, data = request(api, "POST", "/assets", {"name": "secret.txt", "size": len(asset_bytes),
                           "sha256": hashlib.sha256(asset_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    asset = data["asset"]
    original = api.storage.key_for(owner, "asset", asset["id"], "original")
    analysis = api.storage.key_for(owner, "asset", asset["id"], "analysis.json")
    api.storage.put_blob(original, asset_bytes, "text/plain")
    api.storage.put_blob(analysis, json.dumps({"text": secret_text, "parseStatus": "complete"}).encode(),
                         "application/json")
    asset = api.storage.put(owner, "asset", {**asset, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original, "analysisKey": analysis,
                            "projectId": project["id"], "accessRevoked": True}, expected_version=asset["version"])
    assert request(api, "GET", f"/assets/{asset['id']}", actor="carol", project=project["id"])[0] == 404
    matching_rule = {"id": "R1", "title": "확인", "source": {"kind": "explicit", "assetId": asset["id"],
                     "quote": secret_text}, "steps": [{"action": "expectVisible", "target": "x", "value": True}]}
    status_match, payload_match = request(api, "POST", "/contracts", {"productId": product["id"], "title": "t",
                                          "rules": [matching_rule], "assetIds": [asset["id"]]},
                                          actor="carol", project=project["id"])
    nonmatching_rule = {**matching_rule, "source": {**matching_rule["source"], "quote": "이 문장은 존재하지 않습니다"}}
    status_nomatch, payload_nomatch = request(api, "POST", "/contracts", {"productId": product["id"], "title": "t",
                                              "rules": [nonmatching_rule], "assetIds": [asset["id"]]},
                                              actor="carol", project=project["id"])
    assert (status_match, payload_match.get("code")) == (status_nomatch, payload_nomatch.get("code"))
    assert status_match == 404 and payload_match.get("code") == "not-found", (status_match, payload_match)


def test_contract_validation_never_leaks_quote_matching_via_a_revocation_race(monkeypatch):
    """Review 8 #3 (still open after review 7 #1): `_assets` checked access once, up
    front, but retained no authorization observation and a later validation
    failure (`ValueError` -> 400 invalid-contract) raised straight past the
    response gate entirely -- so an asset revoked in the window between that
    initial check and `_asset_texts` reading its analysis for the quote
    comparison still produced a distinguishable code (400 for a non-matching
    quote) instead of the 404 a revoked asset must always get. Reproduced here
    by revoking the asset from inside `_asset_texts` itself (after `_assets`
    already passed it), then submitting a rule whose quote does not match: the
    fix re-authorizes every referenced asset again before ever reporting the
    400, so the race is closed and this returns 404 like every other revoked
    asset, never the content-revealing 400."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    secret_text = "기밀 원본 문장입니다."
    asset_bytes = secret_text.encode()
    status, data = request(api, "POST", "/assets", {"name": "secret.txt", "size": len(asset_bytes),
                           "sha256": hashlib.sha256(asset_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    asset = data["asset"]
    original = api.storage.key_for(owner, "asset", asset["id"], "original")
    analysis = api.storage.key_for(owner, "asset", asset["id"], "analysis.json")
    api.storage.put_blob(original, asset_bytes, "text/plain")
    api.storage.put_blob(analysis, json.dumps({"text": secret_text, "parseStatus": "complete"}).encode(),
                         "application/json")
    asset = api.storage.put(owner, "asset", {**asset, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original, "analysisKey": analysis,
                            "projectId": project["id"]}, expected_version=asset["version"])
    original_texts = http_module.WorkspaceAPI._asset_texts
    armed = {"on": True}

    def racing(self, owner, assets, guide_refs=None):
        texts = original_texts(self, owner, assets, guide_refs)
        if armed["on"]:
            armed["on"] = False
            current = self.storage.get(owner, "asset", asset["id"])
            self.storage.put(owner, "asset", {**current, "accessRevoked": True}, current["version"])
        return texts
    monkeypatch.setattr(http_module.WorkspaceAPI, "_asset_texts", racing)
    nonmatching_rule = {"id": "R1", "title": "확인", "source": {"kind": "explicit", "assetId": asset["id"],
                        "quote": "이 문장은 존재하지 않습니다"}, "steps": [{"action": "expectVisible", "target": "x", "value": True}]}
    status, payload = request(api, "POST", "/contracts", {"productId": product["id"], "title": "t",
                              "rules": [nonmatching_rule], "assetIds": [asset["id"]]},
                              actor="carol", project=project["id"])
    assert not armed["on"]
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_contract_validation_recheck_closes_a_race_between_two_assets_own_reads(monkeypatch):
    """PR #33 review 1 #2: review 8 #3's re-check re-authorized every referenced
    asset fresh, up front, in its OWN list comprehension -- but its readiness
    loop still reads each asset's bytes one at a time (`blob_info`); revoking
    the FIRST (quoted) asset while a LATER asset's blob is being read, in that
    SAME re-check pass, was still invisible to it, since the quoted asset's own
    authorization had already been captured before that later read even
    started. Reproduced with two referenced assets and a non-matching quote on
    the first: revoking it during the SECOND asset's `blob_info` call, inside
    the re-check pass itself (not the original pass), must still surface the
    same 404 as any other revoked asset -- closed by rechecking the gate's
    ENTIRE aggregate reader as one aggregate, strictly after every read this
    validation performs, not per-asset as each one is read."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]

    def stored_reference(name, text):
        data_bytes = text.encode()
        status, data = request(api, "POST", "/assets", {"name": name, "size": len(data_bytes),
                               "sha256": hashlib.sha256(data_bytes).hexdigest(), "purpose": "reference"},
                               actor="carol", project=project["id"])
        assert status == 201, data
        asset = data["asset"]
        original = api.storage.key_for(owner, "asset", asset["id"], "original")
        analysis = api.storage.key_for(owner, "asset", asset["id"], "analysis.json")
        api.storage.put_blob(original, data_bytes, "text/plain")
        api.storage.put_blob(analysis, json.dumps({"text": text, "parseStatus": "complete"}).encode(),
                             "application/json")
        saved = api.storage.put(owner, "asset", {**asset, "status": "stored", "uploadStatus": "stored",
                                "parseStatus": "complete", "originalKey": original, "analysisKey": analysis,
                                "projectId": project["id"]}, expected_version=asset["version"])
        return saved, original

    quoted, quoted_key = stored_reference("quoted.txt", "기밀 원본 문장입니다.")
    other, other_key = stored_reference("other.txt", "다른 참고 파일입니다.")
    from workspace.storage import Storage
    original_blob_info = Storage.blob_info
    calls = {"other": 0}

    def racing(self, key):
        if key == other_key:
            calls["other"] += 1
            if calls["other"] == 2:
                # The re-check pass's OWN readiness loop already re-authorized
                # (and already checked `quoted`'s own blob) before reaching
                # `other`'s -- revoke `quoted` right here, mid-pass, after it
                # already passed within this SAME re-check.
                current = self.get(owner, "asset", quoted["id"])
                self.put(owner, "asset", {**current, "accessRevoked": True}, current["version"])
        return original_blob_info(self, key)
    monkeypatch.setattr(Storage, "blob_info", racing)
    nonmatching_rule = {"id": "R1", "title": "확인", "source": {"kind": "explicit", "assetId": quoted["id"],
                        "quote": "이 문장은 존재하지 않습니다"}, "steps": [{"action": "expectVisible", "target": "x", "value": True}]}
    status, payload = request(api, "POST", "/contracts", {"productId": product["id"], "title": "t",
                              "rules": [nonmatching_rule], "assetIds": [quoted["id"], other["id"]]},
                              actor="carol", project=project["id"])
    assert calls["other"] == 2
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_revoked_base_run_404s_instead_of_a_refine_criteria_status(monkeypatch):
    """PR #33 review 3 #1: `baseRunId` was resolved with plain `_get()` --
    existence only, never authorized -- before its stale fields (outputType,
    contractId, contractVersion, visualPolicy) drove the refinement-
    compatibility check. A revoked base run's own GET already 404s, but
    naming the SAME run as `baseRunId` (matching contract/version, an
    explicit outputType mismatch) let the check read its still-stored react
    outputType and raise 409 refine-criteria-changed instead -- an existence
    oracle distinguishable from a missing/foreign baseRunId's 404. Authorize
    the base run (the same lineage check a plain GET's response gate
    applies) before any of its fields are read for that purpose."""
    api = make_api()
    project = shared(api)
    publication, run = _queued_run(api, project, "base-revoked")
    owner = "project:" + project["id"]
    asset = api.storage.get(owner, "asset", publication["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", "/runs/" + run["id"], actor="carol", project=project["id"])[0] == 404
    body = {"contractId": run["contractId"], "contractVersion": run["contractVersion"],
            "outputType": "html", "baseRunId": run["id"], "requestId": "refine-revoked"}
    status, payload = request(api, "POST", "/runs", body, actor="carol", project=project["id"])
    status_missing, payload_missing = request(api, "POST", "/runs",
                                               {**body, "baseRunId": "does-not-exist", "requestId": "refine-missing"},
                                               actor="carol", project=project["id"])
    assert (status, payload.get("code")) == (status_missing, payload_missing.get("code"))
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_propose_validation_never_leaks_a_guide_hash_match_via_a_revocation_race(monkeypatch):
    """PR #33 review 3 #2: `_propose`'s `validate_selection()` raised `ValueError`
    (a guide page whose textSha256 no longer matches) straight into the outer
    handler, skipping the response gate entirely -- review 8 #3 / review 1 #2
    only applied the aggregate-recheck-after-validation-failure pattern to
    `_validated_contract`, never to `_propose`'s own validation path.
    Reproduced: revoke the guide asset from inside `load_pack` itself (after
    `_assets` already passed it, during the SAME retrieval a matching hash
    would also need), then submit a mismatching textSha256: the fix rechecks
    the gate's aggregate once, strictly after that read, so this returns 404
    like any other revoked asset, never the content-revealing 400."""
    import workspace.guidelines as guidelines_module
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    page_text = "Keep entered values when returning to the form."
    pack = {"format": "ux-guidelines", "schemaVersion": 1, "sources": [
        {"id": "interaction", "name": "guide.pdf", "sha256": "a" * 64, "category": "interaction", "pageCount": 1,
         "pages": [{"page": 1, "text": page_text, "textSha256": guidelines_module.text_hash(page_text),
                    "truncated": False}]}]}
    pack_bytes = json.dumps(pack).encode()
    guide_bytes = b"guide reference bytes"
    status, data = request(api, "POST", "/assets", {"name": "guide.pdf", "size": len(guide_bytes),
                           "sha256": hashlib.sha256(guide_bytes).hexdigest(), "purpose": "guide"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    guide = data["asset"]
    original = api.storage.key_for(owner, "asset", guide["id"], "original")
    guidelines_key = api.storage.key_for(owner, "asset", guide["id"], "guidelines.json")
    api.storage.put_blob(original, guide_bytes, "text/plain")
    api.storage.put_blob(guidelines_key, pack_bytes, "application/json")
    guide = api.storage.put(owner, "asset", {**guide, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original, "guidelinesKey": guidelines_key,
                            "guidelinesSha256": guidelines_module.text_hash(pack_bytes.decode("utf-8")),
                            "projectId": project["id"]}, expected_version=guide["version"])
    original_load_pack = guidelines_module.load_pack
    armed = {"on": True}

    def racing(storage, owner_arg, asset):
        result = original_load_pack(storage, owner_arg, asset)
        if armed["on"]:
            armed["on"] = False
            current = api.storage.get(owner, "asset", guide["id"])
            api.storage.put(owner, "asset", {**current, "accessRevoked": True}, current["version"])
        return result
    monkeypatch.setattr(guidelines_module, "load_pack", racing)
    mismatched_ref = {"assetId": guide["id"], "sourceId": "interaction", "page": 1,
                      "sourceSha256": "a" * 64, "textSha256": hashlib.sha256(b"wrong text").hexdigest()}
    status, payload = request(api, "POST", "/contracts/propose", {"productId": product["id"],
                              "assetIds": [guide["id"]], "brief": "요약", "guideRefs": [mismatched_ref],
                              "requestId": "propose-revoked"}, actor="carol", project=project["id"])
    assert not armed["on"]
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_revoked_contract_404s_instead_of_a_criteria_changed_status():
    """PR #33 review 4: `_run_create`/`create_batch` resolved the supplied
    `contractId` with plain `_get()` -- existence only, never authorized --
    before its stale catalogHash/guidelineId/version fields drove the
    criteria-compatibility check. `GET /contracts/:id` on a contract whose own
    guideline asset was revoked already 404s, but naming that SAME contract
    in `POST /runs` or `POST /batches` returned 409 criteria-changed instead
    (reached via `_criteria`'s guidelineId comparison, using the stale
    contract read before authorization) -- an existence oracle distinguishable
    from a missing/foreign contractId's 404. Authorize the contract (the same
    lineage check a plain GET's response gate applies, closing over its
    referenced assets AND its change-request baseline) before any of its
    fields are read for that purpose, in both entry points."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    owner = "project:" + project["id"]
    assert request(api, "GET", f"/contracts/{contract['id']}", actor="carol", project=project["id"])[0] == 200
    # Republish the product (a new guideline) and revoke the OLD guideline
    # asset the approved contract actually cites -- the contract is now
    # genuinely inaccessible (not merely stale), like the "revoked run input"
    # tests' mechanism.
    _, changed = request(api, "PUT", f"/products/{product['id']}", {"version": product["version"], "description": "변경된 기준"},
                          actor="bob", project=project["id"])
    assert request(api, "POST", f"/products/{product['id']}/publish", {"version": changed["product"]["version"]},
                   actor="bob", project=project["id"])[0] == 200
    asset = api.storage.get(owner, "asset", publication["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", f"/contracts/{contract['id']}", actor="carol", project=project["id"])[0] == 404
    run_body = {"contractId": contract["id"], "contractVersion": contract["version"], "requestId": "run-revoked"}
    status, payload = request(api, "POST", "/runs", run_body, actor="carol", project=project["id"])
    status_missing, payload_missing = request(api, "POST", "/runs",
                                              {**run_body, "contractId": "does-not-exist", "requestId": "run-missing"},
                                              actor="carol", project=project["id"])
    assert (status, payload.get("code")) == (status_missing, payload_missing.get("code"))
    assert status == 404 and payload.get("code") == "not-found", (status, payload)
    batch_body = {"contractId": contract["id"], "contractVersion": contract["version"], "requestId": "batch-revoked"}
    status, payload = request(api, "POST", "/batches", batch_body, actor="carol", project=project["id"])
    status_missing, payload_missing = request(api, "POST", "/batches",
                                              {**batch_body, "contractId": "does-not-exist", "requestId": "batch-missing"},
                                              actor="carol", project=project["id"])
    assert (status, payload.get("code")) == (status_missing, payload_missing.get("code"))
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_change_request_baseline_404s_for_a_revoked_run_regardless_of_hash_match(monkeypatch):
    """PR #33 review 5: `baseline_project` compared the supplied source hash
    BEFORE ever authorizing the baseline run/contract, so neither
    `_validated_contract`'s nor `_propose`'s validation-error aggregate-
    recheck (review 1 #2 / review 3 #2) ever observed the baseline's own
    lineage. Reproduced with the baseline (product A's run) and the calling
    contract (product B, unaffected by the revocation below) kept separate,
    so this isolates the BASELINE's own lineage from the already-fixed
    current-guideline-asset oracle: after revoking product A's guideline
    asset (the base run's own lineage input) -- `GET /runs/:id` on it already
    404s -- submitting its CORRECT source hash in a NEW contract's
    (product B's) `changeRequest.baseline` still 404s (via the created
    contract's own final gate check on its `changeRequest.baseline`
    lineage), but an INCORRECT hash used to return 400 instead -- the
    validation-error path bypassing the gate entirely, never observing the
    baseline's lineage at all. Authorizing the baseline before any hash
    comparison closes this: both now 404 identically, in both
    `POST /contracts` and `/contracts/propose`."""
    from test_workspace_change_requests import change_request
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product_a = data["product"]
    _, publication_a = request(api, "POST", f"/products/{product_a['id']}/publish",
                               {"version": product_a["version"]}, actor="bob", project=project["id"])
    product_a = publication_a["product"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication_a["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product_a["id"], "title": "베이스", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": "baseline-source"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    owner = "project:" + project["id"]
    assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
    run = api.storage.get(owner, "run", data["run"]["id"])
    row = run["rounds"][0]
    assert row["passed"], row
    approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
    assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
    status, baseline_data = request(api, "GET", f"/runs/{run['id']}/baseline", actor="carol", project=project["id"])
    assert status == 200, baseline_data
    correct_baseline = baseline_data["baseline"]
    wrong_baseline = {**correct_baseline, "sourceHash": hashlib.sha256(b"wrong source").hexdigest()}
    # A second, independent product -- its own guideline is never touched by
    # the revocation below, isolating the baseline's OWN lineage check from
    # the already-fixed "current guideline asset revoked" oracle.
    _, data_b = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product_b = data_b["product"]
    _, publication_b = request(api, "POST", f"/products/{product_b['id']}/publish",
                               {"version": product_b["version"]}, actor="bob", project=project["id"])
    product_b = publication_b["product"]
    # Revoke the base run's own guideline asset -- its upstream lineage is now
    # genuinely inaccessible, like the other "revoked run input" tests.
    asset = api.storage.get(owner, "asset", publication_a["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", f"/runs/{run['id']}", actor="carol", project=project["id"])[0] == 404
    manual_rule = {"id": "R1", "title": "진행", "source": {"kind": "manual"},
                  "steps": [{"action": "expectVisible", "target": "next", "value": True}]}
    delta_body = {"productId": product_b["id"], "title": "델타", "rules": [manual_rule]}
    status, payload = request(api, "POST", "/contracts",
                              {**delta_body, "changeRequest": {**change_request(), "baseline": correct_baseline}},
                              actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)
    status, payload = request(api, "POST", "/contracts",
                              {**delta_body, "changeRequest": {**change_request(), "baseline": wrong_baseline}},
                              actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)
    status, payload = request(api, "POST", "/contracts/propose", {"productId": product_b["id"], "brief": "요약",
                              "changeRequest": {**change_request(), "baseline": correct_baseline},
                              "requestId": "propose-baseline"}, actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)
    status, payload = request(api, "POST", "/contracts/propose", {"productId": product_b["id"], "brief": "요약",
                              "changeRequest": {**change_request(), "baseline": wrong_baseline},
                              "requestId": "propose-baseline-wrong"}, actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_propose_baseline_recheck_closes_a_race_after_its_own_authorization(monkeypatch):
    """PR #33 review 6: round 5's fix authorized `baseline_project`'s run and
    contract before comparing hashes, but `_propose` called it OUTSIDE the
    try/except that wraps `validate_selection` and rechecks the gate's
    aggregate on failure -- so a revocation racing in AFTER the baseline's
    own successful authorization (but before its hash comparison raises)
    still escaped straight past `_propose` to the generic top-level
    `except ValueError`, bypassing the aggregate recheck entirely and
    returning the content-revealing 400 instead of 404. Reproduced: revoke
    the baseline run's own guideline asset exactly when `ResponseGate.
    authorize("run", ...)` succeeds for it (mid-`baseline_project`, after
    authorization, before the hash check), submitting a mismatching
    baseline hash so the hash comparison itself is what raises. Moving the
    `baseline_project` call inside the SAME guarded block as
    `validate_selection` closes this: the recheck now covers whatever the
    baseline's own successful authorization joined to the aggregate too."""
    from test_workspace_change_requests import change_request
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product_a = data["product"]
    _, publication_a = request(api, "POST", f"/products/{product_a['id']}/publish",
                               {"version": product_a["version"]}, actor="bob", project=project["id"])
    product_a = publication_a["product"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication_a["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product_a["id"], "title": "베이스", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": "baseline-source-2"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    owner = "project:" + project["id"]
    assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
    run = api.storage.get(owner, "run", data["run"]["id"])
    row = run["rounds"][0]
    assert row["passed"], row
    approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
    assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
    status, baseline_data = request(api, "GET", f"/runs/{run['id']}/baseline", actor="carol", project=project["id"])
    assert status == 200, baseline_data
    wrong_baseline = {**baseline_data["baseline"], "sourceHash": hashlib.sha256(b"wrong source, again").hexdigest()}
    _, data_b = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product_b = data_b["product"]
    _, publication_b = request(api, "POST", f"/products/{product_b['id']}/publish",
                               {"version": product_b["version"]}, actor="bob", project=project["id"])
    product_b = publication_b["product"]
    original_authorize = http_module.ResponseGate.authorize
    armed = {"on": True}

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        # Revoke only after BOTH the run's and the contract's own baseline
        # authorization have already succeeded (immediately after
        # `baseline_project`'s own authorization completes, matching the
        # review's exact repro) -- not mid-authorization, which would trip
        # the contract's OWN (already-fixed, review 5) authorization instead
        # of reaching the hash-mismatch ValueError this test targets.
        if (armed["on"] and view == "contract" and isinstance(record, dict)
                and record.get("id") == contract["id"] and result is not None):
            armed["on"] = False
            asset = self.api.storage.get(owner, "asset", publication_a["assetId"])
            self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status, payload = request(api, "POST", "/contracts/propose", {"productId": product_b["id"], "brief": "요약",
                              "changeRequest": {**change_request(), "baseline": wrong_baseline},
                              "requestId": "propose-baseline-race"}, actor="carol", project=project["id"])
    assert not armed["on"]
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_propose_source_reference_mismatch_recheck_is_not_exception_type_dependent(monkeypatch):
    """PR #33 review 7: round 6 caught only `ValueError` around `_propose`'s
    guarded validation block, but the `source-reference-mismatch` check
    inside it raises `HTTPError` directly -- a DIFFERENT exception type that
    the same `except ValueError:` never caught, bypassing the aggregate
    recheck entirely (the exact "enumerate exception types, miss one" trap
    the git_export.py receipt fix hit a few rounds earlier). Reproduced: a
    screen `sourceRef` absent from `guideRefs` (always triggers this 400,
    a purely structural/content check unrelated to authorization), with the
    baseline's own guideline asset revoked immediately after its own
    successful authorization and a MATCHING baseline hash (so
    `baseline_project` itself raises nothing -- only the sourceRefs check
    does). Must 404 identically to the already-fixed wrong-hash case,
    not the content-revealing 400. Fixed structurally: the guarded block now
    catches `(HTTPError, ValueError)` together and always rechecks the
    aggregate before reporting OR re-raising either one."""
    from test_workspace_change_requests import change_request
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product_a = data["product"]
    _, publication_a = request(api, "POST", f"/products/{product_a['id']}/publish",
                               {"version": product_a["version"]}, actor="bob", project=project["id"])
    product_a = publication_a["product"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication_a["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product_a["id"], "title": "베이스", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "maxRounds": 1, "requestId": "baseline-source-3"},
                           actor="carol", project=project["id"])
    assert status == 202, data
    owner = "project:" + project["id"]
    assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
    run = api.storage.get(owner, "run", data["run"]["id"])
    row = run["rounds"][0]
    assert row["passed"], row
    approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
    assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
    status, baseline_data = request(api, "GET", f"/runs/{run['id']}/baseline", actor="carol", project=project["id"])
    assert status == 200, baseline_data
    correct_baseline = baseline_data["baseline"]
    _, data_b = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product_b = data_b["product"]
    _, publication_b = request(api, "POST", f"/products/{product_b['id']}/publish",
                               {"version": product_b["version"]}, actor="bob", project=project["id"])
    product_b = publication_b["product"]
    original_authorize = http_module.ResponseGate.authorize
    armed = {"on": True}

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        if (armed["on"] and view == "contract" and isinstance(record, dict)
                and record.get("id") == contract["id"] and result is not None):
            armed["on"] = False
            asset = self.api.storage.get(owner, "asset", publication_a["assetId"])
            self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    change = {**change_request(), "baseline": correct_baseline}
    change["screens"] = [{**change["screens"][0], "sourceRefs": [{"assetId": "fake-guide-asset", "sourceId": "s1",
                          "page": 1, "sourceSha256": "a" * 64, "textSha256": "b" * 64}]}, change["screens"][1]]
    status, payload = request(api, "POST", "/contracts/propose", {"productId": product_b["id"], "brief": "요약",
                              "changeRequest": change, "requestId": "propose-source-ref-race"},
                              actor="carol", project=project["id"])
    assert not armed["on"]
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_existing_job_retry_authorizes_before_comparing_its_fingerprint():
    """PR #33 review 7: `_existing_job()` compared a retry's fingerprint
    against the STORED job's private input fingerprint BEFORE ever
    authorizing that job. A revoked job's own GET already 404s, and an
    EXACT retry (matching fingerprint) already 404ed too (via the response
    gate's own final check on the returned job), but retrying the SAME
    requestId with DIFFERENT content (a mismatched fingerprint) returned 409
    request-changed instead -- an existence oracle disclosing that a
    DIFFERENT-content job with this exact requestId still exists, even
    though the caller cannot read it. Authorize the job (the same lineage
    check its own GET's response gate applies) before ever comparing
    fingerprints."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    owner = "project:" + project["id"]
    guide_bytes = b"propose retry guide bytes"
    status, data = request(api, "POST", "/assets", {"name": "guide.txt", "size": len(guide_bytes),
                           "sha256": hashlib.sha256(guide_bytes).hexdigest(), "purpose": "guide"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    guide = data["asset"]
    original = api.storage.key_for(owner, "asset", guide["id"], "original")
    api.storage.put_blob(original, guide_bytes, "text/plain")
    guide = api.storage.put(owner, "asset", {**guide, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original,
                            "projectId": project["id"]}, expected_version=guide["version"])
    status, data = request(api, "POST", "/contracts/propose", {"productId": product["id"], "assetIds": [guide["id"]],
                           "brief": "첫 요청", "requestId": "propose-retry-target"}, actor="carol", project=project["id"])
    assert status == 202, data
    job_id = data["job"]["id"]
    asset = api.storage.get(owner, "asset", guide["id"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", f"/jobs/{job_id}", actor="carol", project=project["id"])[0] == 404
    status_exact, payload_exact = request(api, "POST", "/contracts/propose",
                                          {"productId": product["id"], "assetIds": [guide["id"]],
                                           "brief": "첫 요청", "requestId": "propose-retry-target"},
                                          actor="carol", project=project["id"])
    status_changed, payload_changed = request(api, "POST", "/contracts/propose",
                                              {"productId": product["id"], "assetIds": [guide["id"]],
                                               "brief": "다른 요청", "requestId": "propose-retry-target"},
                                              actor="carol", project=project["id"])
    assert (status_exact, payload_exact.get("code")) == (status_changed, payload_changed.get("code"))
    assert status_exact == 404 and payload_exact.get("code") == "not-found", (status_exact, payload_exact)


def test_batch_retry_authorizes_before_comparing_its_fingerprint():
    """PR #33 review 8: `create_batch()` had the SAME `_existing_job` shape,
    but for the batch record itself: `batch.get("requestHash") != fingerprint`
    compared before authorizing the batch. A revoked batch's own GET already
    404s, but retrying the same requestId with a DIFFERENT variationCount
    (a mismatched fingerprint) returned 409 request-changed instead -- an
    existence oracle. Authorize the batch (the same pinned-contract lineage
    check its own GET's response gate applies) before ever comparing
    fingerprints."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    ref_bytes = b"batch retry reference"
    status, data = request(api, "POST", "/assets", {"name": "reference.txt", "size": len(ref_bytes),
                           "sha256": hashlib.sha256(ref_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    reference = data["asset"]
    original = api.storage.key_for(owner, "asset", reference["id"], "original")
    api.storage.put_blob(original, ref_bytes, "text/plain")
    reference = api.storage.put(owner, "asset", {**reference, "status": "stored", "uploadStatus": "stored",
                                "parseStatus": "complete", "originalKey": original,
                                "projectId": project["id"]}, expected_version=reference["version"])
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내",
                           "rules": [rule], "assetIds": [reference["id"]]}, actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    batch_body = {"contractId": contract["id"], "contractVersion": contract["version"],
                  "mode": "guided", "variationCount": 2, "requestId": "batch-retry-fingerprint"}
    status, data = request(api, "POST", "/batches", batch_body, actor="carol", project=project["id"])
    assert status == 202, data
    batch_id = data["batch"]["id"]
    api.storage.put(owner, "asset", {**reference, "accessRevoked": True}, reference["version"])
    assert request(api, "GET", f"/batches/{batch_id}", actor="carol", project=project["id"])[0] == 404
    status_exact, payload_exact = request(api, "POST", "/batches", batch_body, actor="carol", project=project["id"])
    status_changed, payload_changed = request(api, "POST", "/batches",
                                              {**batch_body, "variationCount": 3},
                                              actor="carol", project=project["id"])
    assert (status_exact, payload_exact.get("code")) == (status_changed, payload_changed.get("code"))
    assert status_exact == 404 and payload_exact.get("code") == "not-found", (status_exact, payload_exact)


def test_retained_run_after_job_eviction_authorizes_before_checking_status():
    """PR #33 review 8: `_run_create`'s `retained_run` check (reached only
    when the OPERATIONAL job has already expired/been evicted, but the run
    record itself is still retained) inspected `retained_run.get("status")`
    BEFORE ever authorizing that run -- a separate check from the job-level
    one already fixed for `_existing_job`. A revoked retained run's own GET
    already 404s, but `POST /runs` with its original requestId returned 409
    request-expired instead -- an existence oracle. Authorize the retained
    run (the same lineage check its own GET's response gate applies) before
    ever inspecting its status."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    run_body = {"contractId": contract["id"], "contractVersion": contract["version"], "requestId": "run-ttl-test"}
    status, data = request(api, "POST", "/runs", run_body, actor="carol", project=project["id"])
    assert status == 202, data
    run_id, job_id = data["run"]["id"], data["job"]["id"]
    # The retained-run check only fires once the run has moved past "queued"
    # (a terminal outcome); force that directly, matching the review's own
    # "terminal-job TTL eviction" repro.
    run = api.storage.get(owner, "run", run_id)
    api.storage.put(owner, "run", {**run, "status": "completed"}, run["version"])
    table = api.storage.table()
    # Simulate DynamoDB TTL deleting only the operational job, retaining the run.
    job_key = next(key for key in table.items if key[1] == f"job#{job_id}")
    del table.items[job_key]
    asset = api.storage.get(owner, "asset", publication["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", f"/runs/{run_id}", actor="carol", project=project["id"])[0] == 404
    status, payload = request(api, "POST", "/runs", run_body, actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_release_retry_authorizes_before_comparing_its_fingerprint(monkeypatch):
    """PR #33 review 8: `create_release()` has the SAME `_existing_job` shape,
    but for the release record itself: `release.get("requestHash") !=
    fingerprint` compared before authorizing the release. A revoked release's
    own GET already 404s, but reusing the SAME requestId string to target a
    genuinely DIFFERENT run (the identifier is a pure hash of the caller's
    requestId string, not scoped to any particular run) -- a mismatched
    fingerprint -- returned 409 request-changed instead of 404. Authorize
    the release (the same round-delivery lineage check its own GET's
    response gate applies) before ever comparing fingerprints.

    Uses two FULLY ISOLATED products/contracts/runs (not one shared product)
    so that revoking product A's guideline asset leaves run B's own lineage
    genuinely intact -- otherwise `POST /releases`'s route-level
    `gate.round(runId, round)` pre-check (its `round="body"` fallback) would
    already deny run B before `create_release()` ever runs, masking whether
    this specific fix does anything."""
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)

    def approved_run(label, request_id):
        _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
        product = data["product"]
        _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                                  {"version": product["version"]}, actor="bob", project=project["id"])
        product = publication["product"]
        rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
                "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
            {"action": "click", "target": "guide-open"},
            {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
        status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": label, "rules": [rule]},
                               actor="carol", project=project["id"])
        assert status == 201, data
        contract = data["contract"]
        _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                          actor="carol", project=project["id"])
        contract = data["contract"]
        status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                               "outputType": "react", "maxRounds": 1, "requestId": request_id},
                               actor="carol", project=project["id"])
        assert status == 202, data
        assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
        run = api.storage.get(owner, "run", data["run"]["id"])
        row = run["rounds"][0]
        assert row["passed"], row
        approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                    "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
        assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
        return run, publication

    run_a, publication_a = approved_run("release retry A", "run-release-collide-a")
    run_b, publication_b = approved_run("release retry B", "run-release-collide-b")
    status, release = request(api, "POST", "/releases", {"runId": run_a["id"], "round": 1,
                              "requestId": "release-collide"}, actor="carol", project=project["id"])
    assert status == 202, release
    release_id = release["release"]["id"]
    asset = api.storage.get(owner, "asset", publication_a["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", f"/releases/{release_id}", actor="carol", project=project["id"])[0] == 404
    # Run B's own lineage (a completely separate product/guideline asset) is
    # untouched -- confirm it is genuinely reachable, so the 404 below can
    # only come from this fix, not from the unrelated route-level round gate.
    assert request(api, "GET", f"/runs/{run_b['id']}", actor="carol", project=project["id"])[0] == 200
    status, payload = request(api, "POST", "/releases", {"runId": run_b["id"], "round": 1,
                              "requestId": "release-collide"}, actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_git_export_retry_authorizes_before_comparing_its_fingerprint(monkeypatch, repo):
    """PR #33 review 8: `create_export()` has the SAME `_existing_job` shape,
    but for the export record itself: `exported.get("requestHash") !=
    fingerprint` compared before authorizing the export. A revoked export's
    own lineage (its release's round-delivery) already denies a plain GET
    of that release, but reusing the SAME requestId string to target a
    genuinely DIFFERENT, unaffected release (the identifier is a pure hash
    of the caller's requestId string, not scoped to any particular release)
    -- a mismatched fingerprint -- returned 409 request-changed instead of
    404. Authorize the export (the same round-delivery lineage its own
    authorization view applies) before ever comparing fingerprints.

    Uses two FULLY ISOLATED products/releases so revoking product A's
    guideline asset leaves release B's own lineage genuinely intact."""
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)
    bare, connection, base, _ = repo
    connection = {**connection, "visibility": "internal"}
    api.git_connections = worker.git_connections = lambda: {connection["id"]: connection}

    def ready_release(label, run_request_id, release_request_id):
        _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
        product = data["product"]
        _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                                  {"version": product["version"]}, actor="bob", project=project["id"])
        product = publication["product"]
        rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
                "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
            {"action": "click", "target": "guide-open"},
            {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
        status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": label, "rules": [rule]},
                               actor="carol", project=project["id"])
        assert status == 201, data
        contract = data["contract"]
        _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                          actor="carol", project=project["id"])
        contract = data["contract"]
        status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                               "outputType": "react", "maxRounds": 1, "requestId": run_request_id},
                               actor="carol", project=project["id"])
        assert status == 202, data
        assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
        run = api.storage.get(owner, "run", data["run"]["id"])
        row = run["rounds"][0]
        assert row["passed"], row
        approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                    "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
        assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
        status, prepared = request(api, "POST", "/releases", {"runId": run["id"], "round": 1,
                                   "requestId": release_request_id}, actor="carol", project=project["id"])
        assert status == 202, prepared
        assert worker.handle({"owner": owner, "jobId": prepared["job"]["id"]})["status"] == "completed"
        release = api.storage.get(owner, "release", prepared["release"]["id"])
        assert release["status"] == "ready", release
        return release, publication

    release_a, publication_a = ready_release("git export retry A", "run-git-collide-a", "release-git-collide-a")
    release_b, publication_b = ready_release("git export retry B", "run-git-collide-b", "release-git-collide-b")
    export_body = {"connectionId": connection["id"], "connectionHash": connection_hash(connection),
                   "requestId": "export-collide"}
    status, exported = request(api, "POST", f"/releases/{release_a['id']}/git", export_body,
                               actor="dana", project=project["id"])
    assert status == 202, exported
    asset = api.storage.get(owner, "asset", publication_a["assetId"])
    api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert request(api, "GET", f"/releases/{release_a['id']}", actor="carol", project=project["id"])[0] == 404
    # Release B's own lineage (a completely separate product/guideline asset)
    # is untouched -- confirm it is genuinely reachable, so the 404 below can
    # only come from this fix, not from an unrelated denial of release B itself.
    assert request(api, "GET", f"/releases/{release_b['id']}", actor="carol", project=project["id"])[0] == 200
    status, payload = request(api, "POST", f"/releases/{release_b['id']}/git", export_body,
                              actor="dana", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_batch_child_run_creation_authorizes_before_disclosing_its_fingerprint(monkeypatch):
    """PR #33 review 9 #1: create_batch() received `gate` but never passed it
    to `_run_create()` for each slot's own child run. Every gate-based
    authorization check inside `_run_create` (its own existing-job lookup,
    the retained-run status check, and the run's own Conflict-recovery
    check) silently no-ops for a batch's children, since `gate` defaults to
    None there. Reproduced by forcing a genuine content mismatch on the
    child job's stored input (simulating drift) and denying that SAME
    child run/job via a targeted authorize override (independent of any
    particular asset's lineage, so the batch's OWN already-fixed top-level
    check is unaffected): its own GET already 404s, but pre-fix, batch
    retry exposed the un-authorized comparison's content-dependent
    "request-changed" in a successful 202 instead of the same "not-found"
    a GET would show."""
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "배치 자식", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    batch_body = {"contractId": contract["id"], "contractVersion": contract["version"],
                  "mode": "guided", "variationCount": 2, "requestId": "batch-child-gate"}
    status, data = request(api, "POST", "/batches", batch_body, actor="carol", project=project["id"])
    assert status == 202, data
    slot0 = next(s for s in data["batch"]["slots"] if s["index"] == 0)
    child_run_id, child_job_id = slot0["runId"], slot0["jobId"]
    job = api.storage.get(owner, "job", child_job_id)
    api.storage.put(owner, "job", {**job, "requestHash": "0" * 64}, job["version"])
    # Clear only slot 0's recorded runId so the retry's loop re-attempts THIS
    # slot's `_run_create()` call again (a slot with a recorded runId is
    # skipped entirely) -- the underlying job/run records themselves are left
    # in place, matching the review's "an inaccessible job occupying the
    # derived child request ID" repro.
    stored_batch = api.storage.get(owner, "batch", data["batch"]["id"])
    cleared_slots = [{**slot, "runId": None} if slot["index"] == 0 else slot for slot in stored_batch["slots"]]
    api.storage.put(owner, "batch", {**stored_batch, "slots": cleared_slots,
                    "runIds": [s["runId"] for s in cleared_slots if s.get("runId")]}, stored_batch["version"])
    original_authorize = http_module.ResponseGate.authorize

    def denying(self, view, record):
        if view in ("run", "job") and isinstance(record, dict) and record.get("id") in (child_run_id, child_job_id):
            return None
        return original_authorize(self, view, record)
    monkeypatch.setattr(http_module.ResponseGate, "authorize", denying)
    assert request(api, "GET", f"/runs/{child_run_id}", actor="carol", project=project["id"])[0] == 404
    status, retry = request(api, "POST", "/batches", batch_body, actor="carol", project=project["id"])
    assert status == 202, retry
    slot = next(s for s in retry["batch"]["slots"] if s["index"] == 0)
    assert slot.get("errorCode") == "not-found", slot


def test_run_creation_conflict_race_authorizes_before_comparing_its_fingerprint(monkeypatch):
    """PR #33 review 9 #2: `_run_create`'s OWN Conflict-recovery block
    (reached when `storage.put(owner, "run", run_data)` itself raises --
    another request created a run with this same identifier) compared
    `run.get("contractHash")`/fingerprint against the raced-in run BEFORE
    ever authorizing it -- a SEPARATE checkpoint from the retained-run
    status check fixed in review 8. Reproduced: seed a run at the exact
    identifier a fresh POST /runs would derive, with different (mismatched)
    content, then deny that SAME run via a targeted authorize override
    (independent of any particular asset's lineage): its own GET already
    404s, but pre-fix, POST /runs with the colliding requestId exposed the
    un-authorized comparison's "request-changed" 409 instead of the same
    404 a GET would show."""
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "런 충돌", "rules": [rule]},
                           actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    run_body = {"contractId": contract["id"], "contractVersion": contract["version"], "requestId": "run-conflict-race"}
    target_identifier = http_module.WorkspaceAPI._request_id(run_body, "run")
    # Build a genuinely valid run record (a normal, separate creation), then
    # relocate it to the TARGET identifier with different content, simulating
    # another request having concurrently created a run there with a
    # different instruction -- a real Conflict on the write below.
    status, other = request(api, "POST", "/runs", {**run_body, "requestId": "run-conflict-template",
                            "instruction": "다른 지시문"}, actor="carol", project=project["id"])
    assert status == 202, other
    template = api.storage.get(owner, "run", other["run"]["id"])
    api.storage.put(owner, "run", {**template, "id": target_identifier}, expected_version=None)
    original_authorize = http_module.ResponseGate.authorize
    calls = {"n": 0}

    def racing(self, view, record):
        if view == "run" and isinstance(record, dict) and record.get("id") == target_identifier:
            calls["n"] += 1
            if calls["n"] == 1:
                # Let the retained-run check's own (already-fixed, review 8)
                # authorization succeed normally -- this test targets the
                # SEPARATE Conflict-recovery block reached afterward, once
                # `storage.put()` below raises Conflict. Every authorize call
                # for this SAME run from that point on (a race denying it
                # between the retained-run check and this write) is denied,
                # matching what a plain GET performed at that same moment
                # would show.
                return original_authorize(self, view, record)
            return None
        return original_authorize(self, view, record)
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status, payload = request(api, "POST", "/runs", run_body, actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)
    assert calls["n"] >= 2, "the race must reach the Conflict-recovery block's own second authorize call"


def test_propose_retry_recheck_closes_a_race_after_existing_jobs_authorization(monkeypatch):
    """PR #33 review 9 #3: `_existing_job()`'s own comparison branch
    (`job.get("requestHash") != fingerprint`) raised its content-dependent
    409 directly, with no recheck of the SAME aggregate its own successful
    authorize() call just joined. A revocation racing in AFTER that
    authorize() succeeds (but before this comparison raises) is never
    caught: an HTTPError raised here skips straight past `gate.finish()`'s
    final aggregate recheck entirely (that recheck only runs for a 2xx
    response), so a MISMATCHED retry brief disclosed 409 request-changed
    while the EXACT brief -- which reaches `finish()`'s own recheck through
    a completed 202 -- 404ed on the identical race. Both must produce the
    same denial regardless of which branch fingerprint comparison took."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    owner = "project:" + project["id"]
    guide_bytes = b"propose retry-recheck guide bytes"
    status, data = request(api, "POST", "/assets", {"name": "guide.txt", "size": len(guide_bytes),
                           "sha256": hashlib.sha256(guide_bytes).hexdigest(), "purpose": "guide"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    guide = data["asset"]
    original = api.storage.key_for(owner, "asset", guide["id"], "original")
    api.storage.put_blob(original, guide_bytes, "text/plain")
    guide = api.storage.put(owner, "asset", {**guide, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original,
                            "projectId": project["id"]}, expected_version=guide["version"])
    status, data = request(api, "POST", "/contracts/propose", {"productId": product["id"], "assetIds": [guide["id"]],
                           "brief": "첫 요청", "requestId": "propose-retry-recheck"}, actor="carol", project=project["id"])
    assert status == 202, data
    job_id = data["job"]["id"]
    original_authorize = http_module.ResponseGate.authorize
    calls = {"n": 0}

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        if view == "job" and isinstance(record, dict) and record.get("id") == job_id:
            calls["n"] += 1
            if calls["n"] == 1 and result is not None:
                # `_existing_job`'s own authorize() succeeds normally here (the
                # job is genuinely still accessible); revoke immediately
                # afterward so its LATER fingerprint comparison -- reached
                # right after, in the SAME call -- is what must be caught by
                # this fix's own recheck, not the authorize() call itself.
                asset = self.api.storage.get(owner, "asset", guide["id"])
                self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status_changed, payload_changed = request(api, "POST", "/contracts/propose",
                                              {"productId": product["id"], "assetIds": [guide["id"]],
                                               "brief": "다른 요청", "requestId": "propose-retry-recheck"},
                                              actor="carol", project=project["id"])
    assert status_changed == 404 and payload_changed.get("code") == "not-found", (status_changed, payload_changed)
    assert calls["n"] >= 1


def test_propose_exact_retry_recheck_normalizes_finish_s_final_check(monkeypatch):
    """PR #33 review 10 #1: review 9's fix normalized ONLY the MISMATCH
    branch inside `_existing_job` (an explicit recheck-before-raise). The
    EXACT-match branch never raises there -- it proceeds to a 202, which
    reaches `gate.finish()`'s own success-path recheck instead
    (`ResponseGate.recheck()` -> `self.aggregate.recheck()`). That call let
    a raw `CollaborationError` (e.g. "ontology-source-changed") propagate
    UNCAUGHT, unlike `ResponseGate.authorize()` which explicitly catches
    and normalizes the same class of failure to 404. A revocation racing
    in at the identical moment therefore disclosed 409 on the exact-match
    retry while the mismatched retry (fixed in review 9) correctly 404ed
    on the SAME race -- a NEW asymmetry replacing the old one. Fixed by
    making `ResponseGate.recheck()` itself perform the SAME normalization
    `authorize()` already does."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    owner = "project:" + project["id"]
    guide_bytes = b"propose exact-retry recheck guide bytes"
    status, data = request(api, "POST", "/assets", {"name": "guide.txt", "size": len(guide_bytes),
                           "sha256": hashlib.sha256(guide_bytes).hexdigest(), "purpose": "guide"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    guide = data["asset"]
    original = api.storage.key_for(owner, "asset", guide["id"], "original")
    api.storage.put_blob(original, guide_bytes, "text/plain")
    guide = api.storage.put(owner, "asset", {**guide, "status": "stored", "uploadStatus": "stored",
                            "parseStatus": "complete", "originalKey": original,
                            "projectId": project["id"]}, expected_version=guide["version"])
    status, data = request(api, "POST", "/contracts/propose", {"productId": product["id"], "assetIds": [guide["id"]],
                           "brief": "첫 요청", "requestId": "propose-exact-retry-recheck"}, actor="carol", project=project["id"])
    assert status == 202, data
    job_id = data["job"]["id"]
    original_authorize = http_module.ResponseGate.authorize
    calls = {"n": 0}

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        if view == "job" and isinstance(record, dict) and record.get("id") == job_id:
            calls["n"] += 1
            if calls["n"] == 1 and result is not None:
                # `_existing_job`'s own authorize() succeeds normally here (the
                # job is genuinely still accessible); revoke immediately
                # afterward. Since this retry's brief EXACTLY matches, no
                # mismatch raise fires -- this request proceeds to a 202,
                # reaching `gate.finish()`'s OWN final recheck instead.
                asset = self.api.storage.get(owner, "asset", guide["id"])
                self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status_exact, payload_exact = request(api, "POST", "/contracts/propose",
                                          {"productId": product["id"], "assetIds": [guide["id"]],
                                           "brief": "첫 요청", "requestId": "propose-exact-retry-recheck"},
                                          actor="carol", project=project["id"])
    assert status_exact == 404 and payload_exact.get("code") == "not-found", (status_exact, payload_exact)
    assert calls["n"] >= 1


def test_batch_retry_recheck_closes_a_race_after_its_own_authorization(monkeypatch):
    """PR #33 review 10 #2: `create_batch()`'s own mismatch raise
    (`batch.get("requestHash") != fingerprint`) authorized the batch first
    (review 8), but never rechecked retained authority before disclosing
    the comparison itself -- a revocation racing in AFTER that authorize()
    succeeds (but before this comparison raises) still discloses the
    content-dependent 409 instead of 404. Fixed by routing this raise
    through the gate's own normalized final recheck (review 10's core fix
    to `ResponseGate.recheck()`)."""
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
    product = data["product"]
    _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                              {"version": product["version"]}, actor="bob", project=project["id"])
    owner = "project:" + project["id"]
    ref_bytes = b"batch recheck race reference"
    status, data = request(api, "POST", "/assets", {"name": "reference.txt", "size": len(ref_bytes),
                           "sha256": hashlib.sha256(ref_bytes).hexdigest(), "purpose": "reference"},
                           actor="carol", project=project["id"])
    assert status == 201, data
    reference = data["asset"]
    original = api.storage.key_for(owner, "asset", reference["id"], "original")
    api.storage.put_blob(original, ref_bytes, "text/plain")
    reference = api.storage.put(owner, "asset", {**reference, "status": "stored", "uploadStatus": "stored",
                                "parseStatus": "complete", "originalKey": original,
                                "projectId": project["id"]}, expected_version=reference["version"])
    rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
            "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
        {"action": "click", "target": "guide-open"},
        {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
    status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": "상품 안내",
                           "rules": [rule], "assetIds": [reference["id"]]}, actor="carol", project=project["id"])
    assert status == 201, data
    contract = data["contract"]
    _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                      actor="carol", project=project["id"])
    contract = data["contract"]
    batch_body = {"contractId": contract["id"], "contractVersion": contract["version"],
                  "mode": "guided", "variationCount": 2, "requestId": "batch-recheck-race"}
    status, data = request(api, "POST", "/batches", batch_body, actor="carol", project=project["id"])
    assert status == 202, data
    batch_id = data["batch"]["id"]
    original_authorize = http_module.ResponseGate.authorize

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        if view == "batch" and isinstance(record, dict) and record.get("id") == batch_id and result is not None:
            # The batch's own authorize() succeeds normally here (still
            # genuinely accessible); revoke immediately afterward so the
            # LATER fingerprint comparison -- reached moments later, in the
            # SAME call -- is exactly where the race lands.
            asset = self.api.storage.get(owner, "asset", reference["id"])
            self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status, payload = request(api, "POST", "/batches", {**batch_body, "variationCount": 3},
                              actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_release_retry_recheck_closes_a_race_after_its_own_authorization(monkeypatch):
    """PR #33 review 10 #2: `create_release()`'s own mismatch raise had the
    SAME gap as the batch one above. Uses two fully ISOLATED products/runs
    (as in review 9's release test) so retrying the SAME requestId against
    a genuinely DIFFERENT run produces a real content mismatch, while
    timing the revocation to land immediately after release A's own
    (already-fixed, authorize-before-compare) checkpoint succeeds -- fixed
    the same way as the batch test above."""
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)

    def approved_run(label, request_id):
        _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
        product = data["product"]
        _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                                  {"version": product["version"]}, actor="bob", project=project["id"])
        rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
                "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
            {"action": "click", "target": "guide-open"},
            {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
        status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": label, "rules": [rule]},
                               actor="carol", project=project["id"])
        assert status == 201, data
        contract = data["contract"]
        _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                          actor="carol", project=project["id"])
        contract = data["contract"]
        status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                               "outputType": "react", "maxRounds": 1, "requestId": request_id},
                               actor="carol", project=project["id"])
        assert status == 202, data
        assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
        run = api.storage.get(owner, "run", data["run"]["id"])
        row = run["rounds"][0]
        assert row["passed"], row
        approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                    "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
        assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
        return run, publication

    run_a, publication_a = approved_run("release recheck A", "run-release-recheck-a")
    run_b, publication_b = approved_run("release recheck B", "run-release-recheck-b")
    status, release = request(api, "POST", "/releases", {"runId": run_a["id"], "round": 1,
                              "requestId": "release-recheck-race"}, actor="carol", project=project["id"])
    assert status == 202, release
    release_a_id = release["release"]["id"]
    original_authorize = http_module.ResponseGate.authorize

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        if view == "release" and isinstance(record, dict) and record.get("id") == release_a_id and result is not None:
            # Release A's own authorize() (checking run A's round-delivery
            # lineage) succeeds normally here; revoke run A's own guideline
            # asset immediately afterward so the LATER fingerprint
            # comparison against release B's colliding retry -- reached
            # moments later, in the SAME call -- is exactly where the race
            # lands. Run B's own lineage is untouched.
            asset = self.api.storage.get(owner, "asset", publication_a["assetId"])
            self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status, payload = request(api, "POST", "/releases", {"runId": run_b["id"], "round": 1,
                              "requestId": "release-recheck-race"}, actor="carol", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)


def test_git_export_retry_recheck_closes_a_race_after_its_own_authorization(monkeypatch, repo):
    """PR #33 review 10 #2: `create_export()`'s own mismatch raise had the
    SAME gap as the batch/release ones above -- fixed the same way. Uses
    two fully isolated ready releases (as in review 8's git-export test)
    so retrying the SAME requestId against a genuinely different release
    produces a real content mismatch, while timing the revocation to land
    immediately after export A's own (already-fixed) checkpoint succeeds."""
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    api = make_api()
    project = shared(api)
    owner = "project:" + project["id"]

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": {"src/App.tsx": APP}}), {}, {"modelId": model_id}
    worker = Worker(storage=api.storage, model_call=model, react_call=evaluate_react)
    bare, connection, base, _ = repo
    connection = {**connection, "visibility": "internal"}
    api.git_connections = worker.git_connections = lambda: {connection["id"]: connection}

    def ready_release(label, run_request_id, release_request_id):
        _, data = request(api, "POST", "/products", DRAFT, actor="bob", project=project["id"])
        product = data["product"]
        _, publication = request(api, "POST", f"/products/{product['id']}/publish",
                                  {"version": product["version"]}, actor="bob", project=project["id"])
        product = publication["product"]
        rule = {"id": "Notice", "title": "필수 안내 페이지 확인", "source": {"kind": "explicit",
                "assetId": publication["assetId"], "quote": DRAFT["notices"][0]["content"]}, "steps": [
            {"action": "click", "target": "guide-open"},
            {"action": "expectText", "target": "notice-consent", "value": DRAFT["notices"][0]["content"], "normalizeWhitespace": True}]}
        status, data = request(api, "POST", "/contracts", {"productId": product["id"], "title": label, "rules": [rule]},
                               actor="carol", project=project["id"])
        assert status == 201, data
        contract = data["contract"]
        _, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                          actor="carol", project=project["id"])
        contract = data["contract"]
        status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                               "outputType": "react", "maxRounds": 1, "requestId": run_request_id},
                               actor="carol", project=project["id"])
        assert status == 202, data
        assert worker.handle({"owner": owner, "jobId": data["job"]["id"]})["status"] == "completed"
        run = api.storage.get(owner, "run", data["run"]["id"])
        row = run["rounds"][0]
        assert row["passed"], row
        approval = {"round": 1, "artifactSha256": row["artifactSha256"], "sourceHash": row["sourceHash"],
                    "bundleHash": row["bundleHash"], "contractVersion": run["contractVersion"]}
        assert request(api, "POST", f"/runs/{run['id']}/approve", approval, actor="carol", project=project["id"])[0] == 200
        status, prepared = request(api, "POST", "/releases", {"runId": run["id"], "round": 1,
                                   "requestId": release_request_id}, actor="carol", project=project["id"])
        assert status == 202, prepared
        assert worker.handle({"owner": owner, "jobId": prepared["job"]["id"]})["status"] == "completed"
        release = api.storage.get(owner, "release", prepared["release"]["id"])
        assert release["status"] == "ready", release
        return release, publication

    release_a, publication_a = ready_release("git recheck race A", "run-git-recheck-a", "release-git-recheck-a")
    release_b, publication_b = ready_release("git recheck race B", "run-git-recheck-b", "release-git-recheck-b")
    export_body = {"connectionId": connection["id"], "connectionHash": connection_hash(connection),
                   "requestId": "export-recheck-race"}
    status, exported = request(api, "POST", f"/releases/{release_a['id']}/git", export_body,
                               actor="dana", project=project["id"])
    assert status == 202, exported
    export_a_id = exported["export"]["id"]
    original_authorize = http_module.ResponseGate.authorize

    def racing(self, view, record):
        result = original_authorize(self, view, record)
        if view == "export" and isinstance(record, dict) and record.get("id") == export_a_id and result is not None:
            # Export A's own authorize() (checking release A's round-delivery
            # lineage) succeeds normally here; revoke immediately afterward
            # so the LATER fingerprint comparison against release B's
            # colliding retry -- reached moments later, in the SAME call --
            # is exactly where the race lands. Release B's own lineage is
            # untouched.
            asset = self.api.storage.get(owner, "asset", publication_a["assetId"])
            self.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
        return result
    monkeypatch.setattr(http_module.ResponseGate, "authorize", racing)
    status, payload = request(api, "POST", f"/releases/{release_b['id']}/git", export_body,
                              actor="dana", project=project["id"])
    assert status == 404 and payload.get("code") == "not-found", (status, payload)
