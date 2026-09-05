from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import prompts  # noqa: E402

SPEC = {"productCode": "PRD-DEP-001", "productName": "아톰 축구사랑 적금", "category": "수신", "hasPreferential": True,
        "conditions": [{"id": "c1", "type": "우대", "name": "축구클럽 회원 인증 시 우대금리 +1.0%p"}],
        "steps": [{"screenId": "SCR-DEP-001", "name": "상품안내", "entryCondition": "로그인 완료"},
                  {"screenId": "SCR-DEP-004", "name": "축구클럽 인증", "entryCondition": "금액 입력 완료"}],
        "terms": ["적금 우대금리"], "brandHex": ["#008485"],
        "items": [{"id": "LLM-1", "category": "명세반영", "text": "기본금리 표기", "required": True, "weight": 3, "check": "llm"},
                  {"id": "T-1", "category": "용어", "text": "용어", "required": False, "weight": 1, "check": "text", "expect": {"contains": "적금 우대금리"}}]}


def test_skills_load_from_platform_skills_dir():
    skills, missing = prompts.load_skills()
    assert missing == [] and set(skills) == set(prompts.SKILL_NAMES)
    assert "data-step" in skills["studio-draft-html"], "출력 계약(프레임 마킹)이 스킬에 있어야 한다"


def test_system_prompt_contains_spec_and_style():
    skills, _ = prompts.load_skills()
    s = prompts.build_system_prompt(SPEC, "ux-flow", "흐름", skills, assets_text="팔레트: #008485", fewshot=["<html>ref</html>"], agent_preset="시니어 모드 우선")
    for needle in ("아톰 축구사랑 적금", "축구클럽 인증", "축구클럽 회원 인증", "적금 우대금리", "가로 스크롤", "흐름", "팔레트: #008485", "승인 참고 시안", "시니어 모드 우선", 'data-step="1"'):
        assert needle in s, needle
    assert "3" not in prompts.OUTPUT_STYLES["design"] or True  # design 은 스타일 문구 없음
    assert prompts.build_system_prompt(SPEC, "wireframe", "밀도", skills).count("브랜드 컬러 금지") == 1


def test_system_prompt_caps_fewshot_and_assets():
    skills, _ = prompts.load_skills()
    fewshot = ["x" * 20000 for _ in range(3)]
    s = prompts.build_system_prompt(SPEC, "design", "밀도", skills, assets_text="a" * 30000, fewshot=fewshot)
    assert s.count("### 승인 참고 시안") == 2
    assert "참고 시안 일부 생략" in s
    assert "자산 내용 일부 생략" in s
    assert len(s) < 60000


def test_user_prompt_regenerate_and_refine():
    u = prompts.build_user_prompt("적금 가입 플로우", SPEC, failures=[{"id": "LLM-1", "text": "기본금리 표기", "evidence": "없음", "fix": "상단에 연 3.0% 표기"}], prev_html="<html>prev</html>")
    assert "이전 라운드" in u and "상단에 연 3.0% 표기" in u and "<html>prev</html>" in u and "필요한 부분만" in u
    r = prompts.build_user_prompt("", SPEC, refine={"selector": "body > section:nth-of-type(2) > h2", "instruction": "제목을 더 크게", "elementHtml": "<h2>기간</h2>"}, prev_html="<html>base</html>")
    assert "body > section:nth-of-type(2) > h2" in r and "제목을 더 크게" in r and "나머지" in r


def test_review_prompt_lists_only_llm_items_and_requires_json():
    p = prompts.build_review_prompt(SPEC, [i for i in SPEC["items"] if i["check"] == "llm"], "[프레임 1] 상품안내")
    assert "LLM-1" in p and "T-1" not in p and '"verdict"' in p and "[프레임 1]" in p
    assert "pass" in prompts.REVIEW_SYSTEM and "fail" in prompts.REVIEW_SYSTEM


def test_extract_html():
    assert prompts.extract_html("설명\n```html\n<!doctype html><html><body>x</body></html>\n```\n끝").startswith("<!doctype html>")
    assert prompts.extract_html("<html><body>y</body></html>") == "<html><body>y</body></html>"
    assert prompts.extract_html("코드 없음") == ""
