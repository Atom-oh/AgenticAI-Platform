"""구조화 로그 (SPEC §10 관측성, §12.3).

CloudWatch에는 메트릭과 traceId만 남긴다 — 프롬프트 원문·개인데이터 필드는 절대 넣지 않는다.
금지 키(prompt/query/answer/payload/text/email)는 길이·해시로 치환한다.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time

_FORBIDDEN = {"prompt", "query", "answer", "payload", "maskedPayload", "text", "email",
              "system", "user", "message", "content"}


def hash8(value: str) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()[:8]


def redact(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k in _FORBIDDEN and isinstance(v, str):
            out[f"{k}Hash"] = hash8(v)
            out[f"{k}Len"] = len(v)
        else:
            out[k] = v
    return out


def log_event(event: str, trace_id: str = "", **fields) -> None:
    rec = {"ts": int(time.time() * 1000), "event": event, "traceId": trace_id, **redact(fields)}
    sys.stdout.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
