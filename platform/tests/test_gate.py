"""익명화 게이트 테스트 (SPEC v2 §3-2, §11, §12.1) — 오프라인. 어댑터는 페이크를 주입하고 AWS 를 호출하지 않는다.

실행: cd platform && python3 -m pytest tests/test_gate.py -q
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

from engine import bedrock, bedrock_token, gate, llm  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _detach_engine_bedrock_attribute():
    """테스트 격리: tests/test_registry.py 의 `sys.modules["engine.bedrock"]` 스텁이 동작하려면 engine 패키지에
    bedrock 속성이 미리 붙어 있지 않아야 한다 (`from engine import bedrock` 는 속성을 우선한다). 모듈 종료 시 떼어낸다."""
    yield
    import engine
    if hasattr(engine, "bedrock"):
        delattr(engine, "bedrock")


S2_SYSTEM = "당신은 아톰은행 상담 도우미입니다. 제공된 계산엔진 확정값만 사용해 설명하세요."
S2_MASKED = ("고객 세그먼트: 우대\n고객명: ⟨CUSTOMERNAME:bb1411a9⟩\n"
             "고객 식별: ⟨CUSTOMER_ID:66d619f6⟩ / 계좌 ⟨ACCOUNT_ID:b6c5b83f⟩\n상품: 아톰 안심전세대출 II\n"
             "[계산엔진 확정값 — 이 숫자만 사용할 것]\n- 적용금리: 3.90% (기본 4.5%)\n"
             "- 우대 판정: 급여이체 실적→충족 → -0.3%p; 카드 사용 실적→충족 → -0.2%p\n- 대출 가능 한도: 200,000,000원\n\n"
             "질문: 제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?")
# 금지 표현 (§12.13) — 자기 자신을 잡지 않게 조각으로 만든다
FORBIDDEN = ["온" + "프렘", "On-" + "Premises", "Two-" + "Plane", "In-" + "Region", "서울을 " + "벗어나지 않"]


# ---------- 페이크 어댑터 ----------
class FakeStream(llm.AdapterStream):
    def __init__(self, model_id, chunks, usage):
        super().__init__(model_id)
        self._chunks, self._usage = list(chunks), usage

    def __iter__(self):
        for c in self._chunks:
            yield c
        self.usage = self._usage
        self.stop_reason = "end_turn"


class FakeAdapter:
    def __init__(self, route, tier, model_id, endpoint, region):
        self.route, self.tier, self.model_id, self.endpoint, self.region = route, tier, model_id, endpoint, region
        self.calls: list = []

    def generate(self, system, user, max_tokens=1500, temperature=0.2):
        self.calls.append(("generate", system, user, max_tokens))
        return f"[{self.route}] 답", {"inputTokens": 11, "outputTokens": 7}

    def stream(self, system, user, max_tokens=1500, temperature=0.2):
        self.calls.append(("stream", system, user, max_tokens))
        return FakeStream(self.model_id, ["안녕", "하세요"], {"inputTokens": 21, "outputTokens": 9})

    def converse_with_tools(self, system, messages, tool_config, model=None, max_tokens=1800, temperature=0.1):
        self.calls.append(("tools", system, messages, tool_config, model, max_tokens))
        return {"stopReason": "end_turn", "usage": {"inputTokens": 5, "outputTokens": 3},
                "output": {"message": {"role": "assistant", "content": [{"text": "{}"}]}}}


@pytest.fixture
def fakes(monkeypatch):
    gate.reset_adapters()
    monkeypatch.delenv("LLM_ROUTE", raising=False)
    monkeypatch.delenv("GATE_REFUSE_TYPES", raising=False)
    c = FakeAdapter("claude", "0/1", "global.anthropic.claude-sonnet-5", "bedrock-runtime", "ap-northeast-2")
    g = FakeAdapter("gemma", "2", "google.gemma-4-31b", "bedrock-mantle", "us-west-2")
    gate.set_adapter("claude", c)
    gate.set_adapter("gemma", g)
    yield {"claude": c, "gemma": g}
    gate.reset_adapters()


# ---------- 차단 ----------
@pytest.mark.parametrize("payload,kind", [
    ("고객 CUST-0042 상담 요청", "CUSTOMER_TOKEN"),
    ("계좌 ACCT-0007 잔액", "ACCOUNT_TOKEN"),
    ("주민번호 900101-1234567 확인", "KR_RRN"),
    ("연락처 010-1234-5678 로 회신", "PHONE"),
    ("계좌번호 110-1234-567890 송금", "KR_BANK_ACCOUNT"),
    ("카드 4111 1111 1111 1111 결제", "CARD"),
])
def test_gate_refuses_identifiers_and_payload_never_reaches_model(fakes, payload, kind):
    with pytest.raises(gate.GateRefused) as ei:
        gate.stream(S2_SYSTEM, payload, purpose="s2")
    assert kind in ei.value.types and ei.value.count >= 1 and ei.value.purpose == "s2"
    assert "모델에 전달되지 않았습니다" in str(ei.value)
    with pytest.raises(gate.GateRefused):
        gate.generate(S2_SYSTEM, payload, purpose="s1.decompose")
    with pytest.raises(gate.GateRefused):  # 시스템 프롬프트 쪽에 있어도 같다
        gate.generate(payload, "질문", purpose="x")
    assert fakes["claude"].calls == [] and fakes["gemma"].calls == []


def test_email_is_counted_but_not_a_refusal_type_by_default(fakes):
    text, usage, info = gate.generate("s", "보도자료 문의 press@example.com", purpose="report.reader")
    assert info["boundary"]["piiRules"]["count"] == 1 and info["boundary"]["piiRules"]["refuseTypes"] == []
    assert info["boundary"]["piiRules"]["hits"] == [{"type": "EMAIL", "detector": "rules", "count": 1}]
    assert len(fakes["claude"].calls) == 1


def test_refuse_types_env_override(fakes, monkeypatch):
    monkeypatch.setenv("GATE_REFUSE_TYPES", "EMAIL")
    with pytest.raises(gate.GateRefused) as ei:
        gate.generate("s", "문의 a@b.co", purpose="x")
    assert ei.value.types == ["EMAIL"]
    text, _, info = gate.generate("s", "고객 CUST-0001", purpose="x")   # 기본 유형은 override 로 빠진다 (설정 검증용)
    assert info["boundary"]["piiRules"]["byType"] == {"CUSTOMER_TOKEN": 1}


# ---------- 계측 ----------
def test_masked_s2_payload_passes_and_boundary_is_measured(fakes):
    st = gate.stream(S2_SYSTEM, S2_MASKED, max_tokens=800, purpose="s2", trace_id="t1")
    assert "".join(st) == "안녕하세요"
    assert st.usage == {"inputTokens": 21, "outputTokens": 9, "totalTokens": 30}
    assert st.tokens_in == 21 and st.tokens_out == 9 and st.stop_reason == "end_turn"
    assert st.model_id == "global.anthropic.claude-sonnet-5" and st.route == "claude" and st.tier == "0/1"
    b = st.boundary
    full = S2_SYSTEM + "\n" + S2_MASKED
    assert b["chars"] == len(full) and b["estTokens"] == len(full) // 3
    assert b["fieldsPassed"] == ["고객 세그먼트", "고객명", "고객 식별", "상품", "적용금리", "우대 판정", "대출 가능 한도", "질문"]
    assert b["piiRules"] == {"count": 0, "hits": [], "byType": {}, "refuseTypes": []}
    info = st.info()
    assert info["modelId"] == st.model_id and info["boundary"] is b and info["usage"] == st.usage
    assert fakes["claude"].calls[0][:3] == ("stream", S2_SYSTEM, S2_MASKED)


def test_parse_fields_filters_headers_urls_numbers_and_caps_at_20():
    user = "\n".join([f"- 필드{i}: 값{i}" for i in range(30)])
    assert gate.parse_fields(user) == [f"필드{i}" for i in range(20)]
    user2 = ('[출처 URL] https://example.com/a\n그래프 순회 결과:\n12:30 시각\n "audience": "부서장",\n'
             ' "externalSummary": {\n질문: 무엇?\n질문: 중복\n' + "x" * 60 + ": 너무 긴 라벨")
    assert gate.parse_fields(user2) == ["audience", "externalSummary", "질문"]
    assert gate.parse_fields("") == []


def test_generate_returns_text_usage_info(fakes):
    text, usage, info = gate.generate("시스템", "질문: 전세대출 담보 인정 기준은?", max_tokens=300, purpose="s1.decompose")
    assert text == "[claude] 답" and usage == {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18}
    assert info["route"] == "claude" and info["tier"] == "0/1" and info["endpoint"] == "bedrock-runtime"
    assert info["boundary"]["fieldsPassed"] == ["질문"] and info["purpose"] == "s1.decompose"


# ---------- 경로 선택 ----------
def test_route_selection_env_override_and_aliases(fakes, monkeypatch):
    assert gate.current_route() == "claude"
    monkeypatch.setenv("LLM_ROUTE", "gemma")
    st = gate.stream("s", "u", purpose="s2")
    assert st.route == "gemma" and st.tier == "2" and st.model_id == "google.gemma-4-31b" and st.endpoint == "bedrock-mantle"
    list(st)
    assert fakes["gemma"].calls and not fakes["claude"].calls
    text, usage, info = gate.generate("s", "u", route="claude", purpose="x")   # 호출별 override
    assert info["route"] == "claude" and text == "[claude] 답"
    for alias in ("onprem-vllm", "idc_vllm", "hybrid_vllm"):
        assert gate.current_route(alias) == "idc_vllm"
    with pytest.raises(ValueError):
        gate.current_route("gpt")


def test_idc_vllm_stub_is_designable_but_unconfigured(fakes):
    with pytest.raises(NotImplementedError) as ei:
        gate.stream("s", "u", route="onprem-vllm", purpose="x")
    msg = str(ei.value)
    assert "idc_vllm" in msg and "미구성" in msg
    for bad in FORBIDDEN:
        assert bad not in msg
    ri = gate.route_info("idc_vllm")
    assert ri["route"] == "idc_vllm" and ri["badge"]["implemented"] is False and ri["badge"]["substituted"] is True


# ---------- 로그: 메트릭·traceId 만 (§12.5) ----------
def test_crossing_log_has_metrics_and_no_payload_text(fakes, capsys):
    marker = "MARKER_원문_비밀_9f3a"
    gate.generate("시스템 " + marker, "- 적용금리: 3.9%\n" + marker, purpose="s2", trace_id="trace-77")
    out = capsys.readouterr().out
    assert marker not in out
    rec = next(json.loads(l) for l in out.splitlines() if '"gate.crossing"' in l)
    assert rec["traceId"] == "trace-77" and rec["purpose"] == "s2" and rec["route"] == "claude"
    assert rec["modelId"] == "global.anthropic.claude-sonnet-5" and rec["fields"] == 1 and rec["piiCount"] == 0
    assert rec["chars"] > 0 and rec["estTokens"] == rec["chars"] // 3
    assert out.count('"gate.crossing"') == 1


def test_refused_log_has_types_not_values(fakes, capsys):
    with pytest.raises(gate.GateRefused):
        gate.generate("s", "고객 CUST-0042", purpose="s2", trace_id="t9")
    out = capsys.readouterr().out
    rec = next(json.loads(l) for l in out.splitlines() if '"gate.refused"' in l)
    assert rec["piiTypes"] == ["CUSTOMER_TOKEN"] and rec["traceId"] == "t9" and "0042" not in out


# ---------- 도구 루프 (Reader) ----------
def test_tool_client_measures_scans_and_dispatches_to_claude(fakes):
    tc = gate.ToolClient(purpose="report.reader", trace_id="tr")
    msgs = [{"role": "user", "content": [{"text": "[출처 URL] https://x/y\n본문: 보증 비율 90% 검토"}]}]
    r = tc.converse(modelId="global.anthropic.claude-sonnet-5", system=[{"text": "S"}], messages=msgs,
                    toolConfig={"tools": [{"toolSpec": {"name": "search_internal_documents"}}]},
                    inferenceConfig={"maxTokens": 1800, "temperature": 0.1})
    assert r["stopReason"] == "end_turn"
    kind, system, messages, tool_config, model, max_tokens = fakes["claude"].calls[-1]
    assert kind == "tools" and system == "S" and messages is msgs and tool_config["tools"] and max_tokens == 1800
    assert len(tc.crossings) == 1 and tc.crossings[0]["fieldsPassed"] == ["본문"]
    s = tc.summary()
    assert s["crossings"] == 1 and s["route"] == "claude" and s["tier"] == "0/1" and s["routeForced"] is False
    assert s["chars"] == tc.crossings[0]["chars"] and s["piiCount"] == 0
    # toolResult 텍스트도 계측·스캔 대상 — 식별자가 있으면 모델에 가지 않는다
    msgs2 = msgs + [{"role": "assistant", "content": [{"toolUse": {"toolUseId": "1", "name": "x", "input": {"q": "a"}}}]},
                    {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": "고객 CUST-0001"}]}}]}]
    with pytest.raises(gate.GateRefused):
        tc.converse(modelId="m", system=[{"text": "S"}], messages=msgs2, toolConfig=None)
    assert len(fakes["claude"].calls) == 1 and len(tc.crossings) == 1


def test_tool_client_forces_claude_when_route_is_gemma(fakes, monkeypatch):
    monkeypatch.setenv("LLM_ROUTE", "gemma")
    tc = gate.ToolClient(purpose="report.reader")
    tc.converse(modelId="m", system=[{"text": "S"}], messages=[{"role": "user", "content": [{"text": "hi"}]}])
    assert tc.route_forced is True and fakes["claude"].calls and not fakes["gemma"].calls
    assert tc.summary()["routeForced"] is True


def test_reader_bedrock_client_is_the_gate_tool_client(fakes):
    from report import reader_handler
    rt = reader_handler._bedrock_client()
    assert isinstance(rt, gate.ToolClient) and rt.purpose == "report.reader"
    assert reader_handler._model_id() == "global.anthropic.claude-sonnet-5"


# ---------- engine.bedrock 호환 표면 ----------
def test_bedrock_wrapper_keeps_public_api_and_routes_through_gate(fakes):
    text, usage = bedrock.generate("s", "질문: x", max_tokens=300)
    assert (text, usage) == ("[claude] 답", {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18})
    st = bedrock.Stream("s", "u", max_tokens=5)
    assert isinstance(st, gate.Stream) and "".join(st) == "안녕하세요"
    assert st.usage["inputTokens"] == 21 and st.tokens_in == 21 and st.model_id and st.route == "claude" and st.boundary["chars"] == 3
    assert "".join(bedrock.generate_stream("s", "u")) == "안녕하세요"
    assert isinstance(bedrock.GEN_MODEL, str) and bedrock.GateRefused is gate.GateRefused
    with pytest.raises(gate.GateRefused):
        bedrock.Stream("s", "고객 CUST-0042")
    assert [c[3] for c in fakes["claude"].calls] == [300, 5, 1500]


def test_embed_and_rerank_are_measured_and_refuse_identifiers(fakes, monkeypatch, capsys):
    class FakeRt:
        def __init__(self):
            self.calls = []

        def invoke_model(self, modelId, body):
            self.calls.append((modelId, json.loads(body)))
            if "embed" in modelId:
                return {"body": io.BytesIO(json.dumps({"embedding": [0.1] * 1024}).encode())}
            return {"body": io.BytesIO(json.dumps({"results": [{"index": 1, "relevance_score": 0.9},
                                                               {"index": 0, "relevance_score": 0.4}]}).encode())}
    rt = FakeRt()
    monkeypatch.setattr(gate, "_rt", lambda region: rt)
    vecs = bedrock.embed(["규정 청크 A", "규정 청크 B"])
    assert len(vecs) == 2 and len(vecs[0]) == 1024 and rt.calls[0][0] == gate.EMBED_MODEL
    assert bedrock.rerank("질문", ["d0", "d1"], top_n=2) == [(1, 0.9), (0, 0.4)]
    out = capsys.readouterr().out
    xs = [json.loads(l) for l in out.splitlines() if '"gate.crossing"' in l]
    assert [x["purpose"] for x in xs][-2:] == ["embed", "rerank"] or {x["route"] for x in xs} >= {"embed", "rerank"}
    n = len(rt.calls)
    with pytest.raises(gate.GateRefused):
        bedrock.embed(["고객 CUST-0001 거래내역"])          # 개인데이터는 벡터화하지 않는다 (§12.3)
    with pytest.raises(gate.GateRefused):
        bedrock.rerank("q", ["주민번호 900101-1234567"])
    assert len(rt.calls) == n


# ---------- 표기 (§8-3 · §11) ----------
def test_route_info_and_badges_are_truthful(fakes):
    g = gate.route_info("gemma")
    assert g["badge"]["prod"] == "IDC GPU + vLLM (EKS Hybrid Nodes)"
    assert g["badge"]["demo"] == "Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체"
    assert g["tier"] == "2" and g["modelId"] == "google.gemma-4-31b" and g["inferenceRouting"] == "us-west-2 direct"
    assert g["storage"] == "ap-northeast-2" and g["badge"]["substituted"] is True
    c = gate.route_info("claude")
    assert c["tier"] == "0/1" and c["endpoint"] == "bedrock-runtime" and c["region"] == "ap-northeast-2"
    assert c["inferenceRouting"] == "global" and c["inferenceRoutingLabel"] == "global (전 세계 상용 리전)"
    assert c["storage"] == "ap-northeast-2" and c["storageLabel"] == "서울 리전"
    assert c["badge"]["region"] == "저장: 서울 리전 / 추론: global 라우팅" and c["badge"]["substituted"] is False
    gi = gate.gate_info()
    assert gi["prod"] == "가명처리 · 토큰화 · 재식별"
    assert gi["demo"] == "합성데이터 가명 생성 + 규칙 기반 토큰화 (ML 가명처리·재식별 볼트 미구현)"
    assert set(gi["refuseTypes"]) == set(gate.DEFAULT_REFUSE_TYPES)
    for r in ("claude", "gemma", "idc_vllm"):
        blob = json.dumps(gate.badges(r), ensure_ascii=False)
        for bad in FORBIDDEN:
            assert bad not in blob, (r, bad)
    assert gate.health("claude")["route"] == "claude"


# ---------- 단기 Bedrock API 키 (bedrock_token) ----------
def test_bedrock_token_assembly_from_fake_presigned_url():
    url = ("https://bedrock.us-west-2.amazonaws.com/?Action=CallWithBearerToken&X-Amz-Algorithm=AWS4-HMAC-SHA256"
           "&X-Amz-Credential=AKIAEXAMPLE%2F20260902%2Fus-west-2%2Fbedrock%2Faws4_request&X-Amz-Date=20260902T000000Z"
           "&X-Amz-Expires=43200&X-Amz-SignedHeaders=host&X-Amz-Signature=abc123")
    tok = bedrock_token.assemble_token(url)
    assert tok.startswith("bedrock-api-key-") and re.fullmatch(r"bedrock-api-key-[A-Za-z0-9+/=]+", tok)
    decoded = bedrock_token.decode_token(tok)
    assert decoded == url[len("https://"):] + "&Version=1"
    assert not decoded.startswith("https://") and decoded.endswith("&Version=1")
    with pytest.raises(ValueError):
        bedrock_token.decode_token("nope")


def test_bedrock_token_presign_is_sigv4_query_auth_offline(monkeypatch):
    from botocore.credentials import Credentials
    monkeypatch.delenv("BEDROCK_TOKEN_HOST", raising=False)
    creds = Credentials("AKIAEXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    url = bedrock_token.presign(creds, "us-west-2")
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    # 실측: 글로벌 호스트 + POST 서명만 mantle 이 받아준다 (리전은 credential scope 에만)
    assert parts.scheme == "https" and parts.netloc == "bedrock.amazonaws.com" and parts.path == "/"
    assert bedrock_token.SIGN_METHOD == "POST"
    assert q["Action"] == ["CallWithBearerToken"] and q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert q["X-Amz-Expires"] == ["43200"] and "/us-west-2/bedrock/aws4_request" in q["X-Amz-Credential"][0]
    assert q["X-Amz-SignedHeaders"] == ["host"] and re.fullmatch(r"[0-9a-f]{64}", q["X-Amz-Signature"][0])
    assert bedrock_token.decode_token(bedrock_token.assemble_token(url)).startswith("bedrock.amazonaws.com/?")
    # 서명은 결정적이다 (같은 자격·같은 시각이면 같은 서명) — GET 으로 서명하면 값이 달라진다
    from botocore.auth import SigV4QueryAuth
    from botocore.awsrequest import AWSRequest
    req = AWSRequest(method="GET", url="https://bedrock.amazonaws.com/", params={"Action": "CallWithBearerToken"},
                     headers={"host": "bedrock.amazonaws.com"})
    SigV4QueryAuth(creds, "bedrock", "us-west-2", expires=43200).add_auth(req)
    assert parse_qs(urlsplit(req.url).query)["X-Amz-Signature"] != q["X-Amz-Signature"]
    # 호스트 override 가능
    monkeypatch.setenv("BEDROCK_TOKEN_HOST", "bedrock.us-west-2.amazonaws.com")
    assert urlsplit(bedrock_token.presign(creds, "us-west-2")).netloc == "bedrock.us-west-2.amazonaws.com"


def test_bedrock_token_is_cached_until_reset(monkeypatch):
    bedrock_token.reset_cache()
    calls = []

    def fake_mint(region, host=None):
        calls.append(region)
        return {"token": f"bedrock-api-key-{len(calls)}", "expiresAt": 10 ** 12, "source": "sigv4-short-term",
                "region": region, "host": host}
    monkeypatch.setattr(bedrock_token, "mint", fake_mint)
    assert bedrock_token.get_token("us-west-2") == "bedrock-api-key-1"
    assert bedrock_token.get_token("us-west-2") == "bedrock-api-key-1" and len(calls) == 1
    bedrock_token.reset_cache()
    assert bedrock_token.get_token("us-west-2") == "bedrock-api-key-2"
    bedrock_token.reset_cache()


# ---------- §12.1 가드: 게이트가 유일한 경로다 ----------
_EXCLUDE_DIRS = {"api-dist", "node_modules", "cdk.out", "__pycache__", ".venv", "venv", "web", "tests"}
_ALLOWED_FILES = {"engine/llm.py", "engine/gate.py", "engine/bedrock.py", "api/common/pii.py"}
_CLIENT_RE = re.compile(r"""client\(\s*['"]bedrock-runtime['"]""")
_CALL_RE = re.compile(r"\.(converse|converse_stream|invoke_model|invoke_model_with_response_stream)\(")


def _py_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        yield rel.as_posix(), p.read_text(encoding="utf-8", errors="replace")


def test_gate_is_the_only_path_to_models_repo_wide():
    violations = []
    seen = set()
    for rel, src in _py_files():
        seen.add(rel)
        if rel in _ALLOWED_FILES:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if _CLIENT_RE.search(line):
                if rel == "api/handlers/s2.py":
                    continue  # apply_guardrail 전용 클라이언트 — 모델 호출(converse/invoke_model)이 없음을 아래에서 검증
                violations.append(f"{rel}:{i} bedrock-runtime 클라이언트 생성")
            if _CALL_RE.search(line):
                if rel == "report/reader_handler.py" and ".converse(" in line and "rt." in line:
                    continue  # 게이트 ToolClient(.converse 호환 표면) 호출 — 아래에서 별도 검증
                violations.append(f"{rel}:{i} 모델 직접 호출: {line.strip()[:80]}")
    assert {"api/handlers/s2.py", "report/reader_handler.py", "report/writer_handler.py", "engine/graphrag.py"} <= seen
    assert violations == [], "\n".join(violations)
    # s2.py 는 apply_guardrail 만 쓴다 (모델 호출 없음) — 클라이언트 생성이 있어도 converse/invoke_model 은 없어야 한다
    s2 = (ROOT / "api" / "handlers" / "s2.py").read_text(encoding="utf-8")
    assert not _CALL_RE.search(s2) and "apply_guardrail" in s2
    # reader 의 .converse 는 게이트 ToolClient 에 대한 호출이어야 한다
    reader_src = (ROOT / "report" / "reader_handler.py").read_text(encoding="utf-8")
    assert not _CLIENT_RE.search(reader_src) and "gate.ToolClient" in reader_src
    from report import reader_handler
    assert isinstance(reader_handler._bedrock_client(), gate.ToolClient)


def test_owned_files_use_single_boundary_terminology():
    owned = ["engine/llm.py", "engine/gate.py", "engine/bedrock.py", "engine/bedrock_token.py",
             "tests/test_gate.py", "tests/test_llm_adapters.py"]
    for rel in owned:
        src = (ROOT / rel).read_text(encoding="utf-8")
        for bad in FORBIDDEN:
            assert bad not in src, (rel, bad)
