"""S2 v2 (SPEC v2 §2 S2 · §6 · §11 · §12.4) — 오프라인. AWS 호출 없음: 게이트·가드레일·플레인은 페이크/로컬 라우터.

실행: cd platform && python3 -m pytest tests/test_s2_v2.py -q
검증 포인트
  - 플레인 /s2/prepare: txnSample(결정론·12~20건·월 경계) + metricByEngine(전월실적·당월실적) — 기존 키 유지
  - Semantic 토글: ON 은 정의+계산엔진 값, OFF 는 거래내역 원본만 (정의 없음)
  - semantic_check 파서: 모델이 말한 수치만 파싱 — 만들어내지 않는다 ('조용히 틀림' 탐지)
  - 핸들러: 'route' 스테이지가 맨 앞, §11 배지 문구, s2.done 의 boundary/modelId/route/tier, 트레이스 필드
  - GateRefused: 페이로드가 모델에 가지 않고 s2.done {blocked, gateRefused}
  - 금지 표현(§12.13)이 소유 파일에 없다
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

from common.ctx import Ctx  # noqa: E402
from engine import gate  # noqa: E402
from handlers import s2  # noqa: E402
from onprem import personal_store, service  # noqa: E402

DEMO = "demo@atomai.click"
REF = date(2026, 9, 2)
Q = "제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?"
# 금지 표현 (§12.13) — 자기 자신을 잡지 않게 조각으로 만든다
FORBIDDEN = ["온" + "프렘", "On-" + "Premises", "Two-" + "Plane", "In-" + "Region", "서울을 " + "벗어나지 않"]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("PLANE_TOKEN", raising=False)
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    monkeypatch.delenv("LLM_ROUTE", raising=False)
    personal_store._MAPPING_CACHE.clear()


# ---------------------------------------------------------------------------
# 플레인: txnSample · metricByEngine
# ---------------------------------------------------------------------------
def _sum(rows, status, lo, hi):
    return sum(r["amountKrw"] for r in rows if r["status"] == status and lo <= date.fromisoformat(r["date"]) <= hi)


def test_txn_sample_is_deterministic_and_within_two_months():
    a = personal_store.txn_sample("CUST-0042", REF, prev_target_krw=450_000)
    b = personal_store.txn_sample("CUST-0042", REF, prev_target_krw=450_000)
    assert a == b
    assert 12 <= len(a) <= 20
    assert personal_store.txn_sample("CUST-0777", REF, prev_target_krw=120_000) != a
    assert personal_store.txn_sample("CUST-0042", date(2026, 10, 3), prev_target_krw=450_000) != a  # 다른 기준월 → 다른 행
    for r in a:
        assert set(r) == {"date", "merchant", "amountKrw", "status"}
        assert r["status"] in (personal_store.TXN_APPROVED, personal_store.TXN_CANCELLED)
        assert r["amountKrw"] > 0 and r["amountKrw"] % 100 == 0
        assert r["merchant"] in personal_store.MERCHANTS
        d = date.fromisoformat(r["date"])
        assert date(2026, 8, 1) <= d <= REF                      # 직전월 1일 ~ 기준일
    assert a == sorted(a, key=lambda r: (r["date"], r["merchant"], r["amountKrw"]))
    assert any(r["status"] == personal_store.TXN_CANCELLED for r in a), "취소 건이 있어야 '취소분 제외' 정의가 의미를 가진다"
    # 직전월 승인 합계 = 프로필 cardMonthlyKrw (계산엔진 카드 우대 판정 입력과 모순 없음)
    assert _sum(a, personal_store.TXN_APPROVED, date(2026, 8, 1), date(2026, 8, 31)) == 450_000


def test_monthly_metrics_month_logic_and_year_boundary():
    rows = [
        {"date": "2025-12-01", "merchant": "x", "amountKrw": 100_000, "status": "APPROVED"},
        {"date": "2025-12-31", "merchant": "x", "amountKrw": 50_000, "status": "APPROVED"},
        {"date": "2025-12-15", "merchant": "x", "amountKrw": 70_000, "status": "CANCELLED"},   # 취소 → 전월실적 제외
        {"date": "2025-11-30", "merchant": "x", "amountKrw": 999_999, "status": "APPROVED"},   # 전전월 → 제외
        {"date": "2026-01-01", "merchant": "x", "amountKrw": 30_000, "status": "APPROVED"},
        {"date": "2026-01-15", "merchant": "x", "amountKrw": 20_000, "status": "APPROVED"},
        {"date": "2026-01-16", "merchant": "x", "amountKrw": 5_000, "status": "APPROVED"},     # 기준일 이후 → 제외
    ]
    m = service.monthly_metrics(rows, date(2026, 1, 15))
    assert m == {"전월실적": 150_000, "당월실적": 50_000}
    d = service.monthly_metrics(rows, date(2026, 1, 15), diagnostics=True)
    assert d["전월_취소포함"] == 220_000 and d["2개월_승인합계"] == 200_000 and d["당월_취소포함"] == 50_000
    assert personal_store.month_bounds(date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31), date(2026, 1, 1))
    assert personal_store.month_bounds(date(2026, 3, 1)) == (date(2026, 2, 1), date(2026, 2, 28), date(2026, 3, 1))
    assert service.monthly_metrics([], REF) == {"전월실적": 0, "당월실적": 0}


def _prepare(**kw):
    body = {"email": DEMO, "query": Q, "traceId": "t-v2", "refDate": REF.isoformat()}
    body.update(kw)
    code, out = service.handle("/s2/prepare", body)
    assert code == 200, out
    return out


def test_prepare_adds_txn_sample_and_engine_metrics_keeping_existing_keys():
    out = _prepare()
    assert {"rawValues", "rate", "limit", "maskedPayload", "maskedFields", "allowedNumbers", "dataSource"} <= set(out)
    assert {"txnSample", "metricByEngine", "metricDefinitions", "metricDiagnostics", "metricPeriod", "semanticLayer"} <= set(out)
    assert out["semanticLayer"] is True
    assert out["rate"]["value"] == "3.90" and out["limit"]["value"] == "200000000"     # 기존 계산 불변
    assert set(out["metricByEngine"]) == {"전월실적", "당월실적"}
    txns = out["txnSample"]
    assert out["metricByEngine"]["전월실적"] == _sum(txns, "APPROVED", date(2026, 8, 1), date(2026, 8, 31)) == 450_000
    assert out["metricByEngine"]["당월실적"] == _sum(txns, "APPROVED", date(2026, 9, 1), REF)
    assert out["metricByEngine"]["당월실적"] != out["metricByEngine"]["전월실적"]
    assert out["metricPeriod"] == {"refDate": "2026-09-02", "prevMonth": "2026-08", "prevFrom": "2026-08-01",
                                   "prevTo": "2026-08-31", "currentMonth": "2026-09"}
    assert out["metricDefinitions"]["전월실적"] == "직전월 1일~말일 승인 합계 (취소분 제외)"
    assert "450,000" in out["allowedNumbers"]
    # 같은 입력 → 같은 출력 (결정론)
    assert _prepare(traceId="t-v2b")["txnSample"] == txns


def test_prepare_semantic_on_prompt_has_definition_and_engine_value_not_raw_rows():
    mp = _prepare()["maskedPayload"]
    assert "- 전월실적: 450,000원 (Semantic Layer 정의: 직전월 1일~말일 승인 합계 (취소분 제외))" in mp
    assert "[카드 거래내역 원본" not in mp and "정의는 제공되지 않습니다" not in mp
    for pii in ("김데모", "CUST-0042", "ACCT-0007"):
        assert pii not in mp
    # 게이트 필드 계측 — 전월실적이 전달 필드로 잡힌다
    assert "전월실적" in gate.parse_fields(mp)


def test_prepare_semantic_off_prompt_has_raw_rows_and_no_definition():
    out = _prepare(semanticLayer=False)
    mp = out["maskedPayload"]
    assert out["semanticLayer"] is False
    assert "[카드 거래내역 원본 — 직전월·당월 (기준일 2026-09-02)]" in mp
    assert "정의는 제공되지 않습니다" in mp and "'전월실적'을 직접 계산" in mp
    assert "Semantic Layer 정의" not in mp and "취소분 제외" not in mp and "- 전월실적:" not in mp
    rows = [l for l in mp.splitlines() if re.match(r"^\d{4}-\d{2}-\d{2} · ", l)]
    assert len(rows) == len(out["txnSample"])
    for l in rows:
        assert l.endswith(" · 승인") or l.endswith(" · 취소")
    # 거래 행은 콜론이 없어 게이트 '전달 필드'로 세어지지 않는다 — 필드 목록은 ON 과 같은 라벨만
    assert gate.parse_fields(mp) == ["고객 세그먼트", "고객명", "고객 식별", "상품", "적용금리", "우대 판정", "대출 가능 한도", "질문"]
    # 규칙 기반 PII 스캔(게이트 차단 기준)에 걸리는 식별자가 없다 — 날짜·금액이 계좌/카드 패턴으로 오인되지 않는다
    assert gate.measure("", mp)["piiRules"]["refuseTypes"] == []
    # OFF 에서는 원장 숫자(거래액·당월 합계)가 '만들어낸 수치'가 아니다 → 수치 검증기는 통과시키고 semantic_check 만 잡는다
    allowed = set(out["allowedNumbers"])
    assert f"{out['metricByEngine']['당월실적']:,}" in allowed
    assert all(f"{t['amountKrw']:,}" in allowed for t in out["txnSample"])
    # ON 에서는 당월 합계가 프롬프트에 없으므로 허용 목록에도 없다
    assert f"{out['metricByEngine']['당월실적']:,}" not in set(_prepare()["allowedNumbers"])


def test_prepare_truthy_parsing_and_default_ref_date():
    for v in ("false", "0", "off", False):
        assert _prepare(semanticLayer=v)["semanticLayer"] is False
    for v in ("true", "1", True, None):
        assert _prepare(semanticLayer=v)["semanticLayer"] is True
    body = {"email": DEMO, "query": Q, "traceId": "t-today"}         # refDate 없음 → 오늘
    code, out = service.handle("/s2/prepare", body)
    assert code == 200 and out["metricPeriod"]["refDate"] == date.today().isoformat()
    code, out = service.handle("/s2/prepare", {**body, "refDate": "garbage"})
    assert code == 200 and out["metricPeriod"]["refDate"] == date.today().isoformat()


# ---------------------------------------------------------------------------
# semantic_check 파서 — 모델 수치는 파싱만
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("고객님의 전월실적은 450,000원으로 30만원 이상 조건을 충족합니다.", [450_000]),
    ("전월 실적 45만 원, 적용금리 3.90%입니다.", [450_000]),
    ("전월실적(8월 승인 합계)은 884,500원입니다. 대출 한도 200,000,000원.", [884_500, 200_000_000]),
    ("전월실적 기준 30만원 이상이면 -0.2%p 우대가 적용됩니다.", []),          # 기준값만 — 고객 수치 아님
    ("전월실적 1억 2,000만원", [120_000_000]),
    ("우대금리 조건을 충족합니다. 한도는 2억원입니다.", []),                     # 전월실적 언급 없음
    ("**전월실적**: 935,200원 (취소 포함)", [935_200]),
    ("450,000원의 전월실적으로 우대 조건 충족", [450_000]),
    ("2026년 8월 전월실적은 총 450,000원입니다", [450_000]),
    ("", []),
])
def test_stated_amounts_parses_only_what_model_said(text, expected):
    assert s2.stated_amounts(text) == expected


def test_semantic_check_detects_silent_mismatch_and_names_the_cause():
    diag = {"당월실적": 884_500, "전월_취소포함": 500_700, "2개월_승인합계": 1_334_500, "2개월_취소포함": 1_385_200}
    r = s2.semantic_check("고객님의 전월실적은 884,500원입니다. 적용금리 3.90%.", False, 450_000, diag)
    assert r["mismatch"] is True and r["modelValue"] == 884_500 and r["engineValue"] == 450_000
    assert r["semanticLayer"] is False and "당월 승인 합계" in r["note"] and "조용히 틀림" in r["note"]
    r = s2.semantic_check("전월실적 500,700원", False, 450_000, diag)
    assert r["mismatch"] and "취소분을 포함" in r["note"]
    r = s2.semantic_check("전월실적 1,334,500원", False, 450_000, diag)
    assert r["mismatch"] and "2개월" in r["note"]
    r = s2.semantic_check("전월실적 777,777원", True, 450_000, diag)
    assert r["mismatch"] and "+327,777원" in r["note"] and "§12.4" in r["note"]


def test_semantic_check_match_and_no_number_and_missing_engine():
    r = s2.semantic_check("전월실적 450,000원으로 조건 충족", True, 450_000, {})
    assert r == {**r, "mismatch": False, "modelValue": 450_000, "engineValue": 450_000}
    # 후보 여럿 중 계산엔진 값이 있으면 일치 (기준값 재진술과 섞여도 오탐하지 않는다)
    r = s2.semantic_check("전월실적(30만원 이상 기준) 450,000원 충족, 이번 달은 884,500원", False, 450_000, {"당월실적": 884_500})
    assert r["mismatch"] is False and r["modelValue"] == 450_000
    r = s2.semantic_check("우대금리 3.90%가 적용됩니다. 한도는 200,000,000원입니다.", False, 450_000, {})
    assert r["modelValue"] is None and r["mismatch"] is False and r["note"] == "모델이 수치를 제시하지 않음"
    r = s2.semantic_check("전월실적 450,000원", True, None, {})
    assert r["mismatch"] is False and r["modelValue"] is None and "metricByEngine" in r["note"]
    r = s2.semantic_check("전월실적 450,000원", True, "abc", {})
    assert r["mismatch"] is False and "형식 오류" in r["note"]


# ---------------------------------------------------------------------------
# 핸들러 — 페이크 게이트·가드레일, 로컬 플레인 라우터
# ---------------------------------------------------------------------------
class _Apigw:
    def __init__(self):
        self.sent = []

    def post_to_connection(self, ConnectionId, Data):
        self.sent.append(json.loads(Data))


class _FakeStream:
    def __init__(self, text, route="claude"):
        self._text = text
        self.route = route
        self.model_id = "global.anthropic.claude-sonnet-5" if route == "claude" else "google.gemma-4-31b"
        self.tier = "0/1" if route == "claude" else "2"
        self.boundary = {"chars": 0, "estTokens": 0, "fieldsPassed": [], "piiRules": {"count": 0, "hits": [], "byType": {}, "refuseTypes": []}}
        self.usage = {}
        self.non_stream = False

    def __iter__(self):
        for i in range(0, len(self._text), 7):
            yield self._text[i:i + 7]
        self.usage = {"inputTokens": 321, "outputTokens": 45, "totalTokens": 366}


@pytest.fixture
def harness(monkeypatch):
    """AWS 없는 S2: 가드레일 NONE, 플레인 local(VPC 내부 라우터 직접 호출), 게이트 route_info/stream 페이크, 트레이스 캡처."""
    calls = {"stream": [], "traces": [], "route_info": []}
    monkeypatch.setattr(s2, "apply_guardrail", lambda text, source, grounding="", query="": {
        "action": "NONE", "topics": [], "grounding": [], "pii": [], "words": [], "message": ""})
    monkeypatch.setattr(s2.plane, "mode", lambda: "local")
    monkeypatch.setattr(s2.plane, "label", lambda: "로컬 폴백 (개발용 — 분리 아님)")
    monkeypatch.setattr(s2.tracing, "record_trace", lambda rec: calls["traces"].append(dict(rec)))

    def fake_route_info(route=None):
        r = gate.current_route(route)
        calls["route_info"].append(r)
        if r == "gemma":
            return {"route": "gemma", "tier": "2", "modelId": "google.gemma-4-31b", "endpoint": "bedrock-mantle",
                    "region": "us-west-2", "storage": "ap-northeast-2", "storageLabel": "서울 리전",
                    "inferenceRouting": "us-west-2 direct", "inferenceRoutingLabel": "us-west-2 직접 호출 (교차 리전 추론 미지원)",
                    "badge": {"title": "PII 추론 경로 — Tier 2", "prod": "IDC GPU + vLLM (EKS Hybrid Nodes)",
                              "demo": "Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체",
                              "region": "저장: 서울 리전 / 추론: us-west-2 직접 호출", "substituted": True}}
        return {"route": "claude", "tier": "0/1", "modelId": "global.anthropic.claude-sonnet-5", "endpoint": "bedrock-runtime",
                "region": "ap-northeast-2", "storage": "ap-northeast-2", "storageLabel": "서울 리전",
                "inferenceRouting": "global", "inferenceRoutingLabel": "global (전 세계 상용 리전)",
                "badge": {"title": "LLM 생성 경로 — Tier 0/1",
                          "prod": "Bedrock Claude (global 프로파일) · bedrock-runtime Converse · 소스 리전 ap-northeast-2",
                          "demo": "운영과 동일 — Bedrock Claude (global 프로파일)",
                          "region": "저장: 서울 리전 / 추론: global 라우팅", "substituted": False}}
    monkeypatch.setattr(gate, "route_info", fake_route_info)

    def make_stream(answer, refuse=None):
        def fake_stream(system, user, max_tokens=800, route=None, purpose="s2", trace_id="", temperature=0.2):
            calls["stream"].append({"system": system, "user": user, "route": route, "purpose": purpose, "trace_id": trace_id})
            if refuse:
                b = gate.measure(system, user)
                b["piiRules"]["byType"][refuse] = 1          # 페이크 거부 — 유형·건수만 (실제 게이트도 값은 남기지 않는다)
                raise gate.GateRefused([refuse], 1, b, purpose)
            st = _FakeStream(answer, gate.current_route(route))
            st.boundary = gate.measure(system, user)   # 실측 함수는 진짜를 쓴다 (순수 함수)
            return st
        monkeypatch.setattr(gate, "stream", fake_stream)
    calls["make_stream"] = make_stream
    return calls


def _run(body):
    a = _Apigw()
    ctx = Ctx(apigw=a, conn_id="c", email=DEMO, rid="r1")
    s2.handle(ctx, body)
    return ctx, a.sent


def _stages(sent):
    return [e["step"] for e in sent if e["type"] == "s2.stage"]


def test_route_stage_is_emitted_first_with_badges_and_done_carries_gate_measurement(harness):
    harness["make_stream"]("고객님은 우대 조건을 충족하며 적용금리는 3.90%, 대출 가능 한도는 200,000,000원, 전월실적은 450,000원입니다.")
    ctx, sent = _run({"query": Q, "refDate": REF.isoformat()})
    steps = _stages(sent)
    assert steps[0] == "route", steps
    assert steps == ["route", "guardrail_in", "semantic", "lookup", "calc", "mask", "semantic_check"]
    assert sent[0]["type"] == "s2.stage" and sent[0]["step"] == "route"       # 어떤 이벤트보다 먼저
    route = sent[0]
    assert route["badge"]["title"] == "추론 경로"
    assert route["badge"]["prod"].startswith("Bedrock Claude (global 프로파일)") and route["badge"]["substituted"] is False
    assert route["modelId"] == "global.anthropic.claude-sonnet-5" and route["tier"] == "0/1"
    assert route["region"] == "ap-northeast-2" and route["inferenceRouting"] == "global" and route["storage"] == "ap-northeast-2"
    assert route["semanticLayer"] is True and route["plane"] == "cloud"
    by = {e["step"]: e for e in sent if e["type"] == "s2.stage"}
    # 기존 스테이지 형태 유지
    assert by["guardrail_in"]["result"]["action"] == "NONE"
    assert by["semantic"]["metric"]["name"] == "우대금리_적용율" and by["semantic"]["prevMonthMetric"]["name"] == "전월실적"
    assert by["semantic"]["semanticLayer"] is True and by["semantic"]["toggleNote"] is None
    assert "고객" in by["lookup"]["values"] and by["lookup"]["metricByEngine"]["전월실적"] == 450_000
    assert 12 <= len(by["lookup"]["txnSample"]) <= 20
    assert by["calc"]["rate"]["value"] == "3.90" and by["calc"]["metricByEngine"]["당월실적"] > 0
    mask = by["mask"]
    assert mask["plane"] == "boundary" and "maskedPayload" in mask and mask["piiOutbound"] == 0
    assert mask["badge"] == {**mask["badge"], "title": "익명화 게이트", "prod": "가명처리 · 토큰화 · 재식별",
                             "demo": "합성데이터 가명 생성 + 규칙 기반 토큰화 (ML 가명처리·재식별 볼트 미구현)"}
    assert set(mask["badge"]["refuseTypes"]) == set(gate.DEFAULT_REFUSE_TYPES)
    chk = by["semantic_check"]
    assert chk["semanticLayer"] is True and chk["engineValue"] == 450_000 and chk["modelValue"] == 450_000
    assert chk["mismatch"] is False and chk["period"]["prevMonth"] == "2026-08"
    # 게이트 호출: 마스킹 페이로드가 그대로 user, purpose='s2', 경로 미지정
    call = harness["stream"][0]
    assert call["user"] == mask["maskedPayload"] and call["purpose"] == "s2" and call["route"] is None
    assert call["trace_id"] == ctx.trace_id and call["system"] == s2.SYSTEM
    # 토큰 스트리밍 → done
    tokens = "".join(e["t"] for e in sent if e["type"] == "s2.token")
    assert "3.90%" in tokens
    done = sent[-1]
    assert done["type"] == "s2.done" and not done.get("blocked")
    assert done["modelId"] == "global.anthropic.claude-sonnet-5" and done["route"] == "claude" and done["tier"] == "0/1"
    assert done["boundary"]["fieldsPassed"] == ["고객 세그먼트", "고객명", "고객 식별", "상품", "적용금리", "우대 판정",
                                                "대출 가능 한도", "전월실적", "질문"]
    assert done["boundary"]["chars"] > 0 and done["boundary"]["piiRules"]["count"] == 0
    assert done["usage"] == {"inputTokens": 321, "outputTokens": 45, "totalTokens": 366}
    assert done["regionBadge"] == "저장: 서울 리전 / 추론: global 라우팅" and done["inferenceRouting"] == "global"
    assert done["semanticLayer"] is True and done["semanticCheck"]["mismatch"] is False
    assert done["inventedNumbers"] == [] and done["unmasked"] and done["plane"] == "local"  # 가짜 회신에는 토큰이 없어 재식별 대상이 없다
    # 트레이스: 라벨·수치만 — 값·원문 없음
    tr = harness["traces"][0]
    assert tr["scenario"] == "S2" and tr["blocked"] is False and tr["plane"] == "local"
    assert tr["modelId"] == "global.anthropic.claude-sonnet-5" and tr["route"] == "claude" and tr["tier"] == "0/1"
    assert tr["boundaryFields"] == done["boundary"]["fieldsPassed"] and tr["boundaryChars"] == done["boundary"]["chars"]
    assert tr["boundaryEstTokens"] == done["boundary"]["estTokens"] and tr["crossings"] == 1
    assert tr["tokensIn"] == 321 and tr["tokensOut"] == 45
    assert tr["semanticLayer"] is True and tr["semanticMismatch"] is False and tr["semanticStated"] is True
    assert "maskedFields" in tr and "customerName" in tr["maskedFields"]
    dumped = json.dumps(tr, ensure_ascii=False)
    for leak in ("김데모", "CUST-0042", "450,000", "450000", "3.90%"):
        assert leak not in dumped, leak


def test_semantic_layer_off_shows_silently_wrong_number(harness):
    # 먼저 계산엔진의 당월 합계를 알아내 '모델이 당월을 전월실적이라고 말한' 회신을 만든다 (모델 흉내가 아니라 파서 검증용 고정 문자열)
    prep = _prepare(semanticLayer=False)
    cur = prep["metricByEngine"]["당월실적"]
    harness["make_stream"](f"거래내역을 합산하면 전월실적은 {cur:,}원입니다. 적용금리 3.90%, 한도 200,000,000원.")
    ctx, sent = _run({"query": Q, "semanticLayer": False, "refDate": REF.isoformat()})
    by = {e["step"]: e for e in sent if e["type"] == "s2.stage"}
    assert by["route"]["semanticLayer"] is False
    assert by["semantic"]["semanticLayer"] is False and by["semantic"]["prevMonthMetric"] is None
    assert "안티패턴" in by["semantic"]["toggleNote"]
    # OFF 프롬프트: 거래내역 원본 + 정의 없음, 시스템 프롬프트도 OFF 변형
    call = harness["stream"][0]
    assert "[카드 거래내역 원본" in call["user"] and "취소분 제외" not in call["user"]
    assert call["system"] == s2.SYSTEM_NO_SEMANTIC
    chk = by["semantic_check"]
    assert chk["semanticLayer"] is False and chk["engineValue"] == 450_000 and chk["modelValue"] == cur
    assert chk["mismatch"] is True and "당월 승인 합계" in chk["note"] and "조용히 틀림" in chk["note"]
    done = sent[-1]
    assert done["semanticCheck"]["mismatch"] is True and done["semanticLayer"] is False
    # 원장 숫자이므로 수치 검증기는 통과 — 잡는 것은 semantic_check 뿐 (이것이 '조용히 틀림')
    assert done["inventedNumbers"] == []
    tr = harness["traces"][0]
    assert tr["semanticLayer"] is False and tr["semanticMismatch"] is True and tr["semanticStated"] is True


def test_semantic_check_reports_when_model_states_no_number(harness):
    harness["make_stream"]("우대 조건을 충족하십니다. 적용금리는 3.90%, 대출 가능 한도는 200,000,000원입니다.")
    _, sent = _run({"query": Q, "refDate": REF.isoformat()})
    chk = next(e for e in sent if e.get("step") == "semantic_check")
    assert chk["modelValue"] is None and chk["mismatch"] is False and chk["note"] == "모델이 수치를 제시하지 않음"


def test_route_override_gemma_flows_to_gate_and_badges(harness):
    harness["make_stream"]("전월실적 450,000원, 적용금리 3.90%, 한도 200,000,000원.")
    _, sent = _run({"query": Q, "route": "gemma", "refDate": REF.isoformat()})
    route = sent[0]
    assert route["step"] == "route" and route["route"] == "gemma" and route["tier"] == "2"
    assert route["modelId"] == "google.gemma-4-31b" and route["region"] == "us-west-2"
    assert route["badge"]["prod"] == "IDC GPU + vLLM (EKS Hybrid Nodes)"
    assert route["badge"]["demo"] == "Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체" and route["badge"]["substituted"] is True
    assert harness["stream"][0]["route"] == "gemma"
    done = sent[-1]
    assert done["route"] == "gemma" and done["modelId"] == "google.gemma-4-31b" and done["tier"] == "2"
    assert done["regionBadge"] == "저장: 서울 리전 / 추론: us-west-2 직접 호출"
    assert harness["traces"][0]["route"] == "gemma" and harness["traces"][0]["modelId"] == "google.gemma-4-31b"


def test_unknown_route_is_an_error_before_any_stage(harness):
    harness["make_stream"]("x")
    _, sent = _run({"query": Q, "route": "gpt-4"})
    assert len(sent) == 1 and sent[0]["type"] == "error" and "LLM_ROUTE" in sent[0]["message"]
    assert harness["stream"] == [] and harness["traces"] == []


def test_gate_refused_emits_blocked_done_and_payload_never_reaches_model(harness):
    harness["make_stream"]("never", refuse="CUSTOMER_TOKEN")
    ctx, sent = _run({"query": Q, "refDate": REF.isoformat()})
    steps = _stages(sent)
    assert steps == ["route", "guardrail_in", "semantic", "lookup", "calc", "mask"]   # 게이트 뒤 단계는 없다
    assert not any(e["type"] == "s2.token" for e in sent)
    done = sent[-1]
    assert done["type"] == "s2.done" and done["blocked"] is True and done["gateRefused"] is True
    assert done["blockedBy"] == "gate" and "모델에 전달되지 않았습니다" in done["message"]
    assert done["hits"] == [{"type": "CUSTOMER_TOKEN", "count": 1}]     # 유형·건수만 — 값 없음
    assert "boundary" in done and done["route"] == "claude" and done["modelId"] == "global.anthropic.claude-sonnet-5"
    tr = harness["traces"][0]
    assert tr["blocked"] is True and tr["blockedBy"] == "gate" and tr["gateRejected"] is True and tr["reason"] == "gate"
    assert tr["gateTypes"] == ["CUSTOMER_TOKEN"] and tr["crossings"] == 0 and tr["tokensIn"] == 0
    assert tr["modelId"] == "global.anthropic.claude-sonnet-5"    # 어떤 경로가 거부했는지는 남긴다
    assert tr["piiDetectors"] == ["rules(gate)"]


def test_guardrail_input_block_still_emits_route_first(harness, monkeypatch):
    harness["make_stream"]("x")
    monkeypatch.setattr(s2, "apply_guardrail", lambda text, source, grounding="", query="": {
        "action": "GUARDRAIL_INTERVENED", "topics": ["investment-advice"], "grounding": [], "pii": [], "words": [],
        "message": "투자 권유 관련 질문에는 답변할 수 없습니다."})
    _, sent = _run({"query": "어떤 상품이 제일 돈 많이 벌어요?"})
    assert _stages(sent) == ["route", "guardrail_in"]
    done = sent[-1]
    assert done["blocked"] is True and done.get("gateRefused") is None and done["topics"] == ["investment-advice"]
    assert done["route"] == "claude" and done["modelId"]
    assert harness["stream"] == []
    assert harness["traces"][0]["blocked"] is True and "blockedBy" not in harness["traces"][0]


def test_real_route_info_for_claude_works_offline_and_matches_gate_strings(monkeypatch):
    """게이트 route_info 는 어댑터를 지연 생성하므로 AWS 없이도 배지 문구를 준다 — 핸들러 route_stage 는 그것을 그대로 옮긴다."""
    gate.reset_adapters()
    try:
        rs = s2.route_stage("claude")
        ri = gate.route_info("claude")
        assert rs["badge"]["title"] == "추론 경로" and rs["badge"]["prod"] == ri["badge"]["prod"]
        assert rs["badge"]["demo"] == ri["badge"]["demo"] and rs["badge"]["region"] == "저장: 서울 리전 / 추론: global 라우팅"
        assert rs["modelId"] == ri["modelId"] and rs["inferenceRouting"] == "global" and rs["storageLabel"] == "서울 리전"
        gb = s2.gate_badge()
        assert gb["title"] == "익명화 게이트" and gb["prod"] == "가명처리 · 토큰화 · 재식별"
        assert gb["demo"] == "합성데이터 가명 생성 + 규칙 기반 토큰화 (ML 가명처리·재식별 볼트 미구현)"
    finally:
        gate.reset_adapters()


# ---------------------------------------------------------------------------
# §12.13 금지 표현 — 이 슬라이스가 소유한 파일
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rel", ["api/handlers/s2.py", "web/src/S2.tsx", "onprem/service.py", "onprem/personal_store.py",
                                 "tests/test_s2_v2.py"])
def test_owned_files_have_no_forbidden_terms(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    for bad in FORBIDDEN:
        assert bad not in src, (rel, bad)
    if rel == "web/src/S2.tsx":
        assert "PII VPC" not in src and "VPC 내부" in src
