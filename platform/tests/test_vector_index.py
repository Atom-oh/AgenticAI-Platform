"""온프렘 벡터 인덱스 단위테스트 (SPEC §3.2·F2) — BM25 · dense(코사인) · RRF 융합.

오프라인: 질의 임베딩은 저장된 청크 임베딩을 그대로 쓴다 (Bedrock 호출 없음).
실행: cd platform && python3 -m pytest tests/test_vector_index.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from onprem.vector_index import VectorIndex, resolve_data_dir  # noqa: E402

SEED = ROOT / "seed" / "out"
HERO_QUERY = "전세자금대출 담보"
HERO_REG = "REG-LN-001"


@pytest.fixture(scope="module")
def idx() -> VectorIndex:
    return VectorIndex.load(SEED)


def _pos(idx: VectorIndex, chunk_id: str) -> int:
    return next(i for i, c in enumerate(idx.chunks) if c["chunkId"] == chunk_id)


def test_resolver_finds_a_corpus():
    d = resolve_data_dir()
    assert d is not None and (d / "corpus.jsonl").is_file()


def test_load_shape(idx):
    assert len(idx.chunks) == 177
    assert len(idx.emb) == len(idx.chunks)
    assert idx.dim == 1024


def test_bm25_finds_hero_regulation(idx):
    hits = idx.bm25(HERO_QUERY, k=5)
    assert hits, "BM25 결과가 비어 있다"
    assert idx.chunks[hits[0][0]]["regCode"] == HERO_REG
    # 상위 5건 중 다수가 히어로 규정의 청크여야 한다 (전세자금대출 담보 인정 기준 = 5청크)
    assert sum(1 for i, _ in hits if idx.chunks[i]["regCode"] == HERO_REG) >= 3
    scores = [s for _, s in hits]
    assert scores == sorted(scores, reverse=True)


def test_dense_with_stored_embedding_is_top1(idx):
    i0 = _pos(idx, f"{HERO_REG}#c0")
    d = idx.dense(idx.emb[i0], k=3)
    assert d[0][0] == i0
    assert abs(d[0][1] - 1.0) < 1e-6  # 자기 자신과의 코사인 = 1


def test_dense_dim_mismatch_raises(idx):
    with pytest.raises(ValueError):
        idx.dense([0.1, 0.2, 0.3])


def test_rrf_fusion_ordering():
    """양쪽 목록에 모두 나온 문서가 한쪽 1위만인 문서보다 앞서고, 단독 등장은 순위가 높을수록 앞선다."""
    bm = [(0, 9.0), (1, 5.0), (2, 1.0)]   # A, B, C
    dn = [(3, 0.9), (0, 0.8), (1, 0.7)]   # D, A, B
    fused = VectorIndex.rrf(bm, dn, limit=10)
    order = [i for i, _ in fused]
    # A=1/60+1/61, B=1/61+1/62, D=1/60, C=1/62
    assert order == [0, 1, 3, 2]
    scores = [s for _, s in fused]
    assert scores == sorted(scores, reverse=True)
    assert abs(fused[0][1] - (1 / 60 + 1 / 61)) < 1e-12


def test_rrf_limit_and_tie_break():
    a = [(5, 1.0), (7, 0.5)]
    b = [(7, 1.0), (5, 0.5)]  # 5·7 동점 → 인덱스 순으로 안정 정렬
    fused = VectorIndex.rrf(a, b, limit=1)
    assert fused == [(5, pytest.approx(1 / 60 + 1 / 61))]


def test_search_hybrid_shape_and_stage(idx):
    i0 = _pos(idx, f"{HERO_REG}#c0")
    hits, timing = idx.search(HERO_QUERY, idx.emb[i0], top_k=5)
    assert len(hits) == 5
    assert hits[0]["chunkId"] == f"{HERO_REG}#c0"  # BM25·dense 양쪽 상위 → 융합 1위
    for h in hits:
        assert {"chunkId", "regCode", "text", "score", "stage"} <= set(h)
        assert h["stage"] == "onprem-hybrid"
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert "bm25_ms" in timing and "dense_ms" in timing


def test_search_bm25_only_without_embedding(idx):
    hits, timing = idx.search(HERO_QUERY, None, top_k=3)
    assert len(hits) == 3 and all(h["stage"] == "onprem-bm25" for h in hits)
    assert hits[0]["regCode"] == HERO_REG
    assert "dense_ms" not in timing


def test_synthetic_hybrid_combines_both_signals():
    """BM25 는 0번, dense 는 1번을 1위로 두는 소형 인덱스 — 융합 결과는 둘을 2번보다 앞세운다."""
    chunks = [
        {"chunkId": "A", "regCode": "R1", "text": "전세자금대출 담보 인정 기준 보증서"},
        {"chunkId": "B", "regCode": "R2", "text": "예금 이자 지급 방법"},
        {"chunkId": "C", "regCode": "R3", "text": "외환 송금 수수료 안내"},
    ]
    emb = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]
    small = VectorIndex(chunks, emb)
    hits, _ = small.search("전세자금대출 담보", [0.0, 1.0], top_k=3)
    ids = [h["chunkId"] for h in hits]
    assert set(ids[:2]) == {"A", "B"}
    assert ids[2] == "C"


def test_embedding_count_mismatch_rejected():
    with pytest.raises(ValueError):
        VectorIndex([{"chunkId": "A", "text": "x"}], [[1.0], [0.0]])
