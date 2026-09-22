"""에이전트 빌더 핸들러 테스트 — 오프라인 (AgentCore Harness/Registry 는 페이크, Registry 는 인메모리).

검증: 생성 → PENDING_APPROVAL + 미러 호출 · 미승인 호출 거부(Harness 미호출) · 승인 후 스트리밍/usage ·
잘못된 도구/이름/스킬 거부 · 카탈로그 조인 · 오류 내성 · executionRoleArn 비노출 · 로그에 프롬프트 원문 없음.
실행: cd platform && python3 -m pytest tests/test_agents_handler.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
for _k in ("REGISTRY_TABLE", "TRACE_TABLE", "CACHE_TABLE", "GATEWAY_ARN", "GATEWAY_URL"):
    os.environ.pop(_k, None)
os.environ["REGISTRY_EMBED"] = "0"
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

from agentcore import harness as harness_mod  # noqa: E402
from agentcore import registry_mirror as mirror_mod  # noqa: E402
from common.ctx import Ctx  # noqa: E402
from registry import api  # noqa: E402

ACTOR = "demo@atomai.click"
SECRET_PROMPT_MARK = "PROMPT_SECRET_ZQX9"
SECRET_MESSAGE_MARK = "MSG_SECRET_KJ7"
ROLE_ARN = "arn:aws:iam::123456789012:role/bank-harness-exec-SECRET"


# ---------------- 페이크 ----------------
class FakeHarness:
    def __init__(self):
        self.harnesses: dict = {}
        self.calls: list = []
        self.fail_list = False
        self.fail_find = False
        self.fail_invoke = False

    def list_platform_harnesses(self):
        self.calls.append(("list",))
        if self.fail_list:
            raise RuntimeError("AccessDeniedException: list_harnesses")
        return [{"harnessId": d["harnessId"], "harnessName": n, "status": d["status"]} for n, d in self.harnesses.items()]

    def find_harness(self, name):
        self.calls.append(("find", name))
        if self.fail_find:
            raise RuntimeError("boom find")
        return self.harnesses.get(name)

    def ensure_harness(self, spec):
        self.calls.append(("ensure", dict(spec)))
        name = f"bank_{spec['name']}"
        if name in self.harnesses:
            return self.harnesses[name]
        d = {**harness_mod.build_config(spec), "harnessId": f"{name}-abc123", "harnessName": name, "status": "READY",
             "arn": f"arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:harness/{name}-abc123",
             "executionRoleArn": ROLE_ARN, "clientToken": "tok",
             "memory": {"disabled": {}}, "maxIterations": 12, "timeoutSeconds": 120}
        self.harnesses[name] = d
        return d

    def invoke_stream(self, arn, text, session_id=None):
        self.calls.append(("invoke", arn, text, session_id))
        if self.fail_invoke:
            raise RuntimeError("ThrottlingException: slow down")
        yield ("text", "안녕")
        yield ("tool_start", {"name": "lookup_customer_profile", "toolUseId": "t1"})
        yield ("tool_input", {"name": "lookup_customer_profile", "input": json.dumps({"question": "우대금리"})})
        yield ("text", "하세요")
        yield ("meta", {"usage": {"inputTokens": 120, "outputTokens": 45, "totalTokens": 165},
                        "stopReason": "end_turn", "sessionId": session_id or "abcdef0123456789abcdef0123456789-session"})


class FakeMirror:
    def __init__(self):
        self.calls: list = []
        self.fail = False
        self.fail_find = False

    def mirror(self, record):
        self.calls.append(dict(record))
        if self.fail:
            raise RuntimeError("AgentCore Registry unreachable")
        return {"recordId": "rec-1", "status": record["status"], "action": "created" if len(self.calls) == 1 else "exists",
                "descriptorType": "CUSTOM"}

    def find_record(self, name, version):
        if self.fail_find:
            raise RuntimeError("no route to us-east-1")
        for r in reversed(self.calls):
            if r["name"] == name and r.get("recordVersion") == version:
                return {"registryRecordId": "rec-1", "name": name, "recordVersion": version, "status": r["status"]}
        return None


class _FakeApigw:
    def __init__(self):
        self.posted = []

    def post_to_connection(self, ConnectionId, Data):
        self.posted.append(json.loads(Data.decode()))


@pytest.fixture(autouse=True)
def fresh_store():
    api.reset_for_tests()
    for s in ("bank-publishing-conventions", "kwcag-accessibility"):
        api.create_record({"name": s, "recordVersion": "v1", "recordType": "SKILL", "description": "스킬",
                           "payload": {"path": f"skills/{s}.md"}}, ACTOR, status="APPROVED")
    yield


@pytest.fixture
def fakes(monkeypatch):
    fh, fm = FakeHarness(), FakeMirror()
    for attr in ("list_platform_harnesses", "find_harness", "ensure_harness", "invoke_stream"):
        monkeypatch.setattr(harness_mod, attr, getattr(fh, attr))
    monkeypatch.setattr(mirror_mod, "mirror", fm.mirror)
    monkeypatch.setattr(mirror_mod, "find_record", fm.find_record)
    monkeypatch.setattr(harness_mod, "HARNESS_ROLE_ARN", ROLE_ARN)
    monkeypatch.setattr(harness_mod, "GATEWAY_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:gateway/synthetic")
    return fh, fm


def _handler():
    spec = importlib.util.spec_from_file_location("handlers_agents_under_test", ROOT / "api" / "handlers" / "agents.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ctx(rid="r1", user_sub="synthetic-user-one"):
    gw = _FakeApigw()
    return Ctx(apigw=gw, conn_id="c1", email=ACTOR, rid=rid, user_sub=user_sub), gw


def _create_body(**kw):
    body = {"name": "card_benefit_agent", "title": "카드 혜택 상담", "description": "카드 혜택을 설명한다",
            "model": "global.anthropic.claude-sonnet-5",
            "systemPrompt": f"당신은 아톰은행 카드 혜택 상담 에이전트다. {SECRET_PROMPT_MARK} 공통 규칙을 따른다.",
            "allowedTools": ["lookup_customer_profile", "resolve_metric"], "skills": ["kwcag-accessibility"],
            "memory": False}
    body.update(kw)
    return body


def _create(h, **kw):
    ctx, gw = _ctx()
    h.agent_create(ctx, _create_body(**kw))
    return gw.posted[-1]


def _types(gw):
    return [e["type"] for e in gw.posted]


def _approve(h, name="card_benefit_agent"):
    from agentcore.administration import apply_request
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": name, "version": "v1", "to": "APPROVED", "reason": "Synthetic administrator approval"})
    request = gw.posted[-1]["request"]
    return apply_request(request["name"], request["version"])


# ---------------- 라우트 ----------------
def test_routes():
    h = _handler()
    assert set(h.ROUTES) == {"agents_catalog", "agent_create", "agent_invoke", "agent_transition", "agent_get"}


# ---------------- 생성 ----------------
def test_create_records_a_request_without_agentcore_control_calls(fakes, capsys):
    fh, fm = fakes
    h = _handler()
    ev = _create(h)
    assert ev["type"] == "agent_create" and ev["ok"] is True
    rec = ev["record"]
    assert rec["status"] == "PENDING_APPROVAL" and rec["recordType"] == "AGENT" and rec["recordVersion"] == "v1"
    assert rec["updatedBy"] == ACTOR and rec["payload"]["runtime"] == "AgentCore Harness"
    assert "harnessArn" not in rec["payload"]
    assert rec["payload"]["allowedTools"] == ["lookup_customer_profile", "resolve_metric"]
    assert rec["payload"]["skills"] == ["kwcag-accessibility"] and rec["payload"]["memory"] is False
    assert rec["payload"]["createdBy"] == ACTOR and rec["payload"]["scenario"] == "custom"
    assert ev["harness"]["status"] == "PENDING_ADMIN" and ev["harness"]["arn"] is None
    assert fh.calls == [] and fm.calls == []
    assert ev["agentcoreRegistry"]["status"] == "PENDING_ADMIN"
    # 감사: DRAFT 생성 → PENDING_APPROVAL (사유 기록)
    trail = api.audit_trail("card_benefit_agent", "v1")
    assert [a["to"] for a in trail] == ["PENDING_APPROVAL", "DRAFT"] and trail[0]["reason"] == "빌더 생성 — 승인 대기"
    # 로그에는 프롬프트 원문·이메일이 없다
    out = capsys.readouterr().out
    assert "agent.requested" in out and SECRET_PROMPT_MARK not in out and ACTOR not in out


def test_create_rejects_unknown_tool(fakes):
    fh, fm = fakes
    h = _handler()
    ev = _create(h, allowedTools=["lookup_customer_profile", "drop_database"])
    assert ev["ok"] is False and ev["code"] == 400 and "drop_database" in ev["error"]
    assert not any(c[0] == "ensure" for c in fh.calls) and fm.calls == []
    assert api.get_record("card_benefit_agent", "v1") is None


@pytest.mark.parametrize("bad,needle", [
    ({"name": "CardAgent"}, "snake_case"),
    ({"name": "ab"}, "snake_case"),
    ({"name": "1abc"}, "snake_case"),
    ({"skills": ["nonexistent-skill"]}, "SKILL"),
    ({"systemPrompt": "   "}, "프롬프트"),
    ({"model": "global.anthropic.claude-haiku-9"}, "모델"),
])
def test_create_validation(fakes, bad, needle):
    fh, _ = fakes
    h = _handler()
    ev = _create(h, **bad)
    assert ev["ok"] is False and ev["code"] == 400 and needle in ev["error"]
    assert not any(c[0] == "ensure" for c in fh.calls)


def test_create_duplicate_name_is_409_without_touching_harness(fakes):
    fh, _ = fakes
    h = _handler()
    assert _create(h)["ok"]
    n = len(fh.calls)
    ev = _create(h)
    assert ev["ok"] is False and ev["code"] == 409 and len(fh.calls) == n


def test_admin_provision_failure_preserves_the_unapproved_request(fakes, monkeypatch):
    fh, fm = fakes
    h = _handler()

    def boom(spec):
        raise RuntimeError("ValidationException: bad model")
    monkeypatch.setattr(harness_mod, "ensure_harness", boom)
    ev = _create(h)
    assert ev["ok"] and fh.calls == []
    with pytest.raises(RuntimeError):
        _approve(h)
    assert api.get_record("card_benefit_agent", "v1")["status"] == "PENDING_APPROVAL" and fm.calls == []


def test_create_tolerates_mirror_failure(fakes):
    _, fm = fakes
    fm.fail = True
    h = _handler()
    ev = _create(h)
    assert ev["ok"] is True and ev["record"]["status"] == "PENDING_APPROVAL"
    assert ev["agentcoreRegistry"]["status"] == "PENDING_ADMIN" and fm.calls == []


def test_generic_registry_route_cannot_apply_agent_approval(fakes):
    from handlers.registry import registry_transition
    fh, fm = fakes
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    registry_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "Request"})
    assert gw.posted[-1]["request"]["status"] == "PENDING_ADMIN"
    assert api.get_record("card_benefit_agent", "v1")["status"] == "PENDING_APPROVAL"
    assert not fh.calls and not fm.calls


def test_generic_registry_create_cannot_supply_an_approved_agent(fakes):
    from handlers.registry import registry_create
    fh, fm = fakes
    ctx, gw = _ctx()
    registry_create(ctx, {"record": {"name": "forged_agent", "recordVersion": "v1",
        "recordType": "AGENT", "status": "APPROVED", "description": "Synthetic forgery",
        "payload": {"runtime": "AgentCore Harness", "harnessArn": "arn:synthetic"}}})
    assert gw.posted[-1]["code"] == 403
    assert api.get_record("forged_agent", "v1") is None and not fh.calls and not fm.calls


def test_admin_request_reconciles_a_failed_mirror_without_reprovisioning(fakes):
    from agentcore.administration import apply_request
    fh, fm = fakes
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "Synthetic"})
    request = gw.posted[-1]["request"]
    fm.fail = True
    with pytest.raises(RuntimeError):
        apply_request(request["name"])
    assert api.get_record("card_benefit_agent", "v1")["status"] == "APPROVED"
    fm.fail = False
    result = apply_request(request["name"])
    assert result["request"]["status"] == "APPROVED"
    assert len([call for call in fh.calls if call[0] == "ensure"]) == 1
    assert apply_request(request["name"])["replayed"] is True


def test_iam_reconciliation_updates_an_unapproved_orphan_with_an_exact_hash(fakes, monkeypatch):
    from types import SimpleNamespace
    from agentcore.administration import apply_request, harness_fingerprint
    fh, _ = fakes
    existing = fh.ensure_harness(_create_body(systemPrompt="Old synthetic specification"))
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "Reconcile"})
    request = gw.posted[-1]["request"]
    calls = []

    def update(**parameters):
        calls.append(parameters)
        existing.update({key: value for key, value in parameters.items() if key not in {"harnessId", "clientToken"}})
        existing["status"] = "READY"
        return {}

    monkeypatch.setattr(harness_mod, "ctl", lambda: SimpleNamespace(
        update_harness=update, get_harness=lambda **kwargs: {"harness": existing}))
    result = apply_request(request["name"], reconcile_hash=harness_fingerprint(existing))
    assert result["record"]["status"] == "APPROVED"
    assert len(calls) == 1 and calls[0]["harnessId"] == existing["harnessId"]
    assert SECRET_PROMPT_MARK in existing["systemPrompt"][0]["text"]


def test_agent_invocation_requires_the_verified_subject(fakes):
    fh, _ = fakes
    ctx, gw = _ctx(user_sub=None)
    _handler().agent_invoke(ctx, {"name": "card_benefit_agent", "message": "hello", "userSub": "forged"})
    assert gw.posted[-1]["code"] == 401
    assert not fh.calls


def test_user_cannot_mark_an_administrative_request_as_applied(fakes):
    from handlers.registry import registry_transition
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "Request"})
    request = gw.posted[-1]["request"]
    registry_transition(ctx, {"name": request["name"], "version": "v1", "to": "PENDING_APPROVAL", "reason": "forged"})
    assert gw.posted[-1]["code"] == 403
    assert api.get_record(request["name"], "v1")["status"] == "DRAFT"


def test_admin_approval_fences_specification_changes_during_provisioning(fakes, monkeypatch):
    from agentcore.administration import apply_request
    from registry.model import RegistryError
    fh, fm = fakes
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "Request"})
    request = gw.posted[-1]["request"]
    ensure = harness_mod.ensure_harness

    def mutate(spec):
        result = ensure(spec)
        record = api.get_record("card_benefit_agent", "v1")
        api.get_store().rewrite(record["name"], "v1", {"payload": {**record["payload"], "systemPrompt": "Changed specification"}}, "admin")
        return result

    monkeypatch.setattr(harness_mod, "ensure_harness", mutate)
    with pytest.raises(RegistryError):
        apply_request(request["name"])
    assert api.get_record("card_benefit_agent", "v1")["status"] == "PENDING_APPROVAL"
    assert not fm.calls


def test_old_approval_request_cannot_survive_a_rejection_and_resubmission(fakes):
    from agentcore.administration import apply_request
    from registry.model import ConflictError
    fh, _ = fakes
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "Old request"})
    request = gw.posted[-1]["request"]
    for state in ["REJECTED", "DRAFT", "PENDING_APPROVAL"]:
        api.transition("card_benefit_agent", "v1", state, "admin", "New review cycle")
    with pytest.raises(ConflictError):
        apply_request(request["name"])
    assert not fh.calls


# ---------------- 호출 — Consumer 게이트 ----------------
def test_invoke_refused_when_not_approved(fakes):
    fh, _ = fakes
    h = _handler()
    assert _create(h)["record"]["status"] == "PENDING_APPROVAL"
    ctx, gw = _ctx("r2")
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": "안녕"})
    assert _types(gw) == ["agent.done"]
    done = gw.posted[-1]
    assert "Consumer 게이트" in done["error"] and done["status"] == "PENDING_APPROVAL" and done["reqId"] == "r2"
    assert not any(c[0] == "invoke" for c in fh.calls), "미승인 에이전트는 Harness 를 호출하면 안 된다"


def test_invoke_unknown_agent(fakes):
    h = _handler()
    ctx, gw = _ctx()
    h.agent_invoke(ctx, {"name": "ghost_agent", "message": "hi"})
    assert _types(gw) == ["agent.done"] and "에이전트 없음" in gw.posted[-1]["error"]


def test_invoke_pipeline_agent_without_harness(fakes):
    """시드형(파이프라인) AGENT 레코드는 Harness 가 없다 — 승인돼도 정직하게 '없음'으로 끝낸다."""
    fh, _ = fakes
    h = _handler()
    api.create_record({"name": "s2_mydata_advisor", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
                       "payload": {"entry": "handlers.s2", "scenario": "S2"}}, ACTOR, status="APPROVED")
    ctx, gw = _ctx()
    h.agent_invoke(ctx, {"name": "s2_mydata_advisor", "message": "hi"})
    assert _types(gw) == ["agent.done"] and "Harness 가 없습니다" in gw.posted[-1]["error"]
    assert not any(c[0] == "invoke" for c in fh.calls)


def test_transition_then_invoke_streams(fakes, capsys):
    fh, fm = fakes
    h = _handler()
    _create(h)
    # User requests cannot apply the state; IAM administration provisions it.
    ctx, gw = _ctx("r3")
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "데모 — 즉시 승인"})
    ev = gw.posted[-1]
    assert ev["type"] == "agent_transition" and ev["ok"] and ev["record"]["status"] == "PENDING_APPROVAL"
    assert fh.calls == [] and fm.calls == []
    from agentcore.administration import apply_request
    applied = apply_request(ev["request"]["name"])
    assert applied["record"]["status"] == "APPROVED" and applied["audit"]["actor"] == "admin"
    assert len(fm.calls) == 1 and fm.calls[-1]["status"] == "APPROVED"
    assert SECRET_PROMPT_MARK not in json.dumps(fm.calls) and ACTOR not in json.dumps(fm.calls)
    # 호출
    capsys.readouterr()
    ctx, gw = _ctx("r4")
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": f"우대금리 조건 {SECRET_MESSAGE_MARK}"})
    types = _types(gw)
    assert types[0] == "agent.stage" and gw.posted[0]["step"] == "gate" and gw.posted[0]["status"] == "APPROVED"
    assert types[-1] == "agent.done"
    tokens = [e["t"] for e in gw.posted if e["type"] == "agent.token"]
    assert tokens == ["안녕", "하세요"]
    stages = [e for e in gw.posted if e["type"] == "agent.stage"]
    ts = next(s for s in stages if s["step"] == "tool_start")
    assert ts["name"] == "lookup_customer_profile" and ts["toolUseId"] == "t1" and ts["plane"] == "vpc"
    ti = next(s for s in stages if s["step"] == "tool_input")
    assert json.loads(ti["input"]) == {"question": "우대금리"}
    done = gw.posted[-1]
    assert done["usage"]["inputTokens"] == 120 and done["usage"]["outputTokens"] == 45
    assert done["modelId"] == "global.anthropic.claude-sonnet-5" and done["runtime"] == "AgentCore Harness"
    assert done["sessionId"] and len(done["runtimeSessionId"]) == 64 and done["stopReason"] == "end_turn" and done["toolCalls"] == 1
    assert "error" not in done and done["errors"] == [] and "elapsedMs" in done
    inv = next(c for c in fh.calls if c[0] == "invoke")
    assert inv[1].endswith("bank_card_benefit_agent-abc123") and SECRET_MESSAGE_MARK in inv[2] and inv[3] == done["runtimeSessionId"]
    # 트레이스: 시나리오 AGENT, 토큰 실측, 메시지 원문 없음
    out = capsys.readouterr().out
    trace = next(json.loads(l) for l in out.splitlines() if '"event": "trace.recorded"' in l)
    assert trace["scenario"] == "AGENT" and trace["tokensIn"] == 120 and trace["tokensOut"] == 45
    assert trace["route"] == "harness" and trace["plane"] == "agentcore" and trace["agent"] == "card_benefit_agent"
    assert trace["piiOutbound"] == 0 and trace["piiDetectors"] == ["tool-egress-gate"] and trace["blocked"] is False
    assert SECRET_MESSAGE_MARK not in out and ACTOR not in out and "queryHash" in trace
    # 세션 유지: 클라이언트가 준 sessionId 가 Harness 로 전달되고 done 에 되돌아온다
    sid = "0123456789abcdef0123456789abcdef-session"
    ctx, gw = _ctx("r5")
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": "다시", "sessionId": sid})
    bound = fh.calls[-1][3]
    assert bound != sid and gw.posted[-1]["sessionId"] == sid
    ctx, gw = _ctx("r6")
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": "다시", "sessionId": sid})
    assert fh.calls[-1][3] == bound
    ctx, gw = _ctx("r7", user_sub="synthetic-user-two")
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": "다시", "sessionId": sid})
    assert fh.calls[-1][3] != bound and gw.posted[-1]["sessionId"] == sid


def test_invoke_harness_exception_ends_with_done_error(fakes):
    fh, _ = fakes
    h = _handler()
    _create(h)
    _approve(h)
    fh.fail_invoke = True
    ctx, gw = _ctx()
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": "hi"})
    done = gw.posted[-1]
    assert done["type"] == "agent.done" and "에이전트 호출 실패" in done["error"] and "RuntimeError" in done["error"]
    assert "slow down" not in json.dumps(done)


def test_transition_invalid_is_400_event(fakes):
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "DEPRECATED", "reason": "x"})
    ev = gw.posted[-1]
    assert ev["type"] == "agent_transition" and ev["ok"] is False and ev["code"] == 400 and "허용되지 않은 전이" in ev["error"]


# ---------------- 카탈로그 · 조회 ----------------
def test_catalog_joins_registry_harness_and_agentcore(fakes, monkeypatch):
    fh, fm = fakes
    monkeypatch.setenv("GATEWAY_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:gateway/gw1")
    monkeypatch.setenv("GATEWAY_URL", "https://gw1.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp")
    h = _handler()
    _create(h)
    _approve(h)
    api.create_record({"name": "s2_mydata_advisor", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
                       "description": "파이프라인형", "tags": ["s2"],
                       "payload": {"entry": "handlers.s2", "scenario": "S2", "model": "m"}}, ACTOR, status="APPROVED")
    ctx, gw = _ctx()
    h.agents_catalog(ctx, {})
    ev = gw.posted[-1]
    assert ev["type"] == "agents_catalog"
    by = {a["name"]: a for a in ev["agents"]}
    a = by["card_benefit_agent"]
    assert a["status"] == "APPROVED" and a["agentcoreStatus"] == "APPROVED" and a["harnessStatus"] == "READY"
    assert a["harnessArn"].endswith("bank_card_benefit_agent-abc123") and a["title"] == "카드 혜택 상담"
    assert a["model"] == "global.anthropic.claude-sonnet-5" and a["allowedTools"] == ["lookup_customer_profile", "resolve_metric"]
    assert a["skills"] == ["kwcag-accessibility"] and a["memory"] is False and a["scenario"] == "custom"
    assert a["createdBy"] == ACTOR and a["updatedAt"] and a["version"] == "v1"
    p = by["s2_mydata_advisor"]
    assert p["harnessStatus"] == "none" and p["harnessArn"] is None and p["agentcoreStatus"] is None and p["scenario"] == "S2"
    # Harness 목록은 1회, 상세는 존재하는 것만
    assert fh.calls.count(("list",)) == 1
    assert [c for c in fh.calls if c[0] == "find" and c[1] == "bank_s2_mydata_advisor"] == []
    # 도구 10종 / 스킬 / 모델 / 게이트웨이 / 공통 규칙
    assert len(ev["tools"]) == 10 and {"name", "description"} <= set(ev["tools"][0])
    assert {t["name"] for t in ev["tools"]} >= {"lookup_customer_profile", "run_screen_gates", "search_internal_documents"}
    assert {s["name"] for s in ev["skills"]} == {"bank-publishing-conventions", "kwcag-accessibility"}
    assert all(s["status"] == "APPROVED" and s["version"] == "v1" for s in ev["skills"])
    assert ev["models"] == ["global.anthropic.claude-sonnet-5", "global.anthropic.claude-opus-5",
                            "global.openai.gpt-6-astra", "global.anthropic.claude-fable-5-1",
                            "global.anthropic.claude-fable-5"]
    assert ev["gateway"]["url"].startswith("https://gw1.") and ev["gateway"]["arn"].endswith("gateway/gw1")
    assert "한국어로 답한다" in ev["commonRules"] and ev["harnessError"] is None and ev["agentcoreRegistryError"] is None
    assert "executionRoleArn" not in json.dumps(ev)


def test_catalog_tolerates_backend_errors(fakes):
    fh, fm = fakes
    h = _handler()
    _create(h)
    fh.fail_list, fm.fail_find = True, True
    ctx, gw = _ctx()
    h.agents_catalog(ctx, {})
    ev = gw.posted[-1]
    a = ev["agents"][0]
    assert a["harnessStatus"] == "unknown" and a["harnessError"] == "RuntimeError"
    assert a["agentcoreStatus"] is None and ev["agentcoreRegistryError"] == "RuntimeError"
    assert a["harnessArn"] is None
    assert ev["gateway"] == {"arn": "", "url": ""}


def test_agent_get_hides_execution_role(fakes):
    h = _handler()
    _create(h)
    _approve(h)
    ctx, gw = _ctx()
    h.agent_get(ctx, {"name": "card_benefit_agent"})
    ev = gw.posted[-1]
    assert ev["type"] == "agent_get" and ev["ok"] and ev["record"]["recordVersion"] == "v1"
    hs = ev["harness"]
    assert hs["status"] == "READY" and hs["arn"].endswith("abc123") and hs["harnessName"] == "bank_card_benefit_agent"
    assert SECRET_PROMPT_MARK in hs["systemPrompt"] and isinstance(hs["systemPrompt"], str)
    assert "executionRoleArn" not in json.dumps(ev) and "clientToken" not in json.dumps(ev) and ROLE_ARN not in json.dumps(ev)
    assert [a["to"] for a in ev["audit"]] == ["APPROVED", "PENDING_APPROVAL", "DRAFT"]
    h.agent_get(ctx, {"name": "nope"})
    assert gw.posted[-1]["ok"] is False and gw.posted[-1]["code"] == 404
