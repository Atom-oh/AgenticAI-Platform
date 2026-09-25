# platform/tests/test_design_composition.py
import copy
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge
from design_loop import composition
from design_loop.composition import canonical, digest, text_of, validate, visible, walk
from design_loop.flow import build_flow
from design_loop.prd_extract import binding_types, bindings
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
PATHS = frozenset(bindings(GOOD))


def base(screen="amount"):
    """A valid fill composition for `amount`."""
    return {"schemaVersion": 1, "screenId": screen, "templateId": "single-task", "state": "default", "variant": "a",
            "surface": "page", "slots": {
                "header": [{"id": "n1", "asset": "title", "props": {"as": "h1", "children": "납입 금액"}}],
                "body": [{"id": "n2", "asset": "amount-field", "props": {"gap": "4"}, "children": [
                    {"id": "n3", "asset": "amount-input", "props": {"label": "월 납입 금액", "type": "number"}},
                    {"id": "n4", "asset": "body-text", "props": {"children": "매월 납입할 금액을 입력하세요"}}]}],
                "footer": [{"id": "n5", "asset": "cta-next", "children": [
                    {"id": "n6", "asset": "next-button", "props": {"label": "다음"}, "on": {"click": "next"}}]}]}}


def codes(c, **kw):
    kw.setdefault("flow", FLOW)
    kw.setdefault("binding_paths", PATHS)
    return {f["code"] for f in validate(c, K, **kw)}


def node(c, node_id):
    return next(n for _, n, _ in walk(c) if n["id"] == node_id)


def test_valid_fill_composition_has_no_findings():
    assert validate(base(), K, flow=FLOW, binding_paths=PATHS) == []
    assert validate(base(), K, flow=FLOW, binding_paths=binding_types(GOOD)) == []   # typed catalog form


def test_shape():
    c = base(); c["extra"] = 1
    assert codes(c) == {"shape"}
    c = base(); c["schemaVersion"] = 2
    assert codes(c) == {"shape"}
    c = base(); node(c, "n1")["style"] = {}
    assert codes(c) == {"shape"}


def test_tree_limits_ids_and_duplicates():
    c = base()
    leaf = node(c, "n4")
    cursor = leaf
    for i in range(5):                                       # n2 depth 1, n4 depth 2 -> depth 7
        cursor["children"] = [{"id": f"d{i}", "asset": "body-text"}]
        cursor = cursor["children"][0]
    assert "tree-limit" in codes(c)
    c = base(); c["slots"]["body"] += [{"id": f"x{i}", "asset": "body-text", "props": {"children": "안내"}} for i in range(200)]
    assert "tree-limit" in codes(c)
    c = base(); node(c, "n4")["id"] = "4-bad"
    assert codes(c) == {"bad-id"}
    c = base(); node(c, "n4")["id"] = "n3"
    assert codes(c) == {"duplicate-id"}


def test_template_checks():
    c = base(); c["templateId"] = "ghost"
    assert "unknown-template" in codes(c)
    k = copy.deepcopy(K); k.templates["other"] = {**k.templates["single-task"], "id": "other"}
    c = base(); c["templateId"] = "other"
    assert {f["code"] for f in validate(c, k, flow=FLOW, binding_paths=PATHS)} == {"template-mismatch"}


def test_slot_checks():
    c = base(); c["slots"]["aside"] = []
    assert codes(c) == {"unknown-slot"}
    c = base(); del c["slots"]["footer"]
    assert codes(c) == {"missing-slot"}
    c = base(); c["slots"]["body"] = []
    assert codes(c) == {"slot-empty"}


def test_allow_list_and_composition():
    c = base(); c["slots"]["body"].append({"id": "n9", "asset": "next-button", "props": {"label": "다음"}})
    assert codes(c) == {"not-allowed"}
    c = base(); node(c, "n2")["children"].append({"id": "n9", "asset": "next-button", "props": {"label": "다음"}})
    assert codes(c) == {"not-composed"}


def test_unknown_asset_and_compose_candidate():
    c = base(); c["slots"]["body"].append({"id": "n9", "asset": "hero-banner"})
    assert "unknown-asset" in codes(c)
    found = [f for f in validate(c, K, flow=FLOW, binding_paths=PATHS, mode="compose") if f["code"] == "new-asset-candidate"]
    assert found and found[0]["severity"] == "major" and found[0]["approvable"] is False
    assert not [f for f in validate(c, K, flow=FLOW, binding_paths=PATHS, mode="compose") if f["code"] == "unknown-asset"]


def test_prop_checks():
    c = base(); node(c, "n1")["props"]["color"] = "red"
    assert codes(c) == {"unknown-prop"}
    c = base(); node(c, "n1")["props"]["as"] = "h7"
    assert codes(c) == {"prop-type"}
    c = base(); del node(c, "n6")["props"]["label"]
    assert codes(c) == {"prop-required"}
    c = base(); node(c, "n6")["bind"] = {"label": "product.productName"}
    assert codes(c) == {"prop-conflict"}
    c = base(); node(c, "n6")["props"]["onClick"] = "alert(1)"
    assert codes(c) == {"callback-literal"}
    c = base(); node(c, "n3")["props"]["value"] = "1000"
    assert codes(c) == {"prop-conflict"}                     # adapter-owned value


def test_binding_checks():
    c = base(); node(c, "n4")["props"] = {}; node(c, "n4")["bind"] = {"children": "product.unknown"}
    assert codes(c) == {"unknown-binding"}
    c = base(); node(c, "n4")["props"] = {}; node(c, "n4")["bind"] = {"children": "product.notice.notice-9"}
    assert codes(c) == {"unknown-binding"}                   # well-formed but absent from this PRD
    c = base(); node(c, "n4")["props"] = {}; node(c, "n4")["bind"] = {"children": "product.summaryItems"}
    assert codes(c) == {"binding-type"}
    c = base(); node(c, "n4")["props"] = {}; node(c, "n4")["bind"] = {"children": "product.baseRate"}
    assert validate(c, K, flow=FLOW, binding_paths=PATHS) == []


def summary(screen="confirm", items=None, bound=False):
    c = base(screen)
    rate = {"id": "n8", "asset": "rate-summary"}
    if bound:
        rate["bind"] = {"items": "product.summaryItems"}
    else:
        rate["props"] = {"items": items}
    c["slots"]["body"] = [{"id": "n7", "asset": "product-summary", "props": {"title": "상품 요약"}, "children": [rate]}]
    return c


@pytest.mark.parametrize("literal", ["연 2.0%", "999만원", "1억원"])
def test_literal_financial_values_are_critical(literal):
    c = base(); node(c, "n4")["props"]["children"] = f"금리는 {literal}입니다"
    found = [f for f in validate(c, K, flow=FLOW, binding_paths=PATHS) if f["code"] == "literal-financial-value"]
    assert found and found[0]["severity"] == "critical"
    c = base(); node(c, "n6")["props"]["label"] = literal
    assert "literal-financial-value" in codes(c)
    c = summary(items=[{"label": "기본금리", "value": literal}])
    assert codes(c) == {"literal-financial-value"}


def test_split_label_and_value_financial_literal_is_still_critical():
    """PR #30 review round 2, #4: a Summary item split into a unit-bearing label ("...(만원)") and a separate
    bare numeric value bypasses the single-string quantity regex; the combination must still be flagged."""
    c = summary(items=[{"label": "추가 한도 (만원)", "value": "999"}])
    assert codes(c) == {"literal-financial-value"}
    c = summary(items=[{"label": "만기 (개월)", "value": "12"}])
    assert codes(c) == {"literal-financial-value"}
    # A bare number with no sibling unit annotation, and ordinary text with no sibling number, both stay clean.
    c = summary(items=[{"label": "가입자 수", "value": "999"}])
    assert codes(c) == set()
    c = summary(items=[{"label": "회원 등급 안내", "value": "일반"}])
    assert codes(c) == set()


def test_the_same_values_bound_from_the_cited_prd_pass():
    assert validate(summary(bound=True), K, flow=FLOW, binding_paths=PATHS) == []
    c = base(); node(c, "n4")["props"] = {}; node(c, "n4")["bind"] = {"children": "product.baseRate"}
    assert validate(c, K, flow=FLOW, binding_paths=PATHS) == []
    values = bindings(GOOD)
    assert "연 2.0%" in text_of(summary(bound=True), values) and "12개월" in text_of(summary(bound=True), values)


def test_transitions():
    c = base(); node(c, "n4")["on"] = {"click": "next"}
    assert codes(c) == {"unknown-transition"}                # body-text has no action adapter
    c = base(); node(c, "n6")["on"] = {"click": "finish"}
    assert codes(c) == {"unknown-transition"}                # amount is not terminal
    c = base(); node(c, "n6")["on"] = {"click": "n09-confirm-done"}
    assert codes(c) == {"unknown-transition"}                # does not leave this screen
    c = base(); node(c, "n6")["on"] = {"click": "n05-amount-preferential"}
    assert codes(c) == set()
    assert codes(base(), flow=None) == {"unknown-transition"}  # unresolvable without a flow (fail closed)
    done = summary("done", bound=True); node(done, "n6")["on"] = {"click": "finish"}
    assert validate(done, K, flow=FLOW, binding_paths=PATHS) == []
    node(done, "n6")["on"] = {"click": "next"}
    assert codes(done) == {"unknown-transition"}             # no outgoing transition from a terminal


def test_state_and_surface():
    c = base(); c["state"] = "sleeping"
    assert codes(c) == {"bad-state"}
    c = base(); c["surface"] = "window"
    assert codes(c) == {"bad-surface"}


def evidence(with_branch=True, spec=None):
    c = base("evidence-auto")
    c["slots"]["body"] = [{"id": "n7", "asset": "auto-transfer", "props": {"title": "자동이체 설정"}}]
    if with_branch:
        branch = {"id": "n8", "asset": "branch-select", "props": {"title": "출금 계좌 선택"}}
        if spec is not False:
            branch["visibleWhen"] = spec or {"when": "cond:autoTransfer", "negate": False}
        c["slots"]["body"].append(branch)
    return c


def test_visibility_is_derived_from_applicable_conditions():
    assert validate(evidence(), K, flow=FLOW, binding_paths=PATHS) == []
    assert codes(evidence(spec=False)) == {"visibility-missing"}
    assert codes(evidence(spec={"when": "cond:eligible", "negate": False})) == {"visibility-unbound"}
    assert codes(evidence(spec={"when": "cond:autoTransfer", "negate": True})) == {"visibility-unbound"}
    c = evidence(); node(c, "n1")["visibleWhen"] = {"when": "cond:autoTransfer", "negate": False}
    assert codes(c) == {"visibility-unbound"}                # title is not a condition target
    shown = visible(evidence(), {"autoTransfer": True, "eligible": True})
    hidden = visible(evidence(), {"autoTransfer": False, "eligible": True})
    assert [n["id"] for n in shown["slots"]["body"]] == ["n7", "n8"] and [n["id"] for n in hidden["slots"]["body"]] == ["n7"]


def test_exclude_negates_the_whole_expression():
    k = copy.deepcopy(K)
    k.assets["auto-transfer"]["conditions"] = [{"id": "c-x", "when": "cond:a & cond:b", "effect": "exclude",
                                                "target": "branch-select"}]
    c = evidence(spec={"when": "cond:a & cond:b", "negate": True})
    assert validate(c, k, flow=FLOW, binding_paths=PATHS) == []
    for a, b in itertools.product((True, False), repeat=2):
        ids = [n["id"] for n in visible(c, {"a": a, "b": b})["slots"]["body"]]
        assert ("n8" in ids) == (not (a and b))


def test_adapt_scope():
    fill = evidence(with_branch=False)
    adapted = evidence()
    assert validate(adapted, K, flow=FLOW, binding_paths=PATHS, mode="adapt", base=fill) == []
    changed = copy.deepcopy(adapted); node(changed, "n7")["props"]["title"] = "다른 제목"
    assert codes(changed, mode="adapt", base=fill) == {"adapt-scope"}
    added = copy.deepcopy(fill); added["slots"]["body"].append({"id": "n9", "asset": "body-text", "props": {"children": "추가"}})
    assert codes(added, mode="adapt", base=fill) == {"adapt-scope"}      # slot-allowed but not condition-governed
    assert codes(adapted, mode="adapt") == {"adapt-scope"}                # no base (fail closed)


def test_bounded_serializer_hashes_the_deepest_accepted_tree():
    c = base()
    cursor = node(c, "n4")
    for i in range(4):                                        # n4 is depth 2 -> deepest node at depth 6
        cursor["children"] = [{"id": f"d{i}", "asset": "body-text", "props": {"children": "안내"}}]
        cursor = cursor["children"][0]
    assert "tree-limit" not in codes(c)
    assert len(digest(c)) == 64 and canonical(c) == canonical(copy.deepcopy(c))
    assert digest(c) != digest(base())
    with pytest.raises(ValueError):
        canonical({"x": float("nan")})
    deep = {}
    cursor = deep
    for _ in range(40):
        cursor["a"] = {}
        cursor = cursor["a"]
    with pytest.raises(ValueError):
        digest(deep)


def test_walk_and_text_of():
    paths = [p for p, _, _ in walk(base())]
    assert paths[:3] == ["slots.header[0]", "slots.body[0]", "slots.body[0].children[0]"]
    text = text_of(base(), bindings(GOOD), K)
    assert "납입 금액" in text and "h1" not in text and "월 납입 금액" in text
    assert composition.text_of(base())                        # verify_graph calls it with the page only


def test_engine_modules_import_only_stdlib_and_pure_schema():
    """Global Constraints: design_loop engine modules import only stdlib and workspace.ontology_schema/_ux."""
    import ast
    allowed_pkgs = {"workspace.ontology_schema", "workspace.ontology_ux"}
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for name in ("guide_rules", "prd_extract", "financial", "flow", "route", "composition", "model_call"):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        for item in ast.walk(tree):
            if isinstance(item, ast.ImportFrom) and item.level == 0:
                mod = item.module
                if mod == "workspace":
                    assert {a.name for a in item.names} <= {"ontology_schema", "ontology_ux"}, name
                    continue
                assert mod in allowed_pkgs or mod.split(".")[0] in sys.stdlib_module_names, (name, mod)
            elif isinstance(item, ast.Import):
                assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names), name
