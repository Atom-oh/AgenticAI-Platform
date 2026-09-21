import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_workspace_worker import environment, request
from workspace.rules import contract_hash, state_coverage_issues, validate_contract


def design():
    return {"title": "Stateful form", "brief": "Show invalid input and let the user correct it.", "assetIds": [],
            "requiredStates": ["input", "error"], "rules": [
                {"id": "input", "title": "Entered value persists", "required": True, "scenario": "input",
                 "source": {"kind": "manual"}, "steps": [{"action": "fill", "target": "amount", "value": "10"},
                                                        {"action": "expectValue", "target": "amount", "value": "10"}]},
                {"id": "error", "title": "Invalid input is explained", "required": True, "scenario": "error",
                 "source": {"kind": "manual"}, "steps": [{"action": "fill", "target": "amount", "value": ""},
                                                        {"action": "click", "target": "next"},
                                                        {"action": "expectVisible", "target": "error", "value": True}]}]}


def test_selected_states_are_hashed_and_legacy_contracts_keep_their_hash():
    contract = design()
    normalized = validate_contract(contract)
    assert normalized["requiredStates"] == ["input", "error"]
    assert not state_coverage_issues(normalized)
    changed = copy.deepcopy(normalized)
    changed["requiredStates"].append("back")
    assert contract_hash(changed) != contract_hash(normalized)
    legacy = {key: value for key, value in normalized.items() if key != "requiredStates"}
    assert contract_hash(legacy) == contract_hash({**legacy, "requiredStates": []})
    changed["rules"][1]["scenario"] = "back"
    assert state_coverage_issues(changed) == ["오류·수정 상태에 연결된 필수 검증 규칙을 작성하세요."]


@pytest.mark.parametrize("states", [None, "error", ["error", "error"], ["invented"], [True], {"error": True}])
def test_unknown_or_malformed_required_states_are_rejected(states):
    with pytest.raises(ValueError):
        validate_contract({**design(), "requiredStates": states})


def test_optional_rules_cannot_satisfy_a_required_state():
    contract = design()
    contract["rules"][1]["required"] = False
    assert state_coverage_issues(validate_contract(contract)) == ["오류·수정 상태에 연결된 필수 검증 규칙을 작성하세요."]


def test_state_gaps_block_approval_until_required_rules_are_connected():
    storage, api, worker = environment()
    draft = design()
    draft["rules"][1].pop("scenario")
    status, created = request(api, "POST", "/contracts", draft)
    assert status == 201
    record = created["contract"]
    status, refused = request(api, "POST", f"/contracts/{record['id']}/approve", {"version": record["version"]})
    assert status == 409 and refused["code"] == "contract-states-incomplete"
    status, edited = request(api, "PUT", f"/contracts/{record['id']}", {**design(), "version": record["version"]})
    assert status == 200
    status, accepted = request(api, "POST", f"/contracts/{record['id']}/approve", {"version": edited["contract"]["version"]})
    assert status == 200 and accepted["contract"]["status"] == "approved"
    approved = accepted["contract"]
    status, changed = request(api, "PUT", f"/contracts/{record['id']}", {**approved, "requiredStates": ["input", "error", "back"]})
    assert status == 200 and changed["contract"]["status"] == "draft" and not changed["contract"].get("approval")
    assert request(api, "POST", f"/contracts/{record['id']}/approve", {"version": changed["contract"]["version"]})[0] == 409


def test_proposal_cannot_silently_remove_the_designers_selected_states():
    prompts = []
    def model(system, user, images, model_id, maximum, trace_id, purpose):
        prompts.append((system, user))
        proposal = design()
        proposal["requiredStates"] = ["input"]
        proposal["rules"] = proposal["rules"][:1]
        return json.dumps(proposal), {}, {"modelId": model_id}
    storage, api, worker = environment(model=model)
    body = {"assetIds": [], "brief": "Explain and recover invalid input", "requiredStates": ["input", "error"],
            "model": "global.openai.gpt-6-astra", "requestId": "state-proposal"}
    status, queued = request(api, "POST", "/contracts/propose", body)
    assert status == 202
    assert worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})["status"] == "completed"
    job = storage.get("designer", "job", queued["job"]["id"])
    contract = storage.get("designer", "contract", job["result"]["contractId"])
    assert contract["requiredStates"] == ["input", "error"]
    assert '"input", "error"' in prompts[0][1]
    assert request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})[0] == 409
    status, refused = request(api, "POST", "/contracts/propose", {**body, "requiredStates": ["input"]})
    assert status == 409 and refused["code"] == "request-changed"
