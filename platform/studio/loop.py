# platform/studio/loop.py
"""에이전틱 루프 — DesignSpec → 생성 → 검수(결정적 + 리뷰어) → 수정 재생성. 점수 기준 반복, 상한·시간 캡.

의존성은 전부 주입된다(테스트 오프라인). 워커(worker_handler)가 engine.bedrock 을 꽂는다.
원칙: 미판정은 pass 가 아니다 · 최종 시안은 최고 점수 라운드 · 프롬프트/HTML 원문은 로그에 넣지 않는다.
"""
from __future__ import annotations

import os
import time

from studio import prompts, review, sanitize

MAX_ROUNDS = 20
DEFAULT_ROUNDS = 3
DEFAULT_PASS = 85
TIME_CAP_S = 780
GEN_MAX_TOKENS = 12000
REVIEW_MAX_TOKENS = 2500
MODEL = os.environ.get("GEN_MODEL", "global.anthropic.claude-sonnet-5")
# .done 프레임 예산 — API Gateway WebSocket 프레임(128KB)을 넘기면 마지막 프레임이 통째로 사라진다.
DONE_FAILURES_PER_ROUND = 5
DONE_TEXT_MAX = 160
DONE_UNDETERMINED_MAX = 20


class ListEmitter:
    def __init__(self) -> None:
        self.stages: list = []
        self.tokens: list = []

    def stage(self, step: str, **kw) -> None:
        self.stages.append({"step": step, **kw})

    def token(self, text: str) -> None:
        self.tokens.append(text)


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def clamp_job(job: dict) -> dict:
    j = dict(job or {})
    j["brief"] = str(j.get("brief", ""))[:1000]
    j["mode"] = "refine" if j.get("mode") == "refine" else "generate"
    j["maxRounds"] = _int(j.get("maxRounds"), 1 if j["mode"] == "refine" else DEFAULT_ROUNDS, 1, MAX_ROUNDS)
    j["passScore"] = _int(j.get("passScore"), DEFAULT_PASS, 50, 100)
    j["outputType"] = j.get("outputType") if j.get("outputType") in prompts.OUTPUT_TYPES else "design"
    j["axis"] = j.get("axis") if j.get("axis") in prompts.AXES else "흐름"
    for k in ("selector", "elementHtml", "instruction", "productCode", "jobId", "baseDraftId", "agentId"):
        j[k] = str(j.get(k) or "")
    j["elementHtml"] = j["elementHtml"][:4000]
    j["assetIds"] = [str(a) for a in (j.get("assetIds") or [])][:20]
    return j


def _items_with_verdicts(items: list, verdicts: dict) -> list:
    out = []
    for it in items:
        v = verdicts.get(it["id"])
        out.append({**{k: it.get(k) for k in ("id", "category", "text", "required", "weight", "check", "source")},
                    "verdict": v["verdict"] if v else None, "evidence": (v or {}).get("evidence", ""), "fix": (v or {}).get("fix", "")})
    return out


def _review_round(spec: dict, items: list, html: str, prev_html: str, review_generate, pass_score: int, stable_weight: int = 0) -> dict:
    doc = review.parse_html(html)
    verdicts = review.deterministic_checks(items, doc)
    items = list(items)
    if prev_html:
        st_item, st_v = review.stability_item(review.skeleton_diff(prev_html, html), weight=stable_weight)
        items.append(st_item)
        verdicts[st_item["id"]] = st_v
    llm_items = [i for i in items if i.get("check") == "llm"]
    reviewer_error, usage, scrubbed = None, {}, 0
    if llm_items:
        digest_safe, scrubbed = sanitize.scrub_result(review.dom_digest(doc))
        review_prompt, n_review = sanitize.scrub_result(prompts.build_review_prompt(spec, llm_items, digest_safe))
        scrubbed += n_review
        try:
            text, usage = review_generate(prompts.REVIEW_SYSTEM, review_prompt, REVIEW_MAX_TOKENS)
            llm_v, reviewer_error = review.parse_review(text, [i["id"] for i in llm_items])
        except Exception as e:  # noqa: BLE001 — 리뷰어 실패는 미판정으로 남긴다
            llm_v, reviewer_error = {i["id"]: None for i in llm_items}, f"{type(e).__name__}: {str(e)[:120]}"
        verdicts.update(llm_v)
    sc = review.score(items, verdicts, pass_score)
    rows = _items_with_verdicts(items, verdicts)
    failures = [{"id": r["id"], "text": r["text"], "verdict": r["verdict"], "evidence": r["evidence"], "fix": r["fix"]} for r in rows if r["verdict"] != "pass"]
    return {**sc, "items": rows, "failures": failures, "reviewerError": reviewer_error, "usage": usage or {},
            "deterministic": sum(1 for i in items if i.get("check") in ("text", "dom")), "llm": len(llm_items), "scrubbed": scrubbed}


def _done_items(items: list) -> list:
    """항목은 전부 남기고 근거·수정 지시만 자른다 — 판정 수치는 그대로 유지된다."""
    return [{**it, "evidence": str(it.get("evidence") or "")[:DONE_TEXT_MAX], "fix": str(it.get("fix") or "")[:DONE_TEXT_MAX]}
            for it in items]


def _done_history(history: list) -> list:
    """라운드 기록 축약 — 실패 항목은 라운드당 상위 5건의 (id, text, verdict) 만. 전체 건수는 failuresTotal 로 사실대로 남긴다."""
    out = []
    for h in history:
        rec = {k: v for k, v in h.items() if k not in ("html", "items", "failures", "undetermined")}
        fails = h.get("failures") or []
        rec["failures"] = [{"id": f.get("id"), "text": f.get("text"), "verdict": f.get("verdict")} for f in fails[:DONE_FAILURES_PER_ROUND]]
        rec["failuresTotal"] = len(fails)
        und = h.get("undetermined") or []
        rec["undetermined"] = und[:DONE_UNDETERMINED_MAX]
        rec["undeterminedTotal"] = len(und)
        out.append(rec)
    return out


def run(job: dict, emitter, *, spec: dict, generate, review_generate, publish, skills=None, assets_text: str = "",
        fewshot=None, agent_preset: str = "", base_html: str = "", clock=time.time, time_cap_s: int = TIME_CAP_S) -> dict:
    t0 = clock()
    job = clamp_job(job)
    items = list(spec["items"])
    emitter.stage("spec_build", items=len(items), required=sum(1 for i in items if i.get("required")),
                  productName=spec.get("productName"), productCode=spec.get("productCode"),
                  hasPreferential=spec.get("hasPreferential"), steps=[s["name"] for s in spec.get("steps", [])], plane="cloud")
    skills = skills if skills is not None else prompts.load_skills()[0]
    system = prompts.build_system_prompt(spec, job["outputType"], job["axis"], skills, assets_text, fewshot, agent_preset)
    system, n_sys = sanitize.scrub_result(system)
    emitter.stage("assets", chars=len(assets_text), fewshot=len(fewshot or []), skills=list(skills), plane="cloud",
                  scrubbedSystem=n_sys)

    usage = {"inputTokens": 0, "outputTokens": 0}
    history: list = []
    failures: list = []
    prev_html = base_html if job["mode"] == "refine" else ""
    stop, error = "max_rounds", None
    model_used, route_used = "", ""  # 실측(engine.bedrock.Stream 이 노출) — 없으면 설정값 MODEL 로 보고한다
    rnd = 0
    while rnd < job["maxRounds"]:
        if rnd > 0 and clock() - t0 > time_cap_s:
            stop = "time_cap"
            break
        rnd += 1
        r_t0 = clock()
        if rnd > 1:
            emitter.stage("regenerate", round=rnd, failures=failures[:12], plane="cloud")
        refine = {"selector": job["selector"], "elementHtml": job["elementHtml"], "instruction": job["instruction"]} if job["mode"] == "refine" else None
        prev_html_safe, n_scrub = sanitize.scrub_result(prev_html)
        if refine:
            elem_safe, n_elem = sanitize.scrub_result(refine["elementHtml"])
            refine = {**refine, "elementHtml": elem_safe}
            n_scrub += n_elem
        user = prompts.build_user_prompt(job["brief"], spec, failures=failures if rnd > 1 else None, prev_html=prev_html_safe, refine=refine)
        user, n_user = sanitize.scrub_result(user)
        n_scrub += n_user
        emitter.stage("generate", round=rnd, systemChars=len(system), userChars=len(user), model=MODEL, plane="cloud", scrubbed=n_scrub)
        try:
            stream = generate(system, user, GEN_MAX_TOKENS)
            parts = []
            for tk in stream:
                parts.append(tk)
                emitter.token(tk)
            u = getattr(stream, "usage", None) or {}
            if not model_used:
                model_used = str(getattr(stream, "model_id", None) or "")
                route_used = str(getattr(stream, "route", None) or "")
        except Exception as e:  # noqa: BLE001 — 게이트 거부 등: 라운드 중단, 사실대로 보고
            stop, error = "error", f"{type(e).__name__}: {str(e)[:200]}"
            history.append({"round": rnd, "score": 0, "passed": False, "url": "", "failures": [{"id": "GENERATE", "text": "생성 실패", "evidence": error, "fix": ""}],
                            "undetermined": [], "reviewerError": None, "elapsedMs": int((clock() - r_t0) * 1000), "html": "", "items": []})
            break
        usage["inputTokens"] += int(u.get("inputTokens", 0) or 0)
        usage["outputTokens"] += int(u.get("outputTokens", 0) or 0)
        html = prompts.extract_html("".join(parts))
        if not html:
            rv = {"score": 0, "passed": False, "requiredFailed": [i["id"] for i in items if i.get("required")], "undetermined": [],
                  "items": _items_with_verdicts(items, {}), "failures": [{"id": "OUTPUT", "text": "HTML 문서를 찾지 못함", "evidence": "```html 펜스 없음", "fix": "자기완결 HTML 1개를 ```html 펜스 안에 출력"}],
                  "reviewerError": None, "usage": {}, "deterministic": 0, "llm": 0}
        else:
            rv = _review_round(spec, items, html, prev_html, review_generate, job["passScore"],
                                stable_weight=1 if job["mode"] == "refine" else 0)
            usage["inputTokens"] += int(rv["usage"].get("inputTokens", 0) or 0)
            usage["outputTokens"] += int(rv["usage"].get("outputTokens", 0) or 0)
        emitter.stage("review", round=rnd, score=rv["score"], passed=rv["passed"], items=rv["items"], requiredFailed=rv["requiredFailed"],
                      undetermined=rv["undetermined"], reviewerError=rv["reviewerError"], deterministic=rv["deterministic"], llm=rv["llm"],
                      scrubbed=rv.get("scrubbed", 0), plane="cloud")
        url = publish(job["jobId"], rnd, html) if html else ""
        if url:
            emitter.stage("publish", round=rnd, url=url, score=rv["score"], plane="cloud")
        history.append({"round": rnd, "score": rv["score"], "passed": rv["passed"], "url": url, "failures": rv["failures"],
                        "undetermined": rv["undetermined"], "reviewerError": rv["reviewerError"],
                        "elapsedMs": int((clock() - r_t0) * 1000), "html": html, "items": rv["items"]})
        failures = rv["failures"]
        if rv["passed"]:
            stop = "passed"
            break
        prev_html = html or prev_html

    best = max(history, key=lambda h: (h["score"], bool(h["html"]), h["round"])) if history else None
    out = {"jobId": job["jobId"], "mode": job["mode"], "outputType": job["outputType"], "axis": job["axis"],
           "score": best["score"] if best else 0, "passed": bool(best and best["passed"]), "rounds": rnd,
           "maxRounds": job["maxRounds"], "passScore": job["passScore"], "stopReason": stop,
           "bestRound": best["round"] if best else 0, "url": best["url"] if best else "", "html": best["html"] if best else "",
           "items": _done_items(best.get("items", []) if best else []), "history": _done_history(history),
           "usage": usage, "model": model_used or MODEL, "route": route_used, "elapsedMs": int((clock() - t0) * 1000),
           "spec": {"productCode": spec.get("productCode"), "productName": spec.get("productName"),
                    "hasPreferential": spec.get("hasPreferential"), "stepCount": len(spec.get("steps", []))}}
    if error:
        out["error"] = error
    return out
