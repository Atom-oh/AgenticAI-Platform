"""S1 규정 영향 분석 — Vector RAG / GraphRAG 병렬 스트리밍 (SPEC F1·F2)."""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from common import costguard, tracing
from common.ctx import Ctx
from common.log import log_event
from handlers.core import GRAPH_BACKEND, lazy_index, lazy_store

_ID_RE = re.compile(r"\b(?:REG|PRD|SCR|CMP|CND|DOC|TPL|D)-[A-Za-z0-9-]+\b")


def _run_vector(ctx: Ctx, query: str, usage: dict, xing: dict) -> None:
    from engine import bedrock, vectorrag
    try:
        hits, timing, system, user = vectorrag.prepare(query, lazy_index())
    except Exception as e:  # 플레인 벡터 검색 실패는 패널에 명시적으로 보인다 (빈 패널 금지)
        ctx.post({"type": "vector.done", "error": f"벡터 검색 실패 ({type(e).__name__}): {str(e)[:160]}",
                  "searchPlane": vectorrag.search_mode()})
        return
    ctx.post({"type": "vector.chunks", "timing": timing, "searchPlane": timing.get("searchPlane", "local"),
              "searchLabel": vectorrag.search_label(),
              "chunks": [{"id": h.chunk["chunkId"], "score": round(h.score, 4), "text": h.chunk["text"][:220]}
                         for h in hits]})
    t0 = time.time()
    st = bedrock.Stream(system, user)
    for tk in st:
        ctx.token("vector", tk)
    usage["vector"] = st.usage
    xing["vector"] = st.info()  # 게이트 실측: modelId/route/tier/boundary (SPEC v2 §8-3)
    ctx.post({"type": "vector.done", "generate_ms": int((time.time() - t0) * 1000), "usage": st.usage,
              "modelId": st.model_id, "route": st.route, "tier": st.tier, "boundary": st.boundary})


def _run_graph(ctx: Ctx, query: str, usage: dict, xing: dict) -> None:
    from engine import bedrock, graphrag
    meta = graphrag.prepare(query, lazy_store())
    if "error" in meta:
        ctx.post({"type": "graph.done", "error": meta["error"], "seedCandidates": meta.get("seedCandidates", [])})
        return
    ctx.post({"type": "graph.meta", "seed": meta["seed"], "seedConfidence": meta["seedConfidence"],
              "seedCandidates": meta["seedCandidates"], "counts": meta["counts"], "graph": meta["graph"],
              "timing": meta["timing"]})
    t0 = time.time()
    full = []
    st = bedrock.Stream(meta["system"], meta["user"], max_tokens=2000)
    for tk in st:
        full.append(tk)
        ctx.token("graph", tk)
    usage["graph"] = st.usage
    xing["graph"] = st.info()
    valid = {n["id"] for n in meta["graph"]["nodes"]}
    cited = set(_ID_RE.findall("".join(full)))
    ctx.post({"type": "graph.done", "generate_ms": int((time.time() - t0) * 1000), "usage": st.usage,
              "modelId": st.model_id, "route": st.route, "tier": st.tier, "boundary": st.boundary,
              "evidenceNodeIds": sorted(valid & cited)[:50], "hallucinatedIds": sorted(cited - valid)})


def handle(ctx: Ctx, body: dict) -> None:
    query = str(body.get("query", ""))[:500].strip()
    if not query:
        ctx.error("질문이 비어 있습니다.")
        return
    usage: dict = {}
    xing: dict = {}  # 경계 통과 실측 (게이트 Stream.info) — 패널별

    def run(c: Ctx) -> None:
        c.post({"type": "meta", "backend": GRAPH_BACKEND, "user": c.email, "query": query})
        errors = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(_run_vector, c, query, usage, xing), ex.submit(_run_graph, c, query, usage, xing)]
            for f in futs:
                try:
                    f.result()
                except Exception as e:  # 한쪽 실패가 다른 쪽을 막지 않게
                    errors.append(e)
        if errors:
            raise errors[0]

    res = costguard.guarded(ctx, "s1", query, run)
    tin = sum(int(u.get("inputTokens", 0)) for u in usage.values())
    tout = sum(int(u.get("outputTokens", 0)) for u in usage.values())
    costguard.add_usage(tin + tout)
    # 게이트 실측 — 모델 ID·경로·경계를 넘은 문자/추정 토큰/필드 수 (SPEC v2 §8-3: 하드코딩 금지, 실측만)
    infos = [x for x in xing.values() if x]
    boundaries = [x.get("boundary") or {} for x in infos]
    pii_rules = sum(int((b.get("piiRules") or {}).get("count", 0)) for b in boundaries)
    tracing.record_trace({"traceId": ctx.trace_id, "scenario": "S1", "email": ctx.email, "query": query,
                          "blocked": False, "piiOutbound": pii_rules, "piiDetectors": ["rules(gate)"],
                          "maskedFields": [], "tokensIn": tin, "tokensOut": tout, "cached": res["cached"],
                          "backend": GRAPH_BACKEND, "plane": "cloud", "elapsedMs": ctx.elapsed_ms(),
                          "modelId": (infos[0]["modelId"] if infos else None),
                          "route": (infos[0]["route"] if infos else None),
                          "tier": (infos[0]["tier"] if infos else None),
                          "crossings": len(infos),
                          "boundaryFields": sum(len(b.get("fieldsPassed") or []) for b in boundaries),
                          "boundaryChars": sum(int(b.get("chars", 0)) for b in boundaries),
                          "boundaryEstTokens": sum(int(b.get("estTokens", 0)) for b in boundaries)})
    log_event("s1.done", ctx.trace_id, tokensIn=tin, tokensOut=tout, cached=res["cached"], ms=ctx.elapsed_ms(),
              modelId=(infos[0]["modelId"] if infos else None), route=(infos[0]["route"] if infos else None),
              crossings=len(infos))


ROUTES = {"s1": handle}
