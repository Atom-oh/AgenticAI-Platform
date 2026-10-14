"""MyData 자유 문장 정규식 익명화 (모델 호출 없음).

MyData 질문의 대부분은 개인을 식별하지 못하는 금융 사실(연봉 8천만원, 금리 3.5%, 12개월)이다.
이 모듈은 그런 값은 그대로 두고, 문장 안의 직접 식별자만 정규식으로 찾아 무작위 토큰으로 바꾼다.

- 원문↔토큰 매핑은 보관하지 않는다. 새로 입력된 자유 텍스트 식별자는 복원하지 않는다.
- 같은 요청 안에서 같은 값은 같은 토큰을 받는다 (문맥 유지). 토큰은 요청마다 새로 뽑는다.
- 이 규칙과 독립된 잔여 검사(common/pii.py 규칙 + Bedrock Guardrails)는 호출자가 수행한다.
  같은 정규식으로 지운 것을 같은 정규식으로 다시 세면 항상 0이므로, 여기서의 자체 잔여 수는 계측이 아니다.
- 정규식은 문맥이 없는 이름·주소를 놓칠 수 있다. 법적 익명성이나 완전한 비식별을 주장하지 않는다.
"""
from __future__ import annotations

import re
import secrets
import time

from . import pii

PROCESSOR = "regex-anonymizer"
RULESET_VERSION = "kr-identifiers-2026-10-14"
MAX_BYTES = 8192

_SIDO = (r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
         r"(?:특별시|광역시|특별자치시|특별자치도|도)?|[가-힣]{2,4}(?:특별시|광역시|특별자치시|특별자치도|도)")
# 호칭·직함은 이름이 아니다 ("고객님", "담당자님"을 이름으로 오인하지 않는다).
_NOT_NAMES = frozenset(("고객", "회원", "담당자", "상담사", "선생", "사장", "부장", "과장", "대리", "팀장", "실장",
                        "어머", "아버", "여러분", "본인", "고객님", "회원님", "손님", "직원", "은행",
                        # "저는 회사원이고" 같은 자기소개 직업·신분 — MyData 상담에 필요한 비식별 정보다.
                        "회사원", "직장인", "공무원", "학생", "대학생", "주부", "자영업자", "프리랜서", "개인사업자",
                        "사업자", "은퇴자", "무직", "군인", "교사", "의사", "간호사", "연금수령자", "신혼부부"))
_NAME = r"[가-힣]{2,4}?"   # 게으른 매치 — 이름 뒤 조사("이고")까지 삼키지 않는다
_NAME_END = r"(?=(?:이고|이며|이에요|이예요|입니다|이구요|이고요|예요|고|님|씨|이|가|은|는|의|,|\.|\s|$))"

# 순서 = 우선순위 (먼저 바뀐 자리는 뒤 규칙이 다시 보지 않는다). 캡처 그룹 "v" 가 있으면 그 부분만 바꾼다.
RULES: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("RRN", re.compile(r"(?<![\d-])\d{6}[- ]?[1-8]\d{6}(?![\d-]|\s*원)")),
    ("CARD", re.compile(r"(?<![\d-])(?:\d{4}[- ]){3}\d{4}(?![\d-])|(?<![\d,])\d{15,16}(?![\d,]|\s*원)")),
    # 국번 앞 0(또는 +82)을 요구한다 — 25000000 같은 금액이 '2-5000-0000'으로 오인되지 않게.
    ("PHONE", re.compile(r"(?<![\d-])(?:\+82[- ]?0?|0)(?:1[016789]|2|[3-6][1-5]|70)[- .)]?\d{3,4}[- .]?\d{4}(?![\d-]|\s*원)")),
    ("PASSPORT", re.compile(r"(?<![A-Za-z0-9])(?:[MSRODG]\d{8}|[MSRODG]\d{3}[A-Z]\d{4})(?![A-Za-z0-9])")),
    ("CUSTOMER_ID", re.compile(r"\b(?:CUST|ACCT)-\d{3,}\b")),
    # 계좌: 하이픈 2~3개, 숫자 합계 10자리 이상 (2026-10-14 같은 날짜는 8자리라 제외).
    ("ACCOUNT", re.compile(r"(?<![\d-])(?=(?:\d-?){10,})\d{2,6}-\d{2,6}-\d{2,7}(?:-\d{1,3})?(?![\d-])")),
    ("ACCOUNT", re.compile(r"계좌(?:번호)?\s*(?:은|는|:)?\s*(?P<v>\d{10,14})(?!\d)")),
    ("DOB", re.compile(r"(?:생년월일|생일|출생일?)\s*(?:은|는|이|:)?\s*"
                       r"(?P<v>(?:19|20)\d{2}\s*(?:[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}|년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)|\d{6})")),
    ("ADDRESS", re.compile(r"(?:" + _SIDO + r")\s+[가-힣]{1,6}(?:시|군|구)(?:\s+[가-힣]{1,6}(?:구|읍|면))?"
                           # 도로명/동·리와 번지는 필수 — "서울 강남구" 같은 지역 수준은 식별자가 아니다.
                           r"\s+[가-힣0-9]{1,12}(?:로|길|동|리)\s*\d{1,5}(?:-\d{1,5})?(?:번지)?"
                           r"(?:\s*,?\s*\d{1,4}동)?(?:\s*\d{1,5}호)?")),
    ("PERSON", re.compile(r"(?<![가-힣])(?:(?:저는|전)\s+|(?:제\s*이름은|이름은|이름:|성함은|성함:|고객명\s*:?|예금주\s*:?|명의자\s*:?)\s*)"
                          r"(?P<v>" + _NAME + r")" + _NAME_END)),
    ("PERSON", re.compile(r"(?<![가-힣])(?P<v>" + _NAME + r")\s?(?:님|씨)(?![가-힣])")),
]
TYPES = tuple(dict.fromkeys(kind for kind, _ in RULES))


class AnonymizationRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _token(kind: str) -> str:
    alphabet = "abcdefghijklmnop"
    return f"⟨{kind}:" + "".join(alphabet[b >> 4] + alphabet[b & 15] for b in secrets.token_bytes(16)) + "⟩"


def _is_name_candidate(kind: str, value: str) -> bool:
    if kind != "PERSON":
        return True
    return value not in _NOT_NAMES and not any(value.startswith(word) for word in ("고객", "회원", "담당"))


def anonymize(text: str) -> dict:
    """{text, counts:{TYPE:n}, total} — 원문 값은 반환하지 않는다."""
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_BYTES:
        raise AnonymizationRejected("PRIVACY_INVALID_REQUEST")
    if pii.tokens_in(text):
        raise AnonymizationRejected("PRIVACY_RESERVED_TOKEN_INPUT")
    tokens: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    out = text
    for kind, pattern in RULES:
        def repl(match: re.Match, kind=kind) -> str:
            span = "v" if "v" in (match.re.groupindex or {}) else 0
            value = match.group(span)
            if not value or "⟨" in match.group(0) or not _is_name_candidate(kind, value):
                return match.group(0)
            key = (kind, re.sub(r"[\s.()-]", "", value))
            token = tokens.get(key)
            if token is None:
                token = tokens[key] = _token(kind)
            counts[kind] = counts.get(kind, 0) + 1
            if span == 0:
                return token
            start, end = match.start(span) - match.start(), match.end(span) - match.start()
            whole = match.group(0)
            return whole[:start] + token + whole[end:]
        out = pattern.sub(repl, out)
    return {"text": out, "counts": counts, "total": sum(counts.values())}


def process(text: str, request_id: str) -> dict:
    """S2 질문 처리 — privacy.process 와 같은 {ok, text, evidence} 모양. 잔여 규칙 식별자가 있으면 차단한다."""
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request_id):
        raise AnonymizationRejected("PRIVACY_INVALID_REQUEST")
    started = time.monotonic()
    result = anonymize(text)
    residual = pii.scan_rules(pii.guardrail_view(result["text"], pii.tokens_in(result["text"])))
    if residual:
        raise AnonymizationRejected("PRIVACY_RESIDUAL_PII")
    evidence = {
        "status": "pass", "processor": PROCESSOR, "method": "redaction", "model": "regex",
        "modelId": PROCESSOR, "modelRevision": RULESET_VERSION, "promptVersion": "not-applicable",
        "entityCounts": [{"type": kind, "count": result["counts"][kind]} for kind in TYPES if kind in result["counts"]],
        "total": result["total"], "sourceChars": len(text), "outputChars": len(result["text"]),
        "ruleResidualCount": 0, "independentNer": "not-configured", "modelInvoked": False,
        "latencyMs": int((time.monotonic() - started) * 1000),
    }
    return {"ok": True, "text": result["text"], "evidence": evidence}


def option() -> dict:
    return {"id": "regex", "label": "정규식 익명화", "family": "rules", "processor": PROCESSOR,
            "modelId": PROCESSOR, "revision": RULESET_VERSION, "available": True}
