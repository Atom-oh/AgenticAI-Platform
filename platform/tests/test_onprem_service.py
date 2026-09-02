"""온프렘 플레인 서비스 라우트 테스트 (SPEC §3.1·F3·F6·§12.3).

파일 백엔드 + 임시 AUDIT_PATH. AWS 호출 없음. HTTP 계층 테스트는 루프백 포트만 쓴다.
실행: cd platform && python3 -m pytest tests/test_onprem_service.py -q
"""
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from onprem import personal_store, service, vector_index  # noqa: E402

TOKEN_RE = re.compile(r"⟨[A-Z_]+:[0-9a-f]{8}⟩")
DEMO = "demo@atomai.click"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ONPREM_DATA_DIR", str(ROOT / "seed" / "out"))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("PLANE_TOKEN", raising=False)
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    personal_store._MAPPING_CACHE.clear()
    vector_index.reset_index()
    yield
    vector_index.reset_index()


def _prepare(trace_id="t-001", query="제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?"):
    code, out = service.handle("/s2/prepare", {"email": DEMO, "query": query, "traceId": trace_id})
    assert code == 200, out
    return out


# ---------- health ----------
def test_health_shape():
    code, out = service.handle("/health", {})
    assert code == 200
    assert out["ok"] is True and out["plane"] == "onprem"
    assert out["store"] == "file" and out["storeReady"] is True
    assert out["vectorReady"] is True and out["vectorChunks"] == 177 and out["vectorDim"] == 1024
    assert isinstance(out["uptimeSec"], int) and out["tokenRequired"] is False


# ---------- S2 prepare / finalize ----------
def test_prepare_shape_and_masking():
    prep = _prepare()
    assert {"rawValues", "rate", "limit", "maskedPayload", "maskedFields", "allowedNumbers",
            "dataSource"} <= set(prep)
    # 계산엔진 확정값 (test_calc_engine 과 동일 규칙): 4.5 - min(0.3+0.2+0.2, 0.6) = 3.90 / 2.4억-0.4억
    assert prep["rate"]["value"] == "3.90"
    assert prep["limit"]["value"] == "200000000"
    mp = prep["maskedPayload"]
    for pii in ("김데모", "CUST-0042", "ACCT-0007"):
        assert pii not in mp, f"{pii} 가 경계 페이로드에 남아 있다"
    fields = {f["field"] for f in prep["maskedFields"]}
    assert {"customerName", "customer_id", "account_id"} <= fields
    # 화면 표시용 원본 값은 신뢰 경계 내부(UI)로만 — 여기에는 그대로 있다
    assert "김데모" in prep["rawValues"]["고객"]
    assert "3.90" in prep["allowedNumbers"] and "200,000,000" in prep["allowedNumbers"]
    assert prep["dataSource"] == "file-synthetic"


def test_prepare_finalize_roundtrip_unmasks():
    prep = _prepare("t-rt")
    tokens = TOKEN_RE.findall(prep["maskedPayload"])
    assert len(tokens) >= 3
    # LLM 을 흉내내지 않는다 — 토큰이 포함된 회신 문자열로 재식별·수치 검증만 확인
    answer = (f"{tokens[0]} 고객님, 적용금리는 3.90%이며 대출 가능 한도는 200,000,000원입니다. "
              + " ".join(tokens[1:]))
    code, fin = service.handle("/s2/finalize", {"traceId": "t-rt", "answer": answer,
                                                "allowedNumbers": prep["allowedNumbers"]})
    assert code == 200
    assert set(fin) == {"unmasked", "inventedNumbers"}  # S2 핸들러가 의존하는 정확한 형태
    for original in ("김데모", "CUST-0042", "ACCT-0007"):
        assert original in fin["unmasked"]
    assert TOKEN_RE.search(fin["unmasked"]) is None
    assert fin["inventedNumbers"] == []


@pytest.mark.parametrize("email", [DEMO, "unknown@atomai.click"])
def test_prepare_masks_name_with_particles_end_to_end(email):
    """질의에 고객명+조사가 들어와도 경계 통과 페이로드(maskedPayload)에는 이름이 없어야 한다.
    DEMO → 김데모(CUST-0042), 미등록 이메일 → 기본 프로필 이방문(CUST-0777)."""
    name = personal_store.exact_lookup(email)["name"]
    q = f"{name}의 대출 한도가 얼마인가요? {name}는 우대 대상인가요? {name}에게 {name}를 안내해 주세요. 저는{name}입니다"
    code, prep = service.handle("/s2/prepare", {"email": email, "query": q, "traceId": f"t-name-{name}"})
    assert code == 200, prep
    assert name not in prep["maskedPayload"]
    assert name not in json.dumps(prep["maskedFields"], ensure_ascii=False)
    assert name in prep["rawValues"]["고객"]                  # 화면 표시용 원본은 신뢰 경계 내부에만
    token = next(m["token"] for m in prep["maskedFields"] if m["field"] == "customerName")
    assert prep["maskedPayload"].count(token) == 6            # '고객명:' 줄 1 + 질의 5
    # 재식별: 토큰 뒤에 조사가 붙은 회신도 원문 이름으로 복원된다
    code, fin = service.handle("/s2/finalize", {"traceId": f"t-name-{name}", "answer": f"{token}의 우대 판정 결과입니다.",
                                                "allowedNumbers": prep["allowedNumbers"]})
    assert code == 200 and fin["unmasked"] == f"{name}의 우대 판정 결과입니다." and fin["inventedNumbers"] == []


def test_finalize_flags_invented_numbers():
    prep = _prepare("t-inv")
    code, fin = service.handle("/s2/finalize", {"traceId": "t-inv", "answer": "적용금리는 3.15%로 예상됩니다.",
                                                "allowedNumbers": prep["allowedNumbers"]})
    assert code == 200 and "3.15" in fin["inventedNumbers"]


def test_finalize_recovers_mapping_from_file_after_restart():
    prep = _prepare("t-restart")
    token = TOKEN_RE.findall(prep["maskedPayload"])[0]
    personal_store._MAPPING_CACHE.clear()  # 프로세스 재시작 흉내 — 파일에서 복원해야 한다
    code, fin = service.handle("/s2/finalize", {"traceId": "t-restart", "answer": f"{token} 안내",
                                                "allowedNumbers": []})
    assert code == 200 and token not in fin["unmasked"]


def test_finalize_unknown_trace_returns_answer_unchanged():
    code, fin = service.handle("/s2/finalize", {"traceId": "nope", "answer": "⟨CUSTOMER_ID:deadbeef⟩",
                                                "allowedNumbers": []})
    assert code == 200 and fin["unmasked"] == "⟨CUSTOMER_ID:deadbeef⟩"


# ---------- audit ----------
def test_audit_recent_exposes_no_prompt_text(tmp_path):
    prep = _prepare("t-audit")
    service.handle("/s2/finalize", {"traceId": "t-audit", "answer": "안내문 " + prep["maskedPayload"][:40],
                                    "allowedNumbers": prep["allowedNumbers"]})
    code, out = service.handle("/audit/recent", {})
    assert code == 200
    assert out["store"] == "file" and out["count"] == 2 and out["total"] == 2
    dumped = json.dumps(out, ensure_ascii=False)
    for pii in ("김데모", "CUST-0042", "ACCT-0007", "우대금리 조건"):
        assert pii not in dumped
    kinds = {it["kind"] for it in out["items"]}
    assert kinds == {"s2.prompt", "s2.answer"}
    for it in out["items"]:
        assert not ({"prompt", "answer", "mapping", "invented"} & set(it))
        assert {"traceId", "kind", "ts", "promptChars", "answerChars", "mappingCount", "inventedCount"} <= set(it)
    prompt_row = next(it for it in out["items"] if it["kind"] == "s2.prompt")
    assert prompt_row["promptChars"] > 0 and prompt_row["mappingCount"] >= 3
    # 최신순
    assert out["items"][0]["kind"] == "s2.answer"
    # 원문은 플레인 내부 저장소(파일)에는 남아 있어야 한다 — 증빙용
    on_disk = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "김데모" in on_disk and "CUST-0042" in on_disk


def test_audit_recent_n_is_clamped():
    for i in range(3):
        _prepare(f"t-n{i}")
    code, out = service.handle("/audit/recent", {"n": 2})
    assert code == 200 and out["count"] == 2 and out["total"] == 3
    code, out = service.handle("/audit/recent", {"n": 0})
    assert code == 200 and out["count"] == 3  # 0/누락은 기본값 20
    code, out = service.handle("/audit/recent", {"n": -5})
    assert code == 200 and out["count"] == 1  # 하한 1
    code, out = service.handle("/audit/recent", {"n": 10_000})
    assert code == 200 and out["count"] == 3  # 상한 100 (행은 3개)


# ---------- vector search ----------
def test_vector_search_with_stored_embedding_ranks_hero_first():
    idx = vector_index.get_index()
    i0 = next(i for i, c in enumerate(idx.chunks) if c["chunkId"] == "REG-LN-001#c0")
    code, out = service.handle("/vector/search", {"query": "전세자금대출 담보",
                                                  "queryEmbedding": idx.emb[i0], "topK": 5})
    assert code == 200
    hits = out["hits"]
    assert len(hits) == 5 and out["total"] == 177
    assert hits[0]["chunkId"] == "REG-LN-001#c0" and hits[0]["regCode"] == "REG-LN-001"
    assert all(h["stage"] == "onprem-hybrid" for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    for h in hits:
        assert {"chunkId", "regCode", "text", "score", "stage"} <= set(h)
    assert "bm25_ms" in out["timing"] and "dense_ms" in out["timing"]


def test_vector_search_bm25_only_without_embedding():
    code, out = service.handle("/vector/search", {"query": "전세자금대출 담보", "topK": 3})
    assert code == 200 and len(out["hits"]) == 3
    assert all(h["stage"] == "onprem-bm25" for h in out["hits"])


def test_vector_search_validation():
    assert service.handle("/vector/search", {"queryEmbedding": [0.1]})[0] == 400
    assert service.handle("/vector/search", {"query": "x", "queryEmbedding": ["a", "b"]})[0] == 400
    assert service.handle("/vector/search", {"query": "x", "queryEmbedding": [0.1, 0.2]})[0] == 400
    assert service.handle("/vector/search", {"query": "x", "queryEmbedding": []})[0] == 400
    assert service.handle("/no/such", {})[0] == 404


def test_vector_search_503_when_corpus_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ONPREM_DATA_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr(vector_index, "HERE", tmp_path / "nowhere")  # onprem/data · seed/out 폴백도 차단
    vector_index.reset_index()
    code, out = service.handle("/vector/search", {"query": "x"})
    assert code == 503 and "error" in out
    code, health = service.handle("/health", {})
    assert code == 200 and health["vectorReady"] is False and health["vectorChunks"] == 0


# ---------- token ----------
def test_token_enforcement(monkeypatch):
    monkeypatch.setenv("PLANE_TOKEN", "s3cret-token")
    body = {"email": DEMO, "query": "q", "traceId": "t-tok"}
    assert service.handle("/s2/prepare", body, headers={})[0] == 401
    assert service.handle("/s2/prepare", body, headers=None)[0] == 401
    assert service.handle("/s2/prepare", body, headers={"X-Plane-Token": "wrong"})[0] == 401
    assert service.handle("/s2/prepare", body, headers={"x-plane-token": "s3cret-token"})[0] == 200
    assert service.handle("/vector/search", {"query": "x"}, headers={"X-Plane-Token": "s3cret-token"})[0] == 200
    # /health 는 ALB 헬스체크·브리지 점검용 — 토큰 없이 허용, 단 tokenRequired 를 알린다
    code, out = service.handle("/health", {}, headers={})
    assert code == 200 and out["tokenRequired"] is True


def test_bad_request_does_not_leak_values():
    code, out = service.handle("/s2/finalize", {"traceId": "t", "answer": "x", "allowedNumbers": "notalist"})
    assert code == 400 and "notalist" not in json.dumps(out)


# ---------- masking rules ----------
def test_masking_new_rules_roundtrip():
    from onprem.masking import mask, strip_tokens, unmask
    text = ("고객 홍길동님(주민 900101-1234567, 카드 1234 5678 9012 3456, 연락처 010 1234 5678, "
            "16자리 1111222233334444) 안내. 금액 300,000,000원과 금리 4.5%는 그대로.")
    r = mask(text, {"customerName": "홍길동"})
    for leaked in ("홍길동", "900101", "9012 3456", "010 1234", "1111222233334444"):
        assert leaked not in r.text, leaked
    assert "300,000,000원" in r.text and "4.5%" in r.text  # 금액·금리는 마스킹 대상이 아니다
    fields = {m["field"] for m in r.masked_fields}
    assert {"customerName", "kr_rrn", "card", "phone"} <= fields
    assert unmask(r.text, r.mapping) == text
    assert not re.search(r"[0-9a-f]{8}", strip_tokens(r.text).replace("1111222233334444", ""))


def test_masking_name_followed_by_korean_particle_is_masked():
    """한국어 조사·호칭·서술어는 이름에 띄어쓰기 없이 붙는다 — 어느 형태든 이름이 경계를 넘으면 안 된다."""
    from onprem.masking import mask, unmask
    name = "김데모"
    suffixes = ["의", "은", "는", "이", "가", "을", "를", "에게", "께", "께서", "께서는", "과", "와", "도", "만",
                "한테", "에", "로", "으로", "이나", "나", "이랑", "랑", "부터", "까지", "처럼", "보다", "조차",
                "님", "씨", "고객", "고객님", "귀하", "대리", "입니다", "이고", "인가요", "이신가요"]
    src = " ".join(f"{name}{sfx}" for sfx in suffixes) + f" 그리고 {name}, {name}. 저는{name}입니다 {name}"
    r = mask(src, {"customerName": name})
    assert name not in r.text
    assert sum(1 for m in r.masked_fields if m["field"] == "customerName") == 1   # 같은 값 → 토큰 1개
    assert unmask(r.text, r.mapping) == src
    # 리뷰에서 재현된 실제 질의 형태 (조사 '의'·'는')
    q = "김데모의 대출 한도가 얼마인가요? 김데모는 우대 대상인가요?"
    r2 = mask(q, {"customerName": name})
    assert "김데모" not in r2.text and unmask(r2.text, r2.mapping) == q


def test_masking_name_boundaries():
    from onprem.masking import mask, unmask
    # 라틴 문자·숫자·밑줄 경계는 유지: "Ann" 은 "Announcement"/"Ann_1" 안에서 잡히지 않는다
    src = "Announcement for Ann_1 and Ann; Ann's account"
    r = mask(src, {"customerName": "Ann"})
    assert "Announcement" in r.text and "Ann_1" in r.text
    assert r.text.count("⟨") == 2 and unmask(r.text, r.mapping) == src
    # 한글은 경계가 아니다: 고객명이 다른 낱말 안에 있어도 마스킹한다 — 과잉 마스킹이 안전한 실패, 재식별은 원문 복원
    src2 = "이방문 고객과 이방 대리, 그리고 이방님"
    r2 = mask(src2, {"customerName": "이방"})
    assert "이방" not in r2.text
    assert sum(1 for m in r2.masked_fields if m["field"] == "customerName") == 1
    assert unmask(r2.text, r2.mapping) == src2
    # 한 글자·빈 값은 명시 마스킹에서 제외 (오탐 방지)
    assert mask("이 문장", {"customerName": "이"}).masked_fields == []
    assert mask("문장", {"customerName": ""}).masked_fields == []


# ---------- HTTP layer ----------
@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), service.H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _post(base, path, data: bytes, ctype="application/json", headers=None):
    h = {"Content-Type": ctype, **(headers or {})}
    req = urllib.request.Request(base + path, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_http_rejects_non_json_oversize_and_malformed(server):
    assert _post(server, "/vector/search", b"query=x", ctype="text/plain")[0] == 415
    assert _post(server, "/vector/search", b"not json")[0] == 400
    assert _post(server, "/vector/search", b"[1,2,3]")[0] == 400
    big = json.dumps({"query": "x", "pad": "a" * (service.MAX_BODY + 1024)}).encode()
    code, out = _post(server, "/vector/search", big)
    assert code == 413
    assert str(service.MAX_BODY) in out["error"]


def test_http_happy_paths(server):
    with urllib.request.urlopen(server + "/health", timeout=10) as r:
        assert r.status == 200
        assert json.loads(r.read().decode())["plane"] == "onprem"
    code, out = _post(server, "/vector/search", json.dumps({"query": "전세자금대출 담보", "topK": 2}).encode())
    assert code == 200 and len(out["hits"]) == 2 and out["hits"][0]["regCode"] == "REG-LN-001"
    code, out = _post(server, "/audit/recent", json.dumps({}).encode())
    assert code == 200 and out["store"] == "file"


def test_http_token_via_header(server, monkeypatch):
    monkeypatch.setenv("PLANE_TOKEN", "hdr-token")
    body = json.dumps({"query": "x"}).encode()
    assert _post(server, "/vector/search", body)[0] == 401
    assert _post(server, "/vector/search", body, headers={"X-Plane-Token": "hdr-token"})[0] == 200
