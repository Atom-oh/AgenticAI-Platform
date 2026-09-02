"""engine.vectorrag ↔ 온프렘 플레인 계약 테스트 (SPEC §3.2·F2·§12.5).

bridge/direct 모드에서 Vector RAG 는 (1) 질의 임베딩만 클라우드에서 계산해 (2) 플레인의
/vector/search 를 호출하고 (3) 결과를 클라우드에서 리랭크한다. 여기서는 common.plane 을
가짜 모듈로 주입해 실제 onprem.service 라우터를 그대로 태우고, Bedrock 호출(embed/rerank)만
저장된 임베딩·결정론적 순서로 대체한다. AWS 호출 없음.
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from onprem import personal_store, service, vector_index  # noqa: E402

HERO_CHUNK = "REG-LN-001#c0"


@pytest.fixture
def plane_calls(tmp_path, monkeypatch):
    """가짜 common.plane — mode()='bridge', call() 은 온프렘 서비스 라우터를 JSON 왕복으로 호출."""
    monkeypatch.setenv("AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ONPREM_DATA_DIR", str(ROOT / "seed" / "out"))
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("PLANE_TOKEN", raising=False)
    personal_store._MAPPING_CACHE.clear()
    vector_index.reset_index()
    calls: list = []

    class PlaneUnavailable(Exception):
        pass

    def call(path, body, timeout=20):
        body = json.loads(json.dumps(body))  # 브리지 Lambda 경유와 같은 JSON 직렬화 경계
        calls.append((path, body))
        code, out = service.handle(path, body)
        if code != 200:
            raise PlaneUnavailable(f"HTTP {code}: {json.dumps(out, ensure_ascii=False)[:200]}")
        return json.loads(json.dumps(out, ensure_ascii=False))

    plane = types.ModuleType("common.plane")
    plane.PlaneUnavailable = PlaneUnavailable
    plane.mode = lambda: "bridge"
    plane.call = call
    common = types.ModuleType("common")
    common.plane = plane
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setitem(sys.modules, "common.plane", plane)
    yield calls
    vector_index.reset_index()


def _hero_embedding():
    idx = vector_index.get_index()
    return next(e for c, e in zip(idx.chunks, idx.emb) if c["chunkId"] == HERO_CHUNK)


def test_plane_mode_routes_search_to_onprem_and_reranks_in_cloud(plane_calls, monkeypatch):
    from engine import bedrock, vectorrag
    hero = _hero_embedding()
    embed_calls: list = []

    def fake_embed(texts):
        embed_calls.append(list(texts))
        return [hero for _ in texts]

    def fake_rerank(query, docs, top_n=5):  # 결정론적: 입력 순서 유지, 점수 감소
        return [(i, round(1.0 - i * 0.1, 3)) for i in range(min(top_n, len(docs)))]

    monkeypatch.setattr(bedrock, "embed", fake_embed)
    monkeypatch.setattr(bedrock, "rerank", fake_rerank)
    # 로컬 인덱스는 절대 쓰이지 않아야 한다
    monkeypatch.setattr(vectorrag.HybridIndex, "load",
                        classmethod(lambda cls: pytest.fail("플레인 모드에서 로컬 인덱스를 적재했다")))

    assert vectorrag.search_mode() == "plane"
    assert "온프렘" in vectorrag.search_label()
    query = "전세자금대출 담보 인정 규정이 개정되면 영향받는 상품은?"
    hits, timing, system, user = vectorrag.prepare(query)

    # (1) 임베딩은 질문 텍스트 1건만 클라우드로
    assert embed_calls == [[query]]
    # (2) 플레인 호출 1회, 임베딩 1024차원(소수 6자리 반올림), topK=융합 후보 수
    assert len(plane_calls) == 1
    path, body = plane_calls[0]
    assert path == "/vector/search" and body["query"] == query and body["topK"] == vectorrag.FUSED_LIMIT
    assert len(body["queryEmbedding"]) == 1024
    assert all(abs(x - round(x, 6)) < 1e-12 for x in body["queryEmbedding"])
    # (3) 결과: 플레인 융합 1위(히어로 청크)가 리랭크 후에도 1위, stage 는 'reranked'
    assert len(hits) == 5
    assert hits[0].chunk["chunkId"] == HERO_CHUNK and hits[0].chunk["regCode"] == "REG-LN-001"
    assert all(h.stage == "reranked" for h in hits)
    assert "stage" not in hits[0].chunk and "score" not in hits[0].chunk
    # 타이밍: 클라우드(embed·plane·rerank) + 플레인 내부(bm25·dense) 모두 보고, 검색 위치 표기
    assert {"embed_ms", "plane_ms", "bm25_ms", "dense_ms", "rerank_ms"} <= set(timing)
    assert timing["searchPlane"] == "onprem"
    # 프롬프트 형태는 로컬 경로와 동일 (S1 핸들러 계약)
    assert system == vectorrag.SYSTEM
    assert user.startswith(f"질문: {query}") and f"[{HERO_CHUNK}]" in user


def test_plane_failure_is_raised_not_hidden(plane_calls, monkeypatch):
    """플레인 호출 실패 시 로컬 인덱스로 조용히 대체하지 않는다 (§3.1·§12.4)."""
    from engine import bedrock, vectorrag
    monkeypatch.setattr(bedrock, "embed", lambda texts: [[0.1, 0.2] for _ in texts])  # 차원 불일치 → 플레인 400
    monkeypatch.setattr(bedrock, "rerank", lambda *a, **k: pytest.fail("실패 경로에서 리랭크가 호출됐다"))
    monkeypatch.setattr(vectorrag.HybridIndex, "load",
                        classmethod(lambda cls: pytest.fail("실패 시 로컬 인덱스로 대체했다")))
    with pytest.raises(Exception) as ei:
        vectorrag.prepare("아무 질문")
    assert type(ei.value).__name__ == "PlaneUnavailable"
    assert plane_calls and plane_calls[0][0] == "/vector/search"


def test_local_mode_when_common_plane_is_absent(monkeypatch):
    """cli.py 처럼 api 경로 밖에서 import 되면 common.plane 이 없다 → 로컬 경로."""
    monkeypatch.delitem(sys.modules, "common", raising=False)
    monkeypatch.delitem(sys.modules, "common.plane", raising=False)
    monkeypatch.syspath_prepend(str(ROOT))  # api/ 는 sys.path 에 없다
    from engine import vectorrag
    assert vectorrag.search_mode() == "local"
    assert "로컬" in vectorrag.search_label()


def test_local_mode_when_plane_not_connected(monkeypatch):
    plane = types.ModuleType("common.plane")
    plane.mode = lambda: "none"
    common = types.ModuleType("common")
    common.plane = plane
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setitem(sys.modules, "common.plane", plane)
    from engine import vectorrag
    assert vectorrag.search_mode() == "local"
