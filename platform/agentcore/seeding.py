"""Versioned IAM seeding; drift stages a reviewable request instead of dead approval."""
from registry import api
from registry.model import ConflictError
from agentcore import agent_specs, harness, registry_mirror, skill_binding
from agentcore.administration import _apply_request, harness_settings, request_transition


def _stage(spec, payload, actor):
    versions = api.get_store().versions(spec["name"])
    versions.sort(key=lambda row: int(row["recordVersion"][1:]))
    if any(row.get("recordType") != "AGENT" or row.get("subtype") for row in versions):
        raise ConflictError("Built-in Agent name is occupied by another record type")
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


def _mirror(record):
    try:
        return registry_mirror.mirror(record)
    except Exception as error:
        return {"status": "SYNC_PENDING", "errorType": type(error).__name__}


def _seed(spec, runtime_arn, actor, row):
    payload = {"model": spec["model"], "allowedTools": spec["allowedTools"], "skills": spec["skills"],
               "memory": bool(spec.get("memory")), "title": spec.get("title"), "scenario": spec.get("scenario"),
               "systemPrompt": spec["systemPrompt"]}
    if runtime_arn:
        payload.update(runtime="agentcore-runtime/strands", runtimeArn=runtime_arn, sdk="Strands Agents",
                       runtimeSourceHash=agent_specs.source_hash(spec, skill_binding.SKILLS_DIR))
    else:
        payload.update(runtime="AgentCore Harness", skillBindings=skill_binding.capture(spec.get("skills", [])))
    # A source and pending request exist before any service is provisioned.
    record = _stage(spec, payload, actor)
    row.update(registry=record["status"], version=record["recordVersion"], applied=False, completed=False)
    for previous in api.get_store().versions(record["name"]):
        if previous["recordVersion"] != record["recordVersion"] and previous["status"] in {"APPROVED", "DEPRECATED"}:
            retired = previous
            if previous["status"] == "APPROVED":
                retired, _ = api.transition(previous["name"], previous["recordVersion"], "DEPRECATED", actor,
                                            "IAM seed superseded specification", expected_record=previous)
            mirrored = _mirror(retired)
            if mirrored.get("status") != "DEPRECATED":
                row.update(action="retirement-sync-required", retiredVersion=retired["recordVersion"],
                           agentcoreRegistry=mirrored)
                return
    if runtime_arn:
        if agent_specs.source_hash(spec, skill_binding.SKILLS_DIR) != payload["runtimeSourceHash"]:
            raise ConflictError("Runtime source changed before seed approval")
        if record["status"] != "APPROVED":
            record, _ = api.transition(record["name"], record["recordVersion"], "APPROVED", actor,
                                       "IAM seed specification approved", expected_record=record)
        mirrored = _mirror(record)
        row.update(registry=record["status"], applied=True, completed=mirrored.get("status") == record["status"],
                   agentcoreRegistry=mirrored, runtime=payload["runtime"])
        return
    if record["status"] == "APPROVED":
        from handlers.agents import _validate_create
        normalized, error = _validate_create({**payload, "name": spec["name"], "description": spec["description"]})
        actual = harness.find_harness("bank_" + spec["name"])
        if (error or not actual or actual.get("status") not in {"READY", "ACTIVE"}
                or harness_settings(actual) != harness_settings(harness.build_config(normalized))):
            row.update(action="deprecate-before-reconciliation", applied=True)
            return
        mirrored = _mirror(record)
        row.update(applied=True, completed=mirrored.get("status") == "APPROVED", agentcoreRegistry=mirrored)
        return
    request = request_transition(record["name"], record["recordVersion"], "APPROVED", actor,
                                 "IAM seed specification approval")
    row.update(action="reconciliation-required", request=request["request"])
    result = _apply_request(request["request"]["name"], actor_ref=actor)
    row.update(registry=result["record"]["status"], applied=result["applied"], completed=result["completed"],
               agentcoreRegistry=result["agentcoreRegistry"])
    if result["completed"]:
        row["action"] = "approved"


def seed_agents(specs, runtime_arn, actor):
    rows = []
    for spec in specs:
        row = {"name": spec["name"], "applied": False, "completed": False}
        try:
            with api.get_store().administration_lease(spec["name"], "agent"):
                _seed(spec, runtime_arn, actor, row)
        except Exception as error:
            row.update(completed=False, errorType=type(error).__name__)
        rows.append(row)
    return {"agents": rows, "runtimeArn": runtime_arn or None}
