"""Generate only React composition code against frozen code and product criteria."""
from __future__ import annotations

import base64
import hashlib
import json
import re

from workspace.component_catalog import read_catalog
from workspace.criteria import resolve_generation_context
from workspace.react_artifacts import decode_build_archives, frozen_assets, generated_files, preview_html, read_archive
from workspace.react_quality import apply_visual_policy, react_report_passes
from workspace.rules import CRITERIA_HASHES, CRITERIA_IDS, contract_hash, validate_contract
from workspace.storage import key_for

SYSTEM = """You implement complete React 18 + TypeScript screens using a fixed code-owned design system.
Return ONLY a JSON object {"files":{"src/App.tsx":"...","src/pages/name.tsx":"..."}}.
Allowed model files: src/App.tsx, src/pages/<lowercase-name>.tsx, src/logic/<lowercase-name>.ts.
Do not write package/config/kit/test files or src/logic/assets.ts. The server supplies the latter.
Import named useState/useReducer/useMemo/useCallback and types from react, named components from
'@studio/approved-ui', and relative generated modules only. Use actual controlled component APIs.
No native JSX tags, arbitrary CSS/style/className/ref/raw HTML, dynamic imports, any, direct DOM/network/storage,
manual React elements or component replacement. Use fragments and kit components for all UI.
Assets are data supplied as `import {assets} from './logic/assets'` (adjust relative path in pages).
Use `<AssetImage src={assets["exact-id"]} alt="..."/>`; never invent or download resources.
Screen pageId is a unique fixed lowercase identifier. For proposed required notice pages use the supplied pageId
and testId=pageId, preserve the exact notice copy, and provide reachable navigation matching approved tests.
Keep every approved business rule, target identity and kit API. Input/Checkbox/Select testId refers to its actual control.
Implement real state, validation, agreement gating and propagation into summaries; no fake success labels.
Financial authentication/transactions are local simulation unless an approved kit capability explicitly supplies them.
The code kit, component versions and tests are immutable. Repair only the generated composition/logic.
If a layout variation is requested, change only permitted composition/density/emphasis, not rules or component styling.
Do not quote opaque metadata hashes or add commentary outside the files JSON."""

VARIATIONS = {
    "baseline": "Follow the supplied design and guidelines as closely as the fixed kit permits.",
    "balanced": "Create a coherent new UX under all fixed guidelines.",
    "layout": "Offer a different composition while preserving the same business flow.",
    "dense": "Show information more compactly using only the kit's approved spacing/layout options.",
    "emphasis": "Emphasize the most important information without changing requirements.",
    "flow": "Clarify progress and recovery paths while preserving required transitions.",
    "information": "Improve grouping and information hierarchy within all fixed rules.",
}


def parse_project(text: str) -> dict[str, str]:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n?```", stripped, re.I)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except ValueError as error:
        raise ValueError("React files JSON 형식으로 전체 소스를 반환해야 합니다.") from error
    files = value.get("files") if isinstance(value, dict) else None
    if isinstance(files, list):
        converted = {}
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or item["path"] in converted:
                raise ValueError("중복되거나 올바르지 않은 React 소스 파일입니다.")
            converted[item.get("path")] = item.get("content")
        files = converted
    if (not isinstance(files, dict) or not files or len(files) > 24 or "src/App.tsx" not in files
            or any(not isinstance(name, str) or not isinstance(code, str) for name, code in files.items())
            or len(json.dumps(files, ensure_ascii=False).encode()) > 256_000):
        raise ValueError("src/App.tsx와 제한된 React 소스 파일이 필요합니다.")
    return files


def _failed(reason):
    return {"passed": False, "functionalStatus": "incomplete", "checks": [], "networkRequests": [],
            "consoleErrors": [], "accessibility": {"status": "incomplete"}, "visual": {"status": "not-run"},
            "blockingFindings": [reason], "outputType": "react", "build": {"ok": False, "diagnostics": [reason], "gates": {}}}


def run_react(worker, owner, run, job, lambda_context=None):
    from workspace.worker import _json_bytes, _metadata_aliases, _restore_metadata
    approved = validate_contract(run["contract"])
    if approved["unresolved"] or contract_hash(approved) != run["contractHash"]:
        raise ValueError("확정한 React 생성 기준이 바뀌었습니다.")
    ontology = resolve_generation_context(worker.storage, owner, run, "generate")
    catalog = read_catalog()
    system = SYSTEM + "\nActual code-owned component APIs:\n" + json.dumps(catalog["components"], ensure_ascii=False)
    preferred = (run["referenceAssetId"], run.get("referencePage", 1)) if run.get("referenceAssetId") else None
    context, images, resources, _, warnings, local_files = worker._context(owner, run["assetSnapshots"], preferred)
    for local in local_files.values():
        if local["kind"] == "image" and local["id"] in resources:
            resources[local["id"]] = local["uri"]
    reference = worker._read(owner, run["referenceKey"], run["referenceSha256"]) if run.get("referenceKey") else None
    previous = {}
    if run.get("baseSourceKey"):
        source = worker._read(owner, run["baseSourceKey"], run.get("baseSourceArchiveSha256"))
        project = read_archive(source, run["baseSourceHash"])
        previous = generated_files(project)
        if run.get("baseContractHash") == run["contractHash"]:
            resources = frozen_assets(project)
    worker._update(owner, "run", run["id"], status="running")
    started, rounds = worker.clock(), []
    usage, findings, stop_reason = {"inputTokens": 0, "outputTokens": 0}, None, "max_rounds"
    for number in range(1, run["maxRounds"] + 1):
        if worker.clock() - started > 650 or (lambda_context and lambda_context.get_remaining_time_in_millis() < 390_000):
            stop_reason = "time_cap"
            break
        worker._update(owner, "job", job["id"], progress={"stage": "generate" if number == 1 else "repair", "round": number,
            "percent": 10 + int(75 * (number - 1) / run["maxRounds"]), "message": f"{number}라운드 고정 컴포넌트로 React 소스 생성"})
        prompt_contract = {key: value for key, value in approved.items() if key not in (*CRITERIA_IDS, *CRITERIA_HASHES)}
        user = (f"Published product ontology:\n{ontology['prompt']}\nApproved tests (immutable):\n"
                f"{json.dumps(prompt_contract, ensure_ascii=False)}\n"
                f"Mode: {run.get('generationMode', 'creative')}; direction: {VARIATIONS.get(run.get('variant'), VARIATIONS['balanced'])}\n"
                f"Requested refinement: {run.get('instruction', '')}\nAvailable asset IDs: {json.dumps(list(resources))}\n"
                f"Imported reference material:\n{context}\n")
        if previous:
            user += "\nPrevious generated React source:\n" + json.dumps(previous, ensure_ascii=False)
        if findings:
            user += "\nActual failed build/browser evidence; fix source, not the criteria:\n" + json.dumps(findings, ensure_ascii=False)[:30000]
        identifiers = [asset["id"] for asset in run["assetSnapshots"]] + list(resources) + ontology["identifiers"]
        user, aliases = _metadata_aliases(user, identifiers)
        output, consumed, info = worker.model_call(system, user, images, run["model"], 14000, job["id"], "workspace.react.generate")
        output = _restore_metadata(output, aliases)
        for key in usage:
            usage[key] += int(consumed.get(key, 0))
        files = None
        try:
            files = parse_project(output)
            worker._update(owner, "job", job["id"], progress={"stage": "build_verify", "round": number,
                "percent": 20 + int(75 * (number - 1) / run["maxRounds"]), "message": f"{number}라운드 React 타입·빌드·브라우저 검증"})
            payload = {"kind": "react", "files": files, "assets": resources, "catalogHash": run["catalogHash"],
                       "contract": approved, "visualTolerance": run.get("visualTolerance", 0.15)}
            if reference is not None:
                payload["referenceBase64"] = base64.b64encode(reference).decode()
            report = worker.react_call(payload)
            if not isinstance(report, dict):
                raise RuntimeError("Invalid React verifier response")
        except ValueError as error:
            report = _failed(str(error)[:500])
        except Exception as error:
            report = _failed(f"React 검증 실행 오류: {type(error).__name__}")
            report["engineError"] = True
        build = report.get("build") or {}
        artifact_keys, source_hash, bundle_hash, preview_digest = {}, None, None, None
        prefix = f"rounds/{number}"
        if files:
            candidate_key = key_for(owner, "run", run["id"], prefix + "-candidate.json")
            worker.storage.put_blob_once(candidate_key, _json_bytes(files), "application/json")
            artifact_keys["candidateKey"] = candidate_key
        if build.get("ok") is True:
            try:
                source, dist, project, bundle = decode_build_archives(build)
                html = preview_html(bundle)
                preview_digest = hashlib.sha256(html.encode()).hexdigest()
                source_hash, bundle_hash = build["sourceHash"], build["bundleHash"]
                for name, data, mime in (("source", source, "application/zip"), ("dist", dist, "application/zip"),
                                         ("html", html.encode(), "text/html")):
                    key = key_for(owner, "run", run["id"], prefix + ("-preview.html" if name == "html" else f"-{name}.zip"))
                    worker.storage.put_blob_once(key, data, mime)
                    artifact_keys[name + "Key"] = key
                artifact_keys["sourceArchiveSha256"] = hashlib.sha256(source).hexdigest()
                artifact_keys["distArchiveSha256"] = hashlib.sha256(dist).hexdigest()
            except Exception:
                report["passed"] = False
                report.setdefault("blockingFindings", []).append("React 소스·배포 파일의 동일성을 확인하지 못했습니다.")
        build.pop("projectZipBase64", None)
        build.pop("distZipBase64", None)
        apply_visual_policy(report, run)
        for page in ontology["pages"]:
            if not page["required"]:
                continue
            in_code = any(item.get("pageId") == page["pageId"] for item in build.get("pageSources", []))
            observed = any(step.get("action") == "expectText" and step.get("target") == page["pageId"]
                           and step.get("status") == "pass" and step.get("actualComponent") == "Screen"
                           for check in report.get("checks", []) for step in check.get("steps", []))
            if not in_code or not observed:
                report["passed"] = False
                report.setdefault("blockingFindings", []).append(f"필수 안내 페이지 {page['pageId']}의 실제 Screen 표시·문구 검증이 필요합니다.")
        report.update(outputType="react", artifactSha256=preview_digest, sourceHash=source_hash, bundleHash=bundle_hash,
                      contractHash=run["contractHash"], contractVersion=run["contractVersion"], catalogHash=run["catalogHash"],
                      model=info.get("modelId", run["model"]), contextWarnings=warnings)
        for field, kind in (("screenshotBase64", "screenshot"), ("diffBase64", "diff")):
            encoded = report.pop(field, None)
            if encoded:
                key = key_for(owner, "run", run["id"], f"{prefix}-{kind}.png")
                worker.storage.put_blob_once(key, base64.b64decode(encoded, validate=True), "image/png")
                artifact_keys[kind + "Key"] = key
        if not all(artifact_keys.get(key) for key in ("sourceKey", "distKey", "htmlKey", "screenshotKey")):
            report["passed"] = False
            report.setdefault("blockingFindings", []).append("React 소스·배포 파일·실행 화면 근거가 부족합니다.")
        if report.get("passed") is True and not react_report_passes(approved, report,
                visual_required=reference is not None, visual_policy=run.get("visualPolicy", "exact")):
            report["passed"] = False
            report.setdefault("blockingFindings", []).append("React 필수 코드·동작 검증 근거가 일치하지 않습니다.")
        report_key = key_for(owner, "run", run["id"], prefix + ".json")
        worker.storage.put_blob_once(report_key, _json_bytes(report), "application/json")
        checks = report.get("checks", [])
        row = {"number": number, "passed": report.get("passed") is True, "outputType": "react",
               "artifactSha256": preview_digest, "sourceHash": source_hash, "bundleHash": bundle_hash,
               "catalogHash": run["catalogHash"], "reportKey": report_key, **artifact_keys,
               "build": build, "pageSources": build.get("pageSources", []),
               "functionalStatus": report.get("functionalStatus", "incomplete"),
               "visualStatus": report.get("visual", {}).get("status", "incomplete"),
               "blockingFindings": report.get("blockingFindings", []),
               "checks": {status: sum(check.get("status") == status for check in checks) for status in ("pass", "fail", "incomplete")}}
        rounds.append(row)
        best = max(rounds, key=lambda item: (item["passed"], item["checks"]["pass"], item["number"]))
        worker._update(owner, "run", run["id"], rounds=rounds, bestRound=best["number"], usage=usage,
                       functionalStatus=best["functionalStatus"], visualStatus=best["visualStatus"], contextWarnings=warnings)
        if report.get("engineError"):
            raise ValueError("React 검증 실행기 오류로 중단했습니다. 생성 코드와 미판정 기록은 보존했습니다.")
        if report.get("requiresNewApproval"):
            stop_reason = "criteria_changed"
            break
        if row["passed"]:
            stop_reason = "passed"
            break
        previous = files or previous
        findings = {key: report.get(key) for key in ("build", "blockingFindings", "checks", "accessibility", "visual")}
    if not rounds:
        raise ValueError("시간 상한으로 React 시안을 검증하지 못했습니다.")
    best = max(rounds, key=lambda item: (item["passed"], item["checks"]["pass"], item["number"]))
    worker._update(owner, "run", run["id"], status="completed" if best["passed"] else "needs_changes",
                   bestRound=best["number"], stopReason=stop_reason, elapsedMs=int((worker.clock() - started) * 1000))
    return {"runId": run["id"], "bestRound": best["number"], "passed": best["passed"], "usage": usage}
