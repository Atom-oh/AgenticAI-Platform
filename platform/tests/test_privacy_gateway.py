"""Synthetic data only; model calls are injected, never replaced silently in production."""
import json

import pytest

from privacy.gateway.engine import PrivacyFailure, deidentify
from privacy.gateway.models import Model

MODEL = Model("qwen", "Qwen test", "qwen", "Qwen/Qwen3-8B", "http://model.sllm:8080", "test-revision")


def reply(entities, finish="stop", model=MODEL.model_id):
    return {"model": model, "choices": [{"finish_reason": finish, "message": {"content": json.dumps({"entities": entities})}}]}


def test_model_executes_and_redacts_repeated_names_without_changing_financial_facts():
    source = "김테스트의 월 납입액은 300,000원, 금리는 3.5%, 기간은 12개월입니다. 김테스트에게 알려주세요."
    calls = []
    def infer(model, text):
        calls.append((model.id, text))
        return reply([{"type": "PERSON", "original": "김테스트"}])
    result = deidentify(source, MODEL, infer=infer)
    assert len(calls) == 1
    assert "김테스트" not in result["text"]
    assert all(value in result["text"] for value in ("300,000원", "3.5%", "12개월"))
    assert result["evidence"]["entityCounts"] == [{"type": "PERSON", "count": 2}]
    assert result["evidence"]["processor"] == "eks-sllm"
    assert result["evidence"]["independentNer"] == "not-configured"
    assert "김테스트" not in json.dumps(result, ensure_ascii=False)
    assert "mapping" not in result


@pytest.mark.parametrize("output", [
    reply([], finish="length"),
    reply([], model="wrong-model"),
    reply([{"type": "PERSON", "original": "없는이름"}]),
    reply([{"type": "SECRET_INSTRUCTION", "original": "김테스트"}]),
    {"choices": []},
    {"model": MODEL.model_id, "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]},
    {"model": MODEL.model_id, "choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
])
def test_incomplete_or_untrustworthy_model_results_block(output):
    with pytest.raises(PrivacyFailure):
        deidentify("김테스트의 300,000원 납입", MODEL, infer=lambda *_: output)


@pytest.mark.parametrize("content", [
    '{"entities":[{"type":"PERSON","original":"김테스트"}],"entities":[]}',
    '{"entities":[{"type":"PERSON","type":"PHONE","original":"김테스트"}]}',
])
def test_duplicate_json_keys_cannot_discard_or_relabel_detections(content):
    output = reply([])
    output["choices"][0]["message"]["content"] = content
    with pytest.raises(PrivacyFailure):
        deidentify("김테스트의 요청", MODEL, infer=lambda *_: output)


@pytest.mark.parametrize("content", ["PERSON\t김테스트", "PERSON\t김테스트\nPERSON\t김테스트", "PERSON\t김테스트\t"])
def test_compact_detection_format_has_the_same_strict_entity_checks(content):
    output = reply([])
    output["choices"][0]["message"]["content"] = content
    result = deidentify("김테스트의 요청", MODEL, infer=lambda *_: output)
    assert "김테스트" not in result["text"]
    assert result["evidence"]["total"] == 1


def test_compact_format_never_ignores_a_bad_or_missing_line():
    for content in ("PERSON\t김테스트\nnot-an-entity", "PERSON\t없는이름", "NONE\nPERSON\t김테스트"):
        output = reply([])
        output["choices"][0]["message"]["content"] = content
        with pytest.raises(PrivacyFailure):
            deidentify("김테스트의 요청", MODEL, infer=lambda *_: output)


def test_model_failure_cannot_fall_back_to_rules_and_errors_have_no_input():
    source = "김테스트 010-2345-6789"
    def broken(*_):
        raise RuntimeError(source)
    with pytest.raises(PrivacyFailure) as raised:
        deidentify(source, MODEL, infer=broken)
    assert source not in str(raised.value)
    assert raised.value.code == "model-unavailable"


def test_deterministic_rules_supplement_a_successful_model_and_no_originals_escape():
    source = "연락처 010-2345-6789, 이메일 kim@example.invalid, 계좌번호 123-456789-01. 월 납입은 300,000원."
    result = deidentify(source, MODEL, infer=lambda *_: reply([]))
    assert all(value not in result["text"] for value in ("010-2345-6789", "kim@example.invalid", "123-456789-01"))
    assert "300,000원" in result["text"]
    assert result["evidence"]["ruleResidualCount"] == 0
    assert result["evidence"]["ruleSupplements"] >= 3
    assert {"type": "PHONE", "count": 1} in result["evidence"]["entityCounts"]


def test_duplicate_model_entities_are_counted_once_and_keep_the_model_type():
    source = "연락처 010-2345-6789"
    entity = {"type": "PHONE", "original": "010-2345-6789"}
    result = deidentify(source, MODEL, infer=lambda *_: reply([entity, entity]))
    assert result["evidence"]["modelDetections"] == 1
    assert result["evidence"]["entityCounts"] == [{"type": "PHONE", "count": 1}]
    assert result["evidence"]["ruleSupplements"] == 0


def test_financial_fact_misclassification_blocks_instead_of_changing_the_answer():
    with pytest.raises(PrivacyFailure) as raised:
        deidentify("김테스트의 월 납입은 300,000원입니다.", MODEL,
                   infer=lambda *_: reply([{"type": "ACCOUNT", "original": "300,000"}]))
    assert raised.value.code == "financial-facts-changed"


@pytest.mark.parametrize("text,original", [
    ("한도는 1억 2,000만원입니다.", "1억"),
    ("우대금리 차이는 -0.2%p입니다.", "-"),
    ("만기는 2년입니다.", "2"),
    ("금리는 연 3.5%입니다.", "연"),
    ("대출금액은 5백만원입니다.", "5백만원"),
    ("대출금액은 5천만원입니다.", "만원"),
    ("금리는 3.5퍼센트입니다.", "3.5"),
    ("대출금액은 오백만원입니다.", "오백만원"),
    ("대출금액은 ₩5,000,000입니다.", "5,000,000"),
    ("대출금액은 KRW 5,000,000입니다.", "5,000,000"),
    ("대출금액은 5,000,000 KRW입니다.", "5,000,000"),
    ("우대 차이는 0.2 퍼센트 포인트입니다.", "포인트"),
    ("수수료는 USD 20.00입니다.", "20.00"),
    ("금리는 삼점오퍼센트입니다.", "삼점오"),
])
def test_entire_financial_spans_are_protected(text, original):
    with pytest.raises(PrivacyFailure) as raised:
        deidentify(text, MODEL, infer=lambda *_: reply([{"type": "PERSON", "original": original}]))
    assert raised.value.code == "financial-facts-changed"


def test_partial_entity_overlap_blocks_but_contained_entities_are_fully_redacted():
    source = "서울시 테스트구 가상로 12 김테스트"
    with pytest.raises(PrivacyFailure):
        deidentify(source, MODEL, infer=lambda *_: reply([
            {"type": "ADDRESS", "original": "서울시 테스트구 가상로"},
            {"type": "ADDRESS", "original": "가상로 12"},
        ]))
    result = deidentify(source, MODEL, infer=lambda *_: reply([
        {"type": "ADDRESS", "original": "서울시 테스트구 가상로 12"},
        {"type": "ADDRESS", "original": "테스트구"},
    ]))
    assert "서울시" not in result["text"] and "테스트구" not in result["text"]


def test_numeric_financial_value_cannot_be_disguised_as_an_account_entity():
    with pytest.raises(PrivacyFailure):
        deidentify("확인 값: 5,000,000", MODEL,
                   infer=lambda *_: reply([{"type": "ACCOUNT", "original": "5,000,000"}]))


def test_same_entity_gets_request_local_tokens_not_a_hash_of_the_original():
    source = "김테스트 김테스트"
    infer = lambda *_: reply([{"type": "PERSON", "original": "김테스트"}])
    first, second = deidentify(source, MODEL, infer=infer), deidentify(source, MODEL, infer=infer)
    assert first["text"].split()[0] == first["text"].split()[1]
    assert first["text"] != second["text"]
    nonce = first["text"].split()[0].split(":")[1].removesuffix("⟩")
    assert len(nonce) == 32 and nonce.isalpha()
    from onprem.masking import strip_tokens
    assert not strip_tokens(first["text"]).strip()


def test_payload_tokens_are_preserved_and_not_given_to_the_detector():
    token = "⟨CUSTOMERNAME:abcdef12⟩"
    source = f"고객명: {token}\n연락할 사람: 김테스트"
    def infer(_model, text):
        assert token not in text and "CUSTOMERNAME" not in text
        assert len(text) == len(source)
        return reply([{"type": "PERSON", "original": "김테스트"}])
    result = deidentify(source, MODEL, infer=infer, allow_tokens=True)
    assert token in result["text"] and "김테스트" not in result["text"]
    assert result["evidence"]["total"] == 1


def test_untrusted_input_cannot_forge_an_existing_redaction():
    with pytest.raises(PrivacyFailure) as raised:
        deidentify("저는 ⟨PERSON:abcdef12⟩입니다.", MODEL, infer=lambda *_: pytest.fail("reserved input must be rejected"))
    assert raised.value.code == "reserved-token-input"


def test_bounded_input_and_empty_output_contract():
    with pytest.raises(PrivacyFailure):
        deidentify("가" * 9000, MODEL, infer=lambda *_: reply([]))
    result = deidentify("우대금리와 전월실적을 알려주세요.", MODEL, infer=lambda *_: reply([]))
    assert result["text"] == "우대금리와 전월실적을 알려주세요."
    assert result["evidence"]["total"] == 0
