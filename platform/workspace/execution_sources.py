"""Current admission and prior lineage for the production execution ledger."""
from __future__ import annotations

import hashlib
import json

from intake import admission, collection
from workspace import ontology_schema as schema
from workspace.collaboration import Collaboration
from workspace.ontology_sources import PROJECT_AUDIENCE, Sources
from workbench.service import Service


def context(host, job):
    collaboration = getattr(host, "collaboration", None) or Collaboration(host.storage)
    scope = collaboration.resolve_scope(job["actor"], job["projectId"])
    scope["authorizationExpiresAt"] = job["authorizationExpiresAt"]
    return Service(host, scope, {"sub": job["actor"], "exp": job["authorizationExpiresAt"] // 1000})


def unique(checks):
    result = {}
    for check in checks:
        identity = check["owner"], check["kind"], check["id"]
        if identity in result and result[identity] != check:
            raise ValueError("Source authority changed")
        result[identity] = dict(check)
    return [result[key] for key in sorted(result)]


def reference(decision):
    return {"sourceKind": "admitted-code", "sourceId": decision["id"], "revision": str(decision["revision"]),
            "sha256": decision["artifact"]["sha256"], "audienceRevision": PROJECT_AUDIENCE}


class ExecutionSources:
    def __init__(self, host):
        self.host = host

    def collection(self, ctx, identifier):
        decision, raw, authority = admission.authorized(self.host, ctx.scope, identifier, claims=ctx.claims)
        if decision["artifact"]["kind"] != "code-collection":
            raise admission.AdmissionError("collection-required")
        collection.check_resolver(self.host, ctx.scope, decision, authority, json.loads(raw))
        payload = collection.analyzer_request(self.host, ctx.scope, identifier, claims=ctx.claims)
        fences, deadlines = authority.collect_deadlines()
        checks = unique([*authority.checks, *fences])
        expiry = min([ctx.scope["authorizationExpiresAt"], *[value for value, _ in deadlines]])
        if self.host.storage.clock() >= expiry:
            raise admission.AdmissionError("authorization-expired", 401)
        return decision, payload, checks, expiry

    def __call__(self, owner, binding, *, job):
        ctx = context(self.host, job)
        if owner != ctx.owner:
            raise ValueError("Foreign execution")
        decision, _, checks, expiry = self.collection(ctx, binding["decisionId"])
        if (str(decision["revision"]) != binding["revision"]
                or decision["artifact"]["sha256"] != binding["artifactHash"]):
            raise admission.AdmissionError("decision-not-current")
        primary = next(row for row in checks if row["kind"] == "adm_decision" and row["id"] == decision["id"])
        return {"key": decision["artifact"]["key"], "sha256": decision["artifact"]["sha256"],
                "check": primary, "checks": [row for row in checks if row != primary], "expiresAt": expiry}

    def prior(self, owner, job, prior):
        ctx = context(self.host, job)
        if ctx.owner != owner or prior.get("sourceKind") not in {"run-round", "release"}:
            raise ValueError("Unsupported prior")
        reader = Sources(ctx)
        release = None
        if prior["sourceKind"] == "release":
            release = ctx.get("release", prior["sourceId"])
            if release.get("status") != "ready" or str(release["version"]) != prior["revision"]:
                raise ValueError("Stale release")
            run = ctx.get("run", release["runId"])
            number = str(release["round"])
        else:
            run, number = ctx.get("run", prior["sourceId"]), prior["revision"]
        rows = [row for row in run.get("rounds", []) if str(row.get("number")) == number]
        if len(rows) != 1:
            raise ValueError("Unavailable round")
        ref = {"sourceKind": "run-round", "sourceId": run["id"], "revision": number,
               "sha256": rows[0]["sourceHash"], "audienceRevision": PROJECT_AUDIENCE}
        row = reader.resolve(ref)["record"]
        key = release.get("sourceKey") if release else row.get("sourceKey")
        if key != prior.get("key") or not self.host.storage.owns_key(owner, key):
            raise ValueError("Foreign prior bytes")
        raw = self.host.storage.get_blob(key)
        if hashlib.sha256(raw).hexdigest() != prior.get("sha256"):
            raise ValueError("Prior hash changed")
        if release:
            from workspace.releases import approval_hash
            if release.get("sourceHash") != row["sourceHash"] or release.get("approvalHash") != approval_hash(run.get("approval")):
                raise ValueError("Release approval changed")
        from workspace.react_artifacts import read_archive
        read_archive(raw, row["sourceHash"])
        checks, deadlines = reader.recheck_deadlines()
        primary = ctx.check("release", release) if release else ctx.check("run", run)
        checks = unique([primary, *checks])
        expiry = min([job["authorizationExpiresAt"], *[bound for bound, _ in deadlines]])
        if self.host.storage.clock() >= expiry:
            raise ValueError("Prior authority expired")
        return {**primary, "checks": [check for check in checks if check != primary], "expiresAt": expiry}
