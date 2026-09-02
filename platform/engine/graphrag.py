"""GraphRAG 질의 엔진 (SPEC F1).

파이프라인: ① 의도 분해(LLM) → ② Seed 노드 선택 → ③ 그래프 순회 →
④ Context 조립 → ⑤ 생성 → ⑥ 근거 검증.

출력에 순회 경로(노드/엣지 배열)와 근거 노드 ID, Seed 선택 신뢰도를 포함한다 —
Seed가 틀리면 이후가 무의미하다는 것을 지표로 보여주기 위함 (F1).
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
 "wanted": ["products","screens","departments","documents","components"] 중 질문이 요구한 것}
규정 개정의 영향 범위를 묻는 질문이면 intent는 impact_analysis 입니다."""


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


def _assemble_context(r: ImpactResult) -> str:
    def block(name: str, nodes, keys):
        lines = [f"- {' · '.join(str(n.props.get(k, '')) for k in keys)}" for n in nodes[:15]]
        more = f"\n  (외 {len(nodes) - 15}건)" if len(nodes) > 15 else ""
        return f"[{name} {len(nodes)}건]\n" + "\n".join(lines) + more

    reg = r.regulation
    return "\n\n".join([
        f"[대상 규정] {reg.props['code']} {reg.props['title']} ({reg.props['article']}, "
        f"v{reg.props['version']}, 시행 {reg.props['effectiveDate']})",
        block("영향 조건", r.conditions, ["conditionId", "type", "operator", "value", "unit"]),
        block("영향 상품", r.products, ["productCode", "name", "category", "status"]),
        block("영향 화면", r.screens, ["screenId", "name", "channel"]),
        block("영향 컴포넌트", r.components, ["componentId", "version", "approvalStatus"]),
        block("담당 부서", r.departments, ["deptCode", "name", "role"]),
        block("수정 필요 문서", r.documents, ["docId", "title", "type"]),
    ])


GENERATE_SYSTEM = """당신은 아톰은행 규정 영향 분석 도우미입니다. 제공된 그래프 순회 결과만
근거로 한국어로 답하세요. 구조: ① 한 줄 요약(영향 상품 N개·화면 N개·부서 N개·문서 N개)
② 상품/화면/부서/문서별 목록(각 항목에 괄호로 노드 ID 표기) ③ 권고 조치 2~3개.
순회 결과에 없는 항목을 만들어내지 마세요. 숫자는 순회 결과의 건수만 사용하세요."""


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
    valid_ids = {n.id for group in (r.conditions, r.products, r.screens,
                                    r.components, r.departments, r.documents) for n in group}
    valid_ids.add(r.regulation.id)
    cited = set(re.findall(r"\b(?:REG|PRD|SCR|CMP|CND|DOC|TPL|D)-[A-Za-z0-9-]+\b", text))
    hallucinated = sorted(cited - valid_ids)

    # 시각화용 경로 (노드/엣지 배열)
    path_nodes = {}
    for group, ntype in [( [r.regulation], "Regulation"), (r.conditions, "Condition"),
                         (r.products, "Product"), (r.screens, "Screen"),
                         (r.components, "Component"), (r.departments, "Department"),
                         (r.documents, "Document")]:
        for n in group:
            path_nodes[n.id] = {"id": n.id, "label": ntype,
                                "name": n.props.get("name") or n.props.get("title") or n.id}
    edges = [{"src": e.src, "rel": e.rel, "dst": e.dst} for e in r.path_edges
             if e.src in path_nodes and e.dst in path_nodes]

    return {
        "engine": "graph-rag",
        "answer": text,
        "intent": intent,
        "seed": seed,
        "seedConfidence": confidence,
        "seedCandidates": candidates,
        "counts": r.counts(),
        "graph": {"nodes": list(path_nodes.values()), "edges": edges[:400]},
        "evidenceNodeIds": sorted(valid_ids & cited),
        "hallucinatedIds": hallucinated,
        "timing": timing,
        "usage": {"decompose": usage1, "generate": usage2},
    }
