import copy

import pytest

from workspace.rules import contract_hash, validate_contract


def contract():
    return {"title": "입력 전달", "assetIds": ["a"], "rules": [{
        "id": "R1", "title": "입력값이 확인 화면에 표시된다", "required": True,
        "source": {"kind": "explicit", "assetId": "a", "quote": "입력값 유지"},
        "steps": [{"action": "fill", "target": "amount", "value": "10000"},
                  {"action": "expectValue", "target": "amount", "value": "10000"}],
    }]}


def test_contract_preserves_meaning_and_has_content_addressed_version():
    c = validate_contract(contract(), {"a": "규칙: 입력값 유지"})
    assert c["rules"][0]["steps"][0]["value"] == "10000"
    assert contract_hash(c) == contract_hash({**c, "status": "approved", "version": 42})
    edited = copy.deepcopy(c)
    edited["rules"][0]["steps"][-1]["value"] = "20000"
    assert contract_hash(edited) != contract_hash(c)


@pytest.mark.parametrize("action,target", [("evaluate", "amount"), ("goto", "amount"),
                                         ("click", 'amount"] *'), ("fetch", "url")])
def test_arbitrary_execution_and_selector_injection_rejected(action, target):
    c = contract()
    c["rules"][0]["steps"].insert(0, {"action": action, "target": target, "value": "x"})
    with pytest.raises(ValueError):
        validate_contract(c)


def test_empty_assertions_unknown_sources_and_fabricated_quotes_rejected():
    c = contract()
    with pytest.raises(ValueError, match="原文|원문"):
        validate_contract(c, {"a": "다른 규칙"})
    c["rules"][0]["steps"].pop()
    with pytest.raises(ValueError, match="확인하는 단계"):
        validate_contract(c)
    c = contract()
    c["rules"][0]["source"]["assetId"] = "foreign"
    with pytest.raises(ValueError, match="선택한"):
        validate_contract(c)


def test_no_rules_or_all_optional_cannot_define_success():
    with pytest.raises(ValueError):
        validate_contract({"rules": []})
    c = contract()
    c["rules"][0]["required"] = False
    with pytest.raises(ValueError, match="필수"):
        validate_contract(c)


def test_unresolved_requirements_are_retained_not_silently_dropped():
    c = contract()
    c["unresolved"] = ["인증 실패 후 이동할 화면 미정"]
    assert validate_contract(c)["unresolved"] == c["unresolved"]
