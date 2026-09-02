"""AOSS 벡터 인덱스 클라이언트 단위테스트 (SPEC v2 §3-3 · §16 · §12.7).

오프라인: 가짜 전송(FakeTransport)이 요청을 기록하고 OpenSearch 응답을 돌려준다. 네트워크·AWS 호출 없음.
자격증명은 고정 값을 주입한다 (botocore 세션·IMDS 조회 없음).
실행: cd platform && python3 -m pytest tests/test_aoss_index.py -q
"""
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from botocore.credentials import Credentials

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from onprem import aoss_index  # noqa: E402
from onprem.aoss_index import (  # noqa: E402
    AossIndex, AossRequestError, AossTransportError, AossUnavailable, get_vector_backend, reset_backend, rrf_fuse,
)
from onprem.vector_index import VectorIndex  # noqa: E402

ENDPOINT = "https://abc123.ap-northeast-2.aoss.amazonaws.com"
INDEX = "bank-rag-chunks"
CREDS = Credentials("AKIATESTACCESSKEY", "test-secret-not-real", token="test-session-token")
DIM = aoss_index.EMBED_DIM


def _doc(cid, reg="REG-LN-001", text="전세자금대출 담보 인정 기준"):
    return {"chunkId": cid, "regCode": reg, "title": "제목", "article": "제12조", "seq": 0, "text": text}


def _hits(docs):
    return {"took": 3, "hits": {"total": {"value": len(docs)}, "hits": [
        {"_index": INDEX, "_id": f"auto-{i}", "_score": 1.0 / (i + 1), "_source": d} for i, d in enumerate(docs)]}}


class FakeTransport:
    """(method, path) → 응답 또는 응답 시퀀스. 요청은 calls 에 기록한다."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        u = urlparse(url)
        self.calls.append({"method": method, "url": url, "path": u.path,
                           "headers": {k.lower(): v for k, v in headers.items()}, "body": body, "timeout": timeout})
        key = (method, u.path)
        if key not in self.routes:
            raise AssertionError(f"unexpected request {key}")
        resp = self.routes[key]
        if isinstance(resp, list):
            resp = resp.pop(0) if len(resp) > 1 else resp[0]
        if callable(resp):
            resp = resp(body)
        if isinstance(resp, Exception):
            raise resp
        status, payload = resp
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        return status, raw


def _index(transport, **kw):
    return AossIndex(ENDPOINT, INDEX, "ap-northeast-2", transport=transport, credentials=CREDS,
                     sleep=lambda s: None, **kw)


def _search_router(bm25_docs, knn_docs):
    def route(body):
        q = json.loads(body.decode())
        if "knn" in q["query"]:
            return 200, _hits(knn_docs)
        assert "multi_match" in q["query"]
        return 200, _hits(bm25_docs)
    return route


# ---------- SigV4 ----------
def test_sigv4_headers_present_with_aoss_scope_and_content_sha256():
    tr = FakeTransport({("GET", f"/{INDEX}/_count"): (200, {"count": 177})})
    idx = _index(tr)
    assert idx.count() == 177
    call = tr.calls[0]
    auth = call["headers"]["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIATESTACCESSKEY/")
    assert "/ap-northeast-2/aoss/aws4_request" in auth
    assert "x-amz-content-sha256" in auth.split("SignedHeaders=")[1].split(",")[0].split(";")
    assert call["headers"]["x-amz-content-sha256"] == hashlib.sha256(b"").hexdigest()  # GET: 빈 본문
    assert call["headers"]["x-amz-security-token"] == "test-session-token"
    assert "x-amz-date" in call["headers"]
    assert call["timeout"] == 10.0


def test_post_body_hash_matches_payload():
    tr = FakeTransport({("POST", f"/{INDEX}/_search"): _search_router([_doc("A")], [])})
    idx = _index(tr)
    idx.bm25("전세자금대출 담보", k=5)
    call = tr.calls[0]
    assert call["headers"]["content-type"] == "application/json"
    assert call["headers"]["x-amz-content-sha256"] == hashlib.sha256(call["body"]).hexdigest()
    sent = json.loads(call["body"].decode())
    assert sent["size"] == 5 and sent["query"]["multi_match"]["query"] == "전세자금대출 담보"
    assert "text" in sent["query"]["multi_match"]["fields"][0]
    assert "embedding" not in sent["_source"]  # 응답에서 임베딩 제외


def test_endpoint_normalisation_and_region_inference():
    idx = AossIndex("abc123.ap-northeast-2.aoss.amazonaws.com/", transport=FakeTransport(), credentials=CREDS)
    assert idx.endpoint == ENDPOINT and idx.region == "ap-northeast-2" and idx.index == INDEX
    with pytest.raises(ValueError):
        AossIndex("", transport=FakeTransport(), credentials=CREDS)


# ---------- 인덱스 생성 ----------
def test_ensure_index_creates_knn_mapping_when_missing():
    tr = FakeTransport({("GET", f"/{INDEX}"): (404, {"error": {"type": "index_not_found_exception"}, "status": 404}),
                        ("PUT", f"/{INDEX}"): (200, {"acknowledged": True, "index": INDEX})})
    idx = _index(tr)
    assert idx.ensure_index() is True
    put = tr.calls[1]
    assert put["method"] == "PUT"
    body = json.loads(put["body"].decode())
    assert body["settings"] == {"index": {"knn": True}}
    props = body["mappings"]["properties"]
    emb = props["embedding"]
    assert emb["type"] == "knn_vector" and emb["dimension"] == 1024
    assert emb["method"] == {"name": "hnsw", "engine": "faiss", "space_type": "innerproduct"}
    assert props["chunkId"] == {"type": "keyword"} and props["regCode"] == {"type": "keyword"}
    assert props["text"] == {"type": "text", "analyzer": "standard"}
    assert props["seq"] == {"type": "integer"} and props["article"] == {"type": "keyword"}
    # Serverless 제약: 샤드/레플리카/refresh 설정을 보내지 않는다
    assert not {"number_of_shards", "number_of_replicas", "refresh_interval"} & set(body["settings"]["index"])


def test_ensure_index_noop_when_exists():
    tr = FakeTransport({("GET", f"/{INDEX}"): (200, {INDEX: {"mappings": {}}})})
    idx = _index(tr)
    assert idx.ensure_index() is False
    assert [c["method"] for c in tr.calls] == ["GET"]


# ---------- bulk ----------
def test_bulk_ingest_has_no_id_and_batches():
    chunks = [_doc(f"C{i}") for i in range(5)]
    embs = [[0.0] * DIM for _ in range(5)]

    def bulk(body):
        lines = [l for l in body.decode().split("\n") if l]
        n = len(lines) // 2
        return 200, {"took": 1, "errors": False,
                     "items": [{"index": {"_index": INDEX, "_id": f"auto{i}", "status": 201}} for i in range(n)]}

    tr = FakeTransport({("POST", "/_bulk"): bulk})
    idx = _index(tr)
    res = idx.bulk_ingest(chunks, embs, batch=2)
    assert res == {"indexed": 5, "errors": 0, "batches": 3}
    assert len(tr.calls) == 3 and all(c["timeout"] == 60.0 for c in tr.calls)
    for call in tr.calls:
        assert call["headers"]["content-type"] == "application/x-ndjson"
        assert call["body"].endswith(b"\n")
        lines = [json.loads(l) for l in call["body"].decode().split("\n") if l]
        actions, docs = lines[0::2], lines[1::2]
        for a in actions:
            assert set(a) == {"index"} and a["index"] == {"_index": INDEX}
            assert "_id" not in a["index"]  # VECTORSEARCH 컬렉션은 _id 지정을 받지 않는다
        for d in docs:
            assert len(d["embedding"]) == DIM
            assert {"chunkId", "regCode", "title", "article", "seq", "text"} <= set(d)
    assert len(json.loads(tr.calls[2]["body"].decode().split("\n")[0])) == 1  # 마지막 배치 1건


def test_bulk_ingest_rejects_dim_and_count_mismatch():
    idx = _index(FakeTransport())
    with pytest.raises(ValueError):
        idx.bulk_ingest([_doc("A")], [[0.0] * 3])
    with pytest.raises(ValueError):
        idx.bulk_ingest([_doc("A")], [])


def test_bulk_all_failed_raises_request_error():
    tr = FakeTransport({("POST", "/_bulk"): (200, {"errors": True, "items": [
        {"index": {"status": 400, "error": {"type": "mapper_parsing_exception", "reason": "bad"}}}]})})
    with pytest.raises(AossRequestError):
        _index(tr).bulk_ingest([_doc("A")], [[0.0] * DIM])


# ---------- RRF ----------
def test_rrf_fuse_matches_vector_index_rrf():
    """양쪽 목록에 모두 나온 문서가 한쪽 1위만인 문서보다 앞서고, 단독 등장은 순위가 높을수록 앞선다 —
    test_vector_index.test_rrf_fusion_ordering 과 같은 픽스처를 chunkId 키로 융합해도 같은 순서."""
    ids = ["c0", "c1", "c2", "c3"]
    bm_int = [(0, 9.0), (1, 5.0), (2, 1.0)]   # A, B, C
    dn_int = [(3, 0.9), (0, 0.8), (1, 0.7)]   # D, A, B
    ref = VectorIndex.rrf(bm_int, dn_int, limit=10)
    got = rrf_fuse([ids[i] for i, _ in bm_int], [ids[i] for i, _ in dn_int], limit=10)
    assert [ids[i] for i, _ in ref] == [cid for cid, _ in got] == ["c0", "c1", "c3", "c2"]
    assert [round(s, 12) for _, s in ref] == [round(s, 12) for _, s in got]
    assert got[0][1] == pytest.approx(1 / 60 + 1 / 61)


def test_rrf_fuse_limit_and_tie_break():
    a = ["c5", "c7"]
    b = ["c7", "c5"]  # 동점 → 키 오름차순 (VectorIndex.rrf 의 인덱스 오름차순과 동일 규칙)
    assert rrf_fuse(a, b, limit=1) == [("c5", pytest.approx(1 / 60 + 1 / 61))]


# ---------- search ----------
def test_search_hybrid_two_requests_and_rrf_order():
    A, B, C, D = (_doc("A"), _doc("B", "REG-2"), _doc("C", "REG-3"), _doc("D", "REG-4"))
    tr = FakeTransport({("POST", f"/{INDEX}/_search"): _search_router([A, B, C], [D, A, B])})
    idx = _index(tr)
    q_emb = [0.01] * DIM
    hits, timing = idx.search("전세자금대출 담보", q_emb, top_k=12)
    assert [c["path"] for c in tr.calls] == [f"/{INDEX}/_search", f"/{INDEX}/_search"]
    knn_body = json.loads(tr.calls[1]["body"].decode())
    assert knn_body["query"]["knn"]["embedding"]["k"] == 20 and knn_body["size"] == 20
    assert knn_body["query"]["knn"]["embedding"]["vector"] == q_emb
    assert [h["chunkId"] for h in hits] == ["A", "B", "D", "C"]
    assert hits[0]["score"] == pytest.approx(1 / 60 + 1 / 61, abs=1e-6)
    for h in hits:
        assert {"chunkId", "regCode", "text", "score", "stage"} <= set(h)
        assert h["stage"] == "aoss-hybrid"
    assert hits[2]["regCode"] == "REG-4" and hits[2]["text"]
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert "bm25_ms" in timing and "knn_ms" in timing


def test_search_top_k_limits_and_dedupes_duplicate_docs():
    A, B = _doc("A"), _doc("B", "REG-2")
    tr = FakeTransport({("POST", f"/{INDEX}/_search"): _search_router([A, A, B], [B, A])})  # A 가 두 번 색인된 사고
    hits, _ = _index(tr).search("q", [0.0] * DIM, top_k=1)
    assert [h["chunkId"] for h in hits] == ["A"]


def test_search_bm25_only_without_embedding():
    tr = FakeTransport({("POST", f"/{INDEX}/_search"): _search_router([_doc("A"), _doc("B")], [])})
    hits, timing = _index(tr).search("전세자금대출", None, top_k=5)
    assert len(tr.calls) == 1
    assert [h["chunkId"] for h in hits] == ["A", "B"] and all(h["stage"] == "aoss-bm25" for h in hits)
    assert "knn_ms" not in timing and "bm25_ms" in timing


def test_search_dim_mismatch_raises_value_error_before_any_request():
    tr = FakeTransport({("POST", f"/{INDEX}/_search"): _search_router([_doc("A")], [])})
    with pytest.raises(ValueError):
        _index(tr).search("q", [0.1, 0.2, 0.3])
    assert tr.calls == []  # BM25 요청도 보내지 않는다 — 호출자(service)는 400 으로 응답하고 대체하지 않는다


# ---------- 재시도 · 장애 ----------
def test_retries_on_503_then_succeeds():
    sleeps = []
    tr = FakeTransport({("GET", f"/{INDEX}/_count"): [(503, {"error": "busy"}), (503, b"<html>"), (200, {"count": 7})]})
    idx = AossIndex(ENDPOINT, INDEX, transport=tr, credentials=CREDS, sleep=sleeps.append)
    assert idx.count() == 7
    assert len(tr.calls) == 3 and sleeps == [0.5, 1.5]
    assert idx.last_error is None and idx.last_docs == 7


def test_gives_up_after_three_attempts_with_unavailable():
    tr = FakeTransport({("GET", f"/{INDEX}/_count"): (503, {"error": {"reason": "throttled"}})})
    idx = _index(tr)
    with pytest.raises(AossUnavailable) as ei:
        idx.count()
    assert len(tr.calls) == 3 and ei.value.status == 503
    assert idx.last_error and "503" in idx.last_error


def test_transport_error_is_retried_then_unavailable():
    tr = FakeTransport({("GET", f"/{INDEX}/_count"): AossTransportError("URLError: timed out")})
    with pytest.raises(AossUnavailable):
        _index(tr).count()
    assert len(tr.calls) == 3


def test_429_is_retryable_but_400_is_not():
    tr = FakeTransport({("GET", f"/{INDEX}/_count"): [(429, {"error": "slow down"}), (200, {"count": 1})]})
    assert _index(tr).count() == 1
    tr2 = FakeTransport({("POST", f"/{INDEX}/_search"): (400, {"error": {"reason": "parsing_exception"}})})
    with pytest.raises(AossRequestError):
        _index(tr2).bm25("q")
    assert len(tr2.calls) == 1


def test_403_raises_unavailable_without_retry():
    tr = FakeTransport({("GET", f"/{INDEX}/_count"): (403, {"error": "User does not have permissions"})})
    with pytest.raises(AossUnavailable):
        _index(tr).count()
    assert len(tr.calls) == 1


def test_health_ok_and_unavailable_shapes():
    ok = _index(FakeTransport({("GET", f"/{INDEX}/_count"): (200, {"count": 177})})).health()
    assert ok == {"ok": True, "docs": 177, "index": INDEX, "endpoint": ENDPOINT, "backend": "aoss"}
    bad = _index(FakeTransport({("GET", f"/{INDEX}/_count"): (503, {})})).health()
    assert bad["ok"] is False and bad["docs"] is None and bad["error"] == "AossUnavailable"


def test_snapshot_needs_no_network():
    idx = _index(FakeTransport())
    snap = idx.snapshot()
    assert snap["docs"] is None and snap["index"] == INDEX and snap["lastError"] is None


# ---------- bootstrap (멱등) ----------
def test_bootstrap_skips_when_count_covers_corpus():
    tr = FakeTransport({("GET", f"/{INDEX}"): (200, {}), ("GET", f"/{INDEX}/_count"): (200, {"count": 177})})
    chunks = [_doc(f"C{i}") for i in range(3)]
    res = _index(tr).bootstrap(chunks, [[0.0] * DIM] * 3)
    assert res["skipped"] is True and res["indexed"] == 0 and res["count"] == 177 and res["created"] is False
    assert ("POST", "/_bulk") not in {(c["method"], c["path"]) for c in tr.calls}


def test_bootstrap_creates_and_ingests_when_empty():
    counts = [(404, {}), (200, {"count": 3})]
    tr = FakeTransport({("GET", f"/{INDEX}"): [(404, {}), (200, {})],
                        ("PUT", f"/{INDEX}"): (200, {"acknowledged": True}),
                        ("GET", f"/{INDEX}/_count"): counts,
                        ("POST", "/_bulk"): (200, {"errors": False, "items": [{"index": {"status": 201}}] * 3})})
    res = _index(tr).bootstrap([_doc(f"C{i}") for i in range(3)], [[0.0] * DIM] * 3)
    assert res["created"] is True and res["indexed"] == 3 and res["skipped"] is False and res["expected"] == 3
    methods = [(c["method"], c["path"]) for c in tr.calls]
    assert ("PUT", f"/{INDEX}") in methods and ("POST", "/_bulk") in methods
    assert ("DELETE", f"/{INDEX}") not in methods


def test_bootstrap_existing_index_zero_count_recounts_after_grace():
    """기존 인덱스가 0건으로 보이면(refresh 지연) 잠깐 기다려 다시 센다 — 재기동 직후 중복 적재를 막는다."""
    sleeps = []
    tr = FakeTransport({("GET", f"/{INDEX}"): (200, {}),
                        ("GET", f"/{INDEX}/_count"): [(200, {"count": 0}), (200, {"count": 3})]})
    idx = AossIndex(ENDPOINT, INDEX, transport=tr, credentials=CREDS, sleep=sleeps.append)
    res = idx.bootstrap([_doc(f"C{i}") for i in range(3)], [[0.0] * DIM] * 3)
    assert res["skipped"] is True and sleeps == [aoss_index.REFRESH_GRACE_SEC]
    assert ("POST", "/_bulk") not in {(c["method"], c["path"]) for c in tr.calls}


def test_bootstrap_partial_or_force_recreates_index():
    tr = FakeTransport({("GET", f"/{INDEX}"): [(200, {}), (404, {})],
                        ("PUT", f"/{INDEX}"): (200, {"acknowledged": True}),
                        ("DELETE", f"/{INDEX}"): (200, {"acknowledged": True}),
                        ("GET", f"/{INDEX}/_count"): [(200, {"count": 1}), (200, {"count": 3})],
                        ("POST", "/_bulk"): (200, {"errors": False, "items": [{"index": {"status": 201}}] * 3})})
    res = _index(tr).bootstrap([_doc(f"C{i}") for i in range(3)], [[0.0] * DIM] * 3)  # 부분 적재(1/3) → 재생성
    methods = [(c["method"], c["path"]) for c in tr.calls]
    assert methods.index(("DELETE", f"/{INDEX}")) < methods.index(("PUT", f"/{INDEX}")) < methods.index(("POST", "/_bulk"))
    assert res["created"] is True and res["indexed"] == 3


# ---------- 백엔드 선택 ----------
@pytest.fixture
def _clean_env(monkeypatch):
    for k in ("VECTOR_BACKEND", "AOSS_ENDPOINT", "AOSS_INDEX", "AOSS_REGION"):
        monkeypatch.delenv(k, raising=False)
    reset_backend()
    yield
    reset_backend()


def test_backend_memory_when_env_unset(_clean_env):
    assert get_vector_backend() == ("memory", None)


def test_backend_memory_when_aoss_requested_without_endpoint(_clean_env, monkeypatch, capsys):
    monkeypatch.setenv("VECTOR_BACKEND", "aoss")
    assert get_vector_backend() == ("memory", None)
    assert "aoss.misconfigured" in capsys.readouterr().out  # 조용히 AOSS 인 척하지 않는다


def test_backend_aoss_when_configured(_clean_env, monkeypatch):
    monkeypatch.setenv("VECTOR_BACKEND", "aoss")
    monkeypatch.setenv("AOSS_ENDPOINT", ENDPOINT + "/")
    monkeypatch.setenv("AOSS_INDEX", "bank-rag-chunks")
    monkeypatch.setenv("AOSS_REGION", "ap-northeast-2")
    name, idx = get_vector_backend()
    assert name == "aoss" and isinstance(idx, AossIndex)
    assert idx.endpoint == ENDPOINT and idx.index == "bank-rag-chunks" and idx.region == "ap-northeast-2"
    assert get_vector_backend()[1] is idx  # 프로세스 단일 인스턴스


# ---------- 코퍼스 · CLI ----------
def test_load_corpus_from_seed():
    chunks, emb = aoss_index.load_corpus(str(ROOT / "seed" / "out"))
    assert len(chunks) == 177 and len(emb) == 177 and len(emb[0]) == 1024
    assert {"chunkId", "regCode", "title", "article", "seq", "text"} <= set(chunks[0])


def test_cli_refuses_when_not_configured(_clean_env, capsys):
    assert aoss_index._cli(["health"]) == 2
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["vectorBackend"] == "memory" and "error" in out


def test_no_forbidden_terms_in_module_and_dockerfile():
    # 금지 표현(§12.13)은 조각을 이어 만든다 — 이 파일 자체에 그 표현이 통째로 남지 않게
    banned = ["".join(p) for p in (("온", "프렘"), ("Two-", "Plane"), ("In-", "Region"), ("서울을 ", "벗어나지"))]
    for rel in ("onprem/aoss_index.py", "onprem/Dockerfile", "tests/test_aoss_index.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for bad in banned:
            assert bad not in text, f"{rel}: 금지 표현 {bad!r} (§12.13)"
