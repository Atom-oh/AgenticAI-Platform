"""구조화 로그 (SPEC §10 관측성, §12.3).

CloudWatch에는 메트릭과 traceId만 남긴다 — 프롬프트 원문·개인데이터 필드는 절대 넣지 않는다.
금지 키(prompt/query/answer/payload/text/email)는 길이·해시로 치환한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time

_FORBIDDEN = {"prompt", "query", "answer", "payload", "maskedPayload", "text", "email",
              "system", "user", "message", "content", "actor", "error"}
_METRICS = set("""agents attempts blocked cached chars code codeLen components count crossings denied dense docs
errors estTokens fields gateRefused harnessListed harnessReused hits imageBytes imageCount injection items
limit maxRounds memory ms ocrChars ok pii piiCount promptLen queryLen reasonLen replay screens sent skills
textLen tokensIn tokensOut toolCalls tools inputTokens outputTokens totalTokens elapsedMs durationMs costUsd
usd bytes piiOutbound boundaryFields boundaryChars boundaryEstTokens deniedAttempts injectionDetected
internalDocs semanticMismatch semanticStated gateRejected""".split())
_TAGS = set("""action agent agentcoreRegistry backend category code decision delivery errorCode errorType fromStatus
harnessStatus label mode model modelId op purpose recordType region route runner scenario semanticLayer
stage status stopReason subtype tier to toStatus transition version plane guardrailOut blockedBy privacyStatus readerRole""".split())
_TAG = re.compile(r"[A-Za-z0-9_.:/-]{1,256}\Z")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?\Z")
_HASH = re.compile(r"(?:[a-f0-9]{8}|[a-f0-9]{64})\Z")


def hash8(value: str) -> str:
    return hashlib.sha256((value or "").encode(errors="replace")).hexdigest()[:8]


def redact(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        metric = k in _METRICS and (type(v) in {int, float, bool} or isinstance(v, str) and _NUMBER.fullmatch(v))
        tag = k in _TAGS and isinstance(v, str) and _TAG.fullmatch(v)
        fingerprint = k.endswith("Hash") and isinstance(v, str) and _HASH.fullmatch(v)
        pii_types = k == "piiTypes" and isinstance(v, list) and len(v) <= 30 and all(
            isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", item) for item in v)
        detectors = k == "piiDetectors" and isinstance(v, list) and len(v) <= 10 and all(
            isinstance(item, str) and item in {"rules", "guardrail", "rules(gate)", "tool-egress-gate"} for item in v)
        if k in _FORBIDDEN or not (metric or tag or fingerprint or pii_types or detectors):
            try:
                text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = "unserializable-" + type(v).__name__
            out[f"{k}Hash"] = hash8(text)
            out[f"{k}Len"] = len(text)
        else:
            out[k] = v
    return out


def log_event(event: str, trace_id: str = "", **fields) -> None:
    rec = {"ts": int(time.time() * 1000), "event": event, "traceId": trace_id, **redact(fields)}
    sys.stdout.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
