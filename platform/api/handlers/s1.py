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


def _run_vector(ctx: Ctx, query: str, usage: dict) -> None:
    from engine import bedrock, vectorrag
    hits, timing, system, user = vectorrag.prepare(query, lazy_index())
    ctx.post({"type": "vector.chunks", "timing": timing,
              "chunks": [{"id": h.chunk["chunkId"], "score": round(h.score, 4), "text": h.chunk["text"][:220]}
                         for h in hits]})
    t0 = time.time()
    st = bedrock.Stream(system, user)
    for tk in st:
        ctx.token("vector", tk)
    usage["vector"] = st.usage
    ctx.post({"type": "vector.done", "generate_ms": int((time.time() - t0) * 1000), "usage": st.usage})


def _run_graph(ctx: Ctx, query: str, usage: dict) -> None:
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
    valid = {n["id"] for n in meta["graph"]["nodes"]}
    cited = set(_ID_RE.findall("".join(full)))
    ctx.post({"type": "graph.done", "generate_ms": int((time.time() - t0) * 1000), "usage": st.usage,
              "evidenceNodeIds": sorted(valid & cited)[:50], "hallucinatedIds": sorted(cited - valid)})


def handle(ctx: Ctx, body: dict) -> None:
    query = str(body.get("query", ""))[:500].strip()
    if not query:
        ctx.error("질문이 비어 있습니다.")
        return
    usage: dict = {}

    def run(c: Ctx) -> None:
        c.post({"type": "meta", "backend": GRAPH_BACKEND, "user": c.email, "query": query})
        errors = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(_run_vector, c, query, usage), ex.submit(_run_graph, c, query, usage)]
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
    tracing.record_trace({"traceId": ctx.trace_id, "scenario": "S1", "email": ctx.email, "query": query,
                          "blocked": False, "piiOutbound": 0, "piiDetectors": ["n/a(합성 규정 문서만)"],
                          "maskedFields": [], "tokensIn": tin, "tokensOut": tout, "cached": res["cached"],
                          "backend": GRAPH_BACKEND, "plane": "cloud", "elapsedMs": ctx.elapsed_ms()})
    log_event("s1.done", ctx.trace_id, tokensIn=tin, tokensOut=tout, cached=res["cached"], ms=ctx.elapsed_ms())


ROUTES = {"s1": handle}
