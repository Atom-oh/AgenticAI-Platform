"""Build deterministic scenes and caption timing from local, verified assets."""
from pathlib import Path
from html import escape
import json
import subprocess

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
TOTAL = 240.0
GAP = 1.0
SCREENS = {
    "02-roles": "roles.png", "03-impact": "changes.png",
    "04-design": "components.png", "05-knowledge": "knowledge.png",
    "06-skills": "skills.png", "07-pension": "pension.png", "08-reports": "reports.png",
}
POINTS = [
    ["상품 조건 변경", "화면 · 가이드 · 컴포넌트", "담당자의 작업과 검토 근거"],
    ["기획자 · 디자이너 · 개발자", "직무별 메뉴, 프로젝트별 자료", "권한은 서버에서 확인"],
    ["무엇을 바꾸는가", "왜 영향을 받는가", "무엇이 아직 확인되지 않았는가"],
    ["상품 지침과 실제 산출물", "React 소스 · 동작 검증", "승인과 개발 전달 기록"],
    ["사내 위키는 지식 원본", "Vector + Graph 함께 게시", "원본 권한과 변경 이력 유지"],
    ["실제 지시문과 참고 자료", "내용 버전 · 해시 · 검증", "승인된 정확한 패키지"],
    ["합성 페르소나와 명시된 가정", "숫자는 서버의 계산 결과", "실제 답변에 연결된 직원 평가"],
    ["내부 심사 · 경영 · 규정", "출처 · 계산 · 미확인 항목", "원본 변경 시 다시 검토"],
]
FOOTNOTES = [
    "가상 상품을 사용한 구현 설명", "화면의 역할 선택은 권한 부여가 아닙니다",
    "등록·검증된 범위의 관계만 탐색합니다", "플랫폼 React 패키지 · 고객 사내 패키지와 구분",
    "비공개 파일 기반 인덱스 · 사내 Confluence 연결 미설정",
    "모델과 행동 평가의 실제 실행 상태를 구분합니다",
    "계산 예시 · 투자 권유 또는 실제 연금 확정액이 아닙니다",
    "자료 기반 초안 · 자동 신용판단·규정 해석 없음",
]


def command(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def fonts(text):
    out = ROOT / "assets/fonts"
    out.mkdir(parents=True, exist_ok=True)
    for weight in ("Regular", "Black"):
        original = Path("/usr/share/fonts/google-noto-cjk") / f"NotoSansCJKkr-{weight}.otf"
        options = subset.Options()
        options.flavor = "woff2"
        font = TTFont(original)
        selector = subset.Subsetter(options=options)
        selector.populate(text=text)
        selector.subset(font)
        font.flavor = "woff2"
        font.save(out / f"korean-{weight.lower()}.woff2")


def diagram(identifier):
    nodes = [(65, 75, "상품 조건"), (365, 75, "업무 흐름"), (665, 75, "안내 가이드"),
             (365, 295, "화면"), (665, 295, "React"), (665, 515, "아이콘")]
    paths = ["M265 130 H365", "M565 130 H665", "M465 185 V295",
             "M565 350 H665", "M765 405 V515"]
    return '<svg viewBox="0 0 980 710" aria-label="업무 의존 관계 개념도">' + "".join(
        f'<path id="s-{identifier}-path-{i}" d="{d}" fill="none" stroke="#008485" stroke-width="5"/>'
        for i, d in enumerate(paths)) + "".join(
        f'<g><rect x="{x}" y="{y}" width="200" height="110" rx="20" fill="#e2efec" stroke="#008485" stroke-width="2"/>'
        f'<text x="{x+100}" y="{y+67}" text-anchor="middle" fill="#123d3d" font-size="30" font-weight="900">{label}</text></g>'
        for x, y, label in nodes) + '</svg>'


def stamp(seconds):
    millis = round(seconds * 1000)
    return f"{millis//3600000:02}:{millis//60000%60:02}:{millis//1000%60:02}.{millis%1000:03}"


def main():
    scenes = json.loads((ROOT / "narration.json").read_text())
    voices = json.loads((ROOT / "audio_meta.json").read_text())
    voice_map = {v["id"]: v for v in voices}
    for filename in SCREENS.values():
        if not (ROOT / "assets/screens" / filename).is_file():
            raise RuntimeError("Capture the actual application before building: " + filename)
    speed = sum(v["duration"] for v in voices) / (TOTAL - len(scenes) * GAP)
    all_text = json.dumps(scenes, ensure_ascii=False) + json.dumps(POINTS, ensure_ascii=False) + "".join(FOOTNOTES)
    all_text += "상품 조건 업무 흐름 안내 가이드 화면 아이콘 아톰은행 구현 설명 합성 데이터"
    fonts(all_text)
    (ROOT / "compositions").mkdir(exist_ok=True)
    slots, audio_slots, subtitles, chapters = [], [], [], []
    cursor = 0.0
    for number, scene in enumerate(scenes):
        sid, voice = scene["id"], voice_map[scene["id"]]
        duration = voice["duration"] / speed + GAP
        if number == len(scenes) - 1:
            duration = TOTAL - cursor
        audio_path = "assets/voice/" + sid + "-timed.wav"
        command(["ffmpeg", "-v", "error", "-y", "-i", str(ROOT / voice["path"]),
                 "-af", f"atempo={speed:.10f}", "-ar", "24000", str(ROOT / audio_path)])
        css = """
          @font-face{font-family:'Workbench Korean';src:url('assets/fonts/korean-regular.woff2');font-weight:400}
          @font-face{font-family:'Workbench Korean';src:url('assets/fonts/korean-black.woff2');font-weight:900}
          #root {position:absolute;inset:0;width:1920px;height:1080px;box-sizing:border-box;background:#f0f6f4;color:#123d3d;
            font-family:'Workbench Korean';overflow:hidden;padding:66px 82px 150px}
          .mast {display:flex;justify-content:space-between;font-size:24px;align-items:center;border-bottom:2px solid #99bdb6;padding-bottom:22px}
          .chapter {font-weight:900;color:#006767} .serial {font-family:'IBM Plex Mono';font-size:22px}
          .body {display:grid;grid-template-columns:570px 1fr;gap:64px;align-items:center;height:720px}
          h1 {font-size:65px;line-height:1.3;letter-spacing:-2px;font-weight:900;margin:0 0 40px;word-break:keep-all}
          .points {display:grid;gap:20px}.point {font-size:30px;line-height:1.5;padding-left:24px;border-left:4px solid #008485}
          .evidence {position:relative;border:2px solid #91b8af;border-radius:24px;background:#fff;overflow:hidden;box-shadow:0 18px 35px #123d3d14}
          .alternate {position:absolute;inset:0}
          .screen {width:100%;height:680px;object-fit:cover;object-position:left top;display:block}
          .footnote {position:absolute;bottom:128px;left:82px;right:82px;font-size:22px;color:#335c57}
          svg {display:block;width:100%;height:680px} .progress {position:absolute;left:0;bottom:0;width:100%;height:7px;background:#008485;transform-origin:left center}
        """
        image = '<div class="evidence">' + (
            f'<img class="screen" src="assets/screens/{SCREENS[sid]}" alt="{escape(scene["label"])} 실제 구현 화면"/>'
            if sid in SCREENS else diagram(sid)) + '</div>'
        alternate = {"05-knowledge": "sources.png", "07-pension": "pension-chat.png"}.get(sid)
        if alternate:
            if not (ROOT / "assets/screens" / alternate).is_file():
                raise RuntimeError("Missing actual alternate capture: " + alternate)
            image = image[:-6] + f'<img id="s-{sid}-alternate" class="screen alternate" src="assets/screens/{alternate}" alt="실제 구현의 다음 작업 화면"/></div>'
        points = "".join(f'<div class="point" id="s-{sid}-point-{i}">{escape(p)}</div>' for i, p in enumerate(POINTS[number]))
        js = f"""const tl=gsap.timeline({{paused:true}});
          tl.fromTo('#s-{sid}-title',{{x:-24,opacity:0}},{{x:0,opacity:1,duration:.7,ease:'power3.out'}},.1);
          tl.fromTo('.evidence',{{y:26,opacity:0}},{{y:0,opacity:1,duration:.8,ease:'power2.out'}},.25);
          tl.fromTo('.progress',{{scaleX:0}},{{scaleX:1,duration:{duration},ease:'none'}},0);
        """
        for i in range(3):
            js += f"tl.fromTo('#s-{sid}-point-{i}',{{x:16,opacity:0}},{{x:0,opacity:1,duration:.6,ease:'power1.out'}},{1+i*duration*.24});"
        if sid == "01-change":
            js += "document.querySelectorAll('svg path').forEach((p,i)=>{const len=p.getTotalLength();p.style.strokeDasharray=len;tl.fromTo(p,{strokeDashoffset:len},{strokeDashoffset:0,duration:1.1,ease:'power2.out'},2+i*3);});"
        if alternate:
            js += f"tl.fromTo('#s-{sid}-alternate',{{opacity:0}},{{opacity:1,duration:.7,ease:'power2.out'}},{duration*.55});"
        js += f"window.__timelines['{sid}']=tl;"
        html = f"""<!doctype html><html lang="ko"><head><meta charset="UTF-8"></head><body><template>
          <style>{css}</style><div id="root" data-composition-id="{sid}" data-width="1920" data-height="1080" data-duration="{duration}">
          <div class="mast"><span class="chapter">{escape(scene["label"])}</span><span class="serial">ATOM AI / {number+1:02} — 08</span></div>
          <div class="body"><div><h1 id="s-{sid}-title">{escape(scene["title"])}</h1><div class="points">{points}</div></div>{image}</div>
          <div class="footnote">{escape(FOOTNOTES[number])}</div><div class="progress"></div></div><script>{js}</script></template></body></html>"""
        (ROOT / "compositions" / (sid + ".html")).write_text(html)
        slots.append(f'<div id="slot-{sid}" class="clip" data-composition-id="{sid}" data-composition-src="compositions/{sid}.html" data-start="{cursor:.6f}" data-duration="{duration:.6f}" data-track-index="1" data-width="1920" data-height="1080"></div>')
        audio_slots.append(f'<audio id="voice-{sid}" src="{audio_path}" data-start="{cursor+.35:.6f}" data-duration="{duration-GAP:.6f}" data-track-index="10" data-volume="1"></audio>')
        marks = [json.loads(line) for line in (ROOT / voice["marks"]).read_text().splitlines() if line]
        words = [m for m in marks if m["type"] == "word"]
        groups, group = [], []
        for word in words:
            if group and len(" ".join(w["value"] for w in group + [word])) > 48:
                groups.append(group); group = []
            group.append(word)
        if group:
            groups.append(group)
        for i, group in enumerate(groups):
            start = cursor + .35 + group[0]["time"] / 1000 / speed
            end = cursor + .35 + (groups[i+1][0]["time"] / 1000 / speed if i+1 < len(groups) else voice["duration"] / speed)
            subtitles.append({"start": round(start, 4), "end": round(end, 4),
                              "text": " ".join(w["value"] for w in group)})
        chapters.append({"id": sid, "start": cursor, "duration": duration, "title": scene["title"]})
        cursor += duration
    caption_nodes = "".join(f'<div id="caption-{i}" class="caption clip" data-start="{c["start"]}" data-duration="{c["end"]-c["start"]:.4f}" data-track-index="20" data-layout-allow-caption-zone>{escape(c["text"])}</div>' for i, c in enumerate(subtitles))
    master = f"""<!doctype html><html lang="ko"><head><meta charset="UTF-8"><script src="assets/gsap.min.js"></script>
    <style>
    @font-face{{font-family:'Workbench Korean';src:url('assets/fonts/korean-regular.woff2');font-weight:400}}
    @font-face{{font-family:'Workbench Korean';src:url('assets/fonts/korean-black.woff2');font-weight:900}}
    body{{margin:0}}#root{{position:relative;width:1920px;height:1080px;background:#f0f6f4;overflow:hidden}}
    .clip[data-composition-src]{{position:absolute;inset:0}}
    .caption{{position:absolute;bottom:34px;left:90px;right:90px;text-align:center;background:#123d3d;color:#f0f6f4;
      border-radius:14px;padding:17px 26px;font:400 30px/1.4 'Workbench Korean';z-index:20;box-sizing:border-box}}
    </style></head><body><div id="root" data-composition-id="workbench" data-width="1920" data-height="1080" data-duration="240">
    {''.join(slots)}{''.join(audio_slots)}{caption_nodes}</div><script>
    window.__timelines['workbench']=gsap.timeline({{paused:true}});
    </script></body></html>"""
    (ROOT / "index.html").write_text(master)
    (ROOT / "chapters.json").write_text(json.dumps(chapters, ensure_ascii=False, indent=2))
    (ROOT / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(
        f'{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}' for c in subtitles) + "\n")
    board = "---\nmode: autonomous\nduration: 240s\naspect: 16:9\n---\n\n# Implementation walkthrough\n\n"
    for chapter in chapters:
        board += (f'## Frame {chapter["id"]} — {chapter["title"]}\n\n'
                  f'- status: animated\n- duration: {chapter["duration"]:.3f}s\n'
                  f'- src: compositions/{chapter["id"]}.html\n'
                  '- motion: svg-path-draw; stat-bars-and-fills; explicit transform-and-opacity entrances\n'
                  '- evidence: actual synthetic application capture or labeled conceptual dependency diagram\n\n')
    (ROOT / "STORYBOARD.md").write_text(board)
    print(json.dumps({"duration": TOTAL, "scenes": len(scenes), "captionGroups": len(subtitles), "speechRate": speed}))


if __name__ == "__main__":
    main()
