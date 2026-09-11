import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_http import FakeLambda
from test_workspace_project_http import request
from test_workspace_storage import FakeS3, FakeTable
from workspace.http import WorkspaceAPI
from workspace.storage import Storage


def setup():
    storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="private")
    api = WorkspaceAPI(storage=storage, lambda_client=FakeLambda(), worker_fn="worker")
    _, made = request(api, "POST", "/contracts", {"title": "기준", "rules": [{"id": "R1", "title": "진행",
        "steps": [{"action": "expectVisible", "target": "next", "value": True}]}]})
    contract = made["contract"]
    _, approved = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})
    contract = approved["contract"]
    return api, {"contractId": contract["id"], "contractVersion": contract["version"],
                 "model": "global.openai.gpt-6-astra", "maxRounds": 2}


@pytest.mark.parametrize("count,total", [(2, 3), (5, 6)])
def test_guided_batch_has_one_baseline_and_the_requested_variations(count, total):
    api, body = setup()
    payload = {**body, "requestId": "guided", "mode": "guided", "variationCount": count}
    status, result = request(api, "POST", "/batches", payload)
    assert status == 202, result
    batch, runs = result["batch"], result["runs"]
    assert len(runs) == total and len(batch["slots"]) == total
    assert sum(slot["role"] == "baseline" for slot in batch["slots"]) == 1
    assert len({run["contractHash"] for run in runs}) == 1
    assert len({run["catalogHash"] for run in runs}) == 1
    assert runs[0]["visualPolicy"] == "exact"
    assert all(run["outputType"] == "react" for run in runs)
    assert len(api.lambda_client.calls) == total
    assert request(api, "POST", "/batches", payload)[1]["batch"]["runIds"] == batch["runIds"]
    assert len(api.lambda_client.calls) == total


def test_creative_mode_creates_one_new_ux_run():
    api, body = setup()
    status, result = request(api, "POST", "/batches", {**body, "requestId": "creative", "mode": "creative"})
    assert status == 202 and len(result["runs"]) == 1
    assert result["runs"][0]["generationMode"] == "creative"


@pytest.mark.parametrize("value", [0, 1, 6, True, 2.5, "3"])
def test_invalid_variation_count_does_not_create_jobs(value):
    api, body = setup()
    assert request(api, "POST", "/batches", {**body, "requestId": "invalid", "mode": "guided", "variationCount": value})[0] == 400
    assert api.lambda_client.calls == []


def test_a_failed_dispatch_is_visible_and_retries_the_same_run_ids():
    api, body = setup()
    api.lambda_client.fail = True
    payload = {**body, "requestId": "partial", "mode": "guided", "variationCount": 2}
    status, first = request(api, "POST", "/batches", payload)
    assert status == 202 and first["batch"]["status"] == "partial"
    assert all(slot.get("error") for slot in first["batch"]["slots"])
    api.lambda_client.fail = False
    status, retried = request(api, "POST", "/batches", payload)
    assert status == 202 and len(retried["runs"]) == 3
    assert len(api.lambda_client.calls) == 3
