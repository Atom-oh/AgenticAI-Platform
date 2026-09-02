"""마스킹/토큰화 게이트 (SPEC F3) — 온프렘 플레인 소속.

경계(온프렘 → Bedrock)를 넘기 직전에 개인 식별 필드를 토큰으로 치환하고,
회신 후 재식별한다. 무엇이 마스킹되어 나갔는지 기록을 남긴다 (F6 계측의 입력).

규칙 기반 1차 + (클라우드 쪽 common/pii.py 의 독립 탐지기·Guardrails PII) 2차 이중화.
치환은 정규식 매치 단위(pattern.sub)로 수행해 경계가 보장된 자리만 바꾼다 —
같은 숫자열이 다른 문맥(금액 등)에 있어도 건드리지 않는다.
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


# 합성데이터도 방어적으로 처리하는 규칙 (실서비스 규칙의 구조 모사). 순서 = 우선순위.
# 캡처 그룹을 두지 않는다 (sub/findall 동작 일관성).
RULES = [
    ("customer_id", re.compile(r"\bCUST-\d{3,}\b")),
    ("account_id", re.compile(r"\bACCT-\d{3,}\b")),
    # 주민/외국인등록번호: 생년월일 6 + 구분 1(1~8) + 6, 하이픈·공백 허용
    ("kr_rrn", re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")),
    # 카드번호: 4-4-4-4 (하이픈·공백) 또는 16자리 연속
    ("card", re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)")),
    # 휴대전화: 010 1234 5678 / 010-1234-5678 / 01012345678
    ("phone", re.compile(r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("krw_exact_balance", re.compile(r"(?<=잔액[ :])\s*[\d,]{7,}원")),
]

# 명시 값(고객명)의 경계 문자: 라틴 문자·숫자·밑줄만 '같은 단어의 연속'으로 본다.
# 한글은 경계에 넣지 않는다 — 한국어는 조사·호칭·서술어(의/는/가/에게/를/님/씨/입니다…)가 이름에 띄어쓰기 없이
# 바로 붙는 교착어라서, 이름 뒤에 한글이 이어지는 형태가 오히려 가장 흔한 사용 형태다. 닫힌 접미사 목록으로는
# 한 글자라도 빠지면 이름이 경계를 넘어 유출되므로 목록을 두지 않는다. 고객 본인의 이름을 다른 낱말 안에서
# 과잉 마스킹하는 쪽이 안전한 실패다 (unmask 는 토큰 치환이라 원문이 그대로 복원된다).
_LATIN_WORD = "A-Za-z0-9_"

# 이 모듈이 만드는 토큰의 형태 — 수치 검증기 등이 토큰 안의 해시 숫자를 '생성된 수치'로 오인하지 않게 걷어낼 때 쓴다
TOKEN_RE = re.compile(r"⟨[^⟨⟩\s]+:[0-9a-f]{8}⟩")


def _token(kind: str, value: str) -> str:
    h = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"⟨{kind.upper()}:{h}⟩"


def _value_pattern(value: str) -> re.Pattern:
    """명시 값(고객명 등)의 패턴. 앞뒤가 라틴 문자·숫자·밑줄이면 같은 단어의 일부로 보아 매치하지 않는다
    ("Ann" 이 "Announcement" 안에서 잡히지 않게). 한글·공백·문장부호·문장 끝이 이어지는 경우는 모두 이름으로
    본다 — '김데모의', '김데모는', '김데모에게', '김데모님', '김데모입니다' 가 전부 마스킹된다."""
    return re.compile(r"(?<![" + _LATIN_WORD + "])" + re.escape(value) + r"(?![" + _LATIN_WORD + "])")


def _apply(r: MaskResult, kind: str, pattern: re.Pattern) -> None:
    def repl(m: re.Match) -> str:
        v = m.group(0)
        t = _token(kind, v)
        if t not in r.mapping:
            r.masked_fields.append({"field": kind, "token": t})
            r.mapping[t] = v
        return t
    r.text = pattern.sub(repl, r.text)


def mask(text: str, extra_values: "dict | None" = None) -> MaskResult:
    """텍스트에서 식별자를 토큰화한다. extra_values: {필드명: 원본값} 명시 마스킹 (고객명 등)."""
    r = MaskResult(text=text)
    for field_name, value in (extra_values or {}).items():
        v = str(value or "").strip()
        if len(v) < 2:  # 한 글자 값은 오탐이 커서 명시 마스킹 대상에서 제외
            continue
        _apply(r, field_name, _value_pattern(v))
    for kind, pattern in RULES:
        _apply(r, kind, pattern)
    return r


def unmask(text: str, mapping: dict) -> str:
    """LLM 회신에서 토큰을 원본으로 재식별한다 (온프렘 내부에서만)."""
    for token, original in (mapping or {}).items():
        text = text.replace(token, original)
    return text


def strip_tokens(text: str, placeholder: str = " ") -> str:
    """마스킹 토큰을 제거한다 — 토큰 해시의 숫자열은 LLM이 만든 수치가 아니므로 검증 대상이 아니다."""
    return TOKEN_RE.sub(placeholder, text or "")
