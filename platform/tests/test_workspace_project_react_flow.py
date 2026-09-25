import os
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_collaboration import DRAFT
from test_workspace_project_http import make_api, request, shared
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
