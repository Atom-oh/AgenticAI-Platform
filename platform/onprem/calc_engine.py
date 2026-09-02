"""결정론적 계산엔진 (SPEC F3) — 온프렘 플레인 소속.

우대금리·한도 계산은 전부 이 순수 함수들이 수행한다. LLM은 결과를 설명만 한다
(§12.2: LLM이 금액·금리·한도를 생성하지 않는다).
모든 함수는 계산 내역(수식 포함)을 함께 반환한다 — S2 화면의 '계산 내역' 패널.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

D = Decimal


@dataclass
class CalcStep:
    label: str
    formula: str
    value: str


@dataclass
class CalcResult:
    name: str
    value: Decimal
    unit: str
    steps: list[CalcStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "value": str(self.value), "unit": self.unit,
                "steps": [{"label": s.label, "formula": s.formula, "value": s.value}
                          for s in self.steps]}


# 우대금리 항목 (아톰 안심전세대출 II — 합성 상품 조건)
PREF_RULES = [
    {"id": "salary_transfer", "label": "급여이체 실적", "rate": D("0.3"),
     "check": lambda p: p.get("salaryTransferMonths", 0) >= 3,
     "criteria": "최근 3개월 연속 급여이체"},
    {"id": "card_usage", "label": "카드 사용 실적", "rate": D("0.2"),
     "check": lambda p: p.get("cardMonthlyKrw", 0) >= 300_000,
     "criteria": "전월 카드 사용 30만원 이상"},
    {"id": "auto_transfer", "label": "자동이체 등록", "rate": D("0.1"),
     "check": lambda p: p.get("autoTransferCount", 0) >= 3,
     "criteria": "자동이체 3건 이상"},
    {"id": "first_home", "label": "생애최초·신혼 우대", "rate": D("0.2"),
     "check": lambda p: bool(p.get("isFirstHome") or p.get("isNewlywed")),
     "criteria": "생애최초 주택 또는 신혼가구"},
]
PREF_CAP = D("0.6")  # 우대 합산 상한


def preferential_rate(profile: dict, base_rate: str | float) -> CalcResult:
    """우대금리 판정·적용금리 계산. profile은 온프렘 정확 조회 결과."""
    base = D(str(base_rate))
    steps = [CalcStep("기본금리", "base_rate", f"{base}%")]
    total = D("0")
    for rule in PREF_RULES:
        ok = bool(rule["check"](profile))
        steps.append(CalcStep(
            f"{rule['label']} ({rule['criteria']})",
            f"충족 시 -{rule['rate']}%p",
            f"{'충족 → -' + str(rule['rate']) if ok else '미충족 → -0'}%p"))
        if ok:
            total += rule["rate"]
    capped = min(total, PREF_CAP)
    if total > PREF_CAP:
        steps.append(CalcStep("우대 상한 적용", f"min({total}, {PREF_CAP})", f"{capped}%p"))
    applied = (base - capped).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    steps.append(CalcStep("적용금리", f"{base} - {capped}", f"{applied}%"))
    r = CalcResult("우대금리_적용", applied, "percent", steps)
    return r


def jeonse_loan_limit(deposit_krw: int, guarantee_ratio: str | float,
                      income_krw: int, existing_debt_krw: int) -> CalcResult:
    """전세대출 한도: min(보증금×보증비율, 소득 기반 한도) - 기존 대출."""
    dep = D(deposit_krw)
    ratio = D(str(guarantee_ratio))
    by_deposit = (dep * ratio).quantize(D("1"))
    by_income = (D(income_krw) * 4).quantize(D("1"))  # 합성 규칙: 연소득 4배
    gross = min(by_deposit, by_income)
    net = max(gross - D(existing_debt_krw), D(0))
    steps = [
        CalcStep("보증금 기준", f"{deposit_krw:,} × {ratio}", f"{by_deposit:,}원"),
        CalcStep("소득 기준", f"{income_krw:,} × 4", f"{by_income:,}원"),
        CalcStep("적용 한도(작은 값)", f"min({by_deposit:,}, {by_income:,})", f"{gross:,}원"),
        CalcStep("기존 대출 차감", f"{gross:,} - {existing_debt_krw:,}", f"{net:,}원"),
    ]
    return CalcResult("전세대출_한도", net, "krw", steps)


def verify_no_generated_numbers(llm_text: str, allowed: list[str]) -> list[str]:
    """출력 검증기 (§12.2): LLM 설명문에 계산엔진이 주지 않은 수치가 있으면 반환.

    allowed에는 계산 결과·조회 원본값의 문자열 표현을 넣는다.
    """
    import re
    allowed_norm = {a.replace(",", "") for a in allowed}
    found = re.findall(r"\d[\d,]*\.?\d*", llm_text)
    bad = []
    for f in found:
        norm = f.replace(",", "")
        if norm in allowed_norm:
            continue
        # 허용 수치의 단순 변형(만원/억 환산)과 1~2자리 서수는 허용
        if len(norm.rstrip("0").rstrip(".")) <= 2:
            continue
        if any(norm in a or a in norm for a in allowed_norm):
            continue
        bad.append(f)
    return bad
