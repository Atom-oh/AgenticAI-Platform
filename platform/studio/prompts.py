# platform/studio/prompts.py
"""스튜디오 프롬프트 — 생성(시스템/유저)·리뷰어. 원본 uiux-studio 하네스의 OUTPUT_STYLES·REFINE 지침을 이식."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAMES = ("studio-design-system", "studio-draft-html", "studio-a11y-finance")
AXES = ("밀도", "강조", "흐름")
OUTPUT_TYPES = ("design", "mockup", "wireframe", "ux-flow")
OUTPUT_STYLES = {
    "design": "",
    "mockup": ("출력 유형: 목업(Mockup). 실서비스 수준 완성도 대신 빠른 검토용 목업 톤으로 — "
               "이미지 영역은 회색 플레이스홀더 박스, 데이터는 대표 샘플 1~2건만."),
    "wireframe": ("출력 유형: 와이어프레임(Wireframe). 로우파이 구조 스케치로 — 흑백+회색만 사용"
                  "(브랜드 컬러 금지), 박스/라인/원 플레이스홀더, 텍스트는 실제 레이블만, "
                  "이미지·아이콘은 X 표시된 회색 박스, 점선 테두리로 스케치 느낌."),
    "ux-flow": ("출력 유형: UX 플로우. 한 HTML 안에 가로 스크롤 컨테이너로 화면 프레임을 절차 단계 수만큼 "
                "순서대로 배치하고, 프레임 사이를 화살표(→)와 트리거 레이블(탭/입력/제출)로 연결해 "
                "하나의 사용자 흐름을 보여줘라. 각 프레임은 390px 폭 모바일 화면이며 <section data-step=\"n\"> 으로 감싼다."),
}
FEWSHOT_MAX_CHARS = 12000
FEWSHOT_MAX_REFS = 2
ASSETS_MAX_CHARS = 16000
_FENCE_RE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)
_DOC_RE = re.compile(r"(<!doctype html>.*?</html>|<html.*?</html>)", re.DOTALL | re.IGNORECASE)


def skills_dir() -> Path:
    env = os.environ.get("SKILLS_DIR")
    for c in ([Path(env)] if env else []) + [ROOT / "skills"]:
        if c.is_dir():
            return c
    return ROOT / "skills"


def load_skills(names=SKILL_NAMES) -> tuple[dict, list]:
    skills, missing = {}, []
    for n in names:
        p = skills_dir() / f"{n}.md"
        if p.is_file():
            skills[n] = p.read_text(encoding="utf-8")
        else:
            missing.append(n)
    return skills, missing


def _spec_block(spec: dict) -> str:
    steps = " → ".join(f"{i + 1}.{s['name']}" for i, s in enumerate(spec.get("steps", [])))
    conds = "\n".join(f"- [{c['type']}] {c['name']}" for c in spec.get("conditions", []))
    return (f"## DesignSpec — {spec.get('productName')} ({spec.get('productCode')}, {spec.get('category')})\n"
            f"가입 절차: {steps}\n"
            f"상품 조건(값을 그대로 화면에 반영):\n{conds or '- (없음)'}\n"
            f"우대 조건 {'있음 — 조건 입력·인증 스텝이 반드시 별도 화면으로 존재' if spec.get('hasPreferential') else '없음 — 절차 밖 스텝을 추가하지 않는다'}\n"
            f"표준 용어(그대로 사용): {', '.join(spec.get('terms', [])) or '-'}\n"
            f"검수 체크리스트(생성 후 이 항목으로 검수된다):\n" +
            "\n".join(f"- ({'필수' if i.get('required') else '권장'}) {i['text']}" for i in spec.get("items", [])[:40]))


def build_system_prompt(spec: dict, output_type: str, axis: str, skills: dict, assets_text: str = "",
                        fewshot: list | None = None, agent_preset: str = "") -> str:
    parts = ["당신은 아톰은행 UI/UX 시안 생성 에이전트다. 아래 스킬을 정확히 따르고, DesignSpec 의 상품 조건·절차·용어를 화면에 그대로 반영한다.",
             "출력은 스킬 'studio-draft-html' 의 계약대로 ```html 펜스 안 자기완결 HTML 1개다. 프레임은 <section data-step=\"1\" …> 부터 순서대로 마킹한다."]
    for n, t in skills.items():
        parts.append(f"### 스킬: {n}\n{t}")
    style = OUTPUT_STYLES.get(output_type, "")
    if style:
        parts.append(style)
    parts.append(f"이번 시안이 움직이는 축: {axis} (다른 축은 기본값 유지).")
    parts.append(_spec_block(spec))
    if agent_preset:
        parts.append("### 공유 에이전트 프리셋\n" + agent_preset)
    if assets_text:
        text = assets_text
        if len(text) > ASSETS_MAX_CHARS:
            text = text[:ASSETS_MAX_CHARS] + "\n(자산 내용 일부 생략)"
        parts.append("### 사용자가 선택한 자산 (기본 토큰보다 우선)\n" + text)
    for i, ref in enumerate((fewshot or [])[:FEWSHOT_MAX_REFS], start=1):
        body = ref
        if len(body) > FEWSHOT_MAX_CHARS:
            body = body[:FEWSHOT_MAX_CHARS] + "\n<!-- 참고 시안 일부 생략 -->"
        parts.append(f"### 승인 참고 시안 {i} — 구조·품질 기준만 따르고 상품 내용은 복사하지 않는다\n```html\n{body}\n```")
    return "\n\n".join(parts)


def build_user_prompt(brief: str, spec: dict, *, failures: list | None = None, prev_html: str = "", refine: dict | None = None) -> str:
    # prev_html is spliced in whole (no truncation): the regenerate/refine contract requires the
    # model to see and return the full previous document, or partial edits would corrupt it.
    if refine:
        head = ("아래 원본 HTML 전체가 주어진다. 사용자가 클릭으로 선택한 요소와 수정 지시에 따라 그 부분만 수정하고, "
                "나머지 마크업·스타일·텍스트·순서는 그대로 보존하라. 완성된 전체 HTML을 다시 출력하라.\n"
                f"선택 요소 selector: {refine.get('selector', '(전체)')}\n선택 요소 HTML:\n{(refine.get('elementHtml') or '')[:4000]}\n"
                f"수정 지시: {refine.get('instruction', '')}")
        if failures:
            fail_block = ["", "이전 라운드 결과가 검수에서 아래 항목에 실패했다. 수정 지시와 함께 이 항목들도 반영하되, "
                              "그 외 마크업·순서·텍스트는 그대로 유지하라.", "[실패 항목]"]
            fail_block += [f"- {f['id']}: {f['text']} — 근거: {f.get('evidence', '')} — 수정: {f.get('fix', '')}" for f in failures]
            head = head + "\n" + "\n".join(fail_block)
        return head + f"\n\n원본 HTML:\n```html\n{prev_html}\n```"
    out = [f"브리프: {brief.strip() or spec.get('productName', '')} 화면 시안"]
    if failures:
        out += ["", "이전 라운드 시안이 검수에서 아래 항목에 실패했다. 각 항목의 수정 지시를 모두 반영해 전체 HTML을 다시 출력하라.",
                "필요한 부분만 고치고 통과한 부분의 구조·순서·텍스트는 유지하라 (위치가 흔들리면 안 된다).", "[실패 항목]"]
        out += [f"- {f['id']}: {f['text']} — 근거: {f.get('evidence', '')} — 수정: {f.get('fix', '')}" for f in failures]
        if prev_html:
            out += ["", "이전 라운드 HTML:", "```html", prev_html, "```"]
    return "\n".join(out)


REVIEW_SYSTEM = (
    "당신은 아톰은행 디자인 리뷰 에이전트다. 생성 에이전트와 별개로, 주어진 화면 다이제스트가 체크리스트 항목을 만족하는지 판정한다.\n"
    "각 항목에 대해 verdict 는 'pass' 또는 'fail' 만 허용한다. 확인할 수 없으면 fail 로 두고 evidence 에 '확인 불가'라고 적는다.\n"
    "evidence 는 다이제스트에서 본 구체 문구·프레임 번호, fix 는 fail 일 때 생성 에이전트에게 줄 한 줄 수정 지시다.\n"
    "evidence·fix 문자열 안에는 큰따옴표(\")와 줄바꿈을 쓰지 말고 「」 를 쓴다.\n"
    "출력은 오직 JSON 한 개: {\"items\":[{\"id\":\"…\",\"verdict\":\"pass|fail\",\"evidence\":\"…\",\"fix\":\"…\"}]} — 설명·마크다운 금지."
)


def build_review_prompt(spec: dict, items: list, digest: str) -> str:
    rows = [{"id": i["id"], "required": bool(i.get("required")), "text": i["text"]} for i in items]
    return (f"상품: {spec.get('productName')} ({spec.get('category')}), 절차: "
            + " → ".join(s["name"] for s in spec.get("steps", []))
            + "\n조건: " + "; ".join(c["name"] for c in spec.get("conditions", []))
            + "\n\n[체크리스트]\n" + json.dumps(rows, ensure_ascii=False)
            + "\n\n[화면 다이제스트]\n" + digest
            + "\n\n위 항목 전부에 대해 JSON 으로 판정하라. 키는 \"items\", 각 원소는 \"id\",\"verdict\",\"evidence\",\"fix\".")


def extract_html(text: str) -> str:
    text = text or ""
    for b in _FENCE_RE.findall(text):
        if "<html" in b.lower() or "<body" in b.lower() or "<section" in b.lower():
            return b.strip()
    m = _DOC_RE.search(text)
    return m.group(1).strip() if m else ""
