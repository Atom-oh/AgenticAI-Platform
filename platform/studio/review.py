# platform/studio/review.py
"""검수 — 결정적 검사(text/dom)·DOM 다이제스트·골격 diff·리뷰어 JSON 파싱·점수. 모델 호출 없음(stdlib만)."""
from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser

_WS = re.compile(r"\s+")
_FETCH = re.compile(r"\bfetch\s*\(|XMLHttpRequest|navigator\.sendBeacon")
# 닫는 태그가 없는 태그 — _stack 에 쌓으면 이후 경로가 전부 밀려 골격 diff 가 왜곡된다.
_VOID = {"input", "br", "img", "meta", "link", "hr", "source", "wbr"}


def _norm(s: str) -> str:
    return _WS.sub("", (s or "").lower())


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.steps: list[dict] = []
        self.cur: dict | None = None
        self.style: list[str] = []
        self.text: list[str] = []
        self.external_scripts = 0
        self.checkboxes = 0
        self.inputs = 0
        self._stack: list[str] = []
        self._in_style = False
        self._in_script = False
        self._heading: str | None = None
        self._button = False
        self._label = False
        self.paths: Counter = Counter()
        self.script_text: list[str] = []
        self._section_is_step: list[bool] = []
        self._step_stack: list[dict | None] = []

    def _step(self) -> dict:
        """섹션 밖 콘텐츠를 담을 임시 프레임 — 명시 <section data-step> 이 하나라도 있으면 parse_html 이 버린다."""
        if self.cur is None:
            self.cur = {"index": len(self.steps) + 1, "screen": "", "text": [], "headings": [], "buttons": [], "inputs": [], "labels": [],
                        "_implicit": True}
            self.steps.append(self.cur)
        return self.cur

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.paths[">".join((self._stack + [tag])[-3:])] += 1
        if tag not in _VOID:
            self._stack.append(tag)
        if tag == "section":
            is_step = "data-step" in a
            self._section_is_step.append(is_step)
            if is_step:
                self._step_stack.append(self.cur)
                self.cur = {"index": len(self.steps) + 1, "screen": a.get("data-screen", ""), "text": [], "headings": [], "buttons": [], "inputs": [],
                            "labels": [], "_implicit": False}
                self.steps.append(self.cur)
                return
        if tag == "style":
            self._in_style = True
        elif tag == "script":
            self._in_script = True
            if a.get("src"):
                self.external_scripts += 1
        elif tag in ("h1", "h2", "h3"):
            self._heading = ""
        elif tag == "button" or (tag == "a" and "cta" in (a.get("class") or "")):
            self._button = True
            self._step()["buttons"].append("")
        elif tag == "label":
            self._label = True
            self._step()["labels"].append("")
        elif tag in ("input", "select", "textarea"):
            t = (a.get("type") or tag).lower()
            self.inputs += 1
            if t == "checkbox" or a.get("role") == "checkbox":
                self.checkboxes += 1
            self._step()["inputs"].append({"type": t, "placeholder": a.get("placeholder", "")})
        if a.get("onclick") and _FETCH.search(a["onclick"]):
            self.script_text.append(a["onclick"])

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag == "section" and self._section_is_step:
            was_step = self._section_is_step.pop()
            if was_step:
                self.cur = self._step_stack.pop() if self._step_stack else None
        if tag == "style":
            self._in_style = False
        elif tag == "script":
            self._in_script = False
        elif tag in ("h1", "h2", "h3") and self._heading is not None:
            self._step()["headings"].append(_WS.sub(" ", self._heading).strip())
            self._heading = None
        elif tag == "button":
            self._button = False
        elif tag == "label":
            self._label = False

    def handle_data(self, data):
        if self._in_style:
            self.style.append(data)
            return
        if self._in_script:
            self.script_text.append(data)
            return
        t = _WS.sub(" ", data).strip()
        if not t:
            return
        self.text.append(t)
        st = self._step()
        st["text"].append(t)
        if self._heading is not None:
            self._heading += t
        if self._button and st["buttons"]:
            st["buttons"][-1] = (st["buttons"][-1] + " " + t).strip()
        if self._label and st["labels"]:
            st["labels"][-1] = (st["labels"][-1] + " " + t).strip()


def parse_html(html: str) -> dict:
    """명시 <section data-step> 이 하나라도 있으면 프레임은 그것만이다 — 헤더·푸터 같은 섹션 밖 텍스트는
    doc["text"] 로만 남고 프레임 수(minSteps/maxSteps)를 부풀리지 않는다. 명시 섹션이 없으면 암시 프레임 1개."""
    p = _Parser()
    p.feed(html or "")
    steps = [s for s in p.steps if not s.get("_implicit")]
    if not steps:
        steps = p.steps[:1] or [{"index": 1, "screen": "", "text": [], "headings": [], "buttons": [], "inputs": [], "labels": []}]
    for i, s in enumerate(steps, start=1):
        s["index"] = i
        s.pop("_implicit", None)
        s["text"] = " ".join(s["text"])
    return {"steps": steps, "text": " ".join(p.text), "style": " ".join(p.style),
            "externalScripts": p.external_scripts, "hasFetch": bool(_FETCH.search(" ".join(p.script_text))),
            "checkboxes": p.checkboxes, "inputs": p.inputs, "_paths": p.paths}


def dom_digest(doc: dict, limit: int = 6000) -> str:
    out = []
    for s in doc["steps"]:
        head = f"[프레임 {s['index']}{' ' + s['screen'] if s['screen'] else ''}] " + " / ".join(s["headings"]) if s["headings"] else f"[프레임 {s['index']}]"
        out.append(head)
        if s["labels"]:
            out.append("  레이블: " + ", ".join(x for x in s["labels"] if x)[:300])
        if s["inputs"]:
            out.append("  입력: " + ", ".join(f"{i['type']}({i['placeholder']})" if i["placeholder"] else i["type"] for i in s["inputs"])[:300])
        if s["buttons"]:
            out.append("  버튼: " + ", ".join(x for x in s["buttons"] if x)[:300])
        out.append("  본문: " + s["text"][:700])
    return "\n".join(out)[:limit]


def _fuzzy_in(needle: str, hay: str) -> bool:
    n, h = _norm(needle), _norm(hay)
    if not n:
        return False
    if n in h:
        return True
    toks = [t for t in re.split(r"[\s·,()/]+", needle) if len(t) >= 2]
    return bool(toks) and sum(_norm(t) in h for t in toks) >= max(1, len(toks) // 2 + 1)


def _check_dom(expect: dict, doc: dict) -> tuple[bool, str, str]:
    steps = doc["steps"]
    if "stepContainsAny" in expect:
        for kw in expect["stepContainsAny"]:
            for s in steps:
                if _fuzzy_in(kw, " ".join(s["headings"]) + " " + s["text"]):
                    return True, f"{s['index']}단계에 '{kw}' 존재", ""
        return False, "조건 입력 스텝을 찾지 못함", f"'{expect['stepContainsAny'][0]}' 입력·인증을 위한 별도 <section data-step> 프레임을 추가"
    if "stepOrder" in expect:
        names, pos, last = expect["stepOrder"], [], -1
        for nm in names:
            found = next((s["index"] for s in steps if s["index"] > last and _fuzzy_in(nm, " ".join(s["headings"]) + " " + s["text"][:120])), None)
            if found is None:
                return False, f"'{nm}' 단계가 순서상 위치에 없음 (이전 단계 {last})", f"프레임 순서를 {' → '.join(names)} 로 맞출 것"
            pos.append(found)
            last = found
        return True, f"단계 순서 일치: {pos}", ""
    if "minSteps" in expect:
        ok = len(steps) >= expect["minSteps"]
        return ok, f"프레임 {len(steps)}개", "" if ok else f"프레임을 {expect['minSteps']}개 이상으로 (각 단계마다 <section data-step>)"
    if "maxSteps" in expect:
        ok = len(steps) <= expect["maxSteps"]
        return ok, f"프레임 {len(steps)}개", "" if ok else f"절차에 없는 추가 스텝 제거 — {expect['maxSteps']}개까지"
    if "control" in expect:
        if expect["control"] == "checkbox":
            ok = doc["checkboxes"] > 0
            return ok, f"체크박스 {doc['checkboxes']}개", "" if ok else "동의 항목을 <input type=checkbox> 로 표현"
        ok = doc["inputs"] > 0
        return ok, f"입력 {doc['inputs']}개", "" if ok else "입력 컨트롤 추가"
    if "brandHex" in expect:
        hexes = expect["brandHex"] if isinstance(expect["brandHex"], list) else ["#008485"]
        css = doc["style"].lower()
        used = [h for h in hexes if h.lower() in css]
        return bool(used), f"브랜드 색 사용: {used}" if used else "브랜드 색 미사용", "" if used else "주 강조색을 #008485 로"
    if "noExternal" in expect:
        ok = doc["externalScripts"] == 0 and not doc["hasFetch"]
        return ok, f"외부 스크립트 {doc['externalScripts']}, fetch {'있음' if doc['hasFetch'] else '없음'}", "" if ok else "외부 <script src>·fetch 제거, 인라인만"
    return False, f"알 수 없는 expect: {list(expect)}", ""


def deterministic_checks(items: list[dict], doc: dict) -> dict[str, dict]:
    out = {}
    for it in items:
        chk, exp = it.get("check"), it.get("expect") or {}
        if chk == "text":
            ok = _fuzzy_in(exp.get("contains", ""), doc["text"])
            out[it["id"]] = {"verdict": "pass" if ok else "fail", "evidence": f"'{exp.get('contains')}' {'존재' if ok else '없음'}",
                             "fix": "" if ok else f"문구에 '{exp.get('contains')}' 표준 용어 사용"}
        elif chk == "dom":
            ok, ev, fix = _check_dom(exp, doc)
            out[it["id"]] = {"verdict": "pass" if ok else "fail", "evidence": ev, "fix": fix}
    return out


def skeleton(html: str) -> Counter:
    return parse_html(html)["_paths"]


def skeleton_diff(prev_html: str, html: str) -> float:
    a, b = skeleton(prev_html), skeleton(html)
    total = sum(a.values()) + sum(b.values())
    if total == 0:
        return 0.0
    changed = sum(((a - b) + (b - a)).values())
    return round(changed / total, 3)


def stability_item(ratio: float, threshold: float = 0.35, weight: int = 1) -> tuple[dict, dict]:
    item = {"id": "STABLE", "category": "안정성", "text": "수정 범위 밖의 구조가 이전 라운드와 같다 (위치가 흔들리지 않는다)",
            "required": False, "weight": weight, "check": "dom", "expect": {"skeletonMax": threshold}, "source": None}
    ok = ratio <= threshold
    return item, {"verdict": "pass" if ok else "fail", "evidence": f"구조 변경 비율 {ratio:.0%}",
                  "fix": "" if ok else "지시된 항목만 고치고 나머지 마크업·순서는 그대로 유지"}


def parse_review(text: str, item_ids: list[str]) -> tuple[dict, str | None]:
    verdicts: dict = {i: None for i in item_ids}
    s = text or ""
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return verdicts, "리뷰어 응답에 JSON 없음"
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        return verdicts, f"리뷰어 JSON 파싱 실패: {str(e)[:80]}"
    err = None
    for row in data.get("items", []) if isinstance(data, dict) else []:
        iid = row.get("id") if isinstance(row, dict) else None
        if iid not in verdicts:
            continue
        v = str(row.get("verdict", "")).lower()
        if v in ("pass", "fail"):
            verdicts[iid] = {"verdict": v, "evidence": str(row.get("evidence", ""))[:300], "fix": str(row.get("fix", ""))[:300]}
        else:
            err = f"허용되지 않은 판정값: {v!r}"
    missing = [i for i, v in verdicts.items() if v is None]
    if missing and not err:
        err = f"리뷰어가 판정하지 않은 항목 {len(missing)}건"
    return verdicts, err


def score(items: list[dict], verdicts: dict, pass_score: int = 85) -> dict:
    total = sum(int(i.get("weight", 1)) for i in items)
    got = sum(int(i.get("weight", 1)) for i in items if (verdicts.get(i["id"]) or {}).get("verdict") == "pass")
    required_failed = [i["id"] for i in items if i.get("required") and (verdicts.get(i["id"]) or {}).get("verdict") != "pass"]
    undetermined = [i["id"] for i in items if verdicts.get(i["id"]) is None]
    sc = round(got / total * 100) if total else 0
    return {"score": sc, "passed": not required_failed and sc >= pass_score, "requiredFailed": required_failed,
            "undetermined": undetermined, "weights": {"total": total, "passed": got}}
