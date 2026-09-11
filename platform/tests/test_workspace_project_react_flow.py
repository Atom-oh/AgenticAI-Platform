import json
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
    if browser.is_file():
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
    _, changed = request(api, "PUT", f"/products/{product['id']}", {"version": product["version"], "description": "변경된 기준"},
                          actor="bob", project=project["id"])
    assert request(api, "POST", f"/products/{product['id']}/publish", {"version": changed["product"]["version"]},
                   actor="bob", project=project["id"])[0] == 200
    assert request(api, "POST", "/releases", {"runId": run["id"], "round": 1, "requestId": "stale-release"},
                   actor="dana", project=project["id"])[0] == 409
    _, impact = request(api, "GET", f"/products/{product['id']}/impact", actor="dana", project=project["id"])
    assert any(item["id"] == run["id"] for item in impact["affectedRuns"])
