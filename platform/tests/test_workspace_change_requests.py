import copy
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_workspace_worker import environment, request, upload
from workspace.change_requests import enforce_scope, normalize_request
from workspace.guidelines import normalize_refs, selected_pages, text_hash
from workspace.intake import extract_file
from workspace.prepare_sources import prepare_archive, source_record
from workspace.rules import contract_hash, state_coverage_issues, validate_contract


def change_request():
    return {"kind": "change", "channel": "synthetic app", "requester": "test planner", "dueDate": "2026-10-01",
            "baselineNote": "Existing synthetic application", "preserve": "Keep the standard sequence",
            "allowedFiles": [], "screens": [
                {"id": "entry", "title": "Entry", "kind": "page", "change": "modify", "states": ["entry", "error"],
                 "uiuxId": "UX-1", "developerId": "DEV-1", "canonicalId": "CANON-1"},
                {"id": "complete", "title": "Complete", "kind": "page", "change": "keep", "states": ["complete"]}],
            "transitions": [{"id": "next-step", "from": "entry", "to": "complete", "action": "Next",
                             "condition": "Valid input", "retention": "Keep amount"}]}


def contract():
    rules = []
    for sid, state in [("entry", "entry"), ("entry", "error"), ("complete", "complete")]:
        rules.append({"id": sid + "-" + state, "title": "Observe " + state, "required": True, "screenId": sid, "scenario": state,
                      "source": {"kind": "manual"}, "steps": [{"action": "expectVisible", "target": sid, "value": True}]})
    rules.append({"id": "next", "title": "Navigate", "transitionId": "next-step", "required": True, "source": {"kind": "manual"},
                  "steps": [{"action": "expectVisible", "target": "entry", "value": True},
                            {"action": "click", "target": "next"},
                            {"action": "expectVisible", "target": "complete", "value": True}]})
    return {"title": "Synthetic change", "brief": "Change only the approved scope", "assetIds": [],
            "viewport": {"width": 390, "height": 844}, "changeRequest": change_request(), "rules": rules, "unresolved": []}


def test_request_draft_persists_before_test_generation_but_cannot_be_approved():
    _, api, _ = environment()
    value = contract()
    value["rules"] = []
    status, created = request(api, "POST", "/contracts", value)
    assert status == 201, created
    saved = created["contract"]
    assert saved["changeRequest"]["screens"][0]["developerId"] == "DEV-1"
    assert request(api, "POST", f"/contracts/{saved['id']}/approve", {"version": saved["version"]})[0] == 409


def test_project_request_can_be_registered_before_product_publication_but_not_approved():
    _, api, _ = environment()
    _, created = request(api, "POST", "/projects", {"name": "Synthetic intake", "requestId": "intake-project"})
    project = created["project"]
    def scoped(method, path, body):
        response = api.handle({"rawPath": "/studio-api" + path, "headers": {"X-Workspace-Project": project["id"]},
            "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": {"sub": "designer", "token_use": "access"}}}},
            "body": json.dumps(body)})
        return response["statusCode"], json.loads(response["body"])
    status, saved = scoped("POST", "/contracts", {**contract(), "rules": []})
    assert status == 201, saved
    status, refused = scoped("POST", f"/contracts/{saved['contract']['id']}/approve", {"version": saved["contract"]["version"]})
    assert status == 409 and refused["code"] == "guideline-required"


def test_independent_screen_states_and_navigation_order_are_required():
    value = validate_contract(contract())
    assert state_coverage_issues(value) == []
    changed = copy.deepcopy(value)
    changed["rules"][1]["screenId"] = "complete"
    assert any("Entry" in issue and "error" in issue for issue in state_coverage_issues(changed))
    changed = copy.deepcopy(value)
    changed["rules"][-1]["steps"].reverse()
    assert any("next-step" in issue for issue in state_coverage_issues(changed))
    changed = copy.deepcopy(value)
    changed["rules"][-1]["required"] = False
    assert any("next-step" in issue for issue in state_coverage_issues(changed))


def test_mapping_schedule_and_scope_edits_invalidate_exact_approval():
    _, api, _ = environment()
    _, created = request(api, "POST", "/contracts", contract())
    saved = created["contract"]
    status, result = request(api, "POST", f"/contracts/{saved['id']}/approve", {"version": saved["version"]})
    assert status == 200, result
    approved = result["contract"]
    changed = copy.deepcopy(approved)
    changed["changeRequest"]["screens"][0]["developerId"] = "DEV-renamed"
    assert contract_hash(changed) != contract_hash(approved)
    status, edited = request(api, "PUT", f"/contracts/{approved['id']}", changed)
    assert status == 200 and edited["contract"]["status"] == "draft" and not edited["contract"].get("approval")


@pytest.mark.parametrize("patch", [
    {"dueDate": "2026-02-30"}, {"allowedFiles": ["../escape.tsx"]}, {"allowedFiles": ["src/logic/assets.ts"]},
    {"baseline": {"runId": "other", "round": True, "sourceHash": "a" * 64}},
    {"screens": [{"id": "bad/id", "title": "Bad", "kind": "page", "change": "add"}]},
    {"transitions": [{"id": "link", "from": "entry", "to": "unknown"}]},
])
def test_invalid_scope_is_rejected(patch):
    with pytest.raises(ValueError):
        normalize_request({**change_request(), **patch})


def test_screen_source_mapping_is_bound_to_the_selected_page_hashes():
    value = contract()
    ref = {"assetId": "source", "sourceId": "guide", "page": 1, "sourceSha256": "a" * 64, "textSha256": "b" * 64}
    value["assetIds"] = ["source"]
    value["guideRefs"] = [ref]
    value["changeRequest"]["screens"][0]["sourceRefs"] = [ref]
    normalized = validate_contract(value)
    assert normalized["changeRequest"]["screens"][0]["sourceRefs"] == [ref]
    changed = copy.deepcopy(value)
    changed["changeRequest"]["screens"][0]["sourceRefs"] = [{**ref, "textSha256": "c" * 64}]
    with pytest.raises(ValueError, match="원본·페이지·해시"):
        validate_contract(changed)
    assert contract_hash(value) != contract_hash({**value, "changeRequest": change_request()})


def test_scope_enforces_exact_unchanged_bytes_and_records_added_modified_deleted_files():
    before = {"src/App.tsx": "entry", "src/pages/keep.tsx": "unchanged", "src/pages/old.tsx": "old"}
    value = normalize_request({**change_request(), "baseline": {"runId": "run", "round": 1, "sourceHash": "a" * 64},
                               "allowedFiles": ["src/App.tsx", "src/pages/new.tsx", "src/pages/old.tsx"]})
    after = {"src/App.tsx": "changed", "src/pages/keep.tsx": "unchanged", "src/pages/new.tsx": "new"}
    changes = enforce_scope(value, before, after)
    assert {item["change"] for item in changes} == {"added", "modified", "deleted"}
    assert all("keep" not in item["path"] for item in changes)
    with pytest.raises(ValueError, match="범위 밖"):
        enforce_scope(value, before, {**after, "src/pages/keep.tsx": "unrelated edit"})


def test_model_cannot_drop_or_rewrite_request_scope():
    def model(*args):
        proposal = contract()
        proposal["changeRequest"]["screens"] = []
        return json.dumps(proposal), {}, {"modelId": "test"}
    store, api, worker = environment(model=model)
    value = {"assetIds": [], "brief": "Synthetic request", "changeRequest": change_request(),
             "model": "global.openai.gpt-6-astra", "requestId": "request-scope"}
    status, queued = request(api, "POST", "/contracts/propose", value)
    assert status == 202, queued
    assert worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})["status"] == "completed"
    job = store.get("designer", "job", queued["job"]["id"])
    saved = store.get("designer", "contract", job["result"]["contractId"])
    assert saved["changeRequest"] == normalize_request(change_request())
    assert request(api, "POST", "/contracts/propose", {**value, "changeRequest": {**change_request(), "preserve": "changed"}})[0] == 409


def test_baseline_cannot_be_read_from_another_storage_scope():
    _, api, _ = environment()
    value = contract()
    value["changeRequest"]["baseline"] = {"runId": "someone-elses-run", "round": 1, "sourceHash": "a" * 64}
    status, _ = request(api, "POST", "/contracts", value)
    assert status == 400


def source(status="완료"):
    return ('import {HxButton} from "@example/ui";\nexport const meta = `\nsid: UX-1\nstatus: ' + status +
            '\npver: V1\n`;\nexport default function Example(){return <HxButton/>;}').encode()


def test_source_txt_is_not_prose_and_deleted_source_cannot_enter_generation_context():
    analysis = extract_file("UX-1.txt", source("삭제"))
    pack = analysis["guidelinePack"]
    item = pack["sources"][0]
    assert item["category"] == "source" and item["metadata"]["sid"] == "UX-1"
    assert analysis["guidelineSources"][0]["reviewStatus"] == "unreviewed"
    assert analysis["guidelineSources"][0]["originalStatus"] == "stored"
    ref = {"assetId": "asset", "sourceId": item["id"], "page": 1, "sourceSha256": item["sha256"],
           "textSha256": text_hash(item["pages"][0]["text"])}
    with pytest.raises(ValueError, match="삭제"):
        selected_pages(pack, normalize_refs([ref], ["asset"]))


def test_source_hash_status_and_private_search_survive_intake():
    store, api, worker = environment()
    identifier = upload(api, worker, "UX-1.tsx", source(), "archive")
    saved = store.get("designer", "asset", identifier)
    assert store.get_blob(saved["originalKey"]) == source()
    assert saved["guidelineSources"][0]["metadata"]["status"] == "완료"
    assert saved["guidelineSources"][0]["reviewStatus"] == "unreviewed"
    status, value = request(api, "GET", f"/assets/{identifier}/guidelines")
    assert status == 200 and value["pages"][0]["category"] == "source"


def test_large_corpus_preparation_is_split_and_restartable_with_explicit_exclusions(tmp_path):
    path = tmp_path / "corpus.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(25):
            archive.writestr(f"pages/SCREEN-{index}.tsx", source())
        archive.writestr("protected.pptx", b"SCDSA004opaque")
        archive.writestr("image.png", b"not-parsed")
    first = prepare_archive(path, tmp_path / "packs")
    second = prepare_archive(path, tmp_path / "packs")
    assert first == second and len(first["packs"]) == 2
    assert {item["reason"] for item in first["excluded"]} == {"protected-original", "unsupported-format"}
    assert sum(len(json.loads((tmp_path / "packs" / name).read_text())["sources"]) for name in first["packs"]) == 25


def test_unsafe_zip_never_creates_a_reference_pack():
    for name in ("../escape.tsx", "/absolute.tsx"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(name, source())
        with pytest.raises(ValueError):
            extract_file("references.zip", output.getvalue())


def test_real_delta_generation_rejects_unrelated_file_edits_and_releases_exact_handoff():
    from workspace.react_runtime import evaluate_react
    from workspace.react_artifacts import generated_files, read_archive
    app = ('import {Screen,Text} from "@studio/approved-ui";'
           'export default function App(){return <Screen pageId="entry" testId="entry">'
           '<Text as="h1">Synthetic scope</Text></Screen>;}')
    untouched = 'export const value = "keep";'
    outputs = [{"src/App.tsx": app, "src/logic/keep.ts": untouched}]
    def model(*args):
        return json.dumps({"files": outputs.pop(0)}), {}, {"modelId": "test"}
    store, api, worker = environment(model=model)
    worker.react_call = evaluate_react
    base_contract = {"title": "Base", "rules": [{"id": "display", "title": "Display", "required": True,
                                              "source": {"kind": "manual"}, "steps": [{"action": "expectVisible", "target": "entry", "value": True}]}]}
    def run(value, request_id, rounds=1):
        status, created = request(api, "POST", "/contracts", value)
        assert status == 201, created
        saved = created["contract"]
        status, approved = request(api, "POST", f"/contracts/{saved['id']}/approve", {"version": saved["version"]})
        assert status == 200, approved
        status, queued = request(api, "POST", "/runs", {"contractId": saved["id"], "contractVersion": approved["contract"]["version"],
            "outputType": "react", "model": "global.openai.gpt-6-astra", "maxRounds": rounds, "requestId": request_id})
        assert status == 202, queued
        outcome = worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})
        assert outcome["status"] == "completed", store.get("designer", "job", queued["job"]["id"])
        return store.get("designer", "run", queued["run"]["id"])
    def approve(run):
        row = run["rounds"][-1]
        status, result = request(api, "POST", f"/runs/{run['id']}/approve", {"round": row["number"],
            "contractVersion": run["contractVersion"], **{key: row[key] for key in ("artifactSha256", "sourceHash", "bundleHash")}})
        assert status == 200, result
        return store.get("designer", "run", run["id"])
    base = approve(run(base_contract, "base-delta"))
    status, selected = request(api, "GET", f"/runs/{base['id']}/baseline")
    assert status == 200 and "src/logic/keep.ts" in selected["files"]
    delta = {**base_contract, "title": "Delta", "changeRequest": {**change_request(), "baseline": selected["baseline"],
             "allowedFiles": ["src/App.tsx"], "transitions": [], "screens": [
                 {"id": "entry", "title": "Entry", "kind": "page", "change": "modify", "states": ["entry"]}]},
             "rules": [{**base_contract["rules"][0], "screenId": "entry", "scenario": "entry"}]}
    next_app = app.replace("Synthetic scope", "Synthetic reviewed change")
    outputs.extend([{"src/App.tsx": next_app, "src/logic/keep.ts": 'export const value = "wrong";'},
                    {"src/App.tsx": next_app, "src/logic/keep.ts": untouched}])
    changed = run(delta, "changed-delta", 2)
    assert [row["passed"] for row in changed["rounds"]] == [False, True]
    assert changed["rounds"][0]["build"]["ok"] is False
    changed = approve(changed)
    row = changed["rounds"][-1]
    files = generated_files(read_archive(store.get_blob(row["sourceKey"]), row["sourceHash"]))
    assert files["src/logic/keep.ts"] == untouched
    assert [item["path"] for item in row["fileChanges"]] == ["src/App.tsx"]
    status, release = request(api, "POST", "/releases", {"runId": changed["id"], "round": 2, "requestId": "delta-release"})
    assert status == 202, release
    assert worker.handle({"owner": "designer", "jobId": release["job"]["id"]})["status"] == "completed"
    saved = store.get("designer", "release", release["release"]["id"])
    manifest = json.loads(store.get_blob(saved["manifestKey"]))
    assert manifest["handoff"]["changeRequest"]["baseline"] == selected["baseline"]
    assert manifest["handoff"]["frontendAcceptance"] == "not-recorded"
    assert all(item["path"] != "src/logic/keep.ts" for item in manifest["handoff"]["sourceChanges"])
    assert outputs == [], "release rebuild does not generate source"
