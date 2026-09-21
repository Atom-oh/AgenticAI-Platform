"""Workspace worker entry point; the parent owns job completion/failure status."""
from __future__ import annotations

from workspace.collaboration import Collaboration, CollaborationError
from workbench.service import Service, fail


def process(worker, owner, job):
    try:
        if not isinstance(job, dict) or job.get("task") != "workbench" or not isinstance(job.get("input"), dict):
            fail(400, "invalid-job", "올바른 workbench 작업이 아닙니다.")
        pinned = job["input"]
        operation = pinned.get("operation", "")
        if operation not in {"index", "skill-propose", "skill-validate", "skill-execute", "ontology-analyze"}:
            from workbench import business
            handler = getattr(business, "process", None)
            if not callable(handler):
                fail(400, "unsupported-operation", "지원하지 않는 workbench 작업입니다.")
            return handler(worker, owner, job)
        actor, project_id = pinned.get("actor"), pinned.get("projectId")
        expiry = pinned.get("authorizationExpiresAt")
        if type(expiry) is not int or expiry <= worker.storage.clock():
            fail(401, "authorization-expired", "작업 인증이 만료되었습니다.")
        collab = getattr(worker, "collaboration", None) or Collaboration(worker.storage)
        scope = collab.resolve_scope(actor, project_id)
        if scope["owner"] != owner or not scope.get("project"):
            fail(403, "forbidden", "다른 프로젝트의 작업입니다.")
        claims = {"sub": actor, "exp": expiry // 1000,
                  "cognito:groups": ["platform-operators"] if pinned.get("operator") is True else []}
        ctx = Service(worker, scope, claims)
        if operation == "ontology-analyze":
            from workspace.ontology_jobs import process
            return process(ctx, pinned)
        from workbench import knowledge, skills
        for source_id, binding in pinned.get("sourceVersions", {}).items():
            source = ctx.get("wb_source", source_id)
            if not knowledge.source_current(ctx, source, binding):
                fail(409, "source-changed", "작업의 소스 버전 또는 권한이 변경되었습니다.")
        if operation == "index":
            return knowledge.publish_batch(ctx, pinned["batchId"], pinned)
        if operation == "skill-validate":
            return skills.process_validation(ctx, pinned)
        if operation == "skill-execute":
            return skills.process_execution(ctx, pinned)
        return skills.process_proposal(ctx, pinned)
    except CollaborationError as error:
        _mark_failed(worker, owner, job, error.code)
        raise
    except Exception:
        # Private SDK, parser, model and connector failures must not leak payloads.
        _mark_failed(worker, owner, job, "workbench-processing-failed")
        fail(503, "workbench-processing-failed", "Workbench 작업을 완료하지 못했습니다.")


def _mark_failed(worker, owner, job, code):
    """System recovery records only failure metadata, even for a revoked actor.

    It cannot publish or restore source access. Match the stored target/job link
    and fence the current project; a failure from an older batch cannot change a
    newer source. The parent's existing machinery owns the job terminal status.
    """
    if not isinstance(job, dict) or not isinstance(job.get("input"), dict):
        return
    data = job["input"]
    operation = data.get("operation")
    kind = {"index": "wb_batch", "skill-propose": "wb_skill", "skill-validate": "wb_skill", "ontology-analyze": "wb_artifact",
            "skill-execute": "wb_artifact"}.get(operation)
    if not kind:
        return
    try:
        identifier = (data.get("batchId") if operation == "index" else data.get("artifactId")
                      if operation in {"skill-execute", "ontology-analyze"} else data.get("skillId"))
        current = worker.storage.get(owner, kind, identifier)
        if (not current or current.get("jobId") != job.get("id")
                or current.get("status") in {"completed", "DRAFT", "APPROVED", "DEPRECATED"}):
            return
        collab = getattr(worker, "collaboration", None) or Collaboration(worker.storage)
        project_id = current["projectId"]
        if owner != "project:" + project_id:
            return
        project = worker.storage.get(owner, "project", project_id)
        if not project:
            return
        scope = {"owner": owner, "actor": data.get("actor"), "project": project}
        writes = [collab._write(owner, kind, {**current,
            "status": "FAILED" if kind == "wb_skill" else "failed",
            "error": "작업을 완료하지 못했습니다. 입력과 현재 권한을 확인하세요.", "errorCode": code}, current["version"])]
        if kind == "wb_batch":
            source = worker.storage.get(owner, "wb_source", current["sourceId"])
            if source and source["sourceVersion"] == current["sourceVersion"]:
                writes.append(collab._write(owner, "wb_source", {**source, "status": "failed"}, source["version"]))
        collab._commit(scope, writes)
    except Exception:
        # A racing update must win. Readers also derive a failed batch state from
        # its parent job, so a conflict does not turn into apparent success.
        return
