"""VPC 내부 플레인 서비스 (SPEC v2 §3) — NAT 없는 프라이빗 서브넷의 ECS Fargate에서 실행.

담당: 정확 조회 · 결정론적 계산 · 마스킹/재식별 · 감사 원문 보관 · 벡터 인덱스(하이브리드 검색).
프롬프트 원문은 이 플레인 밖으로 나가지 않는다 — stdout(CloudWatch)에는 메트릭과 traceId만 (§12.3).

의존성 없는 표준 라이브러리 HTTP 서버 — 컨테이너는 인터넷 접근이 없다.
입구 통제: 내부 ALB(보안그룹) + PLANE_TOKEN 이 설정되면 X-Plane-Token 헤더 일치 요구 (/health 제외).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # 컨테이너(플랫 배치: /app/*.py)
    import aoss_index
    import personal_store
    import vector_index
    from calc_engine import jeonse_loan_limit, preferential_rate, verify_no_generated_numbers
    from masking import mask, strip_tokens, unmask
except ImportError:  # 패키지로 import 될 때 (api 로컬 폴백 ALLOW_LOCAL_PLANE=1 · 테스트)
    from onprem import aoss_index, personal_store, vector_index
    from onprem.calc_engine import jeonse_loan_limit, preferential_rate, verify_no_generated_numbers
    from onprem.masking import mask, strip_tokens, unmask

STARTED = time.time()
MAX_BODY = 64 * 1024                 # 요청 본문 상한 (질의 임베딩 1024차원 ≈ 10~20KB)
TOKEN_HEADER = "X-Plane-Token"
PUBLIC_PATHS = {"/health"}           # ALB 헬스체크·브리지 점검은 토큰 없이 허용
_FORBIDDEN_LOG_KEYS = {"prompt", "query", "answer", "payload", "maskedPayload", "text", "email",
                       "mapping", "system", "user", "message", "content", "queryEmbedding"}


def _log(event: str, **fields) -> None:
    """JSON 라인 로그 — 메트릭·traceId만. 금지 키는 길이로 치환한다 (§12.3)."""
    rec = {"ts": int(time.time() * 1000), "event": event, "plane": "onprem"}
    for k, v in fields.items():
        if k in _FORBIDDEN_LOG_KEYS:
            rec[f"{k}Len"] = len(v) if hasattr(v, "__len__") else 0
        else:
            rec[k] = v
    sys.stdout.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


# ---------- 인증 ----------
def _token() -> str:
    return os.environ.get("PLANE_TOKEN", "")


def authorized(path: str, headers) -> bool:
    """PLANE_TOKEN 이 설정되어 있으면 X-Plane-Token 헤더가 일치해야 한다 (/health 는 예외)."""
    tok = _token()
    if not tok or path in PUBLIC_PATHS:
        return True
    given = ""
    if headers is not None:
        for k, v in dict(headers).items():
            if str(k).lower() == TOKEN_HEADER.lower():
                given = str(v)
                break
    return hmac.compare_digest(given, tok)


# ---------- Semantic Layer 지표 — 계산엔진 몫의 순수 함수 (SPEC v2 §6, §12.4) ----------
# 정의는 semantic/metrics.yaml 의 '전월실적' 과 같다. LLM 은 이 값을 인용만 하고, 토글 OFF 시연에서는 정의 없이 스스로 계산한다.
METRIC_DEFS = {
    "전월실적": "직전월 1일~말일 승인 합계 (취소분 제외)",
    "당월실적": "당월 1일~기준일 승인 합계 (취소분 제외)",
}


def monthly_metrics(txns: "list[dict]", ref_date: date, diagnostics: bool = False) -> dict:
    """거래내역 → {전월실적, 당월실적} (원). diagnostics=True 면 '조용히 틀리는' 후보값도 함께 돌려준다
    (당월 합계·취소 포함 합계·2개월 합계) — 출력 검증기가 모델 오답의 원인을 이름 붙이는 데 쓴다."""
    prev_first, prev_last, cur_first = personal_store.month_bounds(ref_date)
    prev_ok = prev_all = cur_ok = cur_all = 0
    for t in txns or []:
        d = date.fromisoformat(str(t["date"]))
        amt = int(t["amountKrw"])
        ok = str(t.get("status", "")) == personal_store.TXN_APPROVED
        if prev_first <= d <= prev_last:
            prev_all += amt
            if ok:
                prev_ok += amt
        elif cur_first <= d <= ref_date:
            cur_all += amt
            if ok:
                cur_ok += amt
    out = {"전월실적": prev_ok, "당월실적": cur_ok}
    if diagnostics:
        out.update({"전월_취소포함": prev_all, "당월_취소포함": cur_all, "2개월_승인합계": prev_ok + cur_ok,
                    "2개월_취소포함": prev_all + cur_all})
    return out


def _truthy(v, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "off", "no", "")


def _parse_ref_date(v) -> date:
    """기준일 (테스트·리허설용 고정값). 없거나 형식이 틀리면 오늘."""
    if v:
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            pass
    return date.today()


def _txn_block(txns: "list[dict]", ref_date: date) -> str:
    """토글 OFF 프롬프트용 거래내역 원본 — 콜론 없는 줄(게이트 필드 계측이 거래 행을 '필드'로 세지 않게)."""
    lines = [f"[카드 거래내역 원본 — 직전월·당월 (기준일 {ref_date.isoformat()})]"]
    for t in txns:
        status = "승인" if t["status"] == personal_store.TXN_APPROVED else "취소"
        lines.append(f"{t['date']} · {t['merchant']} · {int(t['amountKrw']):,}원 · {status}")
    return "\n".join(lines)


# ---------- 라우트 ----------
def _health() -> tuple[int, dict]:
    """헬스 — AOSS 는 마지막 관측값(snapshot)만 보고한다. ALB 헬스체크가 AOSS 호출에 막히면 안 된다."""
    idx = vector_index.try_get_index()
    backend, aidx = aoss_index.get_vector_backend()
    snap = aidx.snapshot() if aidx is not None else {}
    return 200, {"ok": True, "plane": "onprem",
                 "store": personal_store.store_kind(), "storeReady": personal_store.store_ready(),
                 "vectorChunks": len(idx.chunks) if idx else 0, "vectorDim": idx.dim if idx else 0,
                 "vectorReady": idx is not None,
                 "vectorBackend": backend, "aossDocs": snap.get("docs"), "aossIndex": snap.get("index"),
                 "aossReady": bool(snap.get("docs")), "aossLastError": snap.get("lastError"),
                 "uptimeSec": int(time.time() - STARTED), "tokenRequired": bool(_token())}


def _s2_prepare(body: dict) -> tuple[int, dict]:
    """정확 조회 → 계산 → 마스킹까지 VPC 내부에서 일괄 수행.
    반환: 화면 표시용 원본 값(플랫폼 UI는 신뢰 경계 내부), 계산 내역, 경계 통과용 마스킹 페이로드,
    합성 카드 거래내역(txnSample)과 계산엔진이 낸 지표(metricByEngine: 전월실적·당월실적).
    body.semanticLayer(기본 true): true 면 프롬프트에 '전월실적' 정의 + 계산엔진 값을, false 면 정의 없이 거래내역 원본을 넣어
    모델이 스스로 계산하게 한다 (SPEC v2 §6 데모 포인트 — 조용히 틀리는 숫자). body.refDate: 기준일 고정(테스트·리허설).
    재식별 매핑과 프롬프트 원문은 반환하지 않고 감사 저장소에만 보관한다."""
    email = str(body.get("email", ""))
    query = str(body.get("query", ""))[:500]
    trace_id = str(body.get("traceId", ""))[:64]
    semantic_on = _truthy(body.get("semanticLayer"), default=True)
    ref_date = _parse_ref_date(body.get("refDate"))
    profile = personal_store.exact_lookup(email)
    rate = preferential_rate(profile, profile["product"]["baseRate"])
    limit = jeonse_loan_limit(profile["jeonse"]["depositKrw"],
                              profile["jeonse"]["guaranteeRatio"],
                              profile["jeonse"]["annualIncomeKrw"],
                              profile["jeonse"]["existingDebtKrw"])
    txns = personal_store.txn_sample(profile["customerId"], ref_date,
                                     prev_target_krw=int(profile.get("cardMonthlyKrw", 0) or 0))
    metrics_full = monthly_metrics(txns, ref_date, diagnostics=True)
    metrics = {k: metrics_full[k] for k in ("전월실적", "당월실적")}
    prev_first, prev_last, cur_first = personal_store.month_bounds(ref_date)
    if semantic_on:
        semantic_block = (f"- 전월실적: {metrics['전월실적']:,}원 "
                          f"(Semantic Layer 정의: {METRIC_DEFS['전월실적']})\n")
        raw_block = ""
    else:  # 정의를 주지 않는다 — 모델이 '전월실적'을 스스로 해석·계산한다 (안티패턴 시연)
        semantic_block = ""
        raw_block = (_txn_block(txns, ref_date) + "\n"
                     "전월실적의 정의는 제공되지 않습니다. 위 거래내역에서 고객의 '전월실적'을 직접 계산해 "
                     "'전월실적 N원' 형태로 금액을 명시하세요.\n\n")
    prompt = (f"고객 세그먼트: {profile['segment']}\n"
              f"고객명: {profile['name']}\n"
              f"고객 식별: {profile['customerId']} / 계좌 {profile['account']['accountId']}\n"
              f"상품: {profile['product']['name']}\n"
              f"[계산엔진 확정값 — 이 숫자만 사용할 것]\n"
              f"- 적용금리: {rate.value}% (기본 {profile['product']['baseRate']}%)\n"
              f"- 우대 판정: " + "; ".join(f"{s.label}→{s.value}" for s in rate.steps[1:-1]) + "\n"
              f"- 대출 가능 한도: {int(limit.value):,}원\n"
              + semantic_block + "\n" + raw_block + f"질문: {query}")
    m = mask(prompt, {"customerName": profile["name"]})
    # 재식별 매핑과 프롬프트 원문은 VPC 내부 감사 저장소에만
    personal_store.audit_write({"traceId": trace_id, "kind": "s2.prompt",
                                "prompt": prompt, "mapping": m.mapping})
    raw_values = {
        "고객": f"{profile['name']} ({profile['customerId']}, {profile['segment']})",
        "상품": f"{profile['product']['name']} · 기본금리 {profile['product']['baseRate']}%",
        "급여이체": f"{profile['salaryTransferMonths']}개월 연속",
        "전월 카드사용": f"{profile['cardMonthlyKrw']:,}원",
        "자동이체": f"{profile['autoTransferCount']}건",
        "생애최초/신혼": f"{profile['isFirstHome']}/{profile['isNewlywed']}",
        "임차보증금": f"{profile['jeonse']['depositKrw']:,}원",
        "연소득": f"{profile['jeonse']['annualIncomeKrw']:,}원",
        "기존대출": f"{profile['jeonse']['existingDebtKrw']:,}원",
    }
    allowed = ([str(rate.value), f"{int(limit.value):,}", str(int(limit.value)),
                profile["product"]["baseRate"], f"{profile['cardMonthlyKrw']:,}",
                f"{profile['jeonse']['depositKrw']:,}", f"{profile['jeonse']['annualIncomeKrw']:,}",
                f"{profile['jeonse']['existingDebtKrw']:,}"]
               + [s.value for s in rate.steps] + [s.value for s in limit.steps]
               + [s.formula for s in rate.steps] + [s.formula for s in limit.steps]
               + [f"{metrics['전월실적']:,}"])   # 계산엔진 전월실적 — 두 모드 모두 인용 허용
    if not semantic_on:
        # 거래내역 원본이 프롬프트에 있으므로 원장 숫자(각 거래액·당월 합계)는 '만들어낸 수치'가 아니다.
        # 당월 합계를 '전월실적'이라고 말하면 수치 검증기는 통과하고 semantic_check 만 잡는다 — 그것이 '조용히 틀림' 이다.
        allowed += [f"{t['amountKrw']:,}" for t in txns] + [f"{metrics['당월실적']:,}"]
    return 200, {"rawValues": raw_values, "rate": rate.to_dict(), "limit": limit.to_dict(),
                 "maskedPayload": m.text,
                 "maskedFields": m.masked_fields,
                 "allowedNumbers": [a.replace("%", "").replace("원", "").replace("%p", "")
                                    for a in allowed],
                 "dataSource": os.environ.get("DATA_SOURCE", f"{personal_store.store_kind()}-synthetic"),
                 # --- SPEC v2 §6 (추가 키 — 기존 키는 그대로) ---
                 "txnSample": txns,
                 "metricByEngine": metrics,
                 "metricDefinitions": dict(METRIC_DEFS),
                 "metricDiagnostics": {k: v for k, v in metrics_full.items() if k not in metrics},
                 "metricPeriod": {"refDate": ref_date.isoformat(), "prevMonth": prev_first.strftime("%Y-%m"),
                                  "prevFrom": prev_first.isoformat(), "prevTo": prev_last.isoformat(),
                                  "currentMonth": cur_first.strftime("%Y-%m")},
                 "semanticLayer": semantic_on}


def _s2_finalize(body: dict) -> tuple[int, dict]:
    """재식별 + 수치 검증. 매핑은 감사 저장소에서 traceId로 복원한다 — 경계를 넘지 않는다."""
    trace_id = str(body.get("traceId", ""))[:64]
    answer = str(body.get("answer", ""))
    allowed = body.get("allowedNumbers") or []
    if not isinstance(allowed, list):
        raise ValueError("allowedNumbers must be a list")
    mapping = personal_store.audit_mapping(trace_id)
    # 토큰(⟨KIND:hash⟩)의 해시 숫자열은 LLM이 만든 수치가 아니다 — 걷어낸 뒤 검증한다
    invented = verify_no_generated_numbers(strip_tokens(answer), [str(a) for a in allowed])
    personal_store.audit_write({"traceId": trace_id, "kind": "s2.answer",
                                "answer": answer, "invented": invented})
    return 200, {"unmasked": unmask(answer, mapping), "inventedNumbers": invented}


def _audit_recent(body: dict) -> tuple[int, dict]:
    """감사 원문 요약 — 길이·건수만 노출한다. 원문은 플레인 내부 보관 증빙용."""
    n = int(body.get("n", 20) or 20)
    items = personal_store.audit_recent(n)
    return 200, {"count": len(items), "total": personal_store.audit_total(),
                 "store": personal_store.store_kind(), "items": items}


def _vector_search(body: dict) -> tuple[int, dict]:
    """하이브리드 검색(BM25 + dense + RRF) — 질의 임베딩은 클라우드가 계산해 전달한다."""
    query = str(body.get("query", ""))[:500].strip()
    if not query:
        return 400, {"error": "query is required"}
    q_emb = body.get("queryEmbedding")
    if q_emb is not None:
        if not isinstance(q_emb, list) or not q_emb or \
                not all(isinstance(x, (int, float)) for x in q_emb):
            return 400, {"error": "queryEmbedding must be a non-empty list of numbers"}
    top_k = max(1, min(int(body.get("topK", vector_index.FUSED_LIMIT) or vector_index.FUSED_LIMIT), 50))
    backend, aidx = aoss_index.get_vector_backend()
    fallback_reason = None
    if backend == "aoss":
        try:
            hits, timing = aidx.search(query, q_emb, top_k)   # ValueError(차원 불일치) 는 handle() 이 400 으로
            timing["vectorBackend"] = "aoss"
            return 200, {"hits": hits, "timing": timing, "total": aidx.last_docs}
        except aoss_index.AossError as e:  # AOSS 장애 → 컨테이너 내 인덱스로 대체하되 배지를 단다 (§11)
            fallback_reason = type(e).__name__
            _log("vector.aoss_fallback", error=fallback_reason, traceId=str(body.get("traceId", ""))[:64])
    idx = vector_index.try_get_index()
    if idx is None:
        return 503, {"error": "vector index not loaded (corpus.jsonl 없음 — onprem/data 확인)"}
    if q_emb is not None and idx.dim and len(q_emb) != idx.dim:
        return 400, {"error": f"queryEmbedding dim {len(q_emb)} != index dim {idx.dim}"}
    hits, timing = idx.search(query, q_emb, top_k)
    if fallback_reason:
        for h in hits:
            h["stage"] = "memory-fallback"
        timing["vectorBackend"] = "memory-fallback"
        timing["aossError"] = fallback_reason
    else:
        timing["vectorBackend"] = "memory"
    return 200, {"hits": hits, "timing": timing, "total": len(idx.chunks)}


def _vector_bootstrap(body: dict) -> tuple[int, dict]:
    """AOSS 인덱스 생성 + 코퍼스 적재 (멱등). {force?} → {created, indexed, count, expected, skipped}.
    브리지 op=onprem path=/vector/bootstrap 으로 수동 트리거한다."""
    backend, aidx = aoss_index.get_vector_backend()
    if backend != "aoss":
        return 409, {"error": "AOSS 미설정 (VECTOR_BACKEND=aoss · AOSS_ENDPOINT 필요)", "vectorBackend": backend}
    idx = vector_index.try_get_index()
    if idx is None or not idx.emb:
        return 503, {"error": "corpus/embeddings not loaded — onprem/data 확인"}
    try:
        out = aidx.bootstrap(idx.chunks, idx.emb, force=bool(body.get("force")))
    except aoss_index.AossError as e:
        return 503, {"error": f"aoss: {type(e).__name__}", "detail": str(e)[:300]}
    out["vectorBackend"] = "aoss"
    return 200, out


_ROUTES = {
    "/health": lambda body: _health(),
    "/s2/prepare": _s2_prepare,
    "/s2/finalize": _s2_finalize,
    "/audit/recent": _audit_recent,
    "/vector/search": _vector_search,
    "/vector/bootstrap": _vector_bootstrap,
}


def _metrics(path: str, code: int, out: dict) -> dict:
    """응답에서 로그용 메트릭만 뽑는다 (원문 없음)."""
    m: dict = {}
    if code != 200:
        return m
    if path == "/s2/prepare":
        m["maskedFieldCount"] = len(out.get("maskedFields") or [])
        m["maskedPayloadChars"] = len(out.get("maskedPayload") or "")
        m["txnRows"] = len(out.get("txnSample") or [])
        m["semanticLayer"] = out.get("semanticLayer")
    elif path == "/s2/finalize":
        m["inventedCount"] = len(out.get("inventedNumbers") or [])
        m["unmaskedChars"] = len(out.get("unmasked") or "")
    elif path == "/vector/search":
        m["hits"] = len(out.get("hits") or [])
        timing = out.get("timing") or {}
        m.update({k: v for k, v in timing.items() if isinstance(v, (int, float))})
        m["vectorBackend"] = timing.get("vectorBackend")
    elif path == "/vector/bootstrap":
        m.update({k: out.get(k) for k in ("created", "indexed", "count", "skipped")})
    elif path == "/audit/recent":
        m["count"] = out.get("count", 0)
    return m


def handle(path: str, body: dict, headers=None) -> tuple[int, dict]:
    """라우터 — 테스트와 로컬 폴백(api ALLOW_LOCAL_PLANE=1)이 직접 호출한다."""
    t0 = time.time()
    path = (path or "").split("?", 1)[0]
    body = body if isinstance(body, dict) else {}
    trace_id = str(body.get("traceId", ""))[:64]
    if not authorized(path, headers):
        _log("plane.unauthorized", path=path, traceId=trace_id)
        return 401, {"error": "unauthorized: X-Plane-Token missing or invalid"}
    route = _ROUTES.get(path)
    if route is None:
        return 404, {"error": f"no route: {path}"}
    try:
        code, out = route(body)
    except (KeyError, TypeError, ValueError) as e:  # 잘못된 요청 — 값은 로그에 남기지 않는다
        code, out = 400, {"error": f"bad request: {type(e).__name__}"}
    except Exception as e:  # 내부 오류 — 종류만 기록 (원문 유출 방지)
        code, out = 500, {"error": f"internal: {type(e).__name__}"}
        if os.environ.get("PLANE_DEBUG") == "1":
            out["detail"] = str(e)[:300]
    _log("plane.request", path=path, status=code, ms=int((time.time() - t0) * 1000),
         traceId=trace_id, store=personal_store.store_kind(), **_metrics(path, code, out))
    return code, out


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    server_version = "onprem-plane/1.1"

    def log_message(self, *a):  # 프롬프트 원문이 액세스 로그로 새지 않게 기본 로그 끔
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(*handle(self.path, {}, self.headers))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "invalid Content-Length"}); return
        if n > MAX_BODY:
            if n <= 8 * MAX_BODY:  # 소폭 초과는 비워 주어 클라이언트가 413 을 정상 수신하게 한다
                self.rfile.read(n)
            self.close_connection = True
            self._send(413, {"error": f"body too large (max {MAX_BODY} bytes)"}); return
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" not in ctype:
            self._send(415, {"error": "Content-Type must be application/json"}); return
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "invalid JSON"}); return
        if not isinstance(body, dict):
            self._send(400, {"error": "JSON body must be an object"}); return
        self._send(*handle(self.path, body, self.headers))


def _seed_rds_forever() -> None:
    """RDS 기동 대기 + 멱등 시드. 백그라운드에서 돌려 /health 가 즉시 응답하게 한다
    (ECS 컨테이너 헬스체크 startPeriod 안에 리슨해야 태스크가 죽지 않는다). 성공 전까지 storeReady=false."""
    for attempt in range(60):
        try:
            personal_store.ensure_rds_seed()
            _log("rds.seed_ok", attempt=attempt)
            return
        except Exception as e:
            _log("rds.seed_retry", attempt=attempt, error=type(e).__name__)
            time.sleep(10)
    _log("rds.seed_gave_up", attempts=60)


def _bootstrap_aoss_forever() -> None:
    """AOSS 인덱스 보장 + 코퍼스 적재 (멱등: count ≥ 코퍼스면 건너뜀). RDS 시드처럼 백그라운드 —
    데이터 접근 정책 전파·VPC 엔드포인트 준비를 기다린다. 성공 전까지 /vector/search 는 memory-fallback 배지."""
    idx = vector_index.try_get_index()
    _, aidx = aoss_index.get_vector_backend()
    if idx is None or aidx is None or not idx.emb:
        _log("aoss.bootstrap_skipped", reason="corpus or backend missing")
        return
    for attempt in range(30):
        try:
            out = aidx.bootstrap(idx.chunks, idx.emb)
            _log("aoss.bootstrap_ok", attempt=attempt,
                 **{k: out.get(k) for k in ("created", "indexed", "count", "skipped")})
            return
        except aoss_index.AossError as e:
            _log("aoss.bootstrap_retry", attempt=attempt, error=type(e).__name__)
            time.sleep(10)
    _log("aoss.bootstrap_gave_up", attempts=30)


def _boot() -> None:
    """기동: 벡터 인덱스 선적재 → (RDS 시드·AOSS 부트스트랩은 백그라운드) → 리슨."""
    import threading
    if personal_store.store_kind() == "rds":
        threading.Thread(target=_seed_rds_forever, name="rds-seed", daemon=True).start()
    t0 = time.time()
    try:
        idx = vector_index.get_index()
        _log("vector.loaded", chunks=len(idx.chunks), dim=idx.dim, ms=int((time.time() - t0) * 1000),
             source=idx.source)
    except (FileNotFoundError, ValueError) as e:
        _log("vector.missing", error=str(e)[:200])
    backend, _ = aoss_index.get_vector_backend()
    if backend == "aoss":
        threading.Thread(target=_bootstrap_aoss_forever, name="aoss-bootstrap", daemon=True).start()
    _log("plane.listen", port=8080, store=personal_store.store_kind(), tokenRequired=bool(_token()),
         vectorBackend=backend)


if __name__ == "__main__":
    _boot()
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
