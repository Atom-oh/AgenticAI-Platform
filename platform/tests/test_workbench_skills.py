import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb, call, indexed, queue, run, source, documents
from workspace.collaboration import CollaborationError


def draft(wb, **extra):
    return call(wb, "POST", "skills", {"requestId": "skill", "name": "withdrawal-review",
        "title": "출금 화면 검토", "description": "현재 근거로 출금 화면을 확인합니다.",
        "instructions": "근거를 읽고 누락된 정보를 명시하세요. 도구 권한을 확대하지 마세요.",
        "toolNames": ["knowledge.search"], "examples": [{"input": "안내 검토", "expected": "근거 확인"}],
        **extra})["skill"]


def test_skill_package_is_content_backed_and_unrun_behavior_cannot_be_approved(wb):
    skill = draft(wb)
    package = call(wb, "GET", f"skills/{skill['id']}/package")
    assert "근거를 읽고" in package["files"]["SKILL.md"]
    validated = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})
    assert validated["validation"]["package"]["status"] == "passed"
    assert validated["validation"]["behavior"]["status"] == "not-run"
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/approve",
             {"version": validated["skill"]["version"], "contentHash": skill["contentHash"]})


def test_skill_exact_approval_and_source_staleness_block_consumption(wb):
    src, _ = indexed(wb)
    doc = call(wb, "GET", "knowledge", query={"q": "withdrawal"})["items"][0]
    ref = call(wb, "GET", "knowledge/" + doc["id"])["evidence"]
    skill = draft(wb, sourceRefs=[ref])
    wb.api.workbench_gate = lambda **kw: {"allowed": True, "receipt": {"inspected": True, "bytes": len(str(kw["payload"]).encode())}}
    wb.api.workbench_evaluator = lambda **kw: {
        "status": "passed", "cases": [{"input": "안내 검토", "passed": True}], "evaluator": "injected-test"}
    validated = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})["skill"]
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/approve",
             {"version": validated["version"], "contentHash": "0" * 64})
    approved = call(wb, "POST", f"skills/{skill['id']}/approve",
         {"version": validated["version"], "contentHash": validated["contentHash"]})["skill"]
    from workbench.service import Service
    from workbench.skills import resolve_approved
    context = Service(wb.api, wb.collab.resolve_scope("alice", wb.project["id"]),
                      {"sub": "alice", "exp": wb.now // 1000 + 3600})
    assert resolve_approved(context, skill["id"], approved["version"], approved["contentHash"])["files"]
    queue(wb, src, request="stale")
    with pytest.raises(CollaborationError):
        resolve_approved(context, skill["id"], approved["version"], approved["contentHash"])


def test_skill_edit_invalidates_evidence_and_approval(wb):
    skill = draft(wb)
    validated = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})["skill"]
    edited = call(wb, "PUT", f"skills/{skill['id']}",
                  {"version": validated["version"], "instructions": "변경된 지침의 근거를 확인하세요."})["skill"]
    assert edited["contentHash"] != skill["contentHash"]
    assert edited["status"] == "DRAFT" and edited["validation"]["behavior"]["status"] == "not-run"
    assert edited.get("approval") is None


def test_propose_requires_real_gated_adapter_and_publishes_its_validated_content(wb):
    request = {"requestId": "propose", "goal": "근거 기반 출금 화면 검토", "domain": "ux",
               "toolNames": ["knowledge.search"]}
    with pytest.raises(CollaborationError) as err:
        call(wb, "POST", "skills/propose", request)
    assert err.value.code == "model-not-configured"
    phases = []
    def gate(**kw):
        phases.append(kw["phase"])
        return {"allowed": True, "receipt": {"inspected": True, "bytes": len(str(kw["payload"]).encode())}}
    wb.api.workbench_gate = gate
    wb.api.workbench_model = lambda **kw: {"name": "proposed-skill", "title": "실제 어댑터 초안",
        "description": "주입된 모델 결과", "instructions": "출금 근거를 확인하고 정보 부족을 표시하세요.",
        "examples": [{"input": "검토", "expected": "근거"}]}
    payload = call(wb, "POST", "skills/propose", request)
    run(wb, payload)
    final = next(x for x in call(wb, "GET", "skills")["items"] if x["id"] == payload["skill"]["id"])
    assert final["title"] == "실제 어댑터 초안"
    assert phases == ["input", "output"]
    assert final["drafting"]["modelInvoked"] is True
    assert final["validation"]["behavior"]["status"] == "not-run"


def test_edit_cannot_strip_restricted_source_binding_to_read_private_instructions(wb):
    src = source(wb)
    docs = documents()
    docs[0]["allowedRoles"] = ["owner"]
    run(wb, queue(wb, src, docs))
    hit = call(wb, "GET", "knowledge", query={"q": "withdrawal"})["items"][0]
    ref = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    skill = draft(wb, sourceRefs=[ref])
    with pytest.raises(CollaborationError):
        call(wb, "PUT", f"skills/{skill['id']}", {"version": skill["version"], "sourceRefs": []}, actor="bob")
    assert call(wb, "GET", "skills", actor="bob")["items"] == []


def test_gate_block_never_invokes_model(wb):
    src, _ = indexed(wb)
    invoked = []
    wb.api.workbench_gate = lambda **kw: {"allowed": False}
    wb.api.workbench_model = lambda **kw: invoked.append(kw)
    payload = call(wb, "POST", "skills/propose", {"requestId": "blocked", "goal": "검토", "domain": "ux",
                                                "sourceIds": [src["id"]]})
    with pytest.raises(CollaborationError):
        run(wb, payload)
    assert invoked == []


def test_approval_cas_fences_source_revision_race(wb):
    src, _ = indexed(wb)
    hit = call(wb, "GET", "knowledge", query={"q": "withdrawal"})["items"][0]
    ref = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    skill = draft(wb, sourceRefs=[ref])
    wb.api.workbench_gate = lambda **kw: {"allowed": True, "receipt": {"inspected": True, "bytes": 10}}
    wb.api.workbench_evaluator = lambda **kw: {
        "status": "passed", "cases": [{"input": "안내 검토", "passed": True}], "evaluator": "injected-test"}
    validated = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})["skill"]
    def change_source():
        row = wb.storage.get(wb.owner, "wb_source", src["id"])
        wb.storage.put(wb.owner, "wb_source", {**row, "sourceVersion": row["sourceVersion"] + 1}, row["version"])
    wb.storage.table().before_transaction = change_source
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/approve",
             {"version": validated["version"], "contentHash": validated["contentHash"]})
    assert wb.storage.get(wb.owner, "wb_skill", skill["id"])["status"] == "PENDING_APPROVAL"


def test_approval_rechecks_required_role_after_package_read(wb, monkeypatch):
    skill = draft(wb)
    wb.api.workbench_gate = lambda **kw: {"allowed": True, "receipt": {"inspected": True, "bytes": 10}}
    wb.api.workbench_evaluator = lambda **kw: {
        "status": "passed", "cases": [{"input": "안내 검토", "passed": True}], "evaluator": "test"}
    validated = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})["skill"]
    from workbench import skills
    original = skills.package
    def downgrade(ctx, identifier):
        project = wb.storage.get(wb.owner, "project", wb.project["id"])
        project["members"]["alice"]["role"] = "developer"
        wb.storage.put(wb.owner, "project", project, project["version"])
        return original(ctx, identifier)
    monkeypatch.setattr(skills, "package", downgrade)
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/approve",
             {"version": validated["version"], "contentHash": skill["contentHash"]})
    assert wb.storage.get(wb.owner, "wb_skill", skill["id"])["status"] != "APPROVED"


def test_skill_package_detects_concurrent_revision_change(wb, monkeypatch):
    skill = draft(wb)
    original = wb.storage.get_blob
    def change_record(key, *args, **kwargs):
        raw = original(key, *args, **kwargs)
        if key.endswith("/package.json"):
            stored = wb.storage.get(wb.owner, "wb_skill", skill["id"])
            wb.storage.put(wb.owner, "wb_skill", {**stored, "status": "DEPRECATED"}, stored["version"])
        return raw
    monkeypatch.setattr(wb.storage, "get_blob", change_record)
    with pytest.raises(CollaborationError):
        call(wb, "GET", f"skills/{skill['id']}/package")


def test_author_can_update_stale_skill_with_current_source_evidence(wb):
    src, _ = indexed(wb)
    hit = call(wb, "GET", "knowledge", query={"q": "withdrawal"})["items"][0]
    ref = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    skill = draft(wb, sourceRefs=[ref])
    docs = documents()
    docs[0]["revision"] = "r2"
    run(wb, queue(wb, src, docs, request="revise"))
    listed = call(wb, "GET", "skills")["items"]
    assert listed[0]["id"] == skill["id"] and listed[0]["sourceStatus"] == "stale"
    current = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    changed = call(wb, "PUT", f"skills/{skill['id']}",
                   {"version": skill["version"], "sourceRefs": [current]})["skill"]
    assert changed["status"] == "DRAFT" and changed["sourceRefs"][0]["revision"] == "r2"
    assert changed["contentHash"] != skill["contentHash"]


def install_runtime(wb, model_call):
    from workbench.runtime import install
    for name in ("workbench_gate", "workbench_model", "workbench_evaluator", "workbench_executor"):
        if hasattr(wb.api, name):
            delattr(wb.api, name)
    wb.api.model_call = model_call
    install(wb.api)


def runtime_response(user):
    data = json.loads(user)
    question = data["input"]
    if "shell.execute" in question:
        return {"status": "refused", "answer": "승인되지 않은 작업입니다.", "evidenceIds": []}
    if "없는 근거" in question:
        return {"status": "insufficient-evidence", "answer": "현재 근거가 부족합니다.", "evidenceIds": []}
    return {"status": "answered", "answer": "근거 확인", "evidenceIds": []}


def test_default_runtime_proposes_evaluates_async_approves_and_consumes_exact_skill(wb):
    calls = []
    def model_call(system, user, images, model, max_tokens, trace_id, purpose):
        calls.append({"system": system, "user": user, "purpose": purpose, "model": model})
        if purpose == "workbench-skill-proposal":
            response = {"name": "runtime-review", "title": "런타임 초안", "description": "모델 초안",
                "instructions": "검토 요청에는 근거 확인이라고 답하세요.",
                "examples": [{"input": "검토", "expected": "근거 확인"}]}
        else:
            response = runtime_response(user)
        return json.dumps(response, ensure_ascii=False), {"inputTokens": 20, "outputTokens": 10}, {"backend": "injected"}
    install_runtime(wb, model_call)
    proposed = call(wb, "POST", "skills/propose", {"requestId": "runtime", "goal": "화면 검토", "domain": "ux"})
    run(wb, proposed)
    skill = call(wb, "GET", f"skills/{proposed['skill']['id']}")["skill"]
    assert skill["status"] == "DRAFT" and skill["drafting"]["modelInvoked"]
    assert skill["drafting"]["runtimeReceipts"][0]["physicalAdapter"] == "injected-model-call"
    evaluating = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})
    assert evaluating["job"]["input"]["operation"] == "skill-validate"
    assert evaluating["skill"]["status"] == "VALIDATING"
    run(wb, evaluating)
    checked = call(wb, "GET", f"skills/{skill['id']}")["skill"]
    assert checked["validation"]["behavior"]["status"] == "passed"
    assert checked["validation"]["behavior"]["heldOutCount"] == 2
    evaluation = call(wb, "GET", f"skills/{skill['id']}/evaluation")
    assert evaluation["evidence"]["cases"][0]["actual"]["answer"] == "근거 확인"
    assert all(case["passed"] for case in evaluation["evidence"]["heldOutCases"])
    eval_calls = [c for c in calls if c["purpose"] == "workbench-skill-behavior"]
    assert len(eval_calls) == 3
    assert all("expected" not in json.loads(c["user"]) for c in eval_calls)
    assert all("references/examples.json" not in c["system"] for c in eval_calls)
    approved = call(wb, "POST", f"skills/{skill['id']}/approve",
        {"version": checked["version"], "contentHash": checked["contentHash"]})["skill"]
    execution = call(wb, "POST", f"skills/{skill['id']}/execute",
        {"version": approved["version"], "contentHash": approved["contentHash"], "input": "검토"})
    result = run(wb, execution)
    assert "answer" not in result  # Parent /jobs reads must not bypass source ACL.
    output = call(wb, "GET", f"skills/{skill['id']}/executions/{result['artifactId']}")
    assert output["result"]["answer"] == "근거 확인"
    assert output["artifact"]["skillVersion"] == approved["version"]
    call(wb, "POST", f"skills/{skill['id']}/deprecate", {"version": approved["version"], "reason": "교체"})
    with pytest.raises(CollaborationError):
        call(wb, "GET", f"skills/{skill['id']}/executions/{result['artifactId']}")


def test_runtime_behavior_checks_outputs_not_model_claimed_pass(wb):
    def model_call(system, user, images, model, max_tokens, trace_id, purpose):
        response = {"status": "answered", "answer": "잘못된 출력", "evidenceIds": [], "passed": True}
        return json.dumps(response), {}, {}
    install_runtime(wb, model_call)
    skill = draft(wb)
    queued = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})
    run(wb, queued)
    checked = call(wb, "GET", f"skills/{skill['id']}")["skill"]
    assert checked["validation"]["behavior"]["status"] == "failed"
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/approve",
             {"version": checked["version"], "contentHash": checked["contentHash"]})


def test_runtime_boundary_blocks_identifiers_before_the_physical_model_call(wb):
    calls = []
    install_runtime(wb, lambda *args: calls.append(args))
    queued = call(wb, "POST", "skills/propose",
                  {"requestId": "pii", "goal": "연락처 010-1234-5678을 기억하세요", "domain": "ux"})
    with pytest.raises(CollaborationError) as error:
        run(wb, queued)
    assert error.value.code == "boundary-blocked"
    assert calls == []


def test_runtime_evaluation_does_not_send_source_metadata_or_expected_values_to_model(wb):
    # This is a valid metadata timestamp that matches a raw identifier pattern.
    # It must remain private evidence, outside the actual model prompt.
    wb.now = 1_800_001_000_000
    wb.storage.clock = lambda: wb.now
    src, _ = indexed(wb)
    hit = call(wb, "GET", "knowledge", query={"q": "withdrawal"})["items"][0]
    ref = call(wb, "GET", "knowledge/" + hit["id"])["evidence"]
    seen = []
    def model_call(system, user, images, model, max_tokens, trace_id, purpose):
        seen.append(user)
        return json.dumps(runtime_response(user), ensure_ascii=False), {}, {}
    install_runtime(wb, model_call)
    skill = draft(wb, sourceRefs=[ref])
    queued = call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]})
    run(wb, queued)
    assert seen and all("accessExpiresAt" not in value and "contentHash" not in value for value in seen)
    checked = call(wb, "GET", f"skills/{skill['id']}")["skill"]
    assert checked["validation"]["behavior"]["status"] == "passed"


def test_corrupt_behavior_evidence_cannot_be_approved(wb):
    def model_call(system, user, images, model, max_tokens, trace_id, purpose):
        return json.dumps(runtime_response(user), ensure_ascii=False), {}, {}
    install_runtime(wb, model_call)
    skill = draft(wb)
    run(wb, call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]}))
    checked = wb.storage.get(wb.owner, "wb_skill", skill["id"])
    key = checked["validation"]["behavior"]["evidenceKey"]
    wb.storage.put_blob(key, b'{"status":"passed"}', "application/json")
    with pytest.raises(CollaborationError):
        call(wb, "POST", f"skills/{skill['id']}/approve",
             {"version": checked["version"], "contentHash": checked["contentHash"]})


def test_parent_constructors_install_default_proposal_path_without_manual_attribute_wiring(wb):
    from workspace.http import WorkspaceAPI
    from workspace.worker import Worker
    wb.api = WorkspaceAPI(storage=wb.storage, collaboration=wb.collab,
                          worker_fn="worker", lambda_client=wb.api.lambda_client)
    queued = call(wb, "POST", "skills/propose",
                  {"requestId": "constructor", "goal": "화면 점검", "domain": "ux"})
    def model_call(system, user, images, model, max_tokens, trace_id, purpose):
        return json.dumps({"name": "constructor-path", "title": "기본 생성자 초안", "description": "기본 경로",
            "instructions": "근거를 확인하세요.", "examples": [{"input": "검토", "expected": "근거"}]},
            ensure_ascii=False), {}, {}
    worker = Worker(storage=wb.storage, model_call=model_call)
    assert worker.handle({"owner": wb.owner, "jobId": queued["job"]["id"]})["status"] == "completed"
    skill = wb.storage.get(wb.owner, "wb_skill", queued["skill"]["id"])
    assert skill["name"] == "constructor-path" and skill["drafting"]["modelInvoked"]


def test_consumer_rechecks_approval_after_loading_behavior_evidence(wb, monkeypatch):
    from workbench import skills
    from workbench.service import Service
    def model_call(system, user, images, model, max_tokens, trace_id, purpose):
        return json.dumps(runtime_response(user), ensure_ascii=False), {}, {}
    install_runtime(wb, model_call)
    skill = draft(wb)
    run(wb, call(wb, "POST", f"skills/{skill['id']}/validate", {"version": skill["version"]}))
    checked = call(wb, "GET", f"skills/{skill['id']}")["skill"]
    approved = call(wb, "POST", f"skills/{skill['id']}/approve",
        {"version": checked["version"], "contentHash": checked["contentHash"]})["skill"]
    original = skills.evaluation
    def deprecate(ctx, identifier):
        current = ctx.get("wb_skill", identifier)
        wb.storage.put(wb.owner, "wb_skill", {**current, "status": "DEPRECATED"}, current["version"])
        return original(ctx, identifier)
    monkeypatch.setattr(skills, "evaluation", deprecate)
    ctx = Service(wb.api, wb.collab.resolve_scope("alice", wb.project["id"]),
                  {"sub": "alice", "exp": wb.now // 1000 + 3600})
    with pytest.raises(CollaborationError):
        skills.resolve_approved(ctx, skill["id"], approved["version"], approved["contentHash"])
