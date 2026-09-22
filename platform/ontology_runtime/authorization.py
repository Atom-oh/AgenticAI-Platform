"""Revalidate the original workspace job at every protected Runtime boundary."""
from types import SimpleNamespace

from ontology_runtime.capability import AuthorizationDenied
from workbench.service import Service
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
