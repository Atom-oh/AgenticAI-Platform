"""개인 금융데이터 정확 조회 + 감사 원문 저장소 — VPC 내부 플레인 소속 (SPEC v2 §3-3, F6).

DATA_BACKEND=rds 면 프라이빗 RDS PostgreSQL(pg8000)에서, 아니면 컨테이너 내 합성 레코드·
로컬 jsonl 파일에서 처리한다. 벡터 검색 금지 (§12.1). 전부 합성데이터·토큰 식별자.

감사 원문(프롬프트·재식별 매핑·응답)은 이 플레인 안(RDS audit_log 또는 파일)에만 남는다.
클라우드로는 길이·건수만 나간다 (§12.3). DB 클라이언트는 함수 안에서 지연 생성한다.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import random
import time
from collections import deque
from datetime import date, timedelta

_SYNTH = {
    "demo@atomai.click": {
        "customerId": "CUST-0042", "name": "김데모", "segment": "우대",
        "product": {"code": "PRD-LN-001", "name": "아톰 안심전세대출 II", "baseRate": "4.5"},
        "account": {"accountId": "ACCT-0007", "balanceKrw": 12_450_000},
        "salaryTransferMonths": 6, "cardMonthlyKrw": 450_000,
        "autoTransferCount": 2, "isFirstHome": True, "isNewlywed": False,
        "jeonse": {"depositKrw": 300_000_000, "guaranteeRatio": "0.8",
                   "annualIncomeKrw": 68_000_000, "existingDebtKrw": 40_000_000},
    },
}
_DEFAULT = {
    "customerId": "CUST-0777", "name": "이방문", "segment": "일반",
    "product": {"code": "PRD-LN-001", "name": "아톰 안심전세대출 II", "baseRate": "4.5"},
    "account": {"accountId": "ACCT-0777", "balanceKrw": 3_200_000},
    "salaryTransferMonths": 1, "cardMonthlyKrw": 120_000,
    "autoTransferCount": 0, "isFirstHome": False, "isNewlywed": False,
    "jeonse": {"depositKrw": 200_000_000, "guaranteeRatio": "0.8",
               "annualIncomeKrw": 42_000_000, "existingDebtKrw": 0},
}

_SEEDED = False                       # ensure_rds_seed 성공 여부 (health 표시용)
_MAPPING_CACHE: "dict[str, dict]" = {}  # 파일 백엔드: traceId → 매핑 (재시작 전까지 파일 스캔 불필요)
_CACHE_MAX = 500


def store_kind() -> str:
    """'rds' | 'file' — 감사 원문·개인데이터가 어디에 있는지 (UI 표기용)."""
    return "rds" if os.environ.get("DATA_BACKEND") == "rds" else "file"


def store_ready() -> bool:
    return _SEEDED if store_kind() == "rds" else True


def _audit_path() -> str:
    return os.environ.get("AUDIT_PATH", "/tmp/onprem-audit.jsonl")


def _rds_conn():
    import pg8000.native
    return pg8000.native.Connection(
        user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"], database=os.environ.get("PGDATABASE", "bank"),
        timeout=10)


# ---------- 시드 ----------
def ensure_rds_seed() -> None:
    """RDS에 합성 테이블·레코드와 감사 테이블을 멱등 생성한다 (컨테이너 기동 시 1회)."""
    global _SEEDED
    con = _rds_conn()
    try:
        con.run("""CREATE TABLE IF NOT EXISTS customer_profile (
            email TEXT PRIMARY KEY, profile JSONB NOT NULL)""")
        for email, p in _SYNTH.items():
            con.run("""INSERT INTO customer_profile(email, profile) VALUES(:e, CAST(:p AS JSONB))
                       ON CONFLICT (email) DO UPDATE SET profile = CAST(:p AS JSONB)""",
                    e=email, p=json.dumps(p, ensure_ascii=False))
        # 감사 원문 — 프롬프트 원문·재식별 매핑은 이 테이블 밖으로 나가지 않는다
        con.run("""CREATE TABLE IF NOT EXISTS audit_log (
            id BIGSERIAL PRIMARY KEY,
            trace_id TEXT NOT NULL, kind TEXT NOT NULL, ts BIGINT NOT NULL,
            prompt TEXT, mapping JSONB, answer TEXT, invented JSONB)""")
        con.run("CREATE INDEX IF NOT EXISTS audit_log_trace_idx ON audit_log(trace_id, kind, ts DESC)")
        con.run("CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log(ts DESC)")
    finally:
        con.close()
    _SEEDED = True


# ---------- 정확 조회 ----------
def exact_lookup(email: str) -> dict:
    email = (email or "").lower()
    if store_kind() == "rds":
        con = _rds_conn()
        try:
            rows = con.run("SELECT profile FROM customer_profile WHERE email = :e", e=email)
        finally:
            con.close()
        if rows:
            p = rows[0][0]
            return p if isinstance(p, dict) else json.loads(p)
        return _DEFAULT
    return _SYNTH.get(email, _DEFAULT)


# ---------- 감사 원문 ----------
def _remember(trace_id: str, mapping: dict) -> None:
    if len(_MAPPING_CACHE) >= _CACHE_MAX:
        _MAPPING_CACHE.pop(next(iter(_MAPPING_CACHE)))
    _MAPPING_CACHE[trace_id] = mapping


def _json_or_none(v) -> "str | None":
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def audit_write(rec: dict) -> None:
    """rec: {traceId, kind, prompt?, mapping?, answer?, invented?}. ts 는 없으면 지금."""
    rec = dict(rec)
    rec.setdefault("ts", int(time.time() * 1000))
    trace_id = str(rec.get("traceId", ""))
    if store_kind() == "rds":
        con = _rds_conn()
        try:
            con.run("""INSERT INTO audit_log(trace_id, kind, ts, prompt, mapping, answer, invented)
                       VALUES(:t, :k, :ts, :p, CAST(:m AS JSONB), :a, CAST(:i AS JSONB))""",
                    t=trace_id, k=str(rec.get("kind", "")), ts=int(rec["ts"]),
                    p=rec.get("prompt"), m=_json_or_none(rec.get("mapping")),
                    a=rec.get("answer"), i=_json_or_none(rec.get("invented")))
        finally:
            con.close()
        return
    with open(_audit_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    if rec.get("kind") == "s2.prompt" and isinstance(rec.get("mapping"), dict):
        _remember(trace_id, rec["mapping"])


def audit_mapping(trace_id: str) -> dict:
    """traceId 의 재식별 매핑 (s2.prompt 최신 1건). 없으면 {}."""
    trace_id = str(trace_id or "")
    if not trace_id:
        return {}
    if store_kind() == "rds":
        con = _rds_conn()
        try:
            rows = con.run("""SELECT mapping FROM audit_log
                              WHERE trace_id = :t AND kind = 's2.prompt'
                              ORDER BY ts DESC, id DESC LIMIT 1""", t=trace_id)
        finally:
            con.close()
        if not rows or rows[0][0] is None:
            return {}
        m = rows[0][0]
        return m if isinstance(m, dict) else (json.loads(m) or {})
    cached = _MAPPING_CACHE.get(trace_id)
    if cached is not None:
        return cached
    # 캐시 미스(프로세스 재시작 후)일 때만 파일을 훑는다
    mapping: dict = {}
    try:
        with open(_audit_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("traceId") == trace_id and r.get("kind") == "s2.prompt":
                    mapping = r.get("mapping") or {}
    except FileNotFoundError:
        pass
    if mapping:
        _remember(trace_id, mapping)
    return mapping


def _summary(trace_id, kind, ts, prompt_chars, answer_chars, mapping_count, invented_count) -> dict:
    return {"traceId": trace_id, "kind": kind, "ts": int(ts or 0),
            "promptChars": int(prompt_chars or 0), "answerChars": int(answer_chars or 0),
            "mappingCount": int(mapping_count or 0), "inventedCount": int(invented_count or 0)}


def audit_recent(n: int = 20) -> list[dict]:
    """최근 n건의 요약(길이·건수만, 원문 없음) — 최신순."""
    n = max(1, min(int(n), 100))
    if store_kind() == "rds":
        con = _rds_conn()
        try:
            rows = con.run("""SELECT trace_id, kind, ts,
                                     COALESCE(length(prompt), 0), COALESCE(length(answer), 0),
                                     CASE WHEN jsonb_typeof(mapping) = 'object'
                                          THEN (SELECT count(*) FROM jsonb_object_keys(mapping)) ELSE 0 END,
                                     CASE WHEN jsonb_typeof(invented) = 'array'
                                          THEN jsonb_array_length(invented) ELSE 0 END
                              FROM audit_log ORDER BY ts DESC, id DESC LIMIT :n""", n=n)
        finally:
            con.close()
        return [_summary(*r) for r in rows]
    tail: deque = deque(maxlen=n)
    try:
        with open(_audit_path(), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    tail.append(line)
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(tail):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        mapping = r.get("mapping")
        invented = r.get("invented")
        out.append(_summary(r.get("traceId"), r.get("kind"), r.get("ts"),
                            len(r.get("prompt") or ""), len(r.get("answer") or ""),
                            len(mapping) if isinstance(mapping, dict) else 0,
                            len(invented) if isinstance(invented, list) else 0))
    return out


def audit_total() -> int:
    """보관 중인 감사 원문 총 건수 (플레인 내부 보관 증빙용 카운터)."""
    if store_kind() == "rds":
        con = _rds_conn()
        try:
            return int(con.run("SELECT count(*) FROM audit_log")[0][0])
        finally:
            con.close()
    try:
        with open(_audit_path(), encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except FileNotFoundError:
        return 0


# ---------- 합성 카드 거래내역 (S2 Semantic Layer 시연 · SPEC v2 §6) ----------
# 가맹점명은 전부 가상이다 (§9 — 실제 상호 사용 금지). 거래는 고객·기준월 시드로 결정론적으로 생성한다.
MERCHANTS = ["아톰마트", "별빛카페", "달빛주유소", "해달서점", "구름약국",
             "바다편의점", "숲속식당", "하늘교통", "노을베이커리", "강변피트니스"]
TXN_APPROVED = "APPROVED"
TXN_CANCELLED = "CANCELLED"


def month_bounds(ref: date) -> "tuple[date, date, date]":
    """(직전월 1일, 직전월 말일, 당월 1일) — Semantic Layer '전월실적' 정의(직전월 1일~말일)의 경계."""
    cur_first = ref.replace(day=1)
    prev_last = cur_first - timedelta(days=1)
    return prev_last.replace(day=1), prev_last, cur_first


def _split_exact(rng: random.Random, total: int, n: int) -> "list[int]":
    """total 원을 n건으로 나눈다 — 100원 단위, 합계는 정확히 total."""
    n = max(1, min(n, max(1, total // 1_000)))
    weights = [rng.uniform(0.5, 1.5) for _ in range(n)]
    s = sum(weights)
    parts = [max(100, int(total * w / s) // 100 * 100) for w in weights]
    parts[-1] += total - sum(parts)
    if parts[-1] <= 0:  # 극단적으로 작은 total — 한 건으로 만든다
        return [total]
    return parts


def txn_sample(customer_id: str, ref_date: date, prev_target_krw: "int | None" = None) -> "list[dict]":
    """합성 월별 카드 거래내역 — 직전월 전체 + 당월(1일~기준일), 12~20건.

    같은 (고객, 직전월) 이면 항상 같은 행을 돌려준다 (sha256 시드). prev_target_krw 가 주어지면 직전월 **승인** 합계가
    정확히 그 값이 된다 — 프로필의 cardMonthlyKrw(계산엔진 카드 우대 판정 입력)와 전월실적이 서로 모순되지 않게.
    직전월에는 취소(CANCELLED) 건이 1~2건 섞여 있어 '취소분 제외' 정의가 실제로 결과를 바꾼다.
    행: {date: YYYY-MM-DD, merchant, amountKrw, status}
    """
    prev_first, prev_last, cur_first = month_bounds(ref_date)
    seed = int(hashlib.sha256(f"txn:{customer_id}:{prev_first.isoformat()}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    n_prev_ok, n_prev_cancel, n_cur = rng.randint(6, 9), rng.randint(1, 2), rng.randint(5, 9)   # 합계 12~20
    prev_days = calendar.monthrange(prev_first.year, prev_first.month)[1]
    rows: "list[dict]" = []

    def _row(d: date, amount: int, status: str) -> dict:
        return {"date": d.isoformat(), "merchant": rng.choice(MERCHANTS), "amountKrw": int(amount), "status": status}

    if prev_target_krw and prev_target_krw > 0:
        amounts = _split_exact(rng, int(prev_target_krw), n_prev_ok)
    else:
        amounts = [rng.randrange(3_000, 180_000, 100) for _ in range(n_prev_ok)]
    for a in amounts:
        rows.append(_row(prev_first.replace(day=rng.randint(1, prev_days)), a, TXN_APPROVED))
    for _ in range(n_prev_cancel):
        rows.append(_row(prev_first.replace(day=rng.randint(1, prev_days)), rng.randrange(5_000, 120_000, 100), TXN_CANCELLED))
    cur_rows = [_row(cur_first.replace(day=rng.randint(1, max(1, ref_date.day))), rng.randrange(3_000, 180_000, 100), TXN_APPROVED)
                for _ in range(n_cur)]
    prev_ok_sum = sum(a for a in amounts)
    if sum(r["amountKrw"] for r in cur_rows) == prev_ok_sum:  # 당월 합계가 우연히 전월과 같으면 시연 대비가 사라진다
        cur_rows[-1]["amountKrw"] += 100
    rows += cur_rows
    rows.sort(key=lambda r: (r["date"], r["merchant"], r["amountKrw"]))
    return rows
