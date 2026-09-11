"""Execute a fixed assertion DSL in an isolated browser, never arbitrary tests."""
from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from workspace.rules import validate_contract

RENDER_URL = "https://workspace.invalid/render"


class EngineUnavailable(RuntimeError):
    pass


def _engine_failure(error):
    text = str(error).lower()
    return isinstance(error, EngineUnavailable) or any(fragment in text for fragment in
        ("connection closed", "browser has been closed", "target closed", "browser closed", "driver connection"))


def _phase(value: str):
    path = os.environ.get("STUDIO_TRACE_PATH")
    if path:
        Path(path).write_text(value)


def _isolated_eval(page, expression: str):
    """Trusted instrumentation runs outside the application's JavaScript world."""
    try:
        cached = getattr(page, "_studio_verifier", None)
        if cached is None:
            _phase("cdp-session")
            session = page.context.new_cdp_session(page)
            _phase("frame-tree")
            frame_id = session.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            _phase("isolated-world")
            world = session.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "studio-verifier"})
            cached = (session, world["executionContextId"])
            page._studio_verifier = cached
        session, context_id = cached
        _phase("isolated-evaluate")
        value = session.send("Runtime.evaluate", {"expression": expression,
                             "contextId": context_id, "awaitPromise": True,
                             "returnByValue": True, "timeout": 10000})
    except Exception as error:
        raise EngineUnavailable("브라우저 검증 도구 연결을 확인하지 못했습니다.") from error
    if value.get("exceptionDetails"):
        raise EngineUnavailable("격리된 검증 도구가 실행되지 않았습니다.")
    return value.get("result", {}).get("value")


def _accessibility(page, source: str, result: dict, state: str):
    current = _isolated_eval(page, source + "\n;" + """(async () => {
      await document.fonts.ready;
      const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa']}});
      return {violations:r.violations.map(v=>({id:v.id,impact:v.impact,description:v.description.slice(0,500),
        nodes:v.nodes.slice(0,3).map(n=>({target:n.target.slice(0,3).map(t=>String(t).slice(0,240)),
          failureSummary:String(n.failureSummary||'').slice(0,1200)}))})),
        incomplete:r.incomplete.map(v=>v.id)};
    })()""")
    if not isinstance(current, dict) or "violations" not in current:
        raise EngineUnavailable("접근성 결과를 확인하지 못했습니다.")
    prior = result["accessibility"]
    prior.setdefault("checkedStates", []).append(state)
    prior["totalViolations"] = prior.get("totalViolations", 0) + len(current["violations"])
    room = max(0, 300 - len(prior["violations"]))
    prior["violations"].extend({**violation, "state": state} for violation in current["violations"][:room])
    prior["evidenceTruncated"] = prior["totalViolations"] > len(prior["violations"])
    prior["incomplete"] = list(set(prior.get("incomplete", []) + current["incomplete"]))
    prior["status"] = "fail" if prior["violations"] else "incomplete" if prior["incomplete"] else "pass"


def _visible(page, target: str, bindings) -> bool:
    return bool(_isolated_eval(page, """((input) => {
      const tagged=document.querySelectorAll('[data-testid="'+CSS.escape(input.target)+'"]');
      const nodes=tagged.length===1||!input.selector?tagged:document.querySelectorAll(input.selector);
      if(nodes.length!==1) return false;
      const element=nodes[0], rect=element.getBoundingClientRect();
      if(!rect.width||!rect.height||!element.getClientRects().length) return false;
      for(let node=element;node;node=node.parentElement) {
        const s=getComputedStyle(node);
        if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0) return false;
      }
      return true;
    })(""" + json.dumps({"target": target, "selector": (bindings or {}).get(target)}) + ")"))


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"[:200] if parts.scheme else value[:100]
    except ValueError:
        return "blocked resource"


def _pixel_diff(actual: bytes, reference: bytes, tolerance: float) -> tuple[dict, bytes | None]:
    from PIL import Image, ImageChops, ImageEnhance
    try:
        with Image.open(io.BytesIO(actual)) as image:
            current = image.convert("RGB")
        with Image.open(io.BytesIO(reference)) as image:
            if "A" in image.getbands() and image.getchannel("A").getextrema()[0] != 255:
                return {"status": "incomplete", "reason": "투명 영역이 있는 자산은 전체 화면 비교 기준으로 사용할 수 없습니다. 불투명한 화면 이미지를 선택하세요."}, None
            baseline = image.convert("RGB")
        if current.size != baseline.size:
            return {"status": "incomplete", "reason": "기준 이미지와 실행 화면의 크기가 다릅니다.",
                    "actualSize": list(current.size), "referenceSize": list(baseline.size)}, None
        difference = ImageChops.difference(current, baseline)
        channels = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        channel_threshold = 0 if tolerance == 0 else 3
        mask = maximum.point(lambda value: 255 if value > channel_threshold else 0)
        histogram = mask.histogram()
        ratio = histogram[255] / (current.width * current.height)
        overlay = ImageEnhance.Color(baseline).enhance(0.2)
        overlay.paste(Image.new("RGB", current.size, "#e90061"), mask=mask)
        output = io.BytesIO()
        overlay.save(output, format="PNG")
        return {"status": "pass" if ratio <= tolerance else "fail", "changedRatio": round(ratio, 6),
                "tolerance": tolerance, "pixelChannelThreshold": channel_threshold,
                "width": current.width, "height": current.height}, output.getvalue()
    except Exception:
        return {"status": "incomplete", "reason": "기준 이미지를 비교하지 못했습니다."}, None


def _action(page, step: dict, bindings=None) -> tuple[bool, object]:
    deadline = time.monotonic() + 1.5
    while True:
        target = page.get_by_test_id(step["target"])
        if target.count() != 1 and (bindings or {}).get(step["target"]):
            target = page.locator("css=" + bindings[step["target"]])
        count = target.count()
        if count == 0 and step["action"] == "expectVisible" and step["value"] is False:
            return True, False
        if count == 1:
            break
        if time.monotonic() >= deadline:
            return False, {"matched": count, "reason": "대상 요소가 하나로 식별되지 않습니다."}
        page.wait_for_timeout(50)
    action, value = step["action"], step.get("value")
    if action == "fill":
        target.fill(value, timeout=1500)
    elif action == "click":
        target.click(timeout=1500)
    elif action == "check":
        target.set_checked(value, timeout=1500)
    elif action == "select":
        target.select_option(value, timeout=1500)
    elif action == "press":
        target.wait_for(state="visible", timeout=1500)
        target.press(" " if value == "Space" else value, timeout=1500)
    else:
        # Poll the real DOM for asynchronous UI updates; no caller code is evaluated.
        deadline = time.monotonic() + 1.5
        actual = None
        while True:
            if action != "expectVisible" and not _visible(page, step["target"], bindings):
                actual, passed = "대상 요소가 화면에 보이지 않습니다.", False
            elif action == "expectText":
                actual = target.inner_text(timeout=1500)
                passed = actual == value if step.get("match") == "equals" else value in actual
            elif action == "expectValue":
                actual = target.input_value(timeout=1500)
                passed = actual == value
            elif action == "expectVisible":
                actual = _visible(page, step["target"], bindings)
                passed = actual == value
            elif action == "expectEnabled":
                actual = target.is_enabled(timeout=1500)
                passed = actual == value
            elif action == "expectChecked":
                actual = target.is_checked(timeout=1500)
                passed = actual == value
            elif action == "expectStyle":
                compared = _isolated_eval(page, """((input) => {
                  const tagged=document.querySelectorAll('[data-testid="'+CSS.escape(input.target)+'"]');
                  const nodes=tagged.length===1||!input.selector?tagged:document.querySelectorAll(input.selector);
                  if(nodes.length!==1) return {actual:'대상 연결 변경',expected:''};
                  const element=nodes[0];
                  const probe=document.createElement('span');
                  probe.style[input.property]=input.value;
                  let expected=probe.style[input.property];
                  if(input.property==='fontWeight') expected=({normal:'400',bold:'700'})[expected]||expected;
                  let actual=getComputedStyle(element)[input.property];
                  if(expected&&(input.property==='color'||input.property.endsWith('Color'))) {
                    const painter=document.createElement('canvas').getContext('2d');
                    painter.fillStyle=expected;expected=painter.fillStyle;
                    painter.fillStyle=actual;actual=painter.fillStyle;
                  }
                  return {actual,expected};
                })(""" + json.dumps({"target": step["target"], "selector": (bindings or {}).get(step["target"]),
                                     "property": step["property"], "value": value}) + ")")
                actual = compared["actual"]
                passed = bool(compared["expected"]) and actual == compared["expected"]
            else:
                return False, "지원하지 않는 검증 동작"
            if passed or time.monotonic() >= deadline:
                return passed, actual
            page.wait_for_timeout(50)
    return True, "조작 완료"


def evaluate_html(html: str, contract: dict, reference_png: bytes | None = None,
                  visual_tolerance: float = 0.15) -> dict:
    from playwright.sync_api import sync_playwright
    from studio.artifacts import secure_html

    contract = validate_contract(contract)
    if not isinstance(html, str) or not html.strip() or len(html.encode()) > 1_000_000:
        raise ValueError("실행할 HTML은 비어 있지 않은 1MB 이하의 문서여야 합니다.")
    if isinstance(visual_tolerance, bool) or not 0 <= float(visual_tolerance) <= 0.5:
        raise ValueError("시각 허용 오차는 0~0.5 범위여야 합니다.")
    started = time.monotonic()
    result = {"passed": False, "functionalStatus": "incomplete", "checks": [],
              "networkRequests": [], "consoleErrors": [], "accessibility": {"status": "incomplete", "violations": []},
              "visual": {"status": "not-run"}, "blockingFindings": [], "viewport": contract["viewport"]}
    if contract["unresolved"]:
        result["blockingFindings"] = ["미정의 요구사항이 남아 있습니다."]
        return result
    secured = secure_html(html)
    screenshot = None
    axe_path = Path(os.environ.get("AXE_PATH", str(Path(__file__).parent / "axe.min.js")))
    if not axe_path.is_file():
        local_axe = Path(__file__).parent.parent / "gates/node_modules/axe-core/axe.min.js"
        if local_axe.is_file():
            axe_path = local_axe
    axe_source = axe_path.read_text() if axe_path.is_file() else ""
    environment = {key: os.environ[key] for key in ("PATH", "LD_LIBRARY_PATH", "LANG", "PLAYWRIGHT_BROWSERS_PATH")
                   if key in os.environ}
    environment.update({"TMPDIR": "/tmp", "XDG_CACHE_HOME": "/tmp/studio-browser-cache",
                        "XDG_CONFIG_HOME": "/tmp/studio-browser-config", "XDG_DATA_HOME": "/tmp/studio-browser-data"})
    executable = os.environ.get("WORKSPACE_CHROMIUM_PATH")
    browser = None
    with sync_playwright() as playwright:
        try:
            _phase("launch")
            browser = playwright.chromium.launch(
                headless=True, executable_path=executable or None, env=environment,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking",
                      "--host-resolver-rules=MAP * ~NOTFOUND",
                      "--disable-features=WebRtcHideLocalIpsWithMdns", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
            for rule in contract["rules"]:
                if time.monotonic() - started > 75:
                    result["checks"].append({"caseId": rule["id"], "title": rule["title"], "required": rule["required"],
                                              "status": "incomplete", "steps": [], "reason": "실행 시간 상한"})
                    continue
                context = browser.new_context(viewport=contract["viewport"], device_scale_factor=1,
                                              service_workers="block", accept_downloads=False, offline=True)
                initial = {"used": False}
                def route_request(route):
                    request = route.request
                    if request.url == RENDER_URL and request.is_navigation_request() and not initial["used"]:
                        initial["used"] = True
                        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=secured)
                    result["networkRequests"].append(_safe_url(request.url))
                    route.abort()
                context.route("**/*", route_request)
                def violation(source, data):
                    result["networkRequests"].append(f"CSP {str(data.get('directive', ''))[:80]}: {_safe_url(str(data.get('uri', '')))}")
                context.expose_binding("__studioPolicyViolation", violation)
                context.add_init_script("""(() => {
                  const report = window.__studioPolicyViolation.bind(window);
                  document.addEventListener('securitypolicyviolation', e =>
                    report({directive:e.violatedDirective,uri:e.blockedURI}));
                  for(const key of ['RTCPeerConnection','webkitRTCPeerConnection',
                    'WebSocket','EventSource','Worker','SharedWorker']) {
                    Object.defineProperty(window,key,{configurable:false,writable:false,value:function(){
                      report({directive:'blocked-browser-api',uri:key});
                      throw new Error('외부 통신 API는 검증 환경에서 사용할 수 없습니다.');
                    }});
                  }
                })();""")
                context.on("page", lambda popup: popup.on("download", lambda download: download.cancel()))
                page = context.new_page()
                page.on("popup", lambda popup: (result["networkRequests"].append("popup"), popup.close()))
                page.on("pageerror", lambda error: result["consoleErrors"].append(str(error)[:300]))
                page.on("console", lambda message: result["consoleErrors"].append(message.text[:300])
                        if message.type == "error" else None)
                check = {"caseId": rule["id"], "title": rule["title"], "required": rule["required"],
                         "status": "pass", "steps": []}
                try:
                    _phase("load")
                    page.goto(RENDER_URL, wait_until="load", timeout=10000)
                    page.wait_for_timeout(100)
                    if screenshot is None:
                        screenshot = page.screenshot(type="png", timeout=5000)
                    if axe_source and not result["accessibility"].get("checkedStates"):
                        _accessibility(page, axe_source, result, "initial")
                    for index, step in enumerate(rule["steps"]):
                        _phase("interaction")
                        if time.monotonic() - started > 80:
                            check["status"] = "incomplete"
                            check["reason"] = "실행 시간 상한"
                            break
                        try:
                            passed, actual = _action(page, step, contract["bindings"])
                            item = {"index": index, "action": step["action"], "target": step["target"],
                                    "targetLabel": step["targetLabel"], "expected": step.get("value"), "property": step.get("property"),
                                    "actual": actual if not isinstance(actual, str) else actual[:2000],
                                    "status": "pass" if passed else "fail"}
                        except Exception as error:
                            if _engine_failure(error):
                                result["engineError"] = True
                            item = {"index": index, "action": step["action"], "target": step["target"],
                                    "targetLabel": step["targetLabel"], "expected": step.get("value"),
                                    "actual": type(error).__name__, "status": "fail"}
                        check["steps"].append(item)
                        if item["status"] != "pass":
                            check["status"] = "fail"
                            break
                    if (urlsplit(page.url).scheme, urlsplit(page.url).netloc) != ("https", "workspace.invalid"):
                        result["networkRequests"].append("document navigation")
                    if axe_source:
                        _accessibility(page, axe_source, result, rule["id"] + ":final")
                    check["visibleText"] = page.locator("body").inner_text(timeout=1000)[:4000]
                except Exception as error:
                    if _engine_failure(error):
                        result["engineError"] = True
                    check.update(status="incomplete", reason=f"브라우저 검증 오류: {type(error).__name__}")
                finally:
                    result["checks"].append(check)
                    context.close()
        except Exception as error:
            result["engineError"] = True
            result["blockingFindings"].append(f"브라우저 실행 실패: {type(error).__name__}")
        finally:
            if browser is not None:
                browser.close()
    result["networkRequests"] = list(dict.fromkeys(result["networkRequests"]))[:30]
    result["consoleErrors"] = list(dict.fromkeys(result["consoleErrors"]))[:30]
    required = [check for check in result["checks"] if check["required"]]
    incomplete = len(result["checks"]) != len(contract["rules"]) or any(c["status"] == "incomplete" for c in result["checks"])
    result["functionalStatus"] = ("incomplete" if incomplete or not required else
                                  "pass" if all(c["status"] == "pass" for c in required) else "fail")
    if screenshot:
        result["screenshotBase64"] = base64.b64encode(screenshot).decode()
        if reference_png is not None:
            result["visual"], diff = _pixel_diff(screenshot, reference_png, float(visual_tolerance))
            if diff:
                result["diffBase64"] = base64.b64encode(diff).decode()
    elif reference_png is not None:
        result["visual"] = {"status": "incomplete", "reason": "화면 캡처가 없습니다."}
    if result["functionalStatus"] != "pass":
        result["blockingFindings"].append("필수 동작 확인 실패 또는 미실행")
    if result["networkRequests"]:
        result["blockingFindings"].append("외부 요청 또는 허용되지 않은 문서 이동")
    if result["consoleErrors"]:
        result["blockingFindings"].append("브라우저 오류")
    if result["accessibility"]["status"] != "pass":
        result["blockingFindings"].append("접근성 검사 실패 또는 미판정")
    if result["visual"]["status"] in ("fail", "incomplete"):
        result["blockingFindings"].append("시각 비교 실패 또는 미판정")
    result["passed"] = not result["blockingFindings"]
    result["elapsedMs"] = int((time.monotonic() - started) * 1000)
    _phase("done")
    return result
