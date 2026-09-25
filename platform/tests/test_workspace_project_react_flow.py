import os
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_collaboration import DRAFT
from test_workspace_project_http import make_api, request, shared
import workspace.http as http_module
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
