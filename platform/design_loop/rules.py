"""rule 판정기 — 플로우 맵·DOM(정규식 기반 텍스트 추출) 검사. 결정론, 외부 의존 없음.

각 함수: (flow, prd, **args) -> {"verdict": pass|fail|incomplete, "evidence": str}
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Dict, List

from .models import step_by_id

_TAG = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def visible_text(html: str) -> str:
    t = _TAG.sub(" ", html or "")
    t = _TAGS.sub(" ", t)
    return _WS.sub(" ", _html.unescape(t)).strip()


def _norm(s: str) -> str:
    return _WS.sub("", str(s or "")).lower()


def _steps(flow: Dict[str, Any], steps: List[str] | None) -> List[dict]:
    all_steps = flow.get("steps") or []
    if not steps:
        return all_steps
    return [s for s in all_steps if str(s.get("id")) in set(steps)]


def step_exists(flow, prd, stepId: str, **_) -> Dict[str, str]:
    s = step_by_id(flow, stepId)
    if s is None:
        return {"verdict": "fail", "evidence": f"스텝 '{stepId}' 없음 — 현재 스텝: " + ", ".join(str(x.get("id")) for x in flow.get("steps") or [])}
    if len(visible_text(s.get("html", ""))) < 20:
        return {"verdict": "fail", "evidence": f"스텝 '{stepId}' 는 있으나 화면 내용이 비어 있음"}
    return {"verdict": "pass", "evidence": f"스텝 '{stepId}' ({s.get('title')}) 존재"}


def steps_exist(flow, prd, stepIds: List[str] | None = None, **_) -> Dict[str, str]:
    ids = stepIds or [s["id"] for s in prd.get("steps") or []]
    missing = [i for i in ids if step_by_id(flow, i) is None]
    if missing:
        return {"verdict": "fail", "evidence": "누락 스텝: " + ", ".join(missing)}
    return {"verdict": "pass", "evidence": f"PRD 스텝 {len(ids)}개 모두 존재"}


def text_present(flow, prd, texts: List[str], steps: List[str] | None = None, any: bool = False, **_) -> Dict[str, str]:  # noqa: A002
    targets = _steps(flow, steps)
    if not targets:
        return {"verdict": "fail", "evidence": "검사 대상 스텝이 플로우에 없음: " + ", ".join(steps or [])}
    texts = [t for t in (texts or []) if str(t).strip()]
    if not texts:
        return {"verdict": "incomplete", "evidence": "검사 문구 없음"}
    found: Dict[str, List[str]] = {}
    for s in targets:
        body = _norm(visible_text(s.get("html", "")))
        for t in texts:
            if _norm(t) in body:
                found.setdefault(t, []).append(str(s.get("id")))
    if any:
        ok = bool(found)
    else:
        ok = all(t in found for t in texts)
    missing = [t for t in texts if t not in found]
    if ok:
        return {"verdict": "pass", "evidence": "; ".join(f"'{t}' → {','.join(v)}" for t, v in found.items())}
    return {"verdict": "fail", "evidence": "미표기: " + ", ".join(f"'{m}'" for m in missing) +
            (f" (대상 스텝: {', '.join(steps)})" if steps else "")}


_BTN = re.compile(r"<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>", re.S | re.I)
_INPUT_BTN = re.compile(r"<input\b[^>]*type=[\"']?(?:submit|button)[\"']?[^>]*value=[\"']([^\"']*)", re.I)


def _button_texts(html: str) -> List[str]:
    out = [_norm(visible_text(m)) for m in _BTN.findall(html or "")]
    out += [_norm(v) for v in _INPUT_BTN.findall(html or "")]
    return [o for o in out if o]


def transitions_match_buttons(flow, prd, **_) -> Dict[str, str]:
    """PRD 의 모든 전이(trigger)가 출발 스텝 화면의 버튼/링크 문구로 존재해야 한다."""
    missing = []
    for tr in prd.get("transitions") or []:
        s = step_by_id(flow, str(tr.get("from")))
        if s is None:
            missing.append(f"{tr.get('from')}(스텝 없음)")
            continue
        trig = _norm(tr.get("trigger") or "")
        if not trig:
            continue
        if not any(trig in b for b in _button_texts(s.get("html", ""))):
            missing.append(f"{tr.get('from')}→{tr.get('to')} '{tr.get('trigger')}'")
    if missing:
        return {"verdict": "fail", "evidence": "버튼 없는 전이: " + "; ".join(missing)}
    return {"verdict": "pass", "evidence": f"전이 {len(prd.get('transitions') or [])}건 모두 버튼과 매칭"}


def required_present(flow, prd, **_) -> Dict[str, str]:
    """PRD 스텝별 required 문구가 해당 화면에 있어야 한다."""
    missing = []
    for ps in prd.get("steps") or []:
        s = step_by_id(flow, ps["id"])
        if s is None:
            missing.append(f"{ps['id']}: 스텝 없음")
            continue
        body = _norm(visible_text(s.get("html", "")))
        lost = [r for r in ps.get("required") or [] if _norm(r) not in body]
        if lost:
            missing.append(f"{ps['id']}: " + ", ".join(f"'{x}'" for x in lost))
    if missing:
        return {"verdict": "fail", "evidence": "필수 요소 누락 — " + " | ".join(missing)}
    return {"verdict": "pass", "evidence": "스텝별 필수 요소 모두 존재"}


_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ALT = re.compile(r"\balt\s*=", re.I)
_INPUT = re.compile(r"<(input|select|textarea)\b[^>]*>", re.I)
_ID = re.compile(r"\bid\s*=\s*[\"']([^\"']+)[\"']", re.I)
_ARIA = re.compile(r"\baria-label(?:ledby)?\s*=", re.I)
_HIDDEN = re.compile(r"type\s*=\s*[\"']?(hidden|submit|button)", re.I)
_LABEL_FOR = re.compile(r"<label\b[^>]*for\s*=\s*[\"']([^\"']+)[\"']", re.I)


def a11y_labels(flow, prd, **_) -> Dict[str, str]:
    probs = []
    for s in flow.get("steps") or []:
        h = s.get("html", "")
        imgs = [m for m in _IMG.findall(h) if not _ALT.search(m)]
        fors = set(_LABEL_FOR.findall(h))
        inputs = []
        for m in _INPUT.finditer(h):
            tag = m.group(0)
            if _HIDDEN.search(tag) or _ARIA.search(tag):
                continue
            mid = _ID.search(tag)
            if not mid or mid.group(1) not in fors:
                inputs.append(tag[:40])
        if imgs or inputs:
            probs.append(f"{s.get('id')}: alt 없는 img {len(imgs)}, 라벨 없는 입력 {len(inputs)}")
    if probs:
        return {"verdict": "fail", "evidence": "; ".join(probs)}
    return {"verdict": "pass", "evidence": "img alt · 입력 라벨 연결 확인"}


_LANG = re.compile(r"<html\b[^>]*\blang\s*=", re.I)
_H1 = re.compile(r"<h1\b", re.I)


def a11y_structure(flow, prd, **_) -> Dict[str, str]:
    probs = []
    for s in flow.get("steps") or []:
        h = s.get("html", "")
        if not _LANG.search(h):
            probs.append(f"{s.get('id')}: <html lang> 없음")
        if not _H1.search(h):
            probs.append(f"{s.get('id')}: h1 없음")
    if probs:
        return {"verdict": "fail", "evidence": "; ".join(probs)}
    return {"verdict": "pass", "evidence": "lang 속성·h1 존재"}


RULES = {
    "step_exists": step_exists, "steps_exist": steps_exist, "text_present": text_present,
    "transitions_match_buttons": transitions_match_buttons, "required_present": required_present,
    "a11y_labels": a11y_labels, "a11y_structure": a11y_structure,
}


def run_rule(flow: Dict[str, Any], prd: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, str]:
    fn = RULES.get(str((rule or {}).get("fn") or ""))
    if fn is None:
        return {"verdict": "incomplete", "evidence": f"알 수 없는 판정 함수: {(rule or {}).get('fn')}"}
    try:
        return fn(flow, prd, **((rule or {}).get("args") or {}))
    except Exception as e:  # noqa: BLE001 — 판정기 오류는 미판정으로 남기고 루프는 계속
        return {"verdict": "incomplete", "evidence": f"판정 오류: {type(e).__name__}: {str(e)[:120]}"}
