"""VPC 내부 플레인 벡터 인덱스 — Amazon OpenSearch Serverless(AOSS) 백엔드 (SPEC v2 §3-3 · §16).

컬렉션 'bank-platform-rag'(VECTORSEARCH)는 플레인 VPC의 VPC 엔드포인트로만 접근된다.
이 모듈은 SigV4(botocore)로 서명한 HTTPS 호출만 한다 — opensearch-py 없음, 표준 urllib 전송.
질의 임베딩은 클라우드(engine.bedrock.embed)가 계산해 전달하고 리랭크(Cohere)도 클라우드가 한다.
색인 대상은 합성 규정 청크(corpus.jsonl)만 — 개인 금융데이터는 절대 벡터화하지 않는다 (§12.3).

검색 = BM25(multi_match text/title) + kNN(faiss hnsw innerproduct) 두 요청 → RRF(k=60) 융합.
융합식·후보 수(20)·상한(12)은 vector_index.VectorIndex 와 동일하다 (§12.7: 비교군을 약화하지 않는다).
AOSS 장애 시 AossError(AossUnavailable)를 올린다 — 호출자는 컨테이너 내 인덱스로 대체하고
stage/timing 에 'memory-fallback' 배지를 반드시 단다 (§11: 대체를 숨기지 않는다).

OpenSearch Serverless(VECTORSEARCH) 제약 — 이 모듈이 지키는 것:
  * 문서 `_id` 지정 불가 → bulk 'index' 액션에 `_id` 를 넣지 않는다 (자동 생성). 따라서 멱등성은
    문서 수(count ≥ 코퍼스 크기)로 판단하고, 부분 적재/강제 재적재는 인덱스 삭제 후 재생성으로 처리한다.
  * `refresh` 파라미터 미지원 → bulk 직후 count 는 수 초 뒤에 반영될 수 있다 (eventual).
  * `number_of_shards/replicas`, `refresh_interval` 등 클러스터 설정 미지원 → settings 는 `index.knn` 만.
  * 서명 시 `x-amz-content-sha256` 헤더가 필수다 (botocore SigV4Auth 는 S3 외에는 자동으로 넣지 않는다).

환경변수: VECTOR_BACKEND=aoss · AOSS_ENDPOINT=https://<id>.<region>.aoss.amazonaws.com ·
AOSS_INDEX(기본 bank-rag-chunks) · AOSS_REGION(기본: 엔드포인트에서 추출, 없으면 ap-northeast-2).

CLI (컨테이너 안):  python aoss_index.py bootstrap [--force] | health | search "질문" [--emb-of CHUNK_ID]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # 컨테이너(플랫 배치: /app/*.py)
    import vector_index
except ImportError:  # 패키지로 import 될 때 (api 로컬 폴백 · 테스트)
    from onprem import vector_index

try:
    import botocore.session
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    _BOTOCORE_OK = True
except ImportError:  # 컨테이너 이미지에는 boto3 가 들어간다 (Dockerfile) — 로컬 개발 환경 방어
    _BOTOCORE_OK = False

SERVICE = "aoss"
DEFAULT_INDEX = "bank-rag-chunks"
DEFAULT_REGION = "ap-northeast-2"
EMBED_DIM = 1024                          # Titan Embeddings v2
RRF_K = vector_index.RRF_K                # 60
FUSED_LIMIT = vector_index.FUSED_LIMIT    # 12 — 클라우드 리랭커에 넘기는 후보 수
CANDIDATE_K = 20                          # BM25 · kNN 각각의 후보 수 (VectorIndex.bm25/dense 의 k 와 동일)
RETRIES = 3
BACKOFF_SEC = (0.5, 1.5)                  # 재시도 사이 대기 (2회)
REFRESH_GRACE_SEC = 2.0                   # refresh 미지원 — 기존 인덱스가 0건으로 보이면 잠깐 기다려 한 번 더 센다
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
STAGE_HYBRID = "aoss-hybrid"
STAGE_BM25 = "aoss-bm25"
SOURCE_FIELDS = ["chunkId", "regCode", "title", "article", "seq", "text"]  # 응답에서 embedding 제외

INDEX_BODY: Dict[str, Any] = {
    "settings": {"index": {"knn": True}},
    "mappings": {"properties": {
        "chunkId": {"type": "keyword"},
        "regCode": {"type": "keyword"},
        "title": {"type": "text"},
        "article": {"type": "keyword"},
        "seq": {"type": "integer"},
        "text": {"type": "text", "analyzer": "standard"},
        "embedding": {"type": "knn_vector", "dimension": EMBED_DIM,
                      "method": {"name": "hnsw", "engine": "faiss", "space_type": "innerproduct"}},
    }},
}

Transport = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, bytes]]


# ---------- 예외 ----------
class AossError(Exception):
    """AOSS 호출 실패의 공통 부모 — 호출자는 이것을 잡아 메모리 인덱스로 대체하고 배지를 단다."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class AossUnavailable(AossError):
    """네트워크 오류 · 5xx/429 재시도 소진 · 인증/권한 거부 · 자격증명 없음 · botocore 없음."""


class AossRequestError(AossError):
    """재시도해도 소용없는 4xx (잘못된 질의·매핑 등) — 코드 결함 신호이므로 로그에 종류를 남긴다."""


class AossTransportError(Exception):
    """전송 계층 오류 (연결 실패·타임아웃). _request 가 재시도 대상으로 처리한다."""


# ---------- 로그 (메트릭만 — 질의 원문·임베딩은 남기지 않는다 §12.5) ----------
def _log(event: str, **fields: Any) -> None:
    rec = {"ts": int(time.time() * 1000), "event": event, "plane": "vpc-internal", "backend": SERVICE}
    rec.update(fields)
    sys.stdout.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


# ---------- 전송 ----------
def urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
                     timeout: float) -> Tuple[int, bytes]:
    """기본 전송: urllib. HTTP 오류는 (status, body) 로 돌려주고 연결 오류만 예외로 올린다."""
    req = urllib.request.Request(url, data=body if body else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read()
    except (urllib.error.URLError, OSError) as e:  # timeout · connection refused · DNS
        raise AossTransportError(f"{type(e).__name__}: {str(e)[:200]}")


# ---------- RRF (vector_index.VectorIndex.rrf 와 동일 공식, 키만 일반화) ----------
def rrf_fuse(*ranked: Sequence[str], k: int = RRF_K, limit: int = FUSED_LIMIT) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion: 여러 순위 목록(키 리스트)을 1/(k+rank) 합으로 융합한다.
    동점은 키 오름차순 — VectorIndex.rrf 가 인덱스 오름차순으로 안정 정렬하는 것과 같은 규칙."""
    fused: Dict[str, float] = defaultdict(float)
    for lst in ranked:
        for rank, key in enumerate(lst):
            fused[key] += 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:limit]


def _dedupe(keys: List[str]) -> List[str]:
    """같은 chunkId 가 두 번 색인된 경우(재적재 사고) 순위 목록에서 첫 등장만 남긴다."""
    seen = set()
    out = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ---------- 코퍼스 ----------
def load_corpus(data_dir: Optional[str] = None) -> Tuple[List[dict], List[List[float]]]:
    """vector_index 와 같은 해석기(ONPREM_DATA_DIR → onprem/data → seed/out)로 corpus 를 읽는다."""
    d = Path(data_dir) if data_dir else vector_index.resolve_data_dir()
    if d is None or not (d / "corpus.jsonl").is_file():
        raise FileNotFoundError("corpus.jsonl 을 찾을 수 없습니다 (ONPREM_DATA_DIR / onprem/data / seed/out)")
    with open(d / "corpus.jsonl", encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]
    emb_path = d / "corpus.embeddings.json"
    if not emb_path.is_file():
        raise FileNotFoundError(f"corpus.embeddings.json 이 없습니다: {emb_path}")
    with open(emb_path, encoding="utf-8") as f:
        embeddings = json.load(f)
    if len(embeddings) != len(chunks):
        raise ValueError(f"임베딩 수({len(embeddings)}) ≠ 청크 수({len(chunks)})")
    return chunks, embeddings


# ---------- 인덱스 ----------
class AossIndex:
    """OpenSearch Serverless 하이브리드 인덱스 클라이언트 (SigV4 · urllib · 재시도 3회)."""

    def __init__(self, endpoint: str, index: str = DEFAULT_INDEX, region: Optional[str] = None,
                 transport: Optional[Transport] = None, credentials: Any = None,
                 timeout: float = 10.0, bulk_timeout: float = 60.0,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        ep = (endpoint or "").strip().rstrip("/")
        if not ep:
            raise ValueError("AOSS endpoint is required")
        if not ep.startswith("http"):
            ep = "https://" + ep
        self.endpoint = ep
        self.index = index or DEFAULT_INDEX
        self.region = region or _region_from_endpoint(ep) or DEFAULT_REGION
        self._transport: Transport = transport or urllib_transport
        self._creds = credentials          # None → botocore 세션(태스크 역할)에서 지연 획득
        self.timeout = timeout
        self.bulk_timeout = bulk_timeout
        self._sleep = sleep or time.sleep
        # 마지막으로 관측한 상태 — /health 가 네트워크 호출 없이 읽는다 (ALB 헬스체크를 막지 않기 위해)
        self.last_docs: Optional[int] = None
        self.last_error: Optional[str] = None
        self.last_ok_at: Optional[int] = None

    # ----- 서명 -----
    def _credentials(self) -> Any:
        if self._creds is None:
            if not _BOTOCORE_OK:
                raise AossUnavailable("botocore 가 설치되어 있지 않다 (Dockerfile: boto3)")
            creds = botocore.session.get_session().get_credentials()
            if creds is None:
                raise AossUnavailable("AWS 자격증명을 찾을 수 없다 (ECS 태스크 역할 확인)")
            self._creds = creds
        c = self._creds
        return c.get_frozen_credentials() if hasattr(c, "get_frozen_credentials") else c

    def _signed_headers(self, method: str, url: str, body: bytes, content_type: Optional[str]) -> Dict[str, str]:
        if not _BOTOCORE_OK:
            raise AossUnavailable("botocore 가 설치되어 있지 않다 (Dockerfile: boto3)")
        headers = {"X-Amz-Content-SHA256": hashlib.sha256(body).hexdigest()}  # AOSS 필수 헤더
        if content_type:
            headers["Content-Type"] = content_type
        req = AWSRequest(method=method, url=url, data=body or None, headers=headers)
        SigV4Auth(self._credentials(), SERVICE, self.region).add_auth(req)
        return {k: v for k, v in req.headers.items()}

    # ----- 요청 -----
    def _request(self, method: str, path: str, body: Any = None, *, content_type: Optional[str] = None,
                 timeout: Optional[float] = None, ok: Tuple[int, ...] = (200, 201),
                 allow: Tuple[int, ...] = ()) -> Tuple[int, Any]:
        """서명 → 전송 → JSON 파싱. 429/5xx/전송 오류는 최대 RETRIES 회 재시도한다.
        ok/allow 에 없는 4xx 는 즉시 실패 (401/403 → AossUnavailable, 그 외 → AossRequestError)."""
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif body is None:
            data = b""
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = content_type or "application/json"
        url = self.endpoint + path
        last = "unknown"
        for attempt in range(RETRIES):
            headers = self._signed_headers(method, url, data, content_type if data else None)
            try:
                status, raw = self._transport(method, url, headers, data or None, timeout or self.timeout)
            except AossTransportError as e:
                last = str(e)
                status, raw = None, b""
            if status is not None:
                parsed = _parse(raw)
                if status in ok or status in allow:
                    self.last_ok_at = int(time.time())
                    self.last_error = None
                    return status, parsed
                last = f"HTTP {status}: {_reason(parsed)}"
                if status in (401, 403):
                    self.last_error = last
                    raise AossUnavailable(f"aoss access denied {path}: {last}", status)
                if status not in RETRYABLE_STATUS:
                    self.last_error = last
                    raise AossRequestError(f"aoss {method} {path}: {last}", status)
            if attempt < RETRIES - 1:
                self._sleep(BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)])
        self.last_error = last
        raise AossUnavailable(f"aoss {method} {path} failed after {RETRIES} attempts: {last}",
                              status if isinstance(status, int) else None)

    # ----- 인덱스 관리 -----
    def index_exists(self) -> bool:
        status, _ = self._request("GET", f"/{self.index}", allow=(404,))
        return status != 404

    def ensure_index(self) -> bool:
        """없으면 생성. 반환: 생성했으면 True, 이미 있었으면 False."""
        if self.index_exists():
            return False
        self._request("PUT", f"/{self.index}", INDEX_BODY)
        _log("aoss.index_created", index=self.index, dim=EMBED_DIM)
        return True

    def delete_index(self) -> bool:
        status, _ = self._request("DELETE", f"/{self.index}", allow=(404,))
        if status != 404:
            _log("aoss.index_deleted", index=self.index)
        self.last_docs = 0
        return status != 404

    def count(self) -> int:
        """문서 수 (GET <index>/_count). 인덱스가 없으면 0."""
        status, out = self._request("GET", f"/{self.index}/_count", allow=(404,))
        n = 0 if status == 404 else int((out or {}).get("count", 0))
        self.last_docs = n
        return n

    # ----- 적재 -----
    def bulk_ingest(self, chunks: List[dict], embeddings: List[List[float]], batch: int = 100) -> Dict[str, int]:
        """bulk 'index' 액션으로 적재 — VECTORSEARCH 컬렉션은 _id 지정을 받지 않으므로 넣지 않는다.
        반환: {indexed, errors, batches}. 모두 실패하면 AossRequestError."""
        if len(embeddings) != len(chunks):
            raise ValueError(f"임베딩 수({len(embeddings)}) ≠ 청크 수({len(chunks)})")
        indexed = errors = batches = 0
        first_error = None
        for start in range(0, len(chunks), max(1, batch)):
            lines = []
            for c, e in zip(chunks[start:start + batch], embeddings[start:start + batch]):
                if len(e) != EMBED_DIM:
                    raise ValueError(f"임베딩 차원({len(e)}) ≠ {EMBED_DIM}: {c.get('chunkId')}")
                doc = {k: c.get(k) for k in SOURCE_FIELDS}
                doc["embedding"] = e
                lines.append(json.dumps({"index": {"_index": self.index}}, ensure_ascii=False))
                lines.append(json.dumps(doc, ensure_ascii=False))
            payload = ("\n".join(lines) + "\n").encode("utf-8")
            _, out = self._request("POST", "/_bulk", payload, content_type="application/x-ndjson",
                                   timeout=self.bulk_timeout)
            batches += 1
            for item in (out or {}).get("items", []):
                res = item.get("index") or item.get("create") or {}
                if int(res.get("status", 0)) in (200, 201):
                    indexed += 1
                else:
                    errors += 1
                    first_error = first_error or _reason(res)
        _log("aoss.bulk", indexed=indexed, errors=errors, batches=batches)
        if errors and not indexed:
            raise AossRequestError(f"aoss bulk: all {errors} items failed: {first_error}")
        return {"indexed": indexed, "errors": errors, "batches": batches}

    def bootstrap(self, chunks: List[dict], embeddings: List[List[float]], force: bool = False,
                  batch: int = 100) -> Dict[str, Any]:
        """멱등 부트스트랩: 인덱스 보장 → count ≥ 코퍼스면 건너뜀. force 또는 부분 적재(0<count<코퍼스)면
        삭제 후 재생성·전량 적재 (_id 가 없어 부분 보정이 불가능하므로). 반환: {created, indexed, count, ...}."""
        expected = len(chunks)
        created = self.ensure_index()
        n = self.count()
        if not created and n == 0:
            # 기존 인덱스가 0건: 직전 적재가 아직 검색 가능 상태로 반영되지 않았을 수 있다 (재기동 직후) — 중복 적재 방어
            self._sleep(REFRESH_GRACE_SEC)
            n = self.count()
        if not force and n >= expected > 0:
            return {"created": created, "indexed": 0, "count": n, "expected": expected, "skipped": True}
        if force or n > 0:
            self.delete_index()
            self.ensure_index()
            created = True
        res = self.bulk_ingest(chunks, embeddings, batch=batch)
        n = self.count()  # refresh 미지원 — 수 초 뒤에 반영될 수 있다
        self.last_docs = max(n, res["indexed"]) if n < res["indexed"] else n
        return {"created": created, "indexed": res["indexed"], "errors": res["errors"],
                "count": n, "expected": expected, "skipped": False}

    # ----- 검색 -----
    def bm25(self, query: str, k: int = CANDIDATE_K) -> List[dict]:
        body = {"size": k, "_source": SOURCE_FIELDS,
                "query": {"multi_match": {"query": query, "fields": ["text", "title^0.5"], "operator": "or"}}}
        _, out = self._request("POST", f"/{self.index}/_search", body)
        return [h.get("_source", {}) for h in (out.get("hits") or {}).get("hits", [])]

    def knn(self, query_embedding: List[float], k: int = CANDIDATE_K) -> List[dict]:
        if len(query_embedding) != EMBED_DIM:
            raise ValueError(f"질의 임베딩 차원({len(query_embedding)}) ≠ 인덱스 차원({EMBED_DIM})")
        body = {"size": k, "_source": SOURCE_FIELDS,
                "query": {"knn": {"embedding": {"vector": query_embedding, "k": k}}}}
        _, out = self._request("POST", f"/{self.index}/_search", body)
        return [h.get("_source", {}) for h in (out.get("hits") or {}).get("hits", [])]

    def search(self, query_text: str, query_embedding: Optional[List[float]] = None,
               top_k: int = FUSED_LIMIT) -> Tuple[List[dict], Dict[str, Any]]:
        """BM25 + kNN 두 요청 → RRF 융합. 반환: (hits, timing). 임베딩이 없으면 BM25 만 (stage aoss-bm25)."""
        if query_embedding is not None and len(query_embedding) != EMBED_DIM:  # 요청 전에 실패 (BM25 호출 낭비 방지)
            raise ValueError(f"질의 임베딩 차원({len(query_embedding)}) ≠ 인덱스 차원({EMBED_DIM})")
        k = max(CANDIDATE_K, top_k)
        timing: Dict[str, Any] = {}
        docs: Dict[str, dict] = {}
        t0 = time.time()
        b_docs = self.bm25(query_text, k)
        timing["bm25_ms"] = int((time.time() - t0) * 1000)
        b_ids = _dedupe([str(d.get("chunkId", "")) for d in b_docs])
        for d in b_docs:
            docs.setdefault(str(d.get("chunkId", "")), d)
        if query_embedding is not None:
            t0 = time.time()
            k_docs = self.knn(query_embedding, k)
            timing["knn_ms"] = int((time.time() - t0) * 1000)
            k_ids = _dedupe([str(d.get("chunkId", "")) for d in k_docs])
            for d in k_docs:
                docs.setdefault(str(d.get("chunkId", "")), d)
            fused = rrf_fuse(b_ids, k_ids, limit=top_k)
            stage = STAGE_HYBRID
        else:
            fused = [(cid, 1.0 / (RRF_K + r)) for r, cid in enumerate(b_ids[:top_k])]
            stage = STAGE_BM25
        hits = []
        for cid, score in fused:
            d = docs.get(cid, {})
            hits.append({"chunkId": d.get("chunkId", cid), "regCode": d.get("regCode"),
                         "title": d.get("title"), "article": d.get("article"),
                         "text": d.get("text", ""), "score": round(float(score), 6), "stage": stage})
        _log("aoss.search", hits=len(hits), queryLen=len(query_text), **timing)
        return hits, timing

    # ----- 상태 -----
    def health(self) -> Dict[str, Any]:
        """실제 _count 호출. 실패해도 예외 대신 ok=False 로 돌려준다."""
        try:
            docs = self.count()
            return {"ok": True, "docs": docs, "index": self.index, "endpoint": self.endpoint, "backend": SERVICE}
        except AossError as e:
            return {"ok": False, "docs": None, "index": self.index, "endpoint": self.endpoint,
                    "backend": SERVICE, "error": type(e).__name__}

    def snapshot(self) -> Dict[str, Any]:
        """네트워크 호출 없는 마지막 관측값 — /health 가 ALB 헬스체크 타임아웃을 넘기지 않도록 이것을 쓴다."""
        return {"docs": self.last_docs, "index": self.index, "endpoint": self.endpoint,
                "lastOkAt": self.last_ok_at, "lastError": self.last_error, "backend": SERVICE}


# ---------- 유틸 ----------
def _region_from_endpoint(endpoint: str) -> Optional[str]:
    m = re.search(r"\.([a-z]{2}-[a-z]+-\d)\.aoss\.amazonaws\.com", endpoint)
    return m.group(1) if m else None


def _parse(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"raw": raw[:200].decode("utf-8", "replace")}


def _reason(parsed: Any) -> str:
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            return str(err.get("reason") or err.get("type") or err)[:200]
        if err:
            return str(err)[:200]
        if "raw" in parsed:
            return str(parsed["raw"])[:200]
    return str(parsed)[:200]


# ---------- 백엔드 선택 (프로세스 단일 인스턴스) ----------
_BACKEND: Optional[AossIndex] = None
_LOCK = threading.Lock()


def backend_config() -> Dict[str, str]:
    return {"backend": os.environ.get("VECTOR_BACKEND", "memory").strip().lower(),
            "endpoint": os.environ.get("AOSS_ENDPOINT", "").strip(),
            "index": os.environ.get("AOSS_INDEX", DEFAULT_INDEX).strip() or DEFAULT_INDEX,
            "region": os.environ.get("AOSS_REGION", "").strip()}


def get_vector_backend() -> Tuple[str, Optional[AossIndex]]:
    """('aoss', AossIndex) — VECTOR_BACKEND=aoss 이고 AOSS_ENDPOINT 가 설정된 경우만.
    그 외(미설정·엔드포인트 없음)는 ('memory', None): AOSS 인 척하지 않는다 (§11)."""
    global _BACKEND
    cfg = backend_config()
    if cfg["backend"] != SERVICE:
        return "memory", None
    if not cfg["endpoint"]:
        _log("aoss.misconfigured", reason="VECTOR_BACKEND=aoss but AOSS_ENDPOINT empty", fallback="memory")
        return "memory", None
    with _LOCK:
        if _BACKEND is None or _BACKEND.endpoint.rstrip("/") != cfg["endpoint"].rstrip("/") \
                or _BACKEND.index != cfg["index"]:
            _BACKEND = AossIndex(cfg["endpoint"], cfg["index"], cfg["region"] or None)
        return SERVICE, _BACKEND


def reset_backend() -> None:
    """테스트용 — 다음 호출에서 환경변수를 다시 읽는다."""
    global _BACKEND
    with _LOCK:
        _BACKEND = None


# ---------- CLI ----------
def _cli(argv: List[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="aoss_index", description="AOSS 벡터 인덱스 관리 (컨테이너 내부용)")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="인덱스 생성 + 코퍼스 적재 (멱등)")
    b.add_argument("--force", action="store_true", help="삭제 후 전량 재적재")
    b.add_argument("--data-dir", default=None)
    sub.add_parser("health", help="_count 로 연결·문서 수 확인")
    s = sub.add_parser("search", help="하이브리드 검색 (임베딩은 --emb-of 청크의 저장 임베딩을 대용)")
    s.add_argument("query")
    s.add_argument("--emb-of", default=None, help="질의 임베딩 대용으로 쓸 chunkId (없으면 BM25 만)")
    s.add_argument("--top-k", type=int, default=FUSED_LIMIT)
    s.add_argument("--data-dir", default=None)
    a = p.parse_args(argv)

    backend, idx = get_vector_backend()
    if idx is None:
        cfg = backend_config()
        print(json.dumps({"error": "AOSS 미설정 — VECTOR_BACKEND=aoss 와 AOSS_ENDPOINT 가 필요하다",
                          "vectorBackend": backend, "config": {k: v for k, v in cfg.items() if k != "endpoint"}},
                         ensure_ascii=False))
        return 2
    try:
        if a.cmd == "bootstrap":
            chunks, emb = load_corpus(a.data_dir)
            out = idx.bootstrap(chunks, emb, force=a.force)
        elif a.cmd == "health":
            out = idx.health()
        else:
            q_emb = None
            if a.emb_of:
                chunks, emb = load_corpus(a.data_dir)
                pos = next((i for i, c in enumerate(chunks) if c.get("chunkId") == a.emb_of), None)
                if pos is None:
                    print(json.dumps({"error": f"chunkId not in corpus: {a.emb_of}"}, ensure_ascii=False))
                    return 2
                q_emb = emb[pos]
            hits, timing = idx.search(a.query, q_emb, top_k=a.top_k)
            out = {"timing": timing, "hits": [{"chunkId": h["chunkId"], "regCode": h["regCode"],
                                               "score": h["score"], "stage": h["stage"],
                                               "text": (h.get("text") or "")[:80]} for h in hits]}
    except AossError as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:300]}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
