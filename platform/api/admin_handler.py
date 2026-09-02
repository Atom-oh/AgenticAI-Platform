"""관리자 Lambda — IAM invoke 전용 (WebSocket 사용자 경로에서 도달 불가).

  {"op": "load_neptune"}   시드 데이터를 Neptune에 적재 (wipe 후 재적재, 멱등)
  {"op": "seed_registry"}  Registry 기준선 시드 (멱등)
  {"op": "reset_demo"}     시연 상태 리셋 (Registry 기준선 복원)
  {"op": "health"}         브리지·온프렘·Neptune 점검
호출: aws lambda invoke --function-name <AdminFn> --payload '{"op":"health"}' out.json
"""
from __future__ import annotations

import os

from common import plane
from common.log import log_event


def handler(event, context):
    op = event.get("op")
    log_event("admin.op", op=op)
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
        nep.upsert_edges([e for rels in local._out.values() for lst in rels.values() for e in lst])
        n, e = nep.count()
        return {"nodes": n, "edges": e}
    if op == "seed_registry":
        from registry.seed import seed
        return seed(actor="admin", reset=bool(event.get("reset")))
    if op == "reset_demo":
        from registry.seed import reset_demo_state
        return reset_demo_state(actor="admin")
    return {"error": f"unknown op: {op}"}
