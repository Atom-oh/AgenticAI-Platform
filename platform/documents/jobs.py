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
        prior_timeout = job.get("status") == "failed" and job.get("errorCode") == "job-timeout"
        if not active_timeout and not prior_timeout:
            return target
    updates = {"status": "failed", "error": MESSAGE, "errorCode": "job-timeout" if job else "job-unavailable",
               "finishedAt": now}
    if kind == "docrevision":
        updates.update(parseStatus="failed")
    writes = [library.write(kind, {**target, **updates}, target["version"])]
    if job:
        writes.append(library.write("job", {**job, "status": "failed", "stopReason": "timeout",
                      "errorCode": "job-timeout", "error": MESSAGE, "finishedAt": job.get("finishedAt", now)},
                      job["version"]))
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
