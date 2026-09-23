"""관리자 Lambda — IAM invoke 전용 (WebSocket 사용자 경로에서 도달 불가).

  {"op": "load_neptune"}   시드 데이터를 Neptune에 적재 (wipe 후 재적재, 멱등)
  {"op": "seed_registry"}  Registry 기준선 시드 (멱등)
  {"op": "reset_demo"}     시연 상태 리셋 (Registry 기준선 복원)
  {"op": "health"}         브리지·VPC 내부 서비스·Neptune 점검
호출: aws lambda invoke --function-name <AdminFn> --payload '{"op":"health"}' out.json
"""
from __future__ import annotations

import os

from common import plane
from common.log import log_event


def handler(event, context):
    op = event.get("op")
    log_event("admin.op", getattr(context, "aws_request_id", ""), op=op)
    if op == "health":
        out = {"plane": plane.mode(), "graphBackend": os.environ.get("GRAPH_BACKEND", "local")}
        if plane.mode() == "bridge":
            out["bridge"] = plane.bridge("health")
        return out
    if op == "load_neptune":
        from graph.store import LocalGraphStore, NeptuneGraphStore
        nep = NeptuneGraphStore()
        local = LocalGraphStore.from_seed_dir(os.path.join(os.path.dirname(__file__), "seed", "out"))
        nep.wipe()
        nep.upsert_nodes([local._nodes[i] for i in local._nodes])
        nep.upsert_edges([e for rels in local._out.values() for lst in rels.values() for e in lst],
                         labels={nid: n.label for nid, n in local._nodes.items()})
        n, e = nep.count()
        return {"nodes": n, "edges": e}
    if op == "seed_registry":
        from registry.seed import seed
        return seed(actor="admin", reset=bool(event.get("reset")))
    if op == "seed_mcp_servers":
        from registry.seed import seed_mcp_servers
        return seed_mcp_servers(actor="admin")
    if op == "reset_demo":
        from registry.seed import reset_demo_state
        return reset_demo_state(actor="admin")
    if op in {"inspect_registry_record", "transition_registry_record"}:
        from registry import administration
        try:
            if op == "inspect_registry_record":
                return {"ok": True, **administration.inspect(event["name"], event["version"])}
            return {"ok": True, **administration.transition(
                event["name"], event["version"], event["to"], event["expectedHash"],
                str(event.get("reason", ""))[:500], actor_ref=administration.invocation_actor(context))}
        except Exception as error:
            log_event("admin.registry_decision_failed", errorType=type(error).__name__)
            return {"ok": False, "error": "registry-administration-failed",
                    "errorType": type(error).__name__, "code": getattr(error, "code", 500)}
    if op == "seed_agents":
        from agentcore.agent_specs import SCENARIO_AGENTS
        from agentcore.seeding import seed_agents
        from registry.administration import invocation_actor
        return seed_agents(SCENARIO_AGENTS, os.environ.get("AGENTS_RUNTIME_ARN", ""), invocation_actor(context))
    if op == "inspect_agent_request":
        from agentcore import harness
        from agentcore.administration import harness_fingerprint
        from registry import api
        request = api.get_record(event["name"], event.get("version", "v1"), include_internal=True)
        if not request or request.get("subtype") != "AGENT_ADMIN_REQUEST":
            return {"ok": False, "error": "agent-request-not-found"}
        existing = harness.find_harness("bank_" + request["payload"]["name"])
        return {"ok": True, "request": request, "harnessId": (existing or {}).get("harnessId"),
                "expectedHarnessHash": harness_fingerprint(existing) if existing else None}
    if op in {"apply_agent_request", "reconcile_agent_request"}:
        from agentcore.administration import apply_request
        from registry.model import RegistryError
        try:
            from registry.administration import invocation_actor
            options = {"reconcile_hash": event["expectedHarnessHash"]} if op == "reconcile_agent_request" else {}
            return {"ok": True, **apply_request(event["name"], event.get("version", "v1"),
                                              actor_ref=invocation_actor(context), **options)}
        except Exception as error:
            log_event("admin.agent_request_failed", errorType=type(error).__name__)
            return {"ok": False, "error": str(error)[:300] if isinstance(error, RegistryError) else "agent-administration-failed",
                    "errorType": type(error).__name__}
    return {"error": f"unknown op: {op}"}
