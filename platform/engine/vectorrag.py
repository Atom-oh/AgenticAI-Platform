"""Vector RAG 비교 엔진 (SPEC F2).

일부러 약하게 만들지 않는다: BM25 + dense(Titan v2) 하이브리드(RRF 융합)에
Cohere Rerank v3.5 리랭커까지 정상 구현한다. 그래도 그래프 관계(개정 → 영향
상품·화면·부서)는 추적하지 못한다는 것이 S1의 논지다.

검색 위치 (§3.2: 벡터 인덱스는 온프렘 플레인):
  plane : common.plane.mode() 가 bridge/direct 면 질의 임베딩만 클라우드에서 계산해
          (질문 텍스트는 개인데이터가 아니다) 플레인의 /vector/search 로 보내고,
          BM25+dense+RRF 융합 결과를 받아 클라우드에서 리랭크한다. 플레인 호출이 실패하면
          로컬 인덱스로 조용히 대체하지 않는다 — 분리된 것처럼 보이게 하지 않는다 (§3.1·§12.4).
  local : 개발용 HybridIndex (같은 알고리즘을 프로세스 안에서 수행). cli.py 등 api 밖에서
          common.plane 을 import 할 수 없을 때도 이 경로다.
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
FUSED_LIMIT = 12  # 융합 후 리랭커에 넘기는 후보 수 (플레인 topK 와 동일)

SYSTEM = ("당신은 아톰은행 내규 안내 도우미입니다. 제공된 규정 청크만 근거로 "
          "한국어로 답하세요. 근거에 없는 내용은 '검색된 규정에서 확인할 수 없습니다'라고 "
          "말하세요. 인용한 청크 ID를 답변 끝에 나열하세요.")


def _tokens(text: str) -> list[str]:
    """한국어 대응 토크나이저: 공백·기호 분리 + 음절 바이그램 (onprem/vector_index 와 동일 규칙)."""
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
    stage: str  # bm25 | dense | fused | onprem-hybrid | onprem-bm25 | reranked


class HybridIndex:
    """BM25(Okapi) + dense cosine + RRF 융합 — 로컬 개발용 (플레인 미연결 시)."""

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
        scores: dict = defaultdict(float)
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
        rrf: dict = defaultdict(float)
        for rank, (i, _) in enumerate(b_hits):
            rrf[i] += 1 / (60 + rank)
        for rank, (i, _) in enumerate(d_hits):
            rrf[i] += 1 / (60 + rank)
        fused = sorted(rrf.items(), key=lambda x: -x[1])[:FUSED_LIMIT]
        hits = [Hit(self.chunks[i], s, "fused") for i, s in fused]
        if use_rerank and hits:
            hits, timing["rerank_ms"] = _rerank(query, hits, top_k)
        return hits[:top_k], timing


# ---------- 검색 위치 결정 ----------
def _plane():
    """common.plane 지연 import — engine 은 cli.py(api 경로 밖)에서도 import 되므로 없으면 None."""
    try:
        from common import plane  # api-dist 루트가 sys.path 에 있을 때만 존재
    except ImportError:
        return None
    return plane


def search_mode() -> str:
    """'plane' (온프렘 플레인 인덱스) | 'local' (프로세스 내 인덱스)."""
    p = _plane()
    return "plane" if p is not None and p.mode() in ("bridge", "direct") else "local"


def search_label() -> str:
    return ("온프렘 플레인 벡터 인덱스 (BM25+dense 융합 → 클라우드 리랭크)" if search_mode() == "plane"
            else "로컬 인덱스 (개발용 — 플레인 분리 아님)")


def _rerank(query: str, hits: list[Hit], top_k: int) -> tuple[list[Hit], int]:
    """클라우드 리랭크(Cohere) — 합성 규정 청크만 전달한다. 반환: (hits, ms)."""
    t0 = time.time()
    order = bedrock.rerank(query, [h.chunk["text"] for h in hits], top_n=top_k)
    ms = int((time.time() - t0) * 1000)
    return [Hit(hits[i].chunk, score, "reranked") for i, score in order], ms


def _search_plane(query: str, top_k: int = 5, use_rerank: bool = True) -> tuple[list[Hit], dict]:
    """플레인 경로: 임베딩(클라우드) → /vector/search(온프렘 BM25+dense+RRF) → 리랭크(클라우드)."""
    plane = _plane()
    timing: dict = {}
    t0 = time.time()
    q_emb = bedrock.embed([query])[0]  # 질문 텍스트만 — 개인데이터 아님
    timing["embed_ms"] = int((time.time() - t0) * 1000)
    t0 = time.time()
    res = plane.call("/vector/search", {"query": query,
                                        "queryEmbedding": [round(x, 6) for x in q_emb],
                                        "topK": FUSED_LIMIT})
    timing["plane_ms"] = int((time.time() - t0) * 1000)
    if not isinstance(res, dict) or "hits" not in res:
        raise plane.PlaneUnavailable(f"온프렘 벡터 검색 응답 이상: {str(res)[:200]}")
    for k, v in (res.get("timing") or {}).items():
        if isinstance(v, (int, float)):
            timing[k] = v
    hits = [Hit({k: v for k, v in h.items() if k not in ("score", "stage")},
                float(h.get("score", 0.0)), str(h.get("stage", "onprem-hybrid")))
            for h in res["hits"] if isinstance(h, dict) and h.get("chunkId")]
    if use_rerank and hits:
        hits, timing["rerank_ms"] = _rerank(query, hits, top_k)
    timing["searchPlane"] = "onprem"
    return hits[:top_k], timing


def _search(query: str, index: "HybridIndex | None" = None, top_k: int = 5) -> tuple[list[Hit], dict]:
    if search_mode() == "plane":
        return _search_plane(query, top_k)
    index = index or HybridIndex.load()
    hits, timing = index.search(query, top_k)
    timing["searchPlane"] = "local"
    return hits, timing


def _prompt(query: str, hits: list[Hit]) -> tuple[str, str]:
    context = "\n\n".join(f"[{h.chunk['chunkId']}] {h.chunk['text']}" for h in hits)
    return SYSTEM, f"질문: {query}\n\n검색된 규정 청크:\n{context}"


def prepare(query: str, index: "HybridIndex | None" = None):
    """검색까지 수행하고 (hits, timing, system, user) 를 반환 — 스트리밍 생성용.
    플레인 모드에서는 index 인자를 쓰지 않는다 (인덱스는 온프렘에 있다)."""
    hits, timing = _search(query, index)
    system, user = _prompt(query, hits)
    return hits, timing, system, user


def answer(query: str, index: "HybridIndex | None" = None) -> dict:
    """Vector RAG 전체 파이프라인: 검색 → 생성. S1 좌측 패널의 응답 (단발형·CLI)."""
    hits, timing = _search(query, index)
    system, user = _prompt(query, hits)
    t0 = time.time()
    text, usage = bedrock.generate(system, user)
    timing["generate_ms"] = int((time.time() - t0) * 1000)
    return {
        "engine": "vector-rag",
        "searchPlane": timing.get("searchPlane", "local"),
        "answer": text,
        "chunks": [{"id": h.chunk["chunkId"], "score": round(h.score, 4),
                    "text": h.chunk["text"][:200], "stage": h.stage} for h in hits],
        "timing": timing,
        "usage": usage,
    }
