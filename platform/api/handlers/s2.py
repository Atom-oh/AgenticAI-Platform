"""S2 마이데이터 상담 파이프라인 (SPEC v2 §2 S2 · §6 · §11 · §12.4).

질의 → ⓪ 추론 경로 표기(§11-1) → ① 입력 Guardrails → ② Semantic Layer
→ [VPC 내부 플레인: ③ 정확 조회 → ④ 결정론적 계산 → ⑤ 규칙 기반 토큰화] → 독립 PII 스캔(F6)
→ ⑥ 익명화 게이트(engine.gate — 모델로 가는 유일한 경로, 실측·차단) → Bedrock 설명 스트리밍
→ ⑦ 출력 Guardrails → [VPC 내부: 수치 검증 + 재식별] → ⑧ Semantic 검증('전월실적') → 회신.

숫자는 전부 계산엔진이 만들고 LLM은 설명만 한다 (§12.4). 재식별 매핑은 VPC 내부 밖으로 나오지 않는다.
Semantic Layer 토글(body.semanticLayer, 기본 true)을 끄면 모델이 '전월실적'을 정의 없이 스스로 계산하는 안티패턴을
의도적으로 시연하고, 출력 검증기(semantic_check)가 계산엔진 값과 비교해 '조용히 틀림'을 드러낸다 (§6 데모 포인트).
모델이 낸 숫자는 회신 텍스트에서 파싱만 한다 — 만들어내지 않는다.
경로(body.route: claude | gemma)는 게이트가 어댑터를 고른다; 배지 문구의 출처는 engine.gate.route_info / gate_info 하나다.
"""
from __future__ import annotations

import os
import re

import boto3

from common import costguard, pii, plane, tracing
from common.ctx import Ctx
from common.log import log_event

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VER = os.environ.get("GUARDRAIL_VERSION", "DRAFT")

SYSTEM = ("당신은 아톰은행 상담 도우미입니다. 제공된 계산엔진 확정값만 사용해 우대금리 충족 여부와 "
          "가능 금액을 한국어로 친절히 설명하세요. 어떤 숫자도 새로 만들지 마세요. "
          "전월실적을 포함한 모든 수치는 계산엔진 확정값을 그대로 인용하세요. "
          "확정 신청은 영업점/앱에서 진행하도록 안내하세요.")
# Semantic Layer OFF — 안티패턴 시연 전용 (§6 데모 포인트). 운영 프롬프트가 아니다.
SYSTEM_NO_SEMANTIC = ("당신은 아톰은행 상담 도우미입니다. 제공된 계산엔진 확정값을 사용해 우대금리 충족 여부와 "
                      "가능 금액을 한국어로 친절히 설명하세요. 다만 고객의 '전월실적'은 정의가 제공되지 않으므로 "
                      "제공된 카드 거래내역 원본에서 직접 계산해 '전월실적 N원' 형태로 금액을 반드시 명시하세요. "
                      "확정 신청은 영업점/앱에서 진행하도록 안내하세요.")

PREV_MONTH_METRIC = "전월실적"
_METRIC_KEYS = ("전월실적", "전월 실적", "전월 이용금액", "지난달 사용액", "지난 달 사용액")
_UNIT = {"억": 100_000_000, "만": 10_000, "천": 1_000}
_SKIP_SUFFIX = {"%", "%p", "월", "년", "일", "개월", "건", "회", "배", "점", "번", "명"}
_CRITERION_RE = re.compile(r"^\s*(이상|이하|미만|초과|넘|이면|일 때|이어야|되어야|기준)")
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|만|천)?\s*(원|%p|%|개월|월|년|일|건|회|배|점|번|명)?")


def apply_guardrail(text: str, source: str, grounding: str = "", query: str = "") -> dict:
    """Bedrock Guardrails 실물 평가. OUTPUT 에 grounding(근거=마스킹 페이로드)을 주면 contextual grounding 정책이
    평가되는데, 이때 ApplyGuardrail 은 grounding_source · query · guard_content 세 블록을 모두 요구한다
    (query 가 없으면 ValidationException — 2026-09-02 라이브 확인). query 는 이미 INPUT 가드레일을 통과한 질문 원문."""
    rt = boto3.client("bedrock-runtime", region_name=REGION)
    content = [{"text": {"text": text[:4000]}}]
    if grounding and source == "OUTPUT":
        content = [{"text": {"text": grounding[:4000], "qualifiers": ["grounding_source"]}},
                   {"text": {"text": (query or " ")[:4000], "qualifiers": ["query"]}},
                   {"text": {"text": text[:4000], "qualifiers": ["guard_content"]}}]
    r = rt.apply_guardrail(guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VER,
                           source=source, content=content)
    topics = [t["name"] for a in r.get("assessments", []) for t in a.get("topicPolicy", {}).get("topics", [])]
    grounding_scores = [{"type": f["type"], "score": round(f.get("score", 0), 3), "action": f.get("action")}
                        for a in r.get("assessments", [])
                        for f in a.get("contextualGroundingPolicy", {}).get("filters", [])]
    pii_hits = [{"type": e.get("type") or e.get("name"), "action": e.get("action")}
                for a in r.get("assessments", [])
                for e in (a.get("sensitiveInformationPolicy", {}).get("piiEntities", [])
                          + a.get("sensitiveInformationPolicy", {}).get("regexes", []))
                if e.get("action") in ("ANONYMIZED", "BLOCKED")]
    words = [w.get("match") for a in r.get("assessments", [])
             for w in a.get("wordPolicy", {}).get("managedWordLists", [])]
    action = r["action"]
    # 토픽/단어/차단 없이 PII 익명화만 일어난 경우: 차단이 아니라 '익명화 적용'으로 구분한다
    if action == "GUARDRAIL_INTERVENED" and not topics and not words and pii_hits \
            and all(h["action"] == "ANONYMIZED" for h in pii_hits):
        action = "ANONYMIZED"
    return {"action": action, "topics": topics, "grounding": grounding_scores, "pii": pii_hits,
            "words": words, "message": (r.get("outputs") or [{}])[0].get("text", "")}


# ---------------------------------------------------------------------------
# 출력 검증기 — Semantic 검증 (§6, §12.4). 모델이 말한 '전월실적' 금액을 파싱만 한다.
# ---------------------------------------------------------------------------
def _amounts_in(seg: str) -> list[int]:
    """구간 안에서 원 단위 금액 후보를 순서대로. '30만원 이상' 같은 기준 표현·비율·날짜·건수는 제외한다.
    '1억 2,000만원' 처럼 단위가 이어지는 표현은 하나로 합친다."""
    out: list[int] = []
    acc = 0.0
    have = False
    last_end = -1
    for m in _NUM_RE.finditer(seg):
        num, unit, suffix = m.group(1), m.group(2), m.group(3)
        if suffix in _SKIP_SUFFIX:
            if have:
                out.append(int(round(acc)))
                acc, have = 0.0, False
            continue
        try:
            v = float(num.replace(",", "")) * _UNIT.get(unit or "", 1)
        except ValueError:
            continue
        if have and seg[last_end:m.start()].strip():  # 앞 수치와 떨어져 있으면 새 수치
            out.append(int(round(acc)))
            acc, have = 0.0, False
        if not unit and not suffix and v < 1000:       # 단위 없는 작은 수(서수·항목 번호)는 금액이 아니다
            continue
        acc += v
        have = True
        last_end = m.end()
        if suffix == "원" or not unit:
            if _CRITERION_RE.match(seg[m.end():m.end() + 8]):  # '… 이상/미만' → 기준값, 고객 수치가 아니다
                acc, have = 0.0, False
                continue
            out.append(int(round(acc)))
            acc, have = 0.0, False
    if have:
        out.append(int(round(acc)))
    return out


def stated_amounts(text: str, keys: tuple = _METRIC_KEYS, window: int = 90, back: int = 30) -> list[int]:
    """'전월실적' 언급 근처(뒤 window자, 앞 back자)에서 모델이 제시한 금액(원) 후보 — 등장 순서, 중복 제거. 없으면 []."""
    text = text or ""
    spans = sorted({(m.start(), m.end()) for k in keys for m in re.finditer(re.escape(k), text)})
    out: list[int] = []
    for start, end in spans:
        for v in _amounts_in(text[end:end + window]) + _amounts_in(text[max(0, start - back):start]):
            if v not in out:
                out.append(v)
    return out


def semantic_check(answer: str, semantic_layer: bool, engine_value, diagnostics: dict | None = None,
                   definition: str = "") -> dict:
    """모델 회신의 '전월실적' 금액 vs 계산엔진 값. 반환 {semanticLayer, engineValue, modelValue, mismatch, note, candidates}.
    모델 수치는 파싱 결과만 쓴다 — 없으면 '모델이 수치를 제시하지 않음'. 후보 중 계산엔진 값이 있으면 일치로 본다."""
    out = {"semanticLayer": bool(semantic_layer), "metric": PREV_MONTH_METRIC, "definition": definition,
           "engineValue": engine_value, "modelValue": None, "mismatch": False, "candidates": [], "note": ""}
    if engine_value is None:
        out["note"] = "계산엔진 값 없음 — 플레인이 metricByEngine 을 반환하지 않았습니다 (플레인 재배포 필요) · 비교 미수행"
        return out
    try:
        engine_value = int(engine_value)
    except (TypeError, ValueError):
        out["note"] = "계산엔진 값 형식 오류 · 비교 미수행"
        return out
    out["engineValue"] = engine_value
    cands = stated_amounts(answer)
    out["candidates"] = cands[:5]
    if not cands:
        out["note"] = "모델이 수치를 제시하지 않음"
        return out
    if engine_value in cands:
        out["modelValue"] = engine_value
        out["note"] = ("모델 제시값 = 계산엔진 값 (Semantic Layer 정의 기준)" if semantic_layer
                       else "모델 제시값 = 계산엔진 값 — 정의 없이도 이번엔 맞았음 (보장되지 않음)")
        return out
    mv = cands[0]
    out["modelValue"] = mv
    out["mismatch"] = True
    d = diagnostics or {}
    if mv == d.get("당월실적"):
        why = "모델이 '전월실적'을 당월 승인 합계로 계산"
    elif mv == d.get("전월_취소포함"):
        why = "모델이 취소분을 포함해 계산 (정의: 취소분 제외)"
    elif mv == d.get("2개월_승인합계") or mv == d.get("2개월_취소포함"):
        why = "모델이 직전월·당월 2개월을 합산"
    else:
        why = f"계산엔진 값과 불일치 (차이 {mv - engine_value:+,}원)"
    out["note"] = why + (" — Semantic Layer ON 상태의 불일치: §12.4 위반, 출력 검증기가 잡음"
                        if semantic_layer else " — 정의 없이 조용히 틀림 (Semantic Layer OFF)")
    return out


# ---------------------------------------------------------------------------
# 표기 (§11-1 · §11-2) — 문구 출처는 engine.gate 하나
# ---------------------------------------------------------------------------
def route_stage(route: str | None) -> dict:
    from engine import gate
    ri = gate.route_info(route)
    b = ri.get("badge") or {}
    return {"route": ri["route"], "tier": ri["tier"], "modelId": ri["modelId"], "endpoint": ri.get("endpoint", ""),
            "region": ri.get("region", ""), "inferenceRouting": ri.get("inferenceRouting", ""),
            "inferenceRoutingLabel": ri.get("inferenceRoutingLabel", ""),
            "storage": ri.get("storage", ""), "storageLabel": ri.get("storageLabel", "서울 리전"),
            "badge": {"title": "추론 경로", "prod": b.get("prod", ""), "demo": b.get("demo", ""),
                      "region": b.get("region", ""), "substituted": bool(b.get("substituted", False)),
                      "implemented": bool(b.get("implemented", True))}}


def gate_badge() -> dict:
    from engine import gate
    gi = gate.gate_info()
    return {"title": gi.get("title", "익명화 게이트"), "prod": gi.get("prod", ""), "demo": gi.get("demo", ""),
            "refuseTypes": list(gi.get("refuseTypes", [])), "detector": gi.get("detector", ""),
            "measured": list(gi.get("measured", []))}


def _truthy(v, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "off", "no", "")


# ---------------------------------------------------------------------------
# VPC 내부 플레인 호출 (로컬 폴백은 ALLOW_LOCAL_PLANE=1 에서만 — 분리가 아니며 UI에 표기)
# ---------------------------------------------------------------------------
def _prepare_local(email: str, query: str, extra: dict | None = None) -> dict:
    """개발용 로컬 폴백 — 분리가 아니며 UI에 그렇게 표기된다."""
    from onprem.service import handle
    code, out = handle("/s2/prepare", {"email": email, "query": query, "traceId": "local", **(extra or {})})
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
    route = body.get("route") or None
    if route is not None:
        route = str(route).strip().lower()[:32]
        from engine import gate
        try:
            gate.current_route(route)
        except ValueError as e:
            ctx.error(str(e))
            return
    semantic_on = _truthy(body.get("semanticLayer"), default=True)
    ref_date = str(body.get("refDate") or "")[:10] or None   # 리허설·테스트용 기준일 고정 (선택)
    state: dict = {"blocked": False, "usage": {}, "pii": {"count": 0}, "maskedFields": [], "gOut": None,
                   "plane": plane.mode(), "boundary": None, "modelId": None, "route": None, "tier": None,
                   "gateRefused": False, "gateTypes": [], "semantic": None}

    def run(c: Ctx) -> None:
        from engine import gate
        # ⓪ 추론 경로 — 어떤 모델·엔드포인트·라우팅으로 나가는지 먼저 표기한다 (§11-1, §8-3 ④)
        ri = route_stage(route)
        state.update(route=ri["route"], modelId=ri["modelId"], tier=ri["tier"])
        c.stage("s2", "route", plane="cloud", semanticLayer=semantic_on, **ri)

        # ① 입력 가드레일 (실물 Bedrock Guardrails)
        g_in = apply_guardrail(query, "INPUT")
        c.stage("s2", "guardrail_in", result=g_in, plane="cloud")
        if g_in["action"] == "GUARDRAIL_INTERVENED":
            state.update(blocked=True, topics=g_in["topics"] or [h["type"] for h in g_in["pii"]] or g_in["words"])
            c.done("s2", blocked=True, message=g_in["message"], topics=g_in["topics"],
                   route=ri["route"], modelId=ri["modelId"], tier=ri["tier"], semanticLayer=semantic_on)
            return

        # ② Semantic Layer — 지표 정의는 이 계층만 신뢰 (해석 실패는 실패로 보인다)
        from semantic.loader import SemanticLayer
        layer = SemanticLayer()
        metric = layer.resolve(query)
        prev_def = layer.resolve(PREV_MONTH_METRIC)
        prev_metric = ({"name": prev_def.name, "description": prev_def.description, "unit": prev_def.unit,
                        "ownerDept": prev_def.owner_dept, "sql": prev_def.sql_template.strip()} if prev_def else None)
        sem_extra = {"semanticLayer": semantic_on,
                     "prevMonthMetric": prev_metric if semantic_on else None,
                     "toggleNote": (None if semantic_on else
                                    "Semantic Layer OFF — '전월실적' 정의를 모델에 제공하지 않는다 (안티패턴 시연 · 운영 금지)")}
        if metric is None:
            c.stage("s2", "semantic", metric=None, plane="onprem",
                    note="Semantic Layer에 정의된 지표를 질문에서 찾지 못했습니다 — 기본 상담 지표(우대금리)로 진행", **sem_extra)
            metric = layer.resolve("우대금리")
        else:
            c.stage("s2", "semantic", plane="onprem", metric={"name": metric.name, "unit": metric.unit,
                    "ownerDept": metric.owner_dept, "sql": metric.sql_template.strip()}, **sem_extra)

        # ③④⑤ 정확 조회 → 계산 → 마스킹 : VPC 내부 플레인
        mode = plane.mode()
        prep_body = {"email": c.email, "query": query, "traceId": c.trace_id, "semanticLayer": semantic_on}
        if ref_date:
            prep_body["refDate"] = ref_date
        if mode in ("bridge", "direct"):
            prep = plane.call("/s2/prepare", prep_body)
            source = f"{plane.label()} · {prep.get('dataSource', '')}"
        elif mode == "local":
            prep = _prepare_local(c.email, query, {k: v for k, v in prep_body.items() if k not in ("email", "query")})
            source = prep["dataSource"]
        else:
            raise plane.PlaneUnavailable(plane.label())
        metric_by_engine = prep.get("metricByEngine")           # 구버전 플레인은 없다 → UI 에 '미제공' 으로 보인다
        c.stage("s2", "lookup", values=prep["rawValues"], source=source, plane="onprem",
                txnSample=prep.get("txnSample"), metricByEngine=metric_by_engine, metricPeriod=prep.get("metricPeriod"))
        c.stage("s2", "calc", rate=prep["rate"], limit=prep["limit"], plane="onprem",
                metricByEngine=metric_by_engine, metricDefinitions=prep.get("metricDefinitions"))
        masked_payload = prep["maskedPayload"]
        state["maskedFields"] = [f["field"] for f in prep["maskedFields"]]
        # F6: 마스킹과 독립된 탐지기로 경계 페이로드를 스캔한다
        scan = pii.scan_outbound(masked_payload)
        state["pii"] = scan
        c.stage("s2", "mask", maskedFields=prep["maskedFields"], maskedPayload=masked_payload,
                piiOutbound=scan["count"], piiHits=scan["hits"], piiDetectors=scan["detectors"], plane="boundary",
                badge=gate_badge(), systemPromptChars=len(SYSTEM if semantic_on else SYSTEM_NO_SEMANTIC))

        # ⑥ 익명화 게이트 → Bedrock 설명 생성 (스트리밍). 식별자가 남아 있으면 GateRefused — 페이로드는 모델에 가지 않는다
        system = SYSTEM if semantic_on else SYSTEM_NO_SEMANTIC
        try:
            st = gate.stream(system, masked_payload, max_tokens=800, route=route, purpose="s2", trace_id=c.trace_id)
        except gate.GateRefused as e:
            by_type = ((e.boundary or {}).get("piiRules") or {}).get("byType", {})
            hits = [{"type": t, "count": int(by_type.get(t, 0))} for t in e.types]
            state.update(blocked=True, gateRefused=True, gateTypes=list(e.types), boundary=e.boundary)
            c.done("s2", blocked=True, gateRefused=True, blockedBy="gate", message=str(e), hits=hits,
                   boundary=e.boundary, route=ri["route"], modelId=ri["modelId"], tier=ri["tier"],
                   plane=mode, planeLabel=plane.label(), semanticLayer=semantic_on)
            return
        full = []
        for tk in st:
            full.append(tk)
            c.token("s2", tk)
        answer = "".join(full)
        state.update(usage=st.usage, boundary=st.boundary, modelId=st.model_id, route=st.route, tier=st.tier)

        # ⑦ 출력 가드레일(근거=마스킹 페이로드) → VPC 내부 수치 검증 + 재식별
        g_out = apply_guardrail(answer, "OUTPUT", grounding=masked_payload, query=query)
        state["gOut"] = g_out
        final_answer = answer
        if g_out["action"] in ("ANONYMIZED", "GUARDRAIL_INTERVENED") and g_out["message"]:
            final_answer = g_out["message"]  # 가드레일이 익명화/차단한 텍스트를 최종 회신으로 사용한다
        if mode in ("bridge", "direct"):
            fin = plane.call("/s2/finalize", {"traceId": c.trace_id, "answer": final_answer,
                                              "allowedNumbers": prep["allowedNumbers"]})
        else:
            fin = _finalize_local(c.trace_id, final_answer, prep["allowedNumbers"])

        # ⑧ Semantic 검증 — 모델이 말한 '전월실적' vs 계산엔진 (§6 데모 포인트, §12.4 출력 검증기)
        chk = semantic_check(answer, semantic_on,
                             (metric_by_engine or {}).get(PREV_MONTH_METRIC) if metric_by_engine else None,
                             {**(prep.get("metricDiagnostics") or {}), **(metric_by_engine or {})},  # 당월실적 오인 진단 포함
                             definition=(prep.get("metricDefinitions") or {}).get(PREV_MONTH_METRIC, ""))
        chk["period"] = prep.get("metricPeriod")
        state["semantic"] = chk
        c.stage("s2", "semantic_check", plane="onprem", **chk)

        c.done("s2", unmasked=fin["unmasked"], guardrailOut=g_out, inventedNumbers=fin["inventedNumbers"],
               usage=st.usage, plane=mode, planeLabel=plane.label(),
               boundary=st.boundary, modelId=st.model_id, route=st.route, tier=st.tier,
               inferenceRouting=ri["inferenceRouting"], inferenceRoutingLabel=ri["inferenceRoutingLabel"],
               storageLabel=ri["storageLabel"], regionBadge=ri["badge"]["region"], nonStream=bool(st.non_stream),
               semanticLayer=semantic_on, semanticCheck=chk)

    res = costguard.guarded(ctx, "s2", query, run)
    u = state["usage"]
    costguard.add_usage(int(u.get("inputTokens", 0)) + int(u.get("outputTokens", 0)))
    b = state["boundary"] or {}
    chk = state["semantic"] or {}
    rec = {"traceId": ctx.trace_id, "scenario": "S2", "email": ctx.email, "query": query,
           "blocked": state["blocked"], "topics": state.get("topics", []),
           "maskedFields": state["maskedFields"],
           "piiOutbound": state["pii"]["count"], "piiDetectors": state["pii"].get("detectors", []),
           "tokensIn": int(u.get("inputTokens", 0)), "tokensOut": int(u.get("outputTokens", 0)),
           "guardrailOut": (state["gOut"] or {}).get("action"),
           "cached": res["cached"], "plane": state["plane"], "elapsedMs": ctx.elapsed_ms(),
           # 게이트 실측 (§8-3) — 라벨·수치만, 값은 없다
           "modelId": state["modelId"], "route": state["route"], "tier": state["tier"],
           "boundaryFields": list(b.get("fieldsPassed") or []),
           "boundaryChars": int(b.get("chars", 0) or 0), "boundaryEstTokens": int(b.get("estTokens", 0) or 0),
           "crossings": 0 if (state["gateRefused"] or state["blocked"]) else (1 if state["modelId"] else 0),
           "semanticLayer": semantic_on, "semanticMismatch": bool(chk.get("mismatch", False)),
           "semanticStated": chk.get("modelValue") is not None}
    if state["gateRefused"]:
        rec.update(blockedBy="gate", gateRejected=True, reason="gate", gateTypes=state["gateTypes"],
                   piiOutbound=int((b.get("piiRules") or {}).get("count", 0) or 0), piiDetectors=["rules(gate)"])
    tracing.record_trace(rec)
    log_event("s2.done", ctx.trace_id, blocked=state["blocked"], gateRefused=state["gateRefused"],
              pii=state["pii"]["count"], cached=res["cached"], plane=state["plane"], ms=ctx.elapsed_ms(),
              modelId=state["modelId"], route=state["route"], tier=state["tier"],
              semanticLayer=semantic_on, semanticMismatch=bool(chk.get("mismatch", False)))


ROUTES = {"s2": handle}
