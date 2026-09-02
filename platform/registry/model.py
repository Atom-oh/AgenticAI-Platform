"""Registry 레코드 스키마 + 상태 기계 (CONTRACTS §3, SPEC F4).

상태 전이 (이 외는 전부 TransitionError → 400):
  DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED
  PENDING_APPROVAL → REJECTED → DRAFT
REJECTED/DEPRECATED 전이는 사유(reason)가 필수다 — 감사 이벤트에 근거가 남아야 한다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

RECORD_TYPES = ("MCP", "AGENT", "SKILL", "CUSTOM")
STATUSES = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED")

# 허용 전이표: from → {to: 전이 이름}
TRANSITIONS: dict = {
    "DRAFT": {"PENDING_APPROVAL": "submit"},
    "PENDING_APPROVAL": {"APPROVED": "approve", "REJECTED": "reject"},
    "APPROVED": {"DEPRECATED": "deprecate"},
    "REJECTED": {"DRAFT": "revise"},
    "DEPRECATED": {},
}
# 사유 필수 전이 (도착 상태 기준)
REASON_REQUIRED = ("REJECTED", "DEPRECATED")

# 전이 한국어 라벨 (UI·감사 표시용)
TRANSITION_LABEL = {"submit": "승인 요청", "approve": "승인", "reject": "반려",
                    "deprecate": "폐기(Deprecate)", "revise": "재작성(DRAFT 복귀)"}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")
_VERSION_RE = re.compile(r"^v\d{1,4}$")


class RegistryError(Exception):
    """레지스트리 오류 공통 — code 는 HTTP 유사 코드 (400/404/409)."""
    code = 400


class ValidationError(RegistryError):
    code = 400


class TransitionError(RegistryError):
    """허용되지 않은 상태 전이."""
    code = 400


class NotFoundError(RegistryError):
    code = 404


class ConflictError(RegistryError):
    """name+recordVersion 유일성 위반 또는 낙관적 동시성 충돌."""
    code = 409


def now_ms() -> int:
    return int(time.time() * 1000)


def allowed_targets(status: str) -> list:
    """현재 상태에서 갈 수 있는 상태 목록 (UI 버튼 활성화용)."""
    return sorted(TRANSITIONS.get(status, {}).keys())


def check_transition(from_status: str, to_status: str, reason: str = "") -> str:
    """전이 검증 — 성공 시 전이 이름(submit/approve/...)을 돌려준다."""
    if from_status not in STATUSES:
        raise TransitionError(f"알 수 없는 현재 상태: {from_status}")
    if to_status not in STATUSES:
        raise TransitionError(f"알 수 없는 목표 상태: {to_status}")
    name = TRANSITIONS.get(from_status, {}).get(to_status)
    if name is None:
        allowed = ", ".join(allowed_targets(from_status)) or "없음"
        raise TransitionError(f"허용되지 않은 전이: {from_status} → {to_status} (가능: {allowed})")
    if to_status in REASON_REQUIRED and not (reason or "").strip():
        raise ValidationError(f"{to_status} 전이는 사유(reason)가 필수입니다.")
    return name


@dataclass
class Record:
    """레지스트리 레코드 (CONTRACTS §3 공통 형태). to_dict() 가 저장/전송 형태."""
    name: str
    recordVersion: str
    recordType: str
    description: str = ""
    subtype: str = ""
    status: str = "DRAFT"
    owner: str = ""
    tags: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    createdAt: int = 0
    updatedAt: int = 0
    updatedBy: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "recordVersion": self.recordVersion, "recordType": self.recordType,
                "subtype": self.subtype, "status": self.status, "description": self.description,
                "owner": self.owner, "tags": list(self.tags), "payload": dict(self.payload),
                "createdAt": self.createdAt, "updatedAt": self.updatedAt, "updatedBy": self.updatedBy}


def validate_record(rec: dict) -> dict:
    """입력 dict를 검증·정규화해 저장 가능한 레코드 dict로 만든다 (상태는 호출자가 정한다)."""
    if not isinstance(rec, dict):
        raise ValidationError("레코드는 객체여야 합니다.")
    name = str(rec.get("name", "")).strip()
    version = str(rec.get("recordVersion", "")).strip()
    rtype = str(rec.get("recordType", "")).strip().upper()
    if not _NAME_RE.match(name):
        raise ValidationError("name 은 영문/숫자/._- 1~80자여야 합니다.")
    if not _VERSION_RE.match(version):
        raise ValidationError("recordVersion 은 v1, v2 … 형식이어야 합니다.")
    if rtype not in RECORD_TYPES:
        raise ValidationError(f"recordType 은 {'/'.join(RECORD_TYPES)} 중 하나여야 합니다.")
    subtype = str(rec.get("subtype", "") or "").strip().upper()
    if rtype == "CUSTOM" and not subtype:
        raise ValidationError("CUSTOM 레코드는 subtype(예: COMPONENT)이 필요합니다.")
    tags = rec.get("tags")
    tags = [] if tags is None else tags
    if not isinstance(tags, list):  # 빈 문자열 등 falsy 비배열도 거부한다
        raise ValidationError("tags 는 문자열 배열이어야 합니다.")
    payload = rec.get("payload")
    payload = {} if payload is None else payload
    if not isinstance(payload, dict):  # [] 같은 falsy 비객체도 거부한다
        raise ValidationError("payload 는 객체여야 합니다.")
    status = str(rec.get("status", "DRAFT") or "DRAFT").upper()
    if status not in STATUSES:
        raise ValidationError(f"status 는 {'/'.join(STATUSES)} 중 하나여야 합니다.")
    return {"name": name, "recordVersion": version, "recordType": rtype, "subtype": subtype,
            "status": status, "description": str(rec.get("description", "") or "")[:2000],
            "owner": str(rec.get("owner", "") or "")[:80],
            "tags": [str(t)[:40] for t in tags][:20], "payload": payload}


def audit_event(actor: str, from_status: str, to_status: str, reason: str, ts: int,
                forced: bool = False, transition: str = "") -> dict:
    """감사 이벤트 {actor, from, to, reason, ts} (+ forced/transition 부가 필드)."""
    ev = {"actor": actor, "from": from_status, "to": to_status, "reason": (reason or "")[:500], "ts": ts,
          "transition": transition or ("force" if forced else "")}
    if forced:
        ev["forced"] = True
    return ev
