"""Versioned IAM seeding; drift stages a reviewable request instead of dead approval."""
from registry import api
from registry.model import ConflictError
from agentcore import harness, registry_mirror, skill_binding
from agentcore.administration import harness_settings, request_transition


def _stage(spec, payload, actor):
    versions = api.get_store().versions(spec["name"])
    versions.sort(key=lambda row: int(row["recordVersion"][1:]))
    latest = versions[-1] if versions else None
    same = (latest and latest.get("description") == spec["description"]
            and {key: value for key, value in latest["payload"].items() if key != "supersedes"} == payload)
    if same and latest["status"] in {"DRAFT", "PENDING_APPROVAL", "APPROVED"}:
        record = latest
    else:
        version = "v" + str(int(latest["recordVersion"][1:]) + 1 if latest else 1)
        record = api.create_record({
            "name": spec["name"], "recordVersion": version, "recordType": "AGENT",
            "description": spec["description"], "owner": "AI플랫폼팀",
            "tags": [spec.get("scenario", "custom"), payload["runtime"]],
            "payload": {**payload, **({"supersedes": latest["recordVersion"]} if latest else {})},
        }, actor=actor, embed=False)
    if record["status"] == "DRAFT":
        record, _ = api.transition(record["name"], record["recordVersion"], "PENDING_APPROVAL",
                                   actor, "IAM seed specification staged", expected_record=record)
    return record


def seed_agents(specs, runtime_arn, actor):
    rows = []
    for spec in specs:
        row = {"name": spec["name"]}
        try:
            payload = {"model": spec["model"], "allowedTools": spec["allowedTools"], "skills": spec["skills"],
                       "memory": bool(spec.get("memory")), "title": spec.get("title"), "scenario": spec.get("scenario")}
            needs_reconciliation = False
            if runtime_arn:
                payload.update(runtime="agentcore-runtime/strands", runtimeArn=runtime_arn, sdk="Strands Agents")
            else:
                bindings = skill_binding.capture(spec.get("skills", []))
                bound = {**spec, "skillBindings": bindings}
                expected = harness.build_config(bound)
                actual = harness.ensure_harness(bound)
                payload.update(runtime="AgentCore Harness", harnessArn=actual.get("arn") or actual.get("harnessArn"),
                               harnessId=actual.get("harnessId"), skillBindings=bindings, systemPrompt=spec["systemPrompt"])
                needs_reconciliation = (actual.get("status") not in {"READY", "ACTIVE"}
                                        or harness_settings(actual) != harness_settings(expected))
                skill_binding.resolve(bindings, spec.get("skills", []))
            record = _stage(spec, payload, actor)
            if needs_reconciliation:
                if record["status"] == "APPROVED":
                    raise ConflictError("Deprecate the approved seed before reconciling its Harness")
                request = request_transition(record["name"], record["recordVersion"], "APPROVED",
                                             actor, "IAM seed requires exact Harness reconciliation")
                row.update(registry=record["status"], version=record["recordVersion"], completed=False,
                           action="reconciliation-required", request=request["request"])
                rows.append(row)
                continue
            if not runtime_arn:
                skill_binding.resolve(payload["skillBindings"], payload["skills"])
            if record["status"] != "APPROVED":
                record, _ = api.transition(record["name"], record["recordVersion"], "APPROVED",
                                           actor, "IAM seed specification approved", expected_record=record)
            mirrored = registry_mirror.mirror(record)
            row.update(registry=record["status"], version=record["recordVersion"], runtime=payload["runtime"],
                       agentcoreRegistry=mirrored, completed=mirrored.get("status") == record["status"])
        except Exception as error:
            row.update(completed=False, errorType=type(error).__name__)
        rows.append(row)
    return {"agents": rows, "runtimeArn": runtime_arn or None}
