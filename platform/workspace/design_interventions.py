"""Source-bound human actions and proposed feedback; never approval authority."""
from __future__ import annotations

import copy
import hashlib
import json

from design_loop.verify_graph import Metrics, failure_checklist, rejection_candidates
from workbench.service import fields, fail, check_source_deadlines
from workspace import ontology_schema as schema
from workspace.execution_sources import unique
from workspace.ontology_sources import run_round_reference

MAX_EVENTS = 200


def append(design, event):
    events = copy.deepcopy(design.get("interventions", []))
    if len(events) >= MAX_EVENTS:
        raise ValueError("design-intervention-limit")
    if any(item["id"] == event["id"] for item in events):
        raise ValueError("design-intervention-conflict")
    events.append(copy.deepcopy(event))
    return {**design, "interventions": events,
            "interventionCoverage": design.get("interventionCoverage", "partial"),
            "interventionStartedAt": design.get("interventionStartedAt", event["at"])}


def admit_edit(storage, owner, design, descriptor, actor, manifest):
    """Called inside B0 admission; a failed transaction records no human action."""
    if not manifest or not storage.owns_key(owner, manifest.get("ref")):
        raise ValueError("design-intervention-manifest")
    raw = storage.get_blob(manifest["ref"])
    if hashlib.sha256(raw).hexdigest() != manifest.get("hash"):
        raise ValueError("design-intervention-manifest")
    value = json.loads(raw)
    if (value.get("operation") != "design.edit" or value.get("designId") != design["id"]
            or value.get("designRevision") != design["revision"]):
        raise ValueError("design-intervention-manifest")
    base = next((item for item in design["variants"]
                 if item["artifactId"] == value.get("base", {}).get("artifactId")), None)
    if not base or any(value["base"].get(key) != base[key] for key in ("sourceHash", "bundleHash")):
        raise ValueError("design-intervention-manifest")
    target = value.get("edit") or {}
    for key in ("screenId", "state", "targetId"):
        schema._identifier(target.get(key))
    from workspace.design_stages import unpacked, knowledge
    from design_loop.composition import walk
    pages = unpacked(value["baseResult"]["generated"]["screens"])
    page = pages.get((target["screenId"], target["state"]))
    node = next((node for _, node, _ in walk(page) if node["id"] == target["targetId"]), None) if page else None
    if node is None:
        raise ValueError("design-edit-scope")
    k = knowledge(value["snapshot"])
    return append(design, {
        "id": schema.identity("human-edit", design["id"], descriptor["id"]),
        "kind": "edit", "artifactId": descriptor["id"], "baseArtifactId": base["artifactId"],
        "actor": actor, "at": storage.clock(), "outcome": "submitted",
        "sourceHash": base["sourceHash"], "bundleHash": base["bundleHash"],
        "admissions": copy.deepcopy(value["admissions"]),
        "scopes": [{**{key: target[key] for key in ("screenId", "state", "targetId")},
                    "screenTitle": k.screens[target["screenId"]]["title"],
                    "targetTitle": k.assets[node["asset"]]["title"]}],
    })


def terminal_edit(design, artifact_id, outcome, *, source_hash=None, bundle_hash=None):
    """Outcome changes once with the terminal transaction; original action remains intact."""
    value = copy.deepcopy(design)
    for event in value.get("interventions", []):
        if event["kind"] == "edit" and event["artifactId"] == artifact_id:
            event["outcome"] = outcome
            if source_hash is not None:
                event["resultSourceHash"] = source_hash
                event["resultBundleHash"] = bundle_hash
    return value


def decision(ctx, design, variant, artifact, kind, *, reason=None, request_id=None, request_hash=None):
    from workspace.design_api import read_object, _object
    result = read_object(ctx, artifact["result"])
    screens = sorted({item["screenId"] for item in result["generated"]["screens"]})
    event = {
        "id": ctx.identity("decision", request_id) if request_id else
              schema.identity("human-approval", design["id"], variant["artifactId"], str(design["version"]), ctx.actor),
        "kind": kind, "artifactId": variant["artifactId"], "actor": ctx.actor, "at": ctx.storage.clock(),
        "sourceHash": variant["sourceHash"], "bundleHash": variant["bundleHash"],
        "scopes": [{"screenId": screen} for screen in screens],
    }
    if kind == "reject":
        reason = schema._text(reason, 1000)
        metrics = Metrics()
        for screen in screens:
            metrics.record("reject", screenId=screen, reason=reason)
        run = ctx.get("run", variant["runId"])
        proposal = rejection_candidates(metrics, project_id=ctx.project_id, source_ref=run_round_reference(run, 1))
        event["feedback"] = _object(ctx, design["id"], "human-feedback", {
            "reason": reason, "proposal": proposal, "authority": "candidate-only",
        })
        event["requestHash"] = request_hash
    try:
        return append(design, event)
    except ValueError as error:
        fail(422, str(error), "개입 이력 한도에 도달했습니다. 이력을 보존하고 새 설계로 작업하세요.")


def reject(ctx, identifier, body):
    from workspace.design_api import HUMAN, ROLES, current_inputs, variant_authority, observe, public
    from workspace.storage import Conflict
    fields(body, {"version", "artifactId", "sourceHash", "bundleHash", "requestId", "reason"})
    ctx.fresh(ROLES["approve"])
    reason = schema._text(body.get("reason"), 1000)
    design = ctx.get("design", identifier)
    admitted, checks, _ = current_inputs(ctx, design)
    variant, artifact, more, expiry = variant_authority(ctx, design, body.get("artifactId"))
    if any(body.get(key) != variant[key] for key in ("sourceHash", "bundleHash")):
        fail(409, "design-review-stale", "검토한 시안의 정확한 버전을 확인하세요.")
    request_hash = schema.digest(body)
    event_id = ctx.identity("decision", body.get("requestId"))
    previous = next((item for item in design.get("interventions", []) if item["id"] == event_id), None)
    if previous:
        if previous.get("requestHash") != request_hash or previous["actor"] != ctx.actor:
            fail(409, "request-changed", "같은 요청 ID에 다른 반려 내용이 지정되었습니다.")
        observe(ctx, unique([*checks, *more]), min(expiry, admitted["expiresAt"]))
        return public(design)
    if design["version"] != body.get("version") or design.get("activeJobId"):
        fail(409, "design-changed", "실행이 끝난 현재 시안을 다시 확인하세요.")
    changed = decision(ctx, design, variant, artifact, "reject", reason=reason,
                       request_id=body["requestId"], request_hash=request_hash)
    approval = design["approvals"].get("ux") or {}
    if approval.get("artifactId") == variant["artifactId"]:
        changed["approvals"] = {key: item for key, item in design["approvals"].items() if key != "ux"}
        changed["status"] = "review"
        changed.pop("frontendAcceptance", None)
    rejected_releases = {item["id"] for item in design.get("releases", [])
                         if item.get("artifactId") == variant["artifactId"]}
    if (changed.get("frontendAcceptance") or {}).get("releaseId") in rejected_releases:
        changed.pop("frontendAcceptance", None)
    writes = [ctx.write("design", changed, design["version"])]
    # A prior run can retain approval after another variant becomes current.
    # Rejecting it must also invalidate that run's releases/export authority.
    run = ctx.get("run", variant["runId"])
    if run.get("approval"):
        writes.append(ctx.write("run", {**run, "approval": None}, run["version"]))
    predicates = unique([*checks, *more, ctx.check("project", ctx.scope["project"])])
    identities = {(write["kind"], write["item"]["id"]): write["expected_version"] for write in writes}
    for check in predicates:
        identity = check["kind"], check["id"]
        if identity in identities and identities[identity] != check["version"]:
            fail(409, "design-changed", "반려할 근거의 버전이 변경되었습니다.")
    predicates = [check for check in predicates if (check["kind"], check["id"]) not in identities]
    bound = min(expiry, admitted["expiresAt"])
    try:
        ctx.storage.put_many(writes, predicates, retry_conflicts=False, _writer=HUMAN,
            before_attempt=lambda: check_source_deadlines(ctx.storage, predicates, ctx.claims, bound))
    except Conflict:
        fail(409, "design-changed", "설계 또는 원본이 변경되었습니다. 다시 조회하세요.")
    return public(ctx.get("design", identifier))


def view(ctx, identifier, artifact_id):
    from workspace.design_api import current_inputs, variant_authority, read_object, observe
    design = ctx.get("design", identifier)
    current_inputs(ctx, design)
    variant, artifact, checks, expiry = variant_authority(ctx, design, artifact_id)
    result = read_object(ctx, artifact["result"])
    metrics, events, proposals = Metrics(), [], []
    editors = ctx.scope["role"] in {"owner", "designer", "developer"}
    for stored in design.get("interventions", []):
        if artifact_id not in (stored["artifactId"], stored.get("baseArtifactId")):
            continue
        if stored["kind"] == "edit" and stored["artifactId"] != artifact_id:
            output = next((item for item in design["variants"] if item["artifactId"] == stored["artifactId"]), None)
            if not editors and (not output or not output["passed"]):
                continue
            if output:
                _, _, more, extra_expiry = variant_authority(ctx, design, output["artifactId"])
                checks = unique([*checks, *more]); expiry = min(expiry, extra_expiry)
            else:
                record = ctx.get("wb_artifact", stored["artifactId"])
                checks = unique([*checks, ctx.check("wb_artifact", record)])
        if stored["kind"] == "edit":
            from workspace.execution_sources import ExecutionSources
            resolver = ExecutionSources(ctx.host)
            for binding in stored["admissions"]:
                current = resolver(ctx.owner, binding, job={
                    "actor": ctx.actor, "projectId": ctx.project_id,
                    "authorizationExpiresAt": ctx.authorization()["authorizationExpiresAt"],
                })
                checks = unique([*checks, current["check"], *current.get("checks", [])])
                expiry = min(expiry, current["expiresAt"])
        event = {key: copy.deepcopy(value) for key, value in stored.items()
                 if key not in {"feedback", "requestHash", "admissions"}}
        reason = None
        if stored["kind"] == "reject":
            feedback = read_object(ctx, stored["feedback"])
            reason = feedback["reason"]
            event["reason"] = reason
            proposals.append({"eventId": stored["id"], **feedback["proposal"]})
        for scope in stored["scopes"]:
            metrics.record(stored["kind"], screenId=scope["screenId"], reason=reason, scope=scope)
        events.append(event)
    history = result.get("validationHistory", [])
    # The same failed-round visibility restriction as the artifact preview.
    visible_history = history if editors else [item for item in history if item.get("verdict") == "pass"]
    from workspace.design_stages import knowledge
    snapshot = read_object(ctx, design["input"])["snapshot"]
    k = knowledge(snapshot)
    observe(ctx, checks, expiry)
    return {
        "artifactId": artifact_id, "sourceHash": variant["sourceHash"], "bundleHash": variant["bundleHash"],
        "coverage": design.get("interventionCoverage", "not-recorded"),
        "screenLabels": {item["screenId"]: k.screens[item["screenId"]]["title"]
                         for item in result["generated"]["screens"]},
        "startedAt": design.get("interventionStartedAt"), "events": events,
        "metrics": metrics.summary(),
        "editRequests": sum(event["kind"] == "edit" for event in events),
        "rejectionCount": sum(event["kind"] == "reject" for event in events),
        "failureChecklist": failure_checklist(visible_history),
        "rejectionCandidates": proposals,
    }
