import pytest

from workspace.rules import contract_hash, validate_contract


def base():
    return {"title": "가이드와 코드", "assetIds": ["guide-1"], "rules": [{
        "id": "R1", "title": "진행 버튼", "steps": [{"action": "expectVisible", "target": "next", "value": True}]}]}


def criteria():
    return {**base(), "projectId": "p-1", "productId": "product-1", "guidelineId": "g-1",
            "guidelineAssetId": "guide-1", "catalogHash": "1" * 64, "ontologyHash": "2" * 64}


def test_component_and_ontology_revisions_are_part_of_the_approved_contract_hash():
    original = criteria()
    digest = contract_hash(original)
    assert validate_contract(original)["catalogHash"] == original["catalogHash"]
    for change in [{"catalogHash": "3" * 64}, {"ontologyHash": "4" * 64}, {"guidelineId": "g-2"}]:
        assert contract_hash({**original, **change}) != digest


def test_partial_or_forged_criteria_cannot_be_normalized_away():
    for data in [{**base(), "projectId": "p-1"}, {**criteria(), "catalogHash": "unknown"},
                 {**criteria(), "guidelineAssetId": "not-selected"}]:
        with pytest.raises(ValueError):
            validate_contract(data)
