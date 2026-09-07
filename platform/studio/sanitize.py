# platform/studio/sanitize.py
"""모델(Bedrock)로 나가는 텍스트에서 식별자를 마스킹한다 (익명화 게이트 gate-safe).

익명화 게이트(engine.gate.check)는 common.pii.scan_rules 가 잡는 식별자가 있으면 요청을 통째로
거부한다(GateRefused). 라운드 1이 생성한 합성 샘플 데이터(계좌번호 등)가 라운드 2/refine 재생성이나
리뷰어 다이제스트에 그대로 실려 게이트를 다시 지나갈 때 이 거부가 재발한다.

여기서는 common.pii.RULES(스캐너와 같은 정규식 테이블)의 매치를 자릿수는 그대로 두고 각 문자를
마스킹해 모양은 유지하되 식별자 값은 지운다 — 게이트로 나가는 프롬프트 문자열에만 적용하고,
S3 에 발행되는 원본 HTML이나 채점용 items 에는 적용하지 않는다.
"""
from __future__ import annotations

import re

from common.pii import RULES


def _mask(match: re.Match) -> str:
    s = match.group(0)
    return "".join("*" if ch.isdigit() else ch for ch in s)


def scrub_result(text: str) -> tuple[str, int]:
    """반환: (마스킹된 텍스트, 치환 건수). 식별자가 없으면 원문 그대로, 0."""
    s = text or ""
    count = 0
    for _kind, pat in RULES:
        s, n = pat.subn(_mask, s)
        count += n
    return s, count


def scrub(text: str) -> str:
    return scrub_result(text)[0]
