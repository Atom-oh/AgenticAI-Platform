"""S2 privacy order/cache guarantees with synthetic values and injected AWS calls."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_s2_v2 import _env, harness, _run, _stages, Q, REF
from common import costguard, pii, privacy
from handlers import s2


def receipt(text, model="qwen"):
    return {"ok": True, "text": text, "evidence": {
        "status": "pass", "processor": "eks-sllm", "method": "redaction", "model": model,
        "modelId": "Qwen/Qwen3-8B", "modelRevision": "test-revision", "promptVersion": "test-v1",
        "entityCounts": [], "total": 0, "sourceChars": len(text), "outputChars": len(text),
        "ruleResidualCount": 0, "independentNer": "not-configured", "latencyMs": 10,
    }}


def test_s2_uses_sllm_for_free_text_and_independent_verification_for_structured_payload(harness, monkeypatch):
    steps = []
    raw = "김테스트 010-2345-6789의 전월실적을 알려주세요."
    cleaned = "⟨PERSON:01234567⟩ ⟨PHONE:abcdef12⟩의 전월실적을 알려주세요."
    def process(text, model, purpose, request_id):
        steps.append(purpose)
        value = receipt(cleaned if purpose == "query" else text, model)
        value["evidence"]["sourceChars"] = len(text)
        return value
    def guardrail(text, source, grounding="", query=""):
        assert raw not in text and "김테스트" not in text and "010-2345-6789" not in text
        if source == "INPUT":
            assert text == pii.guardrail_view(cleaned, pii.tokens_in(cleaned)) and steps == ["query"]
        else:
            assert query == pii.guardrail_view(cleaned, pii.tokens_in(cleaned)) and steps == ["query"]
        return {"action": "NONE", "topics": [], "grounding": [], "pii": [], "words": [], "message": ""}
    monkeypatch.setattr(privacy, "process", process)
    monkeypatch.setattr(s2, "apply_guardrail", guardrail)
    harness["make_stream"]("전월실적은 450,000원입니다.")
    _, sent = _run({"query": raw, "privacyModel": "qwen", "refDate": REF.isoformat()})
    assert _stages(sent).index("privacy_input") < _stages(sent).index("guardrail_in")
    assert _stages(sent).index("privacy_payload") < _stages(sent).index("mask")
    assert steps == ["query"]
    assert len(harness["stream"]) == 1
    assert cleaned in harness["stream"][0]["user"]
    assert "김테스트" not in json.dumps(harness["traces"], ensure_ascii=False)
    assert sent[-1]["privacy"]["status"] == "pass"
    assert sent[-1]["privacy"]["payload"]["modelInvoked"] is False
    assert sent[-1]["privacy"]["payload"]["processor"] == "schema-and-independent-scan"


def test_privacy_failure_blocks_downstream_and_never_replays_personal_results(harness, monkeypatch):
    invoked = []
    def process(text, model, purpose, request_id):
        invoked.append(purpose)
        raise privacy.PrivacyUnavailable("PRIVACY_UNAVAILABLE")
    monkeypatch.setattr(privacy, "process", process)
    monkeypatch.setattr(costguard, "get_cached", lambda *_: pytest.fail("S2 must not read personal event cache"))
    monkeypatch.setattr(costguard, "put_cached", lambda *_: pytest.fail("S2 must not store personal event cache"))
    harness["make_stream"]("must never execute")
    _, sent = _run({"query": Q})
    assert not harness["stream"]
    assert sent[-1]["type"] == "s2.done"
    assert sent[-1]["blockedBy"] == "privacy"
    assert sent[-1]["privacy"]["status"] == "blocked"
    assert "guardrail_in" not in _stages(sent)
    assert not any(event["type"] == "cache.replay" for event in sent)


def test_structured_payload_requires_independent_inspection(harness, monkeypatch):
    def unavailable(*args, **kwargs):
        raise pii.PiiVerificationUnavailable("unavailable")
    monkeypatch.setattr(pii, "scan_guardrail", unavailable)
    harness["make_stream"]("must not execute")
    _, sent = _run({"query": Q})
    assert not harness["stream"] and sent[-1]["blockedBy"] == "privacy"


def test_successful_s2_does_not_store_raw_lookup_or_response_events(harness, monkeypatch):
    monkeypatch.setattr(costguard, "get_cached", lambda *_: pytest.fail("no cache reads"))
    monkeypatch.setattr(costguard, "put_cached", lambda *_: pytest.fail("no cache writes"))
    harness["make_stream"]("전월실적은 450,000원입니다.")
    ctx, sent = _run({"query": Q})
    assert not sent[-1].get("blocked")
    assert ctx.recording is None


@pytest.mark.parametrize("query", ["가" * 501, {"text": "unsupported"}, ""])
def test_question_is_rejected_instead_of_silently_truncated(harness, monkeypatch, query):
    monkeypatch.setattr(privacy, "process", lambda *_: pytest.fail("invalid input must not be processed"))
    _, sent = _run({"query": query})
    assert sent[-1]["type"] == "error"
    assert not harness["stream"]


def test_additional_guardrail_anonymization_is_used_for_payload_and_grounding(harness, monkeypatch):
    safe = "고객의 전월실적을 알려주세요."
    def guardrail(text, source, grounding="", query=""):
        if source == "OUTPUT":
            assert query == safe
        return {"action": "ANONYMIZED" if source == "INPUT" else "NONE",
                "topics": [], "grounding": [], "pii": [], "words": [],
                "message": safe if source == "INPUT" else ""}
    monkeypatch.setattr(s2, "apply_guardrail", guardrail)
    harness["make_stream"]("전월실적은 450,000원입니다.")
    _, sent = _run({"query": Q})
    assert not sent[-1].get("blocked")
    assert safe in harness["stream"][0]["user"]
    assert Q not in harness["stream"][0]["user"]


def test_missing_output_guardrail_replacement_never_streams_unchecked_text(harness, monkeypatch):
    def guardrail(text, source, grounding="", query=""):
        return {"action": "ANONYMIZED" if source == "OUTPUT" else "NONE",
                "topics": [], "grounding": [], "pii": [], "words": [], "message": ""}
    monkeypatch.setattr(s2, "apply_guardrail", guardrail)
    harness["make_stream"]("합성 이름을 포함한 아직 검증하지 않은 답변")
    _, sent = _run({"query": Q})
    assert sent[-1]["blockedBy"] == "privacy"
    assert sent[-1]["privacy"]["stage"] == "output"
    assert not any(event["type"] == "s2.token" for event in sent)


@pytest.mark.parametrize("action", ["ANONYMIZED", "GUARDRAIL_INTERVENED"])
def test_output_removed_number_never_reappears_in_semantic_events(harness, monkeypatch, action):
    removed = "123456789012"
    def guardrail(text, source, grounding="", query=""):
        return {"action": action if source == "OUTPUT" else "NONE",
                "topics": [], "grounding": [], "pii": [], "words": [],
                "message": "전월실적은 [제거됨]원입니다." if source == "OUTPUT" else ""}
    monkeypatch.setattr(s2, "apply_guardrail", guardrail)
    harness["make_stream"](f"전월실적은 {removed}원입니다.")
    _, sent = _run({"query": Q})
    assert removed not in json.dumps(sent, ensure_ascii=False)
    assert removed not in json.dumps(harness["traces"], ensure_ascii=False)
    if action == "GUARDRAIL_INTERVENED":
        assert "semantic_check" not in _stages(sent)
        assert sent[-1]["blockedBy"] == "guardrail_output"


def test_oversized_answer_cannot_publish_an_unchecked_tail(harness):
    harness["make_stream"]("가" * 4001)
    _, sent = _run({"query": Q})
    assert sent[-1]["blockedBy"] == "privacy"
    assert not any(event["type"] == "s2.token" for event in sent)
    assert harness["traces"][-1]["crossings"] == 1


@pytest.mark.parametrize("response", [
    {},
    {"action": "NONE", "assessments": [], "usage": {}},
    {"action": "NONE", "assessments": [], "usage": {"sensitiveInformationPolicyUnits": 1},
     "guardrailCoverage": {"textCharacters": {"total": 10, "guarded": 9}}},
])
def test_malformed_or_partial_residual_scan_is_not_pass(monkeypatch, response):
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "configured")
    class Guardrail:
        def apply_guardrail(self, **kwargs): return response
    monkeypatch.setattr(pii.boto3, "client", lambda *_args, **_kwargs: Guardrail())
    with pytest.raises(pii.PiiVerificationUnavailable):
        pii.scan_outbound("합성 이름", strict=True)


def test_detect_only_guardrail_pii_is_counted_even_when_action_is_none(monkeypatch):
    text = "김테스트"
    response = {"action": "NONE", "usage": {"sensitiveInformationPolicyUnits": 1},
                "guardrailCoverage": {"textCharacters": {"total": len(text), "guarded": len(text)}},
                "assessments": [{"sensitiveInformationPolicy": {"piiEntities": [
                    {"type": "NAME", "action": "NONE", "detected": True, "match": text}
                ]}}]}
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "configured")
    class Guardrail:
        def apply_guardrail(self, **kwargs): return response
    monkeypatch.setattr(pii.boto3, "client", lambda *_args, **_kwargs: Guardrail())
    result = pii.scan_outbound(text, strict=True)
    assert result["count"] == 1
    assert text not in json.dumps(result, ensure_ascii=False)


def test_output_guardrail_coverage_distinguishes_context_from_guarded_answer():
    response = {"action": "NONE", "usage": {"sensitiveInformationPolicyUnits": 1},
                "guardrailCoverage": {"textCharacters": {"total": 57, "guarded": 22}}}
    pii.verify_guardrail_coverage(response, "가" * 22, context_chars=35)
    with pytest.raises(pii.PiiVerificationUnavailable):
        pii.verify_guardrail_coverage(response, "가" * 23, context_chars=35)


def test_anonymization_does_not_override_a_content_block_or_leak_matched_words(monkeypatch):
    text = "합성 문장"
    response = {"action": "GUARDRAIL_INTERVENED", "usage": {"sensitiveInformationPolicyUnits": 1},
                "guardrailCoverage": {"textCharacters": {"total": len(text), "guarded": len(text)}},
                "outputs": [{"text": "차단되었습니다."}],
                "assessments": [{
                    "sensitiveInformationPolicy": {"piiEntities": [
                        {"type": "NAME", "action": "ANONYMIZED", "detected": True, "match": "원문 이름"}
                    ]},
                    "wordPolicy": {"customWords": [{"action": "BLOCKED", "match": "원문 이름"}]},
                    "contentPolicy": {"filters": [{"action": "BLOCKED", "type": "MISCONDUCT"}]},
                }]}
    class Guardrail:
        def apply_guardrail(self, **kwargs): return response
    monkeypatch.setattr(s2.boto3, "client", lambda *_args, **_kwargs: Guardrail())
    result = s2.apply_guardrail(text, "INPUT")
    assert result["action"] == "GUARDRAIL_INTERVENED"
    assert result["words"] == ["CUSTOM_WORD"]
    assert "원문 이름" not in json.dumps(result, ensure_ascii=False)


def test_missing_processor_blocks_without_constructing_an_aws_client(monkeypatch):
    monkeypatch.delenv("MYDATA_PRIVACY_FUNCTION_ARN", raising=False)
    monkeypatch.setattr(privacy.boto3, "client", lambda *_args, **_kwargs: pytest.fail("no target configured"))
    with pytest.raises(privacy.PrivacyUnavailable):
        privacy.process("합성 질문", "qwen", "query", "test")


def test_public_query_cannot_claim_a_trusted_processing_token(monkeypatch):
    monkeypatch.setattr(privacy, "_invoke", lambda *_: pytest.fail("forged token must not reach the processor"))
    with pytest.raises(privacy.PrivacyUnavailable):
        privacy.process("⟨PERSON:abcdef12⟩", "qwen", "query", "test")


def test_guardrail_view_excludes_only_known_tokens_and_preserves_other_text(monkeypatch):
    known, unknown = "⟨PERSON:abcdef12⟩", "⟨PERSON:22223333⟩"
    original = f"{known}, 미확인 {unknown}, 300,000원"
    seen = []
    monkeypatch.setattr(s2, "apply_guardrail", lambda text, *args, **kwargs: seen.append(text) or {"action": "NONE"})
    s2.checked_guardrail(original, "INPUT", trusted_tokens={known})
    assert len(seen[0]) == len(original)
    assert known not in seen[0] and unknown in seen[0] and "300,000원" in seen[0]


def test_structured_tokens_are_not_a_dictionary_hash_of_customer_values():
    from onprem.masking import mask, unmask, strip_tokens
    source = "김테스트님, 김테스트의 금액은 300,000원입니다."
    first = mask(source, {"customerName": "김테스트"})
    second = mask(source, {"customerName": "김테스트"})
    assert first.text != second.text
    assert len(first.mapping) == 1
    assert unmask(first.text, first.mapping) == source
    assert "300,000원" in strip_tokens(first.text)


def test_strict_verification_failure_is_not_zero_pii(monkeypatch):
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "")
    with pytest.raises(pii.PiiVerificationUnavailable):
        pii.scan_outbound("개인정보 없는 합성 문장", strict=True)
    monkeypatch.setattr(pii, "GUARDRAIL_ID", "configured")
    monkeypatch.setattr(pii.boto3, "client", lambda *_args, **_kwargs: pytest.fail("oversized input must be rejected before network"))
    with pytest.raises(pii.PiiVerificationUnavailable):
        pii.scan_outbound("가" * 4001, strict=True)


def test_known_residual_pii_is_blocked_before_guardrail_network(monkeypatch):
    monkeypatch.setattr(pii.boto3, "client", lambda *_args, **_kwargs: pytest.fail("raw identifier cannot reach Guardrails"))
    result = pii.scan_outbound("010-2345-6789", strict=True)
    assert result["count"] > 0
    assert result["detectors"] == ["rules"]


@pytest.mark.parametrize("alter", [
    lambda value: value.update(ok=False),
    lambda value: value["evidence"].update(status="skipped"),
    lambda value: value["evidence"].update(processor="rules-only"),
    lambda value: value["evidence"].update(ruleResidualCount=1),
    lambda value: value["evidence"].update(model="other"),
    lambda value: value["evidence"].update(sourceChars=999),
    lambda value: value["evidence"].update(modelId=""),
    lambda value: value["evidence"].update(entityCounts=[{"type": "PERSON", "count": 1}]),
])
def test_api_client_rejects_missing_or_mismatched_processing_evidence(monkeypatch, alter):
    value = receipt("합성 질문")
    alter(value)
    class Lambda:
        def invoke(self, **kwargs):
            return {"Payload": io.BytesIO(json.dumps(value).encode())}
    monkeypatch.setenv("MYDATA_PRIVACY_FUNCTION_ARN", "arn:aws:lambda:ap-northeast-2:180294183052:function:privacy-test")
    monkeypatch.setattr(privacy.boto3, "client", lambda *_args, **_kwargs: Lambda())
    with pytest.raises(privacy.PrivacyUnavailable):
        privacy.process("합성 질문", "qwen", "query", "test")


def test_api_receipt_excludes_nested_originals(monkeypatch):
    value = receipt("⟨PERSON:abcdef12⟩")
    value["evidence"].update(sourceChars=len("김테스트"), total=1,
                             entityCounts=[{"type": "PERSON", "count": 1, "original": "김테스트"}])
    class Lambda:
        def invoke(self, **kwargs):
            return {"Payload": io.BytesIO(json.dumps(value).encode())}
    monkeypatch.setenv("MYDATA_PRIVACY_FUNCTION_ARN", "arn:aws:lambda:ap-northeast-2:180294183052:function:privacy-test")
    monkeypatch.setattr(privacy.boto3, "client", lambda *_args, **_kwargs: Lambda())
    result = privacy.process("김테스트", "qwen", "query", "test")
    assert "김테스트" not in json.dumps(result, ensure_ascii=False)
