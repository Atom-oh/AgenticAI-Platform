"""Resolve authoritative code and persisted project ontology before generation."""
from __future__ import annotations

import json

from graph.store import Edge, LocalGraphStore, Node
from workspace.collaboration import Collaboration, CollaborationError
from workspace.component_catalog import read_catalog
from workspace.rules import CRITERIA_HASHES, CRITERIA_IDS


def criteria_fields(value):
    return {key: value[key] for key in (*CRITERIA_IDS, *CRITERIA_HASHES) if key in value}


def notice_coverage_issues(contract, pages):
    """Required guide copy must be observed on its reachable page, not merely quoted."""
    issues = []
    for page in pages:
        if not page.get("required"):
            continue
        values = [" ".join(step.get("value", "").split()) for rule in contract.get("rules", [])
                  if rule.get("required", True) for step in rule.get("steps", [])
                  if step.get("action") == "expectText" and step.get("target") == page["pageId"]]
        content = " ".join(page["content"].split())
        joined = " ".join(values)
        if not content or content not in joined:
            issues.append(f"필수 안내 페이지 {page['pageId']}의 전체 안내문을 확인하는 필수 문구 검사가 필요합니다.")
    return issues


def resolve_generation_context(storage, owner, value, action):
    if value.get("catalogHash") and value["catalogHash"] != read_catalog()["hash"]:
        raise ValueError("React 컴포넌트 코드 기준이 바뀌었습니다. 새 규칙으로 확인·승인하세요.")
    project_id = value.get("projectId")
    if not project_id:
        if owner.startswith("project:"):
            raise ValueError("공동 작업의 작성자와 프로젝트 기준이 없습니다.")
        return {"prompt": "", "pages": [], "identifiers": []}
    collaboration = Collaboration(storage)
    try:
        scope = collaboration.require(collaboration.resolve_scope(value.get("actor"), project_id), action)
        if scope["owner"] != owner or not collaboration.is_current(scope, value):
            raise ValueError("상품 가이드가 바뀌었거나 작업 범위가 일치하지 않습니다.")
        context = collaboration.published_context(scope, value["productId"])
    except CollaborationError as error:
        raise ValueError("현재 공동 작업 권한과 확정된 상품 기준을 다시 확인하세요.") from error
    ontology = context["ontology"]
    graph = LocalGraphStore()
    graph.upsert_nodes([Node(**node) for node in ontology["nodes"]])
    graph.upsert_edges([Edge(**edge) for edge in ontology["edges"]])
    products = graph.find_by_label("Product")
    if len(products) != 1:
        raise ValueError("저장된 상품 온톨로지의 시작 노드를 확인하지 못했습니다.")
    product = products[0]
    summary = {"title": product.props["title"], "description": product.props.get("description", ""),
               "conditions": [], "procedures": [], "notices": []}
    for _, condition in graph.neighbors(product.id, "HAS_CONDITION"):
        summary["conditions"].append({"sourceNodeId": condition.id, "text": condition.props["text"]})
    for _, procedure in graph.neighbors(product.id, "HAS_PROCEDURE"):
        summary["procedures"].append({"sourceNodeId": procedure.id, "title": procedure.props["title"],
                                      "description": procedure.props["description"], "order": procedure.props["order"]})
    summary["procedures"].sort(key=lambda item: item["order"])
    pages = []
    for _, policy in graph.neighbors(product.id, "HAS_POLICY_RULE"):
        for _, screen in graph.neighbors(policy.id, "CONSTRAINS"):
            page = {"pageId": screen.props["pageId"], "title": screen.props["title"], "content": screen.props["content"],
                    "required": policy.props["required"], "sourceNodeId": policy.id}
            pages.append(page)
            summary["notices"].append(page)
    prompt = json.dumps(summary, ensure_ascii=False)
    if len(prompt.encode()) > 100_000:
        raise ValueError("한 번에 검토할 상품 기준이 너무 큽니다. 업무 범위를 나누어 확정하세요.")
    return {"prompt": prompt, "pages": pages, "identifiers": [node["id"] for node in ontology["nodes"]]}
