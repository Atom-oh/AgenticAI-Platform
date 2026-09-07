"""생성 프롬프트와 출력 파서.

출력 형식(구분자 — HTML 을 JSON 문자열로 이스케이프하지 않기 위해):
  <<<STEP id="intro" title="상품 안내">>>
  <!doctype html> ... 완전한 HTML 문서 ...
  <<<END>>>
  ... 스텝 반복 ...
  <<<FLOW>>>
  {"transitions":[{"from":"intro","to":"eligibility","trigger":"다음"}, ...]}
  <<<END>>>
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

MAX_STEPS = 10
_STEP = re.compile(r"<<<STEP\s+id=\"([^\"]+)\"\s+title=\"([^\"]*)\"\s*>>>\s*(.*?)\s*<<<END>>>", re.S)
_FLOW = re.compile(r"<<<FLOW>>>\s*(.*?)\s*<<<END>>>", re.S)


class ParseError(ValueError):
    pass


def build_prompts(prd: Dict[str, Any], sm: Dict[str, Any], spec: Dict[str, Any], assets: Optional[List[dict]] = None,
                  failures: Optional[List[dict]] = None, previous: Optional[Dict[str, Any]] = None,
                  output_type: str = "design") -> Tuple[str, str]:
    orgs = {o.get("id"): o for o in sm.get("organisms") or []}
    mols = {m.get("id"): m for m in sm.get("molecules") or []}
    steps_desc = []
    for s in prd.get("steps") or []:
        lines = [f"- 스텝 id=\"{s['id']}\" 제목=\"{s['title']}\""]
        if s.get("branch"):
            lines.append(f"  · 분기: {s['branch']['when']} 일 때만 진입 (앵커 '{s['branch']['after']}' 뒤)")
        for oid in s.get("organisms") or []:
            o = orgs.get(oid) or {}
            comp = ", ".join(f"{m}({(mols.get(m) or {}).get('intent', '')})" for m in o.get("composition") or [])
            lines.append(f"  · 오가니즘 {oid}: {o.get('title', '')} — 구성: {comp}")
            for r in o.get("rules") or []:
                lines.append(f"    - 룰: {r}")
        if s.get("required"):
            lines.append("  · 화면에 반드시 보여야 하는 문구: " + " / ".join(f"\"{r}\"" for r in s["required"]))
        steps_desc.append("\n".join(lines))
    trans = "\n".join(f"- {t['from']} → {t['to']} : 버튼 문구 \"{t['trigger']}\"" + (f" (조건: {t['when']})" if t.get("when") else "")
                      for t in prd.get("transitions") or [])
    asset_txt = ""
    if assets:
        asset_txt = "\n\n### 디자인 자산 (팔레트·토큰 — 이 값이 기본 스타일을 이긴다)\n" + "\n".join(
            f"- {a.get('name')} ({a.get('type')}): {json.dumps(a.get('content'), ensure_ascii=False)[:600]}" for a in assets)
    style = {
        "design": "완성도 있는 모바일 뱅킹 화면(하나은행 톤, 청록 #008485 계열). 실제 서비스처럼 보이되 이미지는 쓰지 않는다(CSS 도형·이모지만).",
        "wireframe": "회색조 와이어프레임. 브랜드 컬러 사용 금지, 자리표시 박스와 라벨만.",
        "mockup": "고충실도 목업. 실제 문구·수치 채움.",
        "uxflow": "각 스텝 화면 상단에 진행 표시(현재 스텝 강조)를 넣고, 분기 조건을 화면 안에 배지로 표기.",
    }.get(output_type, "")

    system = (
        "당신은 아톰은행 디자인 스튜디오의 프로세스 화면 생성 에이전트다. 상품명세서에서 도출된 PRD(스텝·분기·필수 요소)와 "
        "UX SM 모델(몰큘=인텐트, 오가니즘=구성·룰·관계, 템플릿=구조)을 조건으로 가입 프로세스 전체를 스텝별 HTML 화면으로 만든다.\n"
        "규칙:\n"
        "1. PRD 의 모든 스텝을 정확히 그 id 로 만든다. 스텝을 합치거나 빼지 않는다.\n"
        "2. 각 스텝은 완전한 self-contained HTML 문서(<!doctype html><html lang=\"ko\">…). 외부 리소스·이미지 URL 금지, CSS 는 <style> 인라인.\n"
        "3. 각 화면에 h1 하나, 모든 <img> 에 alt, 모든 입력 요소에 <label for> 연결.\n"
        "4. PRD 전이의 버튼 문구를 출발 스텝 화면의 <button> 텍스트로 그대로 쓴다. 분기 전이는 조건 문구를 버튼 옆에 함께 표기한다.\n"
        "5. '반드시 보여야 하는 문구'는 글자 그대로 화면 본문에 넣는다 (요약·의역 금지).\n"
        "6. 숫자(금리·기간·한도)는 PRD 와 명세서에 있는 값만 쓴다. 새로 만들지 않는다.\n"
        "7. 출력은 아래 구분자 형식만. 설명·머리말 없이 바로 <<<STEP 로 시작한다.\n"
        f"8. 스타일: {style}\n"
        "출력 형식:\n<<<STEP id=\"<스텝id>\" title=\"<제목>\">>>\n<완전한 HTML>\n<<<END>>>\n(스텝마다 반복)\n"
        "<<<FLOW>>>\n{\"transitions\":[{\"from\":\"…\",\"to\":\"…\",\"trigger\":\"…\",\"when\":\"…(선택)\"}]}\n<<<END>>>"
    )
    user = (
        f"### 상품명세서\n{json.dumps(spec, ensure_ascii=False, indent=1)[:4000]}\n\n"
        f"### PRD — {prd.get('title')}\n" + "\n".join(steps_desc) + f"\n\n### 전이(버튼)\n{trans}" + asset_txt
    )
    if failures:
        user += ("\n\n### ★ 이전 시도의 검수 실패 항목 — 이번에 반드시 해결한다\n" +
                 "\n".join(f"- [{f.get('id')}] {f.get('text')} → 근거: {f.get('evidence')}" for f in failures))
        if previous and previous.get("steps"):
            keep = ", ".join(str(s.get("id")) for s in previous["steps"])
            user += f"\n이전 시도의 스텝({keep}) 구조는 유지하되 실패 항목만 고쳐 전체를 다시 출력한다."
    return system, user


def parse_flow(text: str) -> Dict[str, Any]:
    steps: List[dict] = []
    seen = set()
    for sid, title, html in _STEP.findall(text or ""):
        sid = sid.strip()
        if sid in seen:
            continue
        seen.add(sid)
        steps.append({"id": sid, "title": title.strip(), "html": html.strip()})
        if len(steps) >= MAX_STEPS:
            break
    if not steps:
        raise ParseError("<<<STEP …>>> 블록을 찾지 못했습니다")
    transitions: List[dict] = []
    m = _FLOW.search(text or "")
    if m:
        try:
            obj = json.loads(m.group(1))
            transitions = [t for t in (obj.get("transitions") or []) if isinstance(t, dict) and t.get("from") and t.get("to")]
        except json.JSONDecodeError as e:
            raise ParseError(f"<<<FLOW>>> JSON 오류: {e}") from e
    return {"steps": steps, "transitions": transitions}
