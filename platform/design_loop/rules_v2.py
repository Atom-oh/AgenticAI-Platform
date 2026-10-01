"""Deterministic checklist predicates on the composition model (engine plan, Task E16; review round 7, AA2).

The legacy `design_loop.rules` reads `flow.steps[*].html` and `prd.steps`, which the new model does not have, so it
would pass vacuously. Each base-set predicate is ported here to the new inputs:

- the flow `screens`/`transitions`, with step names resolved to screens by screen id or canonical page id
  (`Registry.page_id`);
- the page compositions, whose text is `composition.text_of` with **resolved** PRD bindings;
- the assembled Browser report for accessibility.

`$spec.<name>` arguments resolve against `prd_extract.bindings(prd)` (`product.<name>`). An unresolvable argument or
an empty target set is `incomplete`, never `pass`.

`ctx` = `{"flow", "screens": {(screen, state): composition}, "k", "binding_values", "registry", "report"}`.
"""
from __future__ import annotations

import re

from .composition import text_of, walk
from .contract import required_texts

_WS = re.compile(r"\s+")


def _verdict(verdict, evidence):
    return {"verdict": verdict, "evidence": evidence}


def _norm(text):
    return _WS.sub("", str(text or "")).lower()


class Unresolved(ValueError):
    pass


def resolve(value, binding_values):
    """`$spec.<name>` (string or list entries) -> the bound PRD values; anything unresolvable raises."""
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, str) and item.startswith("$spec."):
            path = "product." + item[len("$spec."):]
            if path not in binding_values:
                raise Unresolved(item)
            bound = binding_values[path]
            if isinstance(bound, list):
                out += [entry["value"] for entry in bound if isinstance(entry, dict) and isinstance(entry.get("value"), str)]
            elif isinstance(bound, str):
                out.append(bound)
            else:
                raise Unresolved(item)
        elif isinstance(item, str):
            out.append(item)
        else:
            raise Unresolved(repr(item))
    return [t for t in out if t.strip()]


def _screen_for(ctx, name):
    flow, registry = ctx["flow"], ctx.get("registry")
    for s in flow["screens"]:
        if s == name:
            return s
        if registry is not None:
            try:
                if registry.page_id(s) == name:
                    return s
            except KeyError:
                continue
    return None


def _page_text(ctx, screen):
    page = ctx["screens"].get((screen, "default"))
    if page is None:
        return None
    return text_of(page, ctx.get("binding_values") or {}, ctx["k"])


def step_exists(ctx, stepId=None, **_):  # noqa: N803 - base-set argument name
    if not isinstance(stepId, str) or not stepId:
        return _verdict("incomplete", "검사할 스텝 인자가 없음")
    screen = _screen_for(ctx, stepId)
    if screen is None:
        return _verdict("fail", f"스텝 '{stepId}' 없음")
    text = _page_text(ctx, screen)
    if text is None:
        return _verdict("fail", f"스텝 '{stepId}' 화면 구성이 없음")
    if not text.strip():
        return _verdict("fail", f"스텝 '{stepId}' 는 있으나 화면 내용이 비어 있음")
    return _verdict("pass", f"스텝 '{stepId}' 존재")


def steps_exist(ctx, stepIds=None, **_):  # noqa: N803
    ids = list(stepIds) if stepIds else list(ctx["flow"]["screens"])
    if not ids:
        return _verdict("incomplete", "검사할 스텝이 없음")
    missing = []
    for step in ids:
        screen = _screen_for(ctx, step)
        if screen is None or (screen, "default") not in ctx["screens"]:
            missing.append(str(step))
    if missing:
        return _verdict("fail", "누락 스텝: " + ", ".join(missing))
    return _verdict("pass", f"스텝 {len(ids)}개 모두 존재")


def text_present(ctx, texts=None, steps=None, any=False, **_):  # noqa: A002
    try:
        wanted = resolve(texts if texts is not None else [], ctx.get("binding_values") or {})
    except Unresolved as error:
        return _verdict("incomplete", f"해석할 수 없는 인자: {error}")
    if not wanted:
        return _verdict("incomplete", "검사 문구 없음")
    if steps:
        targets = [s for s in dict.fromkeys(_screen_for(ctx, step) for step in steps) if s is not None]
    else:
        targets = list(ctx["flow"]["screens"])
    if not targets:
        return _verdict("incomplete", "검사 대상 스텝이 플로우에 없음: " + ", ".join(map(str, steps or [])))
    found = {}
    for s in targets:
        body = _norm(_page_text(ctx, s) or "")
        for t in wanted:
            if _norm(t) in body:
                found.setdefault(t, []).append(s)
    ok = bool(found) if any else all(t in found for t in wanted)
    if ok:
        return _verdict("pass", "; ".join(f"'{t}' → {','.join(v)}" for t, v in found.items()))
    return _verdict("fail", "미표기: " + ", ".join(f"'{t}'" for t in wanted if t not in found))


def transitions_match_buttons(ctx, **_):
    flow = ctx["flow"]
    moves = [t for t in flow["transitions"] if t.get("navigation", "forward") == "forward"]
    if not moves:
        return _verdict("incomplete", "검사할 전이가 없음")
    missing = []
    for t in moves:
        page = ctx["screens"].get((t["src"], "default"))
        actions = {(n.get("on") or {}).get("click") for _, n, _ in walk(page)} if page else set()
        if not actions & {"next", t["id"]}:
            missing.append(t["id"])
    if missing:
        return _verdict("fail", "행동 버튼이 없는 전이: " + ", ".join(missing))
    return _verdict("pass", f"전이 {len(moves)}개 모두 출발 화면의 행동으로 존재")


def required_present(ctx, **_):
    k, values = ctx["k"], ctx.get("binding_values") or {}
    screens = [s for s in ctx["flow"]["screens"] if (s, "default") in ctx["screens"]]
    if not screens:
        return _verdict("incomplete", "검사 대상 화면이 없음")
    missing = []
    for s in screens:
        body = _norm(_page_text(ctx, s))
        missing += [f"{s}:{v}" for _, _, v in required_texts(values, k, s) if _norm(v) not in body]
    if missing:
        return _verdict("fail", "누락 필수 요소: " + ", ".join(missing))
    return _verdict("pass", "화면별 필수 요소가 모두 표시됨")


def _accessibility(ctx):
    report = ctx.get("report")
    if not isinstance(report, dict) or not isinstance(report.get("accessibility"), dict):
        return None
    return report["accessibility"]


def a11y_labels(ctx, **_):
    a11y = _accessibility(ctx)
    if a11y is None or a11y.get("incomplete"):
        return _verdict("incomplete", "브라우저 접근성 근거가 없음")
    violations = a11y.get("violations") or []
    if a11y.get("status") != "pass" or violations:
        return _verdict("fail", "접근성 위반: " + ", ".join(str(v.get("id", v)) if isinstance(v, dict) else str(v)
                                                           for v in violations))
    return _verdict("pass", "axe wcag2a/aa 검사 통과(라벨·대체 텍스트 포함)")


def a11y_structure(ctx, **_):
    a11y = _accessibility(ctx)
    if a11y is None or a11y.get("incomplete"):
        return _verdict("incomplete", "브라우저 접근성 근거가 없음")
    k, problems = ctx["k"], []
    if not ctx["screens"]:
        return _verdict("incomplete", "검사 대상 화면이 없음")
    for (s, state), page in ctx["screens"].items():
        code = (k.templates.get(page.get("templateId")) or {}).get("code") or {}
        if code.get("exportName") != "Screen" or not (k.screens.get(s) or {}).get("title", "").strip():
            problems.append(f"{s}:{state}: h1 제목 없음")
    if a11y.get("status") != "pass":
        problems.append("lang 등 문서 구조 접근성 검사 실패")
    if problems:
        return _verdict("fail", "; ".join(problems))
    return _verdict("pass", "모든 화면이 Screen 제목(h1)으로 시작하고 문서 lang 검사를 통과")


RULES = {
    "step_exists": step_exists, "steps_exist": steps_exist, "text_present": text_present,
    "transitions_match_buttons": transitions_match_buttons, "required_present": required_present,
    "a11y_labels": a11y_labels, "a11y_structure": a11y_structure,
}


def run_rule(rule, ctx):
    fn = RULES.get(str((rule or {}).get("fn") or ""))
    if fn is None:
        return _verdict("incomplete", f"알 수 없는 판정 함수: {(rule or {}).get('fn')}")
    try:
        return fn(ctx, **((rule or {}).get("args") or {}))
    except Exception as error:  # noqa: BLE001 - a predicate error is never a pass
        return _verdict("incomplete", f"판정 오류: {type(error).__name__}")
