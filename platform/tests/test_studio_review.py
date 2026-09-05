# platform/tests/test_studio_review.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import review  # noqa: E402

SECTIONS = [
    '<section data-step="1" data-screen="SCR-DEP-001"><h1>아톰 축구사랑 적금 상품안내</h1><p>기본금리 연 3.0%, 적금 우대금리 최대 +1.2%p</p><button>다음</button></section>',
    '<section data-step="2"><h2>가입기간 선택</h2><label>가입기간</label><select><option>6개월</option></select></section>',
    '<section data-step="3"><h2>월 납입금액 입력</h2><input type="number" placeholder="1만원 이상 50만원 이하"></section>',
    '<section data-step="4"><h2>축구클럽 인증</h2><input type="text" placeholder="구단 회원번호"><button>인증</button></section>',
    '<section data-step="5"><h2>납입 방식</h2><label><input type="radio">자동이체</label></section>',
    '<section data-step="6"><h2>약관 동의</h2><label><input type="checkbox">전체 동의</label></section>',
    '<section data-step="7"><h2>가입 완료</h2><p>만기 예상 이자</p></section>',
]


def wrap(sections):
    return "<html><head><style>.cta{background:#008485}</style></head><body>" + "\n".join(sections) + "</body></html>"


FLOW_HTML = wrap(SECTIONS)

BAD_HTML = """<html><body><script src="https://cdn.example.com/x.js"></script>
<section data-step="1"><h1>적금</h1><button onclick="fetch('/x')">확인</button></section></body></html>"""

ITEMS = [
    {"id": "FLOW-COND", "check": "dom", "required": True, "weight": 3, "expect": {"stepContainsAny": ["축구클럽 회원 인증 시 우대금리 +1.0%p", "축구클럽"]}},
    {"id": "STEP-ORDER", "check": "dom", "required": True, "weight": 3, "expect": {"stepOrder": ["상품안내", "기간 선택", "금액 입력", "축구클럽 인증", "납입 방식", "약관 동의", "가입 완료"]}},
    {"id": "STEP-MIN", "check": "dom", "required": True, "weight": 2, "expect": {"minSteps": 7}},
    {"id": "POL-1", "check": "dom", "required": True, "weight": 3, "expect": {"control": "checkbox"}},
    {"id": "CM-03", "check": "dom", "required": False, "weight": 1, "expect": {"brandHex": ["#008485", "#00615f"]}},
    {"id": "CM-04", "check": "dom", "required": True, "weight": 2, "expect": {"noExternal": True}},
    {"id": "TERM-1", "check": "text", "required": False, "weight": 1, "expect": {"contains": "적금 우대금리"}},
    {"id": "TERM-2", "check": "text", "required": False, "weight": 1, "expect": {"contains": "만기 예상 이자"}},
    {"id": "LLM-1", "check": "llm", "required": True, "weight": 3},
    {"id": "LLM-2", "check": "llm", "required": False, "weight": 2},
]


def test_parse_html_steps_and_signals():
    doc = review.parse_html(FLOW_HTML)
    assert len(doc["steps"]) == 7 and doc["steps"][0]["screen"] == "SCR-DEP-001"
    assert doc["steps"][3]["headings"] == ["축구클럽 인증"] and doc["checkboxes"] == 1
    assert doc["externalScripts"] == 0 and doc["hasFetch"] is False
    bad = review.parse_html(BAD_HTML)
    assert bad["externalScripts"] == 1 and bad["hasFetch"] is True
    assert len(review.parse_html("<p>단일</p>")["steps"]) == 1


def test_deterministic_checks_pass_on_good_flow():
    v = review.deterministic_checks(ITEMS, review.parse_html(FLOW_HTML))
    assert set(v) == {"FLOW-COND", "STEP-ORDER", "STEP-MIN", "POL-1", "CM-03", "CM-04", "TERM-1", "TERM-2"}
    assert all(x["verdict"] == "pass" for x in v.values()), {k: x for k, x in v.items() if x["verdict"] != "pass"}
    assert "4단계" in v["FLOW-COND"]["evidence"] or "축구클럽" in v["FLOW-COND"]["evidence"]


def test_deterministic_checks_fail_and_fix_text():
    v = review.deterministic_checks(ITEMS, review.parse_html(BAD_HTML))
    assert v["FLOW-COND"]["verdict"] == "fail" and "축구클럽" in v["FLOW-COND"]["fix"]
    assert v["STEP-MIN"]["verdict"] == "fail" and v["CM-04"]["verdict"] == "fail" and v["POL-1"]["verdict"] == "fail"
    assert v["TERM-1"]["verdict"] == "fail"
    nocond = review.deterministic_checks([{"id": "FLOW-NOCOND", "check": "dom", "expect": {"maxSteps": 6}}], review.parse_html(FLOW_HTML))
    assert nocond["FLOW-NOCOND"]["verdict"] == "fail"


def test_step_order_is_fuzzy_but_ordered():
    doc = review.parse_html(FLOW_HTML.replace("가입기간 선택", "기간을 선택하세요"))
    v = review.deterministic_checks([ITEMS[1]], doc)
    assert v["STEP-ORDER"]["verdict"] == "pass"
    swapped = wrap([SECTIONS[0], SECTIONS[2], SECTIONS[1], *SECTIONS[3:]])   # 문서 순서가 기준 — 기간 선택과 금액 입력을 바꾼다
    assert review.deterministic_checks([ITEMS[1]], review.parse_html(swapped))["STEP-ORDER"]["verdict"] == "fail"


def test_skeleton_diff_and_stability_item():
    same = review.skeleton_diff(FLOW_HTML, FLOW_HTML)
    small = review.skeleton_diff(FLOW_HTML, FLOW_HTML.replace("<p>만기 예상 이자</p>", "<p>만기 예상 이자 (세전)</p>"))
    big = review.skeleton_diff(FLOW_HTML, BAD_HTML)
    assert same == 0.0 and small < 0.1 < big
    item, verdict = review.stability_item(big)
    assert item["id"] == "STABLE" and item["weight"] == 1 and not item["required"] and verdict["verdict"] == "fail"
    assert review.stability_item(small)[1]["verdict"] == "pass"


def test_parse_review_strict_json():
    ids = ["LLM-1", "LLM-2"]
    good = 'blah {"items":[{"id":"LLM-1","verdict":"pass","evidence":"제목에 상품명","fix":""},{"id":"LLM-2","verdict":"fail","evidence":"CTA 2개","fix":"하나로"}]} trailing'
    v, err = review.parse_review(good, ids)
    assert err is None and v["LLM-1"]["verdict"] == "pass" and v["LLM-2"]["fix"] == "하나로"
    v2, err2 = review.parse_review("not json", ids)
    assert err2 and v2 == {"LLM-1": None, "LLM-2": None}
    v3, err3 = review.parse_review('{"items":[{"id":"LLM-1","verdict":"maybe"}]}', ids)
    assert v3["LLM-1"] is None and v3["LLM-2"] is None and err3


def test_score_math_and_undetermined_not_pass():
    verdicts = {i["id"]: {"verdict": "pass", "evidence": "", "fix": ""} for i in ITEMS}
    s = review.score(ITEMS, verdicts, pass_score=85)
    assert s["score"] == 100 and s["passed"] and s["requiredFailed"] == []
    verdicts["LLM-1"] = None                       # 미판정 — 필수
    s = review.score(ITEMS, verdicts, pass_score=85)
    assert not s["passed"] and s["undetermined"] == ["LLM-1"] and s["score"] == round(18 / 21 * 100)
    verdicts["LLM-1"] = {"verdict": "pass", "evidence": "", "fix": ""}
    verdicts["LLM-2"] = {"verdict": "fail", "evidence": "", "fix": ""}
    s = review.score(ITEMS, verdicts, pass_score=95)
    assert s["score"] == round(19 / 21 * 100) and not s["passed"], "필수는 다 통과했지만 점수 미달"
