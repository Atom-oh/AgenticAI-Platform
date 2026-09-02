"""기준선 컴포넌트 픽스처 — Registry 모듈(registry.api)을 import 할 수 없을 때만 사용한다.

CONTRACTS §3 레코드 형태와 같다. 시연(배포)에서는 Registry 모듈이 있으므로 이 목록은 쓰이지 않으며,
쓰이는 경우 UI에 "픽스처(Registry 미연결)"로 표기된다 — 승인 상태를 흉내내는 용도가 아니다.
Button v3은 PENDING_APPROVAL 이므로 승인 목록(approved())에는 나오지 않는다 (S3 반전 시연의 기준선).
"""
from __future__ import annotations

import copy

_TS = 1756800000000  # 2025-09-02 (합성 타임스탬프)


def _rec(name: str, version: str, status: str, description: str, props: dict, required: list,
         superseded_by: str = "", tags: list = None) -> dict:
    return {
        "name": name, "recordVersion": version, "recordType": "CUSTOM", "subtype": "COMPONENT",
        "status": status, "description": description, "owner": "UI플랫폼팀", "tags": tags or ["ui"],
        "payload": {
            "module": "@atom/ui/" + _kebab(name),
            "exportName": name,
            "propsSchema": {"type": "object", "properties": props, "required": required},
            "supersededBy": superseded_by or None,
        },
        "createdAt": _TS, "updatedAt": _TS, "updatedBy": "demo@atomai.click",
    }


def _kebab(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


FIXTURE: list = [
    _rec("Button", "v2", "APPROVED", "기본 버튼 (v2 — label/kind)", {
        "label": {"type": "string", "description": "버튼 문구(한국어 동사형)"},
        "kind": {"type": "string", "enum": ["primary", "secondary", "ghost"]},
        "disabled": {"type": "boolean"},
        "onClick": {"type": "function"},
    }, ["label"], superseded_by="v3", tags=["form", "action"]),
    _rec("Button", "v3", "PENDING_APPROVAL", "버튼 v3 — variant/tone/size 토큰 도입 (승인 대기)", {
        "label": {"type": "string"},
        "variant": {"type": "string", "enum": ["primary", "secondary", "danger", "link"]},
        "tone": {"type": "string", "enum": ["neutral", "brand", "critical"]},
        "size": {"type": "string", "enum": ["sm", "md", "lg"]},
        "disabled": {"type": "boolean"},
        "onClick": {"type": "function"},
    }, ["label", "variant"], tags=["form", "action"]),
    _rec("DataTable", "v1", "APPROVED", "데이터 표 — caption 필수, 숫자 셀 우측 정렬", {
        "caption": {"type": "string", "description": "표 제목 (KWCAG 5.3.1)"},
        "columns": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "header": {"type": "string"},
            "align": {"type": "string", "enum": ["left", "right", "center"]},
            "width": {"type": "number"}}, "required": ["key", "header"]}},
        "rows": {"type": "array", "items": {"type": "object"}},
        "rowKey": {"type": "string"},
        "emptyText": {"type": "string"},
    }, ["caption", "columns", "rows"], tags=["data"]),
    _rec("Badge", "v1", "APPROVED", "상태 배지 (role=status) — tone 규칙 P-4", {
        "label": {"type": "string"},
        "tone": {"type": "string", "enum": ["success", "warning", "danger", "neutral", "info"]},
    }, ["label"], tags=["status"]),
    _rec("Card", "v1", "APPROVED", "섹션 카드 (aria-label=title, h2)", {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "children": {"type": "node"},
        "actions": {"type": "node"},
    }, ["title"], tags=["layout"]),
    _rec("FormField", "v1", "APPROVED", "레이블-입력 연결 래퍼 (label htmlFor)", {
        "label": {"type": "string"},
        "htmlFor": {"type": "string", "description": "감싸는 입력 요소의 id 와 동일"},
        "required": {"type": "boolean"},
        "hint": {"type": "string"},
        "error": {"type": "string"},
        "children": {"type": "node"},
    }, ["label", "htmlFor"], tags=["form"]),
    _rec("Select", "v1", "APPROVED", "선택 상자 — id 필수(FormField htmlFor 연결)", {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "value": {"type": "string"},
        "options": {"type": "array", "items": {"type": "object", "properties": {
            "value": {"type": "string"}, "label": {"type": "string"}}, "required": ["value", "label"]}},
        "onChange": {"type": "function"},
        "disabled": {"type": "boolean"},
        "placeholder": {"type": "string"},
    }, ["id", "options"], tags=["form"]),
    _rec("PageHeader", "v1", "APPROVED", "화면 머리 (header/h1) — 화면당 1개", {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "breadcrumbs": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "node"},
    }, ["title"], tags=["layout"]),
    _rec("Alert", "v1", "APPROVED", "안내/오류 알림 (role=alert)", {
        "kind": {"type": "string", "enum": ["info", "success", "warning", "error"]},
        "title": {"type": "string"},
        "message": {"type": "string"},
    }, ["kind", "message"], tags=["status"]),
]


def all_records() -> list:
    return copy.deepcopy(FIXTURE)


def approved() -> list:
    """Consumer API 흉내가 아니다 — 픽스처의 APPROVED 만 걸러 로컬 테스트에 쓴다."""
    return [copy.deepcopy(r) for r in FIXTURE if r["status"] == "APPROVED"]
