"""AgentCore Gateway Lambda 타깃 — 플랫폼 도구 10종 (tool_schema.json과 1:1).

경계 원칙(SPEC v2 §3-2): 개인데이터를 다루는 도구(lookup_customer_profile)는 VPC 내부 플레인에서 조회·계산·마스킹한 뒤
**마스킹된 페이로드와 계산 내역만** 반환한다. 원본 값(고객명 등)은 이 함수 밖으로 나가지 않는다.
도구 이름은 context.client_context.custom['bedrockAgentCoreToolName'] ("<target>___<tool>")로 전달된다.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from common.log import log_event  # noqa: E402

_store = None


def _graph():
    global _store
    if _store is None:
        from graph.store import get_store
        _store = get_store()
    return _store


def _nodes(nodes, keys):
    return [{k: n.props.get(k) for k in keys if k in n.props} | {"id": n.id} for n in nodes[:20]]


def tool_list_regulations(args: dict) -> dict:
    q = (args.get("query") or "").replace(" ", "")
    regs = _graph().find_by_label("Regulation")
    rows = [{"code": r.props["code"], "title": r.props["title"], "article": r.props.get("article"), "version": r.props.get("version")}
            for r in regs if not q or q in r.props["title"].replace(" ", "")]
    return {"regulations": rows[:20], "total": len(rows)}


def tool_analyze_regulation_impact(args: dict) -> dict:
    store = _graph()
    code = args.get("reg_code") or ""
    if not code and args.get("question"):
        from engine.graphrag import _select_seed
        seed, conf, cands = _select_seed(store, [args["question"]])
        if not seed:
            return {"error": "질문에서 규정을 특정하지 못했습니다.", "candidates": cands}
        code = seed["code"]
    r = store.impact_of_regulation(code)
    if not r.regulation:
        return {"error": f"규정 없음: {code}", "hint": "list_regulations로 코드를 먼저 찾으세요"}
    out = {"regulation": {"code": code, **{k: r.regulation.props.get(k) for k in ("title", "article", "version")}},
           "counts": r.counts(),
           "products": _nodes(r.products, ["productCode", "name", "category"]),
           "screens": _nodes(r.screens, ["screenId", "name", "channel"]),
           "components": _nodes(r.components, ["componentId", "name", "version", "approvalStatus"]),
           "departments": _nodes(r.departments, ["deptCode", "name", "role"]),
           "documents": _nodes(r.documents, ["docId", "title", "type"])}
    if hasattr(r, "policy_rules"):
        out["policyRules"] = _nodes(getattr(r, "policy_rules"), ["ruleId", "title", "ruleType"])
    return out


def tool_impact_of_component(args: dict) -> dict:
    store = _graph()
    fn = getattr(store, "impact_of_component", None)
    if fn is None:
        return {"error": "미구현: impact_of_component (온톨로지 v2 배포 전)"}
    r = fn(args["component_id"])
    if not r.component:
        return {"error": f"컴포넌트 없음: {args['component_id']}"}
    return {"component": {"id": r.component.id, **{k: r.component.props.get(k) for k in ("name", "version", "approvalStatus")}},
            "counts": r.counts(),
            "screens": _nodes(r.screens, ["screenId", "name"]), "patterns": _nodes(r.patterns, ["patternId", "name"]),
            "policyRules": _nodes(r.policy_rules, ["ruleId", "title"]), "departments": _nodes(r.departments, ["deptCode", "name"])}


def tool_resolve_metric(args: dict) -> dict:
    from semantic.loader import SemanticLayer
    m = SemanticLayer().resolve(args.get("term", ""))
    if not m:
        return {"error": "Semantic Layer에 정의 없음", "available": [x.name for x in SemanticLayer().metrics]}
    return {"name": m.name, "unit": m.unit, "ownerDept": m.owner_dept, "description": m.description, "sqlTemplate": m.sql_template}


def tool_lookup_customer_profile(args: dict) -> dict:
    """VPC 내부 플레인에서 조회·계산·마스킹 → 마스킹 결과만 반환 (도구 출력 = 경계)."""
    from common import plane
    email = (args.get("customer_ref") or os.environ.get("DEFAULT_CUSTOMER_REF", "demo@atomai.click")).strip()
    mode = plane.mode()
    if mode in ("bridge", "direct"):
        prep = plane.call("/s2/prepare", {"email": email, "query": args.get("question", ""), "traceId": "agent"})
        source = plane.label()
    elif mode == "local":
        from onprem.service import handle
        _, prep = handle("/s2/prepare", {"email": email, "query": args.get("question", ""), "traceId": "agent"})
        source = "로컬 폴백 (개발용)"
    else:
        return {"error": "VPC 내부 플레인 미연결 — 개인데이터 조회 불가", "plane": mode}
    # 경계 통과 허용 항목만: 마스킹 페이로드 + 계산 내역 + 마스킹 필드. rawValues는 반환하지 않는다.
    from common import pii
    scan = pii.scan_rules(prep["maskedPayload"])
    if scan:
        return {"error": "익명화 게이트 거부: 마스킹 페이로드에 식별자 잔존", "hits": [h["type"] for h in scan]}
    log_event("tool.lookup_customer_profile", plane=mode, maskedFields=len(prep.get("maskedFields", [])))
    return {"maskedProfile": prep["maskedPayload"], "rate": prep["rate"], "limit": prep["limit"],
            "maskedFields": [f["field"] for f in prep.get("maskedFields", [])], "dataSource": source,
            "boundaryNote": "개인 식별자는 VPC 내부에서 토큰화됨 — 원본 값은 반환되지 않는다"}


def tool_calc_preferential_rate(args: dict) -> dict:
    from onprem.calc_engine import preferential_rate
    profile = {"salaryTransferMonths": int(args.get("salary_transfer_months") or 0),
               "cardMonthlyKrw": int(args.get("card_monthly_krw") or 0),
               "autoTransferCount": int(args.get("auto_transfer_count") or 0),
               "isFirstHome": bool(args.get("is_first_home"))}
    return preferential_rate(profile, str(args.get("base_rate", "4.5"))).to_dict()


def tool_calc_jeonse_limit(args: dict) -> dict:
    from onprem.calc_engine import jeonse_loan_limit
    return jeonse_loan_limit(int(args["deposit_krw"]), str(args["guarantee_ratio"]),
                             int(args["annual_income_krw"]), int(args.get("existing_debt_krw") or 0)).to_dict()


def tool_list_approved_components(args: dict) -> dict:
    from registry.api import list_approved
    recs = list_approved(subtype=args.get("subtype") or "COMPONENT")
    return {"components": [{"name": r["name"], "version": r.get("recordVersion"), "module": (r.get("payload") or {}).get("module"),
                            "exportName": (r.get("payload") or {}).get("exportName"),
                            "propsSchema": (r.get("payload") or {}).get("propsSchema")} for r in recs],
            "note": "APPROVED만 반환 (Consumer API)"}


def tool_run_screen_gates(args: dict) -> dict:
    import boto3
    fn = os.environ.get("GATES_FN", "")
    if not fn:
        return {"error": "게이트 실행기 미연결(GATES_FN)"}
    from registry.api import list_approved
    comps = [{"name": r["name"], "version": r.get("recordVersion"), **(r.get("payload") or {})} for r in list_approved(subtype="COMPONENT")]
    payload = {"code": args.get("code", ""), "filename": "Screen.tsx", "components": comps}
    r = boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "ap-northeast-2")).invoke(
        FunctionName=fn, Payload=json.dumps(payload, ensure_ascii=False).encode())
    body = json.loads(r["Payload"].read().decode() or "{}")
    # Registry 승인 검증: 헤더 주석의 Name@vN 이 승인 목록에 있는지
    import re
    used = re.findall(r"([A-Za-z]+)@(v\d+)", (args.get("code", "").splitlines() or [""])[0])
    approved = {(c["name"], c["version"]) for c in comps}
    body["registry"] = {"ok": all((n, v) in approved for n, v in used) and bool(used),
                        "used": [f"{n}@{v}" for n, v in used], "unapproved": [f"{n}@{v}" for n, v in used if (n, v) not in approved]}
    return body


def tool_search_internal_documents(args: dict) -> dict:
    try:
        from report.internal_tool_handler import handler as internal
        return internal({"query": args.get("query", ""), "top_k": int(args.get("top_k") or 5)}, None)
    except Exception as e:
        return {"error": f"내부 문서 검색 실패: {type(e).__name__}: {str(e)[:120]}"}


TOOLS = {
    "list_regulations": tool_list_regulations,
    "analyze_regulation_impact": tool_analyze_regulation_impact,
    "impact_of_component": tool_impact_of_component,
    "resolve_metric": tool_resolve_metric,
    "lookup_customer_profile": tool_lookup_customer_profile,
    "calc_preferential_rate": tool_calc_preferential_rate,
    "calc_jeonse_limit": tool_calc_jeonse_limit,
    "list_approved_components": tool_list_approved_components,
    "run_screen_gates": tool_run_screen_gates,
    "search_internal_documents": tool_search_internal_documents,
}


def handler(event, context):
    name = ""
    cc = getattr(context, "client_context", None)
    if cc and getattr(cc, "custom", None):
        name = cc.custom.get("bedrockAgentCoreToolName", "")
    name = name.split("___")[-1] or (event.get("tool") if isinstance(event, dict) else "")
    args = event if isinstance(event, dict) else {}
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}", "available": sorted(TOOLS)}
    log_event("tool.call", tool=name, argKeys=sorted(k for k in args.keys() if k != "code"))
    try:
        out = fn(args)
    except Exception as e:
        log_event("tool.failed", tool=name, error=f"{type(e).__name__}: {str(e)[:200]}")
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return json.loads(json.dumps(out, ensure_ascii=False, default=str))
