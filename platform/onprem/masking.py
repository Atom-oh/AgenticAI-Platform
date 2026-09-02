"""마스킹/토큰화 게이트 (SPEC F3) — 온프렘 플레인 소속.

경계(온프렘 → Bedrock)를 넘기 직전에 개인 식별 필드를 토큰으로 치환하고,
회신 후 재식별한다. 무엇이 마스킹되어 나갔는지 기록을 남긴다 (F6 계측의 입력).

규칙 기반 1차 + (Phase 3 배포 시) Comprehend PII 2차 이중화.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class MaskResult:
    text: str
    masked_fields: list[dict] = field(default_factory=list)  # {field, token}
    mapping: dict = field(default_factory=dict)  # token -> original (온프렘에만 보관)


# 합성데이터도 방어적으로 처리하는 규칙 (실서비스 규칙의 구조 모사)
RULES = [
    ("customer_id", re.compile(r"\bCUST-\d{4}\b")),
    ("account_id", re.compile(r"\bACCT-\d{4}\b")),
    ("phone", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("krw_exact_balance", re.compile(r"(?<=잔액[ :])\s*[\d,]{7,}원")),
]


def _token(kind: str, value: str) -> str:
    h = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"⟨{kind.upper()}:{h}⟩"


def mask(text: str, extra_values: dict | None = None) -> MaskResult:
    """텍스트에서 식별자를 토큰화한다. extra_values: {필드명: 원본값} 명시 마스킹."""
    r = MaskResult(text=text)
    for field_name, value in (extra_values or {}).items():
        v = str(value)
        if v and v in r.text:
            t = _token(field_name, v)
            r.text = r.text.replace(v, t)
            r.masked_fields.append({"field": field_name, "token": t})
            r.mapping[t] = v
    for kind, pattern in RULES:
        for m in set(pattern.findall(r.text)):
            t = _token(kind, m)
            r.text = r.text.replace(m, t)
            r.masked_fields.append({"field": kind, "token": t})
            r.mapping[t] = m
    return r


def unmask(text: str, mapping: dict) -> str:
    """LLM 회신에서 토큰을 원본으로 재식별한다 (온프렘 내부에서만)."""
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text
