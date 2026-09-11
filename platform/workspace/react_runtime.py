"""Trusted React compilation and exact-bundle verification in the isolated child."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from workspace.browser import _phase, evaluate_bundle

KIT_ROOT = Path(__file__).resolve().parents[1] / "react-kit"


def _incomplete(reason, *, engine_error=False):
    result = {"passed": False, "outputType": "react", "functionalStatus": "incomplete", "checks": [],
              "networkRequests": [], "consoleErrors": [], "accessibility": {"status": "incomplete", "violations": []},
              "visual": {"status": "not-run"}, "blockingFindings": [reason]}
    if engine_error:
        result["engineError"] = True
    return result


def _node():
    executable = shutil.which("node")
    if executable:
        return executable
    import playwright
    candidate = Path(playwright.__file__).parent / "driver" / "node"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("Node build runtime is unavailable")


def evaluate_react(event: dict) -> dict:
    started = time.monotonic()
    request = {"files": event.get("files"), "assets": event.get("assets", {}),
               "expectedCatalogHash": event.get("catalogHash"), "contract": event.get("contract", {})}
    encoded = json.dumps(request, ensure_ascii=False)
    if len(encoded.encode()) > 4_500_000:
        return _incomplete("React 소스·자산 요청이 전송 한도를 초과했습니다.")
    try:
        with tempfile.TemporaryDirectory(prefix="studio-react-") as directory:
            input_path, output_path = Path(directory) / "input.json", Path(directory) / "output.json"
            input_path.write_text(encoded)
            environment = {key: os.environ[key] for key in
                           ("PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "PLAYWRIGHT_BROWSERS_PATH", "STUDIO_BROWSER_RUN")
                           if key in os.environ}
            environment.update(TMPDIR=directory)
            _phase("compile")
            process = subprocess.run(
                [_node(), "--max-old-space-size=512", str(KIT_ROOT / "compile.cjs"), str(input_path), str(output_path)],
                env=environment, cwd=KIT_ROOT, timeout=25, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if process.returncode or not output_path.is_file():
                return _incomplete("고정된 React 빌드 도구를 실행하지 못했습니다.", engine_error=True)
            if output_path.stat().st_size > 32_000_000:
                return _incomplete("React 빌드 응답이 처리 한도를 초과했습니다.", engine_error=True)
            build = json.loads(output_path.read_text())
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return _incomplete("React 빌드 실행 환경 오류 또는 제한 시간 초과입니다.", engine_error=True)
    if not isinstance(build, dict) or not isinstance(build.get("gates"), dict):
        return _incomplete("React 빌드 증거 형식이 올바르지 않습니다.", engine_error=True)
    build_elapsed = int((time.monotonic() - started) * 1000)
    if build.get("ok") is not True:
        result = _incomplete("React 코드 규격·타입·빌드 검사를 통과하지 못했습니다.")
        result["build"] = build
        if build["gates"].get("components", {}).get("status") == "fail":
            result["requiresNewApproval"] = True
        return result
    try:
        files = {name: base64.b64decode(data, validate=True) for name, data in build["files"].items()}
        reference = event.get("referenceBase64")
        result = evaluate_bundle(files, event.get("contract", {}),
                                 base64.b64decode(reference, validate=True) if reference else None,
                                 event.get("visualTolerance", 0.15), expected_hash=build["bundleHash"])
    except (ValueError, KeyError, TypeError):
        result = _incomplete("React 빌드 파일·기준 이미지·규칙의 검증 대상을 확인하지 못했습니다.")
    # The worker can derive a preview from the exact dist archive. Do not send
    # the same source/image bytes in four separate fields over Lambda RPC.
    result["build"] = {key: value for key, value in build.items() if key not in ("files", "previewHtml", "sourceFiles")}
    result["outputType"] = "react"
    result["buildElapsedMs"] = build_elapsed
    result["elapsedMs"] = int((time.monotonic() - started) * 1000)
    return result
