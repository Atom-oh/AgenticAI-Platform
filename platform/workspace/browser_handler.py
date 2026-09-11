"""Browser-only Lambda: no application services, AWS clients or network access."""
import base64
import json

from workspace.browser import evaluate_html


def handler(event, context):
    try:
        reference = event.get("referenceBase64")
        if reference and len(reference) > 4_000_000:
            raise ValueError("기준 이미지가 너무 큽니다.")
        result = evaluate_html(event.get("html", ""), event.get("contract", {}),
                               base64.b64decode(reference, validate=True) if reference else None,
                               event.get("visualTolerance", 0.15))
        if len(json.dumps(result, ensure_ascii=False).encode()) > 5_000_000:
            result.pop("screenshotBase64", None)
            result.pop("diffBase64", None)
            result.update(passed=False)
            result["blockingFindings"].append("화면 증거가 응답 한도를 초과했습니다.")
            if len(json.dumps(result, ensure_ascii=False).encode()) > 5_000_000:
                raise ValueError("검증 근거가 응답 한도를 초과했습니다.")
        return result
    except Exception as error:
        return {"passed": False, "functionalStatus": "incomplete", "checks": [],
                "accessibility": {"status": "incomplete", "violations": []},
                "visual": {"status": "not-run"}, "networkRequests": [], "consoleErrors": [],
                "blockingFindings": [f"검증 실행 오류: {type(error).__name__}"]}
