"""S2 마이데이터 상담 파이프라인 (SPEC F3).

질의 → 입력 Guardrails → Semantic Layer → [온프렘 플레인: 정확 조회 → 결정론적 계산 → 마스킹]
→ 독립 PII 스캔(F6) → Bedrock 설명 스트리밍 → 출력 Guardrails → [온프렘: 수치 검증 + 재식별] → 회신.
숫자는 전부 계산엔진이 만들고 LLM은 설명만 한다 (§12.2). 재식별 매핑은 온프렘 밖으로 나오지 않는다.
"""
from __future__ import annotations

import os

import boto3

from common import costguard, pii, plane, tracing
from common.ctx import Ctx
from common.log import log_event

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VER = os.environ.get("GUARDRAIL_VERSION", "DRAFT")

SYSTEM = ("당신은 아톰은행 상담 도우미입니다. 제공된 계산엔진 확정값만 사용해 우대금리 충족 여부와 "
          "가능 금액을 한국어로 친절히 설명하세요. 어떤 숫자도 새로 만들지 마세요. "
          "확정 신청은 영업점/앱에서 진행하도록 안내하세요.")


def apply_guardrail(text: str, source: str, grounding: str = "") -> dict:
    rt = boto3.client("bedrock-runtime", region_name=REGION)
    content = [{"text": {"text": text[:4000]}}]
    if grounding and source == "OUTPUT":
        content = [{"text": {"text": grounding[:4000], "qualifiers": ["grounding_source"]}},
                   {"text": {"text": text[:4000], "qualifiers": ["guard_content"]}}]
    r = rt.apply_guardrail(guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VER,
                           source=source, content=content)
    topics = [t["name"] for a in r.get("assessments", []) for t in a.get("topicPolicy", {}).get("topics", [])]
    grounding_scores = [{"type": f["type"], "score": round(f.get("score", 0), 3), "action": f.get("action")}
                        for a in r.get("assessments", [])
                        for f in a.get("contextualGroundingPolicy", {}).get("filters", [])]
    return {"action": r["action"], "topics": topics, "grounding": grounding_scores,
            "message": (r.get("outputs") or [{}])[0].get("text", "")}


def _prepare_local(email: str, query: str) -> dict:
    """개발용 로컬 폴백 — ALLOW_LOCAL_PLANE=1 에서만. 분리가 아니며 UI에 그렇게 표기된다."""
    from onprem.service import handle
    code, out = handle("/s2/prepare", {"email": email, "query": query, "traceId": "local"})
    out["dataSource"] = "로컬 폴백 (개발용 — 플레인 분리 아님)"
    return out


def _finalize_local(trace_id: str, answer: str, allowed: list[str]) -> dict:
    from onprem.service import handle
    return handle("/s2/finalize", {"traceId": trace_id, "answer": answer, "allowedNumbers": allowed})[1]


def handle(ctx: Ctx, body: dict) -> None:
    query = str(body.get("query", ""))[:500].strip()
    if not query:
        ctx.error("질문이 비어 있습니다.")
        return
    state: dict = {"blocked": False, "usage": {}, "pii": {"count": 0}, "maskedFields": [], "gOut": None,
                   "plane": plane.mode()}

    def run(c: Ctx) -> None:
        # ① 입력 가드레일 (실물 Bedrock Guardrails)
        g_in = apply_guardrail(query, "INPUT")
        c.stage("s2", "guardrail_in", result=g_in, plane="cloud")
        if g_in["action"] == "GUARDRAIL_INTERVENED":
            state.update(blocked=True, topics=g_in["topics"])
            c.done("s2", blocked=True, message=g_in["message"], topics=g_in["topics"])
            return

        # ② Semantic Layer — 지표 정의는 이 계층만 신뢰 (해석 실패는 실패로 보인다)
        from semantic.loader import SemanticLayer
        metric = SemanticLayer().resolve(query)
        if metric is None:
            c.stage("s2", "semantic", metric=None, plane="onprem",
                    note="Semantic Layer에 정의된 지표를 질문에서 찾지 못했습니다 — 기본 상담 지표(우대금리)로 진행")
            metric = SemanticLayer().resolve("우대금리")
        else:
            c.stage("s2", "semantic", plane="onprem", metric={"name": metric.name, "unit": metric.unit,
                    "ownerDept": metric.owner_dept, "sql": metric.sql_template.strip()})

        # ③④⑤ 정확 조회 → 계산 → 마스킹 : 온프렘 플레인
        mode = plane.mode()
        if mode in ("bridge", "direct"):
            prep = plane.call("/s2/prepare", {"email": c.email, "query": query, "traceId": c.trace_id})
            source = f"{plane.label()} · {prep.get('dataSource', '')}"
        elif mode == "local":
            prep = _prepare_local(c.email, query)
            source = prep["dataSource"]
        else:
            raise plane.PlaneUnavailable(plane.label())
        c.stage("s2", "lookup", values=prep["rawValues"], source=source, plane="onprem")
        c.stage("s2", "calc", rate=prep["rate"], limit=prep["limit"], plane="onprem")
        masked_payload = prep["maskedPayload"]
        state["maskedFields"] = [f["field"] for f in prep["maskedFields"]]
        # F6: 마스킹과 독립된 탐지기로 경계 페이로드를 스캔한다
        scan = pii.scan_outbound(masked_payload)
        state["pii"] = scan
        c.stage("s2", "mask", maskedFields=prep["maskedFields"], maskedPayload=masked_payload,
                piiOutbound=scan["count"], piiHits=scan["hits"], piiDetectors=scan["detectors"], plane="boundary")

        # ⑥ Bedrock 설명 생성 (스트리밍) — 마스킹된 컨텍스트만 수신
        from engine import bedrock
        st = bedrock.Stream(SYSTEM, masked_payload, max_tokens=800)
        full = []
        for tk in st:
            full.append(tk)
            c.token("s2", tk)
        answer = "".join(full)
        state["usage"] = st.usage

        # ⑦ 출력 가드레일(근거=마스킹 페이로드) → 온프렘 수치 검증 + 재식별
        g_out = apply_guardrail(answer, "OUTPUT", grounding=masked_payload)
        state["gOut"] = g_out
        if mode in ("bridge", "direct"):
            fin = plane.call("/s2/finalize", {"traceId": c.trace_id, "answer": answer,
                                              "allowedNumbers": prep["allowedNumbers"]})
        else:
            fin = _finalize_local(c.trace_id, answer, prep["allowedNumbers"])
        c.done("s2", unmasked=fin["unmasked"], guardrailOut=g_out, inventedNumbers=fin["inventedNumbers"],
               usage=st.usage, plane=mode, planeLabel=plane.label())

    res = costguard.guarded(ctx, "s2", query, run)
    u = state["usage"]
    costguard.add_usage(int(u.get("inputTokens", 0)) + int(u.get("outputTokens", 0)))
    tracing.record_trace({"traceId": ctx.trace_id, "scenario": "S2", "email": ctx.email, "query": query,
                          "blocked": state["blocked"], "topics": state.get("topics", []),
                          "maskedFields": state["maskedFields"],
                          "piiOutbound": state["pii"]["count"], "piiDetectors": state["pii"].get("detectors", []),
                          "tokensIn": int(u.get("inputTokens", 0)), "tokensOut": int(u.get("outputTokens", 0)),
                          "guardrailOut": (state["gOut"] or {}).get("action"),
                          "cached": res["cached"], "plane": state["plane"], "elapsedMs": ctx.elapsed_ms()})
    log_event("s2.done", ctx.trace_id, blocked=state["blocked"], pii=state["pii"]["count"],
              cached=res["cached"], plane=state["plane"], ms=ctx.elapsed_ms())


ROUTES = {"s2": handle}
