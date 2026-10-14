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
         r"(?:특별시|광역시|특별자치시|특별자치도|시|도)?|[가-힣]{2,4}(?:특별시|광역시|특별자치시|특별자치도|도)")
# 호칭·직함은 이름이 아니다 ("고객님", "담당자님"을 이름으로 오인하지 않는다).
_NOT_NAMES = frozenset(("고객", "회원", "담당자", "상담사", "선생", "사장", "부장", "과장", "대리", "팀장", "실장",
                        "어머", "아버", "여러분", "본인", "고객님", "회원님", "손님", "직원", "은행",
                        # "저는 회사원이고" 같은 자기소개 직업·신분 — MyData 상담에 필요한 비식별 정보다.
                        "회사원", "직장인", "공무원", "학생", "대학생", "주부", "자영업자", "프리랜서", "개인사업자",
                        "사업자", "은퇴자", "무직", "군인", "교사", "의사", "간호사", "연금수령자", "신혼부부",
                        "전업주부", "무주택자", "맞벌이",
                        # 성씨로 시작하지만 가족·직함 호칭인 말 ("사모님", "이모님", "기사님")
                        "사모", "이모", "고모", "장모", "부모", "선배", "후배", "기사", "원장", "소장", "차장",
                        "전무", "이사", "주인", "변호사", "지점장", "강사", "조교", "김씨", "이씨", "박씨",
                        "남편", "조합원", "주주", "사용자", "신청자", "부부", "하나", "도련", "신부", "수녀", "목사",
                        "이사장",
                        # 대출·주거·보험 상담의 역할어 — 이름 음절과 겹칠 수 있어 정확히 일치할 때만 제외한다
                        "차주", "고문", "기장", "주임", "선임", "점장", "노인", "성인", "신혼", "시민", "고령", "임원", "승객"))
# 약한 문맥("저는 ○○입니다", "○○님")의 후보에 이 말이 들어 있으면 금융·주거·고용 상태로 보고 그대로 둔다
# ("저는 전세입니다", "고정금리예요"). 이름 음절과 겹치는 짧은 말(정기·신용·고정·정규·우대)은 넣지 않는다.
# 명시 문맥("제 이름은", "성함은", "예금주")에는 적용하지 않는다.
_STATUS_STEMS = ("금리", "예금", "적금", "전세", "월세", "주택", "정규직", "계약직", "기혼", "미혼", "이혼", "사원",
                 "고객", "회원", "대출", "카드", "보험", "연금", "펀드", "계좌", "소득", "월급", "연봉", "자산",
                 "부채", "신용불량", "신용점수", "등급", "주거래", "우량", "사업", "직장", "직원", "학생", "주부",
                 "은퇴", "무직", "가입", "수급", "거주", "세대", "가구", "담보", "청약", "채무", "채권", "임차",
                 "임대", "고령자", "장애", "차상위", "설계사", "연구원", "노무사", "세무사", "신청인", "부사장",
                 "승무원", "시니어", "태국", "수령")
# 흔한 성씨 첫 글자 — "저는 ○○입니다"·"○○님" 같은 약한 문맥에서는 성씨로 시작하는 말만 이름 후보로 본다.
_SURNAME = ("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공"
            "현함변염여추도소석선설마길연위표명기반라왕금옥육인맹제모탁국어은편용예경봉사부"
            "빈피궁팽점묵빙엽풍흥태승시")
_NAME = r"[가-힣]{2,4}?"   # 게으른 매치 — 이름 뒤 조사("이고")까지 삼키지 않는다
# 이름 끝이 '이'인 경우("김영이입니다")를 위해 맨 '이'는 종결 후보에서 뺀다.
_NAME_END = r"(?=(?:이고|이며|이에요|이예요|입니다|이구요|이고요|예요|고|님|씨|가|은|는|의|,|\.|\s|$))"
# "저는/전 ○○" 은 "저는 전월실적이", "1년 전 대출을" 처럼 일반 문장과 겹친다 — 자기소개 종결어미만 받는다.
_INTRO_END = (r"(?=\s*(?:(?:입니다|이에요|이예요|예요|이고요|이고|이며|인데요?|이?라고\s*(?:합니다|해요))(?![가-힣])"
              r"|고(?=[\s,])|,))")
# 약한 문맥의 이름: 성 + 1~2자 (복성은 + 1~2자). 네 글자 단어("반전세", "정기예금")를 이름으로 보지 않는다.
_WEAK_NAME = (r"(?:(?:남궁|황보|제갈|선우|독고|사공|서문)[가-힣]{1,2}?|[" + _SURNAME + r"][가-힣]{1,2}?)")
_PARTICLES = r"(?:께서|께|에게|한테|의|이|가|은|는|을|를|과|와|도){0,2}(?![가-힣])"

# 순서 = 우선순위 (먼저 바뀐 자리는 뒤 규칙이 다시 보지 않는다). 캡처 그룹 "v"(명시 문맥)·"w"(약한 문맥)가 있으면 그 부분만 바꾼다.
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
    ("PERSON", re.compile(r"(?:제\s*이름은|이름은|이름:|성함은|성함:|고객명\s*:?|예금주\s*:?|명의자\s*:?)\s*"
                          r"(?P<v>" + _NAME + r")" + _NAME_END)),
    ("PERSON", re.compile(r"(?<![가-힣])(?:저는|전)\s+(?P<w>" + _WEAK_NAME + r")" + _INTRO_END)),
    ("PERSON", re.compile(r"(?<![가-힣])(?P<w>" + _WEAK_NAME + r")\s?(?:님|씨)" + _PARTICLES)),
    # "홍길동 고객님" — 성+두 글자 이름만 ("우수 고객님", "신규 고객님" 같은 두 글자 수식어 제외)
    ("PERSON", re.compile(r"(?<![가-힣])(?P<w>(?:남궁|황보|제갈|선우|독고|사공|서문)[가-힣]{1,2}|[" + _SURNAME + r"][가-힣]{2})\s+(?:고객|회원)님")),
]
TYPES = tuple(dict.fromkeys(kind for kind, _ in RULES))


class AnonymizationRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _token(kind: str) -> str:
    alphabet = "abcdefghijklmnop"
    return f"⟨{kind}:" + "".join(alphabet[b >> 4] + alphabet[b & 15] for b in secrets.token_bytes(16)) + "⟩"


def _is_name_candidate(kind: str, value: str, weak: bool) -> bool:
    if kind != "PERSON":
        return True
    if weak and any(stem in value for stem in _STATUS_STEMS):
        return False
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
            groups = match.re.groupindex or {}
            span = "v" if "v" in groups else "w" if "w" in groups else 0
            value = match.group(span)
            if not value or "⟨" in match.group(0) or not _is_name_candidate(kind, value, span == "w"):
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
