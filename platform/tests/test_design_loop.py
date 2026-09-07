"""design_loop 오프라인 테스트 — PRD 파생·체크리스트 파생·rule 판정·유계 루프. Bedrock 호출 없음."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from design_loop import MAX_REGENERATIONS, build_checklist, derive_prd, parse_flow, run  # noqa: E402
from design_loop.generate import ParseError, build_prompts  # noqa: E402
from design_loop.models import SpecError  # noqa: E402
from design_loop.rules import run_rule  # noqa: E402

SEED = ROOT / "seed" / "design"
SPECS = {s["id"]: s for s in json.loads((SEED / "product_specs.json").read_text(encoding="utf-8"))}
SM = json.loads((SEED / "sm_model.json").read_text(encoding="utf-8"))
CHECKLISTS = json.loads((SEED / "checklists.json").read_text(encoding="utf-8"))
SOCCER, LUMP = SPECS["ps-soccer-club-savings"], SPECS["ps-lumpsum-savings"]


# ---------- 가짜 생성기: PRD 를 그대로 충실히 렌더링 (통과용) / 스텝을 빼먹는 변종 (실패용) ----------
def _page(step: dict, spec: dict, prd: dict, extra: str = "") -> str:
    req = "".join(f"<p>{r}</p>" for r in step.get("required") or [])
    btns = "".join(f"<button type=\"button\">{t['trigger']}</button>" + (f"<span>{t['when']}</span>" if t.get("when") else "")
                   for t in prd["transitions"] if t["from"] == step["id"])
    inputs = ""
    if step["id"].startswith("evidence-"):
        inputs = "<label for=\"ev\">회원번호</label><input id=\"ev\" type=\"text\">"
    if step["id"] == "amount":
        inputs = "<label for=\"amt\">월 납입액</label><input id=\"amt\" type=\"number\">"
    partner = "".join(f"<p>제휴: {p['name']}</p>" for p in spec.get("partners") or []) if step["id"] == "intro" else ""
    period = f"<p>이벤트 기간 {spec.get('eventPeriod')}</p>" if step["id"] == "intro" and spec.get("eventPeriod") else ""
    return (f"<!doctype html><html lang=\"ko\"><head><title>{step['title']}</title></head><body><h1>{step['title']}</h1>"
            f"{req}{inputs}{partner}{period}<p>심의필 제2026-000호</p>{extra}{btns}</body></html>")


def faithful_generator(spec, prd, drop: set | None = None):
    def gen(system: str, user: str, on_token) -> str:
        out = []
        for s in prd["steps"]:
            if drop and s["id"] in drop:
                continue
            out.append(f"<<<STEP id=\"{s['id']}\" title=\"{s['title']}\">>>\n{_page(s, spec, prd)}\n<<<END>>>")
        out.append("<<<FLOW>>>\n" + json.dumps({"transitions": prd["transitions"]}, ensure_ascii=False) + "\n<<<END>>>")
        text = "\n".join(out)
        on_token(text[:40])
        return text
    return gen


# ---------- PRD ----------
def test_prd_adds_evidence_step_for_input_condition():
    prd = derive_prd(SOCCER, SM)
    ids = [s["id"] for s in prd["steps"]]
    assert "evidence-soccer-club" in ids and prd["branchSteps"] == ["evidence-soccer-club"]
    assert ids.index("evidence-soccer-club") == ids.index("preferential") + 1
    ev = next(s for s in prd["steps"] if s["id"] == "evidence-soccer-club")
    assert ev["branch"]["after"] == "preferential" and "축구클럽 회원번호" in ev["required"]
    trs = {(t["from"], t["to"]) for t in prd["transitions"]}
    assert ("preferential", "evidence-soccer-club") in trs and ("evidence-soccer-club", "confirm") in trs
    assert ("preferential", "confirm") in trs  # 조건 미선택 시 직행 경로도 남는다


def test_prd_no_evidence_step_when_conditions_are_auto():
    prd = derive_prd(LUMP, SM)
    assert prd["branchSteps"] == [] and len(prd["steps"]) == 7
    terms = next(s for s in prd["steps"] if s["id"] == "terms")
    assert any("예금자보호" in r for r in terms["required"])


def test_prd_rejects_incomplete_spec():
    with pytest.raises(SpecError) as ei:
        derive_prd({"productName": "x"}, SM)
    assert "baseRate" in ei.value.missing


# ---------- 체크리스트 ----------
def test_checklist_sports_product_gets_extra_set_and_derived_items():
    items = build_checklist(SOCCER, CHECKLISTS)
    sets = {i["set"] for i in items}
    assert {"cl-deposit-base", "cl-sports-partner", "product-spec"} <= sets
    derived = [i for i in items if i["source"] == "derived"]
    assert any(i["id"] == "d-step-soccer-club" and i["rule"]["fn"] == "step_exists" for i in derived)
    assert sum(1 for i in items if i["set"] == "cl-sports-partner") == 6
    # $spec 바인딩
    sp01 = next(i for i in items if i["id"] == "sp01")
    assert sp01["rule"]["args"]["texts"] == ["아톰 FC"]


def test_checklist_plain_product_has_base_only():
    items = build_checklist(LUMP, CHECKLISTS)
    assert not any(i["set"] == "cl-sports-partner" for i in items)
    assert not any(i["id"].startswith("d-step-") for i in items)
    assert any(i["id"].startswith("d-notice-") for i in items)


# ---------- rule 판정 ----------
def test_rules_detect_missing_step_and_button_and_text():
    prd = derive_prd(SOCCER, SM)
    flow = parse_flow(faithful_generator(SOCCER, prd, drop={"evidence-soccer-club"})("", "", lambda t: None))
    assert run_rule(flow, prd, {"fn": "step_exists", "args": {"stepId": "evidence-soccer-club"}})["verdict"] == "fail"
    assert run_rule(flow, prd, {"fn": "steps_exist", "args": {}})["verdict"] == "fail"
    assert run_rule(flow, prd, {"fn": "text_present", "args": {"texts": ["없는문구"], "steps": ["intro"]}})["verdict"] == "fail"
    good = parse_flow(faithful_generator(SOCCER, prd)("", "", lambda t: None))
    assert run_rule(good, prd, {"fn": "transitions_match_buttons", "args": {}})["verdict"] == "pass"
    assert run_rule(good, prd, {"fn": "required_present", "args": {}})["verdict"] == "pass"
    assert run_rule(good, prd, {"fn": "a11y_labels", "args": {}})["verdict"] == "pass"
    # 라벨 없는 입력은 잡힌다
    bad = {"steps": [{"id": "amount", "html": "<html lang='ko'><h1>x</h1><input type='text'></html>"}]}
    assert run_rule(bad, prd, {"fn": "a11y_labels", "args": {}})["verdict"] == "fail"
    assert run_rule(bad, prd, {"fn": "nope", "args": {}})["verdict"] == "incomplete"


def test_parse_flow_rejects_missing_blocks():
    with pytest.raises(ParseError):
        parse_flow("안녕하세요 화면입니다")


# ---------- 루프 ----------
def test_loop_passes_first_attempt_with_faithful_generator():
    prd = derive_prd(SOCCER, SM)
    events = []
    res = run(SOCCER, SM, CHECKLISTS, {"generate": faithful_generator(SOCCER, prd)}, emit=events.append)
    assert res["ok"] is True and res["attempts"] == 1 and res["regenerated"] is False
    rep = res["report"]
    assert rep["score"]["fail"] == 0 and rep["openItems"] == []
    # llm 항목은 판정기 미연결 → 미판정으로 정직하게 남는다
    assert rep["score"]["incomplete"] == sum(1 for i in res["checklist"] if i["method"] == "llm")
    assert [e["step"] for e in events if e["type"] == "stage"] == ["prd", "prd", "checklist", "generate", "generate", "review", "review", "report"]
    assert any(e["type"] == "token" for e in events)


def test_loop_regenerates_once_then_stops_with_open_items():
    prd = derive_prd(SOCCER, SM)
    calls = {"n": 0}
    inner = faithful_generator(SOCCER, prd, drop={"evidence-soccer-club"})

    def always_missing(system, user, on_token):
        calls["n"] += 1
        if calls["n"] == 2:
            assert "검수 실패 항목" in user and "d-step-soccer-club" in user
        return inner(system, user, on_token)

    events = []
    res = run(SOCCER, SM, CHECKLISTS, {"generate": always_missing}, emit=events.append)
    assert calls["n"] == MAX_REGENERATIONS + 1 == 2, "재생성은 정확히 1회"
    assert res["ok"] is False and res["attempts"] == 2 and res["regenerated"] is True
    assert "d-step-soccer-club" in res["report"]["openItems"]
    regen = [e for e in events if e["type"] == "stage" and e["step"] == "regenerate"]
    assert len(regen) == 1 and regen[0]["limit"] == 1
    assert len(res["report"]["history"]) == 2


def test_loop_fixed_on_second_attempt():
    prd = derive_prd(SOCCER, SM)
    bad, good = faithful_generator(SOCCER, prd, drop={"evidence-soccer-club"}), faithful_generator(SOCCER, prd)
    calls = {"n": 0}

    def gen(system, user, on_token):
        calls["n"] += 1
        return (bad if calls["n"] == 1 else good)(system, user, on_token)

    res = run(SOCCER, SM, CHECKLISTS, {"generate": gen})
    assert res["ok"] is True and res["attempts"] == 2 and res["report"]["history"][0]["failed"]


def test_loop_uses_llm_judge_and_test_gate():
    prd = derive_prd(LUMP, SM)
    judged = []

    def judge(item, ctx):
        judged.append(item["id"])
        assert "flowText" in ctx and "[intro]" in ctx["flowText"]
        return {"verdict": "pass", "evidence": "ok"}

    def gate(flow):
        return [{"id": "axe", "text": "axe-core", "verdict": "pass", "evidence": "0 violations"}]

    res = run(LUMP, SM, CHECKLISTS, {"generate": faithful_generator(LUMP, prd), "llm_judge": judge, "test_gate": gate})
    assert set(judged) == {"b11", "b12"}
    assert any(i["id"] == "axe" and i["set"] == "test-agent" for i in res["report"]["items"])
    assert res["report"]["score"]["incomplete"] == 0


def test_loop_parse_failure_reparses_once_then_errors():
    prd = derive_prd(LUMP, SM)
    calls = {"n": 0}

    def gen(system, user, on_token):
        calls["n"] += 1
        return "형식 무시"

    res = run(LUMP, SM, CHECKLISTS, {"generate": gen})
    assert res.get("code") == "parse-failed" and calls["n"] == 2 and res["attempts"] == 1


def test_prompts_include_required_texts_and_failures():
    prd = derive_prd(SOCCER, SM)
    system, user = build_prompts(prd, SM, SOCCER, failures=[{"id": "x", "text": "t", "evidence": "e"}], previous={"steps": prd["steps"]})
    assert "<<<STEP" in system and "축구클럽 회원번호" in user and "검수 실패 항목" in user and "구조는 유지" in user
