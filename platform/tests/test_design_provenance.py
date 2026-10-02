# platform/tests/test_design_provenance.py
"""Displayed-literal provenance (engine plan E10; PR #30 review 7, #1 follow-up).

Every displayed literal must come from a PRD binding, approved knowledge copy or an engine template. The review
5/6/7 repros (split nodes, Hangul numerals, glued text, 삼일/삼원/영퍼센트) and model-authored text without any
number are rejected for where they came from, not for how a number is spelled."""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge
from design_loop import provenance
from design_loop.composition import validate, walk
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.gui import fill, generate_screen, generate_states
from design_loop.prd_extract import bindings
from design_loop.route import strategy
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
VALUES = bindings(GOOD)
PATHS = frozenset(VALUES)
OK = lambda text: text  # noqa: E731  verify-only normalization fake
CODE = "unapproved-literal-text"


def base(screen="amount"):
    """A valid composition whose every literal is approved copy (asset and screen titles of the seed)."""
    return {"schemaVersion": 1, "screenId": screen, "templateId": "single-task", "state": "default", "variant": "a",
            "surface": "page", "slots": {
                "header": [{"id": "n1", "asset": "title", "props": {"as": "h1", "children": "납입 금액"}}],
                "body": [{"id": "n2", "asset": "amount-field", "props": {"gap": "4"}, "children": [
                    {"id": "n3", "asset": "amount-input", "props": {"label": "금액 입력", "type": "number"}},
                    {"id": "n4", "asset": "body-text", "props": {"children": "본문 텍스트"}}]}],
                "footer": [{"id": "n5", "asset": "cta-next", "children": [
                    {"id": "n6", "asset": "next-button", "props": {"label": "다음 버튼"}, "on": {"click": "next"}}]}]}}


def node(c, node_id):
    return next(n for _, n, _ in walk(c) if n["id"] == node_id)


def findings(c, k=K, **kw):
    kw.setdefault("flow", FLOW)
    kw.setdefault("binding_paths", PATHS)
    return validate(c, k, **kw)


def codes(c, k=K, **kw):
    return {f["code"] for f in findings(c, k, **kw)}


def summary(items):
    c = base("confirm")
    c["slots"]["body"] = [{"id": "n7", "asset": "product-summary", "props": {"title": "상품 요약"},
                           "children": [{"id": "n8", "asset": "rate-summary", "props": {"items": items}}]}]
    return c


# ---- accepted sources ------------------------------------------------------------------------------------------

def test_approved_copy_bindings_and_enum_tokens_are_accepted():
    assert findings(base()) == []
    c = base(); node(c, "n4")["props"] = {}; node(c, "n4")["bind"] = {"children": "product.baseRate"}   # (a)
    assert findings(c) == []
    c = base(); node(c, "n4")["props"]["children"] = "가입 완료"           # (b) another approved screen's title
    assert findings(c) == []
    c = base("confirm")
    c["slots"]["body"] = [{"id": "n7", "asset": "product-summary", "props": {"title": "상품 요약"},
                           "children": [{"id": "n8", "asset": "rate-summary", "bind": {"items": "product.summaryItems"}}]}]
    assert findings(c) == []
    assert {"납입 금액", "다음 버튼", "금액 입력", "상품 요약"} <= provenance.approved_copy(K)


def test_every_seed_screen_and_state_from_the_generator_is_accepted():
    for s in FLOW["screens"]:
        c = fill(K, FLOW, s, VALUES)
        assert findings(c) == [], s
    elig = fill(K, FLOW, "eligibility", VALUES)
    out = generate_states(elig, ["ineligible"], K, None, strategy=strategy("structured"), flow=FLOW,
                          binding_paths=PATHS)
    assert [f for s in out["states"] for f in s["findings"]] == []


def test_state_props_text_of_an_approved_asset_is_approved_copy():
    k = copy.deepcopy(K)
    k.assets["notice-alert"]["stateProps"] = {"error": {"title": "입력값을 확인하세요", "tone": "danger"}}
    k.assets["notice-alert"]["requiredStates"] = ["default", "error"]
    copy_set = provenance.approved_copy(k)
    assert "입력값을 확인하세요" in copy_set and "danger" not in copy_set   # enum tokens are not display copy
    k.assets["notice-alert"]["reviewState"] = "candidate"
    assert "입력값을 확인하세요" not in provenance.approved_copy(k)


def test_reviewed_copy_containing_a_number_is_accepted_as_reviewed():
    """A human approved this exact title at this revision; the engine did not invent it (SPEC 12.4)."""
    k = copy.deepcopy(K)
    k.screens["amount"]["title"] = "연 9.9% 특판 가입"
    k.assets["body-text"]["title"] = "최대 999만원"
    c = base(); node(c, "n1")["props"]["children"] = "연 9.9% 특판 가입"
    node(c, "n4")["props"]["children"] = "최대 999만원"
    assert findings(c, k) == []


# ---- rejected: no approved source ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "매월 납입할 금액을 입력하세요", "다음", "가입하기", "안내",          # model-authored text, no number at all
    "납입 금액 ", " 납입 금액", "납입금액",                              # near-copies of an approved title
    "납입 금액 다음 버튼",                                              # two approved strings joined
])
def test_model_authored_text_without_numbers_is_rejected(text):
    c = base(); node(c, "n4")["props"]["children"] = text
    f = findings(c)
    assert [x["code"] for x in f] == [CODE] and f[0]["severity"] == "critical"
    assert f[0]["path"] == "slots.body[0].children[1].props.children"


@pytest.mark.parametrize("text", [
    "납입 기간 삼일", "추가 한도 삼원", "금리 영퍼센트", "가입금액구백구십구만원",      # review 7 #1 repros
    "한도 구백구십구만원", "구백구십구만원", "삼일", "삼원", "영퍼센트",               # review 6 #1 / variants
    "연 2.0%", "999만원", "1억원", "월 최대 ９９９", "① 우대", "3단계 중 1단계",
])
def test_review_repros_are_rejected_as_unapproved_literal_text(text):
    c = base(); node(c, "n4")["props"]["children"] = text
    assert codes(c) == {CODE}, text
    c = base(); node(c, "n6")["props"]["label"] = text
    assert codes(c) == {CODE}, text
    c = summary([{"label": "상품 요약", "value": text}])
    assert codes(c) == {CODE}, text


def test_split_nodes_are_rejected_per_node():
    """Review 6 #1 exact repro: "추가 한도" and "999" in separate text nodes."""
    c = base(); node(c, "n1")["props"]["children"] = "추가 한도"
    node(c, "n4")["props"]["children"] = "999"
    assert sorted(f["path"] for f in findings(c) if f["code"] == CODE) == \
        ["slots.body[0].children[1].props.children", "slots.header[0].props.children"]


def test_review_5_summary_item_repro_is_rejected():
    c = summary([{"label": "추가 한도 [만원]", "value": "월 최대 999"}])
    assert codes(c) == {CODE}
    c = summary([{"label": "상품 요약", "value": "금리 요약"}])            # both fields approved copy
    assert findings(c) == []


def test_number_literals_need_their_own_reviewed_state_prop():
    k = copy.deepcopy(K)
    k.assets["step-bar"]["code"]["props"]["max"] = {"type": "number", "required": False}
    c = base(); c["slots"]["header"].append({"id": "n9", "asset": "step-bar", "props": {
        "steps": [{"id": "납입 금액", "label": "납입 금액"}], "current": "납입 금액", "max": 3}})
    assert codes(c, k) == {CODE}
    k.assets["step-bar"]["requiredStates"] = ["default", "many"]
    k.assets["step-bar"]["stateProps"] = {"many": {"max": 3}}
    assert findings(c, k) == []
    c["slots"]["header"][1]["props"]["steps"] = [{"id": "s1", "label": "납입 금액"}]   # a list-item id is shown text too
    assert codes(c, k) == {CODE}


def test_unapproved_knowledge_contributes_no_copy():
    k = copy.deepcopy(K)
    k.assets["body-text"]["reviewState"] = "candidate"
    c = base()
    assert CODE in codes(c, k)                                         # "본문 텍스트" lost its approved source
    k = copy.deepcopy(K)
    del k.screens["amount"]["reviewState"]                              # no review state is not approval
    assert any(f["code"] == CODE and f["path"] == "screen.title" for f in findings(base(), k))


def test_enum_props_are_design_tokens_checked_by_type():
    c = base(); node(c, "n1")["props"]["as"] = "999만원"
    assert codes(c) == {"prop-type"}                                   # critical: outside the declared values


# ---- generation, edit, verification and codegen fail closed ---------------------------------------------------

def _model(reply):
    return {"normalize": OK, "generate": lambda system, user, on_token: json.dumps(reply, ensure_ascii=False)}


def test_compose_with_model_authored_text_is_never_accepted():
    k = copy.deepcopy(K)
    k.screens["amount"]["templateId"] = None
    proposal = base(); node(proposal, "n4")["props"]["children"] = "매월 납입할 금액을 입력하세요"
    out = generate_screen("amount", k, FLOW, _model(proposal), strategy=strategy("unstructured"),
                          binding_paths=PATHS, variants=1, retries=1)
    variant = out["variants"][0]
    assert variant["composition"] is None and len(variant["attempts"]) == 2
    assert all(CODE in {f["code"] for f in a["findings"]} for a in variant["attempts"])
    out = generate_screen("amount", k, FLOW, _model(base()), strategy=strategy("unstructured"),
                          binding_paths=PATHS, variants=1, retries=0)
    assert out["variants"][0]["composition"] is not None


def test_the_catalog_tells_the_model_which_copy_is_approved():
    k = copy.deepcopy(K)
    k.screens["amount"]["templateId"] = None
    seen = []

    def generate(system, user, on_token):
        seen.append((system, json.loads(user)))
        return json.dumps(base(), ensure_ascii=False)
    generate_screen("amount", k, FLOW, {"normalize": OK, "generate": generate}, strategy=strategy("unstructured"),
                    binding_paths=PATHS, variants=1, retries=0)
    system, user = seen[0]
    assert user["catalog"]["approvedCopy"] == sorted(provenance.approved_copy(k))
    assert "approvedCopy" in system


def test_edit_proposing_new_copy_is_not_approvable():
    from design_loop.edit import edit
    c = base()
    out = edit(c, "n4", "안내 문구를 짧게", K, _model({"id": "n4", "asset": "body-text",
                                                   "props": {"children": "금액을 입력하세요"}}),
               flow=FLOW, binding_paths=PATHS)
    assert CODE in {f["code"] for f in out["findings"]}
    out = edit(c, "n4", "안내 문구를 짧게", K, _model({"id": "n4", "asset": "body-text",
                                                   "props": {"children": "금액 입력"}}),
               flow=FLOW, binding_paths=PATHS)
    assert out["findings"] == []


@pytest.mark.parametrize("texts", [
    ["추가 한도", "999"], ["납입 기간 삼일"], ["추가 한도 삼원"], ["금리 영퍼센트"], ["가입금액구백구십구만원"],
    ["매월 납입할 금액을 입력하세요"],
])
def test_repros_are_not_approvable_through_verification(texts):
    from design_loop.verify_graph import verify
    from test_design_verify_graph import JUDGE, bundle
    b = bundle()
    page = copy.deepcopy(b["screens"][("amount", "default")])
    for i, text in enumerate(texts):
        page["slots"]["body"].append({"id": f"x{i}", "asset": "body-text", "props": {"children": text}})
    b["screens"][("amount", "default")] = page
    r = verify(b, K, JUDGE)
    assert r["verdict"] == "fail" and not r["approvable"]
    hits = [f for f in r["findings"] if f["code"] == CODE]
    assert len(hits) == len(texts) and all(f["role"] == "reviewer" and f["severity"] == "critical" for f in hits)


def test_an_unapproved_screen_heading_fails_verification_and_codegen():
    from design_loop.react_project import project
    from design_loop.verify_graph import verify
    from test_design_verify_graph import JUDGE, bundle
    k = copy.deepcopy(K)
    k.screens["amount"]["reviewState"] = "candidate"
    r = verify(bundle(), k, JUDGE)
    assert not r["approvable"] and any(f["code"] == CODE and f.get("screen") == "amount" for f in r["findings"])
    screens = {(s, "default"): fill(K, FLOW, s, VALUES) for s in FLOW["screens"]}
    cases = enumerate_cases(expected(GOOD, K)["conditions"])["cases"]
    with pytest.raises(ValueError, match=CODE):
        project(FLOW, screens, k, VALUES, cases=cases)
    bad = dict(screens)
    bad[("amount", "promo")] = {**screens[("amount", "default")], "state": "promo"}   # a state is case-label text
    with pytest.raises(ValueError, match=CODE):
        project(FLOW, bad, K, VALUES, cases=cases)


_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")


def test_every_visible_string_codegen_emits_has_an_approved_source():
    """Scan the generated project: every Hangul or digit-bearing string literal outside the binding data is approved
    copy, an engine template string (or a case label built from them), a PRD binding value or page metadata."""
    from design_loop.react_project import asset_gallery, project
    screens = {(s, "default"): fill(K, FLOW, s, VALUES) for s in FLOW["screens"]}
    screens[("eligibility", "ineligible")] = generate_states(
        screens[("eligibility", "default")], ["ineligible"], K, None, strategy=strategy("structured"), flow=FLOW,
        binding_paths=PATHS)["states"][0]["composition"]
    cases = enumerate_cases(expected(GOOD, K)["conditions"])["cases"]
    files = project(FLOW, screens, K, VALUES, cases=cases)
    copy_set = provenance.approved_copy(K)
    engine = set(provenance.ENGINE_TEXT.values())
    bound = {v for v in VALUES.values() if isinstance(v, str)} | {i[f] for v in VALUES.values() if isinstance(v, list)
                                                                  for i in v for f in i}
    meta = {K.procedures[FLOW["procedureId"]]["title"]}                  # page meta: exported, never rendered
    case_label = re.compile(re.escape(provenance.CASE_PREFIX) + r"[0-9]+" + re.escape(provenance.CASE_SEPARATOR)
                            + r"(?P<title>.+?)" + re.escape(provenance.CASE_SEPARATOR) + r"(?P<state>[a-z]+)"
                            + r"(?:" + re.escape(provenance.CASE_EMPTY_SUFFIX) + r")?\Z")
    for path, source in files.items():
        if path == "src/logic/data.ts":
            continue                                                    # the PRD binding values themselves
        for literal in _STRING.findall(source):
            text = json.loads(literal) if literal.startswith('"') else literal[1:-1]
            if not re.search(r"[가-힣0-9]", text) or text in copy_set | engine | bound | meta:
                continue
            m = case_label.fullmatch(text)
            if m and m["title"] in copy_set:
                continue
            assert re.fullmatch(r"[A-Za-z0-9_.:|/-]*", text), (path, text)   # ids, test ids, versions: not shown text
    gallery = asset_gallery(K, VALUES)
    assert provenance.GALLERY_TITLE in gallery["src/pages/gallery.tsx"]
    assert provenance.MISSING_CONDITION_ERROR in files["src/logic/flow.ts"]
