"""AgentCore Runtime(Strands 컨테이너) 어댑터 테스트 — 오프라인 (AWS 호출 없음).

검증: SSE 파싱(text/tool/boundary/meta, 빈 줄·주석·비JSON 라인 관용, 접두어 없는 JSON 라인) · meta 누락 시 보정 ·
invoke.stream 디스패치(runtime vs harness, 'bank_' 접두어 제거, 미지원 런타임 오류) · 세션 ID 규격(≥33자, 결정론) ·
컨테이너 익명화 게이트 규칙(importlib 로 agents/boundary_gate.py 직접 로드: 'CUST-0001' 거부, 마스킹 페이로드 통과,
RULES 가 api/common/pii.py 와 동일, 시스템 프롬프트+스킬 본문이 규칙에 걸리지 않음).
실행: cd platform && python3 -m pytest tests/test_agents_runtime.py -q
"""
from __future__ import annotations

import importlib.util
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")
os.environ.pop("AGENTS_RUNTIME_ARN", None)

from agentcore import invoke, runtime  # noqa: E402
from agentcore import agent_specs  # noqa: E402
from common import pii  # noqa: E402
from tests.test_runtime_model_selection import runtime_app


def _load_container_module(name: str):
    path = ROOT / "agents" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"agents_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


boundary_gate = _load_container_module("boundary_gate")


# ---------------- SSE 파싱 ----------------
class FakeBody:
    """botocore StreamingBody 흉내 — iter_lines() 가 bytes 라인을 낸다."""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        for line in self._lines:
            yield line.encode("utf-8") if isinstance(line, str) else line


SSE_LINES = [
    "",
    ": keep-alive comment",
    'data: {"type":"boundary","chars":1200,"estTokens":400,"piiRules":0,"seq":1}',
    'data: {"type":"text","t":"규정 "}',
    "",
    'data: {"type":"tool_start","name":"analyze_regulation_impact","toolUseId":"tu-1"}',
    'data: {"type":"tool_input","name":"analyze_regulation_impact","toolUseId":"tu-1","input":"{\\"reg_code\\":\\"REG-LN-001\\"}"}',
    "data: this is not json",
    '{"type":"tool_result","name":"analyze_regulation_impact","toolUseId":"tu-1","chars":842,"status":"success"}',
    'data: {"type":"text","t":"영향 분석 결과"}',
    "event: something",
    'data: {"type":"error","message":"soft warning"}',
    'data: {"type":"meta","usage":{"inputTokens":1500,"outputTokens":300},"modelId":"global.anthropic.claude-sonnet-5",'
    '"stopReason":"end_turn","sessionId":"abc","runtime":"agentcore-runtime/strands"}',
    "",
]


def test_parse_sse_yields_harness_tuple_protocol():
    out = list(runtime.to_tuples(runtime.parse_events(FakeBody(SSE_LINES)), "fallback-sid"))
    kinds = [k for k, _ in out]
    assert kinds == ["boundary", "text", "tool_start", "tool_input", "tool_result", "text", "error", "meta"]
    assert "".join(d for k, d in out if k == "text") == "규정 영향 분석 결과"
    tool_start = next(d for k, d in out if k == "tool_start")
    assert tool_start == {"name": "analyze_regulation_impact", "toolUseId": "tu-1"}
    tool_result = next(d for k, d in out if k == "tool_result")
    assert tool_result["chars"] == 842 and "value" not in tool_result
    boundary = next(d for k, d in out if k == "boundary")
    assert boundary["piiRules"] == 0 and boundary["estTokens"] == 400
    meta = out[-1][1]
    assert meta["usage"] == {"inputTokens": 1500, "outputTokens": 300}
    assert meta["stopReason"] == "end_turn" and meta["sessionId"] == "abc"
    assert meta["runtime"] == "agentcore-runtime/strands" and "type" not in meta


def test_parse_sse_without_meta_synthesizes_final_meta():
    body = FakeBody(['data: {"type":"text","t":"partial"}', "garbage", ""])
    out = list(runtime.to_tuples(runtime.parse_events(body), "sid-1234567890123456789012345678901234"))
    assert out[0] == ("text", "partial")
    assert out[-1][0] == "meta"
    assert out[-1][1]["sessionId"] == "sid-1234567890123456789012345678901234"
    assert out[-1][1].get("incomplete") is True
    assert out[-1][1]["runtime"] == "agentcore-runtime/strands"


def test_parse_sse_runtime_wrapped_exception_becomes_error():
    body = FakeBody(['data: {"error":"boom","error_type":"RuntimeError","message":"An error occurred during streaming"}'])
    out = list(runtime.to_tuples(runtime.parse_events(body), "s" * 40))
    assert out[0][0] == "error" and "upstream details are withheld" in out[0][1] and "boom" not in out[0][1]


def test_parse_sse_accepts_bytes_and_str_bodies():
    raw = b'data: {"type":"text","t":"a"}\n\ndata: {"type":"text","t":"b"}\n'
    assert [d for k, d in runtime.to_tuples(runtime.parse_events(raw), "x" * 40) if k == "text"] == ["a", "b"]
    assert [d for k, d in runtime.to_tuples(runtime.parse_events(raw.decode()), "x" * 40) if k == "text"] == ["a", "b"]


# ---------------- 세션 ID ----------------
def test_session_id_meets_agentcore_minimum_and_is_deterministic():
    assert len(runtime.new_session_id()) >= 33
    assert len(runtime.normalize_session_id(None)) >= 33
    short = runtime.normalize_session_id("web-42")
    assert len(short) >= 33 and short.startswith("web-42-")
    assert runtime.normalize_session_id("web-42") == short  # 같은 입력 → 같은 세션 (멀티턴 유지)
    assert runtime.normalize_session_id("has spaces/and:colons").count(" ") == 0
    long = "L" * 40
    assert runtime.normalize_session_id(long) == long


def test_invoke_stream_sends_expected_request(monkeypatch):
    calls = {}

    class FakeClient:
        def invoke_agent_runtime(self, **kw):
            calls.update(kw)
            return {"contentType": "text/event-stream", "response": FakeBody(SSE_LINES)}

    monkeypatch.setattr(runtime, "_data", FakeClient())
    out = list(runtime.invoke_stream("arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/x",
                                     "regulation_impact_agent", "REG-LN-001 영향?", session_id="s1",
                                     model="global.anthropic.claude-opus-5"))
    assert calls["accept"] == "text/event-stream" and calls["contentType"] == "application/json"
    assert len(calls["runtimeSessionId"]) >= 33
    body = json.loads(calls["payload"].decode("utf-8"))
    assert body["agent"] == "regulation_impact_agent" and body["prompt"] == "REG-LN-001 영향?"
    assert body["model"] == "global.anthropic.claude-opus-5" and "sessionId" not in body
    assert out[-1][0] == "meta"


def test_invoke_stream_requires_arn():
    with pytest.raises(ValueError):
        list(runtime.invoke_stream("", "regulation_impact_agent", "x"))


# ---------------- 디스패치 ----------------
def test_dispatch_strands_runtime_strips_bank_prefix(monkeypatch):
    seen = {}

    def fake_runtime(arn, name, text, session_id=None, model=None):
        seen.update(arn=arn, name=name, text=text, session_id=session_id, model=model)
        yield ("text", "ok")
        yield ("meta", {"usage": {}, "stopReason": "end_turn", "sessionId": session_id or "gen", "runtime": "agentcore-runtime/strands"})

    def fake_harness(*a, **k):
        raise AssertionError("harness must not be called for strands runtime")

    monkeypatch.setattr(runtime, "invoke_stream", fake_runtime)
    from agentcore import harness
    monkeypatch.setattr(harness, "invoke_stream", fake_harness)
    monkeypatch.setenv("AGENTS_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/env-arn")

    rec = {"name": "bank_regulation_impact_agent", "payload": {"runtime": "agentcore-runtime/strands", "model": "global.anthropic.claude-sonnet-5"}}
    out = list(invoke.stream(rec, "질문", session_id="s-1", model="global.anthropic.claude-sonnet-5"))
    assert seen["name"] == "regulation_impact_agent" and seen["arn"].endswith("runtime/env-arn")
    assert seen["session_id"] == "s-1" and seen["text"] == "질문"
    assert [k for k, _ in out] == ["text", "meta"]

    # payload.runtimeArn 이 env 보다 우선
    rec2 = {"name": "regulation_impact_agent", "payload": {"runtime": "agentcore-runtime/strands", "runtimeArn": "arn:x:payload"}}
    list(invoke.stream(rec2, "q"))
    assert seen["arn"] == "arn:x:payload" and seen["name"] == "regulation_impact_agent"


def test_dispatch_harness(monkeypatch):
    seen = {}

    def fake_harness(arn, text, session_id=None):
        seen.update(arn=arn, text=text, session_id=session_id)
        yield ("meta", {"usage": {}, "stopReason": "end_turn", "sessionId": session_id})

    def fake_runtime(*a, **k):
        raise AssertionError("runtime must not be called for harness")

    from agentcore import harness
    monkeypatch.setattr(harness, "invoke_stream", fake_harness)
    monkeypatch.setattr(runtime, "invoke_stream", fake_runtime)
    rec = {"name": "mydata_advisor_agent", "payload": {"runtime": "AgentCore Harness", "harnessArn": "arn:h:1"}}
    out = list(invoke.stream(rec, "hello", session_id="abc"))
    assert seen == {"arn": "arn:h:1", "text": "hello", "session_id": "abc"}
    assert out[-1][0] == "meta"
    # runtime 표기가 없어도 harnessArn 이 있으면 Harness
    assert invoke.kind_of({"name": "x", "payload": {"harnessArn": "arn:h:2"}}) == "harness"


def test_dispatch_unknown_runtime_raises_and_describe_badges(monkeypatch):
    monkeypatch.delenv("AGENTS_RUNTIME_ARN", raising=False)
    with pytest.raises(ValueError):
        list(invoke.stream({"name": "pipeline_agent", "payload": {"runtime": "pipeline"}}, "x"))
    with pytest.raises(ValueError):  # strands 인데 ARN 이 어디에도 없음
        list(invoke.stream({"name": "a", "payload": {"runtime": "agentcore-runtime/strands"}}, "x"))
    d = invoke.describe({"name": "bank_a", "payload": {"runtime": "agentcore-runtime/strands", "runtimeArn": "arn:r", "model": "m"}})
    assert d["badge"] == "AgentCore Runtime · Strands" and d["tier"] == "0/1" and d["runtimeArn"] == "arn:r" and d["modelId"] == "m"
    h = invoke.describe({"name": "b", "payload": {"runtime": "AgentCore Harness", "harnessArn": "arn:h"}})
    assert h["badge"] == "AgentCore Harness (설정형)" and h["harnessArn"] == "arn:h"
    assert invoke.describe({"name": "c", "payload": {}})["badge"] is None
    assert invoke.agent_name({"name": "bank_regulation_impact_agent"}) == "regulation_impact_agent"


# ---------------- 컨테이너 익명화 게이트 (strands 없이 로드) ----------------
def test_boundary_gate_module_loads_without_strands():
    assert "strands" not in sys.modules or boundary_gate.STRANDS_AVAILABLE
    assert hasattr(boundary_gate, "BoundaryGateHook") and hasattr(boundary_gate, "GateRefused")


def test_boundary_gate_rules_match_api_pii_rules():
    ours = [(k, p.pattern, p.flags) for k, p in boundary_gate.RULES]
    theirs = [(k, p.pattern, p.flags) for k, p in pii.RULES]
    assert ours == theirs


def test_boundary_gate_refuses_identifier_and_passes_masked_payload(monkeypatch):
    monkeypatch.setattr(boundary_gate, "verify_independent", lambda text: {"count": 0, "hits": [], "detectors": ["rules", "guardrail"]})
    hook = boundary_gate.BoundaryGateHook()
    masked = [{"role": "user", "content": [{"text": "우대금리 얼마나 받아요?"}]},
              {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "lookup_customer_profile", "input": {"question": "우대금리"}}}]},
              {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "status": "success", "content": [{"json": {
                  "maskedProfile": {"customer": "⟨CUSTOMER_ID:1a2b3c4d⟩", "account": "⟨ACCOUNT_ID:9f8e7d6c⟩", "salaryTransferMonths": 12},
                  "rate": {"applied": "3.90%"}, "limit": {"krw": 200000000}}}]}}]}]
    m = hook.check(masked, "당신은 아톰은행 마이데이터 상담 에이전트다.")
    assert m["piiRules"] == 0 and m["chars"] > 0 and m["estTokens"] == m["chars"] // 3
    assert hook.drain() and hook.drain() == []  # 한 번만 배출
    assert hook.last["piiRules"] == 0

    bad = [{"role": "user", "content": [{"text": "고객 CUST-0001 의 한도는?"}]}]
    with pytest.raises(boundary_gate.GateRefused) as ei:
        hook.check(bad)
    assert str(ei.value) == "GateRefused: CUSTOMER_TOKEN" and ei.value.types == ["CUSTOMER_TOKEN"]
    pending = hook.drain()
    assert pending and pending[-1]["piiRules"] == 1 and pending[-1]["hits"] == ["CUSTOMER_TOKEN"]
    assert "CUST-0001" not in json.dumps(pending, ensure_ascii=False)  # 계측값에 식별자 원문이 없다
    assert hook.summary()["refused"] is True and hook.summary()["refusedTypes"] == ["CUSTOMER_TOKEN"]

    # 도구 결과(json 블록) 안의 식별자도 잡는다
    leak = [{"role": "user", "content": [{"toolResult": {"toolUseId": "t2", "content": [{"json": {"phone": "010-1234-5678"}}]}}]}]
    with pytest.raises(boundary_gate.GateRefused) as ei2:
        boundary_gate.BoundaryGateHook().check(leak)
    assert "PHONE" in ei2.value.types


def test_find_gate_refusal_walks_exception_chain():
    inner = boundary_gate.GateRefused(["EMAIL"])
    try:
        try:
            raise inner
        except boundary_gate.GateRefused as e:
            raise RuntimeError("event loop cycle failed") from e
    except RuntimeError as outer:
        assert boundary_gate.find_gate_refusal(outer) is inner
    assert boundary_gate.find_gate_refusal(ValueError("x")) is None


def test_system_prompts_and_skills_pass_the_gate():
    """시나리오 명세의 시스템 프롬프트와 스킬 본문이 게이트 규칙에 걸리면 컨테이너가 항상 거부한다 — 배포 전 검증."""
    skills_dir = ROOT / "skills"
    for spec in agent_specs.SCENARIO_AGENTS:
        parts = [spec["systemPrompt"]]
        for name in spec.get("skills") or []:
            p = skills_dir / f"{name}.md"
            assert p.is_file(), f"skill file missing: {p}"
            parts.append(f"[SKILL {name}]\n{p.read_text(encoding='utf-8')}")
        hits = boundary_gate.scan_rules("\n\n".join(parts))
        assert hits == [], f"{spec['name']}: {[h['type'] for h in hits]}"


def test_mcp_gateway_name_filtering_without_strands():
    mg = _load_container_module("mcp_gateway")
    discovered = ["platform___list_regulations", "platform___analyze_regulation_impact", "platform___lookup_customer_profile"]
    assert mg.filter_tool_names(discovered, ["list_regulations", "platform___analyze_regulation_impact"]) == discovered[:2]
    assert mg.filter_tool_names(discovered, []) == []
    assert mg.filter_tool_names(["trusted___tool", "other___tool"], ["trusted___tool"]) == ["trusted___tool"]
    assert mg.filter_tool_names(["trusted___tool", "other___tool"], ["tool"]) == []
    assert mg.missing_tool_names(["trusted___tool", "other___tool"], ["tool"]) == ["tool"]
    assert mg.missing_tool_names(["trusted___tool"], ["trusted___tool", "other___tool"]) == ["other___tool"]
    assert mg.bare_name("platform___run_screen_gates") == "run_screen_gates" and mg.bare_name("resolve_metric") == "resolve_metric"
    assert mg.SERVICE_NAME == "bedrock-agentcore"

@pytest.mark.parametrize("phase", ["start", "load"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_mcp_setup_failure_closes_partial_clients(monkeypatch, phase, cleanup_fails):
    from types import SimpleNamespace
    mg = _load_container_module("mcp_gateway")
    events = []

    def start():
        events.append("start")
        if phase == "start":
            raise ValueError("synthetic setup failure")

    def load(*args):
        events.append("load")
        raise ValueError("synthetic setup failure")

    def stop(*args):
        events.append("stop")
        if cleanup_fails:
            raise RuntimeError("synthetic close failure")

    monkeypatch.setattr(mg, "make_client", lambda *args: SimpleNamespace(start=start, stop=stop))
    monkeypatch.setattr(mg, "load_tools", load)
    with pytest.raises(mg.GatewayCleanupFailed if cleanup_fails else ValueError):
        mg.open_tools(["tool"])
    assert events == (["start", "stop"] if phase == "start" else ["start", "load", "stop"])


@pytest.mark.parametrize("tools_available", [True, False])
def test_runtime_uses_only_the_authenticated_session_and_reports_missing_tools(runtime_app, monkeypatch, tools_available):
    from types import SimpleNamespace
    app, _ = runtime_app
    sid = "authenticated-session-" + "a" * 32
    victim = "payload-selected-victim-" + "b" * 32
    app._session_put(victim, [{"private": "VICTIM_HISTORY_MARKER"}])
    opened, streamed = [], []
    monkeypatch.setattr(app.mcp_gateway, "bare_name", lambda name: name.split("___")[-1], raising=False)
    monkeypatch.setattr(app.mcp_gateway, "missing_tool_names",
                        _load_container_module("mcp_gateway").missing_tool_names, raising=False)

    def session(spec, model, actual_sid):
        opened.append(actual_sid)
        messages = app._session_get(actual_sid)

        async def stream(prompt):
            streamed.append(True)
            messages.append({"role": "user", "content": [{"text": prompt}]})
            yield {"data": "VICTIM_HISTORY_MARKER" if "VICTIM_HISTORY_MARKER" in json.dumps(messages) else "safe"}

        return SimpleNamespace(
            tools=[SimpleNamespace(tool_name=name) for name in spec["allowedTools"]] if tools_available else [],
            discovered=list(spec["allowedTools"]) if tools_available else [],
            skills_loaded=[], skills_missing=[], close=lambda: None,
            gate=SimpleNamespace(drain=lambda: [], summary=lambda: {}),
            agent=SimpleNamespace(messages=messages, stream_async=stream))

    monkeypatch.setattr(app, "_Session", session)

    async def collect():
        return [event async for event in app.run(
            {"agent": "regulation_impact_agent", "prompt": "Synthetic hello", "sessionId": victim}, sid)]

    events = asyncio.run(collect())
    assert opened == [sid]
    assert events[-1]["sessionId"] == sid and events[-1]["ignoredPayloadSessionId"] is True
    assert "VICTIM_HISTORY_MARKER" not in json.dumps(events)
    assert app._session_get(victim) == [{"private": "VICTIM_HISTORY_MARKER"}]
    if tools_available:
        assert streamed == [True] and app._session_get(sid)
    else:
        assert not streamed and events[-1]["toolsMissing"]
        assert any(event.get("code") == 502 for event in events)


def test_payload_session_cannot_replace_missing_authenticated_context(runtime_app):
    app, _ = runtime_app

    async def collect():
        return [event async for event in app.run({"agent": "regulation_impact_agent", "prompt": "hello", "sessionId": "x" * 64})]

    events = asyncio.run(collect())
    assert events[0]["code"] == 400
    assert events[-1]["ignoredPayloadSessionId"] is True


def test_missing_qualified_target_is_not_hidden_by_a_loaded_bare_name(runtime_app, monkeypatch):
    from types import SimpleNamespace
    app, _ = runtime_app
    spec = {**app.agent_specs.spec_by_name("regulation_impact_agent"),
            "allowedTools": ["target_a___lookup", "target_b___lookup"]}
    monkeypatch.setattr(app.agent_specs, "spec_by_name", lambda name: spec)
    monkeypatch.setattr(app.mcp_gateway, "missing_tool_names",
                        _load_container_module("mcp_gateway").missing_tool_names, raising=False)
    monkeypatch.setattr(app, "_Session", lambda *args: SimpleNamespace(
        tools=[SimpleNamespace(tool_name="lookup")], discovered=["target_a___lookup"],
        skills_loaded=[], skills_missing=[], close=lambda: None))

    async def collect():
        return [event async for event in app.run({"agent": spec["name"], "prompt": "hello"}, "s" * 64)]

    events = asyncio.run(collect())
    assert events[-1]["toolsMissing"] == ["target_b___lookup"]
    assert events[-1]["stopReason"] == "tools_unavailable"


def test_missing_runtime_skill_blocks_before_streaming(runtime_app, monkeypatch):
    from types import SimpleNamespace
    app, _ = runtime_app
    spec = app.agent_specs.spec_by_name("regulation_impact_agent")
    monkeypatch.setattr(app.mcp_gateway, "missing_tool_names",
                        _load_container_module("mcp_gateway").missing_tool_names, raising=False)
    # Deliberately no Agent/stream: any attempt to invoke it makes this fail.
    monkeypatch.setattr(app, "_Session", lambda *args: SimpleNamespace(
        tools=[], discovered=list(spec["allowedTools"]),
        skills_loaded=[], skills_missing=["required-skill"], close=lambda: None))

    async def collect():
        return [event async for event in app.run({"agent": spec["name"], "prompt": "hello"}, "s" * 64)]

    events = asyncio.run(collect())
    assert events[-1]["stopReason"] == "skills_unavailable"
    assert any(event.get("code") == 500 for event in events)
    assert not any(event["type"] == "text" for event in events)


@pytest.mark.parametrize("tools", [None, [], ["*"]])
def test_harness_configuration_never_defaults_to_all_tools(tools):
    from agentcore import harness
    with pytest.raises(ValueError, match="nonempty"):
        harness.build_config({"name": "synthetic", "systemPrompt": "safe", "allowedTools": tools})


@pytest.mark.parametrize("text", ["고객 CUST-0042", "person@example.invalid", "M12345678"])
def test_harness_input_is_checked_before_any_model_transport(monkeypatch, text):
    from agentcore import harness
    from engine.gate import GateRefused
    monkeypatch.setattr(harness, "data", lambda: pytest.fail("raw identifiers reached AgentCore"))
    with pytest.raises(GateRefused):
        list(harness.invoke_stream("synthetic-arn", text, "x" * 64))


@pytest.mark.parametrize("finish", ["complete", "cancel", "close"])
def test_same_session_turns_cannot_overwrite_history_and_release_after_exit(runtime_app, monkeypatch, finish):
    from types import SimpleNamespace
    app, _ = runtime_app
    sid = "s" * 64
    opened, closed = [], []
    app._session_put(sid, [{"text": "prior"}])
    monkeypatch.setattr(app.mcp_gateway, "missing_tool_names", lambda *args: [], raising=False)

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        def session(spec, model, actual_sid):
            opened.append(actual_sid)
            messages = app._session_get(actual_sid)

            async def stream(prompt):
                entered.set()
                yield {"data": "started"}
                await release.wait()
                messages.append({"text": prompt})

            return SimpleNamespace(
                tools=[], discovered=[], skills_loaded=[], skills_missing=[],
                close=lambda: closed.append(actual_sid),
                gate=SimpleNamespace(drain=lambda: [], summary=lambda: {}),
                agent=SimpleNamespace(messages=messages, stream_async=stream))

        monkeypatch.setattr(app, "_Session", session)

        async def collect(prompt):
            return [event async for event in app.run(
                {"agent": "regulation_impact_agent", "prompt": prompt}, sid)]

        stream = app.run({"agent": "regulation_impact_agent", "prompt": "first"}, sid)
        assert (await anext(stream))["type"] == "text"
        rejected = await collect("overlap")
        assert rejected[0]["code"] == 409 and rejected[-1]["stopReason"] == "session_busy"
        assert opened == [sid] and app._session_get(sid) == [{"text": "prior"}]
        if finish == "close":
            await stream.aclose()
        else:
            task = asyncio.create_task(anext(stream))
            if finish == "cancel":
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                assert (await task)["type"] == "meta"
            await stream.aclose()
        assert closed == [sid] and sid not in app._active_sessions
        expected = [{"text": "prior"}] + ([{"text": "first"}] if finish == "complete" else [])
        assert app._session_get(sid) == expected
        release.set()
        await collect("next")
        assert app._session_get(sid) == expected + [{"text": "next"}]
        assert opened == [sid, sid] and closed == [sid, sid] and not app._active_sessions

    asyncio.run(exercise())


@pytest.mark.parametrize("finish", ["cancel", "close"])
def test_design_cancellation_joins_worker_before_same_session_retry(runtime_app, monkeypatch, finish):
    import threading
    from types import SimpleNamespace
    import design_loop
    app = _load_container_module("app")
    release, finished = threading.Event(), threading.Event()
    calls = []

    def work(product, state, checks, deps, *, emit, output_type):
        emit({"type": "stage", "step": "generate"})
        try:
            assert release.wait(5), "test failed to release synthetic in-flight model"
            deps["generate"]("system", "user", None)
        finally:
            finished.set()

    monkeypatch.setattr(design_loop, "run", work)
    monkeypatch.setitem(sys.modules, "design_deps", SimpleNamespace(make_deps=lambda model: {
        "generate": lambda *args: calls.append("new-model-call"),
        "llm_judge": lambda *args: calls.append("new-judge-call"),
        "usage": lambda: {"inputTokens": 0, "outputTokens": 0, "calls": 0},
    }))
    payload = {"agent": "design_flow_agent",
               "design": {"productSpec": {"id": "synthetic"}, "smModel": {"id": "synthetic"}}}
    sid = "d" * 64

    async def exercise():
        stream = app.run(payload, sid)
        assert (await anext(stream))["type"] == "stage"
        if finish == "close":
            closing = asyncio.create_task(stream.aclose())
        else:
            closing = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done() and not finished.is_set()
        rejected = [event async for event in app.run(payload, sid)]
        assert rejected[0]["code"] == 409
        release.set()
        if finish == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await closing
        assert finished.is_set() and not calls and sid not in app._active_sessions
        await stream.aclose()

    try:
        asyncio.run(exercise())
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["model", "agent", "cleanup"])
def test_session_constructor_closes_gateway_and_quarantines_failed_cleanup(runtime_app, monkeypatch, failure):
    from types import SimpleNamespace
    app = _load_container_module("app")
    stopped = []

    def stop(*args):
        stopped.append(True)
        if failure == "cleanup":
            raise RuntimeError("synthetic stop failure")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic constructor failure")

    monkeypatch.setattr(app, "BoundaryGateHook", lambda: None)
    monkeypatch.setattr(app, "build_system_prompt", lambda spec: ("safe", [], []))
    monkeypatch.setattr(app, "scan_rules", lambda text: [])
    monkeypatch.setattr(app, "build_model", fail if failure == "model" else lambda model: object())
    monkeypatch.setattr(app.mcp_gateway, "open_tools", lambda tools: (SimpleNamespace(stop=stop), [], []))
    monkeypatch.setitem(sys.modules, "strands", SimpleNamespace(Agent=fail))
    sid = "s" * 64
    with pytest.raises(RuntimeError):
        app._Session({"name": "synthetic", "allowedTools": ["tool"]}, "synthetic-model", sid)
    assert stopped == [True]
    assert (sid in app._quarantined_sessions) == (failure == "cleanup")
    if failure == "cleanup":
        async def rejected():
            return [event async for event in app.run({"agent": "regulation_impact_agent", "prompt": "safe"}, sid)]
        result = asyncio.run(rejected())
        assert result[0]["code"] == 503 and result[-1]["stopReason"] == "cleanup_failed"
        assert stopped == [True]


def test_cancelled_session_constructor_is_joined_and_closed_before_retry(runtime_app, monkeypatch):
    import threading
    from types import SimpleNamespace
    app, _ = runtime_app
    monkeypatch.setattr(app, "asyncio", asyncio)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    sid = "c" * 64

    def construct(*args):
        entered.set()
        assert release.wait(5), "test failed to release synthetic constructor"
        return SimpleNamespace(close=closed.set)

    monkeypatch.setattr(app, "_Session", construct)
    payload = {"agent": "regulation_impact_agent", "prompt": "safe"}

    async def exercise():
        async def collect():
            return [event async for event in app.run(payload, sid)]
        active = asyncio.create_task(collect())
        assert await asyncio.to_thread(entered.wait, 5)
        active.cancel()
        await asyncio.sleep(0)
        assert not active.done()
        rejected = await collect()
        assert rejected[0]["code"] == 409
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert closed.is_set() and sid not in app._active_sessions

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_cleanup_quarantine_capacity_blocks_further_admission(runtime_app):
    app, _ = runtime_app
    for index in range(app.MAX_SESSIONS + 10):
        app._quarantine(f"failed-{index}-" + "s" * 40)
    assert len(app._quarantined_sessions) == app.MAX_SESSIONS
    assert app._quarantine_exhausted

    async def exercise():
        return [event async for event in app.run(
            {"agent": "regulation_impact_agent", "prompt": "safe"}, "new-session-" + "s" * 40)]

    result = asyncio.run(exercise())
    assert result[0]["code"] == 503
    assert not app._active_sessions


def test_cleanup_failure_quarantines_but_releases_active_claim(runtime_app, monkeypatch):
    from types import SimpleNamespace
    app, _ = runtime_app
    sid = "cleanup-failure-" + "s" * 40
    constructed = []

    def close():
        app._quarantine(sid)
        raise app._SessionCleanupFailed()

    async def stream(prompt):
        yield {"data": "completed"}

    def session(*args):
        constructed.append(True)
        return SimpleNamespace(tools=[], discovered=[], skills_loaded=[], skills_missing=[], close=close,
            gate=SimpleNamespace(drain=lambda: [], summary=lambda: {}),
            agent=SimpleNamespace(messages=[], stream_async=stream))

    monkeypatch.setattr(app, "_Session", session)
    monkeypatch.setattr(app.mcp_gateway, "missing_tool_names", lambda *args: [], raising=False)

    async def exercise():
        payload = {"agent": "regulation_impact_agent", "prompt": "safe"}
        result = [event async for event in app.run(payload, sid)]
        assert result[-1]["stopReason"] == "cleanup_failed"
        assert sid not in app._active_sessions and sid in app._quarantined_sessions
        retry = [event async for event in app.run(payload, sid)]
        assert retry[0]["code"] == 503 and constructed == [True]

    asyncio.run(exercise())
