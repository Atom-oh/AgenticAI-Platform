#!/usr/bin/env python3
"""Phase 1 완료 조건 검증 CLI (SPEC §13).

사용:
  python cli.py stats                 # 그래프 볼륨
  python cli.py impact REG-LN-001     # §4.3 규정 영향 분석 순회
  python cli.py metric "지난달 사용액"  # Semantic Layer 해석
백엔드는 GRAPH_BACKEND=local|neptune (기본 local).
"""
import json
import sys

from graph.store import get_store
from semantic.loader import SemanticLayer


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        print(json.dumps(get_store().stats(), ensure_ascii=False, indent=1))
    elif cmd == "impact":
        code = sys.argv[2]
        r = get_store().impact_of_regulation(code)
        if not r.regulation:
            print(f"규정 없음: {code}"); sys.exit(1)
        print(f"[{code}] {r.regulation.props['title']} ({r.regulation.props['article']})")
        print(json.dumps(r.counts(), ensure_ascii=False))
        print(f"순회 경로 엣지: {len(r.path_edges)}")
        for label, items in [("상품", r.products), ("화면", r.screens),
                             ("부서", r.departments), ("문서", r.documents)]:
            names = [n.props.get("name") or n.props.get("title") for n in items[:6]]
            print(f"  {label}({len(items)}): {', '.join(names)}{' …' if len(items) > 6 else ''}")
    elif cmd == "s1":
        import time as _t
        from engine import graphrag, vectorrag
        q = sys.argv[2]
        print(f"질문: {q}\n{'=' * 60}")
        t0 = _t.time()
        v = vectorrag.answer(q)
        print(f"\n◀ Vector RAG ({int((_t.time() - t0) * 1000)}ms, {v['timing']})")
        print(v["answer"][:800])
        t0 = _t.time()
        g = graphrag.answer(q, get_store())
        print(f"\n▶ GraphRAG ({int((_t.time() - t0) * 1000)}ms, seed={g.get('seed')}, conf={g.get('seedConfidence')})")
        print(f"counts={g.get('counts')} hallucinated={g.get('hallucinatedIds')}")
        print(g["answer"][:1200])
    elif cmd == "metric":
        m = SemanticLayer().resolve(sys.argv[2])
        if not m:
            print("해석 불가 — Semantic Layer에 정의 없음"); sys.exit(1)
        print(f"{m.name} ({m.unit}, 소관: {m.owner_dept})\n{m.description}\n--- SQL ---\n{m.sql_template}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
