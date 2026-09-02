"""F5 화면 생성 파이프라인 (SPEC §5 F5, §2 S3, §12.9).

registry_lookup(정확 조회 · 벡터 검색 없음) → skills → generate(Bedrock 스트리밍) → 코드블록 추출 → 헤더/import 파싱
→ Registry 게이트(승인 이름@버전만) → 게이트 실행기(gates/ Node Lambda: build/types/lint/a11y/visual)
→ 실패 시 실패 사유를 컨텍스트에 넣어 **1회만** 재생성 → 최종.

원칙:
- 프롬프트에는 registry.api.list_approved(subtype="COMPONENT") 결과만 들어간다. 승인 상태가 바뀌면 결과가 바뀐다.
- 게이트 실행기가 없으면 {ok: None, note: '게이트 실행기 미연결'} — 통과를 흉내내지 않는다.
- 이 모듈은 import 시점에 boto3 클라이언트를 만들지 않는다 (테스트 오프라인).
- 로그·트레이스에 프롬프트/코드 원문을 넣지 않는다 (§12.3) — 길이·해시만.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

MAX_REGENERATIONS = 1                       # §12.9 무한 루프 금지 — 재생성은 최대 1회
MAX_ATTEMPTS = MAX_REGENERATIONS + 1
GATE_KEYS = ("build", "types", "lint", "a11y", "visual")
SKILL_NAMES = ("bank-publishing-conventions", "kwcag-accessibility", "screen-generation-output-format")
UNAVAILABLE_NOTE = "게이트 실행기 미연결"

ROOT = Path(__file__).resolve().parent.parent
GATES_DIR = ROOT / "gates"
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
MODEL = os.environ.get("GEN_MODEL", "apac.anthropic.claude-sonnet-4-20250514-v1:0")


class GatesError(Exception):
    """게이트 실행기 호출 실패 (Lambda 오류·로컬 node 실패). run()은 이를 '미판정'으로 표기한다."""


# ---------------------------------------------------------------------------
# Registry 컴포넌트 (정확 조회)
# ---------------------------------------------------------------------------
def kebab(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


def version_num(v) -> int:
    digits = re.sub(r"\D", "", str(v or ""))
    return int(digits) if digits else 0


def _type_label(schema) -> str:
    """propsSchema 속성 → 짧은 타입 표기 (UI·프롬프트 표시용)."""
    if schema is None:
        return "unknown"
    if isinstance(schema, str):
        return schema
    if not isinstance(schema, dict):
        return "unknown"
    if schema.get("enum"):
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
    t = schema.get("type")
    if t in ("function", "fn"):
        return "fn"
    if t == "node":
        return "ReactNode"
    if t == "array":
        return f"{_type_label(schema.get('items'))}[]"
    if t == "object":
        props = schema.get("properties") or {}
        if props:
            return "{ " + ", ".join(f"{k}: {_type_label(v)}" for k, v in props.items()) + " }"
        return "object"
    if isinstance(t, list):
        return " | ".join(str(x) for x in t)
    return str(t or "unknown")


def describe_props(schema) -> list:
    """[{name, type, required}] — 화면 표시와 프롬프트 요약에 쓴다."""
    if not isinstance(schema, dict):
        return []
    required = set(schema.get("required") or [])
    props = schema.get("properties") or {}
    return [{"name": k, "type": _type_label(v), "required": k in required} for k, v in props.items()]


def normalize_component(rec: dict) -> dict:
    """CONTRACTS §3 레코드 → 파이프라인 컴포넌트 (module/exportName/propsSchema 보정)."""
    payload = rec.get("payload") or {}
    name = str(rec.get("name", "")).strip()
    version = str(rec.get("recordVersion") or rec.get("version") or "").strip()
    module = payload.get("module")
    if not module or module == "@atom/ui":           # 배럴 경로만 있으면 모듈 경로로 정규화
        module = "@atom/ui/" + kebab(name)
    schema = payload.get("propsSchema") or {"type": "object", "properties": {}}
    return {
        "name": name, "version": version, "module": module,
        "exportName": payload.get("exportName") or name,
        "propsSchema": schema, "props": describe_props(schema),
        "description": rec.get("description", ""),
        "supersededBy": payload.get("supersededBy"),
        "status": rec.get("status", "APPROVED"),
    }


def load_approved_components() -> tuple:
    """Registry Consumer API 정확 조회. 반환 (components, source) — source ∈ {'registry', 'fixture'}.

    registry 모듈이 없을 때(ImportError)만 픽스처를 쓴다. 그 외 오류는 그대로 올린다 — 승인 상태를 흉내내지 않는다.
    """
    try:
        from registry import api as registry_api  # 다른 모듈 소유 — 지연 import
    except ImportError:
        from screengen import components_fixture
        comps = [normalize_component(r) for r in components_fixture.approved()]
        return _sort_components(comps), "fixture"
    records = registry_api.list_approved(subtype="COMPONENT") or []
    comps = [normalize_component(r) for r in records if str(r.get("status", "APPROVED")) == "APPROVED"]
    return _sort_components(comps), "registry"


def _sort_components(comps: list) -> list:
    return sorted(comps, key=lambda c: (c["name"], -version_num(c["version"])))


def public_component(c: dict) -> dict:
    return {k: c.get(k) for k in ("name", "version", "module", "exportName", "props", "propsSchema",
                                  "description", "supersededBy")}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def skills_dir() -> Path:
    env = os.environ.get("SKILLS_DIR")
    candidates = ([Path(env)] if env else []) + [ROOT / "skills", Path(__file__).resolve().parent / "skills"]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


def load_skills(names=SKILL_NAMES) -> tuple:
    """반환 (skills: {name: text}, missing: [name]). 없는 스킬은 흉내내지 않고 missing 에 적는다."""
    base = skills_dir()
    skills, missing = {}, []
    for n in names:
        p = base / f"{n}.md"
        if p.is_file():
            skills[n] = p.read_text(encoding="utf-8")
        else:
            missing.append(n)
    return skills, missing


# ---------------------------------------------------------------------------
# 프롬프트 (순수 함수)
# ---------------------------------------------------------------------------
def build_system_prompt(components: list, skills: dict) -> str:
    """승인 컴포넌트(propsSchema 포함) + 스킬 원문을 담은 시스템 프롬프트. 승인 목록 밖 컴포넌트는 언급조차 하지 않는다."""
    lines = [
        "당신은 아톰은행 프론트엔드 화면 생성 에이전트다. 요청받은 업무 화면을 React 18 + TypeScript(TSX) 코드로 생성한다.",
        "아래 '승인 컴포넌트' 목록에 있는 컴포넌트와 버전만 사용할 수 있다. 목록 밖의 컴포넌트·버전·props 는 존재하지 않는 것으로 취급한다.",
        "",
        f"## 승인 컴포넌트 (Registry Consumer API · APPROVED 만 · 정확 조회 · {len(components)}개)",
    ]
    if not components:
        lines.append("(승인된 컴포넌트가 없다 — 시맨틱 HTML 만으로 화면을 구성한다)")
    for c in components:
        lines += [
            f"### {c['name']}@{c['version']}",
            f"- import: import {{ {c['exportName']} }} from '{c['module']}'",
        ]
        if c.get("description"):
            lines.append(f"- 설명: {c['description']}")
        lines.append("- propsSchema: " + json.dumps(c.get("propsSchema") or {}, ensure_ascii=False, separators=(",", ":")))
    lines += ["", "## 규약 (Skills — 전부 준수)"]
    for name, text in skills.items():
        lines += [f"### skill: {name}", text.strip(), ""]
    lines += [
        "## 출력",
        "- 단일 ```tsx 코드블록 1개만 출력한다. 첫 줄은 `// registry: 이름@버전, …` (사용한 승인 컴포넌트 전부, 승인 버전 그대로).",
        "- `export default function Screen()` 하나만 export 한다. import 는 'react' 와 승인 목록의 '@atom/ui/…' 경로만.",
        "- 120줄 이내로 간결하게. 코드블록 밖 설명은 한 문장 이내.",
    ]
    return "\n".join(lines)


def build_user_prompt(request: str, failures=None, previous_code: str = "") -> str:
    """요청 프롬프트. 재생성 시 실패 사유(게이트 진단)와 이전 코드를 덧붙인다 — 1회만 호출된다."""
    out = [f"요청: {request.strip()}"]
    if failures:
        out += ["", "이전 생성 결과가 검증 게이트에서 실패했다. 아래 사유를 **모두** 해결한 전체 파일을 다시 출력하라 (부분 수정 금지).",
                "[실패 사유]"] + [f"- {f}" for f in failures]
        if previous_code:
            out += ["", "[이전 코드]", "```tsx", previous_code[:6000], "```"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 출력 파싱 (순수 함수)
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:tsx|ts|typescript|jsx|javascript|js)?[ \t]*\r?\n(.*?)```", re.S)
_OPEN_FENCE_RE = re.compile(r"```(?:tsx|ts|typescript|jsx|javascript|js)?[ \t]*\r?\n", re.S)
_HEADER_RE = re.compile(r"^\s*//\s*registry\s*:\s*(.*?)\s*$", re.I)
_HEADER_ENTRY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.\-]*)\s*@\s*(v?\d+)$")
_IMPORT_RE = re.compile(r"""^[ \t]*import\s+(?!['"])(.+?)\s+from\s+['"]([^'"]+)['"]\s*;?""", re.S | re.M)
_SIDE_IMPORT_RE = re.compile(r"""^[ \t]*import\s+['"]([^'"]+)['"]""", re.M)


def extract_code_block(text: str) -> str:
    """응답에서 TSX 코드블록을 꺼낸다. 여러 개면 export default 가 있는 것을, 없으면 첫 블록을. 닫히지 않은 펜스도 처리."""
    text = text or ""
    blocks = [b.strip() for b in _FENCE_RE.findall(text)]
    if blocks:
        for b in blocks:
            if "export default" in b:
                return b
        return blocks[0]
    m = _OPEN_FENCE_RE.search(text)
    if m:                                       # max_tokens 로 잘린 출력
        return text[m.end():].strip()
    t = text.strip()
    if "export default" in t or t.startswith("//") or t.startswith("import "):
        return t
    return ""


def parse_registry_header(code: str) -> list:
    """첫 몇 줄에서 `// registry: Button@v2, DataTable@v1` 를 찾아 [{name, version}] 로 돌려준다."""
    lines = [ln for ln in (code or "").splitlines() if ln.strip()][:5]
    for ln in lines:
        m = _HEADER_RE.match(ln)
        if not m:
            continue
        entries = []
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            em = _HEADER_ENTRY_RE.match(part)
            if em:
                v = em.group(2)
                entries.append({"name": em.group(1), "version": v if v.startswith("v") else "v" + v})
            else:
                entries.append({"name": part, "version": ""})
        return entries
    return []


def header_is_first_line(code: str) -> bool:
    first = next((ln for ln in (code or "").splitlines() if ln.strip()), "")
    return bool(_HEADER_RE.match(first))


def parse_imports(code: str) -> list:
    """[{module, names, default, namespace}] — 정적 import 문만 (문자열/주석 안의 import 는 구분하지 않는다)."""
    out = []
    for m in _IMPORT_RE.finditer(code or ""):
        clause, module = m.group(1).strip(), m.group(2)
        if clause.startswith("type "):
            clause = clause[5:].strip()
        names, default, namespace = [], None, None
        bm = re.search(r"\{(.*?)\}", clause, re.S)
        if bm:
            for part in bm.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                if part.startswith("type "):
                    part = part[5:].strip()
                names.append(part.split(" as ")[0].strip())
        rest = re.sub(r"\{.*?\}", "", clause, flags=re.S).strip().strip(",").strip()
        if rest.startswith("* as"):
            namespace = rest[4:].strip()
        elif rest:
            default = rest.split(",")[0].strip()
        out.append({"module": module, "names": names, "default": default, "namespace": namespace})
    for m in _SIDE_IMPORT_RE.finditer(code or ""):
        out.append({"module": m.group(1), "names": [], "default": None, "namespace": None})
    return out


def _is_ui_module(mod: str) -> bool:
    return mod == "@atom/ui" or mod.startswith("@atom/ui/")


def check_registry_usage(code: str, approved: list) -> dict:
    """Registry 게이트 (순수 함수).

    - 헤더의 이름@버전이 승인 목록에 정확히 있어야 한다 (버전 불일치 = 실패, 사유에 승인 버전을 적는다)
    - import 출처는 react / @atom/ui/* 만
    - import 한 UI 컴포넌트는 모두 헤더에 있어야 하고, 헤더 컴포넌트는 import 되어야 한다(미사용은 경고)
    반환 {ok, problems[], warnings[], used:[{name, version, module}], header:[...]}
    """
    by_name: dict = {}
    by_export: dict = {}
    for c in approved:
        by_name.setdefault(c["name"], {})[c["version"]] = c
        by_export.setdefault(c.get("exportName") or c["name"], []).append(c)

    problems, warnings, used = [], [], []
    header = parse_registry_header(code)
    if not header:
        problems.append("첫 줄 `// registry: 이름@버전, …` 헤더가 없습니다.")
    elif not header_is_first_line(code):
        warnings.append("`// registry:` 헤더가 첫 줄이 아닙니다.")

    ui_imports: dict = {}
    for imp in parse_imports(code):
        mod = imp["module"]
        if mod == "react" or mod.startswith("react/"):
            continue
        if _is_ui_module(mod):
            if imp["default"] or imp["namespace"]:
                problems.append(f"'{mod}' 은(는) named import 만 허용됩니다 (default/namespace import 금지).")
            for n in imp["names"]:
                ui_imports[n] = mod
            continue
        problems.append(f"허용되지 않은 import 출처: '{mod}' (허용: 'react', '@atom/ui/…').")

    header_names = set()
    seen = set()
    for h in header:
        key = f"{h['name']}@{h['version']}" if h["version"] else h["name"]
        if key in seen:
            warnings.append(f"헤더에 {key} 이(가) 중복됩니다.")
            continue
        seen.add(key)
        comp = by_name.get(h["name"], {}).get(h["version"])
        if comp is None:
            avail = sorted(by_name.get(h["name"], {}).keys(), key=version_num)
            if avail:
                problems.append(f"{key} 은(는) 승인되지 않은 버전입니다 (승인: "
                                + ", ".join(f"{h['name']}@{v}" for v in avail) + ").")
            else:
                problems.append(f"{key} 은(는) Registry 승인 목록에 없습니다.")
            continue
        header_names.add(comp["name"])
        exp = comp.get("exportName") or comp["name"]
        if exp not in ui_imports:
            warnings.append(f"헤더의 {key} 이(가) import 되지 않았습니다 (미사용).")
        elif ui_imports[exp] not in (comp["module"], "@atom/ui"):
            problems.append(f"{key} 의 import 경로 '{ui_imports[exp]}' 이(가) 승인 모듈 '{comp['module']}' 과 다릅니다.")
        used.append({"name": comp["name"], "version": comp["version"], "module": comp["module"]})

    for exp, mod in ui_imports.items():
        comps = by_export.get(exp, [])
        if not comps:
            problems.append(f"import 한 '{exp}' ('{mod}') 은(는) 승인 컴포넌트가 아닙니다.")
        elif not any(c["name"] in header_names for c in comps):
            problems.append(f"import 한 '{exp}' 이(가) `// registry:` 헤더에 없습니다.")

    return {"ok": not problems, "problems": problems, "warnings": warnings, "used": used, "header": header}


def select_components_for_gates(approved: list, used: list) -> list:
    """게이트에 넘길 선언 목록 — 모듈당 1개: 헤더가 지정한 버전 우선, 없으면 승인된 최신 버전."""
    used_keys = {(u["name"], u["version"]) for u in used}
    chosen: dict = {}
    for c in approved:
        if (c["name"], c["version"]) in used_keys:
            chosen[c["module"]] = c
    for c in sorted(approved, key=lambda x: -version_num(x["version"])):
        chosen.setdefault(c["module"], c)
    return [{"name": c["name"], "version": c["version"], "module": c["module"],
             "exportName": c.get("exportName") or c["name"], "propsSchema": c.get("propsSchema") or {}}
            for c in chosen.values()]


# ---------------------------------------------------------------------------
# 게이트 실행기 (Lambda / 로컬 node / 미연결)
# ---------------------------------------------------------------------------
def gates_runner_mode() -> str:
    if os.environ.get("GATES_FN"):
        return "lambda"
    if (GATES_DIR / "node_modules").is_dir() and (GATES_DIR / "index.js").is_file() and shutil.which("node"):
        return "local"
    return "none"


def unavailable_gates(note: str = UNAVAILABLE_NOTE, runner: str = "none") -> dict:
    out = {k: {"ok": None, "note": note} for k in GATE_KEYS}
    out["runner"] = runner
    return out


def _invoke_gates_lambda(payload: dict) -> dict:
    import boto3
    from botocore.config import Config
    lam = boto3.client("lambda", region_name=REGION,
                       config=Config(read_timeout=110, connect_timeout=5, retries={"max_attempts": 0}))
    r = lam.invoke(FunctionName=os.environ["GATES_FN"], Payload=json.dumps(payload, ensure_ascii=False).encode())
    body = json.loads(r["Payload"].read().decode() or "{}")
    if r.get("FunctionError") or "errorMessage" in body:
        raise GatesError(str(body.get("errorMessage") or body)[:300])
    body["runner"] = "lambda"
    return body


def _run_gates_local(payload: dict) -> dict:
    try:
        p = subprocess.run(["node", str(GATES_DIR / "index.js")], input=json.dumps(payload, ensure_ascii=False).encode(),
                           capture_output=True, timeout=120, cwd=str(GATES_DIR))
    except subprocess.TimeoutExpired:
        raise GatesError("로컬 게이트 실행 시간 초과 (120s)")
    if p.returncode != 0:
        raise GatesError((p.stderr or b"").decode(errors="replace")[:300] or f"node exit {p.returncode}")
    body = json.loads(p.stdout.decode() or "{}")
    body["runner"] = "local"
    return body


def default_gates_runner(payload: dict) -> dict:
    mode = gates_runner_mode()
    if mode == "lambda":
        return _invoke_gates_lambda(payload)
    if mode == "local":
        return _run_gates_local(payload)
    return unavailable_gates()


def default_generate(system: str, user: str, max_tokens: int):
    from engine import bedrock  # 경계 통과 지점 — 지연 import (테스트 오프라인)
    return bedrock.Stream(system, user, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# 게이트 결과 해석
# ---------------------------------------------------------------------------
def overall_ok(gates: dict):
    vals = [(gates.get(k) or {}).get("ok") for k in GATE_KEYS + ("registry",)]
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def failure_reasons(gates: dict, limit: int = 8) -> list:
    """재생성 컨텍스트에 넣을 실패 사유 (ok is False 인 게이트만 — 미판정(None)은 실패가 아니다)."""
    reasons = []
    reg = gates.get("registry") or {}
    if reg.get("ok") is False:
        reasons += [f"registry: {p}" for p in reg.get("problems", [])[:limit]]
    for key in ("build", "types", "lint"):
        g = gates.get(key) or {}
        if g.get("ok") is not False:
            continue
        errs = g.get("errors") or []
        for e in errs[:limit]:
            loc = e.get("file") or "Screen.tsx"
            if e.get("line"):
                loc += f":{e['line']}"
            rule = f" [{e['ruleId']}]" if e.get("ruleId") else ""
            reasons.append(f"{key}: {loc}{rule} {e.get('message', '')}".strip())
        if not errs and g.get("note"):
            reasons.append(f"{key}: {g['note']}")
    a = gates.get("a11y") or {}
    if a.get("ok") is False:
        for v in (a.get("violations") or [])[:limit]:
            targets = ", ".join(str(n.get("target", "")) for n in (v.get("nodes") or [])[:3])
            kw = f" ({v['kwcag']})" if v.get("kwcag") else ""
            reasons.append(f"a11y: [{v.get('id')}] {v.get('help', '')}{kw} — {targets}".strip())
        if not a.get("violations") and a.get("note"):
            reasons.append(f"a11y: {a['note']}")
    return reasons


def run_gates(code: str, approved: list, registry_result: dict, gates_runner, previous_snapshot=None) -> dict:
    """Node 게이트 실행 + Registry 게이트 합성. 실행기 오류는 '미판정'으로 표기하고 예외를 올리지 않는다."""
    if not code:
        gates = unavailable_gates("코드블록이 없어 게이트를 실행하지 않았습니다.")
        gates["build"] = {"ok": False, "errors": [{"file": "Screen.tsx", "message": "코드블록을 찾지 못했습니다."}]}
    else:
        payload = {"code": code, "filename": "Screen.tsx",
                   "components": select_components_for_gates(approved, registry_result.get("used", []))}
        if previous_snapshot:
            payload["previousSnapshot"] = previous_snapshot
        try:
            gates = gates_runner(payload)
        except GatesError as e:
            gates = unavailable_gates(f"게이트 실행기 오류: {str(e)[:200]}", runner="error")
        except Exception as e:  # boto3/네트워크 오류도 미판정으로
            gates = unavailable_gates(f"게이트 실행기 오류: {type(e).__name__}: {str(e)[:160]}", runner="error")
        for k in GATE_KEYS:
            gates.setdefault(k, {"ok": None, "note": "결과 없음"})
    gates["registry"] = {"ok": registry_result.get("ok", False), "problems": registry_result.get("problems", []),
                         "warnings": registry_result.get("warnings", []), "used": registry_result.get("used", [])}
    gates["ok"] = overall_ok(gates)
    return gates


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------
class ListEmitter:
    """테스트/CLI 용 이벤트 수집기. 핸들러는 ctx 를 감싼 어댑터를 넘긴다 (stage(step, **kw) / token(text))."""

    def __init__(self) -> None:
        self.stages: list = []
        self.tokens: list = []

    def stage(self, step: str, **kw) -> None:
        self.stages.append({"step": step, **kw})

    def token(self, text: str) -> None:
        self.tokens.append(text)


def run(prompt: str, emitter, components=None, components_source: str = "", skills=None,
        generate=None, gates_runner=None, previous_snapshot=None, max_tokens: int = 3500,
        outbound_scan=None) -> dict:
    """전체 파이프라인. 반환 dict 는 그대로 `.done` 페이로드가 된다.

    components/skills/generate/gates_runner 는 주입 가능(테스트). 기본값은 Registry 정확 조회 / 스킬 파일 /
    engine.bedrock.Stream / GATES_FN Lambda(또는 로컬 node, 없으면 미판정).
    outbound_scan(text)->{count, detectors} 가 주어지면 경계(Bedrock)로 나가는 프롬프트를 실측 스캔한다 (F6).
    """
    t0 = time.time()
    if components is None:
        components, components_source = load_approved_components()
    components = _sort_components(list(components))
    emitter.stage("registry_lookup", components=[public_component(c) for c in components], count=len(components),
                  source=components_source or "injected", lookup="exact", plane="cloud")

    missing: list = []
    if skills is None:
        skills, missing = load_skills()
    emitter.stage("skills", names=list(skills.keys()), missing=missing,
                  chars=sum(len(t) for t in skills.values()), plane="cloud")

    system = build_system_prompt(components, skills)
    generate = generate or default_generate
    gates_runner = gates_runner or default_gates_runner

    usage = {"inputTokens": 0, "outputTokens": 0}
    outbound = {"count": 0, "detectors": [], "scanned": 0}   # F6: 경계로 나가는 프롬프트 실측 스캔
    history: list = []
    failures: list = []
    prev_code = ""
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        if attempt > 1:
            emitter.stage("regenerate", attempt=attempt, reasons=failures, limit=MAX_REGENERATIONS, plane="cloud")
        user = build_user_prompt(prompt, failures if attempt > 1 else None, prev_code)
        if outbound_scan is not None:
            try:
                scan = outbound_scan(system + "\n" + user) or {}
                outbound["count"] += int(scan.get("count", 0) or 0)
                outbound["scanned"] += 1
                for d in scan.get("detectors", []) or []:
                    if d not in outbound["detectors"]:
                        outbound["detectors"].append(d)
            except Exception as e:
                outbound["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        emitter.stage("generate", attempt=attempt, model=MODEL, systemChars=len(system), userChars=len(user),
                      piiOutbound=outbound["count"] if outbound_scan is not None else None, plane="cloud")
        stream = generate(system, user, max_tokens)
        parts: list = []
        for tk in stream:
            parts.append(tk)
            emitter.token(tk)
        u = getattr(stream, "usage", None) or {}
        usage["inputTokens"] += int(u.get("inputTokens", 0) or 0)
        usage["outputTokens"] += int(u.get("outputTokens", 0) or 0)

        code = extract_code_block("".join(parts))
        reg = check_registry_usage(code, components) if code else \
            {"ok": False, "problems": ["코드블록을 찾지 못했습니다."], "warnings": [], "used": [], "header": []}
        gates = run_gates(code, components, reg, gates_runner, previous_snapshot)
        emitter.stage("gates", attempt=attempt, results=gates, runner=gates.get("runner"), ok=gates["ok"], plane="cloud")
        history.append({"attempt": attempt, "code": code, "gates": gates, "componentsUsed": reg["used"], "ok": gates["ok"]})

        failures = failure_reasons(gates)
        if not failures:                      # 통과(True) 또는 미판정(None) — 재생성하지 않는다
            break
        prev_code = code

    final = history[-1]
    return {
        "code": final["code"], "componentsUsed": final["componentsUsed"], "gates": final["gates"],
        "attempts": attempt, "ok": final["ok"], "regenerated": attempt > 1, "maxRegenerations": MAX_REGENERATIONS,
        "usage": usage, "piiOutbound": outbound["count"], "outbound": outbound,
        "history": history[:-1], "reasons": failures,
        "componentsSource": components_source or "injected", "componentCount": len(components),
        "skills": list(skills.keys()), "skillsMissing": missing, "model": MODEL,
        "gatesRunner": final["gates"].get("runner"), "elapsedMs": int((time.time() - t0) * 1000),
    }
