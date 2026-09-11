"""Authorized, configured Git exports of an already verified release."""
from __future__ import annotations

import hashlib
import json
import os
import re

from workspace.criteria import resolve_generation_context
from workspace.git_export import GitExporter, GitExportError
from workspace.react_artifacts import read_archive
from workspace.releases import approved_artifacts
from workspace.storage import Conflict

FIELDS = {"id", "label", "provider", "repository", "baseUrl", "webUrl", "baseBranch", "pathPrefix",
          "branchPrefix", "secretArn", "visibility", "barePath"}


def configured_connections():
    entries = json.loads(os.environ.get("WORKSPACE_GIT_CONNECTIONS", "[]"))
    if not isinstance(entries, list) or len(entries) > 20:
        raise ValueError("Git 연결 설정이 올바르지 않습니다.")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - FIELDS or entry.get("id") in result:
            raise ValueError("Git 연결에는 등록된 대상과 자격 증명 참조만 사용할 수 있습니다.")
        if entry.get("provider") == "local":
            raise ValueError("로컬 Git 대상은 운영 환경 연결로 등록할 수 없습니다.")
        if (not isinstance(entry.get("baseUrl"), str) or not entry["baseUrl"].startswith("https://")
                or not isinstance(entry.get("webUrl"), str) or not entry["webUrl"].startswith("https://")
                or not isinstance(entry.get("secretArn"), str)
                or not re.fullmatch(r"arn:[a-z0-9-]+:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+", entry["secretArn"])):
            raise ValueError("명시적인 HTTPS Git 대상과 정확한 Secrets Manager ARN을 등록하세요.")
        GitExporter(entry)  # Validates the registered paths and hosts without making a request.
        result[entry["id"]] = entry
    return result


def connection_hash(connection):
    return hashlib.sha256(json.dumps(connection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_connections(connections):
    return [{"id": item["id"], "label": item.get("label", item["repository"]), "repository": item["repository"],
             "provider": item["provider"], "visibility": item.get("visibility", "unknown"),
             "connectionHash": connection_hash(item)} for item in connections.values()]


def hydrate_release(storage, owner, release):
    result = dict(release)
    identifier = (release.get("git") or {}).get("exportId")
    if identifier:
        exported = storage.get(owner, "gitexport", identifier)
        if exported and exported.get("releaseId") == release["id"]:
            result["git"] = {key: exported[key] for key in
                             ("status", "branch", "baseSha", "commitSha", "commitUrl", "filesUrl", "repository",
                              "connectionId", "sourceHash", "criteriaCurrent", "error", "jobId") if key in exported}
            if "criteriaStatus" in exported:
                result["git"]["criteriaStatus"] = exported["criteriaStatus"]
            result["git"]["exportId"] = identifier
    return result


def secret_token(connection):
    import boto3
    from botocore.config import Config
    arn = connection.get("secretArn")
    if not isinstance(arn, str) or not arn.startswith("arn:") or ":secretsmanager:" not in arn:
        raise ValueError("등록된 Git 자격 증명 참조가 없습니다.")
    client = boto3.client("secretsmanager", config=Config(connect_timeout=3, read_timeout=5,
                                                       retries={"total_max_attempts": 2}))
    value = client.get_secret_value(SecretId=arn)
    text = value.get("SecretString")
    if text is None:
        text = value["SecretBinary"].decode("utf-8")
    if text.lstrip().startswith("{"):
        text = json.loads(text).get("token")
    return text


def create_export(api, owner, release, body, scope):
    from workspace.http import HTTPError, _json
    connections = api.git_connections()
    connection = connections.get(body.get("connectionId"))
    if connection is None:
        raise HTTPError(409, "git-not-configured", "관리자가 등록한 Git 저장소 연결을 선택하세요.")
    digest = connection_hash(connection)
    if body.get("connectionHash") != digest:
        raise HTTPError(409, "git-connection-changed", "Git 대상 설정을 새로 조회하고 확인하세요.")
    if connection.get("visibility", "unknown") in ("public", "unknown") and body.get("confirmPublic") is not True:
        raise HTTPError(409, "git-destination-confirmation", "대상 저장소의 공개 범위를 확인하세요.")
    if release.get("status") != "ready":
        raise HTTPError(409, "release-not-ready", "승인 후 재빌드·검증을 마친 React 릴리스가 필요합니다.")
    run = api._get(owner, "run", release["runId"])
    api._criteria(owner, {}, scope, api._get(owner, "contract", run["contractId"]))
    try:
        approved_artifacts(api.storage, owner, run, release["round"], release["approvalHash"])
    except ValueError as error:
        raise HTTPError(409, "release-approval-changed", str(error)) from error
    identifier = api._request_id(body, "git")
    data = {"releaseId": release["id"], "connectionId": connection["id"], "connectionHash": digest,
            "sourceHash": release["sourceHash"], "actor": scope["actor"],
            **({"projectId": scope["project"]["id"]} if scope.get("project") else {})}
    fingerprint = api._fingerprint(data)
    exported = api.storage.get(owner, "gitexport", identifier)
    if exported and exported.get("requestHash") != fingerprint:
        raise HTTPError(409, "request-changed", "Git 요청의 대상 또는 승인본이 변경되었습니다.")
    if exported is None:
        api._worker_ready()
        for attempt in range(3):
            current = api._get(owner, "release", release["id"])
            record = {**data, "id": identifier, "status": "queued", "requestHash": fingerprint, "jobId": identifier}
            history = list(dict.fromkeys([*current.get("gitExports", []), identifier]))[-100:]
            pending = {"status": "queued", "exportId": identifier, "jobId": identifier, "connectionId": connection["id"]}
            try:
                exported = api.storage.put_many([
                    {"owner": owner, "kind": "gitexport", "item": record, "expected_version": None},
                    {"owner": owner, "kind": "release", "item": {**current, "git": pending, "gitExports": history},
                     "expected_version": current["version"]},
                ])[0]
                break
            except Conflict:
                exported = api.storage.get(owner, "gitexport", identifier)
                if exported:
                    if exported.get("requestHash") != fingerprint:
                        raise HTTPError(409, "request-changed", "Git 요청이 변경되었습니다.")
                    break
                if attempt == 2:
                    raise
    job = api._existing_job(owner, identifier, fingerprint)
    if not job:
        job = api._new_job(owner, identifier, "git", {"exportId": identifier}, fingerprint)
    job = api._retry_dispatch(owner, job)
    api._invoke(owner, job)
    return _json(202, {"export": exported, "release": hydrate_release(api.storage, owner, api._get(owner, "release", release["id"])), "job": job})


def process_export(worker, owner, job):
    exported = worker.storage.get(owner, "gitexport", job["input"]["exportId"])
    if not exported or exported.get("status") != "queued":
        raise ValueError("처리할 Git 내보내기가 없습니다.")
    release = worker.storage.get(owner, "release", exported["releaseId"])
    if not release or release.get("status") != "ready" or release["sourceHash"] != exported["sourceHash"]:
        raise ValueError("검증된 React 릴리스가 일치하지 않습니다.")
    connection = worker.git_connections().get(exported["connectionId"])
    if not connection or connection_hash(connection) != exported["connectionHash"]:
        raise ValueError("Git 대상 설정이 변경되었습니다.")
    run = worker.storage.get(owner, "run", release["runId"])
    resolve_generation_context(worker.storage, owner, {**run, "actor": exported["actor"]}, "export")
    approved_artifacts(worker.storage, owner, run, release["round"], release["approvalHash"])
    source = worker._read(owner, release["sourceKey"])
    files = read_archive(source, release["sourceHash"])
    worker._update(owner, "gitexport", exported["id"], status="running")
    worker._update(owner, "job", job["id"], progress={"stage": "git", "percent": 30,
                                                    "message": "승인된 React 소스를 feature branch에 커밋"})
    exporter = worker.git_exporter_factory(connection, worker.git_token_provider)
    try:
        result = exporter.export_release(release["id"], release["sourceHash"], files,
                                         release.get("productId") or release["runId"],
                                         commit_time=exported["createdAt"] // 1000)
    except GitExportError as error:
        raise ValueError(f"Git 내보내기를 완료하지 못했습니다: {error.code}") from None
    if (not isinstance(result, dict) or result.get("status") != "committed"
            or result.get("sourceHash") != release["sourceHash"] or result.get("connectionId") != connection["id"]
            or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", result.get("commitSha", ""))):
        raise ValueError("Git 커밋 결과를 확인하지 못했습니다.")
    result.update(exportId=exported["id"], criteriaCurrent=None, criteriaStatus="checking")
    worker._update(owner, "gitexport", exported["id"], **result)
    current, criteria_status = True, "current"
    try:
        latest_run = worker.storage.get(owner, "run", run["id"])
        resolve_generation_context(worker.storage, owner, {**latest_run, "actor": exported["actor"]}, "export")
        approved_artifacts(worker.storage, owner, latest_run, release["round"], release["approvalHash"])
    except ValueError:
        current, criteria_status = False, "changed"
    except Exception:
        current, criteria_status = None, "unavailable"
    # A commit that actually happened is retained even when criteria changed
    # while the external API was running. Do not pretend to roll it back.
    result.update(criteriaCurrent=current, criteriaStatus=criteria_status)
    worker._update(owner, "gitexport", exported["id"], **result)
    for _ in range(3):
        latest = worker.storage.get(owner, "release", release["id"])
        if (latest.get("git") or {}).get("exportId") != exported["id"]:
            break
        try:
            worker.storage.put(owner, "release", {**latest, "git": result}, latest["version"])
            break
        except Conflict:
            continue
    return {"releaseId": release["id"], **result}


def exporter_factory(connection, token_provider):
    return GitExporter(connection, token_provider=token_provider)
