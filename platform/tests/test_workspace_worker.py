import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_workspace_browser import HTML, contract as browser_contract
from test_workspace_http import FakeLambda
from test_workspace_storage import FakeS3, FakeTable
from workspace.http import WorkspaceAPI
from workspace.rules import report_passes
from workspace.storage import Storage
from workspace.worker import Worker


def request(api, method, path, body=None):
    if method == "POST" and path == "/runs":
        # This module exercises the preserved HTML prototype path. Real React
        # generation has its own tests and explicitly requests outputType=react.
        body = {**(body or {}), "outputType": (body or {}).get("outputType", "html")}
    binary = isinstance(body, bytes)
    response = api.handle({
        "rawPath": "/studio-api" + path,
        "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": {"sub": "designer", "token_use": "access"}}}},
        "body": base64.b64encode(body).decode() if binary else json.dumps(body or {}),
        "isBase64Encoded": binary,
    })
    return response["statusCode"], json.loads(response["body"]) if not response.get("isBase64Encoded") else base64.b64decode(response["body"])


def environment(model=None, renderer=None):
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private")
    api = WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker")
    worker = Worker(storage=storage, model_call=model, render_call=renderer, ocr=lambda _: ("이미지 기준", "complete"))
    return storage, api, worker


def upload(api, worker, name, data, purpose="guide"):
    status, created = request(api, "POST", "/assets", {"name": name, "size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(), "purpose": purpose})
    assert status == 201, created
    identifier = created["asset"]["id"]
    assert request(api, "PUT", f"/assets/{identifier}/parts/0", data)[0] == 200
    status, finalized = request(api, "POST", f"/assets/{identifier}/complete")
    assert status == 202
    worker.handle({"owner": "designer", "jobId": finalized["job"]["id"]})
    return identifier


def test_html_import_keeps_original_and_static_preview_separate():
    storage, api, worker = environment()
    identifier = upload(api, worker, "screen.html", HTML.encode(), "prototype")
    asset = storage.get("designer", "asset", identifier)
    assert asset["uploadStatus"] == "stored" and asset["parseStatus"] == "complete"
    assert storage.get_blob(asset["originalKey"]) == HTML.encode()
    assert b"script-src 'none'" in storage.get_blob(asset["previews"][0]["key"])
    job_id = asset["jobId"]
    assert worker.handle({"owner": "designer", "jobId": job_id})["status"] == "duplicate-or-unavailable"


def test_fig_and_corrupt_file_can_be_stored_without_claiming_interpretation():
    storage, api, worker = environment()
    for filename, data, expected in [("source.fig", b"fig-kiwi", "unsupported"), ("bad.png", b"broken-image", "failed")]:
        identifier = upload(api, worker, filename, data, "archive")
        asset = storage.get("designer", "asset", identifier)
        assert asset["uploadStatus"] == "stored" and asset["parseStatus"] == expected
        assert storage.get_blob(asset["originalKey"]) == data


def test_frozen_contract_real_browser_repair_and_exact_artifact_approval(monkeypatch):
    from workspace.browser import evaluate_html
    local_browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if local_browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(local_browser))
    calls = []
    def model(system, user, images, model_id, max_tokens, trace_id, purpose):
        calls.append(user)
        html = HTML if len(calls) > 1 else HTML.replace("textContent=document.getElementById('amount').value", "textContent='300000'")
        return html, {"inputTokens": 10, "outputTokens": 10}, {"modelId": model_id}
    storage, api, worker = environment(model, evaluate_html)
    asset_id = upload(api, worker, "guide.md", "금액 변경은 확인 화면으로 전달한다. 동의 전 제출을 차단한다.".encode())
    contract = browser_contract()
    contract["assetIds"] = [asset_id]
    status, created = request(api, "POST", "/contracts", contract)
    assert status == 201, created
    current = created["contract"]
    status, approved = request(api, "POST", f"/contracts/{current['id']}/approve", {"version": current["version"]})
    assert status == 200, approved
    approved = approved["contract"]
    status, queued = request(api, "POST", "/runs", {"contractId": approved["id"],
                         "contractVersion": approved["version"], "model": "global.openai.gpt-6-astra",
                         "maxRounds": 2, "requestId": "repair-round-trip"})
    assert status == 202, queued
    event = {"owner": "designer", "jobId": queued["job"]["id"]}
    outcome = worker.handle(event)
    assert outcome["status"] == "completed", storage.get("designer", "job", event["jobId"])
    run = storage.get("designer", "run", queued["run"]["id"])
    assert run["status"] == "completed" and run["bestRound"] == 2
    assert [r["passed"] for r in run["rounds"]] == [False, True]
    assert "실제 브라우저 실패 근거" in calls[1]
    best = run["rounds"][-1]
    report = json.loads(storage.get_blob(best["reportKey"]))
    assert report_passes(run["contract"], report)
    assert report["contractHash"] == run["contractHash"]
    assert hashlib.sha256(storage.get_blob(best["htmlKey"])).hexdigest() == best["artifactSha256"]
    status, _ = request(api, "POST", f"/runs/{run['id']}/approve", {
        "round": 1, "artifactSha256": run["rounds"][0]["artifactSha256"], "contractVersion": run["contractVersion"]})
    assert status == 409
    status, accepted = request(api, "POST", f"/runs/{run['id']}/approve", {
        "round": 2, "artifactSha256": best["artifactSha256"], "contractVersion": run["contractVersion"]})
    assert status == 200 and accepted["run"]["approval"]["round"] == 2
    assert worker.handle(event)["status"] == "duplicate-or-unavailable" and len(calls) == 2
    edited = {**approved, "title": "변경한 규칙", "version": approved["version"]}
    assert request(api, "PUT", f"/contracts/{approved['id']}", edited)[0] == 200
    status, _ = request(api, "POST", f"/runs/{run['id']}/approve", {
        "round": 2, "artifactSha256": best["artifactSha256"], "contractVersion": run["contractVersion"]})
    assert status == 409


def test_bare_passing_claim_is_not_browser_evidence():
    assert not report_passes(browser_contract(), {
        "passed": True, "functionalStatus": "pass", "checks": [],
        "networkRequests": [], "consoleErrors": [], "blockingFindings": [],
        "accessibility": {"status": "pass"}, "visual": {"status": "not-run"}})


def test_existing_html_can_be_verified_without_ai_rewriting(monkeypatch):
    import re
    from workspace.browser import evaluate_html
    local_browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if local_browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(local_browser))
    def no_generation(*args, **kwargs):
        raise AssertionError("Source verification must not invoke generation")
    storage, api, worker = environment(no_generation, evaluate_html)
    original = HTML.replace("[data-testid=summary]", "p").replace("[data-testid=submit]", "button:last-of-type")
    original = re.sub(r'\sdata-testid="[^"]+"', "", original)
    identifier = upload(api, worker, "existing.html", original.encode(), "prototype")
    contract = browser_contract()
    contract.update(assetIds=[identifier], bindings={"amount": "#amount", "next": "button:first-of-type",
                    "summary": "p", "agree": 'input[type="checkbox"]', "submit": "button:last-of-type"})
    status, created = request(api, "POST", "/contracts", contract)
    assert status == 201, created
    current = created["contract"]
    _, approved = request(api, "POST", f"/contracts/{current['id']}/approve", {"version": current["version"]})
    approved = approved["contract"]
    status, queued = request(api, "POST", "/runs", {"contractId": approved["id"],
        "contractVersion": approved["version"], "mode": "verify", "sourceAssetId": identifier,
        "model": "global.anthropic.claude-fable-5-1", "requestId": "verify-existing"})
    assert status == 202, queued
    result = worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})
    assert result["status"] == "completed", storage.get("designer", "job", queued["job"]["id"])
    run = storage.get("designer", "run", queued["run"]["id"])
    assert run["rounds"][0]["passed"] and len(run["rounds"]) == 1
    report = json.loads(storage.get_blob(run["rounds"][0]["reportKey"]))
    assert report["mode"] == "verify" and report["model"] is None
    assert report["sourceArtifactSha256"] == hashlib.sha256(original.encode()).hexdigest()


def test_verifier_infrastructure_failure_preserves_html_and_stops_model_loop():
    calls = []
    def model(*args):
        calls.append(1)
        return HTML, {"inputTokens": 1, "outputTokens": 1}, {"modelId": "global.openai.gpt-6-astra"}
    def unavailable(*args):
        raise RuntimeError("private diagnostic must not be returned")
    storage, api, worker = environment(model, unavailable)
    _, created = request(api, "POST", "/contracts", browser_contract())
    current = created["contract"]
    _, approved = request(api, "POST", f"/contracts/{current['id']}/approve", {"version": current["version"]})
    current = approved["contract"]
    _, queued = request(api, "POST", "/runs", {"contractId": current["id"], "contractVersion": current["version"],
        "requestId": "engine-failure", "maxRounds": 3, "model": "global.openai.gpt-6-astra"})
    result = worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})
    run = storage.get("designer", "run", queued["run"]["id"])
    assert result["status"] == "failed" and run["status"] == "failed" and len(calls) == 1
    assert len(run["rounds"]) == 1 and storage.get_blob(run["rounds"][0]["htmlKey"])
    report = json.loads(storage.get_blob(run["rounds"][0]["reportKey"]))
    assert report["engineError"] and report["passed"] is False
    assert "private diagnostic" not in json.dumps(run)


def test_only_internal_metadata_ids_are_aliased_and_restored():
    from workspace.worker import _metadata_aliases, _restore_metadata
    identifier = "e86b988aab184562915757615b7f220c"
    actual_identifier = "900101-1234567"
    source = f'assetId={identifier}; imported text={actual_identifier}; existing studioRef_a'
    masked, mapping = _metadata_aliases(source, [identifier])
    assert identifier not in masked and actual_identifier in masked
    assert "existing studioRef_a" in masked
    assert _restore_metadata(masked, mapping) == source
