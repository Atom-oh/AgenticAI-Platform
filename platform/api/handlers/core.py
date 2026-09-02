"""플랫폼 공통 액션 — 허브 집계 · 컨트롤룸/스튜디오 프록시 · 온톨로지 탐색 · 트레이스 · 리셋."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request

import boto3

from common import plane, tracing
from common.ctx import Ctx
from common.log import log_event

CONTROL_ROOM = os.environ.get("CONTROL_ROOM_URL", "https://d1twhttjtzqewp.cloudfront.net")
STUDIO = os.environ.get("STUDIO_URL", "https://d4zwmnh2s47e9.cloudfront.net")
AGENTCORE_REGISTRY_ID = os.environ.get("AGENTCORE_REGISTRY_ID", "b2hOSZL4eOhDXAyk")
GRAPH_BACKEND = os.environ.get("GRAPH_BACKEND", "local")

_store = None
_index = None


def lazy_store():
    global _store
    if _store is None:
        from graph.store import get_store
        _store = get_store()
    return _store


def lazy_index():
    global _index
    if _index is None:
        from engine.vectorrag import HybridIndex
        _index = HybridIndex.load()
    return _index


# ---------- 외부 서피스 프록시 ----------
def _control_room(method: str, path: str, id_token: str, body: dict | None = None) -> dict:
    """컨트롤룸 API — 사용자의 JWT를 그대로 전달해 RBAC·예산·감사를 컨트롤룸이 시행한다."""
    req = urllib.request.Request(CONTROL_ROOM + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + id_token}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"{e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _studio(method: str, path: str, token: str = "", body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if data is not None:  # CloudFront OAC는 본문 있는 요청에 payload hash를 요구한다
        headers["x-amz-content-sha256"] = hashlib.sha256(data).hexdigest()
    if token:
        headers["x-hana-auth"] = token
    req = urllib.request.Request(STUDIO + path, data=data, headers=headers, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read().decode()).get("error", str(e.code))}
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _agentcore_records() -> list[dict]:
    try:
        return boto3.client("bedrock-agentcore-control", region_name="us-east-1") \
            .list_registry_records(registryId=AGENTCORE_REGISTRY_ID)["registryRecords"]
    except Exception as e:
        log_event("agentcore.list_failed", error=str(e)[:200])
        return []


def _registry_counts() -> dict:
    try:
        from registry.api import counts
        return counts()
    except Exception as e:
        log_event("registry.counts_failed", error=str(e)[:200])
        return {"total": 0, "approved": 0}


# ---------- 액션 ----------
def hub(ctx: Ctx, body: dict) -> None:
    agents = _control_room("GET", "/api/agents", body.get("idToken", "")).get("agents", [])
    assets = _studio("GET", "/api/assets").get("assets", [])
    surfaces = _agentcore_records()
    st = lazy_store().stats()
    reg = _registry_counts()
    ctx.post({"type": "hub",
              "agents": len(agents), "agentsApproved": sum(1 for a in agents if a.get("status") == "APPROVED"),
              "assets": len(assets),
              "surfaces": len(surfaces), "surfacesApproved": sum(1 for r in surfaces if r.get("status") == "APPROVED"),
              "registry": reg.get("total", 0), "registryApproved": reg.get("approved", 0),
              "registryByType": reg.get("byType", {}),
              "graphNodes": st["nodes"], "graphEdges": st["edges"],
              "backend": GRAPH_BACKEND, "plane": plane.mode(), "planeLabel": plane.label()})


def agents(ctx: Ctx, body: dict) -> None:
    r = _control_room("GET", "/api/agents", body.get("idToken", ""))
    ags = [{k: a.get(k) for k in ("id", "name", "description", "status", "riskTier", "team")}
           for a in r.get("agents", [])][:40]
    ctx.post({"type": "agents", "agents": ags, "error": r.get("error")})


def chat(ctx: Ctx, body: dict) -> None:
    r = _control_room("POST", "/api/chat", body.get("idToken", ""),
                      {"agentId": body.get("agentId"), "message": body.get("message", "")[:2000],
                       "sessionId": body.get("sessionId")})
    ctx.post({"type": "chat", "reply": r.get("reply"), "sessionId": r.get("sessionId"), "error": r.get("error")})


def assets(ctx: Ctx, body: dict) -> None:
    r = _studio("GET", "/api/assets")
    ctx.post({"type": "assets", "error": r.get("error"), "assets": [
        {k: a.get(k) for k in ("name", "type", "version", "actor", "updated_at", "scope")}
        for a in r.get("assets", [])][:60]})


def surfaces(ctx: Ctx, body: dict) -> None:
    """AgentCore Agent Registry(us-east-1)에 등록된 플랫폼 서피스 레코드 — 읽기 전용."""
    recs = _agentcore_records()
    ctx.post({"type": "surfaces", "records": [
        {"name": r["name"], "type": r.get("descriptorType"), "status": r.get("status"),
         "description": (r.get("description") or "")[:140], "updatedAt": str(r.get("updatedAt", ""))[:19]}
        for r in recs][:60]})


def studio_drafts(ctx: Ctx, body: dict) -> None:
    r = _studio("GET", "/drafts.json")
    ctx.post({"type": "studio_drafts", "drafts": list(reversed(r.get("drafts", [])))[:30], "error": r.get("error")})


def studio_asset(ctx: Ctx, body: dict) -> None:
    aid = urllib.parse.quote(body.get("assetId", ""))
    content = _studio("GET", f"/api/assets/content?asset_id={aid}")
    history = _studio("GET", f"/api/assets/history?asset_id={aid}")
    ctx.post({"type": "studio_asset", "content": content, "history": history.get("history", history)})


def studio_models(ctx: Ctx, body: dict) -> None:
    r = _studio("GET", "/api/models")
    ctx.post({"type": "studio_models", "models": r.get("models", [])[:30], "error": r.get("error")})


def studio_jobs(ctx: Ctx, body: dict) -> None:
    jid = body.get("jobId")
    r = _studio("GET", f"/api/jobs?job_id={jid}" if jid else "/api/jobs")
    ctx.post({"type": "studio_jobs", **{k: r.get(k) for k in ("job", "jobs", "error")}})


def studio_generate(ctx: Ctx, body: dict) -> None:
    r = _studio("POST", "/api/generate", body.get("studioToken", ""),
                {"brief": body.get("brief", "")[:1000], "model_id": body.get("modelId", ""),
                 "asset_ids": body.get("assetIds", []), "output_type": body.get("outputType", "design")})
    ctx.post({"type": "studio_generate", **r})


def studio_feedback(ctx: Ctx, body: dict) -> None:
    r = _studio("POST", "/api/feedback", body.get("studioToken", ""),
                {"draft_id": body.get("draftId"), "action": body.get("decision"), "comment": body.get("comment", "")})
    ctx.post({"type": "studio_feedback", **r})


def studio_register(ctx: Ctx, body: dict) -> None:
    r = _studio("POST", "/api/assets", body.get("studioToken", ""),
                {"name": body.get("name", ""), "type": body.get("assetType", ""),
                 "content": body.get("content", ""), "scope": body.get("scope", "shared")})
    ctx.post({"type": "studio_register", **r})


def traces(ctx: Ctx, body: dict) -> None:
    items = tracing.list_traces(limit=int(body.get("limit", 60)))
    ctx.post({"type": "traces", **tracing.summarize(items), "items": items,
              "plane": plane.mode(), "planeLabel": plane.label(), "backend": GRAPH_BACKEND})


def explore(ctx: Ctx, body: dict) -> None:
    store = lazy_store()
    node_id = str(body.get("nodeId") or "REG-LN-001")[:64]
    n = store.get_node(node_id)
    if not n:
        hits = store.find_by_label("Regulation")[:1]
        n = hits[0] if hits else None
    if not n:
        ctx.post({"type": "explore", "error": "노드 없음"})
        return
    nodes = {n.id: {"id": n.id, "label": n.label, "name": n.props.get("name") or n.props.get("title") or n.id}}
    edges = []
    for direction in ("out", "in"):
        for e, other in store.neighbors(n.id, direction=direction)[:40]:
            nodes[other.id] = {"id": other.id, "label": other.label,
                               "name": other.props.get("name") or other.props.get("title") or other.id}
            edges.append({"src": e.src, "rel": e.rel, "dst": e.dst})
    ctx.post({"type": "explore", "center": n.id, "props": n.props,
              "graph": {"nodes": list(nodes.values()), "edges": edges[:80]}})


def reset(ctx: Ctx, body: dict) -> None:
    """시연 리셋 (SPEC §6.3): Registry 시연 상태를 기준선으로 되돌린다. 대화 이력은 클라이언트가 비운다.
    인프라·데이터를 파괴하지 않는다 (Neptune 재적재 등 관리 작업은 admin_handler 전용)."""
    out = {"registry": None}
    try:
        from registry.seed import reset_demo_state
        out["registry"] = reset_demo_state(actor=ctx.email)
    except Exception as e:
        out["registry"] = {"error": str(e)[:200]}
    log_event("demo.reset", ctx.trace_id, actor=ctx.email)
    ctx.post({"type": "reset", **out})


ROUTES = {
    "hub": hub, "agents": agents, "chat": chat, "assets": assets, "surfaces": surfaces,
    "studio_drafts": studio_drafts, "studio_asset": studio_asset, "studio_models": studio_models,
    "studio_jobs": studio_jobs, "studio_generate": studio_generate, "studio_feedback": studio_feedback,
    "studio_register": studio_register, "traces": traces, "explore": explore, "reset": reset,
}
