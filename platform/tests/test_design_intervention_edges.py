"""Additional current-source/approval and pre-dispatch negatives for V-05."""
import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_design_execution import pipeline, design_state, env, api, ready, execute
from test_design_interventions import get_view
from workspace.design_api import approve_variant, route, read_object, HUMAN
from workspace.design_execution import submit
from workspace.execution_ledger import LedgerError
from workspace.collaboration import CollaborationError


def approve(state, variant):
    record = state.ctx.get("design", state.design["id"])
    return route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [record["id"], "approve"], {
        "version": record["version"], **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")},
    }, {})[1]


def test_rejecting_an_older_approved_variant_invalidates_its_run_without_clearing_other_approval(pipeline):
    state = pipeline
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    assert execute(state, "generate", requestId="second-verified", variant=1)[0]["status"] == "succeeded"
    first = state.ctx.get("design", state.design["id"])["variants"][0]
    second = state.ctx.get("design", state.design["id"])["variants"][1]
    approve(state, first)
    assert execute(state, "release", baseArtifactId=first["artifactId"])[0]["status"] == "succeeded"
    record = state.ctx.get("design", state.design["id"])
    release = state.ctx.get("release", record["releases"][0]["id"])
    route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST",
          [record["id"], "releases", release["id"], "accept"], {
              "version": release["version"], "sourceHash": release["sourceHash"],
              "bundleHash": release["bundleHash"], "note": "합성 패키지 인수 확인",
          }, {})
    approve(state, second)
    current = state.ctx.get("design", state.design["id"])
    release_id = current["releases"][0]["id"]
    body = {"requestId": "reject-old", "version": current["version"], "reason": "이전 시안의 승인도 철회합니다.",
            **{key: first[key] for key in ("artifactId", "sourceHash", "bundleHash")}}
    route(state.ctx.host, state.ctx.scope, state.ctx.claims, "POST", [current["id"], "reject"], body, {})
    assert state.ctx.get("design", current["id"])["approvals"]["ux"]["artifactId"] == second["artifactId"]
    assert not state.ctx.get("design", current["id"]).get("frontendAcceptance")
    assert state.ctx.get("run", first["runId"])["approval"] is None
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET",
              [current["id"], "releases", release_id], {}, {})
    assert error.value.code == "not-found"


def test_pending_edit_cancel_and_instruction_withdrawal_preserve_action_but_deny_detail(pipeline, env):
    from intake import prompts, review
    from intake.records import INTAKE_OWNER
    from workspace.design_stages import unpacked
    from design_loop.composition import walk
    state = pipeline
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    record = state.ctx.get("design", state.design["id"])
    variant = record["variants"][0]
    result = read_object(state.ctx, state.ctx.get("wb_artifact", variant["artifactId"])["result"])
    key, composition = next(iter(unpacked(result["generated"]["screens"]).items()))
    target = next(node["id"] for _, node, _ in walk(composition))
    env.grant("carol", identifier="instruction-only")
    pending = prompts.admit_prompt_text(state.ctx.host, state.ctx.scope, "선택한 구성을 유지하세요.",
        purpose="edit-instruction", claims=state.ctx.claims)
    decision = review.decide(state.ctx.host, state.ctx.collaboration.resolve_scope("carol", state.ctx.project_id),
                            pending["id"], approve=True, reason="synthetic instruction")
    assert decision["review"]["grantId"] == "instruction-only"
    body = {"requestId": "cancel-edit", "version": record["version"], "baseArtifactId": variant["artifactId"],
            "instructionDecisionId": decision["id"], "screenId": key[0], "state": key[1], "targetId": target}
    calls = len(state.models), state.compiles, state.browsers
    queued = submit(state.ctx, record["id"], "edit", body, state.ledger, state.config, lambda *args: None)
    state.ledger.api().cancel(state.ctx.owner, queued["jobId"], actor=state.ctx.actor)
    view = get_view(state, variant["artifactId"])
    assert view["editRequests"] == 1 and view["events"][0]["outcome"] == "cancelled"
    assert get_view(state, variant["artifactId"], actor="bob")["events"] == []
    stored = state.ctx.get("design", record["id"])
    assert stored["interventions"][0]["outcome"] == "cancelled"
    grant = state.storage.get(INTAKE_OWNER, "adm_grant", "instruction-only")
    env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    # Original business inputs remain readable; only the edit's input lineage was revoked.
    assert route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", [record["id"]], {}, {})[0] == 200
    with pytest.raises(CollaborationError) as error:
        get_view(state, variant["artifactId"])
    assert error.value.code == "not-found"
    assert (len(state.models), state.compiles, state.browsers) == calls
    assert state.ctx.get("design", record["id"])["interventions"] == stored["interventions"]


def test_human_history_limit_refuses_before_paid_edit_or_new_job(pipeline):
    from intake import prompts, review
    from workspace.design_stages import unpacked
    from design_loop.composition import walk
    state = pipeline
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    record = state.ctx.get("design", state.design["id"])
    variant = record["variants"][0]
    result = read_object(state.ctx, state.ctx.get("wb_artifact", variant["artifactId"])["result"])
    key, composition = next(iter(unpacked(result["generated"]["screens"]).items()))
    target = next(node["id"] for _, node, _ in walk(composition))
    pending = prompts.admit_prompt_text(state.ctx.host, state.ctx.scope, "선택한 구성을 유지하세요.",
        purpose="edit-instruction", claims=state.ctx.claims)
    decision = review.decide(state.ctx.host, state.ctx.collaboration.resolve_scope("bob", state.ctx.project_id),
                            pending["id"], approve=True, reason="synthetic instruction")
    record["interventions"] = [{
        "id": f"historical-{index}", "kind": "approve", "artifactId": variant["artifactId"],
        "actor": state.ctx.actor, "at": state.storage.clock(), "scopes": [{"screenId": key[0]}],
        "sourceHash": variant["sourceHash"], "bundleHash": variant["bundleHash"],
    } for index in range(200)]
    state.storage.put_many([state.ctx.write("design", record, record["version"])], _writer=HUMAN)
    record = state.ctx.get("design", record["id"])
    before = len(state.models), state.compiles, state.browsers, copy.deepcopy(record)
    with pytest.raises(LedgerError) as error:
        submit(state.ctx, record["id"], "edit", {"requestId": "over-limit", "version": record["version"],
            "baseArtifactId": variant["artifactId"], "instructionDecisionId": decision["id"],
            "screenId": key[0], "state": key[1], "targetId": target}, state.ledger, state.config, lambda *args: None)
    assert error.value.code == "design-intervention-limit"
    assert (len(state.models), state.compiles, state.browsers, state.ctx.get("design", record["id"])) == before
