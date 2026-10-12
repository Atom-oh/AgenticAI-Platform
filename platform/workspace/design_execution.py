"""Design admission and completion composition for the single execution ledger."""
from __future__ import annotations

import copy
import hashlib
import json

from workspace import ontology_schema as schema
from workspace.execution_ledger import LedgerError
from workspace.execution_source import encode
from workspace.execution_sources import context, unique

OPERATIONS = {"extract": "design.extract", "generate": "design.generate", "edit": "design.edit",
              "compose": "design.compose", "release": "design.release"}
STATES = {"extract": {"draft", "prd-review", "interrupted"}, "generate": {"ready", "review", "interrupted"},
          "edit": {"review", "approved", "interrupted"}, "compose": {"review", "interrupted"},
          "release": {"approved", "released", "interrupted"}}
ROLES = {"extract": {"owner", "planner", "designer"}, "generate": {"owner", "designer"},
         "edit": {"owner", "designer"}, "compose": {"owner", "designer"},
         "release": {"owner", "designer", "developer"}}


def admission_write(storage, owner, descriptor, actor, operation):
    stage = descriptor.get("stage")
    design = storage.get(owner, "design", descriptor.get("designId"))
    project = storage.get(owner, "project", (design or {}).get("projectId")) if design else None
    role = ((project or {}).get("members") or {}).get(actor, {}).get("role")
    if (not design or owner != "project:" + design["projectId"] or stage not in OPERATIONS
            or operation != OPERATIONS[stage] or role not in ROLES[stage]
            or design["version"] != descriptor["designVersion"] or design["revision"] != descriptor["designRevision"]
            or design.get("activeJobId") or design["status"] not in STATES[stage]):
        raise LedgerError("design-changed")
    if stage != "extract" and not design.get("approvals", {}).get("flow"):
        raise LedgerError("design-flow-required")
    return {"owner": owner, "kind": "design", "expected_version": design["version"],
            "item": {**design, "status": "executing", "activeStage": stage, "error": None, "unknownOutcome": False}}


def authority(storage, owner, job):
    ref = job["artifact"]
    design = storage.get(owner, "design", ref["designId"])
    if not design or design.get("status") in {"cancelled", "archived"}:
        return None
    if "version" not in job:
        ok = design["version"] == ref["designVersion"] and not design.get("activeJobId")
    elif job["status"] in {"succeeded", "needs_changes"}:
        ok = design.get("lastExecutionId") == job["id"] and design["revision"] == ref["designRevision"] + 1
    else:
        ok = design.get("activeJobId") == job["id"] and design["revision"] == ref["designRevision"]
    if not ok:
        return None
    return {"owner": owner, "kind": "design", "id": design["id"], "version": design["version"]}


def submit(ctx, identifier, stage, body, ledger, configuration, enqueue):
    from workspace.design_api import current_inputs, public, read_object
    from workbench.service import fields
    fields(body, {"requestId", "version", "variant", "maxRounds", "instructionDecisionId", "baseArtifactId", "screenId", "state", "targetId", "acknowledgeUnknownOutcome", "selections"})
    if stage not in OPERATIONS:
        raise LedgerError("unknown-operation")
    ctx.fresh(ROLES[stage])
    design = ctx.get("design", identifier)
    settings = configuration.get("design") or {}
    if (ctx.project_id not in settings.get("projects", []) or design["model"] not in settings.get("models", [])):
        raise LedgerError("design-execution-unavailable")
    admitted, checks, stored = current_inputs(ctx, design)
    artifact_id = ctx.identity("wb_artifact", body.get("requestId"))
    existing = ctx.storage.get(ctx.owner, "wb_artifact", artifact_id)
    request_hash = schema.digest({"designId": identifier, "stage": stage, **body})
    if existing:
        if existing.get("requestHash") != request_hash or existing.get("createdBy") != ctx.actor:
            raise LedgerError("request-changed")
        return {"design": public(design), "jobId": existing["jobId"], "artifactId": artifact_id}
    if design["version"] != body.get("version"):
        raise LedgerError("design-changed")
    if design.get("unknownOutcome") and body.get("acknowledgeUnknownOutcome") is not True:
        raise LedgerError("unknown-outcome-ack-required")
    from workspace.storage import MAX_RECORD_BYTES
    # Fixed-size candidate summaries add only a few KiB. Reserve ample headroom
    # before dispatch so accumulated history cannot fail publication after paid work.
    if len(encode(design)) > MAX_RECORD_BYTES - 65536:
        raise LedgerError("design-history-limit")
    variant = body.get("variant", 0)
    rounds = body.get("maxRounds", 3)
    if type(variant) is not int or not 0 <= variant < 5 or type(rounds) is not int or not 1 <= rounds <= 5:
        raise LedgerError("design-limit")
    manifest = {"schemaVersion": 1, "operation": OPERATIONS[stage], "designId": identifier,
                "designRevision": design["revision"], "snapshot": stored["snapshot"], "pages": admitted["pages"],
                "inspection": stored["inspection"], "model": design["model"], "variant": variant,
                "maxRounds": rounds, "tool": configuration["interpreter"],
                "admissions": admitted["admissions"], "sourceChecks": checks,
                "sourceBindings": [{"sourceKind": row["sourceKind"], "sourceId": row["sourceId"],
                    "revision": row["revision"], "audience": row["audienceRevision"]} for row in admitted["sourceRefs"]]}
    if stage != "extract":
        manifest["prd"] = read_object(ctx, design["prd"])["prd"]
        manifest["prepared"] = read_object(ctx, design["flow"])
        manifest["approvals"] = design["approvals"]
    if stage in {"generate", "edit"}:
        from workspace.design_stages import validation_plan
        from workspace.execution_ledger import PROFILE_DEFAULT
        try:
            plan = validation_plan(stored["snapshot"], manifest["prepared"], manifest["prd"], rounds, stage=stage)
        except ValueError as error:
            raise LedgerError(str(error)) from None
        if plan["totalCalls"] > PROFILE_DEFAULT["maxCallsByOperation"][OPERATIONS[stage]]:
            raise LedgerError("call-budget")
        manifest["validationPlan"] = plan
    if stage == "compose":
        from workspace import design_stages as delivery
        from workspace.execution_sources import ExecutionSources
        from workspace.design_api import variant_authority
        try:
            choices = delivery.selected_screens(body.get("selections"), manifest["prepared"]["flow"]["screens"])
        except ValueError as error:
            raise LedgerError(str(error)) from None
        retained = {}
        resolver = ExecutionSources(ctx.host)
        from workspace.execution_ledger import PROFILE_DEFAULT, profile_hash
        expected_profile = profile_hash(PROFILE_DEFAULT)
        for choice in choices:
            identifier_ = choice["artifactId"]
            if identifier_ in retained:
                continue
            base, artifact, observed, expiry = variant_authority(ctx, design, identifier_)
            if not base["passed"] or artifact.get("status") != "completed":
                raise LedgerError("design-compose-review-required")
            # The immutable completed artifact survives job accounting updates and
            # retention. The job row is execution metadata, not source authority.
            if (artifact.get("backendConfigRevision") != configuration["revision"]
                    or artifact.get("profileHash") != expected_profile):
                raise LedgerError("design-profile-changed")
            result = read_object(ctx, artifact["result"])
            if result.get("reviewProfile") != {"model": design["model"], "profileHash": expected_profile}:
                raise LedgerError("design-compose-review-required")
            retained[identifier_] = result
            checks = unique([*checks, *[check for check in observed if check["kind"] != "design"]])
            admitted["expiresAt"] = min(admitted["expiresAt"], expiry)
            for bound in artifact["admissions"]:
                if bound not in manifest["admissions"]:
                    resolved = resolver(ctx.owner, bound, job={"actor": ctx.actor, "projectId": ctx.project_id,
                        "authorizationExpiresAt": ctx.authorization()["authorizationExpiresAt"]})
                    manifest["admissions"].append(bound)
                    checks = unique([*checks, resolved["check"], *resolved.get("checks", [])])
                    admitted["expiresAt"] = min(admitted["expiresAt"], resolved["expiresAt"])
        manifest["selections"] = [{**choice, "result": retained[choice["artifactId"]]} for choice in choices]
    if stage in {"edit", "release"}:
        # Finalized variants must be selected exactly; these are never caller-supplied files.
        base = next((row for row in design["variants"] if row["artifactId"] == body.get("baseArtifactId")), None)
        if not base:
            raise LedgerError("design-variant-required")
        manifest["base"] = base
        from workspace.design_api import variant_authority
        _, _, base_checks, base_expiry = variant_authority(ctx, design, base["artifactId"])
        checks = unique([*checks, *[check for check in base_checks if check["kind"] != "design"]])
        admitted["expiresAt"] = min(admitted["expiresAt"], base_expiry)
        checks = unique([*checks, ctx.check("run", ctx.get("run", base["runId"]))])
        base_artifact = ctx.get("wb_artifact", base["artifactId"])
        manifest["baseResult"] = read_object(ctx, base_artifact["result"])
        base_job = ctx.storage.get(ctx.owner, "job", base["jobId"])
        if not base_job:
            raise LedgerError("design-evidence-unavailable")
        if base_job["backendConfigRevision"] != configuration["revision"]:
            raise LedgerError("design-profile-changed")
        from workspace.execution_sources import ExecutionSources
        resolver = ExecutionSources(ctx.host)
        for bound in base_job["admissions"]:
            if bound not in manifest["admissions"]:
                resolved = resolver(ctx.owner, bound, job={"actor": ctx.actor, "projectId": ctx.project_id,
                    "authorizationExpiresAt": ctx.authorization()["authorizationExpiresAt"]})
                manifest["admissions"].append(bound)
                checks = unique([*checks, resolved["check"], *resolved.get("checks", [])])
                admitted["expiresAt"] = min(admitted["expiresAt"], resolved["expiresAt"])
    if stage == "edit":
        base_browser = read_object(ctx, manifest["baseResult"]["browser"])
        manifest["baseBrowser"] = {"layoutBoxes": base_browser.get("layoutBoxes", {})}
        from intake import admission
        decision, data, authority_ = admission.authorized(ctx.host, ctx.scope, body.get("instructionDecisionId"), claims=ctx.claims)
        if decision["artifact"]["kind"] != "prompt-text":
            raise LedgerError("design-edit-instruction-required")
        bound = {"decisionId": decision["id"], "revision": str(decision["revision"]), "artifactHash": decision["artifact"]["sha256"]}
        if bound not in manifest["admissions"]:manifest["admissions"].append(bound)
        fences, deadlines = authority_.collect_deadlines()
        checks = unique([*checks, *authority_.checks, *fences])
        admitted["expiresAt"] = min([admitted["expiresAt"], *[expiry for expiry, _ in deadlines]])
        manifest["edit"] = {"instruction": json.loads(data)[0]["text"], "screenId": body.get("screenId"),
                            "state": body.get("state", "default"), "targetId": body.get("targetId")}
    if stage == "release":
        approval = design["approvals"].get("ux")
        if not approval or approval.get("artifactId") != base["artifactId"] or not base["passed"]:
            raise LedgerError("design-approval-required")
        screenshot = base["screenshot"]
        import base64
        shot = ctx.storage.get_blob(screenshot["key"])
        if hashlib.sha256(shot).hexdigest() != screenshot["sha256"]:
            raise LedgerError("receipt-invalid")
        manifest["approved"] = {"sourceHash": base["source"]["sha256"], "bundleHash": base["bundle"]["sha256"],
            "screenshot": {"key": screenshot["key"], "sha256": screenshot["sha256"]}, "approval": approval}
        manifest["approvedScreenshot"] = base64.b64encode(shot).decode()
    manifest["sourceChecks"] = checks
    raw = encode(manifest);digest = hashlib.sha256(raw).hexdigest()
    key = ctx.storage.key_for(ctx.owner, "design", identifier, "inputs/" + digest + ".json")
    ctx.storage.put_blob_once(key, raw, "application/json")
    project = ctx.scope["project"]
    job = ledger.api().admit(ctx.owner, request_key=body["requestId"], actor=ctx.actor, project_id=ctx.project_id,
        operation=OPERATIONS[stage], model=design["model"], manifest={"ref": key, "hash": digest},
        admissions=manifest["admissions"], backend_config_revision=configuration["revision"],
        authorization_expires_at=admitted["expiresAt"], project_authority={"authorityRevision": project["authorityRevision"],
            "membershipDigest": schema.digest(project["members"])},
        completion_scope={"sourceChecks": len(checks), "sourceBindings": len(manifest["sourceBindings"]), "nonSourceOperations": 25},
        artifact={"id": artifact_id, "kind": "design-stage", "designId": identifier,
            "designRevision": design["revision"], "designVersion": design["version"], "stage": stage,
            "name": "design-" + stage, "sourceRefs": admitted["sourceRefs"], "requestHash": request_hash})
    enqueue(ctx.owner, job["id"])
    return {"design": public(ctx.get("design", identifier)), "jobId": job["id"], "artifactId": artifact_id}


def verification_summary(value):
    """Bounded metadata only. Full roles/findings/geometry stay in design.json."""
    findings = value.get("findings") or value.get("issues") or []
    summary = {"verdict": value.get("verdict") if value.get("verdict") in ("pass", "fail", "blocked") else "blocked", "approvable": value.get("approvable") is True,
               "findingCount": len(findings),
               "criticalCount": sum(item.get("severity") == "critical" for item in findings)}
    layout = value.get("layout")
    if isinstance(layout, dict):
        summary["layout"] = {"stable": layout.get("stable") is True,
            **{name + "Count": len(layout.get(name, [])) for name in ("moved", "missing", "blocked")}}
    return summary


class DesignPublication:
    def __init__(self, host, configuration):
        self.host, self.configuration = host, configuration

    def __call__(self, prepared):
        from workspace.design_api import current_inputs, _object
        from design_loop.prd_extract import validate_prd
        job = prepared["job"]
        ctx = context(self.host, job)
        descriptor = job["artifact"]
        design = ctx.get("design", descriptor["designId"])
        artifact = ctx.get("wb_artifact", descriptor["id"])
        if (design["activeJobId"] != job["id"] or design["revision"] != descriptor["designRevision"]
                or artifact.get("executionId") != job["id"] or job["backendConfigRevision"] != self.configuration["revision"]):
            raise LedgerError("design-changed")
        admitted, checks, _ = current_inputs(ctx, design)
        stages = [row for row in job["stages"] if row["stage"] == "verify" and row["attemptId"] == job["attempt"]["id"]]
        if not stages:
            raise LedgerError("receipt-invalid")
        output = stages[-1]["result"].get("designResult")
        if output not in stages[-1]["outputs"]:
            raise LedgerError("receipt-invalid")
        raw = ctx.storage.get_blob(output["key"])
        if len(raw) != output["size"] or hashlib.sha256(raw).hexdigest() != output["sha256"]:
            raise LedgerError("receipt-invalid")
        result = json.loads(raw)
        if job["operation"] in {"design.generate", "design.edit"}:
            self._validation_history(ctx, job, result)
        value = {**copy.deepcopy(design), "activeJobId": None, "activeArtifactId": None,
                 "lastExecutionId": job["id"], "revision": design["revision"] + 1,
                 "artifacts": [*design["artifacts"], artifact["id"]]}
        extra_writes = []
        if job["operation"] == "design.extract":
            verified = validate_prd(admitted["pages"], result.get("prd"))
            if verified != {"prd": result.get("prd"), "issues": result.get("issues")}:
                raise LedgerError("receipt-invalid")
            value.update(prd=_object(ctx, design["id"], "prd", verified), status="prd-review", approvals={}, variants=[])
        else:
            value, extra_writes = self._rendered(ctx, job, design, value, result)
        checks = unique([*checks, *getattr(ctx, "_design_checks", [])])
        expiry = min(admitted["expiresAt"], getattr(ctx, "_design_expiry", admitted["expiresAt"]))
        writes = [ctx.write("design", value, design["version"]),
                  ctx.write("wb_artifact", {**artifact, "status": "completed", "result": output,
                      "receiptHashes": job["result"]["receipts"], "admissions": job["admissions"],
                      "backendConfigRevision": job["backendConfigRevision"], "profileHash": job["profile"]["hash"]},
                      artifact["version"]), *extra_writes]
        return {"writes": writes, "checks": checks, "expiresAt": expiry,
                "sourceChecks": job["obligations"]["sourceChecks"], "sourceBindings": job["obligations"]["sourceBindings"]}

    def _validation_history(self, ctx, job, result):
        """Every delivered round points to this attempt's independently recorded outputs."""
        from workspace.design_api import read_object
        history = result.get("validationHistory")
        rows = [row for row in job["stages"] if row["attemptId"] == job["attempt"]["id"]]
        verified = [row for row in rows if row["stage"] == "verify"]
        count = verified[-1]["result"].get("validationRound") if verified else None
        manifest_raw = ctx.storage.get_blob(job["manifest"]["ref"])
        if hashlib.sha256(manifest_raw).hexdigest() != job["manifest"]["hash"]:
            raise LedgerError("receipt-invalid")
        manifest = json.loads(manifest_raw)
        if (not isinstance(history, list) or type(count) is not int or not 1 <= count <= 5
                or len(history) != count or count > manifest.get("maxRounds", 0)
                or len(encode(history)) > 8 * 1024 * 1024):
            raise LedgerError("receipt-invalid")
        for number, entry in enumerate(history, 1):
            if (not isinstance(entry, dict) or entry.get("round") != number
                    or entry.get("verdict") not in {"pass", "fail", "blocked"} or type(entry.get("approvable")) is not bool):
                raise LedgerError("receipt-invalid")
            stages = {row["stage"]: row for row in rows if row["result"].get("validationRound") == number
                      and not (row["stage"] == "verify" and row["result"].get("repairFailure"))}
            for field, stage in (("source", "generate"), ("candidate", "generate"), ("build", "compile")):
                if stage not in stages or entry.get(field) not in stages[stage]["outputs"]:
                    raise LedgerError("receipt-invalid")
                read_object(ctx, entry[field])
            build = read_object(ctx, entry["build"])["build"]
            if entry.get("compileStatus") == "failed":
                if (stages["compile"]["status"] != "failed" or build.get("ok") is not False
                        or entry["verdict"] != "fail" or entry["approvable"] is not False):
                    raise LedgerError("receipt-invalid")
                if any(field in entry for field in ("browser", "largeText", "bundleHash")):
                    raise LedgerError("receipt-invalid")
            elif entry.get("compileStatus") == "pass":
                if stages["compile"]["status"] != "ok" or build.get("ok") is not True:
                    raise LedgerError("receipt-invalid")
                for field in ("browser", "largeText"):
                    if "browser" not in stages or entry.get(field) not in stages["browser"]["outputs"]:
                        raise LedgerError("receipt-invalid")
                    read_object(ctx, entry[field])
                if entry.get("sourceHash") != build["sourceHash"] or entry.get("bundleHash") != build["bundleHash"]:
                    raise LedgerError("receipt-invalid")
                if ("verify" not in stages or stages["verify"]["result"].get("verdict") != entry["verdict"]
                        or stages["verify"]["result"].get("approvable") is not entry["approvable"]):
                    raise LedgerError("receipt-invalid")
                previous = read_object(ctx, stages["verify"]["result"]["designResult"])
                if entry.get("findings") != previous["verification"].get("findings", previous["verification"].get("issues", [])):
                    raise LedgerError("receipt-invalid")
            else:
                raise LedgerError("receipt-invalid")
            if number < count and (entry["verdict"] == "blocked" or (entry["verdict"] == "pass" and entry["approvable"])):
                raise LedgerError("receipt-invalid")
        refused = result.get("repairFailure")
        if refused is not None:
            if refused not in verified[-1]["outputs"] or verified[-1]["result"].get("repairFailure") != refused:
                raise LedgerError("receipt-invalid")
            read_object(ctx, refused)
            if job["status"] == "succeeded" or result["verification"].get("approvable") is not False:
                raise LedgerError("receipt-invalid")

    def _rendered(self, ctx, job, design, value, result):
        from workspace.design_api import read_object
        from workspace.react_artifacts import read_archive, generated_files
        from workspace.react_quality import react_report_passes
        from workspace import design_stages as delivery
        from design_loop import prd_extract, react_project, coverage, verify_graph
        from design_loop.convention import Registry
        from workspace.rules import contract_hash
        stored = read_object(ctx, design["input"])
        prepared = read_object(ctx, design["flow"])
        prd = read_object(ctx, design["prd"])["prd"]
        k = delivery.knowledge(stored["snapshot"])
        candidate = result["generated"]
        if job["operation"] == "design.compose":
            from workspace.design_api import variant_authority
            raw_manifest = ctx.storage.get_blob(job["manifest"]["ref"])
            if hashlib.sha256(raw_manifest).hexdigest() != job["manifest"]["hash"]:
                raise LedgerError("receipt-invalid")
            manifest = json.loads(raw_manifest)
            selected = []
            for row in manifest["selections"]:
                _, artifact, observed, expiry = variant_authority(ctx, design, row["artifactId"])
                original = read_object(ctx, artifact["result"])
                if original != row["result"]:
                    raise LedgerError("receipt-invalid")
                selected.append({**row, "result": original})
            def current_judgment(record):
                return bool(record.get("admissions")) and all(bound in job["admissions"] for bound in record["admissions"])
            try:
                composed, judgments, evidence = delivery.compose(stored["snapshot"], prepared, prd, selected,
                    {"model": job["model"], "profileHash": job["profile"]["hash"]}, current_judgment)
            except ValueError:
                raise LedgerError("design-compose-review-required") from None
            recorded = next(row["result"] for row in job["stages"]
                            if row["stage"] == "verify" and row["attemptId"] == job["attempt"]["id"])
            if (candidate != composed or result.get("judgments") != judgments
                    or result.get("reviewProfile") != {"model": job["model"], "profileHash": job["profile"]["hash"]}
                    or result["verification"].get("judgeEvidence") != evidence
                    or result["verification"].get("compositionHashes") != composed["compositionHashes"]
                    or recorded.get("judgeEvidence") != evidence
                    or recorded.get("compositionHashes") != composed["compositionHashes"]):
                raise LedgerError("receipt-invalid")
        screens = delivery.unpacked(candidate["screens"])
        registry = Registry(k, state=candidate["registry"])
        expected = delivery.source_files(stored["snapshot"], prepared, prd, screens, registry.to_json())
        if candidate["files"] != expected or result["contract"] != prepared["contract"]:
            raise LedgerError("receipt-invalid")
        stages = {row["stage"]: row for row in job["stages"] if row["attemptId"] == job["attempt"]["id"]}
        for field, stage in (("source", "compile"), ("bundle", "compile"), ("build", "compile"),
                             ("browser", "browser"), ("largeText", "browser"), ("screenshot", "browser")):
            if result[field] not in stages[stage]["outputs"]:
                raise LedgerError("receipt-invalid")
        source = ctx.storage.get_blob(result["source"]["key"])
        bundle = ctx.storage.get_blob(result["bundle"]["key"])
        if (hashlib.sha256(source).hexdigest() != result["source"]["sha256"]
                or hashlib.sha256(bundle).hexdigest() != result["bundle"]["sha256"]
                or generated_files(read_archive(source, result["sourceHash"])) != expected):
            raise LedgerError("receipt-invalid")
        read_archive(bundle, result["bundleHash"])
        build_evidence = read_object(ctx, result["build"])
        build = build_evidence["build"]
        cfg = self.configuration["interpreter"]
        observed = build_evidence["execution"]
        if (observed.get("backend") != "agentcore-code-interpreter"
                or observed.get("toolArchiveHash") != cfg["archiveHash"]
                or observed.get("analyzerCodeHash") != cfg["analyzerCodeHash"]
                or observed.get("dependencyLockHash") != cfg["dependencyLockHash"]
                or observed.get("sessionId") != stages["compile"]["service"]["sessionId"]
                or build["sourceHash"] != result["sourceHash"] or build["bundleHash"] != result["bundleHash"]):
            raise LedgerError("receipt-invalid")
        report = read_object(ctx, result["browser"])
        large = read_object(ctx, result["largeText"])
        if (report["browserExecution"]["sessionId"] != stages["browser"]["service"]["sessionId"]
                or report["bundleHash"] != result["bundleHash"]
                or large["bundleHash"] != result["bundleHash"]
                or stages["browser"]["result"].get("largeText") != {"report": result["largeText"],
                    "sessionId": large["browserExecution"]["sessionId"],
                    "taskId": stages["browser"]["result"].get("largeText", {}).get("taskId")}
                or not any(call.get("callId") == stages["browser"]["result"]["largeText"]["taskId"]
                    and call.get("kind") == "browser" and call.get("status") == "completed"
                    and call.get("attemptId") == job["attempt"]["id"] and call.get("stage") == "browser"
                    and call.get("serviceSessionId") == large["browserExecution"]["sessionId"]
                    for call in job["calls"])):
            raise LedgerError("receipt-invalid")
        tester, missing = verify_graph._tester({"browser_report": report, "large_text_report": large,
            "build": build, "contract": prepared["contract"]},
            {"react_report_passes": react_report_passes, "contract_hash": contract_hash})
        covered = coverage.check(prd, prepared["flow"], prepared["expectation"], screens, k, prd_extract.bindings(prd))
        verification = result["verification"]
        if job["status"] == "succeeded" and (missing or any(f["severity"] == "critical" for f in tester)
                or covered["blocked"] or any(f["severity"] == "critical" for f in covered["findings"])
                or verification.get("verdict") != "pass" or verification.get("approvable") is not True):
            raise LedgerError("receipt-invalid")
        compact = {"artifactId": job["artifact"]["id"], "jobId": job["id"], "variant": result["variant"],
                   "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"],
                   "source": result["source"], "bundle": result["bundle"], "screenshot": result["screenshot"],
                   "passed": job["status"] == "succeeded", "verification": verification_summary(verification),
                   "designRevision": value["revision"]}
        if job["operation"] == "design.release":
            from workspace.releases import approval_hash
            from design_loop.convention import Convention, layout
            from design_loop.handoff import build as handoff
            raw_manifest = ctx.storage.get_blob(job["manifest"]["ref"])
            if hashlib.sha256(raw_manifest).hexdigest() != job["manifest"]["hash"]:
                raise LedgerError("receipt-invalid")
            manifest = json.loads(raw_manifest)
            base = manifest["base"]
            run = ctx.get("run", base["runId"])
            if (design["approvals"].get("ux") != manifest["approved"]["approval"]
                    or run.get("approval") != manifest["approved"]["approval"]
                    or result["source"]["sha256"] != manifest["approved"]["sourceHash"]
                    or result["bundle"]["sha256"] != manifest["approved"]["bundleHash"]
                    or job["status"] != "succeeded"):
                raise LedgerError("design-approval-changed")
            release_id = schema.identity("design-release", design["id"], job["id"])
            approval_digest = approval_hash(run["approval"])
            convention = Convention(prepared["convention"])
            files = read_archive(bundle, result["bundleHash"])
            delivery_manifest = {"schemaVersion": 1, "runId": run["id"], "round": 1,
                "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"], "catalogHash": build["catalogHash"],
                "contractHash": prepared["contractHash"], "approvalHash": approval_digest,
                "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())],
                "verification": "approved-source-rebuild-and-browser-test", "approvedScreenTolerance": 0.02}
            manifest_key, _ = ctx.put_json("release", release_id, "manifest.json", delivery_manifest)
            package, package_hash = handoff(source, layout(convention, registry, screens),
                manifest_fields={"approvalHash": approval_digest, "releaseId": release_id,
                    "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"]}, verification_report=report)
            package_key = ctx.storage.key_for(ctx.owner, "release", release_id, "handoff.zip")
            ctx.storage.put_blob_once(package_key, package, "application/zip")
            release = {"id": release_id, "projectId": ctx.project_id, "designId": design["id"], "runId": run["id"], "round": 1,
                "status": "ready", "executionSchemaVersion": 1, "executionId": job["id"], "jobId": job["id"], "actor": job["actor"],
                "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"], "catalogHash": build["catalogHash"],
                "contractHash": prepared["contractHash"], "contractVersion": run["contractVersion"],
                "approvalHash": approval_digest, "approval": run["approval"], "outputType": "react",
                "sourceKey": result["source"]["key"], "distKey": result["bundle"]["key"], "manifestKey": manifest_key,
                "reportKey": result["browser"]["key"], "handoffKey": package_key, "handoffHash": package_hash,
                "sourceArchiveSha256": result["source"]["sha256"], "distArchiveSha256": result["bundle"]["sha256"],
                "build": {key: value for key, value in build.items() if key not in {"files", "previewHtml", "sourceFiles", "projectZipBase64", "distZipBase64"}}, "verification": {"functionalStatus": report["functionalStatus"], "visual": report["visual"],
                    "accessibility": report["accessibility"]}, "rebuiltAt": ctx.storage.clock(), "admissions": job["admissions"]}
            value.update(status="released", releases=[*design.get("releases", []), {"id": release_id,
                "artifactId": base["artifactId"], "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"]}])
            return value, [ctx.write("release", release)]
        run_id = schema.identity("design-run", design["id"], job["id"])
        contract_id = schema.identity("design-contract", design["id"], design["flow"]["sha256"])
        existing_contract = ctx.storage.get(ctx.owner, "contract", contract_id)
        extra = []
        if existing_contract is None:
            frozen = {**prepared["contract"], "id": contract_id, "projectId": ctx.project_id,
                "executionSchemaVersion": 1, "executionId": job["id"], "status": "approved", "designId": design["id"],
                "approval": {"actor": design["approvals"]["flow"]["actor"], "at": design["approvals"]["flow"]["at"],
                             "hash": prepared["contractHash"], "version": 1}, "flowHash": design["flow"]["sha256"]}
            extra.append(ctx.write("contract", frozen))
            contract_version = 1
        else:
            if existing_contract.get("flowHash") != design["flow"]["sha256"] or existing_contract.get("approval", {}).get("hash") != prepared["contractHash"]:
                raise LedgerError("design-changed")
            contract_version = existing_contract["version"]
        if job["operation"] == "design.edit":
            from design_loop.edit import stability
            raw_manifest = ctx.storage.get_blob(job["manifest"]["ref"])
            if hashlib.sha256(raw_manifest).hexdigest() != job["manifest"]["hash"]:
                raise LedgerError("receipt-invalid")
            manifest = json.loads(raw_manifest)
            base_report = read_object(ctx, manifest["baseResult"]["browser"])
            if manifest.get("baseBrowser") != {"layoutBoxes": base_report.get("layoutBoxes", {})}:
                raise LedgerError("receipt-invalid")
            measured = delivery.edit_layout(manifest, report)
            if verification.get("layout") != measured or (job["status"] == "succeeded" and not measured["stable"]):
                raise LedgerError("design-edit-layout")
            previous = delivery.unpacked(manifest["baseResult"]["generated"]["screens"])
            target = manifest["edit"]
            key = target["screenId"], target["state"]
            if set(previous) != set(screens) or not stability(previous[key], screens[key], target["targetId"])["stable"]:
                raise LedgerError("design-edit-scope")
            if any(previous[k] != screens[k] for k in previous if k != key):
                raise LedgerError("design-edit-scope")
        row = {"number": 1, "status": "reviewable" if compact["passed"] else "needs_changes", "passed": compact["passed"],
            "sourceHash": result["sourceHash"], "bundleHash": result["bundleHash"], "catalogHash": build["catalogHash"],
            "sourceKey": result["source"]["key"], "sourceArchiveSha256": result["source"]["sha256"],
            "distKey": result["bundle"]["key"], "distArchiveSha256": result["bundle"]["sha256"],
            "screenshotKey": result["screenshot"]["key"], "reportKey": result["browser"]["key"],
            "pageSources": build.get("pageSources", []), "designManifestInput": {"admissions": job["admissions"]},
            "blockingFindings": report.get("blockingFindings", []), "executionId": job["id"]}
        run = {"id": run_id, "projectId": ctx.project_id, "designId": design["id"], "executionSchemaVersion": 1,
            "executionId": job["id"], "jobId": job["id"], "actor": job["actor"], "status": "completed",
            "outputType": "react", "model": job["model"], "contractId": contract_id, "contractVersion": contract_version,
            "contractHash": prepared["contractHash"], "contract": prepared["contract"], "catalogHash": build["catalogHash"],
            "rounds": [row], "bestRound": 1 if compact["passed"] else None, "inputSnapshots": []}
        extra.append(ctx.write("run", run))
        compact["runId"] = run_id
        value.update(status="review", variants=[*design["variants"], compact])
        return value, extra
