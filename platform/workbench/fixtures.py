"""Fictional per-project authoring examples; never resets the shared Registry."""
from __future__ import annotations

from workbench import knowledge
from workbench.service import _hash, fields

DESCRIPTION = "가상 상품·출금 흐름·가이드·컴포넌트·아이콘의 프로젝트 전용 예시입니다."
DOCUMENTS = [
    {"id": "fictional-withdrawal", "title": "가상 상품 출금 안내", "revision": "synthetic-v1", "kind": "guide",
     "content": "가상 예시: 출금 요청에서는 출금 금액과 확인 안내를 표시합니다. 조건 변경 시 화면과 API 검토가 필요합니다.",
     "entities": [
         {"id": "example-product", "label": "Product", "title": "가상 모아저축", "version": "1"},
         {"id": "example-flow", "label": "Flow", "title": "출금 확인 흐름", "version": "1"},
         {"id": "example-guide", "label": "Guideline", "title": "출금 확인 가이드", "version": "1"},
         {"id": "example-screen", "label": "Screen", "title": "출금 확인 화면", "version": "1"},
         {"id": "example-component", "label": "Component", "title": "가상 확인 컴포넌트", "version": "1"},
         {"id": "example-icon", "label": "Icon", "title": "가상 안내 아이콘", "version": "1"},
         {"id": "example-api", "label": "API", "title": "출금 신청 API 예시", "version": "1"}],
     "relations": [
         {"src": "example-flow", "rel": "DEPENDS_ON", "dst": "example-product"},
         {"src": "example-guide", "rel": "DEPENDS_ON", "dst": "example-flow"},
         {"src": "example-screen", "rel": "IMPLEMENTS", "dst": "example-guide"},
         {"src": "example-screen", "rel": "IMPLEMENTS", "dst": "example-flow"},
         {"src": "example-screen", "rel": "USES", "dst": "example-component"},
         {"src": "example-component", "rel": "USES", "dst": "example-icon"},
         {"src": "example-api", "rel": "DEPENDS_ON", "dst": "example-flow"}]},
    {"id": "fictional-review", "title": "가상 검토 기준", "revision": "synthetic-v1", "kind": "rule",
     "content": "실제 고객·상품 정보가 아닌 합성 예시입니다. 변경 근거와 담당자 검토를 기록하고 누락된 연결은 미확인으로 표시합니다."},
]


def create_example(ctx, body):
    fields(body, {"requestId"})
    ctx.fresh({"owner", "planner"})
    identifier = ctx.identity("wb_artifact", body.get("requestId"))
    previous = ctx.existing("wb_artifact", identifier, body)
    if previous:
        return previous["result"]
    request = "example-" + _hash([ctx.actor, body["requestId"]])[:32]
    source = knowledge.register_source(ctx, {"requestId": request, "name": "가상 출금 업무 자료",
                                            "kind": "snapshot", "description": DESCRIPTION})
    if source.get("graphProvenance") != "synthetic-fixture":
        ctx.fresh({"owner", "planner"})
        source = ctx.commit([ctx.write("wb_source", {**source, "graphProvenance": "synthetic-fixture"},
                                       source["version"])])[0]
    product_result = ctx.collaboration.handle("POST", ["products"], {
        "requestId": request, "title": "가상 모아저축", "description": DESCRIPTION,
        "conditions": [{"id": "example-limit", "text": "가상 출금 조건과 안내 문구를 확인합니다."}],
        "steps": [{"id": "example-confirm", "title": "출금 확인", "description": "가상 출금 금액을 확인합니다."}],
        "notices": [{"id": "example-notice", "title": "합성 예시", "content": DESCRIPTION, "required": True}],
    }, {}, ctx.actor, ctx.project_id, authorization_expires_at=ctx.scope.get("authorizationExpiresAt"))
    batch = knowledge.start_batch(ctx, source["id"], {"requestId": request, "documents": DOCUMENTS}, dispatch=False)
    from workbench.worker import process
    # The inline example keeps a durable job record: legacy code never completes an artifact
    # whose linked job is unknown (platform-execution/1 linked-job fence).
    job_id = batch["batch"]["jobId"]
    job = ctx.host._new_job(ctx.owner, job_id, "workbench", batch["batch"]["jobInput"], batch["batch"]["requestHash"])
    job = ctx.storage.claim_job(ctx.owner, job_id) or job
    try:
        process(ctx.host, ctx.owner, job)
    except Exception:
        _settle_job(ctx, job_id, "failed")
        raise
    _settle_job(ctx, job_id, "completed")
    ctx.fresh({"owner", "planner"})
    result = {"productId": product_result[1]["product"]["id"], "sourceId": source["id"], "description": DESCRIPTION}
    ctx.commit([ctx.write("wb_artifact", {"id": identifier, "projectId": ctx.project_id, "createdBy": ctx.actor,
        "requestHash": _hash(body), "type": "synthetic-example", "status": "ready", "result": result})])
    return result


def _settle_job(ctx, job_id, status):
    current = ctx.storage.get(ctx.owner, "job", job_id)
    if current and current.get("status") in {"queued", "running"}:
        ctx.storage.put(ctx.owner, "job", {**current, "status": status,
                                           **({"progress": 100} if status == "completed" else {})},
                        current["version"])
