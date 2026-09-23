import copy
from types import SimpleNamespace

from tests.test_agents_handler import fakes, fresh_store, ROLE_ARN
from agentcore import agent_specs, harness, seeding, skill_binding
from agentcore.administration import apply_request, harness_fingerprint
from registry import api


def specification():
    spec = copy.deepcopy(agent_specs.SCENARIO_AGENTS[0])
    spec.update(name="seed_probe", description="Synthetic seed", skills=["bank-publishing-conventions"],
                allowedTools=["list_regulations"], memory=False)
    return spec


def test_same_runtime_legacy_seed_creates_immutable_current_version(fakes):
    spec = specification()
    legacy = api.create_record({"name": spec["name"], "recordVersion": "v1", "recordType": "AGENT",
        "description": spec["description"], "payload": {"runtime": "AgentCore Harness"}},
        "admin", status="APPROVED", embed=False)
    result = seeding.seed_agents([spec], "", "iam-invoke:11111111-1111-4111-8111-111111111111")
    assert result["agents"][0]["completed"] and result["agents"][0]["version"] == "v2"
    assert api.get_record(spec["name"], "v1")["payload"] == legacy["payload"]
    assert api.get_record(spec["name"], "v1")["status"] == "DEPRECATED"
    assert api.get_record(spec["name"], "v2")["payload"]["skillBindings"]
    again = seeding.seed_agents([spec], "", "iam-invoke:11111111-1111-4111-8111-111111111111")
    assert again["agents"][0]["version"] == "v2" and len(api.get_store().versions(spec["name"])) == 2


def test_seed_rechecks_skill_after_slow_harness_creation(fakes, monkeypatch):
    spec = specification()
    ensure = harness.ensure_harness
    def changed(bound):
        result = ensure(bound)
        api.get_store().force_status(spec["skills"][0], "v1", "DEPRECATED", "admin", "Synthetic retirement")
        return result
    monkeypatch.setattr(harness, "ensure_harness", changed)
    result = seeding.seed_agents([spec], "", "iam-invoke:11111111-1111-4111-8111-111111111111")
    assert result["agents"][0]["completed"] is False
    pending = api.get_record(spec["name"], result["agents"][0]["version"])
    assert pending["status"] == "PENDING_APPROVAL"
    assert result["agents"][0]["request"]["status"] == "PENDING_ADMIN"


def test_seed_drift_stages_a_request_that_existing_iam_reconciliation_can_finish(fakes, monkeypatch):
    spec = specification()
    existing = harness.ensure_harness({**spec, "skillBindings": skill_binding.capture(spec["skills"])})
    existing["maxIterations"] = 999
    result = seeding.seed_agents([spec], "", "iam-invoke:11111111-1111-4111-8111-111111111111")["agents"][0]
    assert not result["completed"] and result["action"] == "reconciliation-required"
    assert api.get_record(spec["name"], result["version"])["status"] == "PENDING_APPROVAL"
    def update(**params):
        existing.update({key: value for key, value in params.items() if key not in {"harnessId", "clientToken", "memory"}})
        existing["memory"] = params["memory"]["optionalValue"]
        existing["status"] = "READY"
    monkeypatch.setattr(harness, "ctl", lambda: SimpleNamespace(
        update_harness=update, get_harness=lambda **kwargs: {"harness": existing}))
    applied = apply_request(result["request"]["name"], reconcile_hash=harness_fingerprint(existing))
    assert applied["completed"] and applied["record"]["status"] == "APPROVED"
    assert existing["maxIterations"] == 12



def test_seed_never_approves_generic_record_with_builtin_name(fakes):
    spec = specification()
    api.create_record({"name": spec["name"], "recordVersion": "v1", "recordType": "CUSTOM", "subtype": "COMPONENT",
                       "description": spec["description"], "payload": {}}, "staff", status="PENDING_APPROVAL", embed=False)
    result = seeding.seed_agents([spec], "synthetic-runtime", "iam-invoke:11111111-1111-4111-8111-111111111111")
    assert not result["agents"][0]["completed"]
    assert api.get_record(spec["name"], "v1")["status"] == "PENDING_APPROVAL"


def test_runtime_seed_versions_prompt_and_skill_content_changes(fakes, monkeypatch, tmp_path):
    spec = specification()
    source = tmp_path / (spec["skills"][0] + ".md")
    source.write_text("Synthetic instruction one")
    monkeypatch.setattr(skill_binding, "SKILLS_DIR", tmp_path)
    actor = "iam-invoke:11111111-1111-4111-8111-111111111111"
    first = seeding.seed_agents([spec], "synthetic-runtime", actor)["agents"][0]
    assert first["completed"] and first["version"] == "v1"
    source.write_text("Synthetic instruction two")
    second = seeding.seed_agents([spec], "synthetic-runtime", actor)["agents"][0]
    assert second["completed"] and second["version"] == "v2"
    spec["systemPrompt"] += " Additional instruction"
    third = seeding.seed_agents([spec], "synthetic-runtime", actor)["agents"][0]
    assert third["completed"] and third["version"] == "v3"
    assert [r["recordVersion"] for r in api.list_approved() if r["name"] == spec["name"]] == ["v3"]


def test_runtime_seed_reports_committed_approval_on_mirror_failure(fakes, monkeypatch):
    from agentcore import registry_mirror
    monkeypatch.setattr(registry_mirror, "mirror", lambda record: (_ for _ in ()).throw(RuntimeError("synthetic")))
    spec = specification()
    result = seeding.seed_agents([spec], "synthetic-runtime", "iam-invoke:11111111-1111-4111-8111-111111111111")["agents"][0]
    assert result["applied"] and not result["completed"] and result["registry"] == "APPROVED"
    assert result["agentcoreRegistry"]["status"] == "SYNC_PENDING"
