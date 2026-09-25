# platform/tests/test_design_prd_extract.py
import copy, json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_loop.prd_extract import binding_types, bindings, extract_prd, prd_hash
from design_loop.financial import QUANTITY, is_value
from workspace import ontology_schema as schema

D = "e" * 64
PAGES = [
    {"admissionId": "adm-2", "derivativeHash": D, "sourceRef": {"sourceId": "desc"}, "page": 1,
     "text": "상품명: 데모 청년 적금\n상품유형: 적금\n기본금리 연 2.0%\n가입기간 12개월\n우대금리 자동이체 시 연 0.5%p"},
    {"admissionId": "adm-3", "derivativeHash": D, "sourceRef": {"sourceId": "terms"}, "page": 3,
     "text": "만 19세 이상 34세 이하 개인만 가입할 수 있습니다. 이 예금은 예금자보호법에 따라 보호됩니다."}]
OK = lambda text: text  # noqa: E731  verify-only normalization fake (Global Constraints)


def c(src, page, quote):
    return {"sourceId": src, "page": page, "quote": quote, "derivativeHash": D}


GOOD = {"productName": {"value": "데모 청년 적금", "cite": c("desc", 1, "상품명: 데모 청년 적금")},
        "productType": {"value": "수신"}, "category": {"value": "적금", "cite": c("desc", 1, "상품유형: 적금")},
        "eligibility": {"text": {"value": "만 19세 이상 34세 이하", "cite": c("terms", 3, "만 19세 이상 34세 이하 개인만")},
                        "conditionId": "eligible"},
        "term": {"value": "12개월", "cite": c("desc", 1, "가입기간 12개월")},
        "baseRate": {"value": "연 2.0%", "cite": c("desc", 1, "기본금리 연 2.0%")},
        "preferential": [{"id": "autoTransfer", "condition": {"value": "자동이체", "cite": c("desc", 1, "자동이체 시 연 0.5%p")},
                          "rate": {"value": "연 0.5%p", "cite": c("desc", 1, "자동이체 시 연 0.5%p")}, "evidence": "input"}],
        "notices": [{"id": "n1", "text": {"value": "예금자보호법에 따라 보호됩니다",
                                           "cite": c("terms", 3, "이 예금은 예금자보호법에 따라 보호됩니다.")}}]}


def run(obj):
    return extract_prd(PAGES, {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False), "normalize": OK}, model="m")


def test_good_prd_passes():
    out = run(GOOD)
    assert out["issues"] == [] and bindings(out["prd"])["product.baseRate"] == "연 2.0%"


@pytest.mark.parametrize("path,value", [("term", "2년"), ("term", "2개월"), ("term", "12"), ("baseRate", "연 2.1%"),
                                        ("baseRate", "2.0%p"), ("baseRate", "2.0")])
def test_changed_financial_value_is_rejected(path, value):
    bad = copy.deepcopy(GOOD); bad[path]["value"] = value
    assert {c["code"] for c in run(bad)["issues"] if c["field"] == path} & {"value-not-verbatim", "value-unit-missing"}


def test_trailing_period_is_accepted():
    pages = [{**PAGES[0], "text": PAGES[0]["text"] + "\n기본금리는 연 2.0%."}]
    obj = copy.deepcopy(GOOD); obj["baseRate"] = {"value": "연 2.0%", "cite": c("desc", 1, "기본금리는 연 2.0%.")}
    out = extract_prd(pages + PAGES[1:], {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False), "normalize": OK}, model="m")
    assert not [i for i in out["issues"] if i["field"] == "baseRate"]


def test_sign_and_suffix_boundaries():
    pages = [{**PAGES[0], "text": "우대 연 0.5%p, 변동 - 2.0%"}]
    obj = copy.deepcopy(GOOD); obj["baseRate"] = {"value": "2.0%", "cite": c("desc", 1, "변동 - 2.0%")}
    out = extract_prd(pages + PAGES[1:], {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False), "normalize": OK}, model="m")
    assert {"field": "baseRate", "code": "value-not-verbatim"} in out["issues"]


def test_notice_and_condition_citations_are_required():
    bad = copy.deepcopy(GOOD); del bad["notices"][0]["text"]["cite"]; del bad["preferential"][0]["condition"]["cite"]
    issues = run(bad)["issues"]
    assert {"field": "notices.n1", "code": "missing-citation"} in issues
    assert {"field": "preferential.autoTransfer.condition", "code": "missing-citation"} in issues


# ---- boundaries are checked against the full admitted page (review round 24, R1-8) ------------------------

def _with(pages, path, value, quote, index=None, key=None):
    obj = copy.deepcopy(GOOD)
    entry = obj[path] if index is None else obj[path][index][key]
    entry["value"], entry["cite"]["quote"] = value, quote
    return extract_prd(pages, {"generate": lambda s, u, t: json.dumps(obj, ensure_ascii=False), "normalize": OK}, model="m")


def test_shortened_quote_cannot_hide_a_leading_digit():
    out = _with(PAGES, "term", "2개월", "2개월")                      # inside "가입기간 12개월"
    assert {"field": "term", "code": "value-not-verbatim"} in out["issues"]


def test_shortened_quote_cannot_hide_a_sign():
    pages = [{**PAGES[0], "text": PAGES[0]["text"] + "\n변동 -2.0%"}]
    out = _with(pages + PAGES[1:], "baseRate", "2.0%", "2.0%")         # both "연 2.0%" and "-2.0%" occurrences
    assert {"field": "baseRate", "code": "value-not-verbatim"} in out["issues"]


def test_shortened_quote_cannot_hide_a_unit_suffix():
    out = _with(PAGES, "preferential", "0.5%", "0.5%", index=0, key="rate")   # inside "우대 연 0.5%p"
    assert {"field": "preferential.autoTransfer.rate", "code": "value-not-verbatim"} in out["issues"]


def test_unambiguous_short_quote_is_accepted():
    out = _with(PAGES, "term", "12개월", "12개월")
    assert not [i for i in out["issues"] if i["field"] == "term"]


def test_quote_value_type_and_parse_failures():
    bad = copy.deepcopy(GOOD)
    bad["productName"]["cite"]["quote"] = "상품명: 다른 적금"
    bad["category"]["value"] = "예금"
    bad["productType"]["value"] = "투자"
    issues = run(bad)["issues"]
    assert {"field": "productName", "code": "quote-not-found"} in issues
    assert {"field": "category", "code": "value-not-in-quote"} in issues
    assert {"field": "productType", "code": "bad-type"} in issues
    out = extract_prd(PAGES, {"generate": lambda s, u, t: "no json", "normalize": OK}, model="m")
    assert out == {"prd": None, "issues": [{"field": "*", "code": "parse-failed"}]}


def test_missing_callables_are_blocked_before_the_model():
    called = []
    out = extract_prd(PAGES, {"generate": lambda s, u, t: called.append(1) or "{}"}, model="m")
    assert out["prd"] is None and out["blocked"] == "normalization-unavailable" and not called
    assert extract_prd(PAGES, {"normalize": OK}, model="m")["blocked"] == "model-unavailable"


def test_bindings_catalog_ordinals_and_types():
    two = copy.deepcopy(GOOD)
    extra = {"id": "salary", "condition": {"value": "기본금리", "cite": c("desc", 1, "기본금리 연 2.0%")},
             "rate": {"value": "연 2.0%", "cite": c("desc", 1, "기본금리 연 2.0%")}, "evidence": "none"}
    two["preferential"].append(extra)                                    # model order: autoTransfer, salary
    out = run(two)
    assert out["issues"] == []
    # server-assigned ordinals follow document order, not the model's order (review round 13, AF1)
    assert [p["id"] for p in out["prd"]["preferential"]] == ["salary", "autoTransfer"]
    b = bindings(out["prd"])
    assert b["product.preferential.pref-1.rate"] == "연 2.0%" and b["product.preferential.pref-2.rate"] == "연 0.5%p"
    assert b["product.notice.notice-1"] == "예금자보호법에 따라 보호됩니다"
    assert b["product.summaryItems"] == [{"label": "기본금리", "value": "연 2.0%"}, {"label": "가입기간", "value": "12개월"}]
    types = binding_types(out["prd"])
    assert types["product.summaryItems"] == {"list": {"label": "string", "value": "string"}}
    assert types["product.term"] == "string" and set(types) == set(b)
    from workspace.ontology_ux import BINDING_PATH
    assert all(BINDING_PATH.fullmatch(path) for path in b)
    assert prd_hash(out["prd"]) == schema.digest(out["prd"])


def test_shared_quantity_grammar():
    for text in ("연 2.0%", "999만원", "1억원", "12개월", "-1,000원", "0.5%p", "30bp"):
        assert QUANTITY.search(text), text
    assert is_value("연 0.5%p") and not is_value("12") and not is_value("2.0")


def test_seed_pages_are_the_admitted_fixture():
    from design_fixtures import pages
    assert pages() == PAGES


MALFORMED_PAGES = ([{k: v for k, v in PAGES[0].items() if k != "admissionId"}], [{**PAGES[0], "admissionId": ""}],
                   [{**PAGES[0], "derivativeHash": "nope"}], [{**PAGES[0], "sourceRef": None}],
                   [{**PAGES[0], "text": None}], [{**PAGES[0], "page": 0}], [PAGES[0], PAGES[0]], ["page"], [])


def test_malformed_admission_blocks_before_any_model_call():
    """PR #30 review 1, #3: admission shape is validated before a prompt is built or sent."""
    for pages in MALFORMED_PAGES:
        calls = []
        deps = {"generate": lambda s, u, t: calls.append(u) or json.dumps(GOOD, ensure_ascii=False),
                "normalize": lambda t: calls.append(t)}
        out = extract_prd(pages, deps, model="m")
        assert out["blocked"] == "admission-invalid" and out["prd"] is None, pages
        assert calls == [], pages
