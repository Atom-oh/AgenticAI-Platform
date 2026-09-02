"""온프렘 플레인 벡터 인덱스 (SPEC §3.2: 벡터 인덱스는 온프렘 플레인 소속).

BM25(Okapi) + dense(코사인) + RRF 융합을 플레인 안에서 수행한다. 이 모듈은 Bedrock에
의존하지 않는다 — 질의 임베딩은 클라우드(engine.bedrock.embed)가 계산해 전달하고,
리랭크(Cohere)도 클라우드에서 수행한다. 문서 청크·임베딩은 합성 규정 문서만이며
개인 금융데이터는 절대 벡터화하지 않는다 (§12.1).

데이터 위치(우선순위): ONPREM_DATA_DIR → onprem/data/ (deploy.sh가 seed/out 을 복사)
→ ../seed/out (로컬 개발·테스트). 표준 라이브러리만 사용한다 — 컨테이너는 인터넷이 없다.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RRF_K = 60           # Reciprocal Rank Fusion 상수 (원 논문 기본값)
FUSED_LIMIT = 12     # 융합 후 클라우드 리랭커에 넘기는 후보 수


def _tokens(text: str) -> list[str]:
    """한국어 대응 토크나이저: 공백·기호 분리 + 음절 바이그램 (engine.vectorrag 와 동일 규칙)."""
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    toks = list(words)
    for w in words:
        if re.match(r"[가-힣]{2,}", w):
            toks.extend(w[i:i + 2] for i in range(len(w) - 1))
    return toks


def resolve_data_dir() -> Path | None:
    """corpus.jsonl 이 있는 디렉토리를 찾는다. 없으면 None (서비스는 503 으로 응답)."""
    cands: list[Path] = []
    env = os.environ.get("ONPREM_DATA_DIR", "")
    if env:
        cands.append(Path(env))
    cands.append(HERE / "data")
    cands.append(HERE.parent / "seed" / "out")
    for d in cands:
        if (d / "corpus.jsonl").is_file():
            return d
    return None


class VectorIndex:
    """BM25 + dense cosine + RRF. dense 검색은 미리 계산된 질의 임베딩을 받는다."""

    def __init__(self, chunks: list[dict], embeddings: list[list[float]], source: str = "") -> None:
        if embeddings and len(embeddings) != len(chunks):
            raise ValueError(f"임베딩 수({len(embeddings)}) ≠ 청크 수({len(chunks)})")
        self.chunks = chunks
        self.emb = embeddings
        self.source = source
        self.dim = len(embeddings[0]) if embeddings else 0
        self._norm = [math.sqrt(sum(x * x for x in e)) or 1.0 for e in embeddings]
        self._df: Counter = Counter()
        self._tf: list[Counter] = []
        self._len: list[int] = []
        for c in chunks:
            tf = Counter(_tokens(c.get("text", "")))
            self._tf.append(tf)
            self._len.append(sum(tf.values()))
            for t in tf:
                self._df[t] += 1
        self._avglen = sum(self._len) / max(len(self._len), 1)

    # ---------- 적재 ----------
    @classmethod
    def load(cls, data_dir: "str | Path | None" = None) -> "VectorIndex":
        d = Path(data_dir) if data_dir else resolve_data_dir()
        if d is None or not (d / "corpus.jsonl").is_file():
            raise FileNotFoundError("corpus.jsonl 을 찾을 수 없습니다 (ONPREM_DATA_DIR / onprem/data / seed/out)")
        with open(d / "corpus.jsonl", encoding="utf-8") as f:
            chunks = [json.loads(line) for line in f if line.strip()]
        emb_path = d / "corpus.embeddings.json"
        emb: list[list[float]] = []
        if emb_path.is_file():
            with open(emb_path, encoding="utf-8") as f:
                emb = json.load(f)
        return cls(chunks, emb, source=str(d))

    # ---------- 검색 ----------
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

    def dense(self, query_embedding: list[float], k: int = 20) -> list[tuple[int, float]]:
        """코사인 유사도 — 질의 임베딩은 호출자가 계산해 전달한다 (플레인 안에서 모델 호출 없음)."""
        if not self.emb:
            return []
        if len(query_embedding) != self.dim:
            raise ValueError(f"질의 임베딩 차원({len(query_embedding)}) ≠ 인덱스 차원({self.dim})")
        qn = math.sqrt(sum(x * x for x in query_embedding)) or 1.0
        sims = []
        for i, e in enumerate(self.emb):
            dot = 0.0
            for a, b_ in zip(query_embedding, e):
                dot += a * b_
            sims.append((i, dot / (qn * self._norm[i])))
        return sorted(sims, key=lambda x: -x[1])[:k]

    @staticmethod
    def rrf(*ranked: list[tuple[int, float]], k: int = RRF_K, limit: int = FUSED_LIMIT) -> list[tuple[int, float]]:
        """Reciprocal Rank Fusion: 여러 순위 목록을 1/(k+rank) 합으로 융합한다."""
        fused: dict = defaultdict(float)
        for lst in ranked:
            for rank, (i, _) in enumerate(lst):
                fused[i] += 1.0 / (k + rank)
        return sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:limit]

    def search(self, query: str, query_embedding: "list[float] | None" = None,
               top_k: int = FUSED_LIMIT) -> tuple[list[dict], dict]:
        """하이브리드 검색 → RRF 융합. 반환: (hits, timing). 리랭크는 클라우드가 이어서 수행한다."""
        timing: dict = {}
        t0 = time.time()
        b_hits = self.bm25(query)
        timing["bm25_ms"] = int((time.time() - t0) * 1000)
        if query_embedding is not None and self.emb:
            t0 = time.time()
            d_hits = self.dense(query_embedding)
            timing["dense_ms"] = int((time.time() - t0) * 1000)
            fused = self.rrf(b_hits, d_hits, limit=top_k)
            stage = "onprem-hybrid"
        else:
            fused = [(i, s) for i, s in b_hits[:top_k]]
            stage = "onprem-bm25"
        hits = []
        for i, s in fused:
            c = self.chunks[i]
            hits.append({"chunkId": c.get("chunkId"), "regCode": c.get("regCode"),
                         "title": c.get("title"), "article": c.get("article"),
                         "text": c.get("text", ""), "score": round(float(s), 6), "stage": stage})
        return hits, timing


# ---------- 프로세스 단일 인스턴스 ----------
_INDEX: "VectorIndex | None" = None
_LOCK = threading.Lock()


def get_index() -> VectorIndex:
    """지연 적재 단일 인덱스. 데이터가 없으면 FileNotFoundError."""
    global _INDEX
    with _LOCK:
        if _INDEX is None:
            _INDEX = VectorIndex.load()
        return _INDEX


def try_get_index() -> "VectorIndex | None":
    try:
        return get_index()
    except (FileNotFoundError, ValueError):
        return None


def reset_index() -> None:
    """테스트용 — 다음 호출에서 다시 적재한다."""
    global _INDEX
    with _LOCK:
        _INDEX = None
