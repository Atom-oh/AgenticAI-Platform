"""Rebuild exact approved React sources before creating deployable releases."""
from __future__ import annotations

import base64
import hashlib
import json

from workspace.criteria import resolve_generation_context
from workspace.react_artifacts import decode_build_archives, frozen_assets, generated_files, read_archive
from workspace.react_quality import react_report_passes
from workspace.rules import contract_hash
from workspace.storage import Conflict, key_for


def approval_hash(approval):
    return hashlib.sha256(json.dumps(approval, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approved_artifacts(storage, owner, run, number, expected_approval=None):
    row = next((item for item in run.get("rounds", []) if item.get("number") == number), None)
    approval = run.get("approval") or {}
    contract = storage.get(owner, "contract", run.get("contractId"))
    if (run.get("outputType") != "react" or not row or row.get("passed") is not True
            or approval.get("round") != number or not contract or contract.get("status") != "approved"
            or contract["version"] != run["contractVersion"]
            or (contract.get("approval") or {}).get("hash") != run["contractHash"]
            or contract_hash(run["contract"]) != run["contractHash"]):
        raise ValueError("현재 승인된 React 라운드와 규칙이 필요합니다.")
    for field in ("sourceHash", "bundleHash", "catalogHash"):
        if not row.get(field) or approval.get(field) != row[field]:
            raise ValueError("승인한 React 소스·배포 파일·컴포넌트 해시가 일치하지 않습니다.")
    if expected_approval and approval_hash(approval) != expected_approval:
        raise ValueError("승인 기록이 변경되었습니다. 현재 승인본으로 다시 준비하세요.")
    contents = {}
    for kind in ("source", "dist", "report", "screenshot"):
        key = row.get(kind + "Key")
        if not key or not storage.owns_key(owner, key):
            raise ValueError("승인 산출물의 비공개 경로를 확인하지 못했습니다.")
        if storage.blob_info(key)["size"] > 8_000_000:
            raise ValueError("승인 산출물이 처리 한도를 초과했습니다.")
        contents[kind] = storage.get_blob(key)
    for kind in ("source", "dist"):
        if hashlib.sha256(contents[kind]).hexdigest() != row.get(kind + "ArchiveSha256"):
            raise ValueError("승인 산출물 파일이 변경되었습니다.")
    source = read_archive(contents["source"], row["sourceHash"])
    read_archive(contents["dist"], row["bundleHash"])
    report = json.loads(contents["report"])
    if (not react_report_passes(run["contract"], report, visual_required=bool(run.get("referenceAssetId")),
                                visual_policy=run.get("visualPolicy", "exact"))
            or report.get("sourceHash") != row["sourceHash"] or report.get("bundleHash") != row["bundleHash"]
            or report.get("contractHash") != run["contractHash"]):
        raise ValueError("승인된 React 검수 보고서가 일치하지 않습니다.")
    if report.get("visual", {}).get("status") == "review-required" and approval.get("acceptedVariation") is not True:
        raise ValueError("화면 변형 범위의 UX 확인이 필요합니다.")
    return row, source, contents


def create_release(api, owner, body, scope):
    from workspace.http import HTTPError, _integer, _json
    number = _integer(body.get("round"), "Round", 1, 5)
    run = api._get(owner, "run", body.get("runId"))
    api._criteria(owner, {}, scope, api._get(owner, "contract", run["contractId"]))
    try:
        row, _, _ = approved_artifacts(api.storage, owner, run, number)
    except ValueError as error:
        raise HTTPError(409, "release-approval-required", str(error)) from error
    identifier = api._request_id(body, "release")
    data = {"runId": run["id"], "round": number, "actor": scope["actor"], "sourceHash": row["sourceHash"],
            "bundleHash": row["bundleHash"], "catalogHash": row["catalogHash"], "contractHash": run["contractHash"],
            "contractVersion": run["contractVersion"], "approvalHash": approval_hash(run["approval"]),
            "approval": run["approval"], "outputType": "react"}
    for key in ("projectId", "productId", "guidelineId", "ontologyHash"):
        if key in run:
            data[key] = run[key]
    fingerprint = api._fingerprint(data)
    release = api.storage.get(owner, "release", identifier)
    if release and release.get("requestHash") != fingerprint:
        raise HTTPError(409, "request-changed", "이 릴리스 요청의 승인본이 변경되었습니다.")
    if release is None:
        api._worker_ready()
        try:
            release = api.storage.put(owner, "release", {**data, "id": identifier, "status": "queued",
                                                        "requestHash": fingerprint, "jobId": identifier})
        except Conflict:
            release = api._get(owner, "release", identifier)
            if release.get("requestHash") != fingerprint:
                raise HTTPError(409, "request-changed", "릴리스 요청이 변경되었습니다.")
    job = api._existing_job(owner, identifier, fingerprint)
    if not job:
        job = api._new_job(owner, identifier, "release", {"releaseId": identifier}, fingerprint)
    job = api._retry_dispatch(owner, job)
    api._invoke(owner, job)
    return _json(202, {"release": api._get(owner, "release", identifier), "job": job})


def process_release(worker, owner, job):
    from workspace.worker import _json_bytes
    release = worker.storage.get(owner, "release", job["input"]["releaseId"])
    if not release or release.get("status") != "queued":
        raise ValueError("처리할 React 릴리스가 없습니다.")
    run = worker.storage.get(owner, "run", release["runId"])
    resolve_generation_context(worker.storage, owner, {**run, "actor": release["actor"]}, "release")
    row, project, original = approved_artifacts(worker.storage, owner, run, release["round"], release["approvalHash"])
    worker._update(owner, "release", release["id"], status="running")
    worker._update(owner, "job", job["id"], progress={"stage": "release", "percent": 20,
                                                    "message": "승인된 React 소스를 재빌드하고 동작·화면 유지 검증"})
    # Compare to the screen the designer actually approved, including creative
    # variants. This is distinct from comparing a variant to the original design.
    report = worker.react_call({"kind": "react", "files": generated_files(project), "assets": frozen_assets(project),
                               "catalogHash": release["catalogHash"], "contract": run["contract"],
                               "referenceBase64": base64.b64encode(original["screenshot"]).decode(),
                               "visualTolerance": 0.02})
    if (not isinstance(report, dict) or not react_report_passes(run["contract"], report, visual_required=True)
            or report["build"].get("sourceHash") != release["sourceHash"]
            or report["build"].get("bundleHash") != release["bundleHash"]):
        key = key_for(owner, "release", release["id"], "failed-rebuild.json")
        if isinstance(report, dict):
            for field in ("projectZipBase64", "distZipBase64"):
                (report.get("build") or {}).pop(field, None)
            report.pop("screenshotBase64", None)
            report.pop("diffBase64", None)
            worker.storage.put_blob_once(key, _json_bytes(report), "application/json")
            worker._update(owner, "release", release["id"], reportKey=key)
        raise ValueError("승인한 React 소스·배포 파일·동작·화면 유지 검증을 통과하지 못했습니다.")
    source, dist, _, bundle = decode_build_archives(report["build"])
    # Recheck both criteria and approval after the build, before publishing a release.
    current_run = worker.storage.get(owner, "run", run["id"])
    resolve_generation_context(worker.storage, owner, {**current_run, "actor": release["actor"]}, "release")
    approved_artifacts(worker.storage, owner, current_run, release["round"], release["approvalHash"])
    saved = {}
    for kind, data in (("source", source), ("dist", dist)):
        key = key_for(owner, "release", release["id"], kind + ".zip")
        worker.storage.put_blob_once(key, data, "application/zip")
        saved[kind + "Key"] = key
    for name, data in bundle.items():
        key = key_for(owner, "release", release["id"], "site/" + name)
        mime = "text/html" if name.endswith(".html") else "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "text/plain"
        worker.storage.put_blob_once(key, data, mime)
    report["build"].pop("projectZipBase64", None)
    report["build"].pop("distZipBase64", None)
    report.pop("screenshotBase64", None)
    report.pop("diffBase64", None)
    manifest = {"schemaVersion": 1, "runId": run["id"], "round": release["round"],
                **{key: release[key] for key in ("sourceHash", "bundleHash", "catalogHash", "contractHash", "approvalHash")},
                "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(bundle.items())],
                "verification": "approved-source-rebuild-and-browser-test", "approvedScreenTolerance": 0.02}
    for kind, data in (("manifest", manifest), ("report", report)):
        key = key_for(owner, "release", release["id"], kind + ".json")
        worker.storage.put_blob_once(key, _json_bytes(data), "application/json")
        saved[kind + "Key"] = key
    worker._update(owner, "release", release["id"], **saved, status="ready", rebuiltAt=worker.storage.clock(),
                   build=report["build"], verification={"functionalStatus": report["functionalStatus"],
                                                       "visual": report["visual"], "accessibility": report["accessibility"]})
    return {"releaseId": release["id"], "status": "ready"}
