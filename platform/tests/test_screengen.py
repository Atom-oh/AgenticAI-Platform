"""F5 화면 생성 에이전트 테스트 (오프라인 — Bedrock/Lambda 호출 없음, 페이크 주입).

실행: cd platform && python3 -m pytest tests/test_screengen.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from screengen import agent, components_fixture  # noqa: E402

APPROVED = [agent.normalize_component(r) for r in components_fixture.approved()]


def _approved_with_v3() -> list:
    """반전 시연 상태: Button v2 DEPRECATED, v3 APPROVED."""
    out = []
    for r in components_fixture.all_records():
        if r["name"] == "Button":
            if r["recordVersion"] == "v2":
                continue
            r = dict(r, status="APPROVED")
        if r["status"] == "APPROVED":
            out.append(agent.normalize_component(r))
    return out


V3_APPROVED = _approved_with_v3()

GOOD_CODE = """// registry: Button@v2, PageHeader@v1
import { useState } from 'react';
import { Button } from '@atom/ui/button';
import { PageHeader } from '@atom/ui/page-header';

export default function Screen() {
  const [n, setN] = useState(0);
  return (
    <div>
      <PageHeader title="여신 심사 결과 조회" description={'총 ' + n + '건'} />
      <Button label="조회" kind="primary" onClick={() => setN(n + 1)} />
    </div>
  );
}
"""

V3_CODE = """// registry: Button@v3
import { Button } from '@atom/ui/button';

export default function Screen() {
  return <Button label="조회" variant="primary" tone="brand" />;
}
"""


class FakeStream:
    def __init__(self, text: str, usage=None) -> None:
        self.text = text
        self.usage = usage or {"inputTokens": 1200, "outputTokens": 300}

    def __iter__(self):
        for i in range(0, len(self.text), 9):
            yield self.text[i:i + 9]


def make_generate(responses):
    calls = []

    def gen(system, user, max_tokens):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return FakeStream(responses[min(len(calls) - 1, len(responses) - 1)])
    gen.calls = calls
    return gen


def passing_gates(payload):
    return {k: {"ok": True, "errors": []} for k in ("build", "types", "lint")} | {
        "a11y": {"ok": True, "violations": []},
        "visual": {"ok": True, "changed": None, "note": "구조 스냅샷", "snapshot": {"hash": "abc"}},
        "runner": "fake", "payload": payload}


def failing_gates(payload):
    g = passing_gates(payload)
    g["types"] = {"ok": False, "errors": [{"file": "Screen.tsx", "line": 5, "message": "Property 'variant' does not exist on type 'ButtonProps'."}]}
    return g


# ---------------------------------------------------------------- 프롬프트
def test_system_prompt_includes_only_approved_components():
    skills, missing = agent.load_skills()
    assert not missing and set(skills) == set(agent.SKILL_NAMES)
    p = agent.build_system_prompt(APPROVED, skills)
    assert "Button@v2" in p and "import { Button } from '@atom/ui/button'" in p
    assert "DataTable@v1" in p and "PageHeader@v1" in p
    assert '"ghost"' in p and '"required":["label"]' in p         # v2 propsSchema 원문 (kind enum)
    assert "Button@v3" not in p and '"variant"' not in p          # 승인 대기 버전은 언급조차 없다 (스킬 포함)
    assert "KWCAG" in p and "// registry:" in p                   # 스킬 원문 + 출력 계약


def test_system_prompt_flips_with_approval_state():
    skills, _ = agent.load_skills()
    p = agent.build_system_prompt(V3_APPROVED, skills)
    assert "Button@v3" in p and '"variant"' in p
    assert "Button@v2" not in p and '"ghost"' not in p            # v2 전용 enum 값이 사라진다 (Alert 의 kind 는 별개)


def test_user_prompt_appends_failures_once():
    u = agent.build_user_prompt("여신 심사 결과 조회 화면을 만들어줘")
    assert u.startswith("요청: 여신") and "실패 사유" not in u
    u2 = agent.build_user_prompt("x", ["types: Screen.tsx:5 Property 'variant' does not exist"], "// prev")
    assert "실패 사유" in u2 and "variant" in u2 and "// prev" in u2


# ---------------------------------------------------------------- 파싱
def test_extract_code_block_variants():
    text = "설명입니다.\n```tsx\n// registry: Button@v2\nexport default function Screen() { return null; }\n```\n끝."
    assert agent.extract_code_block(text).startswith("// registry: Button@v2")
    two = "```ts\nconst helper = 1;\n```\n\n```tsx\nimport x from 'y';\nexport default function Screen() {}\n```"
    assert "export default" in agent.extract_code_block(two)
    truncated = "```tsx\n// registry: Button@v2\nimport { Button } from '@atom/ui/button';"
    assert agent.extract_code_block(truncated).startswith("// registry:")
    assert agent.extract_code_block("// registry: A@v1\nexport default function Screen() {}") != ""
    assert agent.extract_code_block("코드가 없습니다.") == ""


def test_parse_registry_header():
    assert agent.parse_registry_header("// registry: Button@v2, DataTable@v1\nimport x") == [
        {"name": "Button", "version": "v2"}, {"name": "DataTable", "version": "v1"}]
    assert agent.parse_registry_header("//registry:Button @ 2") == [{"name": "Button", "version": "v2"}]
    assert agent.parse_registry_header("import x from 'y';") == []
    assert agent.header_is_first_line(GOOD_CODE) is True
    assert agent.header_is_first_line("import a from 'b';\n// registry: A@v1") is False


def test_parse_imports():
    imps = agent.parse_imports(GOOD_CODE)
    mods = {i["module"]: i["names"] for i in imps}
    assert mods["react"] == ["useState"]
    assert mods["@atom/ui/button"] == ["Button"] and mods["@atom/ui/page-header"] == ["PageHeader"]
    ns = agent.parse_imports("import * as UI from '@atom/ui';\nimport dayjs from 'dayjs';\nimport 'side';")
    assert ns[0]["namespace"] == "UI" and ns[1]["default"] == "dayjs" and ns[2]["module"] == "side"


# ---------------------------------------------------------------- Registry 게이트
def test_registry_check_passes_valid_code():
    r = agent.check_registry_usage(GOOD_CODE, APPROVED)
    assert r["ok"] is True and r["problems"] == []
    assert r["used"] == [{"name": "Button", "version": "v2", "module": "@atom/ui/button"},
                         {"name": "PageHeader", "version": "v1", "module": "@atom/ui/page-header"}]


def test_registry_check_flags_unapproved_version():
    r = agent.check_registry_usage(V3_CODE, APPROVED)
    assert r["ok"] is False
    assert any("Button@v3" in p and "Button@v2" in p for p in r["problems"]), r["problems"]
    assert r["used"] == []
    # 반전 상태에서는 같은 코드가 통과하고, v2 코드는 실패한다
    assert agent.check_registry_usage(V3_CODE, V3_APPROVED)["ok"] is True
    r2 = agent.check_registry_usage(GOOD_CODE, V3_APPROVED)
    assert r2["ok"] is False and any("Button@v2" in p and "Button@v3" in p for p in r2["problems"])


def test_registry_check_flags_bad_imports_and_header_gaps():
    code = ("// registry: Button@v2\nimport axios from 'axios';\nimport { Button } from '@atom/ui/button';\n"
            "import { Badge } from '@atom/ui/badge';\nexport default function Screen() { return null; }")
    r = agent.check_registry_usage(code, APPROVED)
    assert r["ok"] is False
    joined = "\n".join(r["problems"])
    assert "'axios'" in joined and "Badge" in joined and "헤더에 없습니다" in joined
    r2 = agent.check_registry_usage("import { Button } from '@atom/ui/button';\nexport default function Screen() {}", APPROVED)
    assert any("헤더가 없습니다" in p for p in r2["problems"])
    r3 = agent.check_registry_usage("// registry: Ghost@v9\nimport { Ghost } from '@atom/ui/ghost';", APPROVED)
    assert any("승인 목록에 없습니다" in p for p in r3["problems"])


def test_select_components_for_gates_prefers_header_version():
    both = APPROVED + [c for c in V3_APPROVED if c["name"] == "Button"]
    sel = agent.select_components_for_gates(both, [{"name": "Button", "version": "v2", "module": "@atom/ui/button"}])
    btn = [c for c in sel if c["module"] == "@atom/ui/button"]
    assert len(btn) == 1 and btn[0]["version"] == "v2"
    sel2 = agent.select_components_for_gates(both, [])
    assert [c for c in sel2 if c["module"] == "@atom/ui/button"][0]["version"] == "v3"   # 헤더 없으면 최신 승인
    assert {"name", "version", "module", "exportName", "propsSchema"} <= set(sel2[0])


def test_describe_props_and_normalize():
    btn = next(c for c in APPROVED if c["name"] == "Button")
    props = {p["name"]: p for p in btn["props"]}
    assert props["label"]["required"] is True and props["label"]["type"] == "string"
    assert props["kind"]["type"] == '"primary" | "secondary" | "ghost"' and props["onClick"]["type"] == "fn"
    barrel = agent.normalize_component({"name": "DataTable", "recordVersion": "v1", "payload": {"import": "@atom/ui"}})
    assert barrel["module"] == "@atom/ui/data-table" and barrel["exportName"] == "DataTable"


# ---------------------------------------------------------------- 파이프라인 · 재시도 정책
def test_retry_policy_regenerates_at_most_once():
    assert agent.MAX_REGENERATIONS == 1 and agent.MAX_ATTEMPTS == 2
    gen = make_generate(["```tsx\n" + V3_CODE + "```"])           # 항상 미승인 버전
    em = agent.ListEmitter()
    res = agent.run("여신 심사 결과 조회 화면", em, components=APPROVED, components_source="test",
                    generate=gen, gates_runner=passing_gates)
    assert res["attempts"] == 2 and len(gen.calls) == 2 and res["ok"] is False and res["regenerated"] is True
    steps = [s["step"] for s in em.stages]
    assert steps == ["registry_lookup", "skills", "generate", "gates", "regenerate", "generate", "gates"]
    regen = next(s for s in em.stages if s["step"] == "regenerate")
    assert regen["limit"] == 1 and any("Button@v3" in r for r in regen["reasons"])
    assert "실패 사유" in gen.calls[1]["user"] and "Button@v3" in gen.calls[1]["user"]
    assert "실패 사유" not in gen.calls[0]["user"]
    assert res["usage"] == {"inputTokens": 2400, "outputTokens": 600}      # 두 시도 합산
    assert len(res["history"]) == 1 and res["history"][0]["attempt"] == 1
    assert "".join(em.tokens).count("// registry") == 2                      # 토큰이 두 번 스트리밍됨


def test_retry_succeeds_on_second_attempt_with_failure_context():
    gen = make_generate(["```tsx\n" + V3_CODE + "```", "```tsx\n" + GOOD_CODE + "```"])
    em = agent.ListEmitter()
    res = agent.run("x", em, components=APPROVED, generate=gen, gates_runner=passing_gates)
    assert res["attempts"] == 2 and res["ok"] is True
    assert res["componentsUsed"][0] == {"name": "Button", "version": "v2", "module": "@atom/ui/button"}
    assert res["gates"]["registry"]["ok"] is True and res["gates"]["types"]["ok"] is True
    assert res["reasons"] == []


def test_gate_failure_triggers_single_regeneration_with_diagnostics():
    calls = {"n": 0}

    def gates(payload):
        calls["n"] += 1
        return failing_gates(payload) if calls["n"] == 1 else passing_gates(payload)
    gen = make_generate(["```tsx\n" + GOOD_CODE + "```"])
    em = agent.ListEmitter()
    res = agent.run("x", em, components=APPROVED, generate=gen, gates_runner=gates)
    assert res["attempts"] == 2 and res["ok"] is True
    regen = next(s for s in em.stages if s["step"] == "regenerate")
    assert regen["reasons"] == ["types: Screen.tsx:5 Property 'variant' does not exist on type 'ButtonProps'."]
    payload = res["gates"]["payload"]
    assert payload["filename"] == "Screen.tsx" and payload["code"].startswith("// registry: Button@v2")
    assert {c["name"] for c in payload["components"]} == {c["name"] for c in APPROVED}


def test_no_regeneration_when_gates_unavailable_and_never_faked():
    gen = make_generate(["```tsx\n" + GOOD_CODE + "```"])
    em = agent.ListEmitter()
    res = agent.run("x", em, components=APPROVED, generate=gen, gates_runner=lambda p: agent.unavailable_gates())
    assert res["attempts"] == 1 and res["ok"] is None
    for k in agent.GATE_KEYS:
        assert res["gates"][k] == {"ok": None, "note": "게이트 실행기 미연결"}
    assert res["gates"]["registry"]["ok"] is True          # Registry 게이트는 Python 에서 실제로 검사한다


def test_gates_runner_error_is_reported_not_raised():
    def boom(payload):
        raise agent.GatesError("Task timed out after 120.00 seconds")
    res = agent.run("x", agent.ListEmitter(), components=APPROVED,
                    generate=make_generate(["```tsx\n" + GOOD_CODE + "```"]), gates_runner=boom)
    assert res["ok"] is None and res["attempts"] == 1
    assert "게이트 실행기 오류" in res["gates"]["types"]["note"] and res["gates"]["runner"] == "error"


def test_missing_code_block_counts_as_failure_and_retries_once():
    res = agent.run("x", agent.ListEmitter(), components=APPROVED,
                    generate=make_generate(["코드를 만들 수 없습니다."]), gates_runner=passing_gates)
    assert res["attempts"] == 2 and res["ok"] is False and res["code"] == ""
    assert res["gates"]["build"]["ok"] is False


def test_outbound_scan_hook_is_applied_per_attempt():
    seen = []

    def scan(text):
        seen.append(len(text))
        return {"count": 0, "detectors": ["rules"]}
    res = agent.run("x", agent.ListEmitter(), components=APPROVED, outbound_scan=scan,
                    generate=make_generate(["```tsx\n" + GOOD_CODE + "```"]), gates_runner=passing_gates)
    assert len(seen) == 1 and res["piiOutbound"] == 0 and res["outbound"]["detectors"] == ["rules"]


def test_gates_runner_mode_none_without_env_or_node_modules(monkeypatch, tmp_path):
    monkeypatch.delenv("GATES_FN", raising=False)
    monkeypatch.setattr(agent, "GATES_DIR", tmp_path)
    assert agent.gates_runner_mode() == "none"
    out = agent.default_gates_runner({"code": "x"})
    assert out["build"] == {"ok": None, "note": "게이트 실행기 미연결"} and out["runner"] == "none"
    monkeypatch.setenv("GATES_FN", "arn:aws:lambda:ap-northeast-2:1:function:GatesFn")
    assert agent.gates_runner_mode() == "lambda"


def test_fixture_fallback_when_registry_module_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "registry" or name.startswith("registry."):
            raise ImportError("no registry")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    comps, source = agent.load_approved_components()
    assert source == "fixture"
    assert {(c["name"], c["version"]) for c in comps} >= {("Button", "v2"), ("DataTable", "v1"), ("Alert", "v1")}
    assert ("Button", "v3") not in {(c["name"], c["version"]) for c in comps}


# ---------------------------------------------------------------- 핸들러 (ctx 이벤트 계약)
class _FakeApigw:
    def __init__(self) -> None:
        self.sent: list = []

    def post_to_connection(self, ConnectionId, Data):
        self.sent.append(json.loads(Data.decode()))


def test_handler_streams_stages_tokens_and_done(monkeypatch):
    monkeypatch.delenv("GATES_FN", raising=False)
    from common.ctx import Ctx
    from handlers import screengen as handler
    monkeypatch.setattr(agent, "load_approved_components", lambda: (APPROVED, "registry"))
    monkeypatch.setattr(agent, "default_generate", make_generate(["```tsx\n" + GOOD_CODE + "```"]))
    monkeypatch.setattr(agent, "default_gates_runner", passing_gates)
    apigw = _FakeApigw()
    ctx = Ctx(apigw=apigw, conn_id="c1", email="demo@atomai.click", rid="r7")
    handler.handle(ctx, {"action": "screengen", "prompt": "여신 심사 결과 조회 화면을 만들어줘"})
    types = [e["type"] for e in apigw.sent]
    assert types[0] == "screengen.stage" and types[-1] == "screengen.done"
    assert "screengen.token" in types and all(e["reqId"] == "r7" for e in apigw.sent)
    steps = [e["step"] for e in apigw.sent if e["type"] == "screengen.stage"]
    assert steps == ["registry_lookup", "skills", "generate", "gates"]
    lookup = apigw.sent[0]
    assert lookup["source"] == "registry" and lookup["plane"] == "cloud"
    assert {c["name"] for c in lookup["components"]} == {c["name"] for c in APPROVED}
    done = apigw.sent[-1]
    assert done["code"].startswith("// registry: Button@v2") and done["attempts"] == 1 and done["ok"] is True
    assert done["componentsUsed"][0]["version"] == "v2" and done["gates"]["registry"]["ok"] is True
    assert set(done["gates"]) >= {"build", "types", "lint", "a11y", "visual", "registry", "ok"}
    assert done["piiOutbound"] == 0

    apigw.sent.clear()
    handler.components(ctx, {})
    assert apigw.sent[0]["type"] == "screengen_components" and apigw.sent[0]["count"] == len(APPROVED)
    assert apigw.sent[0]["gatesRunner"] in ("local", "none")


def test_handler_rejects_empty_prompt():
    from common.ctx import Ctx
    from handlers import screengen as handler
    apigw = _FakeApigw()
    handler.handle(Ctx(apigw=apigw, conn_id="c", email="e", rid="r"), {"prompt": "   "})
    assert apigw.sent == [{"type": "error", "message": "요청 문장이 비어 있습니다.", "reqId": "r", "traceId": apigw.sent[0]["traceId"]}]


# ---------------------------------------------------------------- 로컬 게이트 통합 (node_modules 있을 때만)
_GATES_READY = (agent.GATES_DIR / "node_modules").is_dir() and shutil.which("node") is not None


@pytest.mark.skipif(not _GATES_READY, reason="gates/node_modules 없음 (cd gates && npm install)")
def test_local_gates_end_to_end(monkeypatch):
    monkeypatch.delenv("GATES_FN", raising=False)
    assert agent.gates_runner_mode() == "local"
    em = agent.ListEmitter()
    res = agent.run("x", em, components=APPROVED, generate=make_generate(["```tsx\n" + GOOD_CODE + "```"]))
    g = res["gates"]
    assert g["runner"] == "local" and res["ok"] is True, json.dumps(g, ensure_ascii=False)[:1500]
    assert g["build"]["ok"] and g["types"]["ok"] and g["lint"]["ok"] and g["a11y"]["ok"] and g["visual"]["ok"]
    assert g["visual"]["snapshot"]["tagCounts"]["button"] == 1 and g["visual"]["changed"] is None
    # 반전 상태(v3 승인)에서 v2 props 코드는 Registry 게이트 + 타입 게이트가 함께 실패하고 1회 재생성한다
    gen = make_generate(["```tsx\n" + GOOD_CODE + "```"])
    res2 = agent.run("x", agent.ListEmitter(), components=V3_APPROVED, generate=gen,
                     previous_snapshot=g["visual"]["snapshot"])
    assert res2["attempts"] == 2 and res2["ok"] is False
    assert res2["gates"]["registry"]["ok"] is False and res2["gates"]["types"]["ok"] is False
    assert any("kind" in e["message"] for e in res2["gates"]["types"]["errors"])
    assert res2["gates"]["visual"]["changed"] is False       # 같은 마크업 → 구조 동일
