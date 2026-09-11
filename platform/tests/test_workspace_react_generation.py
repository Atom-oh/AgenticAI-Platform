import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_http import FakeLambda
from test_workspace_git_export import git, repo
from test_workspace_react_runtime import APP, CONTRACT
from test_workspace_storage import FakeS3, FakeTable
from test_workspace_worker import request
from workspace.http import WorkspaceAPI
from workspace.git_service import connection_hash
from workspace.react_artifacts import read_archive
from workspace.react_runtime import evaluate_react
from workspace.storage import Storage
from workspace.worker import Worker


def test_react_repair_preserves_code_criteria_and_approval_binds_source_and_dist(monkeypatch, repo):
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    calls = []

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        calls.append((system, user))
        source = APP if len(calls) > 1 else APP.replace("Number(amount).toLocaleString", "Number(300000).toLocaleString")
        return json.dumps({"files": {"src/App.tsx": source}}), {"inputTokens": 10, "outputTokens": 10}, {"modelId": model_id}

    store = Storage(table=FakeTable(), s3=FakeS3(), bucket="private")
    api = WorkspaceAPI(storage=store, lambda_client=FakeLambda(), worker_fn="worker")
    worker = Worker(storage=store, model_call=model, react_call=evaluate_react)
    status, data = request(api, "POST", "/contracts", CONTRACT)
    assert status == 201, data
    contract = data["contract"]
    status, data = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})
    assert status == 200, data
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                         "outputType": "react", "model": "global.openai.gpt-6-astra", "variant": "baseline",
                         "generationMode": "guided", "maxRounds": 2, "requestId": "react-repair"})
    assert status == 202, data
    event = {"owner": "designer", "jobId": data["job"]["id"]}
    result = worker.handle(event)
    assert result["status"] == "completed", store.get("designer", "job", event["jobId"])
    run = store.get("designer", "run", data["run"]["id"])
    assert run["outputType"] == "react" and run["bestRound"] == 2
    assert [row["passed"] for row in run["rounds"]] == [False, True]
    assert "Actual failed build/browser evidence" in calls[1][1]
    first, final = run["rounds"]
    assert first["catalogHash"] == final["catalogHash"] == contract["catalogHash"]
    source = store.get_blob(final["sourceKey"])
    dist = store.get_blob(final["distKey"])
    assert read_archive(source, final["sourceHash"])["src/App.tsx"].decode() == APP
    assert read_archive(dist, final["bundleHash"])["assets/app.js"]
    body = {"round": 2, "artifactSha256": final["artifactSha256"], "contractVersion": run["contractVersion"],
            "sourceHash": final["sourceHash"], "bundleHash": final["bundleHash"]}
    assert request(api, "POST", f"/runs/{run['id']}/approve", {**body, "sourceHash": "0" * 64})[0] == 409
    assert request(api, "POST", f"/runs/{run['id']}/approve", {**body, "bundleHash": "0" * 64})[0] == 409
    status, accepted = request(api, "POST", f"/runs/{run['id']}/approve", body)
    assert status == 200, accepted
    assert accepted["run"]["approval"]["sourceHash"] == final["sourceHash"]
    assert accepted["run"]["approval"]["bundleHash"] == final["bundleHash"]
    assert hashlib.sha256(store.get_blob(final["htmlKey"])).hexdigest() == final["artifactSha256"]
    status, prepared = request(api, "POST", "/releases", {"runId": run["id"], "round": 2, "requestId": "release"})
    assert status == 202, prepared
    release_event = {"owner": "designer", "jobId": prepared["job"]["id"]}
    result = worker.handle(release_event)
    assert result["status"] == "completed", store.get("designer", "job", release_event["jobId"])
    release = store.get("designer", "release", prepared["release"]["id"])
    assert release["status"] == "ready" and release["bundleHash"] == final["bundleHash"]
    assert read_archive(store.get_blob(release["sourceKey"]), release["sourceHash"])["src/App.tsx"].decode() == APP
    assert release["verification"]["visual"]["status"] == "pass"
    assert len(calls) == 2, "post-approval rebuild must not call a generation model"
    bare, connection, base, _ = repo
    connection = {**connection, "visibility": "internal"}
    api.git_connections = worker.git_connections = lambda: {connection["id"]: connection}
    import workspace.git_service as git_service
    original_context, original_factory = git_service.resolve_generation_context, worker.git_exporter_factory
    committed = False

    def context_after_export(*args):
        if committed:
            raise ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "transient read failure"}}, "GetItem")
        return original_context(*args)

    def marked_exporter(*args):
        real = original_factory(*args)
        def export_release(*values, **options):
            nonlocal committed
            result = real.export_release(*values, **options)
            committed = True
            return result
        return SimpleNamespace(export_release=export_release)

    monkeypatch.setattr(git_service, "resolve_generation_context", context_after_export)
    worker.git_exporter_factory = marked_exporter
    export_body = {"connectionId": connection["id"], "connectionHash": connection_hash(connection), "requestId": "export"}
    status, queued = request(api, "POST", f"/releases/{release['id']}/git", export_body)
    assert status == 202, queued
    export_event = {"owner": "designer", "jobId": queued["job"]["id"]}
    assert worker.handle(export_event)["status"] == "completed", store.get("designer", "job", export_event["jobId"])
    _, viewed = request(api, "GET", f"/releases/{release['id']}")
    commit = viewed["release"]["git"]
    assert commit["status"] == "committed" and commit["branch"].startswith("feature/")
    assert commit["criteriaStatus"] == "unavailable" and commit["criteriaCurrent"] is None
    assert commit["commitUrl"] is None, "local tests must not invent remote commit links"
    assert git(bare, "show", f"{commit['commitSha']}:generated/studio/{run['id']}/src/App.tsx") == APP.encode()
    assert git(bare, "rev-parse", "refs/heads/main").decode().strip() == base
    assert request(api, "POST", f"/releases/{release['id']}/git", export_body)[1]["export"]["commitSha"] == commit["commitSha"]
    assert len(calls) == 2
    refinement = {"contractId": contract["id"], "contractVersion": contract["version"], "baseRunId": run["id"],
                  "baseRound": 2, "outputType": "react", "requestId": "refine-baseline"}
    status, refined = request(api, "POST", "/runs", refinement)
    assert status == 202, refined
    assert refined["run"]["variant"] == "baseline" and refined["run"]["visualPolicy"] == "exact"
    assert refined["run"]["generationMode"] == "guided"
    assert request(api, "POST", "/runs", {**refinement, "requestId": "weaken-reference", "visualTolerance": 0.5})[0] == 409
