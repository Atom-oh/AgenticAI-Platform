"""경계 통과 페이로드의 독립 PII 스캔 (F6).

마스킹 게이트(onprem/masking.py)와 **다른 탐지기**를 쓴다. 같은 정규식으로 지운 것을
같은 정규식으로 다시 세면 항상 0이 나오므로 계측이 아니다. 여기서는
  (1) 확장 규칙: 주민등록번호·외국인등록번호·여권·카드번호(Luhn)·전화·이메일·계좌/고객 토큰
  (2) Bedrock Guardrails sensitiveInformationPolicy 평가(ML NER: NAME/ADDRESS 등)
두 탐지기의 합집합을 반환한다. 어떤 탐지기가 잡았는지도 함께 기록한다.
"""
from __future__ import annotations

import os
import re

import boto3

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VER = os.environ.get("GUARDRAIL_VERSION", "DRAFT")

RULES: list[tuple[str, re.Pattern]] = [
    ("KR_RRN", re.compile(r"(?<!\d)\d{6}-?[1-8]\d{6}(?!\d)")),          # 주민/외국인등록번호
    ("KR_PASSPORT", re.compile(r"\b[MSRODG]\d{8}\b")),
    ("PHONE", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("CARD", re.compile(r"(?<!\d)(?:\d{4}[- ]?){3}\d{4}(?!\d)")),
    ("CUSTOMER_TOKEN", re.compile(r"\bCUST-\d{3,}\b")),
    ("ACCOUNT_TOKEN", re.compile(r"\bACCT-\d{3,}\b")),
    ("KR_BANK_ACCOUNT", re.compile(r"(?<!\d)\d{3}-\d{2,6}-\d{2,6}(?:-\d{1,3})?(?!\d)")),
]


def _luhn_ok(num: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", num)]
    if len(digits) < 13:
        return False
    s = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def scan_rules(text: str) -> list[dict]:
    hits = []
    for kind, pat in RULES:
        for m in pat.findall(text or ""):
            if kind == "CARD" and not _luhn_ok(m):
                continue
            hits.append({"type": kind, "detector": "rules", "sample": m[:3] + "…"})
    return hits


def scan_guardrail(text: str) -> list[dict]:
    """Bedrock Guardrails PII 평가 — 마스킹 규칙과 독립된 ML 탐지기. 실패 시 빈 목록(로그)."""
    if not GUARDRAIL_ID or not text:
        return []
    try:
        rt = boto3.client("bedrock-runtime", region_name=REGION)
        r = rt.apply_guardrail(guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VER,
                               source="INPUT", content=[{"text": {"text": text[:4000]}}])
        hits = []
        for a in r.get("assessments", []):
            sip = a.get("sensitiveInformationPolicy", {})
            for e in sip.get("piiEntities", []):
                if e.get("detected", True) and e.get("action") in ("ANONYMIZED", "BLOCKED"):
                    hits.append({"type": e.get("type"), "detector": "guardrail", "sample": ""})
            for e in sip.get("regexes", []):
                if e.get("action") in ("ANONYMIZED", "BLOCKED"):
                    hits.append({"type": f"REGEX:{e.get('name')}", "detector": "guardrail", "sample": ""})
        return hits
    except Exception as ex:  # 계측 실패는 요청을 막지 않되 기록한다
        from common.log import log_event
        log_event("pii.guardrail_scan_failed", error=str(ex)[:200])
        return []


def scan_outbound(text: str, use_guardrail: bool = True) -> dict:
    """반환: {"count": n, "hits": [...], "detectors": ["rules","guardrail"]}"""
    hits = scan_rules(text)
    detectors = ["rules"]
    if use_guardrail:
        hits += scan_guardrail(text)
        detectors.append("guardrail")
    return {"count": len(hits), "hits": hits[:20], "detectors": detectors}
