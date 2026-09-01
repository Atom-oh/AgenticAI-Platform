"""Seed sample assets of every type so the 자산 tab demonstrates the full catalog.

Reuses feedback.assets_api.register_asset (same versioning/history/skill
propagation as the live API). Re-running bumps versions — harmless.
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

cfg = json.loads((ROOT / "config" / "stack.json").read_text())
os.environ.update({
    "AWS_DEFAULT_REGION": cfg["region"],
    "ASSETS_BUCKET": cfg["assets_bucket"],
    "REGISTRY_TABLE": cfg["registry_table"],
    "HISTORY_TABLE": cfg["history_table"],
    "SKILLS_BUCKET": cfg["skills_bucket"],
    "DISPATCHER_FN": cfg.get("dispatcher_fn", ""),
})

from feedback.assets_api import register_asset  # noqa: E402

ACTOR = "하나 UX팀"

SAMPLES = [
    {"name": "하나 시그니처 팔레트", "type": "palette", "scope": "shared",
     "content": json.dumps({
         "primary": "#008485", "primaryDark": "#00615f", "mint": "#e6f3f2",
         "ink": "#17332f", "bg": "#fbfcfb", "warn": "#d9a441", "danger": "#b23b3b",
         "usage": "primary는 CTA·활성 상태 1곳에만, mint는 배경 틴트, warn/danger는 상태 전용"},
         ensure_ascii=False, indent=2)},

    {"name": "하나 라이트 토큰", "type": "token", "scope": "shared",
     "content": json.dumps({
         "tokens": {
             "color": {"primary": "#008485", "ink": "#17332f", "bg": "#ffffff",
                       "line": "#e2e8e6", "muted": "#8aa19c"},
             "type": {"display": {"fontFamily": "Noto Sans KR", "fontSize": 32, "fontWeight": 900},
                      "heading": {"fontFamily": "Noto Sans KR", "fontSize": 20, "fontWeight": 700},
                      "body": {"fontFamily": "Noto Sans KR", "fontSize": 15, "fontWeight": 400}},
             "space": {"xs": 8, "sm": 12, "md": 16, "lg": 24, "xl": 32},
             "radius": {"card": 16, "button": 14, "chip": 22}}},
         ensure_ascii=False, indent=2)},

    {"name": "하나 코어 아이콘셋", "type": "icon-set", "scope": "shared",
     "content": json.dumps({
         "style": "stroke 2px, 24px grid, round cap",
         "icons": {
             "home": "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M3 11l9-8 9 8M5 10v10h14V10'/></svg>",
             "transfer": "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M4 7h13l-3-3M20 17H7l3 3'/></svg>",
             "alert": "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M12 3a6 6 0 016 6v4l2 3H4l2-3V9a6 6 0 016-6zM10 19a2 2 0 004 0'/></svg>",
             "settings": "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><circle cx='12' cy='12' r='3'/><path d='M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2'/></svg>"}},
         ensure_ascii=False, indent=2)},

    {"name": "바텀 내비게이션 컴포넌트", "type": "component", "scope": "shared",
     "content": """# 바텀 내비게이션 (Bottom Nav)

용도: 모바일 화면 하단 고정 내비게이션. 홈 / 이체 / 알림 / 전체 4탭.

규격
- 높이 64px, 상단 1px #e2e8e6 보더, 배경 #ffffff
- 탭: 아이콘 24px + 라벨 11px, 활성 탭 color/primary + 굵기 700
- 히트 영역: 탭당 최소 64×64px
- 화면당 1개, 시안 하단에 고정 (position: sticky/fixed)

사용 규칙
- 이체 플로우 진행 중(위저드 2단계 이후)에는 숨긴다
- 알림 탭은 미확인 건수 뱃지(8px 도트)만 — 숫자 뱃지 금지"""},

    {"name": "하나 UI 라이팅 가이드", "type": "style-guide", "scope": "shared",
     "content": """# 하나 UI 라이팅 가이드

톤: 정중하되 간결. 해요체 기본 ("이체가 완료됐어요"), 법적 고지는 합니다체.

버튼
- 동사로 끝나는 행동 문구: "이체하기", "지금 개설하기" (O) / "확인", "다음" 남용 (X)
- 금액이 확정된 CTA에는 금액 포함: "150,000원 이체하기"

금액·숫자
- 통화는 항상 원화 표기 + 천단위 콤마, 큰 금액은 한글 병기 ("1,500,000원 (백오십만원)")

에러
- 원인 + 해결책 한 문장씩: "잔액이 부족해요. 출금계좌를 변경하거나 금액을 줄여주세요."
- 시스템 탓 표현 금지 ("오류가 발생했습니다" 단독 사용 X)"""},

    {"name": "다크모드 변환", "type": "skill", "scope": "shared",
     "content": """---
name: dark-mode
description: 시안을 다크모드로 변환할 때의 규칙
---

# 다크모드 변환 스킬

- 배경 #101b19, 카드 #1a2624, 라인 #2c3a37
- 텍스트: 본문 #e8efed, 보조 #9db3ae
- color/primary(#008485)는 다크에서 #2fb3ae로 한 단계 밝게
- 그림자 대신 1px 라인으로 위계 표현, 순수 흰색/검정 금지
- 상태 색(warn/danger)은 채도 10% 낮춰 사용"""},

    {"name": "신규 고객 온보딩 워크플로우", "type": "workflow", "scope": "shared",
     "content": """# 신규 고객 온보딩 흐름

1. 환영 + 핵심 가치 1줄 (스킵 가능)
2. 본인 인증 (휴대폰 → 신분증 촬영)
3. 계좌 선택 (추천 1개를 기본 선택으로)
4. 완료 + 첫 행동 유도 (첫 이체 or 카드 신청 중 택1)

규칙: 단계 표시는 항상 'n/4', 뒤로가기 허용, 각 단계 한 화면 하나의 질문만."""},

    {"name": "트렌디 MZ 에이전트", "type": "agent", "scope": "shared",
     "content": json.dumps({
         "system": "20-30대 대상 트렌디한 톤: 볼드한 타이포, 위트 있는 카피(반말 금지), "
                   "여백 많은 카드, 마이크로 인터랙션 힌트(hover/pressed 상태 명시). "
                   "그라디언트 남용 금지, 포인트 컬러는 한 곳에만.",
         "model_id": "global.anthropic.claude-sonnet-5",
         "asset_ids": ["palette:하나-시그니처-팔레트", "style-guide:하나-ui-라이팅-가이드"],
         "skills": []}, ensure_ascii=False, indent=2)},
]


def main():
    for sample in SAMPLES:
        code, out = register_asset(sample, actor=ACTOR)
        print(code, out.get("asset_id", out))


if __name__ == "__main__":
    main()
