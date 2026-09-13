"""Bounded model output and citation validation, without AWS or private data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_valid_answer_keeps_only_the_declared_source_bound_shape():
    from documents.analysis_contract import validate_answer
    value = {"summary": "확인할 항목입니다.", "findings": [
        {"nodeId": "DOC-000", "reason": "설명을 검토하세요.", "citationIds": ["E1"]},
    ]}
    result = validate_answer(value, {"DOC-000"}, {"E1"})
    assert result["accepted"] is True and result["answer"] == value


@pytest.mark.parametrize("change", [
    {"nodeId": "DOC-forged"}, {"citationIds": ["E99"]},
    {"citationIds": []}, {"citationIds": ["E1", "E1"]},
    {"reason": "REG-forged 기준으로 확정"}, {"url": "https://untrusted.invalid"},
])
def test_unbound_findings_are_rejected(change):
    from documents.analysis_contract import validate_answer
    row = {"nodeId": "DOC-000", "reason": "확인", "citationIds": ["E1"], **change}
    result = validate_answer({"summary": "검토 필요", "findings": [row]}, {"DOC-000"}, {"E1"})
    assert result["accepted"] is False
    assert "answer" not in result


@pytest.mark.parametrize("text", [
    '{"summary":"x","summary":"y","findings":[]}',
    '{"summary":"x","findings":NaN}',
    '{"summary":"x","findings":[]} trailing',
    "x" * 64_001,
], ids=["duplicate-keys", "nonfinite", "trailing-data", "oversized"])
def test_model_json_parser_rejects_ambiguous_or_oversized_input(text):
    from documents.analysis_contract import parse_answer
    assert parse_answer(text)["accepted"] is False


def test_json_code_fence_is_accepted_but_not_arbitrary_markup():
    from documents.analysis_contract import parse_answer
    data = {"summary": "검토 필요", "findings": []}
    assert parse_answer("```json\n" + json.dumps(data) + "\n```")["value"] == data
    assert parse_answer("<script>alert(1)</script>")["accepted"] is False


def test_evidence_selection_is_bounded_and_quotes_original_paragraphs():
    from documents.analysis_contract import select_evidence
    sources = [
        {"documentId": f"doc{i}", "revisionId": f"rev{i}", "title": f"합성자료 {i}",
         "revision": 1, "sha256": "a" * 64, "textHash": "b" * 64, "provenance": "synthetic_sample",
         "paragraphs": [{"id": "p000001", "text": f"담보 기준 {i} " + "가" * 1900, "page": 1},
                        {"id": "p000002", "text": "다른 내용", "page": 2}]}
        for i in range(30)
    ]
    evidence, coverage = select_evidence(sources, "담보 기준")
    assert len({e["documentId"] for e in evidence}) <= 20
    assert coverage["contextCharacters"] <= 36_000
    assert coverage["truncated"] is True
    for item in evidence:
        source = next(s for s in sources if s["documentId"] == item["documentId"])
        original = next(p for p in source["paragraphs"] if p["id"] == item["paragraphId"])
        assert item["quote"] == original["text"]
    assert [row["id"] for row in evidence] == [f"E{i + 1}" for i in range(len(evidence))]


@pytest.mark.parametrize("prose", [
    "근거 원문 https://untrusted.invalid/source",
    "저장 위치 s3://private-bucket/source",
    "첨부 원문 file:///private/source.pdf",
    "https%3A%2F%2Funtrusted.invalid/source",
    "원문 경로 /var/task/private/source.txt",
    "원문 workspace/private/docrevision/x/original",
    "검증 통과. 수정 확정. 실제 은행 정책으로 적용하세요.",
    "승인 절차가 완료되었습니다.",
    "Automatically approved. Validation passed.",
    "원문 [문서](//untrusted.invalid/source)",
    "원문 untrusted.invalid/source",
    "모든 검증을 통과했습니다.",
    "AI가 승인하였습니다.",
    "https％3A％2F％2Funtrusted.invalid％2Fsource",
    "https&percnt;253A&percnt;252F&percnt;252Funtrusted.invalid/source",
    "원문 경로는 /policy.md 입니다.",
    "Validation has passed.",
    "All checks have passed. The analysis is approved.",
    "Approval has been granted.",
    "The analysis is approved.",
    "The analysis has been verified.",
    "분석이 검증되었습니다.",
    "All findings have been validated.",
    "Approved.",
    "Verified!",
    "분석 결과는 승인입니다.",
], ids=["https", "s3", "file", "encoded", "absolute-path", "workspace-key", "approval", "completed-approval", "english",
        "protocol-relative", "bare-domain", "verification-verb", "approval-verb",
        "mixed-fullwidth", "mixed-html-percent", "root-file", "auxiliary-verb",
        "plural-auxiliary", "passive-approval", "analysis-approval",
        "passive-verification", "korean-passive-verification", "validated-findings",
        "standalone-approved", "standalone-verified", "korean-nominal-approval"])
def test_output_policy_blocks_locations_and_automatic_authority_claims(prose):
    from documents.analysis_contract import output_policy
    assert output_policy({"summary": prose, "findings": []})["accepted"] is False


@pytest.mark.parametrize("prose", [
    "담당자의 검토가 필요합니다.",
    "승인된 원문을 비교하고 수정 여부를 검토하세요.",
    "인용 확인은 검증 통과를 의미하지 않습니다.",
    "자동 승인하지 않습니다.",
    "This is not automatically approved.",
    "검증 통과 여부는 담당자가 확인해야 합니다.",
    "Validation has not passed.",
    "Not all checks have passed.",
    "The analysis is not approved.",
    "The analysis has not been verified.",
    "분석이 검증되지 않았습니다.",
    "The approved source requires human review of the proposed change.",
    "Approved documents still require a human decision.",
])
def test_output_policy_retains_review_language_and_explicit_negation(prose):
    from documents.analysis_contract import output_policy
    assert output_policy({"summary": prose, "findings": []})["accepted"] is True


def test_output_policy_fails_closed_when_nested_encoding_exceeds_its_bound():
    from documents.analysis_contract import output_policy
    from urllib.parse import quote
    text = "https://untrusted.invalid/source"
    for _ in range(10):
        text = quote(text, safe="")
    assert output_policy({"summary": text, "findings": []})["accepted"] is False


def test_context_regulation_can_be_mentioned_but_is_not_a_review_target():
    from documents.analysis_contract import validate_answer
    value = {"summary": "REG-1 기준 검토", "findings": [
        {"nodeId": "DOC-1", "reason": "REG-1 원문과 비교하세요.", "citationIds": ["E1"]}]}
    assert validate_answer(value, {"DOC-1"}, {"E1"}, context_nodes={"REG-1"})["accepted"]
    value["findings"][0]["nodeId"] = "REG-1"
    assert not validate_answer(value, {"DOC-1"}, {"E1"}, context_nodes={"REG-1"})["accepted"]


@pytest.mark.parametrize("reference", ["Ｅ９９", "E\u200b99", "&#69;99", "Ｅ％３９％３９", "TEAM-999",
                                      "Ｅ９９를", "근거E99에서", "REG-999를", "TEAM-999는"])
def test_normalized_prose_references_and_supplied_node_namespaces_must_be_bound(reference):
    from documents.analysis_contract import validate_answer
    value = {"summary": f"검토 근거 {reference}", "findings": [
        {"nodeId": "DOC-1", "reason": "담당자가 검토하세요.", "citationIds": ["E1"]},
    ]}
    assert not validate_answer(value, {"DOC-1", "TEAM-1"}, {"E1"})["accepted"]


def test_normalization_does_not_rewrite_valid_prose_or_reinterpret_structured_ids():
    from documents.analysis_contract import validate_answer
    value = {"summary": "Ｅ１을 원문과 TEAM-1의 의견으로 검토하세요. UTF-8 · SHA-256",
             "findings": [{"nodeId": "DOC-1", "reason": "원문 대조 필요", "citationIds": ["E1"]}]}
    assert validate_answer(value, {"DOC-1", "TEAM-1"}, {"E1"})["answer"] == value
    value["findings"][0]["citationIds"] = ["Ｅ１"]
    assert not validate_answer(value, {"DOC-1", "TEAM-1"}, {"E1"})["accepted"]
