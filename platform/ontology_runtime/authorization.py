"""Revalidate the original workspace job at every protected Runtime boundary."""
from types import SimpleNamespace

from ontology_runtime.capability import AuthorizationDenied
from workbench.service import Service, check_source_deadlines
from workspace.collaboration import CollaborationError
from workspace.storage import Conflict
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources


def active_job(storage, collaboration, project_id, artifact_id, *, deadline=None):
    owner = "project:" + schema._identifier(project_id)
    artifact = storage.get(owner, "wb_artifact", schema._identifier(artifact_id))
    if not artifact or artifact.get("kind") != "ontology-analysis" or artifact.get("status") != "queued":
        raise AuthorizationDenied()
    pinned = artifact.get("jobInput", {})
    job = storage.get(owner, "job", artifact["jobId"])
    if (pinned.get("projectId") != project_id or pinned.get("artifactId") != artifact_id
            or pinned.get("operation") != "ontology-analyze" or not job
            or job.get("status") != "running" or job.get("task") != "workbench"
            or job.get("input") != pinned):
        raise AuthorizationDenied()
    expiry = pinned.get("authorizationExpiresAt")
    if type(expiry) is not int:
        raise AuthorizationDenied()
    expiry //= 1000
    if deadline is not None:
        if type(deadline) is not int:
            raise AuthorizationDenied()
        expiry = min(expiry, deadline)
    scope = collaboration.resolve_scope(pinned["actor"], project_id)
    ctx = Service(SimpleNamespace(storage=storage, collaboration=collaboration),
                  scope, {"sub": pinned["actor"], "exp": expiry})
    sources = Sources(ctx)
    if pinned.get("authorityHash") != schema.digest(list(sources.authority)):
        raise AuthorizationDenied()
    checks = [ctx.check("wb_artifact", artifact), ctx.check("job", job)]
    return ctx, artifact, job, sources, checks


def commit_execution(ctx, writes, checks):
    """The Runtime roles can write only their execution partition."""
    if any(write["owner"] != "ontology-executions" or write["kind"] not in {"ac_execution", "ac_operation"}
           for write in writes):
        raise AuthorizationDenied()
    project = ctx.scope["project"]
    checks = [*checks, ctx.check("project", project)]
    unique = {}
    for check in checks:
        key = check["owner"], check["kind"], check["id"]
        if key in unique and unique[key] != check:
            raise AuthorizationDenied()
        unique[key] = check
    observed = list(unique.values())
    if len(writes) + len(observed) > 100:
        raise AuthorizationDenied()
    try:
        return ctx.storage.put_many(writes, checks=observed, retry_conflicts=False,
            before_attempt=lambda: check_source_deadlines(ctx.storage, observed, ctx.claims))
    except Conflict as error:
        raise CollaborationError(409, "execution-conflict", "실행 또는 원본 권한이 변경되었습니다.") from error
