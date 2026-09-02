"""S1 WebSocket 백엔드 — 인증 연결 + 양 엔진 병렬 스트리밍.

라우트:
  $connect    : ?token=<Cognito access token> → cognito-idp GetUser로 검증.
                토큰이 없거나 무효면 연결 거부(401/403) — 미인증 공개 노출 없음.
  $disconnect : 연결 레코드 삭제
  $default    : {"action":"s1","query":"..."} → vector/graph 두 엔진을 병렬 실행,
                단계 이벤트와 생성 토큰을 실시간 push (SPEC §10 스트리밍).

이벤트 타입:
  meta / vector.chunks / vector.token / vector.done
  graph.meta / graph.token / graph.done / error
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import boto3

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
CONN_TABLE = os.environ["CONN_TABLE"]
GRAPH_BACKEND = os.environ.get("GRAPH_BACKEND", "local")

_ddb = boto3.resource("dynamodb", region_name=REGION).Table(CONN_TABLE)
_idp = boto3.client("cognito-idp", region_name=REGION)

# 콜드스타트에 1회 로드 (엔진·인덱스·그래프)
_store = None
_index = None


def _lazy_load():
    global _store, _index
    if _store is None:
        from graph.store import get_store
        _store = get_store()
    if _index is None:
        from engine.vectorrag import HybridIndex
        _index = HybridIndex.load()
    return _store, _index


def _post(apigw, conn_id: str, payload: dict) -> None:
    apigw.post_to_connection(ConnectionId=conn_id,
                             Data=json.dumps(payload, ensure_ascii=False).encode())


def _run_vector(apigw, conn_id: str, query: str, index) -> None:
    from engine import bedrock, vectorrag
    hits, timing, system, user = vectorrag.prepare(query, index)
    _post(apigw, conn_id, {"type": "vector.chunks", "timing": timing,
                           "chunks": [{"id": h.chunk["chunkId"],
                                       "score": round(h.score, 4),
                                       "text": h.chunk["text"][:220]} for h in hits]})
    t0 = time.time()
    for token in bedrock.generate_stream(system, user):
        _post(apigw, conn_id, {"type": "vector.token", "t": token})
    _post(apigw, conn_id, {"type": "vector.done",
                           "generate_ms": int((time.time() - t0) * 1000)})


def _run_graph(apigw, conn_id: str, query: str, store) -> None:
    from engine import bedrock, graphrag
    meta = graphrag.prepare(query, store)
    if "error" in meta:
        _post(apigw, conn_id, {"type": "graph.done", "error": meta["error"],
                               "seedCandidates": meta.get("seedCandidates", [])})
        return
    _post(apigw, conn_id, {"type": "graph.meta",
                           "seed": meta["seed"], "seedConfidence": meta["seedConfidence"],
                           "seedCandidates": meta["seedCandidates"],
                           "counts": meta["counts"], "graph": meta["graph"],
                           "timing": meta["timing"]})
    t0 = time.time()
    full = []
    for token in bedrock.generate_stream(meta["system"], meta["user"], max_tokens=2000):
        full.append(token)
        _post(apigw, conn_id, {"type": "graph.token", "t": token})
    # 근거 검증 (F1 ⑥)
    import re as _re
    valid = {n["id"] for n in meta["graph"]["nodes"]}
    cited = set(_re.findall(r"\b(?:REG|PRD|SCR|CMP|CND|DOC|TPL|D)-[A-Za-z0-9-]+\b", "".join(full)))
    _post(apigw, conn_id, {"type": "graph.done",
                           "generate_ms": int((time.time() - t0) * 1000),
                           "evidenceNodeIds": sorted(valid & cited)[:50],
                           "hallucinatedIds": sorted(cited - valid)})


def handler(event, context):
    rc = event.get("requestContext", {})
    route = rc.get("routeKey")
    conn_id = rc.get("connectionId")

    if route == "$connect":
        token = (event.get("queryStringParameters") or {}).get("token", "")
        if not token:
            return {"statusCode": 401}
        try:
            u = _idp.get_user(AccessToken=token)
            email = next((a["Value"] for a in u["UserAttributes"] if a["Name"] == "email"), u["Username"])
        except Exception:
            return {"statusCode": 403}
        _ddb.put_item(Item={"connId": conn_id, "email": email, "ts": int(time.time())})
        return {"statusCode": 200}

    if route == "$disconnect":
        _ddb.delete_item(Key={"connId": conn_id})
        return {"statusCode": 200}

    # $default — 인증된 연결만 도달 가능 ($connect에서 검증됨)
    rec = _ddb.get_item(Key={"connId": conn_id}).get("Item")
    if not rec:
        return {"statusCode": 403}
    endpoint = f"https://{rc['domainName']}/{rc['stage']}"
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        body = {}
    if body.get("action") != "s1" or not body.get("query", "").strip():
        _post(apigw, conn_id, {"type": "error", "message": "지원하지 않는 요청입니다."})
        return {"statusCode": 400}

    query = body["query"][:500]
    store, index = _lazy_load()
    _post(apigw, conn_id, {"type": "meta", "backend": GRAPH_BACKEND,
                           "user": rec["email"], "query": query})
    with ThreadPoolExecutor(max_workers=2) as ex:
        fv = ex.submit(_run_vector, apigw, conn_id, query, index)
        fg = ex.submit(_run_graph, apigw, conn_id, query, store)
        for f in (fv, fg):
            try:
                f.result()
            except Exception as e:  # 한쪽 실패가 다른 쪽을 막지 않게
                try:
                    _post(apigw, conn_id, {"type": "error", "message": str(e)[:300]})
                except Exception:
                    pass
    return {"statusCode": 200}
