"""F7 내부 도구 Lambda — 사내 문서 검색 (BM25-lite, 한국어 바이그램).

대상: seed/out/nodes.jsonl 의 Document 노드(docId,title,type,deptCode) + Regulation 노드(code,title).
이 Lambda 자체는 아무 AWS 권한도 필요 없다. **Reader 역할에는 이 함수의 invoke 권한이 없다** — 그 실패가 F7 시연의 근거다.
Writer 역할만 invoke 할 수 있다.

event: {"query": str, "top_k"?: int}  →  {"query", "results": [{docId,title,type,dept,score}], "total", "corpusSize"}
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Optional

_TOKEN = re.compile(r"[0-9a-zA-Z가-힣]+")
_HANGUL = re.compile(r"^[가-힣]+$")
K1, B = 1.2, 0.75
MAX_TOP_K = 20

_DOCS: Optional[list] = None


def default_seed_dir() -> Path:
    # api-dist 루트(=Lambda task root) 와 로컬 platform/ 모두에서 report/ 의 형제 디렉토리
    return Path(__file__).resolve().parent.parent / "seed" / "out"


def tokenize(text: str) -> list[str]:
    """소문자 토큰 + 한글 토큰의 문자 바이그램 (전세대출 ↔ 전세자금대출 같은 부분 일치용)."""
    out: list[str] = []
    for tok in _TOKEN.findall((text or "").lower()):
        out.append(tok)
        if _HANGUL.match(tok) and len(tok) >= 3:
            out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
    return out


def load_documents(seed_dir: Optional[Path] = None) -> list[dict]:
    """Document + Regulation 노드를 검색 문서로 변환. 부서 코드는 Department 노드 이름으로 풀어준다."""
    sd = Path(seed_dir) if seed_dir else default_seed_dir()
    depts: dict[str, str] = {}
    docs: list[dict] = []
    regs: list[dict] = []
    with open(sd / "nodes.jsonl", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            n = json.loads(line)
            p = n.get("props") or {}
            label = n.get("label")
            if label == "Department":
                depts[str(p.get("deptCode", ""))] = str(p.get("name", ""))
            elif label == "Document":
                docs.append({"docId": str(p.get("docId") or n.get("id")), "title": str(p.get("title", "")),
                             "type": str(p.get("type", "문서")), "deptCode": str(p.get("deptCode", "")),
                             "updatedAt": str(p.get("updatedAt", ""))})
            elif label == "Regulation":
                regs.append({"docId": str(p.get("code") or n.get("id")), "title": str(p.get("title", "")),
                             "type": "규정", "deptCode": "", "article": str(p.get("article", ""))})
    for d in docs:
        d["dept"] = depts.get(d["deptCode"], d["deptCode"]) or "-"
    for r in regs:
        r["dept"] = "준법감시(규정)"
    return docs + regs


def _doc_text(d: dict) -> str:
    return " ".join(str(d.get(k, "")) for k in ("title", "type", "article", "dept"))


def rank_documents(query: str, docs: list[dict], top_k: int = 5) -> list[dict]:
    """순수 BM25-lite 랭킹. 점수 0 초과만, 내림차순. 반환 항목: docId,title,type,dept,score(,updatedAt)."""
    q_tokens = tokenize(query)
    if not q_tokens or not docs:
        return []
    doc_tokens = [tokenize(_doc_text(d)) for d in docs]
    n = len(docs)
    avg_len = sum(len(t) for t in doc_tokens) / n
    df: dict[str, int] = {}
    for toks in doc_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    q_unique = list(dict.fromkeys(q_tokens))
    scored: list[tuple[float, int]] = []
    for i, toks in enumerate(doc_tokens):
        if not toks:
            continue
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        norm = K1 * (1 - B + B * len(toks) / avg_len)
        for t in q_unique:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            # 긴 원형 토큰 일치는 바이그램보다 가중 (정확 어휘 일치 우선)
            weight = 1.0 if len(t) <= 2 else 1.6
            score += weight * idf * f * (K1 + 1) / (f + norm)
        if score > 0:
            scored.append((score, i))
    scored.sort(key=lambda x: (-x[0], docs[x[1]].get("docId", "")))
    out = []
    for score, i in scored[:max(1, min(int(top_k or 5), MAX_TOP_K))]:
        d = docs[i]
        item = {"docId": d["docId"], "title": d["title"], "type": d["type"], "dept": d.get("dept", "-"),
                "score": round(score, 3)}
        if d.get("updatedAt"):
            item["updatedAt"] = d["updatedAt"]
        out.append(item)
    return out


def _corpus() -> list[dict]:
    global _DOCS
    if _DOCS is None:
        _DOCS = load_documents()
    return _DOCS


def handler(event, context=None) -> dict:
    t0 = time.time()
    body = event if isinstance(event, dict) else {}
    query = str(body.get("query", ""))[:300].strip()
    top_k = body.get("top_k", 5)
    docs = _corpus()
    results = rank_documents(query, docs, top_k) if query else []
    return {"query": query, "results": results, "total": len(results), "corpusSize": len(docs),
            "elapsedMs": int((time.time() - t0) * 1000), "source": "seed/out/nodes.jsonl (Document·Regulation)"}
