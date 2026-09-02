"""Registry 공개 API — 다른 모듈(core.hub, screengen)이 import 하는 함수 (CONTRACTS §3).

  counts()                              대시보드 집계
  list_approved(record_type, subtype)   ★ Consumer API — APPROVED 만 반환. 다른 상태는 절대 섞이지 않는다 (S3 논지)
  get_record / list_records / create_record / transition / audit_trail / version_chain
  search(query, record_type)            키워드(한국어 부분일치 + 토큰 겹침) + 임베딩 코사인 → RRF 융합

저장소 싱글턴은 REGISTRY_TABLE 이 있으면 DynamoDB, 없으면 인메모리 페이크 (테스트는 reset_for_tests()).
임베딩은 REGISTRY_EMBED=1 일 때만 engine.bedrock.embed 를 호출한다 (기본: REGISTRY_TABLE 있으면 1, 없으면 0).
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from registry.model import (RECORD_TYPES, STATUSES, ConflictError, NotFoundError, RegistryError,  # noqa: F401
                            TransitionError, ValidationError, allowed_targets, validate_record)
from registry.store import EMBEDDING_ATTR, RegistryStore

_store: Optional[RegistryStore] = None

RRF_K = 60
DENSE_TOP_K = 15
DENSE_MIN_SIM = 0.20


# ---------- 저장소 싱글턴 ----------
def get_store() -> RegistryStore:
    global _store
    if _store is None:
        _store = RegistryStore()
    return _store


def use_store(store: RegistryStore) -> None:
    """테스트/통합용 주입."""
    global _store
    _store = store


def reset_for_tests() -> RegistryStore:
    """새 인메모리 저장소로 교체 (테스트 격리)."""
    from registry.fake_table import new_registry_table
    s = RegistryStore(table=new_registry_table(), table_name="")
    use_store(s)
    return s


def backend() -> str:
    return get_store().backend


# ---------- 임베딩 ----------
def embeddings_enabled() -> bool:
    default = "1" if os.environ.get("REGISTRY_TABLE") else "0"
    return os.environ.get("REGISTRY_EMBED", default) == "1"


def _embed_text(rec: dict) -> str:
    return " ".join([str(rec.get("name", "")), str(rec.get("recordType", "")), str(rec.get("subtype", "")),
                     str(rec.get("description", "")), " ".join(rec.get("tags") or [])]).strip()


def _embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Bedrock Titan 임베딩 — 비활성/실패 시 None (호출자는 키워드만으로 진행)."""
    if not embeddings_enabled() or not texts:
        return None
    try:
        from engine import bedrock  # 지연 import: boto3 클라이언트 생성이 import 시점에 일어난다
        return bedrock.embed(texts)
    except Exception as e:  # noqa: BLE001
        from common.log import log_event
        log_event("registry.embed_failed", error=f"{type(e).__name__}: {str(e)[:120]}")
        return None


def _encode_embedding(vec: List[float]) -> str:
    return json.dumps([round(float(x), 5) for x in vec], separators=(",", ":"))


def _decode_embedding(raw: Any) -> Optional[List[float]]:
    if not raw:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else list(raw)
        return [float(x) for x in v]
    except Exception:  # noqa: BLE001
        return None


def ensure_embedding(rec: dict) -> bool:
    """레코드에 임베딩을 계산·저장. 성공 True. 비활성/실패 False (검색은 키워드로만)."""
    vecs = _embed([_embed_text(rec)])
    if not vecs:
        return False
    get_store().set_embedding(rec["name"], rec["recordVersion"], _encode_embedding(vecs[0]))
    return True


def strip(rec: dict) -> dict:
    """전송용 — 임베딩 제거 + 허용 전이 첨부."""
    out = {k: v for k, v in rec.items() if k != EMBEDDING_ATTR}
    out["allowedTargets"] = allowed_targets(out.get("status", ""))
    out["hasEmbedding"] = bool(rec.get(EMBEDDING_ATTR))
    return out


def _sort_key(r: dict) -> Tuple[str, int, str]:
    m = re.match(r"^v(\d+)$", str(r.get("recordVersion", "")))
    return (str(r.get("name", "")).lower(), int(m.group(1)) if m else 0, str(r.get("recordVersion", "")))


# ---------- 조회 ----------
def counts() -> dict:
    recs = get_store().all_records()
    by_type = {t: 0 for t in RECORD_TYPES}
    by_status = {s: 0 for s in STATUSES}
    for r in recs:
        by_type[r.get("recordType", "CUSTOM")] = by_type.get(r.get("recordType", "CUSTOM"), 0) + 1
        by_status[r.get("status", "")] = by_status.get(r.get("status", ""), 0) + 1
    return {"total": len(recs), "approved": by_status.get("APPROVED", 0), "byType": by_type,
            "byStatus": by_status, "backend": backend()}


def list_approved(record_type: Optional[str] = None, subtype: Optional[str] = None) -> List[dict]:
    """★ Consumer API. GSI byStatus 를 APPROVED 로만 질의한다 — 다른 상태를 읽는 코드 경로 자체가 없다."""
    recs = get_store().by_status("APPROVED")
    if record_type:
        recs = [r for r in recs if r.get("recordType") == record_type.upper()]
    if subtype:
        recs = [r for r in recs if (r.get("subtype") or "").upper() == subtype.upper()]
    recs = [r for r in recs if r.get("status") == "APPROVED"]  # 방어적 재확인
    return [strip(r) for r in sorted(recs, key=_sort_key)]


def get_record(name: str, version: str) -> Optional[dict]:
    r = get_store().get(name, version)
    return strip(r) if r else None


def list_records(filters: Optional[dict] = None) -> List[dict]:
    """관리 화면용 목록 — filters: {type, status, subtype, q(이름/설명 부분일치)}."""
    f = filters or {}
    st = (f.get("status") or "").upper()
    recs = get_store().by_status(st) if st in STATUSES else get_store().all_records()
    t = (f.get("type") or f.get("recordType") or "").upper()
    if t and t != "ALL":
        recs = [r for r in recs if r.get("recordType") == t]
    sub = (f.get("subtype") or "").upper()
    if sub:
        recs = [r for r in recs if (r.get("subtype") or "").upper() == sub]
    q = _norm(f.get("q") or "")
    if q:
        recs = [r for r in recs if q in _norm(r.get("name", "")) or q in _norm(r.get("description", ""))
                or any(q in _norm(t) for t in r.get("tags") or [])]
    return [strip(r) for r in sorted(recs, key=_sort_key)]


def version_chain(name: str, version: str) -> List[dict]:
    """supersededBy 를 양방향으로 따라 v1→v2→v3 사슬을 만든다 (끊긴 버전은 버전 번호순으로 뒤에 붙인다)."""
    vers = {r["recordVersion"]: r for r in get_store().versions(name)}
    if version not in vers:
        return []
    nxt = {v: (r.get("payload") or {}).get("supersededBy") for v, r in vers.items()}
    prev = {}
    for v, n in nxt.items():
        if n in vers:
            prev[n] = v
    # 뒤로 끝까지
    head = version
    seen = {head}
    while prev.get(head) and prev[head] not in seen:
        head = prev[head]
        seen.add(head)
    chain, cur = [], head
    seen = set()
    while cur and cur in vers and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = nxt.get(cur)
    rest = sorted((v for v in vers if v not in seen), key=lambda v: _sort_key(vers[v]))
    return [{"recordVersion": v, "status": vers[v]["status"], "updatedAt": vers[v].get("updatedAt"),
             "supersededBy": nxt.get(v), "inChain": v not in rest, "current": v == version}
            for v in chain + rest]


def audit_trail(name: str, version: str) -> List[dict]:
    return get_store().audit(name, version)


# ---------- 변경 ----------
def create_record(record: dict, actor: str, status: Optional[str] = None, reason: str = "",
                  embed: bool = True) -> dict:
    """신규 레코드 — 기본 DRAFT 로 시작. status 는 시드 전용 (기준선 상태로 직접 생성)."""
    rec = validate_record(record)
    rec["status"] = (status or "DRAFT").upper()
    if rec["status"] not in STATUSES:
        raise ValidationError(f"알 수 없는 상태: {rec['status']}")
    saved = get_store().put_new(rec, actor, reason=reason, transition="seed" if status else "create")
    if embed:
        ensure_embedding(saved)
    return strip(saved)


def transition(name: str, version: str, to_status: str, actor: str, reason: str = "") -> Tuple[dict, dict]:
    rec, ev = get_store().transition(name, version, str(to_status).upper(), actor, reason)
    return strip(rec), ev


# ---------- 검색 ----------
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _tokens(s: str) -> set:
    return {t.lower() for t in _TOKEN_RE.findall(str(s or ""))}


def _bigrams(s: str) -> set:
    n = _norm(s)
    return {n[i:i + 2] for i in range(len(n) - 1)} if len(n) > 1 else ({n} if n else set())


def _doc_text(rec: dict) -> str:
    return f"{rec.get('name', '')} {rec.get('description', '')} {' '.join(str(t) for t in rec.get('tags') or [])}"


def build_idf(recs: List[dict]) -> Dict[str, float]:
    """토큰·바이그램 역문서빈도 — "컴포넌트"처럼 60건에 나오는 흔한 말이 "버튼"처럼 3건에만 나오는 말을 덮지 못하게 한다."""
    n = max(len(recs), 1)
    df: Dict[str, int] = {}
    for r in recs:
        t = _doc_text(r) + f" {rec_type_tokens(r)}"
        for term in _tokens(t) | _bigrams(_doc_text(r)):
            df[term] = df.get(term, 0) + 1
    return {term: math.log(1.0 + n / d) for term, d in df.items()}


def rec_type_tokens(rec: dict) -> str:
    return f"{rec.get('subtype', '')} {rec.get('recordType', '')}"


def _weighted_overlap(q_terms: set, doc_terms: set, idf: Optional[Dict[str, float]]) -> float:
    """가중 겹침 비율 [0,1]. idf 가 없으면 균등 가중. 어떤 문서에도 없는 질의 항(오타 등)은 분모에서 뺀다."""
    if not q_terms:
        return 0.0
    if idf is None:
        return len(q_terms & doc_terms) / len(q_terms)
    known = [t for t in q_terms if t in idf]
    denom = sum(idf[t] for t in known)
    if denom <= 0:
        return 0.0
    return sum(idf[t] for t in known if t in doc_terms) / denom


def keyword_score(query: str, rec: dict, idf: Optional[Dict[str, float]] = None) -> float:
    """한국어 친화 키워드 점수 — 이름/태그/설명 부분일치 + IDF 가중 토큰 겹침 + IDF 가중 문자 바이그램 겹침."""
    qn = _norm(query)
    if not qn:
        return 0.0
    name, desc = str(rec.get("name", "")), str(rec.get("description", ""))
    tags = [str(t) for t in rec.get("tags") or []]
    score = 0.0
    nn = _norm(name)
    if nn == qn:
        score += 5.0
    elif qn in nn:
        score += 3.0
    elif nn and nn in qn:
        score += 2.0
    if qn in _norm(desc):
        score += 2.0
    tag_hits = sum(1 for t in tags if qn == _norm(t) or qn in _norm(t) or _norm(t) in qn)
    score += min(tag_hits, 2) * 1.5
    doc_tokens = _tokens(f"{_doc_text(rec)} {rec_type_tokens(rec)}")
    score += 2.0 * _weighted_overlap(_tokens(query), doc_tokens, idf)
    score += 2.0 * _weighted_overlap(_bigrams(query), _bigrams(_doc_text(rec)), idf)
    return round(score, 4)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search_detailed(query: str, record_type: Optional[str] = None, limit: int = 25) -> dict:
    """키워드 랭킹 + 임베딩 랭킹 → RRF(k=60). 반환 {hits, dense(bool), keyword(bool), note}."""
    q = str(query or "").strip()
    recs = get_store().all_records()
    if record_type and record_type.upper() != "ALL":
        recs = [r for r in recs if r.get("recordType") == record_type.upper()]
    if not q:
        return {"hits": [], "dense": False, "keyword": False, "note": "검색어가 비어 있습니다."}
    key = lambda r: (r["name"], r["recordVersion"])  # noqa: E731
    by_key = {key(r): r for r in recs}

    idf = build_idf(recs)
    kw = sorted(((keyword_score(q, r, idf), key(r)) for r in recs), key=lambda x: -x[0])
    kw_rank: Dict[Tuple[str, str], int] = {}
    kw_score: Dict[Tuple[str, str], float] = {}
    for i, (s, k) in enumerate(x for x in kw if x[0] > 0):
        kw_rank[k], kw_score[k] = i + 1, s

    dense_rank: Dict[Tuple[str, str], int] = {}
    dense_score: Dict[Tuple[str, str], float] = {}
    dense_used, note = False, ""
    qv = _embed([q])
    if qv:
        sims = []
        for r in recs:
            ev = _decode_embedding(r.get(EMBEDDING_ATTR))
            if ev:
                sims.append((_cosine(qv[0], ev), key(r)))
        sims.sort(key=lambda x: -x[0])
        for i, (s, k) in enumerate(x for x in sims[:DENSE_TOP_K] if x[0] >= DENSE_MIN_SIM):
            dense_rank[k], dense_score[k] = i + 1, round(s, 4)
        dense_used = bool(sims)
        if not sims:
            note = "임베딩이 저장된 레코드가 없어 키워드만 사용했습니다."
    else:
        note = ("임베딩 미사용 (REGISTRY_EMBED=0) — 키워드 검색만" if not embeddings_enabled()
                else "임베딩 호출 실패 — 키워드 검색만 (로그 registry.embed_failed)")

    fused: Dict[Tuple[str, str], float] = {}
    for k, rk in kw_rank.items():
        fused[k] = fused.get(k, 0.0) + 1.0 / (RRF_K + rk)
    for k, rk in dense_rank.items():
        fused[k] = fused.get(k, 0.0) + 1.0 / (RRF_K + rk)
    hits = []
    for k, s in sorted(fused.items(), key=lambda x: -x[1])[:limit]:
        in_kw, in_dense = k in kw_rank, k in dense_rank
        hits.append({"record": strip(by_key[k]), "score": round(s, 5),
                     "match": "hybrid" if (in_kw and in_dense) else ("keyword" if in_kw else "dense"),
                     "keywordRank": kw_rank.get(k), "keywordScore": kw_score.get(k),
                     "denseRank": dense_rank.get(k), "denseScore": dense_score.get(k)})
    return {"hits": hits, "dense": dense_used, "keyword": True, "note": note, "total": len(recs)}


def search(query: str, record_type: Optional[str] = None) -> List[dict]:
    return search_detailed(query, record_type)["hits"]
