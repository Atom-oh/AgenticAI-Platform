"""Authenticated workspace HTTP flows; all AWS operations use local fakes."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_storage import FakeS3, FakeTable

CHUNK = 2 * 1024 * 1024
MAX_FILE = 50 * 1024 * 1024


class FakeLambda:
    def __init__(self):
        self.calls = []
        self.fail = False

    def invoke(self, **kwargs):
        if self.fail:
            raise RuntimeError("private credential material must never reach an HTTP error")
        self.calls.append(kwargs)
        return {"StatusCode": 202}


class FakeRules:
    """Only the separately owned rule validator is substituted in HTTP unit tests."""
    @staticmethod
    def validate_contract(data, asset_texts=None):
        normalized = {key: copy.deepcopy(data[key]) for key in
                      ("title", "brief", "assetIds", "viewport", "rules", "unresolved") if key in data}
        normalized.setdefault("schemaVersion", 1)
        normalized.setdefault("unresolved", [])
        normalized.setdefault("viewport", {"width": 390, "height": 844})
        if not normalized.get("rules"):
            raise ValueError("At least one rule is required")
        for rule in normalized["rules"]:
            source = rule.get("source", {})
            if source.get("kind") == "explicit" and source.get("quote", "") not in (asset_texts or {}).get(source.get("assetId"), ""):
                raise ValueError("Source quote not present")
        return normalized

    @staticmethod
    def contract_hash(data):
        normalized = {key: data[key] for key in
                      ("schemaVersion", "title", "brief", "assetIds", "viewport", "rules", "unresolved") if key in data}
        return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def report_passes(contract, report, *, visual_required=False):
        from workspace.rules import report_passes
        return report_passes(contract, report, visual_required=visual_required)


@pytest.fixture
def api():
    from workspace.http import WorkspaceAPI
    from workspace.storage import Storage
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test")
    return WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker", rules=FakeRules)


def call(api, method, path, body=None, owner="alice", token_use="access", query=None, claims=True):
    event = {"version": "2.0", "rawPath": "/studio-api" + path,
             "requestContext": {"http": {"method": method}},
             "queryStringParameters": query or {}}
    if claims:
        event["requestContext"]["authorizer"] = {"jwt": {"claims": {"sub": owner, "token_use": token_use}}}
    if isinstance(body, bytes):
        event["body"] = base64.b64encode(body).decode()
        event["isBase64Encoded"] = True
    elif body is not None:
        event["body"] = json.dumps(body)
    result = api.handle(event)
    payload = base64.b64decode(result["body"]) if result.get("isBase64Encoded") else json.loads(result["body"])
    return result["statusCode"], payload, result.get("headers", {})


def begin(api, data=b"hello", name="guide.txt", owner="alice", **fields):
    status, payload, _ = call(api, "POST", "/assets", {
        "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "purpose": "guide", **fields}, owner=owner)
    assert status == 201, payload
    return payload["asset"]["id"]


def stored_asset(api, owner="alice", data=b"approved instruction", name="guide.txt", **fields):
    aid = begin(api, data, name, owner, **fields)
    record = api.storage.get(owner, "asset", aid)
    original = api.storage.key_for(owner, "asset", aid, "original")
    analysis = api.storage.key_for(owner, "asset", aid, "analysis.json")
    api.storage.put_blob(original, data, "text/plain")
    api.storage.put_blob(analysis, json.dumps({"text": data.decode(), "parseStatus": "complete"}).encode(), "application/json")
    api.storage.put(owner, "asset", {**record, "status": "stored", "uploadStatus": "stored",
                    "parseStatus": "complete", "originalKey": original, "analysisKey": analysis},
                    expected_version=record["version"])
    return aid


def contract_data(aid):
    return {"title": "Amount", "assetIds": [aid], "brief": "Confirm entered amount",
            "viewport": {"width": 390, "height": 844}, "unresolved": [],
            "rules": [{"id": "R1", "title": "Amount is shown", "required": True,
                       "source": {"kind": "manual"},
                       "steps": [{"action": "expectText", "target": "summary", "value": "100"}]}]}


def approved_contract(api):
    aid = stored_asset(api)
    status, payload, _ = call(api, "POST", "/contracts", contract_data(aid))
    assert status == 201, payload
    contract = payload["contract"]
    status, payload, _ = call(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})
    assert status == 200, payload
    return aid, payload["contract"]


@pytest.mark.parametrize("claims,owner,token", [(False, "alice", "access"), (True, "", "access"), (True, "alice", "id")])
def test_requires_authorizer_access_claims(api, claims, owner, token):
    status, body, _ = call(api, "GET", "/config", claims=claims, owner=owner, token_use=token)
    assert status == 401 and body["code"] == "unauthorized"
    assert not api.storage.table().items


def test_config_limits_and_no_body_owner(api):
    status, config, _ = call(api, "GET", "/config")
    assert status == 200 and config["maxFileBytes"] == MAX_FILE and config["chunkBytes"] == CHUNK
    status, payload, _ = call(api, "POST", "/assets", {
        "name": "a.txt", "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest(),
        "purpose": "guide", "owner": "bob"})
    assert status == 201
    aid = payload["asset"]["id"]
    assert api.storage.get("alice", "asset", aid)
    assert api.storage.get("bob", "asset", aid) is None
    assert call(api, "GET", f"/assets/{aid}", owner="bob")[0] == 404
    assert call(api, "DELETE", f"/assets/{aid}", owner="bob")[0] == 404


@pytest.mark.parametrize("size", [MAX_FILE + 1, -1, True, "100"])
def test_reject_invalid_or_oversized_upload(api, size):
    status, _, _ = call(api, "POST", "/assets", {"name": "a.txt", "size": size, "sha256": "a" * 64, "purpose": "guide"})
    assert status == (413 if size == MAX_FILE + 1 else 400)
    assert not api.storage.table().items and not api.storage.s3().puts


def test_upload_parts_exact_sizes_duplicates_changes_and_late_parts(api):
    data = b"a" * CHUNK + b"tail"
    aid = begin(api, data)
    route = f"/assets/{aid}"
    assert call(api, "POST", route + "/complete", {})[0] == 409
    assert call(api, "PUT", route + "/parts/0", b"a")[0] == 409
    assert call(api, "PUT", route + "/parts/2", b"a")[0] == 409
    assert call(api, "PUT", route + "/parts/0", data[:CHUNK])[0] == 200
    writes = len(api.storage.s3().puts)
    assert call(api, "PUT", route + "/parts/0", data[:CHUNK])[0] == 200
    assert len(api.storage.s3().puts) == writes
    assert call(api, "PUT", route + "/parts/0", b"b" * CHUNK)[0] == 409
    assert call(api, "PUT", route + "/parts/1", b"tail")[0] == 200
    status, completed, _ = call(api, "POST", route + "/complete", {})
    assert status == 202 and completed["asset"]["uploadStatus"] == "processing"
    assert completed["job"]["task"] == "finalize"
    assert json.loads(api.lambda_client.calls[0]["Payload"]) == {"owner": "alice", "jobId": completed["job"]["id"]}
    assert api.lambda_client.calls[0]["InvocationType"] == "Event"
    writes = len(api.storage.s3().puts)
    assert call(api, "PUT", route + "/parts/1", b"tail")[0] == 409
    assert len(api.storage.s3().puts) == writes
    again = call(api, "POST", route + "/complete", {})
    assert again[0] == 202 and again[1]["job"]["id"] == completed["job"]["id"]


def test_parts_and_parent_are_owner_scoped(api):
    aid = begin(api)
    assert call(api, "PUT", f"/assets/{aid}/parts/0", b"hello", owner="bob")[0] == 404
    assert call(api, "POST", f"/assets/{aid}/complete", {}, owner="bob")[0] == 404
    status, _, _ = call(api, "POST", "/assets", {
        "name": "new.txt", "size": 1, "sha256": "a" * 64, "purpose": "guide", "parentId": aid}, owner="bob")
    assert status == 404 and not api.storage.s3().puts


def test_binary_download_is_private_bounded_and_never_active(api):
    aid = stored_asset(api, data=b"<script>alert(1)</script>", name="source.html")
    status, data, headers = call(api, "GET", f"/assets/{aid}/blob", query={"kind": "original", "offset": "2"})
    assert status == 200 and data == b"<script>alert(1)</script>"[2:]
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["X-Content-Type"] == "application/octet-stream"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "private, no-store"
    assert call(api, "GET", f"/assets/{aid}/blob", owner="bob")[0] == 404
    for query in ({"offset": "-1"}, {"offset": "1.5"}, {"offset": "999999"}, {"kind": "../../secret"}):
        assert call(api, "GET", f"/assets/{aid}/blob", query=query)[0] in (400, 416)
    metadata = call(api, "GET", f"/assets/{aid}")[1]
    assert "originalKey" not in json.dumps(metadata) and "analysisKey" not in json.dumps(metadata)


def test_archive_retains_blobs_and_contract_snapshots(api):
    aid = stored_asset(api)
    before = copy.deepcopy(api.storage.s3().objects)
    assert call(api, "DELETE", f"/assets/{aid}")[0] == 200
    assert api.storage.get("alice", "asset", aid)["archived"] is True
    assert api.storage.s3().objects == before
    assert call(api, "POST", "/contracts", contract_data(aid))[0] == 409


def test_contract_edit_uses_cas_clears_approval_and_retains_revision(api):
    aid, approved = approved_contract(api)
    changes = {**contract_data(aid), "version": approved["version"], "title": "Changed"}
    status, payload, _ = call(api, "PUT", f"/contracts/{approved['id']}", changes)
    assert status == 200, payload
    edited = payload["contract"]
    assert edited["version"] == approved["version"] + 1 and edited["status"] == "draft"
    assert not edited.get("approval")
    assert call(api, "PUT", f"/contracts/{approved['id']}", changes)[0] == 409
    snapshots = [item["data"] for item in api.storage.s3().objects.values()
                 if b'"approval"' in item["data"]]
    assert any(json.loads(blob)["version"] == approved["version"] for blob in snapshots)
    assert call(api, "GET", f"/contracts/{approved['id']}", owner="bob")[0] == 404


def test_unresolved_and_mismatched_quotes_cannot_be_approved(api):
    aid = stored_asset(api)
    data = contract_data(aid)
    data["unresolved"] = ["Unknown transition"]
    status, payload, _ = call(api, "POST", "/contracts", data)
    assert status == 201
    contract = payload["contract"]
    assert call(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})[0] == 409
    data["rules"][0]["source"] = {"kind": "explicit", "assetId": aid, "quote": "not in the source"}
    assert call(api, "POST", "/contracts", data)[0] == 400


def test_run_freezes_owner_scoped_approved_contract_and_is_idempotent(api):
    aid, approved = approved_contract(api)
    request = {"contractId": approved["id"], "contractVersion": approved["version"],
               "model": "global.openai.gpt-6-astra", "maxRounds": 3, "requestId": "run-1"}
    status, payload, _ = call(api, "POST", "/runs", request)
    assert status == 202, payload
    run = api.storage.get("alice", "run", payload["run"]["id"])
    assert run["contractVersion"] == approved["version"] and run["contractHash"]
    assert run["contract"]["rules"] == approved["rules"]
    assert run["assetSnapshots"][0]["id"] == aid
    assert json.loads(api.lambda_client.calls[-1]["Payload"]) == {"owner": "alice", "jobId": payload["job"]["id"]}
    assert call(api, "POST", "/runs", request)[1]["run"]["id"] == run["id"]
    assert call(api, "POST", "/runs", {**request, "maxRounds": 4})[0] == 409
    assert call(api, "POST", "/runs", {**request, "requestId": "other", "contractVersion": 999})[0] == 409
    assert call(api, "POST", "/runs", {**request, "requestId": "foreign"}, owner="bob")[0] == 404
    assert call(api, "GET", f"/runs/{run['id']}", owner="bob")[0] == 404


def test_proposal_records_input_identity_and_rejects_foreign_assets(api):
    aid = stored_asset(api)
    body = {"assetIds": [aid], "brief": "propose", "requestId": "proposal-1"}
    status, payload, _ = call(api, "POST", "/contracts/propose", body)
    assert status == 202 and payload["job"]["task"] == "propose"
    assert call(api, "POST", "/contracts/propose", body)[1]["job"]["id"] == payload["job"]["id"]
    assert call(api, "POST", "/contracts/propose", {**body, "brief": "different"})[0] == 409
    assert call(api, "POST", "/contracts/propose", body, owner="bob")[0] == 404


def test_invocation_failure_persists_failure_without_exception_leak(api):
    aid = begin(api)
    call(api, "PUT", f"/assets/{aid}/parts/0", b"hello")
    api.lambda_client.fail = True
    status, payload, _ = call(api, "POST", f"/assets/{aid}/complete", {})
    assert status == 503
    assert "credential" not in json.dumps(payload)
    jobs = api.storage.list("alice", "job")
    assert jobs and jobs[0]["status"] == "failed"
    assert api.storage.get("alice", "asset", aid)["uploadStatus"] == "failed"


def test_complete_retry_recovers_a_queued_but_not_dispatched_job(api):
    aid = begin(api)
    call(api, "PUT", f"/assets/{aid}/parts/0", b"hello")
    asset = api.storage.get("alice", "asset", aid)
    jid = f"finalize-{aid}"
    api.storage.put("alice", "asset", {**asset, "uploadStatus": "processing", "status": "processing", "jobId": jid},
                    asset["version"])
    # Simulate termination between the asset transition and job creation.
    status, payload, _ = call(api, "POST", f"/assets/{aid}/complete", {})
    assert status == 202, payload
    assert payload["job"]["id"] == jid and len(api.lambda_client.calls) == 1


def test_concurrent_changed_parts_have_exactly_one_winner(api):
    aid = begin(api, b"aaaa")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda data: call(api, "PUT", f"/assets/{aid}/parts/0", data),
                                  [b"aaaa", b"bbbb"]))
    assert sorted(response[0] for response in responses) == [200, 409]
    asset = api.storage.get("alice", "asset", aid)
    assert asset["parts"]["0"]["status"] == "stored"
    data = api.storage.get_blob(asset["parts"]["0"]["key"])
    assert hashlib.sha256(data).hexdigest() == asset["parts"]["0"]["sha256"]
    assert len(api.storage.s3().puts) == 1


def test_concurrent_identical_parts_are_idempotent(api):
    aid = begin(api)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: call(api, "PUT", f"/assets/{aid}/parts/0", b"hello"), range(8)))
    assert all(response[0] == 200 for response in responses), responses
    assert len(api.storage.s3().puts) == 1


def test_binary_request_and_download_bounds(api):
    aid = begin(api, b"x" * CHUNK)
    assert call(api, "PUT", f"/assets/{aid}/parts/0", b"x" * (CHUNK + 1))[0] == 413
    assert not api.storage.s3().puts
    asset = api.storage.get("alice", "asset", aid)
    data = b"x" * (CHUNK + 11)
    api.storage.put_blob(asset["originalKey"], data, "text/html")
    api.storage.put("alice", "asset", {
        **asset, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "uploadStatus": "stored"},
        asset["version"])
    first = call(api, "GET", f"/assets/{aid}/blob")
    last = call(api, "GET", f"/assets/{aid}/blob", query={"offset": str(CHUNK)})
    assert len(first[1]) == CHUNK and first[1] + last[1] == data
    assert first[2]["X-Total-Size"] == str(len(data))
    assert first[2]["X-SHA256"] == hashlib.sha256(data).hexdigest()
    assert all(body.closed for body in api.storage.s3().bodies)


def test_run_invocation_failure_is_not_left_queued(api):
    _, contract = approved_contract(api)
    api.lambda_client.fail = True
    status, _, _ = call(api, "POST", "/runs", {
        "contractId": contract["id"], "contractVersion": contract["version"], "requestId": "failed-run"})
    assert status == 503
    assert api.storage.list("alice", "run")[0]["status"] == "failed"


def _tested_run(api, *, passed=True):
    _, contract = approved_contract(api)
    status, payload, _ = call(api, "POST", "/runs", {
        "contractId": contract["id"], "contractVersion": contract["version"], "requestId": "tested"})
    assert status == 202
    run = api.storage.get("alice", "run", payload["run"]["id"])
    html = b"<html><body>tested</body></html>"
    digest = hashlib.sha256(html).hexdigest()
    html_key = api.storage.key_for("alice", "run", run["id"], "r1.html")
    report_key = api.storage.key_for("alice", "run", run["id"], "r1.json")
    api.storage.put_blob(html_key, html, "text/html")
    report = {"passed": passed, "artifactSha256": digest, "contractHash": run["contractHash"],
              "functionalStatus": "pass" if passed else "fail",
              "checks": [{"caseId": "R1", "title": "Amount is shown", "required": True,
                          "status": "pass" if passed else "fail",
                          "steps": [{"index": 0, "action": "expectText", "target": "summary",
                                     "expected": "100", "actual": "100",
                                     "status": "pass" if passed else "fail"}]}],
              "networkRequests": [], "consoleErrors": [], "blockingFindings": [] if passed else ["R1"],
              "accessibility": {"status": "pass"}, "visual": {"status": "not-run"}}
    api.storage.put_blob(report_key, json.dumps(report).encode(), "application/json")
    row = {"number": 1, "passed": passed, "artifactSha256": digest, "htmlKey": html_key,
           "reportKey": report_key, "checks": {"functionalStatus": "passed", "networkStatus": "passed"},
           "blockingFindings": [] if passed else ["R1"]}
    run = api.storage.put("alice", "run", {**run, "status": "completed", "bestRound": 1, "rounds": [row],
                                          "functionalStatus": "passed", "visualStatus": "not-run"}, run["version"])
    return run, {"round": 1, "artifactSha256": digest, "contractVersion": run["contractVersion"]}


def test_run_approval_binds_tested_bytes_contract_and_actor(api):
    run, request = _tested_run(api)
    route = f"/runs/{run['id']}/approve"
    assert call(api, "POST", route, {**request, "artifactSha256": "0" * 64})[0] == 409
    assert call(api, "POST", route, request, owner="bob")[0] == 404
    status, payload, _ = call(api, "POST", route, {**request, "actor": "forged"})
    assert status == 200, payload
    assert payload["run"]["approval"]["actor"] == "alice"
    assert payload["run"]["approval"]["artifactSha256"] == request["artifactSha256"]
    api.storage.put_blob(run["rounds"][0]["htmlKey"], b"swapped artifact", "text/html")
    assert call(api, "POST", route, request)[0] == 409


def test_in_progress_runs_cannot_be_approved(api):
    run, request = _tested_run(api)
    api.storage.put("alice", "run", {**run, "status": "running"}, run["version"])
    assert call(api, "POST", f"/runs/{run['id']}/approve", request)[0] == 409


def test_run_approval_rejects_swapped_passing_report(api):
    run, request = _tested_run(api)
    api.storage.put_blob(run["rounds"][0]["reportKey"], json.dumps({
        "passed": True, "artifactSha256": "0" * 64, "contractHash": run["contractHash"]}).encode(), "application/json")
    assert call(api, "POST", f"/runs/{run['id']}/approve", request)[0] == 409


def test_run_approval_rejects_changed_contract(api):
    run, request = _tested_run(api)
    contract = api.storage.get("alice", "contract", run["contractId"])
    api.storage.put("alice", "contract", {**contract, "status": "draft"}, contract["version"])
    assert call(api, "POST", f"/runs/{run['id']}/approve", request)[0] == 409


@pytest.mark.parametrize("field,value", [
    ("model", "https://unapproved.invalid/model"), ("maxRounds", 6), ("maxRounds", True),
    ("instruction", "x" * 4001), ("visualTolerance", -0.1), ("visualTolerance", 0.51), ("variant", "arbitrary"),
])
def test_run_input_limits_fail_before_worker_invocation(api, field, value):
    _, contract = approved_contract(api)
    status, _, _ = call(api, "POST", "/runs", {
        "contractId": contract["id"], "contractVersion": contract["version"], "requestId": "invalid", field: value})
    assert status == 400 and api.lambda_client.calls == []


def test_real_rules_validator_enforces_source_quotes_and_assertions(api):
    api._rules = None
    aid = stored_asset(api)
    data = contract_data(aid)
    data["rules"][0]["source"] = {"kind": "explicit", "assetId": aid, "quote": "approved instruction"}
    assert call(api, "POST", "/contracts", data)[0] == 201
    data["rules"][0]["steps"] = [{"action": "click", "target": "next"}]
    assert call(api, "POST", "/contracts", data)[0] == 400


@pytest.mark.parametrize("field,value", [
    ("checks", []), ("checks", [{"caseId": "R1", "required": False, "status": "fail"}]),
    ("functionalStatus", "incomplete"), ("networkRequests", ["outside"]),
    ("consoleErrors", ["error"]), ("accessibility", {"status": "incomplete"}),
    ("visual", {"status": "fail"}), ("blockingFindings", ["incomplete"]),
])
def test_overall_pass_cannot_hide_failed_or_missing_required_gates(api, field, value):
    run, request = _tested_run(api)
    key = run["rounds"][0]["reportKey"]
    report = json.loads(api.storage.get_blob(key))
    report[field] = value
    api.storage.put_blob(key, json.dumps(report).encode(), "application/json")
    assert call(api, "POST", f"/runs/{run['id']}/approve", request)[0] == 409


@pytest.mark.parametrize("authorizer", [{}, {"jwt": {}}, {"jwt": {"claims": "not-claims"}}, {"jwt": []}])
def test_malformed_authorizer_claims_are_unauthorized(api, authorizer):
    response = api.handle({"rawPath": "/studio-api/config", "requestContext": {
        "http": {"method": "GET"}, "authorizer": authorizer}})
    assert response["statusCode"] == 401


def test_private_artifact_availability_is_exposed_without_s3_addresses(api):
    run, _ = _tested_run(api)
    row = run["rounds"][0]
    row["screenshotKey"] = api.storage.key_for("alice", "run", run["id"], "screenshot.png")
    row["diffKey"] = api.storage.key_for("alice", "run", run["id"], "diff.png")
    api.storage.put("alice", "run", run, run["version"])
    status, payload, _ = call(api, "GET", f"/runs/{run['id']}")
    assert status == 200
    public_row = payload["run"]["rounds"][0]
    assert public_row["hasScreenshot"] and public_row["hasDiff"] and public_row["hasHtml"] and public_row["hasReport"]
    assert "workspace/" not in json.dumps(payload)


def _add_numbered_previews(api, aid):
    asset = api.storage.get("alice", "asset", aid)
    previews = []
    for page in (1, 2):
        key = api.storage.key_for("alice", "asset", aid, f"previews/{page}.png")
        api.storage.put_blob(key, f"preview-{page}".encode(), "image/png")
        previews.append({"page": page, "key": key, "mime": "image/png", "width": 390, "height": 844})
    return api.storage.put("alice", "asset", {**asset, "previews": previews}, asset["version"])


def test_preview_download_defaults_to_human_page_one(api):
    aid = stored_asset(api)
    _add_numbered_previews(api, aid)
    route = f"/assets/{aid}/blob"
    status, data, headers = call(api, "GET", route, query={"kind": "preview"})
    assert status == 200 and data == b"preview-1"
    assert headers["Content-Type"] == "image/png"
    assert call(api, "GET", route, query={"kind": "preview", "page": "2"})[1] == b"preview-2"
    assert call(api, "GET", route, query={"kind": "preview", "page": "0"})[0] == 400
    assert call(api, "GET", route, query={"kind": "preview", "page": "-1"})[0] == 400


def test_reference_selection_defaults_to_human_page_one(api):
    aid, contract = approved_contract(api)
    asset = _add_numbered_previews(api, aid)
    request = {"contractId": contract["id"], "contractVersion": contract["version"],
               "referenceAssetId": aid, "requestId": "reference-default"}
    status, payload, _ = call(api, "POST", "/runs", request)
    assert status == 202, payload
    run = api.storage.get("alice", "run", payload["run"]["id"])
    assert run["referencePage"] == 1 and run["referenceKey"] == asset["previews"][0]["key"]
    assert run["assetSnapshots"][0]["previews"][0]["page"] == 1
    status, payload, _ = call(api, "POST", "/runs", {**request, "requestId": "reference-second", "referencePage": 2})
    assert status == 202 and payload["run"]["referencePage"] == 2
    assert call(api, "POST", "/runs", {**request, "requestId": "reference-zero", "referencePage": 0})[0] == 400


def test_approved_version_matches_returned_storage_version_with_real_rules(api):
    api._rules = None
    aid = stored_asset(api)
    status, payload, _ = call(api, "POST", "/contracts", contract_data(aid))
    assert status == 201
    draft = payload["contract"]
    status, payload, _ = call(api, "POST", f"/contracts/{draft['id']}/approve", {"version": draft["version"]})
    assert status == 200
    approved = payload["contract"]
    assert approved["approval"]["version"] == approved["version"] == draft["version"] + 1
    from workspace.rules import contract_hash
    assert approved["approval"]["hash"] == contract_hash(approved)
    assert call(api, "POST", "/runs", {"contractId": approved["id"], "contractVersion": approved["version"],
                                      "requestId": "returned-approved-version"})[0] == 202


def test_asset_list_is_metadata_only_and_detail_reads_worker_analysis(api):
    aid = stored_asset(api)
    asset = _add_numbered_previews(api, aid)
    analysis = {"text": "worker extracted source", "format": "pdf", "pages": 2,
                "dimensions": {"width": 390, "height": 844}, "warnings": ["partial extraction"],
                "resources": {"external": [], "missing": []}, "parseStatus": "partial",
                "textTruncated": True}
    api.storage.put_blob(asset["analysisKey"], json.dumps(analysis).encode(), "application/json")
    api.storage.put("alice", "asset", {**asset, "parseStatus": "partial",
                                      "warnings": analysis["warnings"]}, asset["version"])
    before = len(api.storage.s3().reads)
    status, listing, _ = call(api, "GET", "/assets")
    assert status == 200 and len(api.storage.s3().reads) == before
    assert listing["assets"][0]["previews"][0]["page"] == 1
    assert "analysis" not in listing["assets"][0]
    status, detail, _ = call(api, "GET", f"/assets/{aid}")
    assert status == 200 and detail["analysis"] == analysis
    assert detail["asset"]["parseStatus"] == "partial"
    assert "analysisKey" not in detail["asset"] and "key" not in detail["asset"]["previews"][0]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_stale_job_read_persists_timeout_and_stops_polling(api, status):
    now = [1_000]
    api.storage.clock = lambda: now[0]
    job = api.storage.put("alice", "job", {"id": "expired", "task": "run", "input": {"runId": "run"},
                                          "status": status, "progress": 20})
    now[0] += 16 * 60 * 1000 + 1
    response_status, payload, _ = call(api, "GET", "/jobs/expired")
    assert response_status == 200
    failed = payload["job"]
    assert failed["status"] == "failed" and failed["errorCode"] == "job-timeout"
    assert failed["stopReason"] == "timeout" and failed["error"]
    assert failed["version"] == job["version"] + 1
    assert api.storage.get("alice", "job", "expired") == failed
    assert api.storage.claim_job("alice", "expired") is None
    assert call(api, "GET", "/jobs/expired")[1]["job"] == failed
    assert not api.lambda_client.calls and not api.storage.s3().puts


@pytest.mark.parametrize("age", [0, 16 * 60 * 1000 - 1, 16 * 60 * 1000])
def test_jobs_with_recent_progress_are_not_expired(api, age):
    now = [0]
    api.storage.clock = lambda: now[0]
    job = api.storage.put("alice", "job", {"id": "active", "task": "run", "status": "running"})
    now[0] = 60 * 60 * 1000
    job = api.storage.put("alice", "job", {**job, "progress": 50}, job["version"])
    now[0] += age
    response_status, payload, _ = call(api, "GET", "/jobs/active")
    assert response_status == 200 and payload["job"] == job


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_terminal_job_reads_never_rewrite_old_jobs(api, status):
    now = [100]
    api.storage.clock = lambda: now[0]
    job = api.storage.put("alice", "job", {"id": "terminal", "task": "finalize", "status": status})
    now[0] += 60 * 60 * 1000
    assert call(api, "GET", "/jobs/terminal")[1]["job"] == job


def test_foreign_stale_job_lookup_cannot_trigger_timeout(api):
    now = [100]
    api.storage.clock = lambda: now[0]
    job = api.storage.put("alice", "job", {"id": "private", "task": "run", "status": "running"})
    now[0] += 17 * 60 * 1000
    assert call(api, "GET", "/jobs/private", owner="bob")[0] == 404
    assert api.storage.get("alice", "job", "private") == job


@pytest.mark.parametrize("next_status", ["running", "completed"])
def test_stale_job_cas_does_not_overwrite_concurrent_progress(api, monkeypatch, next_status):
    now = [100]
    api.storage.clock = lambda: now[0]
    job = api.storage.put("alice", "job", {"id": "racing", "task": "run", "status": "running", "progress": 20})
    now[0] += 17 * 60 * 1000
    put = api.storage.put
    attempts = []

    def racing_put(owner, kind, item, expected_version=None):
        if kind == "job" and item.get("status") == "failed":
            attempts.append(item)
            put(owner, kind, {**job, "status": next_status, "progress": 100}, job["version"])
        return put(owner, kind, item, expected_version)

    monkeypatch.setattr(api.storage, "put", racing_put)
    response_status, payload, _ = call(api, "GET", "/jobs/racing")
    assert response_status == 200
    assert len(attempts) == 1, "Timeout recovery must make only one conditional write attempt"
    assert payload["job"]["status"] == next_status and payload["job"]["progress"] == 100
    assert "errorCode" not in payload["job"]


@pytest.mark.parametrize("mutation", [
    "missing", "empty", "extra", "index", "action", "target", "expected", "status",
])
def test_run_approval_requires_complete_matching_passing_steps(api, mutation):
    run, request = _tested_run(api)
    key = run["rounds"][0]["reportKey"]
    report = json.loads(api.storage.get_blob(key))
    check = report["checks"][0]
    if mutation == "missing":
        del check["steps"]
    elif mutation == "empty":
        check["steps"] = []
    elif mutation == "extra":
        check["steps"].append(copy.deepcopy(check["steps"][0]))
    else:
        replacement = {"index": 1, "action": "click", "target": "wrong", "expected": "101", "status": "fail"}
        check["steps"][0][mutation] = replacement[mutation]
    api.storage.put_blob(key, json.dumps(report).encode(), "application/json")
    status, payload, _ = call(api, "POST", f"/runs/{run['id']}/approve", request)
    assert status == 409 and payload["code"] == "approval-evidence-required"
    assert not api.storage.get("alice", "run", run["id"]).get("approval")


def test_expired_operational_job_cannot_requeue_a_retained_finished_run(api):
    run, _ = _tested_run(api)
    table = api.storage.table()
    # Simulate DynamoDB TTL deleting only the operational job.
    job_key = next(key for key in table.items if key[1] == f"job#{run['jobId']}")
    del table.items[job_key]
    invocations = len(api.lambda_client.calls)
    status, payload, _ = call(api, "POST", "/runs", {
        "contractId": run["contractId"], "contractVersion": run["contractVersion"], "requestId": "tested"})
    assert status == 409 and payload["code"] == "request-expired"
    assert api.storage.get("alice", "run", run["id"]) == run
    assert api.storage.get("alice", "job", run["jobId"]) is None
    assert len(api.lambda_client.calls) == invocations


def test_import_revision_tracks_lineage_not_part_or_status_cas_writes(api):
    root_id = begin(api, importRevision=99, lineageId="forged", sourceVersion="source-v99")
    root = api.storage.get("alice", "asset", root_id)
    assert root["importRevision"] == 1 and root["lineageId"] == root_id and root["parentId"] is None
    assert root.get("sourceVersion") is None
    assert call(api, "PUT", f"/assets/{root_id}/parts/0", b"hello")[0] == 200
    assert call(api, "POST", f"/assets/{root_id}/complete", {})[0] == 202
    updated = api.storage.get("alice", "asset", root_id)
    assert updated["version"] > root["version"]
    assert updated["importRevision"] == 1 and updated["lineageId"] == root_id
    child_id = begin(api, parentId=root_id)
    child = api.storage.get("alice", "asset", child_id)
    assert child["importRevision"] == 2 and child["lineageId"] == root_id and child["parentId"] == root_id
    grandchild_id = begin(api, parentId=child_id)
    grandchild = api.storage.get("alice", "asset", grandchild_id)
    assert grandchild["importRevision"] == 3 and grandchild["lineageId"] == root_id
    assert grandchild["parentId"] == child_id
    independent_id = begin(api)
    independent = api.storage.get("alice", "asset", independent_id)
    assert independent["importRevision"] == 1 and independent["lineageId"] == independent_id


def test_child_of_legacy_asset_uses_intake_baseline_not_cas_version(api):
    parent = api.storage.put("alice", "asset", {"id": "legacy", "status": "stored"})
    for _ in range(4):
        parent = api.storage.put("alice", "asset", parent, parent["version"])
    child_id = begin(api, parentId=parent["id"])
    child = api.storage.get("alice", "asset", child_id)
    assert parent["version"] == 5
    assert child["importRevision"] == 2 and child["lineageId"] == parent["id"]
    assert child.get("sourceVersion") is None


def test_run_and_proposal_snapshots_freeze_import_lineage(api):
    root_id = stored_asset(api)
    child_id = stored_asset(api, parentId=root_id)
    _, proposed, _ = call(api, "POST", "/contracts/propose", {
        "assetIds": [child_id], "brief": "Use this import", "requestId": "lineage-proposal"})
    status, created, _ = call(api, "POST", "/contracts", contract_data(child_id))
    assert status == 201
    contract = created["contract"]
    status, approved, _ = call(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})
    assert status == 200
    status, queued, _ = call(api, "POST", "/runs", {
        "contractId": contract["id"], "contractVersion": approved["contract"]["version"], "requestId": "lineage-run"})
    assert status == 202
    expected = {"importRevision": 2, "lineageId": root_id, "parentId": root_id}
    for snapshot in [proposed["job"]["input"]["assetSnapshots"][0], queued["run"]["assetSnapshots"][0]]:
        assert {key: snapshot[key] for key in expected} == expected
        assert snapshot.get("sourceVersion") is None
    child = api.storage.get("alice", "asset", child_id)
    api.storage.put("alice", "asset", {**child, "warnings": ["status update"]}, child["version"])
    retained = api.storage.get("alice", "run", queued["run"]["id"])["assetSnapshots"][0]
    assert {key: retained[key] for key in expected} == expected
