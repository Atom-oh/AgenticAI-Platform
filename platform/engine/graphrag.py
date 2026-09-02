"""GraphRAG 질의 엔진 (SPEC v2 S1 · §5-5).

파이프라인: ① 의도 분해(LLM) → ② Seed 노드 선택 → ③ 그래프 순회 →
④ Context 조립 → ⑤ 생성 → ⑥ 근거 검증.

출력에 순회 경로(노드/엣지 배열)와 근거 노드 ID, Seed 선택 신뢰도를 포함한다 —
Seed가 틀리면 이후가 무의미하다는 것을 지표로 보여주기 위함.
v2: 순회 경로에 Regulation ← PolicyRule → Screen (CONSTRAINS/DERIVED_FROM) 이 포함되고,
Context 에 "[정책 규칙 N건]" 블록이 들어간다. 생성 답변은 컴포넌트 목록까지 요구한다.
"""
from __future__ import annotations

import json
import re
import time

from engine import bedrock
from graph.store import GraphStore, ImpactResult

DECOMPOSE_SYSTEM = """당신은 은행 내규 질의를 그래프 질의로 분해하는 분석기입니다.
질문에서 다음을 JSON으로만 추출하세요 (설명 금지):
{"intent": "impact_analysis|lookup|other",
 "entity_keywords": ["규정을 특정할 핵심 명사구들"],
 "wanted": ["products","screens","components","departments","documents","policyRules"] 중 질문이 요구한 것}
규정 개정의 영향 범위를 묻는 질문이면 intent는 impact_analysis 입니다."""

# 순회 결과에서 인용될 수 있는 노드 ID 접두어 (근거 검증용). D- 는 Department.
ID_PATTERN = re.compile(r"\b(?:REG|PRD|SCR|CMP|CND|DOC|TPL|POL|PAT|PRC|SM|D)-[A-Za-z0-9-]+\b")

# 시각화 노드 순서: 프론트가 노드 수를 잘라 그릴 때(Condition 제외 후 상위 N개) 소규모 그룹(규정·정책규칙·상품·
# 부서·문서)이 먼저 남고, 대규모 그룹(화면·컴포넌트)이 뒤에서 잘리도록 싣는다.
_GRAPH_ORDER = [("regulation", "Regulation"), ("policy_rules", "PolicyRule"), ("products", "Product"),
                ("departments", "Department"), ("documents", "Document"), ("screens", "Screen"),
                ("components", "Component"), ("conditions", "Condition")]
# 시각화 엣지 우선순위: 경로의 뼈대(규정→정책규칙→화면, 상품→화면, 소유·참조)를 먼저, 대량의 USES 를 마지막에.
_EDGE_PRIORITY = {"CONSTRAINS": 0, "SOLD_VIA": 1, "REFERENCES": 2, "OWNED_BY": 3,
                  "DERIVED_FROM": 4, "HAS_CONDITION": 5, "USES": 6}


def _decompose(query: str) -> tuple[dict, dict]:
    text, usage = bedrock.generate(DECOMPOSE_SYSTEM, query, max_tokens=300)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}, usage
    except json.JSONDecodeError:
        return {"intent": "other", "entity_keywords": [], "wanted": []}, usage


def _select_seed(store: GraphStore, keywords: list[str]) -> tuple[dict | None, float, list[dict]]:
    """키워드로 Regulation seed를 선택한다. 반환: (seed, 신뢰도, 후보 목록)."""
    regs = store.find_by_label("Regulation")
    scored = []
    for r in regs:
        title = r.props["title"]
        score = 0.0
        for kw in keywords:
            k = kw.replace(" ", "")
            t = title.replace(" ", "")
            if k and k in t:
                score += len(k) / max(len(t), 1)
            else:  # 부분 형태소 겹침
                score += sum(1 for i in range(len(k) - 1) if k[i:i + 2] in t) * 0.05
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return None, 0.0, []
    top = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    confidence = round(min(1.0, top / (top + second + 1e-9) * (1 if top >= 0.3 else top / 0.3)), 3)
    candidates = [{"code": r.props["code"], "title": r.props["title"],
                   "score": round(s, 3)} for s, r in scored[:5]]
    return {"code": scored[0][1].props["code"], "title": scored[0][1].props["title"]}, confidence, candidates


def _policy_screen_ids(r: ImpactResult) -> set[str]:
    """정책규칙이 제약하는 화면 ID — 순회 경로의 CONSTRAINS 엣지에서 읽는다 (재조회 없음)."""
    return {e.dst for e in r.path_edges if e.rel == "CONSTRAINS"}


def _assemble_context(r: ImpactResult) -> str:
    def block(name: str, nodes, keys, mark=None):
        lines = []
        for n in nodes[:15]:
            line = f"- {' · '.join(str(n.props.get(k, '')) for k in keys)}"
            if mark and n.id in mark:
                line += " (정책규칙 제약)"
            lines.append(line)
        more = f"\n  (외 {len(nodes) - 15}건)" if len(nodes) > 15 else ""
        return f"[{name} {len(nodes)}건]\n" + "\n".join(lines) + more

    reg = r.regulation
    pol_screens = _policy_screen_ids(r)
    # 정책규칙 제약 화면을 먼저 보여 15건 미리보기에서 잘리지 않게 한다
    screens = sorted(r.screens, key=lambda n: (n.id not in pol_screens, n.id))
    return "\n\n".join([
        f"[대상 규정] {reg.props['code']} {reg.props['title']} ({reg.props['article']}, "
        f"v{reg.props['version']}, 시행 {reg.props['effectiveDate']})",
        block("정책 규칙", r.policy_rules, ["ruleId", "title", "ruleType", "severity"]),
        block("영향 조건", r.conditions, ["conditionId", "type", "operator", "value", "unit"]),
        block("영향 상품", r.products, ["productCode", "name", "category", "status"]),
        block("영향 화면", screens, ["screenId", "name", "channel"], mark=pol_screens),
        block("영향 컴포넌트", r.components, ["componentId", "version", "approvalStatus", "owner"]),
        block("담당 부서", r.departments, ["deptCode", "name", "role"]),
        block("수정 필요 문서", r.documents, ["docId", "title", "type"]),
    ])


GENERATE_SYSTEM = """당신은 아톰은행 규정 영향 분석 도우미입니다. 제공된 그래프 순회 결과만
근거로 한국어로 답하세요. 구조: ① 한 줄 요약(정책규칙 N건·영향 상품 N개·화면 N개·컴포넌트 N개·부서 N개·문서 N개)
② 정책규칙/상품/화면/컴포넌트/부서/문서별 목록(각 항목에 괄호로 노드 ID 표기, 정책규칙이 제약하는 화면은 그 표시를 유지)
③ 권고 조치 2~3개.
순회 결과에 없는 항목을 만들어내지 마세요. 숫자는 순회 결과의 건수만 사용하세요."""


def _graph_payload(r: ImpactResult) -> dict:
    """시각화용 경로 (노드/엣지 배열). 엣지는 (src,rel,dst) 중복을 제거하고 400건으로 자른다."""
    path_nodes: dict[str, dict] = {}
    for attr, ntype in _GRAPH_ORDER:
        group = [r.regulation] if attr == "regulation" else getattr(r, attr)
        for n in group:
            path_nodes[n.id] = {"id": n.id, "label": ntype,
                                "name": n.props.get("name") or n.props.get("title") or n.id}
    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for e in r.path_edges:
        key = (e.src, e.rel, e.dst)
        if key in seen or e.src not in path_nodes or e.dst not in path_nodes:
            continue
        seen.add(key)
        edges.append({"src": e.src, "rel": e.rel, "dst": e.dst})
    # PolicyRule → Regulation 의 DERIVED_FROM 은 뼈대이므로 Condition 의 DERIVED_FROM 보다 앞에 둔다
    pol_ids = {n.id for n in r.policy_rules}
    edges.sort(key=lambda e: (-1 if (e["rel"] == "DERIVED_FROM" and e["src"] in pol_ids)
                              else _EDGE_PRIORITY.get(e["rel"], 9)))
    return {"nodes": list(path_nodes.values()), "edges": edges[:400]}


def _valid_ids(r: ImpactResult) -> set[str]:
    ids = {n.id for group in (r.conditions, r.products, r.screens, r.components,
                              r.departments, r.documents, r.policy_rules) for n in group}
    ids.add(r.regulation.id)
    return ids


def prepare(query: str, store: GraphStore) -> dict:
    """생성 직전까지 수행 — 스트리밍용. meta(seed/counts/graph)와 프롬프트를 반환."""
    timing: dict = {}
    t0 = time.time()
    intent, _ = _decompose(query)
    timing["decompose_ms"] = int((time.time() - t0) * 1000)
    seed, confidence, candidates = _select_seed(store, intent.get("entity_keywords", []))
    if not seed:
        return {"error": "질문에서 대상 규정을 특정하지 못했습니다.", "intent": intent,
                "seedCandidates": candidates, "timing": timing}
    t0 = time.time()
    r = store.impact_of_regulation(seed["code"])
    timing["traverse_ms"] = int((time.time() - t0) * 1000)
    return {
        "intent": intent, "seed": seed, "seedConfidence": confidence,
        "seedCandidates": candidates, "counts": r.counts(),
        "graph": _graph_payload(r),
        "timing": timing, "system": GENERATE_SYSTEM,
        "user": f"질문: {query}\n\n그래프 순회 결과:\n{_assemble_context(r)}",
    }


def answer(query: str, store: GraphStore) -> dict:
    """GraphRAG 전체 파이프라인. S1 우측 패널의 응답."""
    timing: dict = {}
    t0 = time.time()
    intent, usage1 = _decompose(query)
    timing["decompose_ms"] = int((time.time() - t0) * 1000)

    seed, confidence, candidates = _select_seed(store, intent.get("entity_keywords", []))
    if not seed:
        return {"engine": "graph-rag", "answer": "질문에서 대상 규정을 특정하지 못했습니다.",
                "intent": intent, "seed": None, "seedConfidence": 0.0,
                "seedCandidates": [], "timing": timing}

    t0 = time.time()
    r = store.impact_of_regulation(seed["code"])
    timing["traverse_ms"] = int((time.time() - t0) * 1000)

    context = _assemble_context(r)
    t0 = time.time()
    text, usage2 = bedrock.generate(GENERATE_SYSTEM, f"질문: {query}\n\n그래프 순회 결과:\n{context}", max_tokens=2000)
    timing["generate_ms"] = int((time.time() - t0) * 1000)

    # ⑥ 근거 검증: 답변에 인용된 노드 ID가 실제 순회 결과에 있는지 확인
    valid_ids = _valid_ids(r)
    cited = set(ID_PATTERN.findall(text))
    hallucinated = sorted(cited - valid_ids)

    return {
        "engine": "graph-rag",
        "answer": text,
        "intent": intent,
        "seed": seed,
        "seedConfidence": confidence,
        "seedCandidates": candidates,
        "counts": r.counts(),
        "graph": _graph_payload(r),
        "evidenceNodeIds": sorted(valid_ids & cited),
        "hallucinatedIds": hallucinated,
        "timing": timing,
        "usage": {"decompose": usage1, "generate": usage2},
    }
