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
TOKEN_RE = re.compile(r"⟨[A-Z][A-Z0-9_]{0,39}:(?:[0-9a-f]{8}|[0-9a-f]{32}|[a-p]{32})⟩")


def tokens_in(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text))


def guardrail_view(text: str, trusted_tokens=None) -> str:
    known = set(trusted_tokens or ())
    # Keep positions/character count stable. This excludes only exact markers
    # already produced by trusted stages; arbitrary raw text is still inspected.
    return TOKEN_RE.sub(lambda match: " " * len(match.group()) if match.group() in known else match.group(), text)

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


class PiiVerificationUnavailable(Exception):
    pass


def guardrail_hits(response: dict, *, strict: bool = False) -> list[dict]:
    """Detection is distinct from policy enforcement (action NONE can detect)."""
    assessments = response.get("assessments")
    if not isinstance(assessments, list):
        if strict:
            raise PiiVerificationUnavailable("Missing PII assessments")
        return []
    hits = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            raise PiiVerificationUnavailable("Invalid PII assessment")
        policy = assessment.get("sensitiveInformationPolicy", {})
        if not isinstance(policy, dict):
            raise PiiVerificationUnavailable("Invalid PII assessment")
        for collection in ("piiEntities", "regexes"):
            entities = policy.get(collection, [])
            if not isinstance(entities, list):
                raise PiiVerificationUnavailable("Invalid PII entities")
            for entity in entities:
                if not isinstance(entity, dict):
                    raise PiiVerificationUnavailable("Invalid PII entity")
                action, detected = entity.get("action"), entity.get("detected")
                if strict and (action not in ("NONE", "ANONYMIZED", "BLOCKED")
                               or (detected is not None and type(detected) is not bool)):
                    raise PiiVerificationUnavailable("Invalid detection status")
                found = detected is True or (detected is None and (
                    action in ("ANONYMIZED", "BLOCKED") or bool(entity.get("match"))))
                if strict and detected is None and action == "NONE" and not entity.get("match"):
                    raise PiiVerificationUnavailable("Ambiguous detection status")
                if found:
                    kind = entity.get("type") if collection == "piiEntities" else "REGEX"
                    if not isinstance(kind, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", kind):
                        raise PiiVerificationUnavailable("Missing detection type")
                    hits.append({"type": kind, "detector": "guardrail", "sample": "", "action": action})
    return hits


def verify_guardrail_coverage(response: dict, text: str, *, context_chars: int = 0):
    if not isinstance(response, dict) or response.get("action") not in ("NONE", "GUARDRAIL_INTERVENED"):
        raise PiiVerificationUnavailable("Invalid PII verification response")
    usage = response.get("usage")
    units = usage.get("sensitiveInformationPolicyUnits") if isinstance(usage, dict) else None
    coverage = response.get("guardrailCoverage")
    chars = coverage.get("textCharacters") if isinstance(coverage, dict) else None
    if (type(units) is not int or units <= 0 or not isinstance(chars, dict)
            or type(chars.get("total")) is not int or type(chars.get("guarded")) is not int
            or chars["total"] < len(text) + context_chars or chars["guarded"] < len(text)
            or chars["guarded"] > chars["total"]
            or (context_chars == 0 and chars["guarded"] != chars["total"])):
        raise PiiVerificationUnavailable("PII verification did not cover the input")


def scan_guardrail(text: str, *, strict: bool = False) -> list[dict]:
    """Bedrock Guardrails PII 평가 — 마스킹 규칙과 독립된 ML 탐지기. 실패 시 빈 목록(로그)."""
    if not GUARDRAIL_ID or not text:
        if strict:
            raise PiiVerificationUnavailable("PII verification is not configured")
        return []
    if strict and len(text) > 4000:
        raise PiiVerificationUnavailable("PII verification input exceeds checked length")
    try:
        rt = boto3.client("bedrock-runtime", region_name=REGION)
        r = rt.apply_guardrail(guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VER,
                               source="INPUT", content=[{"text": {"text": text[:4000]}}])
        if strict:
            verify_guardrail_coverage(r, text)
        return guardrail_hits(r, strict=strict)
    except Exception as ex:  # 계측 실패는 요청을 막지 않되 기록한다
        from common.log import log_event
        log_event("pii.guardrail_scan_failed", errorType=type(ex).__name__)
        if strict:
            raise PiiVerificationUnavailable("PII verification failed") from None
        return []


def scan_outbound(text: str, use_guardrail: bool = True, *, strict: bool = False, trusted_tokens=None) -> dict:
    """반환: {"count": n, "hits": [...], "detectors": ["rules","guardrail"]}"""
    checked_text = guardrail_view(text, trusted_tokens)
    hits = scan_rules(checked_text)
    detectors = ["rules"]
    if strict and hits:
        return {"count": len(hits), "hits": hits[:20], "detectors": detectors}
    if use_guardrail:
        hits += scan_guardrail(checked_text, strict=strict)
        detectors.append("guardrail")
    return {"count": len(hits), "hits": hits[:20], "detectors": detectors}
