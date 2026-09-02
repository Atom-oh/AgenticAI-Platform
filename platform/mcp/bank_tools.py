"""아톰은행 플랫폼 툴 — AgentCore Gateway Lambda 타깃 (중앙 MCP 연동).

중앙 MCP(Gateway `nexus-platform-tools`, Cognito JWT 인바운드)에 노출되는 툴:
  analyze_regulation_impact(reg_code|question) — 규정 개정 영향 4-hop 순회 (S1 엔진과 동일 데이터)
  resolve_metric(term)                          — Semantic Layer 지표 해석 (SQL 템플릿 반환)
  list_regulations(query)                       — 규정 목록/검색

읽기 전용·합성데이터. AWS 리소스 접근 없음 (데이터는 배포 패키지에 동봉).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from graph.store import LocalGraphStore  # noqa: E402

_store = None


def _get_store():
    global _store
    if _store is None:
        _store = LocalGraphStore.from_seed_dir(
            os.path.join(os.path.dirname(__file__), "seed", "out"))
    return _store


def _metrics():
    with open(os.path.join(os.path.dirname(__file__), "semantic", "metrics.json"),
              encoding="utf-8") as f:
        return json.load(f)["metrics"]


def handler(event, context):
    tool = ""
    cc = getattr(context, "client_context", None)
    if cc and getattr(cc, "custom", None):
        tool = cc.custom.get("bedrockAgentCoreToolName", "")
    tool = tool.split("___")[-1]
    args = event if isinstance(event, dict) else {}
    store = _get_store()

    if tool == "list_regulations":
        q = (args.get("query") or "").replace(" ", "")
        regs = store.find_by_label("Regulation")
        rows = [{"code": r.props["code"], "title": r.props["title"],
                 "article": r.props["article"], "version": r.props["version"]}
                for r in regs if not q or q in r.props["title"].replace(" ", "")]
        return {"regulations": rows[:20]}

    if tool == "analyze_regulation_impact":
        code = args.get("reg_code") or ""
        if not code and args.get("question"):
            # 간이 seed 선택: 제목 부분일치
            qq = args["question"].replace(" ", "")
            best = None
            for r in store.find_by_label("Regulation"):
                t = r.props["title"].replace(" ", "")
                score = sum(1 for i in range(len(t) - 1) if t[i:i + 2] in qq)
                if best is None or score > best[0]:
                    best = (score, r.props["code"])
            code = best[1] if best else ""
        r = store.impact_of_regulation(code)
        if not r.regulation:
            return {"error": f"규정 없음: {code}",
                    "hint": "list_regulations로 코드를 먼저 찾으세요"}
        return {
            "regulation": {"code": code, "title": r.regulation.props["title"],
                           "article": r.regulation.props["article"]},
            "counts": r.counts(),
            "products": [{"code": n.props["productCode"], "name": n.props["name"]}
                         for n in r.products[:15]],
            "screens": [{"id": n.props["screenId"], "name": n.props["name"]}
                        for n in r.screens[:15]],
            "departments": [{"code": n.props["deptCode"], "name": n.props["name"]}
                            for n in r.departments],
            "documents": [{"id": n.props["docId"], "title": n.props["title"]}
                          for n in r.documents[:15]],
        }

    if tool == "resolve_metric":
        term = (args.get("term") or "").replace(" ", "")
        for m in _metrics():
            names = [m["name"]] + m.get("korean_aliases", [])
            if any(a.replace(" ", "") in term or term in a.replace(" ", "") for a in names if a):
                return {"name": m["name"], "unit": m["unit"], "ownerDept": m["owner_dept"],
                        "description": m["description"], "sqlTemplate": m["sql_template"]}
        return {"error": "Semantic Layer에 정의 없음",
                "available": [m["name"] for m in _metrics()]}

    return {"error": f"unknown tool: {tool}"}
