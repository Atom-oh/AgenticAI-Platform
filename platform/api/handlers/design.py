"""디자인 스튜디오 — 상품명세서 기반 프로세스 생성 + 검수 루프 (설계: docs/specs/2026-09-04-design-studio-agentic-loop-design.md).

요청/응답형(액션명 = 응답 type):
  design_catalog {}                                  → {productSpecs[], smModels[], checklists[], source, badge}
  design_preview {productSpecId, smModelId?}         → {prd, checklist, counts}            (오프라인 — 모델 호출 없음)
  design_runs    {}                                  → {runs[]}                             (S3 index.json)
  design_run     {runId}                             → {run}                                (리포트 + 스텝 URL, HTML 은 URL 로)
  design_review  {runId, decision: approve|reject}   → {ok, run}
스트리밍형(kind = 'design'):
  design_flow    {productSpecId, smModelId?, outputType?, model?}
    design.stage(gate | prd | checklist | generate | review | test | regenerate | report | error) · design.token · design.done

실행 위치: AGENTS_RUNTIME_ARN 이 있으면 AgentCore Runtime 의 design_flow_agent(Strands 컨테이너, 같은 design_loop)를 호출하고
이벤트를 중계한다. 없으면 이 Lambda 안에서 같은 루프를 직접 돈다(배지 'lambda-local' 로 표기 — §11).
정본 자산은 Registry(APPROVED)이며, 비어 있으면 시드 파일로 폴백하고 source='seed-fallback' 을 표기한다.
산출물: S3(WEB_BUCKET) design-runs/<runId>/<step>.html + <runId>.json + index.json → CloudFront(WEB_URL) 로 서빙.
로그·트레이스에 프롬프트·HTML 원문은 남기지 않는다 (§12.5).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import tracing
from common.ctx import Ctx
from common.log import log_event

KIND = "design"
AGENT_NAME = "design_flow_agent"
RUNTIME_ARN = os.environ.get("AGENTS_RUNTIME_ARN", "").strip()
WEB_BUCKET = os.environ.get("WEB_BUCKET", "").strip()
WEB_URL = os.environ.get("WEB_URL", "").rstrip("/")
GEN_MODEL = os.environ.get("GEN_MODEL", "global.anthropic.claude-sonnet-5")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "").strip()
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "").strip()
PREFIX = "design-runs"
INDEX_KEY = f"{PREFIX}/index.json"
MAX_INDEX = 60
BADGE_RUNTIME = "AgentCore Runtime · Strands 컨테이너 · design_loop 공유 엔진"
BADGE_LOCAL = "데모 대체: Lambda 내 실행 (AgentCore Runtime 미배포) · 같은 design_loop"
BADGE_TEST = "테스트 에이전트: 결정론 rule 판정(플로우 맵·DOM·KWCAG 기본) — axe 브라우저 렌더링 미연동"

_s3 = None


def _s3c():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3")
    return _s3


# ---------- 자산 로드 (Registry → 시드 폴백) ----------
def _seed_dir() -> Optional[Path]:
    here = Path(__file__).resolve()
    for base in (here.parents[1], here.parents[2]):
        d = base / "seed" / "design"
        if (d / "product_specs.json").is_file():
            return d
    return None


def _seed_assets() -> Dict[str, list]:
    d = _seed_dir()
    if d is None:
        return {"productSpecs": [], "smModels": [], "checklists": []}
    load = lambda n: json.loads((d / n).read_text(encoding="utf-8"))  # noqa: E731
    return {"productSpecs": load("product_specs.json"), "smModels": [load("sm_model.json")], "checklists": load("checklists.json")}


def _registry_assets() -> Dict[str, list]:
    try:
        from registry import api as reg
        specs = [dict(r["payload"], _record=f"{r['name']} {r['recordVersion']}") for r in reg.list_approved("CUSTOM", "PRODUCT_SPEC")]
        sms = [dict(r["payload"], _record=f"{r['name']} {r['recordVersion']}") for r in reg.list_approved("CUSTOM", "SM_MODEL")]
        cls = [dict(r["payload"], _record=f"{r['name']} {r['recordVersion']}") for r in reg.list_approved("SKILL", "CHECKLIST")]
        return {"productSpecs": specs, "smModels": sms, "checklists": cls}
    except Exception as e:  # noqa: BLE001 — 테이블 없음/권한 등은 폴백으로
        log_event("design.registry_unavailable", "", error=f"{type(e).__name__}: {str(e)[:120]}")
        return {"productSpecs": [], "smModels": [], "checklists": []}


def load_assets() -> Dict[str, Any]:
    a = _registry_assets()
    if a["productSpecs"] and a["smModels"] and a["checklists"]:
        return {**a, "source": "registry"}
    return {**_seed_assets(), "source": "seed-fallback"}


def _find(items: list, key: str) -> Optional[dict]:
    return next((x for x in items if str(x.get("id")) == key), None)


def _spec_summary(s: dict) -> dict:
    return {"id": s.get("id"), "productName": s.get("productName"), "productType": s.get("productType"), "category": s.get("category"),
            "shape": s.get("shape"), "baseRate": s.get("baseRate"), "record": s.get("_record"),
            "conditions": len(s.get("preferentialConditions") or []),
            "inputConditions": sum(1 for c in s.get("preferentialConditions") or [] if (c.get("evidence") or {}).get("type") == "input"),
            "partners": [p.get("name") for p in s.get("partners") or []], "notices": len(s.get("mandatoryNotices") or [])}


def catalog(ctx: Ctx, body: dict) -> None:
    a = load_assets()
    use_runtime = bool(RUNTIME_ARN) and os.environ.get("DESIGN_USE_RUNTIME", "").strip() == "1"
    ctx.post({"type": "design_catalog", "source": a["source"],
              "badge": "정본: Registry APPROVED 자산" if a["source"] == "registry" else "데모 대체: 시드 파일 (Registry 미시드)",
              "productSpecs": [_spec_summary(s) for s in a["productSpecs"]],
              "smModels": [{"id": m.get("id"), "title": m.get("title"), "record": m.get("_record"),
                            "molecules": len(m.get("molecules") or []), "organisms": len(m.get("organisms") or []),
                            "templates": [t.get("id") for t in m.get("templates") or []]} for m in a["smModels"]],
              "checklists": [{"id": c.get("id"), "title": c.get("title"), "appliesTo": c.get("appliesTo") or {},
                              "items": len(c.get("items") or []), "record": c.get("_record")} for c in a["checklists"]],
              "runtime": "agentcore-runtime/strands" if use_runtime else "lambda-local",
              "runtimeBadge": BADGE_RUNTIME if use_runtime else BADGE_LOCAL, "testBadge": BADGE_TEST})


def _resolve(body: dict):
    a = load_assets()
    spec = _find(a["productSpecs"], str(body.get("productSpecId") or ""))
    sm = _find(a["smModels"], str(body.get("smModelId") or "")) or (a["smModels"][0] if a["smModels"] else None)
    return a, spec, sm


def preview(ctx: Ctx, body: dict) -> None:
    from design_loop import build_checklist, derive_prd
    from design_loop.models import SpecError
    a, spec, sm = _resolve(body)
    if spec is None or sm is None:
        ctx.post({"type": "design_preview", "error": "상품명세서 또는 SM 모델을 찾을 수 없습니다", "source": a["source"]})
        return
    try:
        prd = derive_prd(spec, sm)
    except SpecError as e:
        ctx.post({"type": "design_preview", "error": str(e), "code": "spec-incomplete", "missing": e.missing})
        return
    items = build_checklist(spec, a["checklists"])
    ctx.post({"type": "design_preview", "prd": prd, "checklist": items, "source": a["source"],
              "counts": {"steps": len(prd["steps"]), "branchSteps": len(prd.get("branchSteps") or []),
                         "items": len(items), "base": sum(1 for i in items if i["source"] == "base"),
                         "derived": sum(1 for i in items if i["source"] == "derived"),
                         "llm": sum(1 for i in items if i["method"] == "llm")}})


# ---------- 산출물 저장 (S3 → CloudFront) ----------
def _put(key: str, body: bytes, ctype: str) -> None:
    _s3c().put_object(Bucket=WEB_BUCKET, Key=key, Body=body, ContentType=ctype, CacheControl="no-cache")


def _get_json(key: str, default):
    try:
        return json.loads(_s3c().get_object(Bucket=WEB_BUCKET, Key=key)["Body"].read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — NoSuchKey 등
        return default


def _strip_html(result: dict) -> dict:
    flow = result.get("flow") or {}
    steps = [{k: v for k, v in s.items() if k != "html"} for s in flow.get("steps") or []]
    return {**result, "flow": {**flow, "steps": steps}}


def store_run(run_id: str, result: dict, meta: dict) -> dict:
    """스텝 HTML → design-runs/<runId>/<step>.html, 리포트 → <runId>.json, 목록 → index.json. WEB_BUCKET 없으면 저장 생략."""
    steps_out: List[dict] = []
    flow = result.get("flow") or {}
    for s in flow.get("steps") or []:
        key = f"{PREFIX}/{run_id}/{s['id']}.html"
        url = f"{WEB_URL}/{key}" if WEB_URL else key
        if WEB_BUCKET:
            _put(key, s.get("html", "").encode("utf-8"), "text/html; charset=utf-8")
        steps_out.append({"id": s["id"], "title": s.get("title"), "url": url, "htmlChars": len(s.get("html", ""))})
    run = {"runId": run_id, **meta, "createdAt": int(time.time() * 1000), "status": "검토중",
           "ok": result.get("ok"), "attempts": result.get("attempts"), "regenerated": result.get("regenerated"),
           "score": (result.get("report") or {}).get("score"), "openItems": (result.get("report") or {}).get("openItems"),
           "steps": steps_out, "branchSteps": (result.get("prd") or {}).get("branchSteps"),
           "transitions": (result.get("prd") or {}).get("transitions")}
    full = {**run, "prd": result.get("prd"), "checklist": result.get("checklist"), "report": result.get("report"),
            "flow": _strip_html(result).get("flow")}
    if WEB_BUCKET:
        _put(f"{PREFIX}/{run_id}.json", json.dumps(full, ensure_ascii=False).encode("utf-8"), "application/json")
        idx = _get_json(INDEX_KEY, {"runs": []})
        idx["runs"] = ([run] + [r for r in idx.get("runs", []) if r.get("runId") != run_id])[:MAX_INDEX]
        _put(INDEX_KEY, json.dumps(idx, ensure_ascii=False).encode("utf-8"), "application/json")
    return full


def runs(ctx: Ctx, body: dict) -> None:
    idx = _get_json(INDEX_KEY, {"runs": []}) if WEB_BUCKET else {"runs": []}
    ctx.post({"type": "design_runs", "runs": idx.get("runs", []), "stored": bool(WEB_BUCKET)})


def run_detail(ctx: Ctx, body: dict) -> None:
    rid = str(body.get("runId") or "").strip()
    if not rid or not WEB_BUCKET:
        ctx.post({"type": "design_run", "error": "runId 필요 또는 저장소 미구성"})
        return
    full = _get_json(f"{PREFIX}/{rid}.json", None)
    ctx.post({"type": "design_run", "run": full, "error": None if full else "없음"})


def review_decision(ctx: Ctx, body: dict) -> None:
    rid = str(body.get("runId") or "").strip()
    decision = str(body.get("decision") or "")
    if decision not in ("approve", "reject") or not rid or not WEB_BUCKET:
        ctx.post({"type": "design_review", "ok": False, "error": "runId/decision(approve|reject) 필요"})
        return
    status = "승인됨" if decision == "approve" else "반려"
    idx = _get_json(INDEX_KEY, {"runs": []})
    hit = None
    for r in idx.get("runs", []):
        if r.get("runId") == rid:
            r["status"], r["decidedBy"], r["decidedAt"] = status, tracing.hash8(ctx.email or ""), int(time.time() * 1000)
            hit = r
    if hit is None:
        ctx.post({"type": "design_review", "ok": False, "error": "없음"})
        return
    _put(INDEX_KEY, json.dumps(idx, ensure_ascii=False).encode("utf-8"), "application/json")
    full = _get_json(f"{PREFIX}/{rid}.json", None)
    if full:
        full["status"] = status
        _put(f"{PREFIX}/{rid}.json", json.dumps(full, ensure_ascii=False).encode("utf-8"), "application/json")
    log_event("design.review", ctx.trace_id, runId=rid, decision=decision, email=ctx.email)
    ctx.post({"type": "design_review", "ok": True, "run": hit,
              "badge": "데모: 스튜디오 승인 → S3 목록 상태 / 운영: 마스터 승인 → GitLab 푸시"})


# ---------- 실행 ----------
def _design_payload(spec: dict, sm: dict, checklists: list, output_type: str) -> dict:
    strip = lambda o: {k: v for k, v in o.items() if not k.startswith("_")}  # noqa: E731
    return {"productSpec": strip(spec), "smModel": strip(sm), "checklists": [strip(c) for c in checklists],
            "outputType": output_type}


def _relay_runtime(ctx: Ctx, design: dict, model: Optional[str]) -> tuple:
    """AgentCore Runtime 의 design_flow_agent 호출 → 이벤트 중계. returns (result, meta, errors)."""
    from agentcore import runtime
    result, meta, errors = None, {}, []
    for kind, data in runtime.invoke_stream(RUNTIME_ARN, AGENT_NAME, "", model=model, extra={"design": design}):
        if kind == "stage":
            d = dict(data)
            step = str(d.pop("step", "") or "stage")
            ctx.stage(KIND, step, plane="agentcore", **d)
        elif kind == "text":
            ctx.token(KIND, data)
        elif kind == "design_done":
            result = (data or {}).get("result")
        elif kind == "error":
            errors.append(str(data)[:400])
            ctx.stage(KIND, "error", message=str(data)[:400])
        elif kind == "meta":
            meta = data or {}
    return result, meta, errors


def _run_local(ctx: Ctx, design: dict, model: Optional[str]) -> tuple:
    from design_loop import run as loop_run
    from engine import gate
    # 게이트가 모델로 나가는 유일한 통과 지점 — 생성·판정 모두 경계 계측·PII 스캔을 지난다 (§3-2, §12.1)
    route = "claude"
    deps = gate.design_deps(route=route, trace_id=ctx.trace_id)

    def emit(ev: dict) -> None:
        if ev.get("type") == "stage":
            d = dict(ev)
            d.pop("type", None)
            step = str(d.pop("step", "") or "stage")
            ctx.stage(KIND, step, plane="cloud", **d)
        elif ev.get("type") == "token":
            ctx.token(KIND, ev.get("text", ""))

    result = loop_run(design["productSpec"], design["smModel"], design["checklists"], deps, emit=emit,
                      output_type=design.get("outputType") or "design")
    return result, {"usage": deps["usage"](), "modelId": model or GEN_MODEL, "runtime": "lambda-local"}, []


def flow(ctx: Ctx, body: dict) -> None:
    started = time.time()
    a, spec, sm = _resolve(body)
    output_type = str(body.get("outputType") or "design")
    model = str(body.get("model") or "").strip() or None
    if spec is None or sm is None:
        ctx.done(KIND, error="상품명세서 또는 SM 모델을 찾을 수 없습니다", source=a["source"])
        return
    design = _design_payload(spec, sm, a["checklists"], output_type)
    use_runtime = bool(RUNTIME_ARN) and os.environ.get("DESIGN_USE_RUNTIME", "").strip() == "1"
    runtime_label = "agentcore-runtime/strands" if use_runtime else "lambda-local"
    ctx.stage(KIND, "gate", ok=True, productSpec=spec.get("id"), productName=spec.get("productName"), smModel=sm.get("id"),
              checklists=[c.get("id") for c in a["checklists"]], source=a["source"], runtime=runtime_label,
              runtimeBadge=BADGE_RUNTIME if use_runtime else BADGE_LOCAL, testBadge=BADGE_TEST,
              maxRegenerations=1, plane="agentcore" if use_runtime else "cloud")
    try:
        if use_runtime:
            result, meta, errors = _relay_runtime(ctx, design, model)
            if result is None and not errors:
                errors.append("런타임이 결과(design_done)를 반환하지 않았습니다")
        else:
            result, meta, errors = _run_local(ctx, design, model)
    except Exception as e:  # noqa: BLE001
        log_event("design.flow_failed", ctx.trace_id, error=f"{type(e).__name__}: {str(e)[:200]}")
        ctx.done(KIND, error=f"실행 실패 — {type(e).__name__}: {str(e)[:200]}", runtime=runtime_label)
        return
    if not result or result.get("error"):
        ctx.done(KIND, error=(result or {}).get("error") or "; ".join(errors) or "실패", code=(result or {}).get("code"),
                 missing=(result or {}).get("missing"), runtime=runtime_label, attempts=(result or {}).get("attempts"))
        return
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    meta_out = {"productSpecId": spec.get("id"), "productName": spec.get("productName"), "shape": spec.get("shape"),
                "smModelId": sm.get("id"), "outputType": output_type, "modelId": meta.get("modelId") or model or GEN_MODEL,
                "runtime": meta.get("runtime") or runtime_label, "usage": meta.get("usage") or {},
                "source": a["source"], "by": tracing.hash8(ctx.email or "")}
    try:
        full = store_run(run_id, result, meta_out)
    except Exception as e:  # noqa: BLE001 — 저장 실패해도 결과는 돌려준다 (URL 없음)
        log_event("design.store_failed", ctx.trace_id, error=f"{type(e).__name__}: {str(e)[:200]}")
        full = {**_strip_html(result), "runId": run_id, **meta_out, "steps": [], "storeError": str(e)[:200]}
    rep = result.get("report") or {}
    tracing.record_trace({"traceId": ctx.trace_id, "scenario": "STUDIO", "email": ctx.email, "runId": run_id,
                          "productSpec": spec.get("id"), "attempts": result.get("attempts"), "regenerated": result.get("regenerated"),
                          "ok": result.get("ok"), **{f"items{k.capitalize()}": v for k, v in (rep.get("score") or {}).items()},
                          "usage": meta_out["usage"], "modelId": meta_out["modelId"], "runtime": meta_out["runtime"],
                          "elapsedMs": int((time.time() - started) * 1000)})
    ctx.done(KIND, runId=run_id, ok=result.get("ok"), attempts=result.get("attempts"), regenerated=result.get("regenerated"),
             prd=result.get("prd"), report=rep, steps=full.get("steps"), status=full.get("status"),
             usage=meta_out["usage"], modelId=meta_out["modelId"], runtime=meta_out["runtime"],
             runtimeBadge=BADGE_RUNTIME if use_runtime else BADGE_LOCAL, testBadge=BADGE_TEST, source=a["source"],
             errors=errors or None, storeError=full.get("storeError"))


ROUTES = {"design_catalog": catalog, "design_preview": preview, "design_flow": flow, "design_runs": runs,
          "design_run": run_detail, "design_review": review_decision}
