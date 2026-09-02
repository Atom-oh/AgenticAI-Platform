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
        nep.upsert_edges([e for rels in local._out.values() for lst in rels.values() for e in lst],
                         labels={nid: n.label for nid, n in local._nodes.items()})
        n, e = nep.count()
        return {"nodes": n, "edges": e}
    if op == "seed_registry":
        from registry.seed import seed
        return seed(actor="admin", reset=bool(event.get("reset")))
    if op == "reset_demo":
        from registry.seed import reset_demo_state
        return reset_demo_state(actor="admin")
    if op == "seed_agents":
        # 시나리오 에이전트 4종: 실행 = AgentCore Runtime(Strands 컨테이너, AGENTS_RUNTIME_ARN) 우선, 없으면 설정형 Harness.
        # 플랫폼 Registry + AgentCore Registry(미러)에 등록·승인한다 (멱등).
        from agentcore import registry_mirror
        from agentcore.agent_specs import SCENARIO_AGENTS
        runtime_arn = os.environ.get("AGENTS_RUNTIME_ARN", "")
        out = []
        for spec in SCENARIO_AGENTS:
            row = {"name": spec["name"]}
            payload = {"model": spec["model"], "allowedTools": spec["allowedTools"], "skills": spec["skills"],
                       "memory": bool(spec.get("memory")), "title": spec.get("title"), "scenario": spec.get("scenario")}
            if runtime_arn:
                payload.update({"runtime": "agentcore-runtime/strands", "runtimeArn": runtime_arn, "sdk": "Strands Agents"})
                row["runtime"] = "agentcore-runtime/strands"
            else:
                try:
                    from agentcore import harness
                    h = harness.ensure_harness(spec)
                    payload.update({"runtime": "AgentCore Harness", "harnessArn": h.get("arn"), "harnessId": h.get("harnessId")})
                    row["harnessArn"] = h.get("arn"); row["status"] = h.get("status")
                except Exception as e:
                    row["harnessError"] = f"{type(e).__name__}: {str(e)[:300]}"
            try:
                from registry.api import create_record, get_record, transition
                rec = get_record(spec["name"], "v1")
                if not rec:
                    rec = create_record({"name": spec["name"], "recordVersion": "v1", "recordType": "AGENT",
                                         "description": spec["description"], "owner": "AI플랫폼팀",
                                         "tags": [spec.get("scenario", "custom"), payload.get("runtime", "")],
                                         "payload": payload}, actor="admin")
                    transition(spec["name"], "v1", "PENDING_APPROVAL", "admin", "시드 — 시나리오 에이전트")
                    rec, _ev = transition(spec["name"], "v1", "APPROVED", "admin", "시드 기준선 승인")
                elif rec.get("payload", {}).get("runtime") != payload.get("runtime"):
                    # 런타임 정보가 바뀐 경우(Harness → Strands 런타임): 새 버전 레코드로 남긴다 (감사 무결성 — 제자리 수정 없음)
                    ver = "v2" if rec.get("recordVersion") == "v1" else "v" + str(int(str(rec.get("recordVersion", "v1"))[1:]) + 1)
                    if not get_record(spec["name"], ver):
                        create_record({"name": spec["name"], "recordVersion": ver, "recordType": "AGENT",
                                       "description": spec["description"], "owner": "AI플랫폼팀",
                                       "tags": [spec.get("scenario", "custom"), payload.get("runtime", "")],
                                       "payload": {**payload, "supersedes": rec.get("recordVersion")}}, actor="admin")
                        transition(spec["name"], ver, "PENDING_APPROVAL", "admin", "런타임 전환 — 승인 대기")
                        rec, _ev = transition(spec["name"], ver, "APPROVED", "admin", "런타임 전환 승인 (시드)")
                row["registry"] = rec.get("status") if isinstance(rec, dict) else str(rec)
                row["agentcoreRegistry"] = registry_mirror.mirror(rec)
            except Exception as e:
                row["registryError"] = f"{type(e).__name__}: {str(e)[:300]}"
            out.append(row)
        return {"agents": out, "runtimeArn": runtime_arn or None}
    return {"error": f"unknown op: {op}"}
