"""Registry 시연 기준선 시드(멱등) · 시연 리셋 (SPEC §6.3, CONTRACTS §3).

기준선:
  COMPONENT(CUSTOM/COMPONENT) — 온톨로지 seed/out 의 Component 노드 80건 + SUPERSEDED_BY 엣지로 버전 사슬.
    시연 오버라이드(S3 반전): Button v2 = APPROVED(supersededBy v3), Button v3 = PENDING_APPROVAL.
    여신 심사 화면용 풍부한 propsSchema: DataTable/Badge/Card/FormField/Select/PageHeader/Alert v1 = APPROVED.
  MCP   bank_platform_mcp_tools v1 (Gateway nexus-platform-tools)  APPROVED
  AGENT s1_regulation_impact / s2_mydata_advisor / screen_generation_agent / report_reader / report_writer v1 APPROVED,
        card_benefit_advisor v1 PENDING_APPROVAL (카탈로그만 — 미구현)
  SKILL bank-publishing-conventions / kwcag-accessibility v1 APPROVED (payload.path = skills/<name>.md)
seed(): 있는 레코드는 건너뛴다(reset=True 면 기준선 내용·상태로 재기록). 임베딩은 REGISTRY_EMBED=1 일 때만.
reset_demo_state(): Button v2→APPROVED, v3→PENDING_APPROVAL 를 직접 기록(감사 사유 "시연 리셋"), 나머지는 건드리지 않는다.

SPEC v2 §7 등록 대상 매핑 (ASSET_RECORD_TYPES) · MCP 서버 레코드 3건 (mcp_server_records / seed_mcp_servers):
  registry_gitlab_mcp v1 (원본 VPC 내 EKS — 데모: 미배포), figma_mcp v1 · drawio_mcp v1 (외부 SaaS — Tier 0/1 전용).
  기준선(baseline_records)과 분리된 멱등 시드다 — UX Asset Portal(handlers/portal.py) 이 §7 매핑 패널을 열 때 보장한다.
  컴포넌트 계약은 §7 상 SKILL 이지만 현재 Registry 는 CUSTOM/COMPONENT 를 유지한다(screengen Consumer API·기존 테스트 의존) —
  매핑표에 deviation=True 로 그대로 드러낸다.
"""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from registry import api
from registry.model import STATUSES
from registry.store import EMBEDDING_ATTR

ROOT = Path(__file__).resolve().parent.parent  # platform/ 또는 api-dist/
OWNER_UI = "UI플랫폼팀"
OWNER_AI = "AI플랫폼팀"

# 시연 리셋이 되돌리는 상태 (S3 반전 시연의 출발점)
DEMO_RESET_TARGETS: Dict[Tuple[str, str], str] = {("Button", "v2"): "APPROVED", ("Button", "v3"): "PENDING_APPROVAL"}
# 기준선에서 온톨로지 approvalStatus 를 덮어쓰는 항목 (여신 심사 화면 필수 컴포넌트는 반드시 승인)
STATUS_OVERRIDES: Dict[Tuple[str, str], str] = dict(DEMO_RESET_TARGETS)
STATUS_OVERRIDES.update({("Badge", "v1"): "APPROVED", ("Card", "v1"): "APPROVED", ("Select", "v1"): "APPROVED"})


# ---------- propsSchema 빌딩 블록 (JSON-schema 유사) ----------
def _s(**kw) -> dict:
    return {"type": "string", **kw}


def _enum(*vals: str, **kw) -> dict:
    return {"type": "string", "enum": list(vals), **kw}


def _b(**kw) -> dict:
    return {"type": "boolean", **kw}


def _fn(sig: str, **kw) -> dict:
    return {"type": "function", "signature": sig, **kw}


def _node(desc: str = "React 자식 요소") -> dict:
    return {"type": "node", "description": desc}


def _arr(items: dict, **kw) -> dict:
    return {"type": "array", "items": items, **kw}


def _obj(props: dict, required: List[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _schema(props: dict, required: List[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_OPTION = _obj({"value": _s(), "label": _s()}, ["value", "label"])
_COLUMN = _obj({"key": _s(description="행 객체의 필드명"), "header": _s(description="열 머리글")}, ["key", "header"])
_ITEM = _obj({"id": _s(), "label": _s()}, ["id", "label"])

# (이름, 메이저) → 풍부한 propsSchema. 없는 조합은 온톨로지의 얇은 스키마에서 합성한다.
RICH_SCHEMAS: Dict[Tuple[str, int], dict] = {
    ("Button", 1): _schema({"label": _s(), "variant": _enum("primary", "ghost"), "size": _enum("sm", "md", "lg"),
                            "onClick": _fn("() => void")}, ["label"]),
    ("Button", 2): _schema({"label": _s(description="버튼 텍스트"),
                            "kind": _enum("primary", "secondary", "danger", description="버튼 종류"),
                            "disabled": _b(), "onClick": _fn("() => void")}, ["label", "kind"]),
    ("Button", 3): _schema({"label": _s(description="버튼 텍스트"),
                            "variant": _enum("solid", "outline", "ghost", description="형태"),
                            "tone": _enum("brand", "neutral", "critical", description="색조"),
                            "size": _enum("sm", "md", "lg"), "disabled": _b(), "onClick": _fn("() => void")},
                           ["label", "variant", "tone"]),
    ("Input", 1): _schema({"label": _s(), "value": _s(), "onChange": _fn("(value: string) => void")}, ["label", "value", "onChange"]),
    ("Input", 2): _schema({"label": _s(), "value": _s(), "onChange": _fn("(value: string) => void"),
                           "placeholder": _s(), "type": _enum("text", "number", "password")}, ["label", "value", "onChange"]),
    ("Input", 3): _schema({"label": _s(), "value": _s(), "onChange": _fn("(value: string) => void"),
                           "placeholder": _s(), "type": _enum("text", "number", "password", "tel"),
                           "error": _s(description="오류 메시지(있으면 aria-invalid)"), "prefix": _s()}, ["label", "value", "onChange"]),
    ("Table", 1): _schema({"columns": _arr(_COLUMN), "rows": _arr({"type": "object"})}, ["columns", "rows"]),
    ("Table", 2): _schema({"columns": _arr(_COLUMN), "rows": _arr({"type": "object"}), "sortable": _b(),
                           "emptyText": _s()}, ["columns", "rows"]),
    ("DataTable", 1): _schema({"columns": _arr(_COLUMN, description="열 정의"), "rows": _arr({"type": "object"}, description="행 데이터"),
                               "caption": _s(description="표 설명(스크린리더용, KWCAG 필수)")}, ["columns", "rows", "caption"]),
    ("Modal", 1): _schema({"open": _b(), "title": _s(), "onClose": _fn("() => void"), "children": _node()}, ["open", "title", "onClose"]),
    ("Modal", 2): _schema({"open": _b(), "title": _s(), "onClose": _fn("() => void"), "children": _node(),
                           "size": _enum("sm", "md", "lg")}, ["open", "title", "onClose"]),
    ("DatePicker", 1): _schema({"label": _s(), "value": _s(description="ISO-8601 날짜"), "onChange": _fn("(iso: string) => void")}, ["label", "value", "onChange"]),
    ("DatePicker", 2): _schema({"label": _s(), "value": _s(description="ISO-8601 날짜"), "onChange": _fn("(iso: string) => void"),
                                "min": _s(), "max": _s()}, ["label", "value", "onChange"]),
    ("Select", 1): _schema({"label": _s(), "options": _arr(_OPTION), "value": _s(),
                            "onChange": _fn("(value: string) => void")}, ["label", "options", "value", "onChange"]),
    ("Select", 2): _schema({"label": _s(), "options": _arr(_OPTION), "value": _s(),
                            "onChange": _fn("(value: string) => void"), "searchable": _b(), "placeholder": _s()},
                           ["label", "options", "value", "onChange"]),
    ("Card", 1): _schema({"title": _s(), "children": _node()}, ["title", "children"]),
    ("Card", 2): _schema({"title": _s(), "children": _node(), "elevated": _b(), "footer": _node("하단 영역")}, ["title", "children"]),
    ("Tabs", 1): _schema({"items": _arr(_ITEM), "activeId": _s(), "onChange": _fn("(id: string) => void")}, ["items", "activeId", "onChange"]),
    ("Tabs", 2): _schema({"items": _arr(_ITEM), "activeId": _s(), "onChange": _fn("(id: string) => void"),
                          "variant": _enum("line", "pill")}, ["items", "activeId", "onChange"]),
    ("Stepper", 1): _schema({"steps": _arr(_ITEM), "current": _s(description="현재 단계 id")}, ["steps", "current"]),
    ("FileUpload", 1): _schema({"label": _s(), "accept": _s(), "onFiles": _fn("(files: File[]) => void")}, ["label", "onFiles"]),
    ("Badge", 1): _schema({"text": _s(description="배지 텍스트"),
                           "tone": _enum("neutral", "success", "warning", "critical", "info", description="색조")}, ["text", "tone"]),
    ("Toast", 1): _schema({"message": _s(), "tone": _enum("info", "success", "warning", "error"), "onDismiss": _fn("() => void")}, ["message", "tone"]),
    ("FormField", 1): _schema({"label": _s(), "htmlFor": _s(description="자식 입력의 id — 라벨 연결(KWCAG)"),
                               "children": _node("입력 컨트롤"), "hint": _s()}, ["label", "htmlFor", "children"]),
    ("PageHeader", 1): _schema({"title": _s(), "subtitle": _s()}, ["title"]),
    ("Alert", 1): _schema({"severity": _enum("info", "success", "warning", "error"), "message": _s()}, ["severity", "message"]),
}

DESCRIPTIONS: Dict[str, str] = {
    "Button": "기본 동작 버튼 — 확인·제출·취소 등 주요 액션",
    "Input": "단일 행 텍스트 입력 필드",
    "Table": "기본 표 — 열 정의와 행 데이터 표시",
    "DataTable": "데이터 표 — 열 정의·행 데이터·캡션(스크린리더용)을 받는 여신 심사 결과 목록용 표",
    "Modal": "모달 대화상자",
    "DatePicker": "날짜 선택기",
    "Select": "드롭다운 선택 — 라벨·옵션·값·변경 핸들러",
    "Card": "카드 컨테이너 — 제목과 본문 영역",
    "Tabs": "탭 내비게이션",
    "Stepper": "단계 표시기 — 심사 진행 단계 등",
    "FileUpload": "파일 업로드 영역",
    "Badge": "상태 배지 — 심사 결과(승인/보류/거절) 등 짧은 상태 텍스트",
    "Toast": "일시 알림 토스트",
    "FormField": "폼 필드 래퍼 — 라벨과 입력을 htmlFor 로 연결(KWCAG 라벨 필수)·힌트",
    "PageHeader": "페이지 헤더 — 제목·부제",
    "Alert": "인라인 알림 — 심각도(info/success/warning/error)와 메시지",
}
VERSION_NOTES: Dict[Tuple[str, str], str] = {
    ("Button", "v1"): " (폐기 — v2 로 대체)",
    ("Button", "v2"): " (시연 기준선: 승인 — v3 이 대체 예정)",
    ("Button", "v3"): " (시연 기준선: 승인 대기 — S3 반전 시연에서 승인)",
}
ROLE_TAGS: Dict[str, List[str]] = {
    "Button": ["action", "form"], "Input": ["form", "input"], "Table": ["table", "data"],
    "DataTable": ["table", "data", "loan-review", "여신심사"], "Modal": ["overlay"], "DatePicker": ["form", "date"],
    "Select": ["form", "input", "loan-review"], "Card": ["layout", "loan-review"], "Tabs": ["navigation"],
    "Stepper": ["progress"], "FileUpload": ["form", "file"], "Badge": ["status", "loan-review"], "Toast": ["feedback"],
    "FormField": ["form", "a11y", "loan-review"], "PageHeader": ["layout", "loan-review"], "Alert": ["feedback", "loan-review"],
}
# 한국어 별칭 태그 — 한국어 질의("버튼", "표")가 영문 컴포넌트명에 닿게 한다
KO_ALIASES: Dict[str, List[str]] = {
    "Button": ["버튼"], "Input": ["입력", "인풋"], "Table": ["표", "테이블"], "DataTable": ["데이터표", "표", "테이블", "목록"],
    "Modal": ["모달", "대화상자"], "DatePicker": ["날짜", "달력"], "Select": ["선택", "드롭다운"], "Card": ["카드"],
    "Tabs": ["탭"], "Stepper": ["단계"], "FileUpload": ["파일", "업로드"], "Badge": ["배지", "상태"], "Toast": ["토스트", "알림"],
    "FormField": ["폼", "필드", "라벨"], "PageHeader": ["헤더", "제목"], "Alert": ["알림", "경고"],
}
# 온톨로지에는 없지만 여신 심사 화면 생성에 필요한 승인 컴포넌트
EXTRA_COMPONENTS: List[Tuple[str, str]] = [("DataTable", "v1"), ("FormField", "v1"), ("PageHeader", "v1"), ("Alert", "v1")]


def kebab(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    return s.lower()


def _seed_dir() -> Path:
    return Path(os.environ.get("SEED_DIR") or (ROOT / "seed" / "out"))


def _major(semver: str) -> int:
    m = re.match(r"^(\d+)", str(semver or "1"))
    return int(m.group(1)) if m else 1


def _thin_to_schema(raw: str) -> dict:
    """온톨로지의 얇은 스키마 {"variant":[...],"size":[...]} → JSON-schema 유사 객체로 합성."""
    props: dict = {}
    try:
        thin = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        thin = {}
    for k, v in (thin or {}).items():
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            props[k] = _enum(*v)
        else:
            props[k] = {"type": "string"}
    props.setdefault("children", _node())
    return _schema(props, [k for k in props if k != "children"][:1])


def load_ontology_components() -> Tuple[List[dict], Dict[str, str]]:
    """seed/out 의 Component 노드와 SUPERSEDED_BY 엣지. 파일이 없으면 빈 결과."""
    d = _seed_dir()
    nodes: List[dict] = []
    edges: Dict[str, str] = {}
    np_, ep = d / "nodes.jsonl", d / "edges.jsonl"
    if np_.exists():
        with open(np_, encoding="utf-8") as f:
            for line in f:
                if '"Component"' not in line:
                    continue
                n = json.loads(line)
                if n.get("label") == "Component":
                    nodes.append(n)
    if ep.exists():
        with open(ep, encoding="utf-8") as f:
            for line in f:
                if "SUPERSEDED_BY" not in line:
                    continue
                e = json.loads(line)
                if e.get("rel") == "SUPERSEDED_BY":
                    edges[e["src"]] = e["dst"]
    return nodes, edges


def _component_record(name: str, version: str, status: str, schema: dict, superseded_by: Optional[str],
                      component_id: Optional[str], semver: str, origin: str) -> dict:
    major = _major(semver)
    desc = DESCRIPTIONS.get(name) or "합성 온톨로지 컴포넌트 (시연 볼륨용 더미 위젯 — 실제 UI 구현 없음)"
    desc += VERSION_NOTES.get((name, version), "")
    tags = ["ui", "component", kebab(name)] + ROLE_TAGS.get(name, []) + KO_ALIASES.get(name, [])
    payload = {"module": f"@atom/ui/{kebab(name)}", "exportName": name, "propsSchema": schema,
               "supersededBy": superseded_by, "componentId": component_id, "semver": semver, "origin": origin}
    return {"name": name, "recordVersion": version, "recordType": "CUSTOM", "subtype": "COMPONENT",
            "status": status, "description": desc, "owner": OWNER_UI, "tags": tags, "payload": payload,
            "_major": major}


def component_records() -> List[dict]:
    nodes, edges = load_ontology_components()
    by_id = {n["id"]: n for n in nodes}
    out: Dict[Tuple[str, str], dict] = {}
    for n in nodes:
        p = n.get("props", {})
        name, semver = str(p.get("name", "")), str(p.get("version", "1.0.0"))
        version = f"v{_major(semver)}"
        status = str(p.get("approvalStatus", "DRAFT")).upper()
        if status not in STATUSES:
            status = "DRAFT"
        status = STATUS_OVERRIDES.get((name, version), status)
        sup = edges.get(n["id"])
        sup_v = f"v{_major(by_id[sup]['props'].get('version', '1.0.0'))}" if sup in by_id else None
        schema = RICH_SCHEMAS.get((name, _major(semver))) or _thin_to_schema(p.get("propsSchema", ""))
        out[(name, version)] = _component_record(name, version, status, schema, sup_v, n["id"], semver, "ontology")
    for name, version in EXTRA_COMPONENTS:
        if (name, version) not in out:
            out[(name, version)] = _component_record(name, version, "APPROVED", RICH_SCHEMAS[(name, _major(version[1:]))],
                                                     None, None, f"{version[1:]}.0.0", "demo-baseline")
    # 시연 반전 사슬 보장: Button v2 → v3
    if ("Button", "v2") in out:
        out[("Button", "v2")]["payload"]["supersededBy"] = "v3"
    recs = sorted(out.values(), key=lambda r: (r["name"].lower(), r["_major"]))
    for r in recs:
        r.pop("_major", None)
    return recs


# ---------- MCP / AGENT / SKILL ----------
_MCP_DOC_FALLBACK = ("아톰은행 플랫폼 툴 — AgentCore Gateway Lambda 타깃 (중앙 MCP 연동).\n\n"
                     "중앙 MCP(Gateway `nexus-platform-tools`, Cognito JWT 인바운드)에 노출되는 툴:\n"
                     "  analyze_regulation_impact(reg_code|question) — 규정 개정 영향 4-hop 순회 (S1 엔진과 동일 데이터)\n"
                     "  resolve_metric(term)                          — Semantic Layer 지표 해석 (SQL 템플릿 반환)\n"
                     "  list_regulations(query)                       — 규정 목록/검색\n\n"
                     "읽기 전용·합성데이터. AWS 리소스 접근 없음 (데이터는 배포 패키지에 동봉).")


def mcp_description() -> str:
    """mcp/bank_tools.py 모듈 docstring — 파일이 배포 패키지에 없으면 동일 문구의 사본을 쓴다."""
    p = ROOT / "mcp" / "bank_tools.py"
    try:
        if p.exists():
            doc = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")))
            if doc:
                return doc
    except Exception:  # noqa: BLE001
        pass
    return _MCP_DOC_FALLBACK


MCP_TOOLS = [
    {"name": "analyze_regulation_impact", "args": ["reg_code", "question"],
     "description": "규정 개정 영향 4-hop 순회 (S1 엔진과 동일 데이터)"},
    {"name": "resolve_metric", "args": ["term"], "description": "Semantic Layer 지표 해석 (SQL 템플릿 반환)"},
    {"name": "list_regulations", "args": ["query"], "description": "규정 목록/검색"},
]


def platform_records() -> List[dict]:
    model_id = os.environ.get("GEN_MODEL", "apac.anthropic.claude-sonnet-4-20250514-v1:0")
    return [
        {"name": "bank_platform_mcp_tools", "recordVersion": "v1", "recordType": "MCP", "subtype": "GATEWAY",
         "status": "APPROVED", "description": mcp_description(), "owner": OWNER_AI,
         "tags": ["mcp", "gateway", "규정", "지표", "agentcore"],
         "payload": {"gateway": "nexus-platform-tools", "tools": MCP_TOOLS, "auth": "Cognito JWT (inbound)",
                     "target": "Lambda (mcp/bank_tools.py)", "readOnly": True}},
        {"name": "s1_regulation_impact", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
         "status": "APPROVED", "owner": OWNER_AI, "tags": ["s1", "graphrag", "규정", "영향분석", "온톨로지"],
         "description": "규정 영향 분석 에이전트 — 자연어 질문을 의도 분해 → Seed 규정 선택 → 4-hop 그래프 순회 → "
                        "근거 검증까지 수행하고, 동일 질문의 Vector RAG(하이브리드+리랭커) 결과와 나란히 비교한다 (S1).",
         "payload": {"entry": "handlers.s1", "scenario": "S1", "engines": ["engine.graphrag", "engine.vectorrag"],
                     "model": model_id, "streaming": True}},
        {"name": "s2_mydata_advisor", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
         "status": "APPROVED", "owner": OWNER_AI, "tags": ["s2", "마이데이터", "우대금리", "계산엔진", "마스킹"],
         "description": "마이데이터 상담 에이전트 — 입력 Guardrails → Semantic Layer → 정확 조회(SQL) → 결정론적 계산엔진 → "
                        "마스킹 게이트 → Bedrock 설명 생성 → 출력 Guardrails → 재식별. 숫자는 LLM 이 만들지 않는다 (S2).",
         "payload": {"entry": "handlers.s2", "scenario": "S2", "plane": ["/s2/prepare", "/s2/finalize"],
                     "guardrails": "Bedrock Guardrails INPUT/OUTPUT", "model": model_id, "streaming": True}},
        {"name": "screen_generation_agent", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
         "status": "APPROVED", "owner": OWNER_UI, "tags": ["s3", "화면생성", "react", "registry", "게이트"],
         "description": "화면 생성 에이전트 — Registry Consumer API(APPROVED 컴포넌트만, 정확 조회)와 퍼블리싱 규약 Skills 를 "
                        "컨텍스트로 React+TS 코드를 생성하고 빌드/타입/린트/KWCAG 게이트를 실행한다. 실패 시 1회만 재생성 (S3).",
         "payload": {"entry": "handlers.screengen", "scenario": "S3",
                     "consumes": "registry.api.list_approved(subtype='COMPONENT')",
                     "skills": ["bank-publishing-conventions", "kwcag-accessibility"],
                     "gates": ["build", "types", "lint", "a11y", "visual"], "maxRegenerate": 1, "model": model_id}},
        {"name": "report_reader", "recordVersion": "v1", "recordType": "AGENT", "subtype": "WORKER",
         "status": "APPROVED", "owner": OWNER_AI, "tags": ["f7", "보고서", "reader", "인젝션", "iam"],
         "description": "보고서 Reader — 외부 웹 콘텐츠만 읽어 구조화 JSON 요약을 만든다. 내부 조회 도구 권한이 IAM 역할로 "
                        "차단되어 있어 프롬프트 인젝션이 내부 문서에 닿지 못한다 (F7).",
         "payload": {"entry": "READER_FN (별도 Lambda·별도 IAM 역할)", "scenario": "F7",
                     "iam": "bedrock:InvokeModel 만 — 내부 조회 Lambda invoke 없음", "outputs": "구조화 JSON 요약"}},
        {"name": "report_writer", "recordVersion": "v1", "recordType": "AGENT", "subtype": "WORKER",
         "status": "APPROVED", "owner": OWNER_AI, "tags": ["f7", "보고서", "writer", "내부문서"],
         "description": "보고서 Writer — Reader 의 구조화 요약과 내부 문서 검색 결과로 보고서를 작성한다. 외부 URL 원문에는 "
                        "접근하지 않는다 (F7).",
         "payload": {"entry": "WRITER_FN (별도 Lambda·별도 IAM 역할)", "scenario": "F7",
                     "inputs": "Reader 구조화 JSON + 내부 Document 노드", "externalFetch": False}},
        {"name": "card_benefit_advisor", "recordVersion": "v1", "recordType": "AGENT", "subtype": "PIPELINE",
         "status": "PENDING_APPROVAL", "owner": "카드기획부", "tags": ["카드", "혜택", "전월실적", "승인대기"],
         "description": "카드 혜택 상담 에이전트 — 전월실적·혜택 조건을 Semantic Layer 지표로 해석해 안내. "
                        "미구현: 카탈로그 등록만 된 승인 대기 예시이며 실행 경로가 없다.",
         "payload": {"entry": None, "implemented": False, "note": "미구현 — 승인 워크플로우 시연용 카탈로그 항목"}},
        {"name": "bank-publishing-conventions", "recordVersion": "v1", "recordType": "SKILL", "subtype": "MARKDOWN",
         "status": "APPROVED", "owner": OWNER_UI, "tags": ["skill", "퍼블리싱", "규약", "react"],
         "description": "은행 퍼블리싱 규약 — 화면 생성 에이전트가 컨텍스트로 로드하는 레이아웃·네이밍·import 규약 마크다운.",
         "payload": {"path": "skills/bank-publishing-conventions.md", "format": "markdown"}},
        {"name": "kwcag-accessibility", "recordVersion": "v1", "recordType": "SKILL", "subtype": "MARKDOWN",
         "status": "APPROVED", "owner": OWNER_UI, "tags": ["skill", "접근성", "kwcag", "a11y"],
         "description": "KWCAG 접근성 규약 — 라벨 연결·대체 텍스트·키보드 접근·색 대비 등 생성 코드가 지켜야 하는 항목.",
         "payload": {"path": "skills/kwcag-accessibility.md", "format": "markdown"}},
    ]


def baseline_records() -> List[dict]:
    return component_records() + platform_records()


# ---------- SPEC v2 §7 등록 대상 매핑 ----------
# asset(사내 자산) → recordType/subtype/원본. current 는 이 코드베이스의 실제 상태 — 명세와 다르면 deviation=True 로 표시한다.
ASSET_RECORD_TYPES: List[dict] = [
    {"key": "component_contract", "asset": "컴포넌트 계약 (props · variants)", "recordType": "SKILL", "subtype": None,
     "origin": "Git", "current": "CUSTOM / COMPONENT", "deviation": True,
     "note": "§7 은 SKILL — 현재 Registry 는 CUSTOM/COMPONENT 유지 (screengen Consumer API list_approved(subtype='COMPONENT') 와 "
             "기존 테스트가 의존). 이번 웨이브에서 미변경."},
    {"key": "screen_spec", "asset": "화면 스펙 (MDX / JSON)", "recordType": "CUSTOM", "subtype": "SCREEN_SPEC",
     "origin": "Git", "current": "Portal Publish → CUSTOM/SCREEN_SPEC (DRAFT)", "deviation": False,
     "note": "온톨로지 Screen + ScreenMeta 를 스펙 payload 로 등록. MDX 원본 연동은 미구현."},
    {"key": "pattern", "asset": "패턴 (Pattern Library)", "recordType": "CUSTOM", "subtype": "PATTERN",
     "origin": "Git", "current": "Portal Publish → CUSTOM/PATTERN (DRAFT)", "deviation": False,
     "note": "§7 표에는 없는 확장 — Pattern 노드를 CUSTOM/PATTERN 으로 등록."},
    {"key": "registry_gitlab_mcp", "asset": "레지스트리 · GitLab MCP 서버", "recordType": "MCP", "subtype": "SERVER",
     "origin": "VPC 내 EKS", "current": "registry_gitlab_mcp v1 — 데모: 미배포 (카탈로그 레코드만)", "deviation": False,
     "note": "운영은 VPC 프라이빗 서브넷의 EKS 에 배포. 데모 환경에는 EKS 가 없어 실행 경로가 없다."},
    {"key": "skills", "asset": "Skills (은행 퍼블리싱 규약)", "recordType": "SKILL", "subtype": "MARKDOWN",
     "origin": "Git · 마크다운", "current": "bank-publishing-conventions v1 · kwcag-accessibility v1", "deviation": False,
     "note": None},
    {"key": "screen_agent", "asset": "화면 생성 에이전트", "recordType": "AGENT", "subtype": "PIPELINE",
     "origin": "AgentCore Runtime", "current": "screen_generation_agent v1", "deviation": False, "note": None},
    {"key": "design_mcp", "asset": "Figma · draw.io MCP", "recordType": "MCP", "subtype": "SERVER",
     "origin": "외부 SaaS", "current": "figma_mcp v1 · drawio_mcp v1 — Tier 0/1 전용 · 데모: 에이전트 미연결",
     "deviation": False,
     "note": "개인데이터가 흐르지 않는 디자인 자산 경로에서만 사용 (SPEC v2 §11-4)."},
]

MCP_SERVER_NAMES = ("registry_gitlab_mcp", "figma_mcp", "drawio_mcp")


def mcp_server_records() -> List[dict]:
    """§7 MCP 서버 레코드 — 전부 APPROVED(거버넌스 상태). 배포/연결 여부는 payload·description 에 사실대로 적는다."""
    return [
        {"name": "registry_gitlab_mcp", "recordVersion": "v1", "recordType": "MCP", "subtype": "SERVER",
         "status": "APPROVED", "owner": OWNER_AI,
         "tags": ["mcp", "gitlab", "registry", "vpc", "eks", "미배포"],
         "description": "레지스트리 · GitLab MCP 서버 — Registry 조회와 GitLab(컴포넌트 계약·화면 스펙 원본) 접근을 MCP 도구로 노출. "
                        "운영 원본: VPC 프라이빗 서브넷 내 EKS. 데모: 미배포 — 카탈로그 레코드만 있으며 실행 경로가 없다.",
         "payload": {"origin": "VPC 내 EKS", "deployed": False, "demo": "미배포 (EKS 미구성)",
                     "transport": "streamable-http", "auth": "IAM SigV4 (Gateway 인바운드)",
                     "plannedTools": ["registry.search", "registry.get_record", "gitlab.read_file", "gitlab.list_tree"],
                     "dataClass": "메타데이터 · 소스 (개인데이터 없음)"}},
        {"name": "figma_mcp", "recordVersion": "v1", "recordType": "MCP", "subtype": "SERVER",
         "status": "APPROVED", "owner": "UX디자인부",
         "tags": ["mcp", "figma", "design", "saas", "tier01"],
         "description": "Figma MCP — 외부 SaaS(Figma) 디자인 컨텍스트·컴포넌트 매핑 도구. Tier 0/1 전용: 개인데이터가 흐르지 않는 "
                        "디자인 자산 경로에서만 사용한다 (§11-4). 데모: 플랫폼 에이전트에 연결되지 않음 — 카탈로그 레코드.",
         "payload": {"origin": "외부 SaaS", "vendor": "Figma", "tier": "Tier 0/1 전용", "connected": False,
                     "dataClass": "디자인 자산 (개인데이터 없음)", "egress": "인터넷 (VPC 외부)"}},
        {"name": "drawio_mcp", "recordVersion": "v1", "recordType": "MCP", "subtype": "SERVER",
         "status": "APPROVED", "owner": "UX디자인부",
         "tags": ["mcp", "drawio", "diagram", "saas", "tier01"],
         "description": "draw.io MCP — 외부 SaaS(diagrams.net) 다이어그램 생성·편집 도구. Tier 0/1 전용: 개인데이터가 흐르지 않는 "
                        "설계 문서 경로에서만 사용한다 (§11-4). 데모: 플랫폼 에이전트에 연결되지 않음 — 카탈로그 레코드.",
         "payload": {"origin": "외부 SaaS", "vendor": "draw.io (diagrams.net)", "tier": "Tier 0/1 전용", "connected": False,
                     "dataClass": "설계 다이어그램 (개인데이터 없음)", "egress": "인터넷 (VPC 외부)"}},
    ]


def seed_mcp_servers(actor: str, embed: bool = True) -> dict:
    """§7 MCP 서버 레코드 3건 멱등 시드 — 있으면 건너뛴다. 기준선(seed)과 분리되어 기존 카운트 계약을 바꾸지 않는다."""
    store = api.get_store()
    res = {"created": 0, "skipped": 0, "embedded": 0, "embedFailed": 0, "total": 0, "names": list(MCP_SERVER_NAMES)}
    want_embed = embed and api.embeddings_enabled()
    for rec in mcp_server_records():
        res["total"] += 1
        if store.get(rec["name"], rec["recordVersion"]) is not None:
            res["skipped"] += 1
            continue
        saved = api.create_record(rec, actor, status=rec["status"], reason="§7 MCP 매핑 시드", embed=False)
        res["created"] += 1
        if want_embed:
            if api.ensure_embedding(saved):
                res["embedded"] += 1
            else:
                res["embedFailed"] += 1
    return res


# ---------- 시드 / 리셋 ----------
def seed(actor: str, reset: bool = False, embed: bool = True) -> dict:
    """기준선 시드 (멱등). reset=True 면 기존 레코드도 기준선 내용·상태로 재기록(감사 이벤트 기록)."""
    store = api.get_store()
    existing = {(r["name"], r["recordVersion"]): r for r in store.all_records()}
    res = {"created": 0, "updated": 0, "skipped": 0, "embedded": 0, "embedFailed": 0, "statusReset": 0,
           "total": 0, "backend": store.backend, "embeddingsEnabled": api.embeddings_enabled()}
    want_embed = embed and api.embeddings_enabled()
    for rec in baseline_records():
        res["total"] += 1
        k = (rec["name"], rec["recordVersion"])
        cur = existing.get(k)
        if cur is not None:
            if not reset:
                res["skipped"] += 1
                continue
            store.rewrite(rec["name"], rec["recordVersion"],
                          {f: rec[f] for f in ("description", "payload", "tags", "owner", "subtype", "recordType")}, actor)
            if cur.get("status") != rec["status"]:
                store.force_status(rec["name"], rec["recordVersion"], rec["status"], actor, "기준선 재설정 (seed reset)")
                res["statusReset"] += 1
            res["updated"] += 1
            saved = {**cur, **rec}
            need_embed = want_embed and not cur.get(EMBEDDING_ATTR)
        else:
            saved = api.create_record(rec, actor, status=rec["status"], reason="기준선 시드", embed=False)
            res["created"] += 1
            need_embed = want_embed
        if need_embed:
            if api.ensure_embedding(saved):
                res["embedded"] += 1
            else:
                res["embedFailed"] += 1
    return res


def reset_demo_state(actor: str) -> dict:
    """시연 리셋 — Button v2 APPROVED / v3 PENDING_APPROVAL 로 직접 복원. 기준선이 없으면 먼저 시드한다."""
    store = api.get_store()
    seeded = None
    if any(store.get(n, v) is None for (n, v) in DEMO_RESET_TARGETS):
        seeded = seed(actor)
    changed, states = [], {}
    for (name, version), target in DEMO_RESET_TARGETS.items():
        cur = store.get(name, version)
        if cur is None:
            states[f"{name} {version}"] = "없음"
            continue
        if cur.get("status") != target:
            rec, ev = store.force_status(name, version, target, actor, "시연 리셋")
            changed.append({"name": name, "recordVersion": version, "from": ev["from"], "to": ev["to"]})
        states[f"{name} {version}"] = target
    return {"changed": changed, "records": states, "seeded": seeded,
            "consumerComponents": len(api.list_approved(subtype="COMPONENT"))}
