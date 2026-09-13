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
