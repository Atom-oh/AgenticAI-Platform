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
        d = {"harnessId": f"{name}-abc123", "harnessName": name, "status": "READY",
             "arn": f"arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:harness/{name}-abc123",
             "executionRoleArn": ROLE_ARN, "clientToken": "tok",
             "model": {"bedrockModelConfig": {"modelId": spec.get("model")}},
             "systemPrompt": [{"text": spec["systemPrompt"]}],
             "allowedTools": [f"bank_platform_tools___{t}" for t in spec.get("allowedTools", [])],
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
    return fh, fm


def _handler():
    spec = importlib.util.spec_from_file_location("handlers_agents_under_test", ROOT / "api" / "handlers" / "agents.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ctx(rid="r1"):
    gw = _FakeApigw()
    return Ctx(apigw=gw, conn_id="c1", email=ACTOR, rid=rid), gw


def _create_body(**kw):
    body = {"name": "card_benefit_agent", "title": "카드 혜택 상담", "description": "카드 혜택을 설명한다",
            "model": "global.anthropic.claude-sonnet-5",
            "systemPrompt": f"당신은 아톰은행 카드 혜택 상담 에이전트다. {SECRET_PROMPT_MARK} 공통 규칙을 따른다.",
            "allowedTools": ["lookup_customer_profile", "resolve_metric"], "skills": ["kwcag-accessibility"],
            "memory": True}
    body.update(kw)
    return body


def _create(h, **kw):
    ctx, gw = _ctx()
    h.agent_create(ctx, _create_body(**kw))
    return gw.posted[-1]


def _types(gw):
    return [e["type"] for e in gw.posted]


# ---------------- 라우트 ----------------
def test_routes():
    h = _handler()
    assert set(h.ROUTES) == {"agents_catalog", "agent_create", "agent_invoke", "agent_transition", "agent_get"}


# ---------------- 생성 ----------------
def test_create_goes_to_pending_and_mirrors(fakes, capsys):
    fh, fm = fakes
    h = _handler()
    ev = _create(h)
    assert ev["type"] == "agent_create" and ev["ok"] is True
    rec = ev["record"]
    assert rec["status"] == "PENDING_APPROVAL" and rec["recordType"] == "AGENT" and rec["recordVersion"] == "v1"
    assert rec["updatedBy"] == ACTOR and rec["payload"]["runtime"] == "AgentCore Harness"
    assert rec["payload"]["harnessArn"].endswith("harness/bank_card_benefit_agent-abc123")
    assert rec["payload"]["allowedTools"] == ["lookup_customer_profile", "resolve_metric"]
    assert rec["payload"]["skills"] == ["kwcag-accessibility"] and rec["payload"]["memory"] is True
    assert rec["payload"]["createdBy"] == ACTOR and rec["payload"]["scenario"] == "custom"
    assert ev["harness"] == {**ev["harness"], "status": "READY", "reused": False}
    assert ev["harness"]["arn"] == rec["payload"]["harnessArn"]
    # 미러는 PENDING_APPROVAL 레코드로 1회 호출
    assert len(fm.calls) == 1 and fm.calls[0]["status"] == "PENDING_APPROVAL" and fm.calls[0]["name"] == "card_benefit_agent"
    assert ev["agentcoreRegistry"]["status"] == "PENDING_APPROVAL" and ev["agentcoreRegistry"]["action"] == "created"
    # Harness 명세: createdBy = 사용자, scenario custom, 프롬프트 그대로
    ensure = next(c for c in fh.calls if c[0] == "ensure")[1]
    assert ensure["createdBy"] == ACTOR and ensure["scenario"] == "custom" and SECRET_PROMPT_MARK in ensure["systemPrompt"]
    # 감사: DRAFT 생성 → PENDING_APPROVAL (사유 기록)
    trail = api.audit_trail("card_benefit_agent", "v1")
    assert [a["to"] for a in trail] == ["PENDING_APPROVAL", "DRAFT"] and trail[0]["reason"] == "빌더 생성 — 승인 대기"
    # 로그에는 프롬프트 원문·이메일이 없다
    out = capsys.readouterr().out
    assert "agent.created" in out and SECRET_PROMPT_MARK not in out and ACTOR not in out


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


def test_create_harness_failure_returns_error_and_no_record(fakes, monkeypatch):
    fh, fm = fakes
    h = _handler()

    def boom(spec):
        raise RuntimeError("ValidationException: bad model")
    monkeypatch.setattr(harness_mod, "ensure_harness", boom)
    ev = _create(h)
    assert ev["ok"] is False and ev["stage"] == "harness" and "ValidationException" in ev["error"]
    assert api.get_record("card_benefit_agent", "v1") is None and fm.calls == []


def test_create_tolerates_mirror_failure(fakes):
    _, fm = fakes
    fm.fail = True
    h = _handler()
    ev = _create(h)
    assert ev["ok"] is True and ev["record"]["status"] == "PENDING_APPROVAL"
    assert "error" in ev["agentcoreRegistry"] and "unreachable" in ev["agentcoreRegistry"]["error"]


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
    # 데모: 즉시 승인 — 전이 + 미러 동기화
    ctx, gw = _ctx("r3")
    h.agent_transition(ctx, {"name": "card_benefit_agent", "version": "v1", "to": "APPROVED", "reason": "데모 — 즉시 승인"})
    ev = gw.posted[-1]
    assert ev["type"] == "agent_transition" and ev["ok"] and ev["record"]["status"] == "APPROVED"
    assert ev["audit"]["actor"] == ACTOR and ev["transition"] == "approve"
    assert len(fm.calls) == 2 and fm.calls[-1]["status"] == "APPROVED" and fm.calls[-1]["statusReason"] == "데모 — 즉시 승인"
    assert ev["agentcoreRegistry"]["status"] == "APPROVED"
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
    assert done["sessionId"].endswith("-session") and done["stopReason"] == "end_turn" and done["toolCalls"] == 1
    assert "error" not in done and done["errors"] == [] and "elapsedMs" in done
    inv = next(c for c in fh.calls if c[0] == "invoke")
    assert inv[1].endswith("bank_card_benefit_agent-abc123") and SECRET_MESSAGE_MARK in inv[2] and inv[3] is None
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
    assert fh.calls[-1][3] == sid and gw.posted[-1]["sessionId"] == sid


def test_invoke_harness_exception_ends_with_done_error(fakes):
    fh, _ = fakes
    h = _handler()
    _create(h)
    api.transition("card_benefit_agent", "v1", "APPROVED", ACTOR)
    fh.fail_invoke = True
    ctx, gw = _ctx()
    h.agent_invoke(ctx, {"name": "card_benefit_agent", "message": "hi"})
    done = gw.posted[-1]
    assert done["type"] == "agent.done" and "Harness 호출 실패" in done["error"] and "ThrottlingException" in done["error"]


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
    api.create_record({"name": "s2_mydata_advisor", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
                       "description": "파이프라인형", "tags": ["s2"],
                       "payload": {"entry": "handlers.s2", "scenario": "S2", "model": "m"}}, ACTOR, status="APPROVED")
    ctx, gw = _ctx()
    h.agents_catalog(ctx, {})
    ev = gw.posted[-1]
    assert ev["type"] == "agents_catalog"
    by = {a["name"]: a for a in ev["agents"]}
    a = by["card_benefit_agent"]
    assert a["status"] == "PENDING_APPROVAL" and a["agentcoreStatus"] == "PENDING_APPROVAL" and a["harnessStatus"] == "READY"
    assert a["harnessArn"].endswith("bank_card_benefit_agent-abc123") and a["title"] == "카드 혜택 상담"
    assert a["model"] == "global.anthropic.claude-sonnet-5" and a["allowedTools"] == ["lookup_customer_profile", "resolve_metric"]
    assert a["skills"] == ["kwcag-accessibility"] and a["memory"] is True and a["scenario"] == "custom"
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
    assert ev["models"] == ["global.anthropic.claude-sonnet-5", "global.anthropic.claude-opus-5"]
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
    assert a["harnessStatus"] == "unknown" and "AccessDeniedException" in a["harnessError"]
    assert a["agentcoreStatus"] is None and "us-east-1" in ev["agentcoreRegistryError"]
    assert a["harnessArn"].endswith("abc123")  # payload 의 ARN 은 그대로 보인다
    assert ev["gateway"] == {"arn": "", "url": ""}


def test_agent_get_hides_execution_role(fakes):
    h = _handler()
    _create(h)
    ctx, gw = _ctx()
    h.agent_get(ctx, {"name": "card_benefit_agent"})
    ev = gw.posted[-1]
    assert ev["type"] == "agent_get" and ev["ok"] and ev["record"]["recordVersion"] == "v1"
    hs = ev["harness"]
    assert hs["status"] == "READY" and hs["arn"].endswith("abc123") and hs["harnessName"] == "bank_card_benefit_agent"
    assert SECRET_PROMPT_MARK in hs["systemPrompt"] and isinstance(hs["systemPrompt"], str)
    assert "executionRoleArn" not in json.dumps(ev) and "clientToken" not in json.dumps(ev) and ROLE_ARN not in json.dumps(ev)
    assert [a["to"] for a in ev["audit"]] == ["PENDING_APPROVAL", "DRAFT"]
    h.agent_get(ctx, {"name": "nope"})
    assert gw.posted[-1]["ok"] is False and gw.posted[-1]["code"] == 404
