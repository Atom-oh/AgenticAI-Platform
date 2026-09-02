"""Vector RAG 비교 엔진 (SPEC F2).

일부러 약하게 만들지 않는다: BM25 + dense(Titan v2) 하이브리드(RRF 융합)에
Cohere Rerank v3.5 리랭커까지 정상 구현한다. 그래도 그래프 관계(개정 → 영향
상품·화면·부서)는 추적하지 못한다는 것이 S1의 논지다.

개발 단계는 로컬 인덱스(임베딩 캐시 포함), 운영 단계는 동일 계약으로
OpenSearch Serverless에 이관한다 (§15.3 확정).
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from engine import bedrock

SEED_OUT = Path(__file__).resolve().parent.parent / "seed" / "out"
EMB_CACHE = SEED_OUT / "corpus.embeddings.json"


def _tokens(text: str) -> list[str]:
    """한국어 대응 토크나이저: 공백·기호 분리 + 음절 바이그램."""
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    toks = list(words)
    for w in words:
        if re.match(r"[가-힣]{2,}", w):
            toks.extend(w[i:i + 2] for i in range(len(w) - 1))
    return toks


@dataclass
class Hit:
    chunk: dict
    score: float
    stage: str  # bm25 | dense | fused | reranked


class HybridIndex:
    """BM25(Okapi) + dense cosine + RRF 융합."""

    def __init__(self, chunks: list[dict], embeddings: list[list[float]]) -> None:
        self.chunks = chunks
        self.emb = embeddings
        self._df: Counter = Counter()
        self._tf: list[Counter] = []
        self._len: list[int] = []
        for c in chunks:
            tf = Counter(_tokens(c["text"]))
            self._tf.append(tf)
            self._len.append(sum(tf.values()))
            for t in tf:
                self._df[t] += 1
        self._avglen = sum(self._len) / max(len(self._len), 1)

    @classmethod
    def load(cls) -> "HybridIndex":
        chunks = [json.loads(l) for l in open(SEED_OUT / "corpus.jsonl", encoding="utf-8")]
        if EMB_CACHE.exists():
            emb = json.loads(EMB_CACHE.read_text())
        else:
            emb = bedrock.embed([c["text"] for c in chunks])
            EMB_CACHE.write_text(json.dumps(emb))
        assert len(emb) == len(chunks)
        return cls(chunks, emb)

    def bm25(self, query: str, k: int = 20, k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
        n = len(self.chunks)
        scores: dict[int, float] = defaultdict(float)
        for t in set(_tokens(query)):
            df = self._df.get(t)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                f = tf.get(t)
                if f:
                    scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self._len[i] / self._avglen))
        return sorted(scores.items(), key=lambda x: -x[1])[:k]

    def dense(self, query: str, k: int = 20) -> list[tuple[int, float]]:
        q = bedrock.embed([query])[0]
        sims = [(i, sum(a * b for a, b in zip(q, e))) for i, e in enumerate(self.emb)]
        return sorted(sims, key=lambda x: -x[1])[:k]

    def search(self, query: str, top_k: int = 5, use_rerank: bool = True) -> tuple[list[Hit], dict]:
        """하이브리드 검색 → RRF 융합 → 리랭크. 반환: (hits, 단계별 타이밍)."""
        timing = {}
        t0 = time.time()
        b_hits = self.bm25(query)
        timing["bm25_ms"] = int((time.time() - t0) * 1000)
        t0 = time.time()
        d_hits = self.dense(query)
        timing["dense_ms"] = int((time.time() - t0) * 1000)
        # Reciprocal Rank Fusion
        rrf: dict[int, float] = defaultdict(float)
        for rank, (i, _) in enumerate(b_hits):
            rrf[i] += 1 / (60 + rank)
        for rank, (i, _) in enumerate(d_hits):
            rrf[i] += 1 / (60 + rank)
        fused = sorted(rrf.items(), key=lambda x: -x[1])[:12]
        hits = [Hit(self.chunks[i], s, "fused") for i, s in fused]
        if use_rerank and hits:
            t0 = time.time()
            order = bedrock.rerank(query, [h.chunk["text"] for h in hits], top_n=top_k)
            timing["rerank_ms"] = int((time.time() - t0) * 1000)
            hits = [Hit(hits[i].chunk, score, "reranked") for i, score in order]
        return hits[:top_k], timing


def prepare(query: str, index: HybridIndex | None = None):
    """검색까지 수행하고 (hits, timing, system, user) 를 반환 — 스트리밍 생성용."""
    index = index or HybridIndex.load()
    hits, timing = index.search(query)
    context = "\n\n".join(f"[{h.chunk['chunkId']}] {h.chunk['text']}" for h in hits)
    system = ("당신은 아톰은행 내규 안내 도우미입니다. 제공된 규정 청크만 근거로 "
              "한국어로 답하세요. 근거에 없는 내용은 '검색된 규정에서 확인할 수 없습니다'라고 "
              "말하세요. 인용한 청크 ID를 답변 끝에 나열하세요.")
    user = f"질문: {query}\n\n검색된 규정 청크:\n{context}"
    return hits, timing, system, user


def answer(query: str, index: HybridIndex | None = None) -> dict:
    """Vector RAG 전체 파이프라인: 검색 → 생성. S1 좌측 패널의 응답."""
    index = index or HybridIndex.load()
    hits, timing = index.search(query)
    context = "\n\n".join(f"[{h.chunk['chunkId']}] {h.chunk['text']}" for h in hits)
    system = ("당신은 아톰은행 내규 안내 도우미입니다. 제공된 규정 청크만 근거로 "
              "한국어로 답하세요. 근거에 없는 내용은 '검색된 규정에서 확인할 수 없습니다'라고 "
              "말하세요. 인용한 청크 ID를 답변 끝에 나열하세요.")
    t0 = time.time()
    text, usage = bedrock.generate(system, f"질문: {query}\n\n검색된 규정 청크:\n{context}")
    timing["generate_ms"] = int((time.time() - t0) * 1000)
    return {
        "engine": "vector-rag",
        "answer": text,
        "chunks": [{"id": h.chunk["chunkId"], "score": round(h.score, 4),
                    "text": h.chunk["text"][:200]} for h in hits],
        "timing": timing,
        "usage": usage,
    }
