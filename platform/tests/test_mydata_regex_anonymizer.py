"""MyData 정규식 익명화: 식별자만 바꾸고 금융 사실은 보존하며, S2 기본 처리기로 동작하는지 검증 (합성 값)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_s2_v2 import _env, harness, _run, _stages, Q, REF  # noqa: F401
from common import anonymize, pii, privacy
from handlers import s2

FINANCIAL = "저는 회사원이고 연봉 8천만원, 월급 6,500,000원, 대출 25000000원, 금리 3.5%, 기간 12개월, 2026-10-14 기준, 서울 강남구 거주입니다."


def test_non_identifying_mydata_values_pass_unchanged():
    result = anonymize.anonymize(FINANCIAL)
    assert result["text"] == FINANCIAL and result["total"] == 0


@pytest.mark.parametrize("raw,kind", [
    ("연락처는 010-0000-0000 입니다", "PHONE"),
    ("사무실 02-123-4567 로 연락", "PHONE"),
    ("메일 mydata@example.invalid 로 보내 주세요", "EMAIL"),
    ("주민번호 900101-1234567 입니다", "RRN"),
    ("카드 1234-5678-9012-3456 결제", "CARD"),
    ("계좌번호 123-456789-01234 로 이체", "ACCOUNT"),
    ("계좌번호는 12345678901234 입니다", "ACCOUNT"),
    ("여권 M12345678 갱신", "PASSPORT"),
    ("고객 CUST-0001 조회", "CUSTOMER_ID"),
    ("생년월일은 1990년 1월 5일 입니다", "DOB"),
    ("주소는 서울특별시 중구 가상로 123, 101동 202호입니다", "ADDRESS"),
    ("제 이름은 홍길동입니다", "PERSON"),
    ("김철수님 상담 부탁드립니다", "PERSON"),
])
def test_each_identifier_type_is_replaced_with_a_typed_random_token(raw, kind):
    result = anonymize.anonymize(raw)
    tokens = pii.tokens_in(result["text"])
    assert result["counts"] == {kind: 1} and len(tokens) == 1
    assert next(iter(tokens)).startswith(f"⟨{kind}:")
    assert not pii.scan_rules(pii.guardrail_view(result["text"], tokens))


def test_same_value_reuses_one_token_and_tokens_change_per_request():
    text = "010-0000-0000 이 맞나요? 다시 01000000000 로 확인"
    first, second = anonymize.anonymize(text), anonymize.anonymize(text)
    assert len(pii.tokens_in(first["text"])) == 1 and first["counts"] == {"PHONE": 2}
    assert pii.tokens_in(first["text"]) != pii.tokens_in(second["text"])


def test_titles_and_occupations_are_not_treated_as_names():
    text = "고객님, 담당자님께 전달해 주세요. 저는 직장인이고 공무원 배우자가 있습니다."
    assert anonymize.anonymize(text)["text"] == text


def test_evidence_never_contains_originals_and_reports_no_model_call():
    raw = "제 이름은 홍길동이고 연락처는 010-0000-0000, 연봉 8천만원입니다"
    out = anonymize.process(raw, "trace-1")
    flat = repr(out["evidence"])
    assert "홍길동" not in out["text"] and "010-0000-0000" not in out["text"] and "8천만원" in out["text"]
    assert "홍길동" not in flat and "010" not in flat
    ev = out["evidence"]
    assert ev["processor"] == "regex-anonymizer" and ev["modelInvoked"] is False and ev["status"] == "pass"
    assert ev["total"] == 2 and {c["type"] for c in ev["entityCounts"]} == {"PERSON", "PHONE"}
    assert ev["sourceChars"] == len(raw) and ev["outputChars"] == len(out["text"])


@pytest.mark.parametrize("text,code", [
    ("⟨PERSON:abcdef12⟩ 우대금리", "PRIVACY_RESERVED_TOKEN_INPUT"),
    ("가" * 3000, "PRIVACY_INVALID_REQUEST"),
    ("   ", "PRIVACY_INVALID_REQUEST"),
])
def test_forged_tokens_and_invalid_input_are_rejected(text, code):
    with pytest.raises(anonymize.AnonymizationRejected) as error:
        anonymize.process(text, "trace-1")
    assert error.value.code == code


def test_s2_defaults_to_regex_without_calling_the_eks_processor(harness, monkeypatch):
    monkeypatch.setattr(privacy, "process", lambda *_: pytest.fail("EKS processor is optional, not the default"))
    harness["make_stream"]("우대금리 조건을 충족합니다.")
    raw = "제 이름은 홍길동이고 연락처는 010-0000-0000 입니다. " + Q
    _, sent = _run({"query": raw, "refDate": REF.isoformat()})
    by = {e["step"]: e for e in sent if e["type"] == "s2.stage"}
    assert by["privacy_input"]["processor"] == "regex-anonymizer" and by["privacy_input"]["modelInvoked"] is False
    assert by["privacy_input"]["plane"] == "cloud"
    assert "홍길동" not in by["privacy_input"]["sanitizedPreview"]
    assert by["mask"]["badge"]["privacyProcessor"] == "regex-anonymizer"
    assert sent[-1]["type"] == "s2.done" and not sent[-1].get("blocked")
    user = harness["stream"][0]["user"]
    assert "홍길동" not in user and "010-0000-0000" not in user


def test_s2_explanation_model_comes_from_the_platform_catalog(harness):
    harness["make_stream"]("우대금리 조건을 충족합니다.")
    model = "global.anthropic.claude-haiku-5-5"
    _, sent = _run({"query": Q, "refDate": REF.isoformat(), "model": model})
    assert harness["stream"][0]["model_id"] == model
    assert next(e for e in sent if e.get("step") == "route")["modelId"] == model


@pytest.mark.parametrize("body", [
    {"model": "https://outside.example/model"},
    {"model": "global.anthropic.claude-sonnet-5-5", "route": "gemma"},
])
def test_s2_rejects_uncataloged_or_non_bedrock_model_choice_before_any_stage(harness, body):
    harness["make_stream"]("must never execute")
    _, sent = _run({"query": Q, **body})
    assert not harness["stream"] and not _stages(sent)
    assert sent[-1]["type"] == "error"


def test_privacy_model_listing_keeps_regex_available_when_eks_is_unconfigured(monkeypatch):
    monkeypatch.delenv("MYDATA_PRIVACY_FUNCTION_ARN", raising=False)
    events = []
    ctx = type("Ctx", (), {"done": lambda self, kind, **kw: events.append(kw)})()
    s2.privacy_models(ctx, {})
    out = events[-1]
    assert out["defaultModel"] == "regex" and out["available"] is True
    assert out["models"][0]["id"] == "regex" and out["models"][0]["available"] is True
    assert "EKS" in out["message"]
    assert "global.openai.gpt-6.1-sol" in {m["id"] for m in out["explanationModels"]}


def test_s2_route_badge_names_the_selected_models_provider():
    badge = s2.route_stage("claude", "global.openai.gpt-6.1-sol")["badge"]
    assert "GPT-6.1 Sol (OpenAI)" in badge["prod"] and "Claude" not in badge["prod"] + badge["demo"]
    assert s2.route_stage("claude")["badge"]["prod"].startswith("Bedrock Claude (global 프로파일)")


@pytest.mark.parametrize("text", [
    "저는 전월실적이 30만원인데 우대금리 조건이 궁금해요",
    "전 연봉이 8천만원이고 신용점수가 900점입니다",
    "1년 전 대출을 받았는데 금리 3.5%예요",
    "저는 서울에 살아요. 저는 전업주부이고 남편님 소득은 6천만원",
    "우수 고객님 혜택과 신규 고객님 혜택, 사모님과 이모님께 안내",
])
def test_intro_phrases_and_honorifics_do_not_erase_financial_words(text):
    assert anonymize.anonymize(text)["text"] == text


@pytest.mark.parametrize("raw,kept", [
    ("김철수님께 전달해 주세요", "님께 전달해 주세요"),
    ("김철수씨가 상담했어요", "씨가 상담했어요"),
    ("홍길동 고객님 상담", " 고객님 상담"),
    ("저는 홍길동입니다", "입니다"),
    ("제 이름은 김영이입니다", "입니다"),
    ("서울시 강남구 테헤란로 123 거주", " 거주"),
])
def test_names_with_particles_and_short_city_addresses_are_replaced(raw, kept):
    out = anonymize.anonymize(raw)
    assert out["total"] == 1 and out["text"].endswith(kept)
    assert pii.tokens_in(out["text"])
