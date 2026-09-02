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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import boto3

CONTROL_ROOM = "https://d1twhttjtzqewp.cloudfront.net"
STUDIO = "https://d4zwmnh2s47e9.cloudfront.net"
REGISTRY_ID = "b2hOSZL4eOhDXAyk"
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "iol2t2rp0q9i")
GUARDRAIL_VER = os.environ.get("GUARDRAIL_VERSION", "2")

_PII_PATTERNS = [r"\bCUST-\d{4}\b", r"\bACCT-\d{4}\b",
                 r"\b01[016789]-?\d{3,4}-?\d{4}\b", r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"]


def _pii_outbound_count(text: str) -> int:
    """경계를 넘는 페이로드에서 개인 식별자 실측 스캔 (F6 — 하드코딩 금지)."""
    import re as _re
    return sum(len(_re.findall(p, text)) for p in _PII_PATTERNS)


def _apply_guardrail(text: str, source: str) -> dict:
    rt = boto3.client("bedrock-runtime", region_name=REGION)
    r = rt.apply_guardrail(guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VER,
                           source=source, content=[{"text": {"text": text[:4000]}}])
    topics = [t["name"] for a in r.get("assessments", [])
              for t in a.get("topicPolicy", {}).get("topics", [])]
    return {"action": r["action"], "topics": topics,
            "message": (r.get("outputs") or [{}])[0].get("text", "")}


def _studio(method: str, path: str, token: str = "", body: dict | None = None) -> dict:
    """UI/UX 스튜디오 API 프록시 — 쓰기는 사용자 본인의 스튜디오 토큰(x-hana-auth)을
    그대로 전달한다 (같은 공유 계정, 신원이 스튜디오 감사에 그대로 남는다)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["x-hana-auth"] = token
    req = urllib.request.Request(STUDIO + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body else None,
        headers=headers, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read().decode()).get("error", str(e.code))}
        except Exception:
            return {"error": f"HTTP {e.code}"}


def _save_trace(rec: dict) -> None:
    rec = {"connId": f"trace#{int(time.time() * 1000)}", "ttl": int(time.time()) + 86400 * 7, **rec}
    _ddb.put_item(Item=json.loads(json.dumps(rec), parse_float=str))


def _list_traces() -> list[dict]:
    items = [i for i in _ddb.scan(Limit=200).get("Items", [])
             if str(i.get("connId", "")).startswith("trace#")]
    items.sort(key=lambda x: x["connId"], reverse=True)
    return items[:40]


def _proxy(method: str, path: str, id_token: str, body: dict | None = None) -> dict:
    """컨트롤룸 API 프록시 — 사용자의 JWT를 그대로 전달해 RBAC·예산·감사를
    컨트롤룸 백엔드가 시행하게 한다. 플랫폼 통합이지 통제 우회가 아니다."""
    req = urllib.request.Request(CONTROL_ROOM + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + id_token}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"{e.code}: {e.read().decode()[:200]}"}

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
                             Data=json.dumps(payload, ensure_ascii=False,
                                             default=str).encode())  # DDB Decimal 등


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


def _run_s2(apigw, conn_id: str, email: str, query: str, rid: str) -> None:
    """S2 마이데이터 상담 파이프라인 (SPEC F3) — 단계별 이벤트를 push한다.

    숫자는 전부 결정론적 계산엔진이 만들고 LLM은 설명만 한다 (§12.2).
    """
    import uuid
    from engine import bedrock
    from onprem import masking
    from onprem.calc_engine import jeonse_loan_limit, preferential_rate, verify_no_generated_numbers
    from personal_data import exact_lookup

    trace_id = uuid.uuid4().hex[:12]
    t_start = time.time()

    def stage(step: str, **kw) -> None:
        _post(apigw, conn_id, {"type": "s2.stage", "reqId": rid, "step": step,
                               "traceId": trace_id, **kw})

    # ① 입력 가드레일 (실물 Bedrock Guardrails)
    g_in = _apply_guardrail(query, "INPUT")
    stage("guardrail_in", result=g_in)
    if g_in["action"] == "GUARDRAIL_INTERVENED":
        _save_trace({"traceId": trace_id, "scenario": "S2", "email": email,
                     "query": query[:120], "blocked": True, "topics": g_in["topics"],
                     "piiOutbound": 0, "tokensOut": 0, "maskedFields": []})
        _post(apigw, conn_id, {"type": "s2.done", "reqId": rid, "blocked": True,
                               "message": g_in["message"], "traceId": trace_id})
        return

    # ② 의도 → Semantic Layer 해석 (지표 정의는 이 계층만 신뢰)
    from semantic.loader import SemanticLayer
    sl = SemanticLayer()
    metric = sl.resolve(query) or sl.resolve("우대금리")
    stage("semantic", metric={"name": metric.name, "unit": metric.unit,
                              "ownerDept": metric.owner_dept,
                              "sql": metric.sql_template.strip()})

    # ③ 정확 조회 (tool call — 벡터 검색 없음)
    profile = exact_lookup(email)
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
    stage("lookup", values=raw_values, source="온프렘 정확 조회(합성) — Phase 3에서 RDS 이관")

    # ④ 결정론적 계산엔진
    rate = preferential_rate(profile, profile["product"]["baseRate"])
    limit = jeonse_loan_limit(profile["jeonse"]["depositKrw"],
                              profile["jeonse"]["guaranteeRatio"],
                              profile["jeonse"]["annualIncomeKrw"],
                              profile["jeonse"]["existingDebtKrw"])
    stage("calc", rate=rate.to_dict(), limit=limit.to_dict())

    # ⑤ 마스킹/토큰화 게이트 — 이 페이로드만 경계를 넘는다
    prompt = (f"고객 세그먼트: {profile['segment']}\n"
              f"고객 식별: {profile['customerId']} / 계좌 {profile['account']['accountId']}\n"
              f"상품: {profile['product']['name']}\n"
              f"[계산엔진 확정값 — 이 숫자만 사용할 것]\n"
              f"- 적용금리: {rate.value}% (기본 {profile['product']['baseRate']}%)\n"
              f"- 우대 판정: " + "; ".join(f"{s.label}→{s.value}" for s in rate.steps[1:-1]) + "\n"
              f"- 대출 가능 한도: {int(limit.value):,}원\n\n질문: {query}")
    m = masking.mask(prompt, {"customerName": profile["name"]})
    pii_out = _pii_outbound_count(m.text)
    stage("mask", maskedFields=m.masked_fields, maskedPayload=m.text,
          piiOutbound=pii_out)

    # ⑥ Bedrock 설명 생성 (스트리밍) — 마스킹된 컨텍스트만 수신
    system = ("당신은 아톰은행 상담 도우미입니다. 제공된 계산엔진 확정값만 사용해 우대금리 "
              "충족 여부와 가능 금액을 한국어로 친절히 설명하세요. 어떤 숫자도 새로 만들지 "
              "마세요. 확정 신청은 영업점/앱에서 진행하도록 안내하세요.")
    full = []
    for tk in bedrock.generate_stream(system, m.text, max_tokens=800):
        full.append(tk)
        _post(apigw, conn_id, {"type": "s2.token", "reqId": rid, "t": tk})
    answer = "".join(full)

    # ⑦ 출력 가드레일 + 수치 검증기 + 재식별
    g_out = _apply_guardrail(answer, "OUTPUT")
    allowed = [str(rate.value), f"{int(limit.value):,}", str(int(limit.value)),
               profile["product"]["baseRate"], f"{profile['cardMonthlyKrw']:,}",
               f"{profile['jeonse']['depositKrw']:,}", f"{profile['jeonse']['annualIncomeKrw']:,}",
               f"{profile['jeonse']['existingDebtKrw']:,}"] + \
              [s.value for s in rate.steps] + [s.value for s in limit.steps] + \
              [s.formula for s in rate.steps] + [s.formula for s in limit.steps]
    invented = verify_no_generated_numbers(answer, [a.replace("%", "").replace("원", "").replace("%p", "") for a in allowed])
    unmasked = masking.unmask(answer, m.mapping)

    usage_tokens = len(m.text) // 3  # 추정치 표기용 (정확 usage는 스트림 API 미제공)
    _save_trace({"traceId": trace_id, "scenario": "S2", "email": email,
                 "query": query[:120], "blocked": False,
                 "maskedFields": [f["field"] for f in m.masked_fields],
                 "piiOutbound": pii_out, "tokensOut": usage_tokens,
                 "guardrailOut": g_out["action"],
                 "elapsedMs": int((time.time() - t_start) * 1000)})
    _post(apigw, conn_id, {"type": "s2.done", "reqId": rid, "traceId": trace_id,
                           "unmasked": unmasked, "guardrailOut": g_out,
                           "inventedNumbers": invented,
                           "elapsedMs": int((time.time() - t_start) * 1000)})


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
    action = body.get("action")
    rid = body.get("reqId", "")

    # ---------- 통합 플랫폼 액션 (요청/응답형) ----------
    if action == "hub":
        agents = _proxy("GET", "/api/agents", body.get("idToken", "")).get("agents", [])
        try:
            assets = json.loads(urllib.request.urlopen(
                STUDIO + "/api/assets", timeout=10).read().decode()).get("assets", [])
        except Exception:
            assets = []
        try:
            recs = boto3.client("bedrock-agentcore-control", region_name="us-east-1") \
                .list_registry_records(registryId=REGISTRY_ID)["registryRecords"]
        except Exception:
            recs = []
        store, _ = _lazy_load()
        st = store.stats()
        _post(apigw, conn_id, {"type": "hub", "reqId": rid,
            "agents": len(agents),
            "agentsApproved": sum(1 for a in agents if a.get("status") == "APPROVED"),
            "assets": len(assets),
            "registry": len(recs),
            "registryApproved": sum(1 for r in recs if r.get("status") == "APPROVED"),
            "graphNodes": st["nodes"], "graphEdges": st["edges"],
            "backend": GRAPH_BACKEND})
        return {"statusCode": 200}

    if action == "agents":
        r = _proxy("GET", "/api/agents", body.get("idToken", ""))
        ags = [{k: a.get(k) for k in ("id", "name", "description", "status", "riskTier", "team")}
               for a in r.get("agents", [])][:40]
        _post(apigw, conn_id, {"type": "agents", "reqId": rid,
                               "agents": ags, "error": r.get("error")})
        return {"statusCode": 200}

    if action == "chat":
        r = _proxy("POST", "/api/chat", body.get("idToken", ""),
                   {"agentId": body.get("agentId"), "message": body.get("message", "")[:2000],
                    "sessionId": body.get("sessionId")})
        _post(apigw, conn_id, {"type": "chat", "reqId": rid,
                               "reply": r.get("reply"), "sessionId": r.get("sessionId"),
                               "error": r.get("error")})
        return {"statusCode": 200}

    if action == "assets":
        try:
            assets = json.loads(urllib.request.urlopen(
                STUDIO + "/api/assets", timeout=10).read().decode()).get("assets", [])
        except Exception as e:
            _post(apigw, conn_id, {"type": "assets", "reqId": rid, "assets": [],
                                   "error": str(e)[:200]})
            return {"statusCode": 200}
        _post(apigw, conn_id, {"type": "assets", "reqId": rid, "assets": [
            {k: a.get(k) for k in ("name", "type", "version", "actor", "updated_at", "scope")}
            for a in assets][:60]})
        return {"statusCode": 200}

    if action == "registry":
        try:
            recs = boto3.client("bedrock-agentcore-control", region_name="us-east-1") \
                .list_registry_records(registryId=REGISTRY_ID)["registryRecords"]
            out = [{"name": r["name"], "type": r.get("descriptorType"),
                    "status": r.get("status"), "description": (r.get("description") or "")[:140],
                    "updatedAt": str(r.get("updatedAt", ""))[:19]} for r in recs][:60]
            _post(apigw, conn_id, {"type": "registry", "reqId": rid, "records": out})
        except Exception as e:
            _post(apigw, conn_id, {"type": "registry", "reqId": rid, "records": [],
                                   "error": str(e)[:200]})
        return {"statusCode": 200}

    # ---------- 디자인 스튜디오 네이티브 통합 (embed 아님 — 소스 기반 프록시) ----------
    if action == "studio_drafts":
        r = _studio("GET", "/drafts.json")
        drafts = list(reversed(r.get("drafts", [])))[:30]
        _post(apigw, conn_id, {"type": "studio_drafts", "reqId": rid, "drafts": drafts,
                               "error": r.get("error")})
        return {"statusCode": 200}

    if action == "studio_asset":
        aid = body.get("assetId", "")
        content = _studio("GET", f"/api/assets/content?asset_id={urllib.parse.quote(aid)}")
        history = _studio("GET", f"/api/assets/history?asset_id={urllib.parse.quote(aid)}")
        _post(apigw, conn_id, {"type": "studio_asset", "reqId": rid,
                               "content": content, "history": history.get("history", history)})
        return {"statusCode": 200}

    if action == "studio_models":
        r = _studio("GET", "/api/models")
        _post(apigw, conn_id, {"type": "studio_models", "reqId": rid,
                               "models": r.get("models", [])[:30], "error": r.get("error")})
        return {"statusCode": 200}

    if action == "studio_jobs":
        jid = body.get("jobId")
        r = _studio("GET", f"/api/jobs?job_id={jid}" if jid else "/api/jobs")
        _post(apigw, conn_id, {"type": "studio_jobs", "reqId": rid, **{k: r.get(k) for k in ("job", "jobs", "error")}})
        return {"statusCode": 200}

    if action == "studio_generate":
        r = _studio("POST", "/api/generate", body.get("studioToken", ""),
                    {"brief": body.get("brief", "")[:1000], "model_id": body.get("modelId", ""),
                     "asset_ids": body.get("assetIds", []), "output_type": body.get("outputType", "design")})
        _post(apigw, conn_id, {"type": "studio_generate", "reqId": rid, **r})
        return {"statusCode": 200}

    if action == "studio_feedback":
        r = _studio("POST", "/api/feedback", body.get("studioToken", ""),
                    {"draft_id": body.get("draftId"), "action": body.get("decision"),
                     "comment": body.get("comment", "")})
        _post(apigw, conn_id, {"type": "studio_feedback", "reqId": rid, **r})
        return {"statusCode": 200}

    if action == "studio_register":
        r = _studio("POST", "/api/assets", body.get("studioToken", ""),
                    {"name": body.get("name", ""), "type": body.get("assetType", ""),
                     "content": body.get("content", ""), "scope": body.get("scope", "shared")})
        _post(apigw, conn_id, {"type": "studio_register", "reqId": rid, **r})
        return {"statusCode": 200}

    if action == "traces":
        items = _list_traces()
        total_pii = sum(int(i.get("piiOutbound", 0)) for i in items)
        _post(apigw, conn_id, {"type": "traces", "reqId": rid, "piiOutboundTotal": total_pii,
                               "items": items})
        return {"statusCode": 200}

    if action == "explore":
        store, _ = _lazy_load()
        node_id = body.get("nodeId") or "REG-LN-001"
        n = store.get_node(node_id)
        if not n:
            hits = store.find_by_label("Regulation")[:1]
            n = hits[0] if hits else None
        if not n:
            _post(apigw, conn_id, {"type": "explore", "reqId": rid, "error": "노드 없음"})
            return {"statusCode": 200}
        nodes = {n.id: {"id": n.id, "label": n.label,
                        "name": n.props.get("name") or n.props.get("title") or n.id}}
        edges = []
        for direction in ("out", "in"):
            for e, other in store.neighbors(n.id, direction=direction)[:40]:
                nodes[other.id] = {"id": other.id, "label": other.label,
                                   "name": other.props.get("name") or other.props.get("title") or other.id}
                edges.append({"src": e.src, "rel": e.rel, "dst": e.dst})
        _post(apigw, conn_id, {"type": "explore", "reqId": rid, "center": n.id,
                               "props": n.props, "graph": {"nodes": list(nodes.values()),
                                                           "edges": edges[:80]}})
        return {"statusCode": 200}

    if action == "s2":
        _run_s2(apigw, conn_id, rec["email"], body.get("query", "")[:500], rid)
        return {"statusCode": 200}

    if action != "s1" or not body.get("query", "").strip():
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
