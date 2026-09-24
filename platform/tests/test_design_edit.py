# platform/tests/test_design_edit.py
"""Task E14: targeted edit with complete-document and layout-box stability (G-04; Codex #12)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import knowledge
from design_loop.composition import walk
from design_loop.convention import Registry
from design_loop.edit import apply_edit, edit, layout_stable, locate, stability, subtree_testids
from design_loop.flow import build_flow
from design_loop.gui import fill
from design_loop.prd_extract import bindings
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
PATHS = frozenset(bindings(GOOD))
OK = lambda text: text  # noqa: E731  verify-only normalization fake


def base():
    return fill(K, FLOW, "amount", bindings(GOOD))          # n1 title | n2 amount-field(n3 input, n4 text) | n5 cta(n6)


def node(c, node_id):
    return next(n for _, n, _ in walk(c) if n["id"] == node_id)


def test_locate_and_apply():
    c = base()
    assert locate(c, "n4") == ("body", [0, 1]) and locate(c, "n6") == ("footer", [0, 0])
    with pytest.raises(KeyError):
        locate(c, "ghost")
    changed = apply_edit(c, "n4", {"id": "n4", "asset": "body-text", "props": {"children": "새 안내"}})
    assert node(changed, "n4")["props"]["children"] == "새 안내" and node(c, "n4")["props"]["children"] != "새 안내"
    with pytest.raises(ValueError, match="target-id-changed"):
        apply_edit(c, "n4", {"id": "n9", "asset": "body-text"})


def test_template_change_is_unstable():
    before = base()
    after = apply_edit(before, "n4", {**node(before, "n4"), "props": {"children": "바뀐 문구"}})
    assert stability(before, after, "n4") == {"stable": True, "changes": []}
    after["templateId"] = "other-template"
    out = stability(before, after, "n4")
    assert not out["stable"] and "templateId" in out["changes"]


def test_surface_state_and_variant_changes_are_unstable():
    before = base()
    for field, value in (("surface", "bottom-sheet"), ("state", "error"), ("variant", "b")):
        after = copy.deepcopy(before)
        after[field] = value
        out = stability(before, after, "n4")
        assert not out["stable"] and out["changes"] == [field]


def test_deleting_the_target_is_target_missing():
    before = base()
    after = copy.deepcopy(before)
    after["slots"]["body"][0]["children"].pop(1)
    out = stability(before, after, "n4")
    assert not out["stable"] and "target-missing" in out["changes"]


def test_moving_the_target_to_another_slot_is_target_moved():
    before = base()
    after = copy.deepcopy(before)
    moved = after["slots"]["body"][0]["children"].pop(1)
    after["slots"]["header"].append(moved)
    out = stability(before, after, "n4")
    assert not out["stable"] and "target-moved" in out["changes"]
    reordered = copy.deepcopy(before)                             # same slot, other index is also a move
    reordered["slots"]["body"][0]["children"].reverse()
    assert "target-moved" in stability(before, reordered, "n4")["changes"]


def test_outside_changes_are_listed_by_path_and_target_subtree_changes_are_not():
    before = base()
    after = copy.deepcopy(before)
    node(after, "n1")["props"]["children"] = "다른 제목"
    node(after, "n6")["on"] = {"click": "finish"}
    node(after, "n2").setdefault("props", {})["gap"] = "2"
    out = stability(before, after, "n4")
    assert not out["stable"]
    assert set(out["changes"]) == {"slots.header[0].props.children", "slots.footer[0].children[0].on.click",
                                   "slots.body[0].props"}
    inner = copy.deepcopy(before)
    node(inner, "n2")["children"][0]["props"]["label"] = "새 금액 이름"   # inside the target subtree n2
    assert stability(before, inner, "n2") == {"stable": True, "changes": []}


def gen(reply):
    return {"normalize": OK, "generate": lambda system, user, on_token: json.dumps(reply, ensure_ascii=False)}


def test_edit_replaces_only_the_target_and_validates():
    c = base()
    out = edit(c, "n4", "안내 문구를 짧게", K, gen({"id": "n4", "asset": "body-text", "props": {"children": "금액 입력"}}),
               flow=FLOW, binding_paths=PATHS)
    assert out["findings"] == [] and out["stability"]["stable"]
    assert node(out["composition"], "n4")["props"]["children"] == "금액 입력"


def test_edit_id_change_from_the_model_is_target_id_changed():
    out = edit(base(), "n4", "문구 수정", K, gen({"id": "n99", "asset": "body-text", "props": {"children": "x"}}),
               flow=FLOW, binding_paths=PATHS)
    assert "target-id-changed" in {f["code"] for f in out["findings"]} and out["composition"] is None
    assert out["stability"] == {"stable": False, "changes": ["target-id-changed"]}


def test_edit_findings_block_literal_values_and_missing_models():
    out = edit(base(), "n4", "금리 표시", K, gen({"id": "n4", "asset": "body-text", "props": {"children": "연 2.0%"}}),
               flow=FLOW, binding_paths=PATHS)
    assert "literal-financial-value" in {f["code"] for f in out["findings"]}
    assert edit(base(), "n4", "x", K, {"normalize": OK}, flow=FLOW, binding_paths=PATHS)["blocked"] == "model-unavailable"
    seen = []
    blocked = edit(base(), "n4", "x", K, {"generate": lambda *a: seen.append(a)}, flow=FLOW, binding_paths=PATHS)
    assert blocked["blocked"] == "normalization-unavailable" and seen == []
    with pytest.raises(KeyError):
        edit(base(), "ghost", "x", K, gen({}), flow=FLOW, binding_paths=PATHS)


def boxes(**overrides):
    value = {"k5-n1": [16, 16, 358, 40], "k5-n2": [16, 70, 358, 120], "k5-f1": [16, 80, 358, 44],
             "k5-n4": [16, 140, 358, 24], "k5-cta": [16, 200, 358, 44]}
    value.update(overrides)
    return value


def test_layout_stability_over_checkpoints():
    c = base()
    registry = Registry(K)
    registry.register_flow(FLOW)
    assert registry.page_key("amount") == "k5"                   # flow order: intro, eligibility, terms, ineligible, amount
    excluded = subtree_testids(c, "n2", registry)
    ids = registry.test_ids("amount", c)["byNode"]
    assert excluded == {ids["n2"], ids["n3"], ids["n4"]}
    before = {"R1:1": boxes(), "R1:3": boxes()}
    assert layout_stable(before, copy.deepcopy(before), excluded_testids=excluded) == \
        {"stable": True, "moved": [], "missing": [], "blocked": []}
    shifted = {"R1:1": boxes(**{"k5-cta": [16, 205, 358, 44]}), "R1:3": boxes()}
    out = layout_stable(before, shifted, excluded_testids=excluded)
    assert not out["stable"] and out["moved"] == [{"checkpoint": "R1:1", "testId": "k5-cta"}]
    gone = {"R1:1": {k: v for k, v in boxes().items() if k != "k5-n1"}, "R1:3": boxes()}
    assert layout_stable(before, gone, excluded_testids=excluded)["missing"] == [{"checkpoint": "R1:1", "testId": "k5-n1"}]
    assert layout_stable(before, {"R1:1": boxes()}, excluded_testids=excluded)["blocked"] == ["R1:3"]
    inner = {"R1:1": boxes(**{"k5-f1": [16, 90, 300, 44], "k5-n4": [20, 160, 358, 40]}), "R1:3": boxes()}
    assert layout_stable(before, inner, excluded_testids=excluded)["stable"]      # descendants of the target
    within = {"R1:1": boxes(**{"k5-cta": [17, 201, 358, 44]}), "R1:3": boxes()}
    assert layout_stable(before, within, excluded_testids=excluded)["stable"]    # 1px tolerance
    assert layout_stable({}, {}, excluded_testids=excluded)["stable"] is False   # nothing compared fails closed


def test_edit_module_imports_only_stdlib_and_pure_schema():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for item in ast.walk(ast.parse((root / "edit.py").read_text(encoding="utf-8"))):
        if isinstance(item, ast.ImportFrom) and item.level == 0:
            assert item.module in {"workspace.ontology_schema", "workspace.ontology_ux"} \
                or item.module.split(".")[0] in sys.stdlib_module_names, item.module
        elif isinstance(item, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names)
