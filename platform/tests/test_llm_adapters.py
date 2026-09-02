"""LLM 어댑터 테스트 (SPEC v2 §4) — 오프라인. HTTP/boto3 는 페이크로 대체하고 네트워크를 쓰지 않는다.

실행: cd platform && python3 -m pytest tests/test_llm_adapters.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

from engine import bedrock_token, llm  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _detach_engine_bedrock_attribute():
    """테스트 격리: tests/test_registry.py 는 `sys.modules["engine.bedrock"]` 스텁으로 임베딩 실패를 흉내내는데,
    `from engine import bedrock` 는 engine 패키지에 이미 붙은 속성을 우선 쓰므로 이 파일이 먼저 실행되면 스텁이 무시된다
    (실 Bedrock 호출로 이어질 수 있음). 모듈 종료 시 속성만 떼어내 원래의 지연 import 상태로 되돌린다."""
    yield
    import engine
    if hasattr(engine, "bedrock"):
        delattr(engine, "bedrock")


GEMMA = "google.gemma-4-31b"
BASE = "https://bedrock-mantle.us-west-2.api.aws/openai/v1"

SSE = (b"event: message\n"
       b'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n'
       b'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"\xec\x95\x88\xeb\x85\x95"}}]}\n\n'
       b'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"\xed\x95\x98\xec\x84\xb8\xec\x9a\x94"},"finish_reason":null}]}\n\n'
       b": keep-alive\n\n"
       b"data: not-json\n\n"
       b'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
       b'data: {"id":"c1","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":4,"total_tokens":16}}\n\n'
       b"data: [DONE]\n\n"
       b'data: {"after":"done must not be parsed"}\n')

COMPLETION = {"id": "c2", "object": "chat.completion", "model": GEMMA,
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "우대금리 조건을 충족합니다."},
                           "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 40, "completion_tokens": 9, "total_tokens": 49}}


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json", status: int = 200):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.status = status
        self.closed = False

    def read(self):
        return self._body

    def __iter__(self):
        return iter(io.BytesIO(self._body).readlines())

    def close(self):
        self.closed = True


class FakeOpener:
    """urllib.request.urlopen 대체 — 요청을 기록하고 스크립트된 응답을 돌려준다."""

    def __init__(self, responder):
        self.responder = responder
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return self.responder(req)


def _adapter(opener, key=("test-key", "secretsmanager"), model=GEMMA):
    return llm.GemmaAdapter(model_id=model, base_url=BASE, api_key_provider=lambda: key, opener=opener)


# ---------- SSE 파싱 ----------
def test_iter_sse_json_parses_data_lines_skips_junk_and_stops_at_done():
    objs = list(llm.iter_sse_json(SSE.splitlines()))
    assert len(objs) == 5 and objs[-1]["usage"]["total_tokens"] == 16
    assert all("after" not in o for o in objs)
    assert list(llm.iter_sse_json(["data: [DONE]", 'data: {"x":1}'])) == []
    assert list(llm.iter_sse_json([b'data: {"a":1}', "data: [1,2]", "", "id: 7"])) == [{"a": 1}]


def test_gemma_stream_yields_deltas_and_extracts_usage():
    resp = FakeResponse(SSE, "text/event-stream")
    st = llm.GemmaStream(resp, GEMMA, close=resp.close)
    assert "".join(st) == "안녕하세요"
    assert st.usage == {"inputTokens": 12, "outputTokens": 4, "totalTokens": 16}
    assert st.stop_reason == "stop" and st.model_id == GEMMA and resp.closed


def test_gemma_parse_completion_non_stream_and_content_parts():
    text, usage, finish = llm.GemmaAdapter.parse_completion(COMPLETION)
    assert text == "우대금리 조건을 충족합니다." and finish == "stop"
    assert usage == {"inputTokens": 40, "outputTokens": 9, "totalTokens": 49}
    parts = {"choices": [{"message": {"content": [{"type": "text", "text": "가"}, {"type": "text", "text": "나"}]}}]}
    assert llm.GemmaAdapter.parse_completion(parts)[0] == "가나"
    assert llm.GemmaAdapter.parse_completion({}) == ("", {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}, "")


# ---------- Gemma 어댑터 HTTP ----------
def test_gemma_generate_posts_openai_chat_body_with_bearer_key():
    op = FakeOpener(lambda req: FakeResponse(json.dumps(COMPLETION, ensure_ascii=False).encode()))
    ad = _adapter(op)
    assert ad.region == "us-west-2" and ad.route == "gemma" and ad.tier == "2" and ad.endpoint == "bedrock-mantle"
    text, usage = ad.generate("시스템", "질문: 우대금리?", max_tokens=50, temperature=0.1)
    assert text == "우대금리 조건을 충족합니다." and usage["inputTokens"] == 40 and usage["outputTokens"] == 9
    req = op.requests[0]
    assert req.full_url == BASE + "/chat/completions" and req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer test-key" and req.get_header("Content-type") == "application/json"
    body = json.loads(req.data.decode("utf-8"))
    assert body["model"] == GEMMA and body["stream"] is False and body["max_tokens"] == 50 and body["temperature"] == 0.1
    assert body["messages"] == [{"role": "system", "content": "시스템"}, {"role": "user", "content": "질문: 우대금리?"}]
    assert "stream_options" not in body and ad.auth_source == "secretsmanager"


def test_gemma_stream_requests_sse_with_usage_and_falls_back_on_json():
    op = FakeOpener(lambda req: FakeResponse(SSE, "text/event-stream; charset=utf-8"))
    st = _adapter(op).stream("s", "u", max_tokens=20)
    assert isinstance(st, llm.GemmaStream) and "".join(st) == "안녕하세요" and st.usage["totalTokens"] == 16
    body = json.loads(op.requests[0].data.decode())
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    # 서버가 스트리밍 대신 완성 JSON 을 준 경우 — 1청크 + non_stream 표기 (흉내 없음)
    op2 = FakeOpener(lambda req: FakeResponse(json.dumps(COMPLETION, ensure_ascii=False).encode(), "application/json"))
    st2 = _adapter(op2).stream("s", "u")
    assert isinstance(st2, llm.ListStream) and st2.non_stream is True
    assert "".join(st2) == "우대금리 조건을 충족합니다." and st2.usage["inputTokens"] == 40 and st2.stop_reason == "stop"


def test_gemma_health_checks_model_catalog():
    models = {"object": "list", "data": [{"id": GEMMA, "object": "model"}, {"id": "google.gemma-3-27b-it"}]}
    op = FakeOpener(lambda req: FakeResponse(json.dumps(models).encode()))
    h = _adapter(op, key=("bedrock-api-key-abc", "sigv4-short-term")).health()
    assert h["ok"] and h["found"] and h["model"] == GEMMA and GEMMA in h["models"] and h["status"] == 200
    assert h["authSource"] == "sigv4-short-term" and h["region"] == "us-west-2" and h["error"] == ""
    assert op.requests[0].full_url == BASE + "/models" and op.requests[0].get_method() == "GET"
    h2 = _adapter(FakeOpener(lambda req: FakeResponse(json.dumps(models).encode())), model="google.gemma-4-99b").health()
    assert h2["ok"] is False and h2["found"] is False and "google.gemma-4-99b" in h2["error"] and "2개" in h2["error"]

    # 실측 형태: {base}/models 는 404, 오리진 /v1/models 가 응답 → 폴백 경로로 성공하고 어느 경로였는지 기록
    def responder(req):
        if req.full_url == BASE + "/models":
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))
        assert req.full_url == "https://bedrock-mantle.us-west-2.api.aws/v1/models"
        return FakeResponse(json.dumps(models).encode())
    op3 = FakeOpener(responder)
    h3 = _adapter(op3).health()
    assert h3["ok"] and h3["modelsPath"] == "https://bedrock-mantle.us-west-2.api.aws/v1/models" and len(op3.requests) == 2
    assert _adapter(op3).models_urls() == [BASE + "/models", "https://bedrock-mantle.us-west-2.api.aws/v1/models"]


def test_gemma_http_error_is_surfaced_without_leaking_key():
    def boom(req):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b'{"error":{"message":"not authorized to CallWithBearerToken","type":"auth"}}'))
    ad = _adapter(FakeOpener(boom), key=("SECRET-KEY-VALUE", "secretsmanager"))
    with pytest.raises(llm.LLMHttpError) as ei:
        ad.generate("s", "u")
    assert ei.value.status == 403 and "not authorized to CallWithBearerToken" in str(ei.value)
    assert "SECRET-KEY-VALUE" not in str(ei.value)
    h = ad.health()
    assert h["ok"] is False and h["status"] == 403 and "HTTP 403" in h["error"] and "SECRET-KEY-VALUE" not in json.dumps(h)

    def down(req):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(llm.LLMError, match="연결 실패"):
        _adapter(FakeOpener(down)).generate("s", "u")


def test_gemma_payload_cap_is_enforced_before_sending():
    op = FakeOpener(lambda req: pytest.fail("전송되면 안 된다"))
    with pytest.raises(llm.LLMError, match="상한"):
        _adapter(op).generate("s", "x" * (llm.GEMMA_MAX_BODY_BYTES + 10))
    assert op.requests == []


# ---------- API 키 출처 ----------
class FakeSecrets:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def get_secret_value(self, SecretId):
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": "x"}}, "GetSecretValue")
        return {"SecretString": self.value}


def test_secret_api_key_parses_json_or_plain_and_returns_none_when_missing():
    llm._secret_cache.clear()
    assert llm.secret_api_key("t/json", "ap-northeast-2", client=FakeSecrets(json.dumps({"api_key": "k-json"}))) == "k-json"
    assert llm.secret_api_key("t/plain", "ap-northeast-2", client=FakeSecrets("k-plain")) == "k-plain"
    assert llm.secret_api_key("t/missing", "ap-northeast-2", client=FakeSecrets(error="ResourceNotFoundException")) is None
    assert llm.secret_api_key("t/denied", "ap-northeast-2", client=FakeSecrets(error="AccessDeniedException")) is None
    assert llm.secret_api_key("t/json", "ap-northeast-2", client=FakeSecrets(error="AccessDeniedException")) == "k-json"  # 캐시
    llm._secret_cache.clear()


def test_default_api_key_prefers_long_term_secret_then_short_term_token(monkeypatch):
    monkeypatch.setattr(llm, "secret_api_key", lambda *a, **k: "long-term-key")
    assert llm.default_api_key("us-west-2") == ("long-term-key", "secretsmanager")
    monkeypatch.setattr(llm, "secret_api_key", lambda *a, **k: None)
    monkeypatch.setattr(bedrock_token, "get_token", lambda region="us-west-2", host=None: f"bedrock-api-key-{region}")
    assert llm.default_api_key("us-west-2") == ("bedrock-api-key-us-west-2", "sigv4-short-term")


# ---------- Claude 어댑터 ----------
class FakeRuntime:
    def __init__(self, events=None, response=None):
        self.events, self.response, self.calls = events or [], response or {}, []

    def converse_stream(self, **kw):
        self.calls.append(("stream", kw))
        return {"stream": iter(self.events)}

    def converse(self, **kw):
        self.calls.append(("converse", kw))
        return self.response


def test_claude_adapter_extracts_usage_from_stream_metadata(monkeypatch):
    monkeypatch.delenv("GEN_TEMPERATURE", raising=False)
    events = [{"messageStart": {"role": "assistant"}},
              {"contentBlockDelta": {"delta": {"text": "가"}, "contentBlockIndex": 0}},
              {"contentBlockDelta": {"delta": {"text": "나"}, "contentBlockIndex": 0}},
              {"contentBlockStop": {"contentBlockIndex": 0}},
              {"messageStop": {"stopReason": "end_turn"}},
              {"metadata": {"usage": {"inputTokens": 123, "outputTokens": 45, "totalTokens": 168}, "metrics": {"latencyMs": 900}}}]
    rt = FakeRuntime(events=events)
    ad = llm.ClaudeAdapter(model_id="global.anthropic.claude-sonnet-5", region="ap-northeast-2", client=rt)
    assert ad.route == "claude" and ad.tier == "0/1" and ad.endpoint == "bedrock-runtime"
    st = ad.stream("시스템", "질문", max_tokens=800, temperature=0.2)
    assert isinstance(st, llm.ClaudeStream) and st.usage == {}
    assert "".join(st) == "가나"
    assert st.usage == {"inputTokens": 123, "outputTokens": 45, "totalTokens": 168} and st.stop_reason == "end_turn"
    kind, kw = rt.calls[0]
    assert kind == "stream" and kw["modelId"] == "global.anthropic.claude-sonnet-5"
    assert kw["system"] == [{"text": "시스템"}] and kw["messages"] == [{"role": "user", "content": [{"text": "질문"}]}]
    # Claude 5 는 temperature 를 거부한다 (실측) — 기본은 maxTokens 만
    assert kw["inferenceConfig"] == {"maxTokens": 800}
    monkeypatch.setenv("GEN_TEMPERATURE", "0.3")
    list(ad.stream("s", "u", max_tokens=9))
    assert rt.calls[-1][1]["inferenceConfig"] == {"maxTokens": 9, "temperature": 0.3}


def test_claude_adapter_generate_and_converse_with_tools(monkeypatch):
    monkeypatch.delenv("GEN_TEMPERATURE", raising=False)
    resp = {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 2},
            "output": {"message": {"role": "assistant", "content": [{"text": "A"}, {"text": "B"}]}}}
    rt = FakeRuntime(response=resp)
    ad = llm.ClaudeAdapter(model_id="m1", region="ap-northeast-2", client=rt)
    assert ad.generate("s", "u", max_tokens=5) == ("AB", {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12})
    tool_cfg = {"tools": [{"toolSpec": {"name": "search_internal_documents"}}]}
    msgs = [{"role": "user", "content": [{"text": "본문"}]}]
    r = ad.converse_with_tools("S", msgs, tool_cfg, model="m2", max_tokens=1800, temperature=0.1)
    assert r is resp
    kind, kw = rt.calls[1]
    assert kind == "converse" and kw["modelId"] == "m2" and kw["toolConfig"] is tool_cfg and kw["messages"] == msgs
    assert kw["system"] == [{"text": "S"}] and kw["inferenceConfig"] == {"maxTokens": 1800}
    ad.converse_with_tools("", msgs, None)
    assert "system" not in rt.calls[2][1] and "toolConfig" not in rt.calls[2][1] and rt.calls[2][1]["modelId"] == "m1"


def test_claude_adapter_defaults_from_env(monkeypatch):
    monkeypatch.delenv("GEN_MODEL", raising=False)
    assert llm.ClaudeAdapter().model_id == "global.anthropic.claude-sonnet-5"
    monkeypatch.setenv("GEN_MODEL", "global.anthropic.claude-opus-5")
    assert llm.ClaudeAdapter().model_id == "global.anthropic.claude-opus-5"
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    monkeypatch.delenv("GEMMA_BASE_URL", raising=False)
    g = llm.GemmaAdapter(api_key_provider=lambda: ("k", "x"), opener=lambda *a, **k: None)
    assert g.model_id == GEMMA and g.base_url == BASE and g.region == "us-west-2"


# ---------- idc_vllm 자리 ----------
def test_vllm_stub_raises_not_implemented_with_hybrid_wording():
    v = llm.VllmAdapter()
    for fn in (lambda: v.generate("s", "u"), lambda: v.stream("s", "u")):
        with pytest.raises(NotImplementedError) as ei:
            fn()
        assert "idc_vllm" in str(ei.value) and "미구성" in str(ei.value) and ("온" + "프렘") not in str(ei.value)
    assert v.route == "idc_vllm" and v.tier == "2" and v.health()["ok"] is False


def test_normalize_usage_variants():
    assert llm.normalize_usage(None) == {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
    assert llm.normalize_usage({"inputTokens": "3", "outputTokens": 2, "cacheReadInputTokens": 1}) == \
        {"inputTokens": 3, "outputTokens": 2, "cacheReadInputTokens": 1, "totalTokens": 5}
    assert llm.normalize_openai_usage({"prompt_tokens": 1, "completion_tokens": 2}) == \
        {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3}
