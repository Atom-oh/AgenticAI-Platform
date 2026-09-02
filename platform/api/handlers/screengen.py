"""S3 화면 생성 핸들러 (SPEC F5, §2 S3, §6.1 화면 7).

액션
  screengen             (스트리밍) body {prompt, previousSnapshot?}
                        단계 registry_lookup → skills → generate(토큰) → gates → [regenerate → generate → gates] → screengen.done
  screengen_components  (요청/응답) 에이전트에게 지금 보이는 승인 컴포넌트 목록 — Registry Consumer API 정확 조회

원칙: 프롬프트에는 Registry APPROVED 컴포넌트만 들어간다(벡터 검색 없음). 게이트는 실제 tsc/eslint/axe 실행 결과이며,
실행기가 없으면 '미판정'으로 표기한다. 재생성은 1회 상한(§12.9). Bedrock 실패 시 costguard 가 캐시 이벤트를 재생한다(cached 배지).
"""
from __future__ import annotations

from common import costguard, pii, tracing
from common.ctx import Ctx
from common.log import log_event
from screengen import agent

KIND = "screengen"
SCENARIO = "S3"


class _CtxEmitter:
    """agent.run 이 요구하는 emitter 표면(stage/token) → ctx 이벤트."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def stage(self, step: str, **kw) -> None:
        self.ctx.stage(KIND, step, **kw)

    def token(self, text: str) -> None:
        self.ctx.token(KIND, text)


def components(ctx: Ctx, body: dict) -> None:
    """Registry 가 에이전트에게 보여주는 승인 컴포넌트 (현재 시점 소비자 뷰)."""
    comps, source = agent.load_approved_components()
    ctx.post({"type": "screengen_components", "components": [agent.public_component(c) for c in comps],
              "count": len(comps), "source": source, "lookup": "exact",
              "gatesRunner": agent.gates_runner_mode(), "maxRegenerations": agent.MAX_REGENERATIONS})


def handle(ctx: Ctx, body: dict) -> None:
    prompt = str(body.get("prompt", ""))[:1000].strip()
    if not prompt:
        ctx.error("요청 문장이 비어 있습니다.")
        return
    prev = body.get("previousSnapshot")
    previous_snapshot = prev if isinstance(prev, dict) and prev.get("hash") else None
    state: dict = {"result": None}

    def run(c: Ctx) -> None:
        res = agent.run(prompt, _CtxEmitter(c), previous_snapshot=previous_snapshot,
                        outbound_scan=pii.scan_outbound)
        state["result"] = res
        c.done(KIND, **res)

    g = costguard.guarded(ctx, KIND, prompt, run)
    res = state["result"] or {}
    u = res.get("usage") or {}
    tokens_in, tokens_out = int(u.get("inputTokens", 0) or 0), int(u.get("outputTokens", 0) or 0)
    costguard.add_usage(tokens_in + tokens_out)
    used = [f"{c['name']}@{c['version']}" for c in res.get("componentsUsed", [])]
    tracing.record_trace({
        "traceId": ctx.trace_id, "scenario": SCENARIO, "email": ctx.email, "query": prompt,
        "blocked": False, "piiOutbound": int(res.get("piiOutbound", 0) or 0),
        "piiDetectors": (res.get("outbound") or {}).get("detectors", []), "maskedFields": [],
        "tokensIn": tokens_in, "tokensOut": tokens_out, "cached": g["cached"], "plane": "cloud",
        "attempts": res.get("attempts"), "regenerated": res.get("regenerated"), "gatesOk": res.get("ok"),
        "gatesRunner": res.get("gatesRunner"), "componentsUsed": used,
        "componentsSource": res.get("componentsSource"), "codeLen": len(res.get("code") or ""),
        "elapsedMs": ctx.elapsed_ms(),
    })
    log_event("screengen.done", ctx.trace_id, attempts=res.get("attempts"), ok=res.get("ok"), cached=g["cached"],
              runner=res.get("gatesRunner"), components=len(used), codeLen=len(res.get("code") or ""),
              tokensIn=tokens_in, tokensOut=tokens_out, ms=ctx.elapsed_ms())


ROUTES = {"screengen": handle, "screengen_components": components}
