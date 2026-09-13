"""Fenced terminal-state reconciliation for private asynchronous document work."""
from documents.errors import DocumentError
from documents.library import Library

STALE_MS = 16 * 60_000
MESSAGE = "작업 응답이 만료되어 종료했습니다. 원문 상태를 확인하고 새 요청으로 다시 실행하세요."


def reconcile(host, scope, kind, target, *, library=None, documents=None):
    library = library or Library(host, scope)
    library.fresh()
    if target.get("status") not in ("queued", "running", "processing") or not target.get("jobId"):
        return target
    if documents is None:
        if kind != "docrevision":
            raise DocumentError(409, "job-changed", "분석 권한 확인이 필요합니다.")
        document = library.document(target["documentId"])
        documents = [document]
    job = library.storage.get(library.owner, "job", target["jobId"])
    expected_task, id_field = ("document-analysis", "analysisId") if kind == "docanalysis" else ("document-finalize", "revisionId")
    now = library.storage.clock()
    if job:
        data = job.get("input", {})
        if (job.get("task") != expected_task or data.get(id_field) != target["id"]
                or data.get("projectId") != library.project_id
                or kind == "docanalysis" and data.get("actorId") != target.get("createdBy")
                or kind == "docrevision" and data.get("documentId") != target.get("documentId")):
            raise DocumentError(409, "job-changed", "작업과 대상 버전이 일치하지 않습니다.")
        updated = job.get("updatedAt")
        active_timeout = (job.get("status") in ("queued", "running") and type(updated) is int
                          and now - updated > STALE_MS)
        terminal_failure = job.get("status") == "failed"
        if not active_timeout and not terminal_failure:
            return target
    prior_failure = bool(job and job.get("status") == "failed")
    error_code = (job.get("errorCode") or "job-failed") if prior_failure else ("job-timeout" if job else "job-unavailable")
    message = "작업 실패 기록을 반영했습니다. 원문 상태를 확인하고 새 요청으로 다시 실행하세요." if prior_failure and error_code != "job-timeout" else MESSAGE
    updates = {"status": "failed", "error": message, "errorCode": error_code,
               "finishedAt": now}
    if kind == "docrevision":
        updates.update(parseStatus="failed")
    writes = [library.write(kind, {**target, **updates}, target["version"])]
    if job:
        failed_job = job if prior_failure else {
            **job, "status": "failed", "stopReason": "timeout", "errorCode": "job-timeout",
            "error": MESSAGE, "finishedAt": now,
        }
        writes.append(library.write("job", failed_job, job["version"]))
    checks = library.checks(documents)
    if not job:
        checks.append({"owner": library.owner, "kind": "job", "id": target["jobId"], "version": None})
    # Target/job CAS and current source/project authority are one transaction.
    return library.storage.put_many(writes, checks=checks)[0]


def reconcile_analysis(host, scope, identifier):
    from documents.analysis import authorize_analysis
    authorized = authorize_analysis(host, scope, identifier)
    return reconcile(host, scope, "docanalysis", authorized["analysis"],
                     library=authorized["library"], documents=authorized["documents"])


def expire_raw_job(host, scope, job):
    from documents.library import authorize_job
    authorize_job(host, scope, job)
    data = job["input"]
    if job["task"] == "document-analysis":
        reconcile_analysis(host, scope, data["analysisId"])
    else:
        library = Library(host, scope)
        document = library.document(data["documentId"])
        revision = library.revision(document, data["revisionId"])
        reconcile(host, scope, "docrevision", revision, library=library, documents=[document])
    latest = host.storage.get(scope["owner"], "job", job["id"])
    if not latest:
        raise DocumentError(404, "job-unavailable", "작업 기록이 만료되었습니다.")
    authorize_job(host, scope, latest)
    return latest


def fail_work(host, owner, job, message):
    """Record a worker failure only while its current authority still permits it."""
    from workspace.collaboration import Collaboration
    data = job.get("input", {})
    collaboration = getattr(host, "collaboration", None) or Collaboration(host.storage)
    scope = collaboration.resolve_scope(data.get("actorId"), data.get("projectId"))
    if scope["owner"] != owner:
        raise DocumentError(403, "forbidden", "작업의 문서함 범위가 일치하지 않습니다.")
    if job.get("task") == "document-analysis":
        from documents.analysis import authorize_analysis
        authorized = authorize_analysis(host, scope, data.get("analysisId"))
        library, target, documents = authorized["library"], authorized["analysis"], authorized["documents"]
        kind = "docanalysis"
        if target.get("createdBy") != scope["actor"]:
            raise DocumentError(403, "forbidden", "분석 요청자가 일치하지 않습니다.")
    elif job.get("task") == "document-finalize":
        library = Library(host, scope)
        document = library.document(data.get("documentId"), "edit")
        target = library.revision(document, data.get("revisionId"))
        documents, kind = [document], "docrevision"
    else:
        raise DocumentError(409, "job-changed", "문서 작업이 아닙니다.")
    current = host.storage.get(owner, "job", job["id"])
    if (not current or current.get("input") != data or current.get("task") != job["task"]
            or target.get("jobId") != job["id"]):
        return False
    if current.get("status") == target.get("status") == "failed":
        return True
    if current.get("status") != "running" or target.get("status") not in ("processing", "queued", "running"):
        return False
    now = host.storage.clock()
    update = {"status": "failed", "error": message, "errorCode": "document-job-failed", "finishedAt": now}
    if kind == "docrevision":
        update["parseStatus"] = "failed"
    library.commit([
        library.write("job", {**current, "status": "failed", "error": message,
                             "errorCode": "document-job-failed", "finishedAt": now}, current["version"]),
        library.write(kind, {**target, **update}, target["version"]),
    ], documents)
    return True
