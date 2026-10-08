"""Revalidate the original workspace job at every protected Runtime boundary."""
from types import SimpleNamespace

from ontology_runtime.capability import AuthorizationDenied
from workbench.service import Service, collect_source_deadlines, compare_deadlines
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


def _denied():
    raise AuthorizationDenied()


def key_deadline(record):
    """A key registry record's validity end (`notAfter`, seconds) as a collected
    `(expiresAt_ms, rank, raise_error)` deadline: expiry changes no version, so
    a version fence alone never sees it (execution-capability/1, AUTH-04)."""
    if type(record.get("notAfter")) is not int:
        raise AuthorizationDenied()
    return record["notAfter"] * 1000, 0, _denied


def attempt_deadline(deadline):
    """The execution attempt's deadline (seconds) as a collected deadline."""
    if type(deadline) is not int:
        raise AuthorizationDenied()
    return deadline * 1000, 0, _denied


def key_check(record):
    from ontology_runtime.tools import KEYS
    return {"owner": KEYS, "kind": "ac_key", "id": record["id"], "version": record["version"]}


def commit_execution(ctx, writes, checks, *, guard=None):
    """The Runtime roles can write only their execution partition.

    `writes` may be empty: a delivery that commits nothing (a cached replay)
    still takes its linearization point here, as a check-only transaction over
    every fence. Before every attempt, the source/admission deadlines of
    `checks` and every deadline `guard()` collects with its own later reads
    (key registry validity, the attempt deadline) join ONE aggregate compared
    with a single fresh clock read taken after the last of those reads."""
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

    def before_attempt():
        deadlines = collect_source_deadlines(ctx.storage, observed, ctx.claims)
        if guard is not None:
            deadlines.extend(guard())
        compare_deadlines(ctx.storage, deadlines)  # after every read above

    try:
        return ctx.storage.put_many(writes, checks=observed, retry_conflicts=False, before_attempt=before_attempt)
    except Conflict as error:
        raise CollaborationError(409, "execution-conflict", "실행 또는 원본 권한이 변경되었습니다.") from error
