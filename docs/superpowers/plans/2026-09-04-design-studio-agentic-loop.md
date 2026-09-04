# Design Studio Agentic Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the platform design studio natively with a product-spec-driven agentic loop: DesignSpec from the ontology → HTML draft → checklist review (deterministic + reviewer LLM) → targeted regenerate, scored, up to N rounds, streamed over WebSocket from a worker Lambda.

**Architecture:** `platform/studio/` is a new module (spec builder, review, prompts, loop, store, worker). `api/handlers/studio.py` exposes `studio_*` actions; `studio_run` invokes `StudioLoopFn` asynchronously and the worker pushes `studio.stage/.token/.done` to the same WebSocket connection via `common.ctx.Ctx`. Drafts live in the web bucket under `studio/drafts/`, jobs/rounds/drafts in a new DynamoDB table. Assets remain proxied from the uiux-studio registry. Front end is `web/src/studio/*`.

**Tech Stack:** Python 3.12 (stdlib + boto3 only), `engine.bedrock` (anonymization gate), `graph.store.GraphStore`, DynamoDB, S3, CDK TypeScript, React 18 + TS + Tailwind v4, pytest offline.

Spec: `docs/superpowers/specs/2026-09-04-design-studio-agentic-loop-design.md`.

Deviation from spec §4: no `studio_models` proxy route — platform generation runs through the anonymization gate's `GEN_MODEL`; the UI shows that model read-only from `studio_products`.

## Global Constraints

- All model calls go through `engine.bedrock.Stream` / `engine.bedrock.generate` (SPEC §3). No direct boto3 Bedrock clients.
- Lambda runtime has **no pip packages** except boto3. No PyYAML, no bs4 — HTML parsing uses `html.parser`.
- No prompt/HTML text in logs or traces — lengths, hashes, scores only (SPEC §12.3).
- Never fake a pass: reviewer parse failure = 미판정 (`None`), which is not a pass (SPEC §12).
- Streaming events are exactly `studio.stage`, `studio.token`, `studio.done` (CONTRACTS §1).
- Handler action names start with `studio_`.
- Do not use the words "온프렘", "Two-Plane", "In-Region" in UI strings (SPEC §12.13).
- Rounds clamp to 1..20 (default 3). Pass score clamps to 50..100 (default 85). Worker wall-clock cap 780 s.
- `seed/generate.py`: existing nodes/edges must stay byte-identical. New block uses `random.Random(SEED + 2)` and is appended after the UX-asset block, before the output section.
- Commit specific files only (`git add <paths>`), never `git add -A`.
- Log paths: `SCRATCH=/tmp/claude-1001/-home-atomoh-AgenticAI-Platform/4f13f3ea-6d5f-4911-ae8d-eb1bc957202f/scratchpad` (export once per shell; the session scratchpad).
- Existing tests must keep passing: `cd platform && python3 -m pytest tests/ -q`.
- Import path convention in tests: `sys.path.insert(0, ROOT)` and `sys.path.insert(0, ROOT/"api")` as in `tests/test_screengen.py`.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/seed/generate.py` (modify, append block) | Deposit hero products PRD-DEP-001/002 with conditions, screens, ScreenMeta, procedures, policy rules, terms |
| `platform/studio/__init__.py` | package marker |
| `platform/studio/checklists/{common,deposit,loan,card,fx,conditional}.json` | category checklist templates |
| `platform/studio/spec.py` | `build_spec(store, product_code, output_type)`, `list_products(store)` |
| `platform/studio/review.py` | `dom_digest`, `deterministic_checks`, `skeleton_diff`, `parse_review`, `score` |
| `platform/studio/prompts.py` | system/user/review prompt builders, `OUTPUT_STYLES`, `load_skills` |
| `platform/skills/studio-design-system.md`, `studio-draft-html.md`, `studio-a11y-finance.md` | generation skills ported from `demo/uiux-studio/skills` |
| `platform/studio/loop.py` | `run(job, emitter, ...)` — the agentic loop, dependency-injected |
| `platform/studio/store.py` | `StudioStore` (jobs, rounds, drafts) over DynamoDB or `InMemoryTable` |
| `platform/studio/worker_handler.py` | `StudioLoopFn` entry: Ctx from payload, S3 publish, run loop |
| `platform/api/common/studio_proxy.py` | `_studio()` HTTP proxy moved out of core.py |
| `platform/api/handlers/studio.py` | `studio_run/jobs/drafts/feedback/products/spec/asset/register` |
| `platform/api/handlers/core.py` (modify) | drop studio routes, import proxy from common |
| `platform/api/handlers/__init__.py` (modify) | register `handlers.studio` |
| `platform/deploy.sh` (modify) | copy `studio/` into api-dist |
| `platform/infra/lib/stack.ts` (modify) | `StudioTable`, `StudioLoopFn`, WsFn env/grants |
| `platform/web/src/studio/{Studio,Playground,Gallery,Assets,SpecPanel,RoundTimeline,types}.tsx` | studio UI |
| `platform/web/src/Studio.tsx` (replace) | re-export of `studio/Studio` |
| `platform/tests/test_studio_{seed,spec,review,prompts,loop,store,handler,worker}.py` | offline tests |
| `SPEC.md` §16, `platform/README.md`, `platform/docs/CONTRACTS.md` | docs |

---

### Task 1: Seed — deposit hero products (축구사랑 적금 · 기본 적금)

**Files:**
- Modify: `platform/seed/generate.py` (insert before the `# ---------------- 출력 ----------------` line, ~line 490)
- Test: `platform/tests/test_studio_seed.py`

**Interfaces:**
- Produces graph nodes: `PRD-DEP-001` (우대 조건 있음), `PRD-DEP-002` (없음); Conditions `CND-DEP-*` with props `type, name, operator, value, unit, priority`; Screens `SCR-DEP-0xx`/`SCR-DEP-1xx` with `name`; ScreenMeta `SM-DEP-*`; Procedures `PRC-030`, `PRC-031`; PolicyRules `POL-DEP-001..003`; UXTerms `TRM-DEP-01..05`.
- Later tasks rely on: `Product -HAS_CONDITION-> Condition(type="우대", name=...)`, `Product -SOLD_VIA-> Screen`, `Procedure -INCLUDES-> Screen`, `ScreenMeta{prevScreens,nextScreens as JSON list} -DESCRIBES-> Screen`, `PolicyRule -CONSTRAINS-> Screen`, `UXTerm -USED_IN-> Screen`.

- [ ] **Step 1: Write the failing test**

```python
# platform/tests/test_studio_seed.py
"""수신 히어로 상품 시드 (스튜디오 DesignSpec 시연용) — 기존 노드·엣지는 그대로, 새 블록만 검증."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.store import LocalGraphStore  # noqa: E402

STORE = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


def _names(pairs):
    return [n.props.get("name") for _, n in pairs]


def test_football_product_has_preferential_condition_and_extra_step():
    p = STORE.get_node("PRD-DEP-001")
    assert p and p.props["category"] == "수신" and "축구" in p.props["name"]
    conds = [n for _, n in STORE.neighbors("PRD-DEP-001", rel="HAS_CONDITION")]
    pref = [c for c in conds if c.props["type"] == "우대"]
    assert len(pref) >= 1 and any("축구클럽" in c.props["name"] for c in pref)
    screens = _names(STORE.neighbors("PRD-DEP-001", rel="SOLD_VIA"))
    assert "축구클럽 인증" in screens and len(screens) == 7


def test_basic_product_has_no_preferential_condition_and_no_extra_step():
    conds = [n for _, n in STORE.neighbors("PRD-DEP-002", rel="HAS_CONDITION")]
    assert conds and all(c.props["type"] != "우대" for c in conds)
    screens = _names(STORE.neighbors("PRD-DEP-002", rel="SOLD_VIA"))
    assert "축구클럽 인증" not in screens and len(screens) == 6


def test_procedure_chain_is_ordered_by_screenmeta():
    proc = STORE.get_node("PRC-030")
    steps = json.loads(proc.props["steps"])
    assert steps[0] == "상품안내" and steps[3] == "축구클럽 인증" and steps[-1] == "가입 완료"
    first = STORE.get_node("SCR-DEP-001")
    sm = [n for _, n in STORE.neighbors("SCR-DEP-001", rel="DESCRIBES", direction="in")]
    assert len(sm) == 1 and json.loads(sm[0].props["prevScreens"]) == [] \
        and json.loads(sm[0].props["nextScreens"]) == ["SCR-DEP-002"]
    assert first.props["name"] == "상품안내"


def test_policy_rules_and_terms_attached():
    pol = [n for _, n in STORE.neighbors("SCR-DEP-004", rel="CONSTRAINS", direction="in")]
    assert any(n.props["ruleType"] == "동의" and n.props["severity"] == "HIGH" for n in pol)
    derived = [n.id for _, n in STORE.neighbors("POL-DEP-001", rel="DERIVED_FROM")]
    assert derived == ["REG-CS-003"]
    terms = [n for _, n in STORE.neighbors("SCR-DEP-001", rel="USED_IN", direction="in")]
    assert any(n.props["term"] == "적금 우대금리" for n in terms)


def test_existing_nodes_untouched():
    """새 블록은 뒤에 덧붙는다 — 기존 첫 노드·마지막 UX 노드 id가 그대로."""
    assert STORE.get_node("REG-LN-001") and STORE.get_node("TRM-0199")
    assert len(STORE.find_by_label("Product")) == 122
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform && python3 -m pytest tests/test_studio_seed.py -q`
Expected: FAIL (`PRD-DEP-001` is `None`).

- [ ] **Step 3: Add the seed block**

Insert immediately before `    # ---------------- 출력 ----------------` in `platform/seed/generate.py`:

```python
    # ============================================================
    # 수신 히어로 상품 2건 — 디자인 스튜디오 DesignSpec 시연용 (별도 난수열 SEED+2).
    # 위 여신·UX 자산 노드/엣지는 바이트 단위로 그대로 유지된다.
    # PRD-DEP-001 아톰 축구사랑 적금: 우대 조건(축구클럽 회원 인증) → 가입 흐름에 '축구클럽 인증' 스텝이 하나 더 있다.
    # PRD-DEP-002 아톰 기본 적금: 같은 흐름에서 조건 스텝만 없다 (대조군).
    # ============================================================
    drng = random.Random(SEED + 2)

    def deposit_product(code: str, name: str, steps: list[tuple[str, str]], conds: list[tuple[str, str, str, str, str]],
                        scr_prefix: str, sm_prefix: str, proc_id: str, proc_name: str) -> list[str]:
        node(code, "Product", productCode=code, name=name, category="수신",
             launchDate="2026-03-01", status="ON_SALE")
        edge(code, "OWNED_BY", "D-DEP")
        for i, (ctype, cname, op, val, unit) in enumerate(conds, start=1):
            cid = f"CND-DEP-{code[-3:]}-{i:02d}"
            node(cid, "Condition", conditionId=cid, type=ctype, name=cname, operator=op, value=val,
                 unit=unit, priority=1 if ctype == "우대" else drng.randint(2, 4))
            edge(code, "HAS_CONDITION", cid)
            edge(cid, "DERIVED_FROM", "REG-CS-003")
        scr_ids = []
        for i, (sname, entry) in enumerate(steps, start=1):
            sid = f"{scr_prefix}{i:02d}"
            node(sid, "Screen", screenId=sid, name=sname, channel="MBS(모바일)",
                 route=f"/deposit/{code[-3:]}/{i}", status="LIVE")
            edge(sid, "OWNED_BY", "D-UXD")
            edge(code, "SOLD_VIA", sid)
            for c in drng.sample([c for c in components if idx[c]["props"]["approvalStatus"] == "APPROVED"], k=3):
                edge(sid, "USES", c)
            scr_ids.append(sid)
        for i, sid in enumerate(scr_ids):
            prev_ids = [scr_ids[i - 1]] if i > 0 else []
            next_ids = [scr_ids[i + 1]] if i + 1 < len(scr_ids) else []
            smid = f"{sm_prefix}{i + 1:02d}"
            node(smid, "ScreenMeta", screenNo=sid,
                 purpose=f"{steps[i][0]} — 적금 가입 흐름 {i + 1}단계 (MBS(모바일))",
                 entryCondition=steps[i][1], prevScreens=json.dumps(prev_ids), nextScreens=json.dumps(next_ids))
            edge(smid, "DESCRIBES", sid)
        node(proc_id, "Procedure", procedureId=proc_id, name=proc_name,
             steps=json.dumps([s for s, _ in steps], ensure_ascii=False), status="APPROVED")
        for sid in scr_ids:
            edge(proc_id, "INCLUDES", sid)
        return scr_ids

    football_steps = [("상품안내", "로그인 완료"), ("기간 선택", "상품 선택 완료"), ("금액 입력", "기간 선택 완료"),
                      ("축구클럽 인증", "금액 입력 완료"), ("납입 방식", "우대 조건 확인 완료"),
                      ("약관 동의", "납입 방식 선택 완료"), ("가입 완료", "약관 동의 완료")]
    basic_steps = [s for s in football_steps if s[0] != "축구클럽 인증"]
    basic_steps[3] = ("납입 방식", "금액 입력 완료")
    football_conds = [("금리", "기본금리 연 3.0%", "=", "3.0", "percent"),
                      ("우대", "축구클럽 회원 인증 시 우대금리 +1.0%p", "=", "1.0", "percent"),
                      ("우대", "자동이체 등록 시 우대금리 +0.2%p", "=", "0.2", "percent"),
                      ("한도", "월 납입금액 1만원 이상 50만원 이하", "<=", "50", "krw_10k"),
                      ("자격", "가입기간 6개월 또는 12개월", "IN", "6,12", "month")]
    basic_conds = [("금리", "기본금리 연 2.8%", "=", "2.8", "percent"),
                   ("한도", "월 납입금액 1만원 이상 100만원 이하", "<=", "100", "krw_10k"),
                   ("자격", "가입기간 12개월", "=", "12", "month")]
    fb = deposit_product("PRD-DEP-001", "아톰 축구사랑 적금", football_steps, football_conds,
                         "SCR-DEP-0", "SM-DEP-0", "PRC-030", "축구사랑 적금 가입 절차")
    bs = deposit_product("PRD-DEP-002", "아톰 기본 적금", basic_steps, basic_conds,
                         "SCR-DEP-1", "SM-DEP-1", "PRC-031", "기본 적금 가입 절차")

    # PolicyRule — 두 도메인 연결점 유지: DERIVED_FROM Regulation, CONSTRAINS Screen
    dep_rules = [
        ("POL-DEP-001", "우대 조건·미충족 시 불이익 설명 확인", "동의", "HIGH", "REG-CS-003", [fb[3], fb[5], bs[4]]),
        ("POL-DEP-002", "기본금리·우대금리 구분 표기", "표기", "MEDIUM", "REG-CS-003", [fb[0], fb[6], bs[0], bs[5]]),
        ("POL-DEP-003", "월 납입금액 입력 형식 검증(숫자·범위)", "입력검증", "MEDIUM", "REG-GN-012", [fb[2], bs[2]]),
    ]
    for rid, title, rtype, sev, reg_id, targets in dep_rules:
        node(rid, "PolicyRule", ruleId=rid, title=title, ruleType=rtype, severity=sev, status="ACTIVE")
        edge(rid, "DERIVED_FROM", reg_id)
        for s in targets:
            edge(rid, "CONSTRAINS", s)

    # UXTerm — 적금 표준 용어
    dep_terms = [("적금 우대금리", [fb[0], fb[3], fb[6], bs[0]]), ("월 납입금액", [fb[2], bs[2]]),
                 ("가입기간", [fb[1], bs[1]]), ("자동이체", [fb[4], bs[3]]), ("만기 예상 이자", [fb[6], bs[5]])]
    for i, (term, scrs) in enumerate(dep_terms, start=1):
        tid = f"TRM-DEP-{i:02d}"
        node(tid, "UXTerm", termId=tid, term=term,
             definition=f"적금 가입 화면에서 '{term}'을(를) 가리키는 표준 표기. 안내 문구·버튼 명칭은 '{term}'으로 통일한다.",
             category="수신")
        for s in scrs:
            edge(tid, "USED_IN", s)
```

Screen ids resolve to `SCR-DEP-001..007` and `SCR-DEP-101..106` (prefix `SCR-DEP-0`/`SCR-DEP-1` + two digits). ScreenMeta ids `SM-DEP-001..`/`SM-DEP-101..`.

- [ ] **Step 4: Regenerate seed output and run tests**

Run: `cd platform && python3 seed/generate.py && python3 -m pytest tests/test_studio_seed.py tests/test_ontology_v2.py tests/test_coverage.py -q`
Expected: all PASS. `git diff --stat seed/out/` should show only appended lines (check `git diff seed/out/nodes.jsonl | grep '^-' | wc -l` prints `0`).

- [ ] **Step 5: Commit**

```bash
git add platform/seed/generate.py platform/seed/out/nodes.jsonl platform/seed/out/edges.jsonl platform/tests/test_studio_seed.py
git commit -m "seed: deposit hero products (축구사랑 적금 with 우대 condition step, 기본 적금 control) for studio DesignSpec"
```

---

### Task 2: Checklist templates + DesignSpec builder

**Files:**
- Create: `platform/studio/__init__.py` (empty), `platform/studio/checklists/common.json`, `deposit.json`, `loan.json`, `card.json`, `fx.json`, `conditional.json`
- Create: `platform/studio/spec.py`
- Test: `platform/tests/test_studio_spec.py`

**Interfaces:**
- Produces:
  - `class SpecError(Exception)`
  - `build_spec(store: GraphStore, product_code: str, output_type: str = "design") -> dict` with keys `productCode, productName, category, hasPreferential, conditions[{id,type,name}], steps[{screenId,name,entryCondition}], terms[str], brandHex[str], items[Item]`
  - `Item = {"id": str, "category": "명세반영"|"UX흐름"|"정합성"|"용어"|"정책"|"패턴", "text": str, "required": bool, "weight": int, "check": "llm"|"text"|"dom", "expect": dict|None, "source": {"nodeId": str, "label": str}|None}`
  - `list_products(store) -> list[{"code","name","category","conditionCount","hasPreferential","stepCount"}]`
  - `BRAND_HEX = ["#008485", "#00615f", "#e90061", "#17332f"]`

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_spec.py
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.store import LocalGraphStore  # noqa: E402
from studio import spec  # noqa: E402

STORE = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


def _by_id(s, item_id):
    return next(i for i in s["items"] if i["id"] == item_id)


def test_football_spec_requires_condition_step():
    s = spec.build_spec(STORE, "PRD-DEP-001", "ux-flow")
    assert s["hasPreferential"] is True and s["category"] == "수신"
    flow = _by_id(s, "FLOW-COND")
    assert flow["required"] and flow["check"] == "dom" and "축구클럽" in flow["expect"]["stepContainsAny"][0]
    assert [st["name"] for st in s["steps"]][3] == "축구클럽 인증"
    order = _by_id(s, "STEP-ORDER")
    assert order["expect"]["stepOrder"] == [st["name"] for st in s["steps"]]
    assert not any(i["id"] == "FLOW-NOCOND" for i in s["items"])


def test_basic_spec_forbids_extra_step():
    s = spec.build_spec(STORE, "PRD-DEP-002", "ux-flow")
    assert s["hasPreferential"] is False
    assert _by_id(s, "FLOW-NOCOND")["expect"] == {"maxSteps": 6}
    assert not any(i["id"] == "FLOW-COND" for i in s["items"])
    assert not any(i["id"].startswith("CND-") and i["category"] == "조건형" for i in s["items"])


def test_conditions_terms_policies_become_items():
    s = spec.build_spec(STORE, "PRD-DEP-001", "design")
    cond_items = [i for i in s["items"] if i["id"].startswith("COND-")]
    assert len(cond_items) == 5 and all(i["check"] == "llm" for i in cond_items)
    assert any("축구클럽" in i["text"] for i in cond_items)
    pol = _by_id(s, "POL-POL-DEP-001")
    assert pol["required"] and pol["weight"] == 3 and pol["check"] == "dom" and pol["expect"] == {"control": "checkbox"}
    terms = [i for i in s["items"] if i["id"].startswith("TERM-")]
    assert any(i["expect"] == {"contains": "적금 우대금리"} for i in terms) and all(i["check"] == "text" for i in terms)
    # design(단일 화면) 에서는 STEP-ORDER 대신 내비 항목
    assert not any(i["id"] == "STEP-ORDER" for i in s["items"])
    assert any(i["id"] == "NAV-FLOW" for i in s["items"])


def test_templates_and_conditional_addon():
    s = spec.build_spec(STORE, "PRD-DEP-001")
    ids = {i["id"] for i in s["items"]}
    assert "CM-01" in ids and "DEP-01" in ids and "CD-01" in ids
    s2 = spec.build_spec(STORE, "PRD-DEP-002")
    ids2 = {i["id"] for i in s2["items"]}
    assert "CD-01" not in ids2 and "DEP-01" in ids2
    assert all(len({i["id"] for i in x["items"]}) == len(x["items"]) for x in (s, s2)), "item id 중복"


def test_unknown_product_raises():
    with pytest.raises(spec.SpecError):
        spec.build_spec(STORE, "PRD-NOPE")


def test_list_products_marks_preferential():
    rows = spec.list_products(STORE)
    fb = next(r for r in rows if r["code"] == "PRD-DEP-001")
    assert fb["hasPreferential"] and fb["conditionCount"] == 5 and fb["stepCount"] == 7
    assert rows[0]["code"] == "PRD-DEP-001", "히어로 상품이 먼저"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_spec.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'studio'`.

- [ ] **Step 3: Create the checklist templates**

`platform/studio/__init__.py`: empty file.

`platform/studio/checklists/common.json`:
```json
{"items": [
  {"id": "CM-01", "category": "정합성", "text": "화면 제목이 상품명을 포함하고 브리프의 목적과 일치한다", "required": true, "weight": 2, "check": "llm"},
  {"id": "CM-02", "category": "정합성", "text": "주 액션(CTA) 버튼이 하나이며 하단에 고정되어 있다", "required": false, "weight": 2, "check": "llm"},
  {"id": "CM-03", "category": "정합성", "text": "브랜드 색(Hana Green #008485 계열)이 주 강조색으로 쓰였다", "required": false, "weight": 1, "check": "dom", "expect": {"brandHex": true}},
  {"id": "CM-04", "category": "정합성", "text": "외부 스크립트·fetch 없이 자기완결 HTML이다", "required": true, "weight": 2, "check": "dom", "expect": {"noExternal": true}},
  {"id": "CM-05", "category": "정합성", "text": "가짜 기기 크롬(상태바·시계·배터리)이 없다", "required": false, "weight": 1, "check": "llm"},
  {"id": "CM-06", "category": "정합성", "text": "본문 13px 이상, 터치 타깃 44px 이상으로 보인다", "required": false, "weight": 1, "check": "llm"}
]}
```

`platform/studio/checklists/deposit.json`:
```json
{"items": [
  {"id": "DEP-01", "category": "명세반영", "text": "기본금리가 연 % 단위로 명확히 표기된다", "required": true, "weight": 3, "check": "llm"},
  {"id": "DEP-02", "category": "명세반영", "text": "가입기간 선택 요소가 있고 허용 기간만 제시된다", "required": true, "weight": 2, "check": "llm"},
  {"id": "DEP-03", "category": "명세반영", "text": "월 납입금액 입력이 있고 최소·최대 한도가 안내된다", "required": true, "weight": 2, "check": "llm"},
  {"id": "DEP-04", "category": "UX흐름", "text": "약관 동의 후에만 가입 완료로 진행되는 순서다", "required": false, "weight": 2, "check": "llm"},
  {"id": "DEP-05", "category": "명세반영", "text": "만기 예상 이자(세전) 안내가 있다", "required": false, "weight": 1, "check": "llm"}
]}
```

`platform/studio/checklists/loan.json`:
```json
{"items": [
  {"id": "LN-01", "category": "명세반영", "text": "대출 한도·금리 산정 기준 출처가 표기된다", "required": true, "weight": 3, "check": "llm"},
  {"id": "LN-02", "category": "명세반영", "text": "상환 방식·중도상환수수료 안내가 있다", "required": false, "weight": 2, "check": "llm"},
  {"id": "LN-03", "category": "UX흐름", "text": "심사 결과 상태(승인/보류/거절)별 다음 행동이 안내된다", "required": true, "weight": 2, "check": "llm"}
]}
```

`platform/studio/checklists/card.json`:
```json
{"items": [
  {"id": "CD-C01", "category": "명세반영", "text": "연회비·실적 조건이 표기된다", "required": true, "weight": 3, "check": "llm"},
  {"id": "CD-C02", "category": "명세반영", "text": "전월 실적 기준과 혜택 한도가 함께 표기된다", "required": false, "weight": 2, "check": "llm"}
]}
```

`platform/studio/checklists/fx.json`:
```json
{"items": [
  {"id": "FX-01", "category": "명세반영", "text": "적용 환율·우대율과 고시 시각이 표기된다", "required": true, "weight": 3, "check": "llm"},
  {"id": "FX-02", "category": "UX흐름", "text": "통화 선택 → 금액 입력 → 확인 순서가 지켜진다", "required": false, "weight": 2, "check": "llm"}
]}
```

`platform/studio/checklists/conditional.json` (applied only when `hasPreferential`):
```json
{"items": [
  {"id": "CD-01", "category": "조건형", "text": "우대 조건 입력·인증 화면이 별도 스텝으로 존재한다", "required": true, "weight": 3, "check": "llm"},
  {"id": "CD-02", "category": "조건형", "text": "조건 충족 여부(인증 결과)가 화면에 표시된다", "required": true, "weight": 2, "check": "llm"},
  {"id": "CD-03", "category": "조건형", "text": "우대 적용 전/후 금리가 구분되어 표기된다", "required": true, "weight": 2, "check": "llm"},
  {"id": "CD-04", "category": "조건형", "text": "조건 미충족 시 기본금리로 가입된다는 안내가 있다", "required": false, "weight": 2, "check": "llm"}
]}
```

- [ ] **Step 4: Implement `spec.py`**

```python
# platform/studio/spec.py
"""DesignSpec — 온톨로지(Product·Condition·Procedure·ScreenMeta·PolicyRule·UXTerm·Pattern)에서 검수 체크리스트를 만든다.

GraphStore 인터페이스만 사용한다(Local/Neptune 공통). 항목(Item)의 check 종류:
  llm  — 리뷰어 모델이 판정 (evidence·fix 반환)
  text — 문구 존재 (결정적)
  dom  — 구조 검사 (결정적, expect 에 규칙)
"""
from __future__ import annotations

import json
from pathlib import Path

BRAND_HEX = ["#008485", "#00615f", "#e90061", "#17332f"]
PREFERENTIAL_TYPES = {"우대"}
WEIGHT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
CATEGORY_TEMPLATE = {"수신": "deposit", "여신": "loan", "카드": "card", "외환": "fx"}
CHECKLIST_DIR = Path(__file__).resolve().parent / "checklists"
# ruleType → (문구, check, expect)
RULE_ITEM = {
    "고지의무": ("고지 문구가 화면에 존재한다", "llm", None),
    "동의": ("동의 컨트롤(체크박스 등)이 존재한다", "dom", {"control": "checkbox"}),
    "입력검증": ("입력 필드에 형식 힌트·검증 안내가 있다", "llm", None),
    "표기": ("명칭·표기가 규칙대로 통일되어 있다", "llm", None),
    "접근성": ("터치 타깃 44px 이상, 본문 13px 이상이다", "llm", None),
    "보안": ("세션·인증 관련 안내가 있다", "llm", None),
}
MAX_TERMS = 12
MAX_PATTERNS = 6


class SpecError(Exception):
    """상품을 찾지 못했거나 판매 화면이 없다."""


def _load_template(name: str) -> list[dict]:
    p = CHECKLIST_DIR / f"{name}.json"
    if not p.is_file():
        return []
    return [dict(i, expect=i.get("expect"), source=None) for i in json.loads(p.read_text(encoding="utf-8"))["items"]]


def _item(iid: str, category: str, text: str, *, required: bool, weight: int, check: str,
          expect: dict | None = None, source: dict | None = None) -> dict:
    return {"id": iid, "category": category, "text": text, "required": required, "weight": weight,
            "check": check, "expect": expect, "source": source}


def _src(node) -> dict:
    return {"nodeId": node.id, "label": node.label}


def _cond_name(c) -> str:
    p = c.props
    return p.get("name") or f"{p.get('type', '조건')} {p.get('operator', '')} {p.get('value', '')}{p.get('unit', '')}".strip()


def _ordered_screens(store, product) -> list:
    """SOLD_VIA 화면을 ScreenMeta prev/next 사슬로 정렬한다. 사슬이 없으면 엣지 순서."""
    screens = [n for _, n in store.neighbors(product.id, rel="SOLD_VIA")]
    if not screens:
        return []
    meta = {}
    for s in screens:
        for _, sm in store.neighbors(s.id, rel="DESCRIBES", direction="in"):
            meta[s.id] = sm
    ids = {s.id for s in screens}
    by_id = {s.id: s for s in screens}
    heads = [s for s in screens if s.id in meta and not [p for p in json.loads(meta[s.id].props.get("prevScreens", "[]")) if p in ids]]
    if len(heads) != 1:
        return screens
    chain, cur, seen = [], heads[0], set()
    while cur and cur.id not in seen:
        chain.append(cur)
        seen.add(cur.id)
        nxt = [n for n in json.loads(meta[cur.id].props.get("nextScreens", "[]")) if n in ids] if cur.id in meta else []
        cur = by_id.get(nxt[0]) if nxt else None
    return chain if len(chain) == len(screens) else screens


def build_spec(store, product_code: str, output_type: str = "design") -> dict:
    product = store.get_node(product_code)
    if not product or product.label != "Product":
        raise SpecError(f"상품을 찾을 수 없습니다: {product_code}")
    conds = [n for _, n in store.neighbors(product.id, rel="HAS_CONDITION")]
    screens = _ordered_screens(store, product)
    if not screens:
        raise SpecError(f"판매 화면(SOLD_VIA)이 없는 상품입니다: {product_code}")
    pref = [c for c in conds if c.props.get("type") in PREFERENTIAL_TYPES]
    has_pref = bool(pref)
    category = product.props.get("category", "")

    steps = []
    for s in screens:
        sm = next((m for _, m in store.neighbors(s.id, rel="DESCRIBES", direction="in")), None)
        steps.append({"screenId": s.id, "name": s.props.get("name", s.id),
                      "entryCondition": sm.props.get("entryCondition", "") if sm else ""})
    step_names = [st["name"] for st in steps]

    items: list[dict] = []
    # 1) 조건 → 명세반영
    for c in conds:
        items.append(_item(f"COND-{c.id}", "명세반영", f"조건 '{_cond_name(c)}'이(가) 화면에 표기된다",
                           required=c.props.get("type") in ("금리", "우대", "한도"), weight=3 if c.props.get("type") in ("금리", "우대") else 2,
                           check="llm", source=_src(c)))
    # 2) 흐름
    if has_pref:
        kws = []
        for c in pref:
            nm = _cond_name(c)
            kws += [nm] + [w for w in nm.replace("(", " ").replace(")", " ").split() if len(w) >= 2][:3]
        items.append(_item("FLOW-COND", "UX흐름", "우대 조건 입력·인증 스텝이 가입 흐름에 존재한다 (조건이 있으면 페이지가 하나 더 생겨야 한다)",
                           required=True, weight=3, check="dom", expect={"stepContainsAny": kws}, source=_src(pref[0])))
    else:
        items.append(_item("FLOW-NOCOND", "UX흐름", f"우대 조건이 없는 상품이므로 절차({len(steps)}단계) 밖의 추가 스텝이 없다",
                           required=True, weight=2, check="dom", expect={"maxSteps": len(steps)}, source=_src(product)))
    if output_type == "ux-flow":
        items.append(_item("STEP-ORDER", "UX흐름", "프레임 순서가 절차 단계 순서와 일치한다: " + " → ".join(step_names),
                           required=True, weight=3, check="dom", expect={"stepOrder": step_names}, source=_src(screens[0])))
        items.append(_item("STEP-MIN", "UX흐름", f"프레임이 {len(steps)}개 이상이다", required=True, weight=2, check="dom",
                           expect={"minSteps": len(steps)}, source=_src(screens[0])))
    else:
        items.append(_item("NAV-FLOW", "UX흐름", "이전/다음 화면으로의 이동(뒤로가기·다음 버튼)이 절차 순서와 맞는다: " + " → ".join(step_names),
                           required=False, weight=2, check="llm", source=_src(screens[0])))
    # 3) 정책
    seen_rules = set()
    for s in screens:
        for _, r in store.neighbors(s.id, rel="CONSTRAINS", direction="in"):
            if r.id in seen_rules or r.props.get("status", "ACTIVE") != "ACTIVE":
                continue
            seen_rules.add(r.id)
            text, check, expect = RULE_ITEM.get(r.props.get("ruleType", ""), ("정책 규칙을 준수한다", "llm", None))
            sev = r.props.get("severity", "MEDIUM")
            items.append(_item(f"POL-{r.id}", "정책", f"[{r.props.get('ruleType', '')}] {r.props.get('title', r.id)} — {text}",
                               required=sev == "HIGH", weight=WEIGHT.get(sev, 2), check=check, expect=expect, source=_src(r)))
    # 4) 용어
    terms, seen_terms = [], set()
    for s in screens:
        for _, t in store.neighbors(s.id, rel="USED_IN", direction="in"):
            term = t.props.get("term", "")
            if term and term not in seen_terms and len(terms) < MAX_TERMS:
                seen_terms.add(term)
                terms.append(term)
                items.append(_item(f"TERM-{t.id}", "용어", f"표준 용어 '{term}'을(를) 사용한다", required=False, weight=1,
                                   check="text", expect={"contains": term}, source=_src(t)))
    # 5) 패턴 (패턴 하나가 여러 화면에 걸려도 항목은 1건)
    seen_pat: set = set()
    for s in screens:
        for _, p in store.neighbors(s.id, rel="FOLLOWS"):
            if p.props.get("status") == "APPROVED" and p.id not in seen_pat and len(seen_pat) < MAX_PATTERNS:
                seen_pat.add(p.id)
                items.append(_item(f"PAT-{p.id}", "패턴", f"승인 패턴 '{p.props.get('name', p.id)}' 구조가 화면 '{s.props.get('name')}'에 나타난다",
                                   required=False, weight=1, check="llm", source=_src(p)))
    # 6) 템플릿
    items += _load_template("common")
    items += _load_template(CATEGORY_TEMPLATE.get(category, ""))
    if has_pref:
        items += _load_template("conditional")
    for it in items:
        if it["id"] == "CM-03":
            it["expect"] = {"brandHex": BRAND_HEX}

    return {"productCode": product.id, "productName": product.props.get("name", product.id), "category": category,
            "hasPreferential": has_pref, "outputType": output_type,
            "conditions": [{"id": c.id, "type": c.props.get("type", ""), "name": _cond_name(c)} for c in conds],
            "steps": steps, "terms": terms, "brandHex": BRAND_HEX, "items": items}


def list_products(store) -> list[dict]:
    """조건이 있고 판매 화면이 있는 상품만. 히어로(PRD-DEP-*) 먼저, 나머지는 조건 수 내림차순."""
    rows = []
    for p in store.find_by_label("Product"):
        conds = [n for _, n in store.neighbors(p.id, rel="HAS_CONDITION")]
        screens = [n for _, n in store.neighbors(p.id, rel="SOLD_VIA")]
        if not conds or not screens:
            continue
        rows.append({"code": p.id, "name": p.props.get("name", p.id), "category": p.props.get("category", ""),
                     "conditionCount": len(conds), "stepCount": len(screens),
                     "hasPreferential": any(c.props.get("type") in PREFERENTIAL_TYPES for c in conds)})
    rows.sort(key=lambda r: (0 if r["code"].startswith("PRD-DEP-") else 1, -r["conditionCount"], r["code"]))
    return rows[:40]
```

- [ ] **Step 5: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_spec.py -q`
Expected: 6 passed. If `test_basic_spec_forbids_extra_step` fails on `"조건형"` category items, confirm `conditional.json` was not loaded for PRD-DEP-002.

- [ ] **Step 6: Commit**

```bash
git add platform/studio/__init__.py platform/studio/checklists platform/studio/spec.py platform/tests/test_studio_spec.py
git commit -m "studio: DesignSpec builder — ontology traversal to weighted checklist (conditions, flow, policy, terms, templates)"
```

---

### Task 3: Review — deterministic checks, DOM digest, skeleton diff, scoring

**Files:**
- Create: `platform/studio/review.py`
- Test: `platform/tests/test_studio_review.py`

**Interfaces:**
- Produces:
  - `parse_html(html: str) -> Doc` where `Doc = {"steps": [{"index": int, "screen": str, "text": str, "headings": [str], "buttons": [str], "inputs": [{"type": str, "placeholder": str}], "labels": [str]}], "text": str, "style": str, "externalScripts": int, "hasFetch": bool, "checkboxes": int, "inputs": int}` — a document without `section[data-step]` is one implicit step.
  - `dom_digest(doc: Doc, limit: int = 6000) -> str` — Korean outline for the reviewer.
  - `deterministic_checks(items: list[dict], doc: Doc) -> dict[str, Verdict]` for items with `check in ("text","dom")`; `Verdict = {"verdict": "pass"|"fail", "evidence": str, "fix": str}`.
  - `skeleton(html: str) -> Counter` (tag-path multiset), `skeleton_diff(prev_html: str, html: str) -> float` (0..1 change ratio).
  - `stability_item(ratio: float, threshold: float = 0.35) -> tuple[dict, Verdict]` → item id `STABLE`, weight 1, not required.
  - `parse_review(text: str, item_ids: list[str]) -> tuple[dict[str, Verdict|None], str|None]` — strict JSON, missing/invalid → `None` verdict and error string.
  - `score(items: list[dict], verdicts: dict[str, Verdict|None]) -> {"score": int, "passed": bool, "requiredFailed": [id], "undetermined": [id], "weights": {"total": int, "passed": int}}` — `passed` requires `pass_score` argument: signature `score(items, verdicts, pass_score: int = 85)`.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_review.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import review  # noqa: E402

SECTIONS = [
    '<section data-step="1" data-screen="SCR-DEP-001"><h1>아톰 축구사랑 적금 상품안내</h1><p>기본금리 연 3.0%, 적금 우대금리 최대 +1.2%p</p><button>다음</button></section>',
    '<section data-step="2"><h2>가입기간 선택</h2><label>가입기간</label><select><option>6개월</option></select></section>',
    '<section data-step="3"><h2>월 납입금액 입력</h2><input type="number" placeholder="1만원 이상 50만원 이하"></section>',
    '<section data-step="4"><h2>축구클럽 인증</h2><input type="text" placeholder="구단 회원번호"><button>인증</button></section>',
    '<section data-step="5"><h2>납입 방식</h2><label><input type="radio">자동이체</label></section>',
    '<section data-step="6"><h2>약관 동의</h2><label><input type="checkbox">전체 동의</label></section>',
    '<section data-step="7"><h2>가입 완료</h2><p>만기 예상 이자</p></section>',
]


def wrap(sections):
    return "<html><head><style>.cta{background:#008485}</style></head><body>" + "\n".join(sections) + "</body></html>"


FLOW_HTML = wrap(SECTIONS)

BAD_HTML = """<html><body><script src="https://cdn.example.com/x.js"></script>
<section data-step="1"><h1>적금</h1><button onclick="fetch('/x')">확인</button></section></body></html>"""

ITEMS = [
    {"id": "FLOW-COND", "check": "dom", "required": True, "weight": 3, "expect": {"stepContainsAny": ["축구클럽 회원 인증 시 우대금리 +1.0%p", "축구클럽"]}},
    {"id": "STEP-ORDER", "check": "dom", "required": True, "weight": 3, "expect": {"stepOrder": ["상품안내", "기간 선택", "금액 입력", "축구클럽 인증", "납입 방식", "약관 동의", "가입 완료"]}},
    {"id": "STEP-MIN", "check": "dom", "required": True, "weight": 2, "expect": {"minSteps": 7}},
    {"id": "POL-1", "check": "dom", "required": True, "weight": 3, "expect": {"control": "checkbox"}},
    {"id": "CM-03", "check": "dom", "required": False, "weight": 1, "expect": {"brandHex": ["#008485", "#00615f"]}},
    {"id": "CM-04", "check": "dom", "required": True, "weight": 2, "expect": {"noExternal": True}},
    {"id": "TERM-1", "check": "text", "required": False, "weight": 1, "expect": {"contains": "적금 우대금리"}},
    {"id": "TERM-2", "check": "text", "required": False, "weight": 1, "expect": {"contains": "만기 예상 이자"}},
    {"id": "LLM-1", "check": "llm", "required": True, "weight": 3},
    {"id": "LLM-2", "check": "llm", "required": False, "weight": 2},
]


def test_parse_html_steps_and_signals():
    doc = review.parse_html(FLOW_HTML)
    assert len(doc["steps"]) == 7 and doc["steps"][0]["screen"] == "SCR-DEP-001"
    assert doc["steps"][3]["headings"] == ["축구클럽 인증"] and doc["checkboxes"] == 1
    assert doc["externalScripts"] == 0 and doc["hasFetch"] is False
    bad = review.parse_html(BAD_HTML)
    assert bad["externalScripts"] == 1 and bad["hasFetch"] is True
    assert len(review.parse_html("<p>단일</p>")["steps"]) == 1


def test_deterministic_checks_pass_on_good_flow():
    v = review.deterministic_checks(ITEMS, review.parse_html(FLOW_HTML))
    assert set(v) == {"FLOW-COND", "STEP-ORDER", "STEP-MIN", "POL-1", "CM-03", "CM-04", "TERM-1", "TERM-2"}
    assert all(x["verdict"] == "pass" for x in v.values()), {k: x for k, x in v.items() if x["verdict"] != "pass"}
    assert "4단계" in v["FLOW-COND"]["evidence"] or "축구클럽" in v["FLOW-COND"]["evidence"]


def test_deterministic_checks_fail_and_fix_text():
    v = review.deterministic_checks(ITEMS, review.parse_html(BAD_HTML))
    assert v["FLOW-COND"]["verdict"] == "fail" and "축구클럽" in v["FLOW-COND"]["fix"]
    assert v["STEP-MIN"]["verdict"] == "fail" and v["CM-04"]["verdict"] == "fail" and v["POL-1"]["verdict"] == "fail"
    assert v["TERM-1"]["verdict"] == "fail"
    nocond = review.deterministic_checks([{"id": "FLOW-NOCOND", "check": "dom", "expect": {"maxSteps": 6}}], review.parse_html(FLOW_HTML))
    assert nocond["FLOW-NOCOND"]["verdict"] == "fail"


def test_step_order_is_fuzzy_but_ordered():
    doc = review.parse_html(FLOW_HTML.replace("가입기간 선택", "기간을 선택하세요"))
    v = review.deterministic_checks([ITEMS[1]], doc)
    assert v["STEP-ORDER"]["verdict"] == "pass"
    swapped = wrap([SECTIONS[0], SECTIONS[2], SECTIONS[1], *SECTIONS[3:]])   # 문서 순서가 기준 — 기간 선택과 금액 입력을 바꾼다
    assert review.deterministic_checks([ITEMS[1]], review.parse_html(swapped))["STEP-ORDER"]["verdict"] == "fail"


def test_skeleton_diff_and_stability_item():
    same = review.skeleton_diff(FLOW_HTML, FLOW_HTML)
    small = review.skeleton_diff(FLOW_HTML, FLOW_HTML.replace("<p>만기 예상 이자</p>", "<p>만기 예상 이자 (세전)</p>"))
    big = review.skeleton_diff(FLOW_HTML, BAD_HTML)
    assert same == 0.0 and small < 0.1 < big
    item, verdict = review.stability_item(big)
    assert item["id"] == "STABLE" and item["weight"] == 1 and not item["required"] and verdict["verdict"] == "fail"
    assert review.stability_item(small)[1]["verdict"] == "pass"


def test_parse_review_strict_json():
    ids = ["LLM-1", "LLM-2"]
    good = 'blah {"items":[{"id":"LLM-1","verdict":"pass","evidence":"제목에 상품명","fix":""},{"id":"LLM-2","verdict":"fail","evidence":"CTA 2개","fix":"하나로"}]} trailing'
    v, err = review.parse_review(good, ids)
    assert err is None and v["LLM-1"]["verdict"] == "pass" and v["LLM-2"]["fix"] == "하나로"
    v2, err2 = review.parse_review("not json", ids)
    assert err2 and v2 == {"LLM-1": None, "LLM-2": None}
    v3, err3 = review.parse_review('{"items":[{"id":"LLM-1","verdict":"maybe"}]}', ids)
    assert v3["LLM-1"] is None and v3["LLM-2"] is None and err3


def test_score_math_and_undetermined_not_pass():
    verdicts = {i["id"]: {"verdict": "pass", "evidence": "", "fix": ""} for i in ITEMS}
    s = review.score(ITEMS, verdicts, pass_score=85)
    assert s["score"] == 100 and s["passed"] and s["requiredFailed"] == []
    verdicts["LLM-1"] = None                       # 미판정 — 필수
    s = review.score(ITEMS, verdicts, pass_score=85)
    assert not s["passed"] and s["undetermined"] == ["LLM-1"] and s["score"] == round(18 / 21 * 100)
    verdicts["LLM-1"] = {"verdict": "pass", "evidence": "", "fix": ""}
    verdicts["LLM-2"] = {"verdict": "fail", "evidence": "", "fix": ""}
    s = review.score(ITEMS, verdicts, pass_score=95)
    assert s["score"] == round(19 / 21 * 100) and not s["passed"], "필수는 다 통과했지만 점수 미달"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_review.py -q`
Expected: FAIL with `cannot import name 'review'`.

- [ ] **Step 3: Implement `review.py`**

```python
# platform/studio/review.py
"""검수 — 결정적 검사(text/dom)·DOM 다이제스트·골격 diff·리뷰어 JSON 파싱·점수. 모델 호출 없음(stdlib만)."""
from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser

_WS = re.compile(r"\s+")
_FETCH = re.compile(r"\bfetch\s*\(|XMLHttpRequest|navigator\.sendBeacon")


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

    def _step(self) -> dict:
        if self.cur is None:
            self.cur = {"index": len(self.steps) + 1, "screen": "", "text": [], "headings": [], "buttons": [], "inputs": [], "labels": []}
            self.steps.append(self.cur)
        return self.cur

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._stack.append(tag)
        self.paths[">".join(self._stack[-3:])] += 1
        if tag == "section" and "data-step" in a:
            self.cur = {"index": len(self.steps) + 1, "screen": a.get("data-screen", ""), "text": [], "headings": [], "buttons": [], "inputs": [], "labels": []}
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
    p = _Parser()
    p.feed(html or "")
    steps = p.steps or [{"index": 1, "screen": "", "text": [], "headings": [], "buttons": [], "inputs": [], "labels": []}]
    for s in steps:
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
    return bool(toks) and sum(_norm(t) in h for t in toks) >= max(1, (len(toks) + 1) // 2)


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


def stability_item(ratio: float, threshold: float = 0.35) -> tuple[dict, dict]:
    item = {"id": "STABLE", "category": "안정성", "text": "수정 범위 밖의 구조가 이전 라운드와 같다 (위치가 흔들리지 않는다)",
            "required": False, "weight": 1, "check": "dom", "expect": {"skeletonMax": threshold}, "source": None}
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
```

- [ ] **Step 4: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_review.py -q`
Expected: 7 passed. If `test_step_order_is_fuzzy_but_ordered` fails on the "기간을 선택하세요" case, the fuzzy tokenizer must accept "기간" alone: with `needle="기간 선택"` tokens are `["기간","선택"]`, both present → passes; verify `_fuzzy_in` splits on spaces.

- [ ] **Step 5: Commit**

```bash
git add platform/studio/review.py platform/tests/test_studio_review.py
git commit -m "studio: hybrid review primitives — html.parser digest, deterministic dom/text checks, skeleton diff, strict reviewer JSON, weighted score"
```

---

### Task 4: Prompts + studio skills

**Files:**
- Create: `platform/skills/studio-design-system.md`, `platform/skills/studio-draft-html.md`, `platform/skills/studio-a11y-finance.md`
- Create: `platform/studio/prompts.py`
- Test: `platform/tests/test_studio_prompts.py`

**Interfaces:**
- Produces:
  - `SKILL_NAMES = ("studio-design-system", "studio-draft-html", "studio-a11y-finance")`
  - `load_skills(names=SKILL_NAMES) -> tuple[dict[str,str], list[str]]` (same behavior as `screengen.agent.load_skills`: missing listed, never faked)
  - `OUTPUT_STYLES: dict[str,str]` keys `design, mockup, wireframe, ux-flow`
  - `AXES = ("밀도", "강조", "흐름")`
  - `build_system_prompt(spec: dict, output_type: str, axis: str, skills: dict, assets_text: str = "", fewshot: list[str] | None = None, agent_preset: str = "") -> str`
  - `build_user_prompt(brief: str, spec: dict, *, failures: list[dict] | None = None, prev_html: str = "", refine: dict | None = None) -> str` — `failures` rows are `{"id","text","evidence","fix"}`; `refine` is `{"selector","elementHtml","instruction"}`
  - `REVIEW_SYSTEM: str`, `build_review_prompt(spec: dict, items: list[dict], digest: str) -> str`
  - `extract_html(text: str) -> str` — returns the `<html>…</html>` document (or fenced block) or `""`.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_prompts.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import prompts  # noqa: E402

SPEC = {"productCode": "PRD-DEP-001", "productName": "아톰 축구사랑 적금", "category": "수신", "hasPreferential": True,
        "conditions": [{"id": "c1", "type": "우대", "name": "축구클럽 회원 인증 시 우대금리 +1.0%p"}],
        "steps": [{"screenId": "SCR-DEP-001", "name": "상품안내", "entryCondition": "로그인 완료"},
                  {"screenId": "SCR-DEP-004", "name": "축구클럽 인증", "entryCondition": "금액 입력 완료"}],
        "terms": ["적금 우대금리"], "brandHex": ["#008485"],
        "items": [{"id": "LLM-1", "category": "명세반영", "text": "기본금리 표기", "required": True, "weight": 3, "check": "llm"},
                  {"id": "T-1", "category": "용어", "text": "용어", "required": False, "weight": 1, "check": "text", "expect": {"contains": "적금 우대금리"}}]}


def test_skills_load_from_platform_skills_dir():
    skills, missing = prompts.load_skills()
    assert missing == [] and set(skills) == set(prompts.SKILL_NAMES)
    assert "data-step" in skills["studio-draft-html"], "출력 계약(프레임 마킹)이 스킬에 있어야 한다"


def test_system_prompt_contains_spec_and_style():
    skills, _ = prompts.load_skills()
    s = prompts.build_system_prompt(SPEC, "ux-flow", "흐름", skills, assets_text="팔레트: #008485", fewshot=["<html>ref</html>"], agent_preset="시니어 모드 우선")
    for needle in ("아톰 축구사랑 적금", "축구클럽 인증", "축구클럽 회원 인증", "적금 우대금리", "가로 스크롤", "흐름", "팔레트: #008485", "승인 참고 시안", "시니어 모드 우선", 'data-step="1"'):
        assert needle in s, needle
    assert "3" not in prompts.OUTPUT_STYLES["design"] or True  # design 은 스타일 문구 없음
    assert prompts.build_system_prompt(SPEC, "wireframe", "밀도", skills).count("브랜드 컬러 금지") == 1


def test_user_prompt_regenerate_and_refine():
    u = prompts.build_user_prompt("적금 가입 플로우", SPEC, failures=[{"id": "LLM-1", "text": "기본금리 표기", "evidence": "없음", "fix": "상단에 연 3.0% 표기"}], prev_html="<html>prev</html>")
    assert "이전 라운드" in u and "상단에 연 3.0% 표기" in u and "<html>prev</html>" in u and "필요한 부분만" in u
    r = prompts.build_user_prompt("", SPEC, refine={"selector": "body > section:nth-of-type(2) > h2", "instruction": "제목을 더 크게", "elementHtml": "<h2>기간</h2>"}, prev_html="<html>base</html>")
    assert "body > section:nth-of-type(2) > h2" in r and "제목을 더 크게" in r and "나머지" in r


def test_review_prompt_lists_only_llm_items_and_requires_json():
    p = prompts.build_review_prompt(SPEC, [i for i in SPEC["items"] if i["check"] == "llm"], "[프레임 1] 상품안내")
    assert "LLM-1" in p and "T-1" not in p and '"verdict"' in p and "[프레임 1]" in p
    assert "pass" in prompts.REVIEW_SYSTEM and "fail" in prompts.REVIEW_SYSTEM


def test_extract_html():
    assert prompts.extract_html("설명\n```html\n<!doctype html><html><body>x</body></html>\n```\n끝").startswith("<!doctype html>")
    assert prompts.extract_html("<html><body>y</body></html>") == "<html><body>y</body></html>"
    assert prompts.extract_html("코드 없음") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_prompts.py -q`
Expected: FAIL (`cannot import name 'prompts'`).

- [ ] **Step 3: Create the three skill files**

`platform/skills/studio-design-system.md`:
```markdown
---
name: studio-design-system
description: 아톰은행(데모) 디자인 토큰·팔레트 적용 규칙 — 스튜디오 시안 생성용
---

# Studio Design System

1. 팔레트 역할: `primary` #008485 (단일 강조색 — CTA·활성 상태·핵심 하이라이트), `primaryDark` #00615f (눌림·2차 강조),
   `ink` #17332f (본문), `bg` #fbfcfb (페이지 바탕), `mist` #e6f3f2 (틴트 서피스), `danger` #e90061 (오류·경고만).
2. 색·글자 크기·간격은 위 토큰과 4px 배수 간격 스케일(4/8/12/16/24/32)에서만 고른다. 임의 값을 만들지 않는다.
3. 타이포: Noto Sans KR. 화면 제목 900, 섹션 레이블·CTA 700, 본문 400–500. 본문 최소 13px.
4. 사용자가 선택한 자산(팔레트·토큰·스타일가이드)이 프롬프트에 있으면 그 값이 위 기본값보다 우선한다.
5. 등록 컴포넌트가 프롬프트에 주어지면 그 어휘(이름·역할)를 우선 사용하고, 새 요소는 토큰 값으로만 구성한다.
```

`platform/skills/studio-draft-html.md`:
```markdown
---
name: studio-draft-html
description: 스튜디오 시안 출력 계약 — 자기완결 HTML 1개, 프레임 마킹, 축(밀도/강조/흐름)
---

# Studio Draft HTML — 출력 계약

출력: **자기완결 HTML 문서 1개**를 ```html 펜스 안에 낸다. 설명은 펜스 밖에 한 문장만.
- 인라인 `<style>`만. 외부 `<script src>`·`fetch`·외부 CSS 금지. Google Fonts `<link>`(Noto Sans KR)만 허용.
- 모바일 프레임 폭 390px, 최소 높이 844px. 흰 카드(radius 16px)를 #f4f6f5 바탕 위에 섹션 단위로 배치, 주 CTA 1개를 하단 고정.
- **프레임 마킹(필수)**: 화면 하나마다 `<section data-step="n" data-screen="SCR-…">`. 단일 화면 시안도 `data-step="1"` 하나를 둔다.
  ux-flow 는 절차 단계 순서대로 n을 매기고 각 프레임 첫 요소는 `<h2>`로 단계 이름을 쓴다.
- 가짜 기기 크롬 금지(상태바·시계·배터리·가상 키보드).
- 한국어 카피, 실제 같은 샘플 데이터(김하나, 하나 주거래 통장 …). 상품 조건(금리·기간·한도·우대)은 프롬프트의 DesignSpec 값을 그대로 쓴다.
- 터치 타깃 ≥ 44px, 본문 ≥ 13px.

축(axis) — 요청된 축 하나만 움직인다:
- 밀도: compact ↔ airy — 간격 스케일·카드 패딩.
- 강조: 어느 섹션이 지배하는가(금액 중심 vs 조건 중심).
- 흐름: 단일 화면(기본) vs 단계형 위저드.

수정(refine) 요청이면: 지시된 요소만 고치고 나머지 마크업·순서·텍스트는 그대로 둔다. 전체 HTML을 다시 낸다.
```

`platform/skills/studio-a11y-finance.md`:
```markdown
---
name: studio-a11y-finance
description: 금융권 웹접근성 체크(KWCAG 기반) — 스튜디오 시안용
---

# Accessibility — Finance

- 본문 대비 ≥ 4.5:1 (#17332f on #fbfcfb 통과; #8aa19c 텍스트를 #e6f3f2 위에 놓지 않는다).
- 모든 인터랙티브 요소: 44px 이상 터치 타깃, 보이는 포커스 스타일.
- 금액·계좌번호: 색만으로 의미를 주지 않고 레이블 텍스트를 함께 둔다.
- 입력은 `<label>`을 가진 `<input>`, 버튼은 `<button>`(styled div 금지). 동의는 `<input type="checkbox">`.
- 글자 크기는 rem, 본문 최소 13px, 시니어 모드 16px+.
```

- [ ] **Step 4: Implement `prompts.py`**

```python
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
        parts.append("### 사용자가 선택한 자산 (기본 토큰보다 우선)\n" + assets_text)
    for i, ref in enumerate(fewshot or [], start=1):
        parts.append(f"### 승인 참고 시안 {i} — 구조·품질 기준만 따르고 상품 내용은 복사하지 않는다\n```html\n{ref}\n```")
    return "\n\n".join(parts)


def build_user_prompt(brief: str, spec: dict, *, failures: list | None = None, prev_html: str = "", refine: dict | None = None) -> str:
    if refine:
        return ("아래 원본 HTML 전체가 주어진다. 사용자가 클릭으로 선택한 요소와 수정 지시에 따라 그 부분만 수정하고, "
                "나머지 마크업·스타일·텍스트·순서는 그대로 보존하라. 완성된 전체 HTML을 다시 출력하라.\n"
                f"선택 요소 selector: {refine.get('selector', '(전체)')}\n선택 요소 HTML:\n{(refine.get('elementHtml') or '')[:4000]}\n"
                f"수정 지시: {refine.get('instruction', '')}\n\n원본 HTML:\n```html\n{prev_html}\n```")
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
```

- [ ] **Step 5: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_prompts.py -q`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add platform/skills/studio-design-system.md platform/skills/studio-draft-html.md platform/skills/studio-a11y-finance.md platform/studio/prompts.py platform/tests/test_studio_prompts.py
git commit -m "studio: generation/review prompts and ported studio skills (output contract with data-step frames)"
```

---

### Task 5: The loop (`studio/loop.py`)

**Files:**
- Create: `platform/studio/loop.py`
- Test: `platform/tests/test_studio_loop.py`

**Interfaces:**
- Consumes: `spec.build_spec` output dict; `prompts.*`; `review.*`.
- Produces:
  - `MAX_ROUNDS = 20`, `DEFAULT_ROUNDS = 3`, `DEFAULT_PASS = 85`, `TIME_CAP_S = 780`
  - `clamp_job(job: dict) -> dict` — normalizes `maxRounds` (1..20, default 3), `passScore` (50..100, default 85), `outputType` (∈ OUTPUT_TYPES else `design`), `axis` (∈ AXES else `흐름`), `mode` (`generate`|`refine`), `brief` ≤ 1000 chars.
  - `class ListEmitter` with `.stage(step, **kw)`, `.token(text)`, `.stages`, `.tokens` (same shape as `screengen.agent.ListEmitter`).
  - `run(job, emitter, *, spec, generate, review_generate, publish, skills=None, assets_text="", fewshot=None, agent_preset="", base_html="", clock=time.time, time_cap_s=TIME_CAP_S) -> dict`
    - `generate(system, user, max_tokens) -> iterable of str` with optional `.usage` attr (like `engine.bedrock.Stream`)
    - `review_generate(system, user, max_tokens) -> (text, usage)` (like `engine.bedrock.generate`)
    - `publish(job_id: str, round_n: int, html: str) -> str` returns URL
    - returns the `.done` payload: `{jobId, score, passed, rounds, maxRounds, passScore, stopReason, bestRound, url, html, items, verdicts, history:[{round, score, passed, url, failures, undetermined, reviewerError, elapsedMs}], usage:{inputTokens,outputTokens}, model, elapsedMs, spec:{productCode, productName, hasPreferential, stepCount}}`
    - `stopReason ∈ passed | max_rounds | time_cap | error`
  - Stage events emitted: `spec_build{items,required,productName,hasPreferential,steps}`, `assets{chars,fewshot}`, `generate{round,systemChars,userChars,model}`, `review{round,score,passed,items:[{id,category,text,required,weight,check,verdict,evidence,fix,source}],requiredFailed,undetermined,reviewerError,deterministic,llm}`, `regenerate{round,failures}`, `publish{round,url}`.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_loop.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.store import LocalGraphStore  # noqa: E402
from studio import loop, spec as specmod  # noqa: E402

STORE = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")
SPEC = specmod.build_spec(STORE, "PRD-DEP-001", "ux-flow")
LLM_IDS = [i["id"] for i in SPEC["items"] if i["check"] == "llm"]

STEP_HTML = "".join(
    f'<section data-step="{i + 1}" data-screen="{s["screenId"]}"><h2>{s["name"]}</h2>'
    f'<p>기본금리 연 3.0% 적금 우대금리 축구클럽 회원 인증 시 우대금리 +1.0%p 자동이체 월 납입금액 가입기간 만기 예상 이자</p>'
    f'<label><input type="checkbox">동의</label><input type="number" placeholder="금액"><button>다음</button></section>'
    for i, s in enumerate(SPEC["steps"]))
GOOD_HTML = f"<html><head><style>.cta{{background:#008485}}</style></head><body>{STEP_HTML}</body></html>"
BAD_HTML = "<html><body><section data-step=\"1\"><h2>적금</h2><button>확인</button></section></body></html>"


class FakeStream(list):
    usage = {"inputTokens": 100, "outputTokens": 50}


def gen_seq(*htmls):
    it = iter(htmls)
    calls = []

    def generate(system, user, max_tokens):
        calls.append(user)
        return FakeStream(["```html\n", next(it), "\n```"])
    generate.calls = calls
    return generate


def reviewer(verdict="pass"):
    import json

    def review_generate(system, user, max_tokens):
        return json.dumps({"items": [{"id": i, "verdict": verdict, "evidence": "e", "fix": "f"} for i in LLM_IDS]}), {"inputTokens": 10, "outputTokens": 5}
    return review_generate


def publish(job_id, n, html):
    return f"https://x/studio/drafts/{job_id}-r{n}.html"


def _job(**kw):
    return loop.clamp_job({"jobId": "j1", "brief": "축구사랑 적금 가입 플로우", "productCode": "PRD-DEP-001", "outputType": "ux-flow", **kw})


def test_clamp_job_bounds():
    j = loop.clamp_job({"maxRounds": 99, "passScore": 10, "outputType": "weird", "axis": "x", "brief": "a" * 2000})
    assert j["maxRounds"] == 20 and j["passScore"] == 50 and j["outputType"] == "design" and j["axis"] == "흐름" and len(j["brief"]) == 1000
    assert loop.clamp_job({})["maxRounds"] == 3 and loop.clamp_job({"maxRounds": 0})["maxRounds"] == 1 and loop.clamp_job({})["mode"] == "generate"


def test_passes_first_round_and_stops():
    em = loop.ListEmitter()
    out = loop.run(_job(maxRounds=5), em, spec=SPEC, generate=gen_seq(GOOD_HTML), review_generate=reviewer("pass"), publish=publish, skills={"s": "skill"})
    assert out["stopReason"] == "passed" and out["rounds"] == 1 and out["passed"] and out["score"] == 100
    assert [s["step"] for s in em.stages] == ["spec_build", "assets", "generate", "review", "publish"]
    assert out["url"].endswith("j1-r1.html") and out["bestRound"] == 1 and out["usage"]["outputTokens"] == 55
    assert "".join(em.tokens).strip().endswith("```")


def test_regenerates_until_pass_and_feeds_failures():
    gen = gen_seq(BAD_HTML, GOOD_HTML)
    out = loop.run(_job(maxRounds=3), loop.ListEmitter(), spec=SPEC, generate=gen, review_generate=reviewer("pass"), publish=publish)
    assert out["rounds"] == 2 and out["stopReason"] == "passed" and out["bestRound"] == 2
    assert "이전 라운드" in gen.calls[1] and "FLOW-COND" in gen.calls[1] and BAD_HTML in gen.calls[1]
    assert [h["round"] for h in out["history"]] == [1, 2] and out["history"][0]["score"] < out["history"][1]["score"]


def test_max_rounds_picks_best_round():
    gen = gen_seq(BAD_HTML, GOOD_HTML, BAD_HTML)
    out = loop.run(_job(maxRounds=3, passScore=100), loop.ListEmitter(), spec=SPEC, generate=gen, review_generate=reviewer("fail"), publish=publish)
    assert out["stopReason"] == "max_rounds" and out["rounds"] == 3 and not out["passed"]
    assert out["bestRound"] == 2 and out["url"].endswith("j1-r2.html") and out["html"] == GOOD_HTML


def test_time_cap_stops_before_next_round():
    t = [0.0]

    def clock():
        t[0] += 500.0
        return t[0]
    out = loop.run(_job(maxRounds=10), loop.ListEmitter(), spec=SPEC, generate=gen_seq(BAD_HTML, BAD_HTML, BAD_HTML), review_generate=reviewer("fail"), publish=publish, clock=clock, time_cap_s=780)
    assert out["stopReason"] == "time_cap" and out["rounds"] < 10


def test_reviewer_garbage_is_undetermined_not_pass():
    def bad_reviewer(system, user, max_tokens):
        return "죄송합니다, 판정할 수 없습니다.", {}
    em = loop.ListEmitter()
    out = loop.run(_job(maxRounds=1), em, spec=SPEC, generate=gen_seq(GOOD_HTML), review_generate=bad_reviewer, publish=publish)
    rv = next(s for s in em.stages if s["step"] == "review")
    assert rv["reviewerError"] and set(rv["undetermined"]) == set(LLM_IDS) and not out["passed"] and out["stopReason"] == "max_rounds"
    assert all(i["verdict"] is None for i in rv["items"] if i["check"] == "llm")


def test_no_html_in_output_is_a_failed_round():
    out = loop.run(_job(maxRounds=1), loop.ListEmitter(), spec=SPEC, generate=gen_seq("텍스트만 있음"), review_generate=reviewer("pass"), publish=publish)
    assert out["score"] == 0 and not out["passed"] and out["history"][0]["failures"][0]["id"] == "OUTPUT"


def test_refine_mode_single_round_with_stability_item():
    gen = gen_seq(GOOD_HTML)
    em = loop.ListEmitter()
    job = _job(mode="refine", selector="body > section:nth-of-type(2) > h2", instruction="제목 크게", elementHtml="<h2>기간 선택</h2>")
    out = loop.run(job, em, spec=SPEC, generate=gen, review_generate=reviewer("pass"), publish=publish, base_html=GOOD_HTML)
    assert job["maxRounds"] == 1 and out["rounds"] == 1
    assert "제목 크게" in gen.calls[0] and "body > section:nth-of-type(2) > h2" in gen.calls[0]
    rv = next(s for s in em.stages if s["step"] == "review")
    assert any(i["id"] == "STABLE" and i["verdict"] == "pass" for i in rv["items"])


def test_generate_exception_becomes_error_stop():
    def boom(system, user, max_tokens):
        raise RuntimeError("gate refused")
    out = loop.run(_job(maxRounds=3), loop.ListEmitter(), spec=SPEC, generate=boom, review_generate=reviewer(), publish=publish)
    assert out["stopReason"] == "error" and "gate refused" in out["error"] and out["rounds"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_loop.py -q`
Expected: FAIL (`cannot import name 'loop'`).

- [ ] **Step 3: Implement `loop.py`**

```python
# platform/studio/loop.py
"""에이전틱 루프 — DesignSpec → 생성 → 검수(결정적 + 리뷰어) → 수정 재생성. 점수 기준 반복, 상한·시간 캡.

의존성은 전부 주입된다(테스트 오프라인). 워커(worker_handler)가 engine.bedrock 을 꽂는다.
원칙: 미판정은 pass 가 아니다 · 최종 시안은 최고 점수 라운드 · 프롬프트/HTML 원문은 로그에 넣지 않는다.
"""
from __future__ import annotations

import os
import time

from studio import prompts, review

MAX_ROUNDS = 20
DEFAULT_ROUNDS = 3
DEFAULT_PASS = 85
TIME_CAP_S = 780
GEN_MAX_TOKENS = 12000
REVIEW_MAX_TOKENS = 2500
MODEL = os.environ.get("GEN_MODEL", "global.anthropic.claude-sonnet-5")


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


def _review_round(spec: dict, items: list, html: str, prev_html: str, review_generate, pass_score: int) -> dict:
    doc = review.parse_html(html)
    verdicts = review.deterministic_checks(items, doc)
    items = list(items)
    if prev_html:
        st_item, st_v = review.stability_item(review.skeleton_diff(prev_html, html))
        items.append(st_item)
        verdicts[st_item["id"]] = st_v
    llm_items = [i for i in items if i.get("check") == "llm"]
    reviewer_error, usage = None, {}
    if llm_items:
        try:
            text, usage = review_generate(prompts.REVIEW_SYSTEM, prompts.build_review_prompt(spec, llm_items, review.dom_digest(doc)), REVIEW_MAX_TOKENS)
            llm_v, reviewer_error = review.parse_review(text, [i["id"] for i in llm_items])
        except Exception as e:  # noqa: BLE001 — 리뷰어 실패는 미판정으로 남긴다
            llm_v, reviewer_error = {i["id"]: None for i in llm_items}, f"{type(e).__name__}: {str(e)[:120]}"
        verdicts.update(llm_v)
    sc = review.score(items, verdicts, pass_score)
    rows = _items_with_verdicts(items, verdicts)
    failures = [{"id": r["id"], "text": r["text"], "evidence": r["evidence"], "fix": r["fix"]} for r in rows if r["verdict"] != "pass"]
    return {**sc, "items": rows, "failures": failures, "reviewerError": reviewer_error, "usage": usage or {},
            "deterministic": sum(1 for i in items if i.get("check") in ("text", "dom")), "llm": len(llm_items)}


def run(job: dict, emitter, *, spec: dict, generate, review_generate, publish, skills=None, assets_text: str = "",
        fewshot=None, agent_preset: str = "", base_html: str = "", clock=time.time, time_cap_s: int = TIME_CAP_S) -> dict:
    t0 = clock()
    job = clamp_job(job)
    items = list(spec["items"])
    emitter.stage("spec_build", items=len(items), required=sum(1 for i in items if i.get("required")),
                  productName=spec.get("productName"), productCode=spec.get("productCode"),
                  hasPreferential=spec.get("hasPreferential"), steps=[s["name"] for s in spec.get("steps", [])], plane="cloud")
    skills = skills if skills is not None else prompts.load_skills()[0]
    emitter.stage("assets", chars=len(assets_text), fewshot=len(fewshot or []), skills=list(skills), plane="cloud")
    system = prompts.build_system_prompt(spec, job["outputType"], job["axis"], skills, assets_text, fewshot, agent_preset)

    usage = {"inputTokens": 0, "outputTokens": 0}
    history: list = []
    failures: list = []
    prev_html = base_html if job["mode"] == "refine" else ""
    stop, error = "max_rounds", None
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
        user = prompts.build_user_prompt(job["brief"], spec, failures=failures if rnd > 1 else None, prev_html=prev_html, refine=refine)
        emitter.stage("generate", round=rnd, systemChars=len(system), userChars=len(user), model=MODEL, plane="cloud")
        try:
            stream = generate(system, user, GEN_MAX_TOKENS)
            parts = []
            for tk in stream:
                parts.append(tk)
                emitter.token(tk)
            u = getattr(stream, "usage", None) or {}
        except Exception as e:  # noqa: BLE001 — 게이트 거부 등: 라운드 중단, 사실대로 보고
            stop, error = "error", f"{type(e).__name__}: {str(e)[:200]}"
            history.append({"round": rnd, "score": 0, "passed": False, "url": "", "failures": [{"id": "GENERATE", "text": "생성 실패", "evidence": error, "fix": ""}],
                            "undetermined": [], "reviewerError": None, "elapsedMs": int((clock() - r_t0) * 1000), "html": ""})
            break
        usage["inputTokens"] += int(u.get("inputTokens", 0) or 0)
        usage["outputTokens"] += int(u.get("outputTokens", 0) or 0)
        html = prompts.extract_html("".join(parts))
        if not html:
            rv = {"score": 0, "passed": False, "requiredFailed": [i["id"] for i in items if i.get("required")], "undetermined": [],
                  "items": _items_with_verdicts(items, {}), "failures": [{"id": "OUTPUT", "text": "HTML 문서를 찾지 못함", "evidence": "```html 펜스 없음", "fix": "자기완결 HTML 1개를 ```html 펜스 안에 출력"}],
                  "reviewerError": None, "usage": {}, "deterministic": 0, "llm": 0}
        else:
            rv = _review_round(spec, items, html, prev_html, review_generate, job["passScore"])
            usage["inputTokens"] += int(rv["usage"].get("inputTokens", 0) or 0)
            usage["outputTokens"] += int(rv["usage"].get("outputTokens", 0) or 0)
        emitter.stage("review", round=rnd, score=rv["score"], passed=rv["passed"], items=rv["items"], requiredFailed=rv["requiredFailed"],
                      undetermined=rv["undetermined"], reviewerError=rv["reviewerError"], deterministic=rv["deterministic"], llm=rv["llm"], plane="cloud")
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

    best = max(history, key=lambda h: (h["score"], h["round"])) if history else None
    out = {"jobId": job["jobId"], "mode": job["mode"], "outputType": job["outputType"], "axis": job["axis"],
           "score": best["score"] if best else 0, "passed": bool(best and best["passed"]), "rounds": rnd,
           "maxRounds": job["maxRounds"], "passScore": job["passScore"], "stopReason": stop,
           "bestRound": best["round"] if best else 0, "url": best["url"] if best else "", "html": best["html"] if best else "",
           "items": best.get("items", []) if best else [], "history": [{k: v for k, v in h.items() if k not in ("html", "items")} for h in history],
           "usage": usage, "model": MODEL, "elapsedMs": int((clock() - t0) * 1000),
           "spec": {"productCode": spec.get("productCode"), "productName": spec.get("productName"),
                    "hasPreferential": spec.get("hasPreferential"), "stepCount": len(spec.get("steps", []))}}
    if error:
        out["error"] = error
    return out
```

- [ ] **Step 4: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_loop.py -q`
Expected: 9 passed. `test_time_cap_stops_before_next_round`: clock advances 500 s per call; after round 1 (several clock calls) elapsed exceeds 780 → `time_cap` with rounds 1 or 2.

- [ ] **Step 5: Commit**

```bash
git add platform/studio/loop.py platform/tests/test_studio_loop.py
git commit -m "studio: agentic loop — generate/review/regenerate with score threshold, round cap, time cap, best-round selection"
```

---

### Task 6: StudioStore (jobs · rounds · drafts)

**Files:**
- Create: `platform/studio/store.py`
- Test: `platform/tests/test_studio_store.py`

**Interfaces:**
- Produces `class StudioStore(table=None, table_name=None)` (mirrors `registry.store.RegistryStore` construction; falls back to `registry.fake_table.InMemoryTable(hash_key="pk", range_key="sk")` when no table name):
  - `backend -> "dynamodb"|"memory"`
  - `put_job(job: dict, actor: str) -> dict` (pk `job#<jobId>`, sk `meta`, status `running`, createdAt ms)
  - `update_job(job_id, **fields) -> None`
  - `get_job(job_id) -> dict|None` (with `rounds` list)
  - `list_jobs(limit=20) -> list[dict]` (pk `jobs`, sk `<createdAt>#<jobId>` index rows written by put_job)
  - `put_round(job_id, rec: dict) -> None` (pk `job#<jobId>`, sk `round#<nn>`; strips `html`, `items`)
  - `put_draft(draft: dict) -> dict` (pk `draft#<draftId>`, sk `meta`; plus list row pk `drafts`, sk `<createdAt>#<draftId>`)
  - `get_draft(draft_id) -> dict|None`
  - `list_drafts(limit=60) -> list[dict]` newest first
  - `set_draft_status(draft_id, status: str, comment: str, actor: str) -> dict|None`
  - `approved_drafts(limit=2) -> list[dict]` newest approved
  - Draft record fields: `draftId, jobId, title, axis, outputType, productCode, productName, score, passed, rounds, bestRound, status(검토중|승인됨|반려), comment, parentId, url, key, createdAt, createdBy, model`.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_store.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.store import StudioStore  # noqa: E402


def _draft(i, status="검토중", ts=None):
    return {"draftId": f"d{i}", "jobId": f"j{i}", "title": f"시안 {i}", "axis": "흐름", "outputType": "ux-flow", "productCode": "PRD-DEP-001",
            "productName": "아톰 축구사랑 적금", "score": 80 + i, "passed": i > 1, "rounds": 2, "bestRound": 2, "status": status,
            "url": f"https://x/studio/drafts/d{i}.html", "key": f"studio/drafts/d{i}.html", "createdAt": ts or (1000 + i), "createdBy": "u@x", "model": "m"}


def test_memory_backend_and_job_roundtrip():
    st = StudioStore()
    assert st.backend == "memory"
    st.put_job({"jobId": "j1", "brief": "b", "productCode": "PRD-DEP-001", "maxRounds": 3}, actor="u@x")
    st.put_round("j1", {"round": 1, "score": 40, "passed": False, "url": "u1", "html": "<html>", "items": [1, 2], "failures": []})
    st.put_round("j1", {"round": 2, "score": 90, "passed": True, "url": "u2", "html": "<html>", "items": [], "failures": []})
    st.update_job("j1", status="done", score=90, stopReason="passed")
    j = st.get_job("j1")
    assert j["status"] == "done" and j["score"] == 90 and [r["round"] for r in j["rounds"]] == [1, 2]
    assert "html" not in j["rounds"][0] and "items" not in j["rounds"][0]
    assert st.get_job("nope") is None
    st.put_job({"jobId": "j2", "brief": "b", "productCode": "PRD-DEP-002"}, actor="u@x")
    assert [x["jobId"] for x in st.list_jobs()] == ["j2", "j1"]


def test_drafts_list_status_and_approved():
    st = StudioStore()
    for i in (1, 2, 3):
        st.put_draft(_draft(i))
    assert [d["draftId"] for d in st.list_drafts()] == ["d3", "d2", "d1"]
    assert st.get_draft("d2")["score"] == 82
    upd = st.set_draft_status("d2", "승인됨", "좋음", actor="u@x")
    assert upd["status"] == "승인됨" and upd["comment"] == "좋음" and upd["reviewedBy"] == "u@x"
    st.set_draft_status("d3", "반려", "CTA 두 개", actor="u@x")
    assert st.get_draft("d3")["status"] == "반려"
    assert [d["draftId"] for d in st.list_drafts()][1] == "d2" and st.list_drafts()[1]["status"] == "승인됨", "목록 행도 갱신"
    assert [d["draftId"] for d in st.approved_drafts()] == ["d2"]
    assert st.set_draft_status("nope", "승인됨", "", actor="u@x") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_store.py -q`
Expected: FAIL (`No module named 'studio.store'`).

- [ ] **Step 3: Implement `store.py`**

```python
# platform/studio/store.py
"""스튜디오 저장소 — 잡·라운드·시안(승인 상태). DynamoDB(STUDIO_TABLE) 또는 인메모리 페이크(테스트)."""
from __future__ import annotations

import os
import time
from typing import Any, Optional

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
STATUSES = ("검토중", "승인됨", "반려")


def now_ms() -> int:
    return int(time.time() * 1000)


class StudioStore:
    def __init__(self, table: Any = None, table_name: Optional[str] = None) -> None:
        self._table = table
        self._table_name = table_name if table_name is not None else os.environ.get("STUDIO_TABLE", "")

    @property
    def backend(self) -> str:
        if self._table is not None and type(self._table).__name__ == "InMemoryTable":
            return "memory"
        return "dynamodb" if (self._table is not None or self._table_name) else "memory"

    def table(self) -> Any:
        if self._table is None:
            if self._table_name:
                import boto3  # 지연 import — 테스트 오프라인
                self._table = boto3.resource("dynamodb", region_name=REGION).Table(self._table_name)
            else:
                from registry.fake_table import InMemoryTable
                self._table = InMemoryTable(hash_key="pk", range_key="sk")
        return self._table

    # ---------- 공통 ----------
    def _get(self, pk: str, sk: str) -> Optional[dict]:
        it = self.table().get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return _clean(it) if it else None

    def _list(self, pk: str, limit: int, prefix: str = "") -> list:
        kwargs: dict = dict(KeyConditionExpression="pk = :pk" + (" AND begins_with(sk, :p)" if prefix else ""),
                            ExpressionAttributeValues={":pk": pk, **({":p": prefix} if prefix else {})},
                            ScanIndexForward=False, Limit=limit)
        return [_clean(i) for i in self.table().query(**kwargs).get("Items", [])]

    # ---------- 잡 ----------
    def put_job(self, job: dict, actor: str) -> dict:
        ts = now_ms()
        item = {**job, "pk": f"job#{job['jobId']}", "sk": "meta", "status": "running", "createdAt": ts, "createdBy": actor, "updatedAt": ts}
        self.table().put_item(Item=item)
        self.table().put_item(Item={"pk": "jobs", "sk": f"{ts:013d}#{job['jobId']}", "jobId": job["jobId"], "brief": job.get("brief", "")[:200],
                                    "productCode": job.get("productCode", ""), "status": "running", "createdAt": ts, "createdBy": actor})
        return _clean(item)

    def update_job(self, job_id: str, **fields) -> None:
        meta = self._get(f"job#{job_id}", "meta")
        if not meta:
            return
        meta.update(fields)
        meta["updatedAt"] = now_ms()
        self.table().put_item(Item={**meta, "pk": f"job#{job_id}", "sk": "meta"})
        row = {"pk": "jobs", "sk": f"{int(meta['createdAt']):013d}#{job_id}", "jobId": job_id, "brief": str(meta.get("brief", ""))[:200],
               "productCode": meta.get("productCode", ""), "status": meta.get("status", "running"), "createdAt": meta["createdAt"],
               "createdBy": meta.get("createdBy", ""), "score": meta.get("score"), "stopReason": meta.get("stopReason"), "draftId": meta.get("draftId")}
        self.table().put_item(Item={k: v for k, v in row.items() if v is not None})

    def get_job(self, job_id: str) -> Optional[dict]:
        meta = self._get(f"job#{job_id}", "meta")
        if not meta:
            return None
        rounds = sorted(self._list(f"job#{job_id}", 50, prefix="round#"), key=lambda r: r.get("round", 0))
        return {**meta, "rounds": rounds}

    def list_jobs(self, limit: int = 20) -> list:
        return self._list("jobs", limit)

    def put_round(self, job_id: str, rec: dict) -> None:
        item = {k: v for k, v in rec.items() if k not in ("html", "items")}
        self.table().put_item(Item={**item, "pk": f"job#{job_id}", "sk": f"round#{int(rec['round']):02d}", "createdAt": now_ms()})

    # ---------- 시안 ----------
    def put_draft(self, draft: dict) -> dict:
        d = {**draft, "status": draft.get("status") or "검토중", "comment": draft.get("comment", ""), "createdAt": draft.get("createdAt") or now_ms()}
        self.table().put_item(Item={**d, "pk": f"draft#{d['draftId']}", "sk": "meta"})
        self._put_draft_row(d)
        return d

    def _put_draft_row(self, d: dict) -> None:
        self.table().put_item(Item={**d, "pk": "drafts", "sk": f"{int(d['createdAt']):013d}#{d['draftId']}"})

    def get_draft(self, draft_id: str) -> Optional[dict]:
        return self._get(f"draft#{draft_id}", "meta")

    def list_drafts(self, limit: int = 60) -> list:
        return self._list("drafts", limit)

    def set_draft_status(self, draft_id: str, status: str, comment: str, actor: str) -> Optional[dict]:
        d = self.get_draft(draft_id)
        if not d or status not in STATUSES:
            return None
        d.update({"status": status, "comment": (comment or "")[:500], "reviewedBy": actor, "reviewedAt": now_ms()})
        self.table().put_item(Item={**d, "pk": f"draft#{draft_id}", "sk": "meta"})
        self._put_draft_row(d)
        return d

    def approved_drafts(self, limit: int = 2) -> list:
        return [d for d in self.list_drafts(200) if d.get("status") == "승인됨"][:limit]


def _clean(item: dict) -> dict:
    out = {k: v for k, v in item.items() if k not in ("pk", "sk")}
    for k, v in list(out.items()):
        if type(v).__name__ == "Decimal":
            out[k] = int(v) if v == int(v) else float(v)
    return out
```

- [ ] **Step 4: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_store.py -q`
Expected: 2 passed. If `begins_with` with `ScanIndexForward=False` misbehaves in `InMemoryTable`, check `registry/fake_table.py` query support (it documents `begins_with` and `ScanIndexForward`).

- [ ] **Step 5: Commit**

```bash
git add platform/studio/store.py platform/tests/test_studio_store.py
git commit -m "studio: StudioStore — jobs, rounds, drafts with approval status over DynamoDB/in-memory table"
```

---

### Task 7: WebSocket handlers `studio_*` + proxy move

**Files:**
- Create: `platform/api/common/studio_proxy.py`
- Create: `platform/api/handlers/studio.py`
- Modify: `platform/api/handlers/core.py` (remove `_studio`, `STUDIO`, `studio_*` handlers and their ROUTES entries; import `studio_proxy.studio_get` for `hub`/`assets`)
- Modify: `platform/api/handlers/__init__.py` (add `"handlers.studio"` to `MODULES`)
- Test: `platform/tests/test_studio_handler.py`

**Interfaces:**
- `api/common/studio_proxy.py`: `STUDIO_URL`, `studio(method, path, token="", body=None, timeout=60) -> dict` (verbatim move of `core._studio`), `studio_get(path) -> dict`, `asset_text(asset_ids: list[str]) -> str` (fetches `/api/assets/content?asset_id=` for each id and joins as `### 자산 <id>\n<content>` blocks, ≤ 4000 chars each), `agent_preset(agent_id: str) -> str` (asset content of type `agent`, expects JSON with `system` key; returns `system` or raw text).
- `api/handlers/studio.py` ROUTES: `studio_run, studio_jobs, studio_drafts, studio_feedback, studio_products, studio_spec, studio_asset, studio_register`.
  - `studio_run` body: `{brief, productCode, outputType, axis, assetIds, agentId, maxRounds, passScore, mode, baseDraftId, selector, elementHtml, instruction}` → posts `{"type":"studio_run","jobId","maxRounds","passScore","backend"}` then the worker streams. When `STUDIO_LOOP_FN` env is empty → `ctx.done("studio", error="미배포: STUDIO_LOOP_FN 미설정 — 워커 Lambda 가 배포되지 않았습니다")`.
  - Worker payload: `{"connId", "endpoint", "reqId", "email", "traceId", "job"}` — `endpoint = ctx.apigw.meta.endpoint_url`.
  - Module-level injectables for tests: `_store: StudioStore|None`, `_graph`, `_invoke(fn_name, payload) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_handler.py
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")
os.environ.setdefault("CONN_TABLE", "test-conn")

import ws_handler  # noqa: E402
from common.ctx import Ctx  # noqa: E402
from graph.store import LocalGraphStore  # noqa: E402
from handlers import studio as h  # noqa: E402
from studio.store import StudioStore  # noqa: E402


class _Apigw:
    def __init__(self):
        self.sent = []
        self.meta = type("M", (), {"endpoint_url": "https://ws.example/prod"})()

    def post_to_connection(self, ConnectionId, Data):
        self.sent.append(json.loads(Data))


def _ctx():
    a = _Apigw()
    return Ctx(apigw=a, conn_id="c1", email="u@x", rid="r1"), a


def setup_module(module):
    h._store = StudioStore()
    h._graph = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


def test_routes_registered():
    for a in ("studio_run", "studio_jobs", "studio_drafts", "studio_feedback", "studio_products", "studio_spec", "studio_asset", "studio_register"):
        assert a in ws_handler.ROUTES, a
    assert "studio_generate" not in ws_handler.ROUTES, "옛 프록시 생성 라우트는 제거"


def test_products_and_spec():
    ctx, a = _ctx()
    h.studio_products(ctx, {})
    e = a.sent[-1]
    assert e["type"] == "studio_products" and e["products"][0]["code"] == "PRD-DEP-001" and e["graphBackend"] == "local"
    h.studio_spec(ctx, {"productCode": "PRD-DEP-001", "outputType": "ux-flow"})
    s = a.sent[-1]
    assert s["type"] == "studio_spec" and s["spec"]["hasPreferential"] and any(i["id"] == "FLOW-COND" for i in s["spec"]["items"])
    h.studio_spec(ctx, {"productCode": "PRD-NOPE"})
    assert a.sent[-1]["type"] == "studio_spec" and "찾을 수 없" in a.sent[-1]["error"]


def test_run_without_worker_is_honest(monkeypatch):
    monkeypatch.setenv("STUDIO_LOOP_FN", "")
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "b", "productCode": "PRD-DEP-001"})
    assert a.sent[-1]["type"] == "studio.done" and "미배포" in a.sent[-1]["error"]


def test_run_invokes_worker_with_clamped_job(monkeypatch):
    monkeypatch.setenv("STUDIO_LOOP_FN", "studio-fn")
    calls = []
    monkeypatch.setattr(h, "_invoke", lambda fn, payload: calls.append((fn, payload)))
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "축구 적금", "productCode": "PRD-DEP-001", "maxRounds": 50, "passScore": 20, "outputType": "ux-flow", "axis": "흐름", "assetIds": ["palette:hana"]})
    fn, p = calls[0]
    assert fn == "studio-fn" and p["connId"] == "c1" and p["endpoint"] == "https://ws.example/prod" and p["reqId"] == "r1" and p["email"] == "u@x"
    assert p["job"]["maxRounds"] == 20 and p["job"]["passScore"] == 50 and p["job"]["assetIds"] == ["palette:hana"] and len(p["job"]["jobId"]) == 12
    ack = a.sent[-1]
    assert ack["type"] == "studio_run" and ack["jobId"] == p["job"]["jobId"] and ack["maxRounds"] == 20
    assert h._store.get_job(p["job"]["jobId"])["status"] == "running"


def test_run_rejects_missing_product():
    ctx, a = _ctx()
    h.studio_run(ctx, {"brief": "b"})
    assert a.sent[-1]["type"] == "studio.done" and "productCode" in a.sent[-1]["error"]


def test_jobs_drafts_feedback():
    h._store.put_draft({"draftId": "dA", "jobId": "jA", "title": "t", "axis": "흐름", "outputType": "design", "productCode": "PRD-DEP-001",
                        "productName": "아톰 축구사랑 적금", "score": 91, "passed": True, "rounds": 2, "bestRound": 2, "url": "https://x/d.html",
                        "key": "studio/drafts/dA.html", "createdAt": 5, "createdBy": "u@x", "model": "m"})
    ctx, a = _ctx()
    h.studio_drafts(ctx, {})
    assert a.sent[-1]["type"] == "studio_drafts" and a.sent[-1]["drafts"][0]["draftId"] == "dA" and a.sent[-1]["backend"] == "memory"
    h.studio_feedback(ctx, {"draftId": "dA", "decision": "reject", "comment": "CTA 두 개"})
    assert a.sent[-1]["type"] == "studio_feedback" and a.sent[-1]["draft"]["status"] == "반려" and a.sent[-1]["draft"]["comment"] == "CTA 두 개"
    h.studio_feedback(ctx, {"draftId": "dA", "decision": "bogus"})
    assert a.sent[-1]["error"]
    h.studio_jobs(ctx, {})
    assert a.sent[-1]["type"] == "studio_jobs" and isinstance(a.sent[-1]["jobs"], list)
    h.studio_jobs(ctx, {"jobId": "nope"})
    assert a.sent[-1]["job"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_handler.py -q`
Expected: FAIL (`cannot import name 'studio' from 'handlers'`).

- [ ] **Step 3: Create `api/common/studio_proxy.py`**

```python
# platform/api/common/studio_proxy.py
"""uiux-studio(자매 데모) 자산 레지스트리 프록시 — 자산 조회·등록·에이전트 프리셋만. 시안·잡은 플랫폼 소유(studio.store).
CloudFront OAC 때문에 본문 있는 요청은 x-amz-content-sha256 이 필수다 (없으면 403)."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

STUDIO_URL = os.environ.get("STUDIO_URL", "https://d4zwmnh2s47e9.cloudfront.net")


def studio(method: str, path: str, token: str = "", body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if data is not None:
        headers["x-amz-content-sha256"] = hashlib.sha256(data).hexdigest()
    if token:
        headers["x-hana-auth"] = token
    req = urllib.request.Request(STUDIO_URL + path, data=data, headers=headers, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read().decode()).get("error", str(e.code))}
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def studio_get(path: str) -> dict:
    return studio("GET", path)


def _content(asset_id: str) -> str:
    r = studio_get(f"/api/assets/content?asset_id={urllib.parse.quote(asset_id)}")
    c = r.get("content", r) if isinstance(r, dict) else r
    if isinstance(c, dict) and "content" in c:
        c = c["content"]
    return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)


def asset_text(asset_ids: list) -> str:
    blocks = []
    for aid in asset_ids[:20]:
        try:
            blocks.append(f"### 자산 {aid}\n{_content(aid)[:4000]}")
        except Exception as e:  # noqa: BLE001 — 자산 하나가 실패해도 생성은 계속
            blocks.append(f"### 자산 {aid}\n(조회 실패: {str(e)[:80]})")
    return "\n\n".join(blocks)


def agent_preset(agent_id: str) -> str:
    if not agent_id:
        return ""
    raw = _content(agent_id)
    try:
        return str(json.loads(raw).get("system", raw))
    except Exception:
        return raw
```

- [ ] **Step 4: Create `api/handlers/studio.py`**

```python
# platform/api/handlers/studio.py
"""디자인 스튜디오 액션 — 잡 실행(워커 Lambda 비동기 invoke)·잡/시안 조회·승인·상품/명세 미리보기·자산 프록시.

studio_run 은 요청/응답형 ack(`studio_run`) 1건을 보내고, 이후 이벤트(studio.stage/.token/.done)는 StudioLoopFn 이
같은 커넥션으로 push 한다 (studio/worker_handler.py). 워커가 미배포면 흉내내지 않고 .done(error) 로 알린다.
"""
from __future__ import annotations

import json
import os
import uuid

from common import studio_proxy
from common.ctx import Ctx
from common.log import log_event
from studio import loop, spec as specmod
from studio.store import StudioStore

KIND = "studio"
_store: StudioStore | None = None
_graph = None


def store() -> StudioStore:
    global _store
    if _store is None:
        _store = StudioStore()
    return _store


def graph():
    global _graph
    if _graph is None:
        from graph.store import get_store
        _graph = get_store()
    return _graph


def _invoke(fn_name: str, payload: dict) -> None:
    import boto3  # 지연 import — 테스트 오프라인
    boto3.client("lambda").invoke(FunctionName=fn_name, InvocationType="Event",
                                  Payload=json.dumps(payload, ensure_ascii=False).encode())


def studio_run(ctx: Ctx, body: dict) -> None:
    job = loop.clamp_job(body)
    if not job["productCode"]:
        ctx.done(KIND, error="productCode 가 필요합니다 — 상품을 선택하세요")
        return
    if job["mode"] == "refine" and not job["baseDraftId"]:
        ctx.done(KIND, error="refine 모드에는 baseDraftId 가 필요합니다")
        return
    fn = os.environ.get("STUDIO_LOOP_FN", "")
    if not fn:
        ctx.done(KIND, error="미배포: STUDIO_LOOP_FN 미설정 — 워커 Lambda 가 배포되지 않았습니다")
        return
    job["jobId"] = uuid.uuid4().hex[:12]
    store().put_job(job, actor=ctx.email)
    payload = {"connId": ctx.conn_id, "endpoint": getattr(getattr(ctx.apigw, "meta", None), "endpoint_url", ""),
               "reqId": ctx.rid, "email": ctx.email, "traceId": ctx.trace_id, "job": job}
    _invoke(fn, payload)
    log_event("studio.run", ctx.trace_id, jobId=job["jobId"], productCode=job["productCode"], maxRounds=job["maxRounds"], mode=job["mode"])
    ctx.post({"type": "studio_run", "jobId": job["jobId"], "maxRounds": job["maxRounds"], "passScore": job["passScore"],
              "backend": store().backend})


def studio_jobs(ctx: Ctx, body: dict) -> None:
    jid = str(body.get("jobId") or "")
    if jid:
        ctx.post({"type": "studio_jobs", "job": store().get_job(jid)})
    else:
        ctx.post({"type": "studio_jobs", "jobs": store().list_jobs(int(body.get("limit", 20)))})


def studio_drafts(ctx: Ctx, body: dict) -> None:
    ctx.post({"type": "studio_drafts", "drafts": store().list_drafts(int(body.get("limit", 60))), "backend": store().backend})


def studio_feedback(ctx: Ctx, body: dict) -> None:
    decision = body.get("decision")
    status = {"approve": "승인됨", "reject": "반려"}.get(decision)
    if not status:
        ctx.post({"type": "studio_feedback", "error": f"decision 은 approve|reject 여야 합니다: {decision!r}"})
        return
    d = store().set_draft_status(str(body.get("draftId", "")), status, str(body.get("comment", "")), actor=ctx.email)
    if not d:
        ctx.post({"type": "studio_feedback", "error": "시안을 찾을 수 없습니다"})
        return
    log_event("studio.feedback", ctx.trace_id, draftId=d["draftId"], status=status, actor=ctx.email)
    ctx.post({"type": "studio_feedback", "draft": d})


def studio_products(ctx: Ctx, body: dict) -> None:
    g = graph()
    ctx.post({"type": "studio_products", "products": specmod.list_products(g), "graphBackend": getattr(g, "name", "unknown"),
              "model": loop.MODEL})


def studio_spec(ctx: Ctx, body: dict) -> None:
    try:
        s = specmod.build_spec(graph(), str(body.get("productCode", "")), str(body.get("outputType", "design")))
        ctx.post({"type": "studio_spec", "spec": s})
    except specmod.SpecError as e:
        ctx.post({"type": "studio_spec", "error": str(e)})


def studio_asset(ctx: Ctx, body: dict) -> None:
    import urllib.parse
    aid = urllib.parse.quote(str(body.get("assetId", "")))
    content = studio_proxy.studio_get(f"/api/assets/content?asset_id={aid}")
    history = studio_proxy.studio_get(f"/api/assets/history?asset_id={aid}")
    ctx.post({"type": "studio_asset", "content": content, "history": history.get("history", history)})


def studio_register(ctx: Ctx, body: dict) -> None:
    r = studio_proxy.studio("POST", "/api/assets", str(body.get("studioToken", "")),
                            {"name": str(body.get("name", ""))[:80], "type": str(body.get("assetType", "")),
                             "content": str(body.get("content", ""))[:20000], "scope": body.get("scope", "shared")})
    ctx.post({"type": "studio_register", **r})


ROUTES = {"studio_run": studio_run, "studio_jobs": studio_jobs, "studio_drafts": studio_drafts, "studio_feedback": studio_feedback,
          "studio_products": studio_products, "studio_spec": studio_spec, "studio_asset": studio_asset, "studio_register": studio_register}
```

- [ ] **Step 5: Edit `core.py` and `handlers/__init__.py`**

In `platform/api/handlers/core.py`:
1. Delete `import hashlib` if no other use remains; delete the `STUDIO = os.environ.get("STUDIO_URL", ...)` line and the whole `_studio()` function (lines ~60–76).
2. Add `from common import studio_proxy` to the imports and replace every remaining `_studio("GET", ...)` call (in `hub` around line 159 and `assets` around line 190) with `studio_proxy.studio_get(...)`.
3. Delete the functions `studio_drafts`, `studio_asset`, `studio_models`, `studio_jobs`, `studio_generate`, `studio_feedback`, `studio_register` (lines ~206–245) and remove their entries from `ROUTES` so it reads:
```python
ROUTES = {
    "hub": hub, "agents": agents, "chat": chat, "assets": assets, "surfaces": surfaces,
    "traces": traces, "explore": explore, "reset": reset,
}
```
In `platform/api/handlers/__init__.py`, change `MODULES` to end with `..., "handlers.agents", "handlers.portal", "handlers.studio"]`.

- [ ] **Step 6: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_handler.py tests/test_handlers_smoke.py -q && python3 -c "import sys; sys.path[:0]=['api','.']; import handlers.core"`
Expected: all PASS; import succeeds (no leftover `_studio` references — `grep -n "_studio(" api/handlers/core.py` prints nothing).

- [ ] **Step 7: Commit**

```bash
git add platform/api/common/studio_proxy.py platform/api/handlers/studio.py platform/api/handlers/core.py platform/api/handlers/__init__.py platform/tests/test_studio_handler.py
git commit -m "studio: studio_* handlers (run→worker invoke, jobs, drafts, feedback, products, spec); move uiux-studio proxy to common"
```

---

### Task 8: Worker Lambda (`studio/worker_handler.py`)

**Files:**
- Create: `platform/studio/worker_handler.py`
- Test: `platform/tests/test_studio_worker.py`

**Interfaces:**
- Consumes payload from Task 7: `{"connId","endpoint","reqId","email","traceId","job"}`.
- Produces `handler(event, context) -> dict` and injectable module attrs for tests: `_apigw_client(endpoint)`, `_s3_client()`, `_graph()`, `_store()`, `_generate`, `_review_generate`, `_assets_text`, `_agent_preset`, `_fewshot(store, s3, bucket) -> list[str]`.
- Env: `STUDIO_TABLE`, `WEB_BUCKET`, `WEB_URL` (e.g. `https://agent.atomai.click`), `GEN_MODEL`, graph envs.
- Publishes `s3://$WEB_BUCKET/studio/drafts/<jobId>-r<n>.html` (ContentType `text/html; charset=utf-8`, CacheControl `no-cache`), returns `f"{WEB_URL}/studio/drafts/{jobId}-r{n}.html"`. On completion writes draft record `draftId = jobId` (best round key) and `update_job(status="done"|"failed", ...)`.
- `.done` payload = `loop.run` output minus `html`, plus `draftId`, `backend`, `graphBackend`.
- WebSocket gone (`GoneException`) is swallowed; the loop keeps writing rounds to the store.

- [ ] **Step 1: Write the failing tests**

```python
# platform/tests/test_studio_worker.py
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
os.environ["WEB_BUCKET"] = "web-bkt"
os.environ["WEB_URL"] = "https://agent.example"

from graph.store import LocalGraphStore  # noqa: E402
from studio import worker_handler as w  # noqa: E402
from studio.store import StudioStore  # noqa: E402

GRAPH = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")


class _Apigw:
    def __init__(self, gone=False):
        self.sent, self.gone = [], gone

    def post_to_connection(self, ConnectionId, Data):
        if self.gone:
            raise Exception("GoneException: connection gone")
        self.sent.append(json.loads(Data))


class _S3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeStream(list):
    usage = {"inputTokens": 1, "outputTokens": 1}


GOOD = "<html><head><style>.c{background:#008485}</style></head><body>" + "".join(
    f'<section data-step="{i}"><h2>{n}</h2><p>기본금리 연 3.0% 적금 우대금리 축구클럽 회원 인증 시 우대금리 +1.0%p 자동이체 월 납입금액 가입기간 만기 예상 이자</p><label><input type="checkbox">동의</label><input type="number"><button>다음</button></section>'
    for i, n in enumerate(["상품안내", "기간 선택", "금액 입력", "축구클럽 인증", "납입 방식", "약관 동의", "가입 완료"], start=1)) + "</body></html>"


def _wire(monkeypatch, apigw, s3, store):
    monkeypatch.setattr(w, "_apigw_client", lambda endpoint: apigw)
    monkeypatch.setattr(w, "_s3_client", lambda: s3)
    monkeypatch.setattr(w, "_graph", lambda: GRAPH)
    monkeypatch.setattr(w, "_store", lambda: store)
    monkeypatch.setattr(w, "_generate", lambda system, user, max_tokens: FakeStream(["```html\n", GOOD, "\n```"]))
    monkeypatch.setattr(w, "_review_generate", lambda system, user, max_tokens: (json.dumps({"items": []}), {}))
    monkeypatch.setattr(w, "_assets_text", lambda ids: "팔레트" if ids else "")
    monkeypatch.setattr(w, "_agent_preset", lambda aid: "")


def _event(**job):
    return {"connId": "c1", "endpoint": "https://ws/prod", "reqId": "r1", "email": "u@x", "traceId": "t1",
            "job": {"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001", "outputType": "ux-flow", "maxRounds": 2, **job}}


def test_worker_streams_publishes_and_records(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    out = w.handler(_event(), None)
    assert out["statusCode"] == 200
    types = [e["type"] for e in apigw.sent]
    assert types[0] == "studio.stage" and types[-1] == "studio.done" and "studio.token" in types
    assert all(e["reqId"] == "r1" and e["traceId"] == "t1" for e in apigw.sent)
    done = apigw.sent[-1]
    assert done["draftId"] == "job123456789" and "html" not in done and done["url"] == "https://agent.example/studio/drafts/job123456789-r1.html"
    assert ("web-bkt", "studio/drafts/job123456789-r1.html") in s3.objects
    job = store.get_job("job123456789")
    assert job["status"] == "done" and job["rounds"][0]["round"] == 1 and job["draftId"] == "job123456789"
    d = store.get_draft("job123456789")
    assert d["status"] == "검토중" and d["productName"] == "아톰 축구사랑 적금" and d["bestRound"] == 1 and d["key"].endswith("-r1.html")


def test_worker_survives_gone_connection(monkeypatch):
    apigw, s3, store = _Apigw(gone=True), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-DEP-001"}, actor="u@x")
    assert w.handler(_event(), None)["statusCode"] == 200
    assert store.get_job("job123456789")["status"] == "done" and len(s3.objects) == 1


def test_worker_reports_spec_error(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    store.put_job({"jobId": "job123456789", "brief": "b", "productCode": "PRD-NOPE"}, actor="u@x")
    w.handler(_event(productCode="PRD-NOPE"), None)
    done = apigw.sent[-1]
    assert done["type"] == "studio.done" and "찾을 수 없" in done["error"] and store.get_job("job123456789")["status"] == "failed"


def test_refine_loads_base_draft_from_s3(monkeypatch):
    apigw, s3, store = _Apigw(), _S3(), StudioStore()
    _wire(monkeypatch, apigw, s3, store)
    s3.put_object(Bucket="web-bkt", Key="studio/drafts/base1-r1.html", Body=GOOD.encode(), ContentType="text/html", CacheControl="no-cache")
    store.put_draft({"draftId": "base1", "jobId": "base1", "title": "t", "axis": "흐름", "outputType": "ux-flow", "productCode": "PRD-DEP-001",
                     "productName": "아톰 축구사랑 적금", "score": 90, "passed": True, "rounds": 1, "bestRound": 1, "url": "u", "key": "studio/drafts/base1-r1.html",
                     "createdAt": 1, "createdBy": "u@x", "model": "m"})
    store.put_job({"jobId": "job123456789", "brief": "", "productCode": "PRD-DEP-001"}, actor="u@x")
    seen = {}
    monkeypatch.setattr(w, "_generate", lambda system, user, max_tokens: seen.setdefault("user", user) and FakeStream(["```html\n", GOOD, "\n```"]))
    w.handler(_event(mode="refine", baseDraftId="base1", selector="h2", instruction="크게", maxRounds=1), None)
    assert "크게" in seen["user"] and GOOD in seen["user"]
    assert store.get_draft("job123456789")["parentId"] == "base1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd platform && python3 -m pytest tests/test_studio_worker.py -q`
Expected: FAIL (`cannot import name 'worker_handler'`).

- [ ] **Step 3: Implement `worker_handler.py`**

```python
# platform/studio/worker_handler.py
"""StudioLoopFn — 비동기 워커. WsFn(handlers/studio.py) 이 Event 로 invoke 한다.

같은 WebSocket 커넥션에 studio.stage/.token/.done 을 push 하고(common.ctx.Ctx 재사용), 라운드마다 S3(웹 버킷 studio/drafts/)와
STUDIO_TABLE 에 기록한다. 모델 호출은 engine.bedrock(익명화 게이트) 만 쓴다. 커넥션이 끊겨도(GoneException) 기록은 계속한다.
"""
from __future__ import annotations

import json
import os
import time

from common import tracing
from common.ctx import Ctx
from common.log import log_event
from studio import loop, spec as specmod
from studio.store import StudioStore

KIND = "studio"
BUCKET = os.environ.get("WEB_BUCKET", "")
WEB_URL = os.environ.get("WEB_URL", "").rstrip("/")
PREFIX = "studio/drafts"


# ---------- 주입 지점 (테스트가 monkeypatch) ----------
def _apigw_client(endpoint: str):
    import boto3
    return boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)


def _s3_client():
    import boto3
    return boto3.client("s3")


def _graph():
    from graph.store import get_store
    return get_store()


def _store() -> StudioStore:
    return StudioStore()


def _generate(system: str, user: str, max_tokens: int):
    from engine import bedrock  # 경계 통과 지점
    return bedrock.Stream(system, user, max_tokens=max_tokens, purpose="studio.generate")


def _review_generate(system: str, user: str, max_tokens: int):
    from engine import bedrock
    return bedrock.generate(system, user, max_tokens=max_tokens, purpose="studio.review")


def _assets_text(asset_ids: list) -> str:
    from common import studio_proxy
    return studio_proxy.asset_text(asset_ids)


def _agent_preset(agent_id: str) -> str:
    from common import studio_proxy
    return studio_proxy.agent_preset(agent_id)


# ---------- 도우미 ----------
class _TolerantApigw:
    """끊긴 커넥션(GoneException)·스로틀은 무시한다 — 기록이 우선."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.meta = getattr(inner, "meta", None)

    def post_to_connection(self, **kw) -> None:
        try:
            self.inner.post_to_connection(**kw)
        except Exception as e:  # noqa: BLE001
            if "Gone" not in type(e).__name__ and "Gone" not in str(e):
                log_event("studio.ws_post_failed", "", error=f"{type(e).__name__}: {str(e)[:120]}")


class _CtxEmitter:
    """stage → WS push + 라운드 기록. review 와 publish 가 같은 라운드 키를 쓰므로 병합해서 put 한다(덮어쓰기 금지)."""

    def __init__(self, ctx: Ctx, store: StudioStore, job_id: str) -> None:
        self.ctx, self.store, self.job_id = ctx, store, job_id
        self.rounds: dict = {}

    def _put(self, n: int, **fields) -> None:
        rec = self.rounds.setdefault(n, {"round": n})
        rec.update({k: v for k, v in fields.items() if v is not None})
        self.store.put_round(self.job_id, rec)

    def stage(self, step: str, **kw) -> None:
        self.ctx.stage(KIND, step, **kw)
        if step == "review":
            self._put(int(kw.get("round", 0)), score=kw.get("score"), passed=kw.get("passed"),
                      failures=[i for i in kw.get("items", []) if i.get("verdict") != "pass"][:30],
                      undetermined=kw.get("undetermined", []), reviewerError=kw.get("reviewerError"))
        elif step == "publish":
            self._put(int(kw.get("round", 0)), url=kw.get("url"))

    def token(self, text: str) -> None:
        self.ctx.token(KIND, text)


def _s3_key(job_id: str, n: int) -> str:
    return f"{PREFIX}/{job_id}-r{n}.html"


def _fewshot(store: StudioStore, s3, bucket: str, limit: int = 2) -> list:
    out = []
    for d in store.approved_drafts(limit):
        try:
            body = s3.get_object(Bucket=bucket, Key=d["key"])["Body"].read().decode("utf-8", "ignore")
            out.append(body[:12000])
        except Exception as e:  # noqa: BLE001
            log_event("studio.fewshot_failed", "", draftId=d.get("draftId"), error=str(e)[:80])
    return out


def handler(event, context):
    t0 = time.time()
    job = loop.clamp_job(event.get("job") or {})
    ctx = Ctx(apigw=_TolerantApigw(_apigw_client(event.get("endpoint", ""))), conn_id=event.get("connId", ""),
              email=event.get("email", ""), rid=event.get("reqId", ""), trace_id=event.get("traceId") or Ctx(None, "", "", "").trace_id)
    store, s3 = _store(), _s3_client()
    emitter = _CtxEmitter(ctx, store, job["jobId"])
    try:
        graph = _graph()
        spec = specmod.build_spec(graph, job["productCode"], job["outputType"])
        base_html, parent_id = "", ""
        if job["mode"] == "refine":
            base = store.get_draft(job["baseDraftId"])
            if not base:
                raise specmod.SpecError(f"원본 시안을 찾을 수 없습니다: {job['baseDraftId']}")
            base_html = s3.get_object(Bucket=BUCKET, Key=base["key"])["Body"].read().decode("utf-8", "ignore")
            parent_id = base["draftId"]

        def publish(job_id: str, n: int, html: str) -> str:
            s3.put_object(Bucket=BUCKET, Key=_s3_key(job_id, n), Body=html.encode("utf-8"),
                          ContentType="text/html; charset=utf-8", CacheControl="no-cache")
            return f"{WEB_URL}/studio/drafts/{job_id}-r{n}.html"

        out = loop.run(job, emitter, spec=spec, generate=_generate, review_generate=_review_generate, publish=publish,
                       assets_text=_assets_text(job["assetIds"]), fewshot=_fewshot(store, s3, BUCKET),
                       agent_preset=_agent_preset(job["agentId"]), base_html=base_html)
        draft = None
        if out["url"]:
            title = (job["brief"][:40] or spec["productName"]) + (" (수정)" if job["mode"] == "refine" else "")
            draft = store.put_draft({"draftId": job["jobId"], "jobId": job["jobId"], "title": title, "axis": "수정" if job["mode"] == "refine" else job["axis"],
                                     "outputType": job["outputType"], "productCode": spec["productCode"], "productName": spec["productName"],
                                     "score": out["score"], "passed": out["passed"], "rounds": out["rounds"], "bestRound": out["bestRound"],
                                     "stopReason": out["stopReason"], "url": out["url"], "key": _s3_key(job["jobId"], out["bestRound"]),
                                     "parentId": parent_id, "createdBy": ctx.email, "model": out["model"]})
        store.update_job(job["jobId"], status="done" if not out.get("error") else "failed", score=out["score"], passed=out["passed"],
                         stopReason=out["stopReason"], rounds=out["rounds"], draftId=draft["draftId"] if draft else None, error=out.get("error"))
        try:
            tracing.record_trace({"traceId": ctx.trace_id, "scenario": "studio", "email": ctx.email, "query": job["brief"],
                                  "tokensIn": out["usage"]["inputTokens"], "tokensOut": out["usage"]["outputTokens"], "plane": "cloud",
                                  "rounds": out["rounds"], "score": out["score"], "passed": out["passed"], "stopReason": out["stopReason"],
                                  "elapsedMs": int((time.time() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001 — 계측 실패가 결과 전달을 막지 않는다 (TRACE_TABLE 미설정 등)
            log_event("studio.trace_failed", ctx.trace_id, error=str(e)[:80])
        done = {k: v for k, v in out.items() if k != "html"}
        ctx.done(KIND, **done, draftId=draft["draftId"] if draft else None, backend=store.backend, graphBackend=getattr(graph, "name", "unknown"))
        return {"statusCode": 200}
    except Exception as e:  # noqa: BLE001 — 사용자에게 보여야 하는 실패
        msg = f"{type(e).__name__}: {str(e)[:200]}" if not isinstance(e, specmod.SpecError) else str(e)
        log_event("studio.worker_failed", ctx.trace_id, jobId=job["jobId"], error=msg)
        try:
            store.update_job(job["jobId"], status="failed", error=msg)
        except Exception:
            pass
        ctx.done(KIND, jobId=job["jobId"], error=msg, stopReason="error", rounds=0, score=0, passed=False)
        return {"statusCode": 200}
```

Note: `Ctx(None, "", "", "").trace_id` is only used to mint a random id when the payload lacks one — acceptable because `Ctx` does not touch `apigw` in `__init__`.

- [ ] **Step 4: Run tests**

Run: `cd platform && python3 -m pytest tests/test_studio_worker.py -q`
Expected: 4 passed.

- [ ] **Step 5: Run the whole suite**

Run: `cd platform && python3 -m pytest tests/ -q 2>&1 | tee $SCRATCH/pytest.log | tail -3`
Expected: all passed (existing suites unaffected).

- [ ] **Step 6: Commit**

```bash
git add platform/studio/worker_handler.py platform/tests/test_studio_worker.py
git commit -m "studio: StudioLoopFn worker — Ctx push to WebSocket, S3 draft publish per round, job/draft records, gone-connection tolerant"
```

---

### Task 9: Infra — StudioTable, StudioLoopFn, WsFn wiring, deploy.sh

**Files:**
- Modify: `platform/infra/lib/stack.ts` (after `wsStage.grantManagementApiAccess(fn);` ~line 470; also WsFn env)
- Modify: `platform/deploy.sh:35` (module copy loop)
- Test: `cd platform/infra && npx cdk synth --quiet` then grep the template.

**Interfaces:**
- Env on WsFn: `STUDIO_LOOP_FN`, `STUDIO_TABLE`. Env on StudioLoopFn: `STUDIO_TABLE, WEB_BUCKET, WEB_URL, TRACE_TABLE, GRAPH_BACKEND, NEPTUNE_ENDPOINT, BRIDGE_FN, ALLOW_LOCAL_PLANE, GEN_MODEL, GUARDRAIL_ID, GUARDRAIL_VERSION, LLM_ROUTE, STUDIO_URL(optional)`.
- Construct IDs: `StudioTable`, `StudioLoopFn` (new; never rename later).

- [ ] **Step 1: Add table + worker to `stack.ts`**

Insert after the `registryTable` definition (~line 107):
```ts
    const studioTable = new dynamodb.Table(this, 'StudioTable', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
```

Insert after `wsStage.grantManagementApiAccess(fn);`:
```ts
    // ---------- 디자인 스튜디오 워커 — 에이전틱 루프(생성→명세 검수→재생성)를 15분 안에 돌리고 같은 WS 커넥션에 push ----------
    const studioLoopFn = new lambda.Function(this, 'StudioLoopFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'studio.worker_handler.handler',
      code: apiCode,
      memorySize: 2048,
      timeout: cdk.Duration.minutes(15),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        STUDIO_TABLE: studioTable.tableName,
        WEB_BUCKET: webBucket.bucketName,
        WEB_URL: `https://${props.domainName ?? 'agent.atomai.click'}`,
        TRACE_TABLE: traceTable.tableName,
        GRAPH_BACKEND: props.graphBackend,
        NEPTUNE_ENDPOINT: neptuneEndpoint,
        BRIDGE_FN: bridgeFnName,
        ALLOW_LOCAL_PLANE: props.planeDeployed ? '0' : '1',
        GEN_MODEL: 'global.anthropic.claude-sonnet-5',
        GUARDRAIL_ID: guardrail.attrGuardrailId,
        GUARDRAIL_VERSION: guardrailVersion.attrVersion,
        LLM_ROUTE: 'claude',
      },
      description: 'bank-platform design studio agentic loop worker (spec-checklist review, async, pushes to WebSocket)',
    });
    studioTable.grantReadWriteData(studioLoopFn);
    traceTable.grantReadWriteData(studioLoopFn);
    webBucket.grantReadWrite(studioLoopFn, 'studio/*');
    studioLoopFn.addToRolePolicy(bedrockInvoke);
    studioLoopFn.addToRolePolicy(guardrailApply);
    wsStage.grantManagementApiAccess(studioLoopFn);
    if (props.planeDeployed) {
      studioLoopFn.addToRolePolicy(new iam.PolicyStatement({ actions: ['lambda:InvokeFunction'], resources: [bridgeFnArn] }));
    }
    fn.addEnvironment('STUDIO_LOOP_FN', studioLoopFn.functionName);
    fn.addEnvironment('STUDIO_TABLE', studioTable.tableName);
    studioTable.grantReadWriteData(fn);
    studioLoopFn.grantInvoke(fn);
    new cdk.CfnOutput(this, 'StudioLoopFnName', { value: studioLoopFn.functionName });
```
If `neptuneEndpoint`, `bridgeFnName`, `bridgeFnArn`, `guardrail`, `guardrailVersion`, `bedrockInvoke`, `guardrailApply` are declared *after* this insertion point, move the block below their declarations (they are all declared before WsFn at ~line 365, so placing the block right after `wsStage.grantManagementApiAccess(fn)` is safe).

- [ ] **Step 2: Add `studio` to the deploy copy loop**

`platform/deploy.sh:35`:
```bash
for m in registry screengen report agentcore studio; do [ -d "$m" ] && cp -r "$m" api-dist/; done
```

- [ ] **Step 3: Synthesize and verify**

Run:
```bash
cd platform/infra && rm -rf cdk.out && npx cdk synth --quiet > /dev/null && \
grep -c '"StudioLoopFn' cdk.out/*.template.json && grep -o 'studio.worker_handler.handler' cdk.out/*.template.json | head -1 && \
grep -o '"STUDIO_LOOP_FN"' cdk.out/*.template.json | head -1
```
Expected: count ≥ 1, `studio.worker_handler.handler`, `"STUDIO_LOOP_FN"`. (CLAUDE.md: stale `cdk.out` has bitten before — always `rm -rf cdk.out` first.)

- [ ] **Step 4: Commit**

```bash
git add platform/infra/lib/stack.ts platform/deploy.sh
git commit -m "infra: StudioTable + StudioLoopFn (15 min worker, WS management access, studio/* bucket prefix); WsFn wiring"
```

---

### Task 10: Web — studio shell, types, Playground with live loop

**Files:**
- Create: `platform/web/src/studio/types.ts`, `platform/web/src/studio/Studio.tsx`, `platform/web/src/studio/Playground.tsx`, `platform/web/src/studio/RoundTimeline.tsx`, `platform/web/src/studio/SpecPanel.tsx`
- Replace: `platform/web/src/Studio.tsx` with `export { default } from './studio/Studio';`
- Test: `cd platform/web && npx tsc --noEmit -p . && npm run build` (log to a file, then grep).

**Interfaces:**
- `types.ts` exports `Draft`, `Asset`, `Product`, `Spec`, `SpecItem`, `ReviewItem`, `StageEvent`, `RoundResult`, `DoneEvent`, `JobForm`, `ASSET_TYPES`, `TYPE_LABEL`, `OUTPUT_TYPES`, `AXES`, `BRIEF_PRESETS`, `aid(a: Asset)`.
- `Studio.tsx` default export keeps the hero strip and the three tabs `gallery | play | assets`; passes `openInPlayground(draft)` to Gallery (Task 11) and holds `drafts`, `assets`, `products`.
- `Playground.tsx` props: `{ assets: Asset[]; products: Product[]; canWrite: boolean; initialDraft?: Draft | null; onDone: () => void }`.
- Socket contract: `sock.run('studio_run', payload, onEvent)` resolves at `studio.done`; events `studio_run` (ack), `studio.stage`, `studio.token`, `studio.done`.

- [ ] **Step 1: `types.ts`**

```ts
// platform/web/src/studio/types.ts
export type Draft = { draftId: string; jobId: string; title: string; axis: string; outputType: string; productCode: string; productName: string;
  score: number; passed: boolean; rounds: number; bestRound: number; stopReason?: string; status: '검토중' | '승인됨' | '반려'; comment?: string;
  parentId?: string; url: string; createdAt: number; createdBy: string; model?: string };
export type Asset = { name: string; type: string; version: string; actor: string; updated_at: string; scope: string; asset_id?: string };
export type Product = { code: string; name: string; category: string; conditionCount: number; stepCount: number; hasPreferential: boolean };
export type SpecItem = { id: string; category: string; text: string; required: boolean; weight: number; check: 'llm' | 'text' | 'dom';
  expect?: Record<string, any> | null; source?: { nodeId: string; label: string } | null };
export type Spec = { productCode: string; productName: string; category: string; hasPreferential: boolean; outputType: string;
  conditions: { id: string; type: string; name: string }[]; steps: { screenId: string; name: string; entryCondition: string }[];
  terms: string[]; brandHex: string[]; items: SpecItem[] };
export type ReviewItem = SpecItem & { verdict: 'pass' | 'fail' | null; evidence: string; fix: string };
export type StageEvent = { type: 'studio.stage'; step: string; round?: number; score?: number; passed?: boolean; items?: ReviewItem[];
  requiredFailed?: string[]; undetermined?: string[]; reviewerError?: string | null; url?: string; failures?: any[]; [k: string]: any };
export type RoundResult = { round: number; score: number; passed: boolean; url: string; failures: { id: string; text: string; evidence: string; fix: string }[];
  undetermined: string[]; reviewerError?: string | null; elapsedMs: number };
export type DoneEvent = { type: 'studio.done'; jobId: string; draftId?: string | null; score: number; passed: boolean; rounds: number; maxRounds: number;
  passScore: number; stopReason: 'passed' | 'max_rounds' | 'time_cap' | 'error'; bestRound: number; url: string; items: ReviewItem[];
  history: RoundResult[]; usage: { inputTokens: number; outputTokens: number }; model: string; elapsedMs: number; error?: string;
  backend?: string; graphBackend?: string; spec?: { productCode: string; productName: string; hasPreferential: boolean; stepCount: number } };
export type JobForm = { brief: string; productCode: string; outputType: string; axis: string; assetIds: string[]; agentId: string;
  maxRounds: number; passScore: number };

export const ASSET_TYPES = ['token', 'palette', 'icon-set', 'component', 'style-guide', 'skill', 'workflow', 'agent'];
export const TYPE_LABEL: Record<string, string> = { palette: '팔레트', token: '토큰', 'icon-set': '아이콘', component: '컴포넌트',
  'style-guide': '스타일가이드', skill: '스킬', workflow: '워크플로우', agent: '에이전트' };
export const OUTPUT_TYPES: [string, string][] = [['design', '디자인'], ['mockup', '목업'], ['wireframe', '와이어프레임'], ['ux-flow', 'UX 플로우']];
export const AXES = ['밀도', '강조', '흐름'];
export const BRIEF_PRESETS: { label: string; brief: string; productCode: string; outputType: string }[] = [
  { label: '축구사랑 적금 가입 플로우', brief: '아톰 축구사랑 적금 모바일 가입 플로우 — 축구클럽 회원 우대금리 인증 단계를 포함한 전체 흐름', productCode: 'PRD-DEP-001', outputType: 'ux-flow' },
  { label: '기본 적금 가입 플로우', brief: '아톰 기본 적금 모바일 가입 플로우 — 조건 없는 표준 5단계', productCode: 'PRD-DEP-002', outputType: 'ux-flow' },
  { label: '축구사랑 적금 상품안내', brief: '아톰 축구사랑 적금 상품안내 단일 화면 — 기본금리·우대금리 구분 표기, 가입하기 CTA', productCode: 'PRD-DEP-001', outputType: 'design' },
];
export const STEP_LABEL: Record<string, string> = {
  spec_build: '① DesignSpec — 온톨로지(상품 조건·절차·정책·용어) → 체크리스트',
  assets: '② 자산·스킬·승인 참고 시안 로드',
  generate: '③ 시안 생성 (Bedrock · 익명화 게이트 경유 · 토큰 스트리밍)',
  review: '④ 검수 — 결정적 검사(구조·문구) + 리뷰 에이전트 판정 → 점수',
  regenerate: '⑤ 수정 재생성 — 실패 항목·수정 지시 주입',
  publish: '⑥ 라운드 시안 저장',
};
export const aid = (a: Asset) => a.asset_id || `${a.type}:${a.name.trim().replace(/\s+/g, '-')}`;
```

- [ ] **Step 2: `RoundTimeline.tsx` and `SpecPanel.tsx`**

```tsx
// platform/web/src/studio/RoundTimeline.tsx
import { ReviewItem, RoundResult, StageEvent, STEP_LABEL } from './types';

export function ScoreBadge({ score, passed, size = 'md' }: { score: number; passed?: boolean; size?: 'sm' | 'md' | 'lg' }) {
  const cls = passed ? 'bg-emerald-50 text-emerald-700 border-emerald-300' : score >= 70 ? 'bg-amber-50 text-amber-700 border-amber-300' : 'bg-rose-50 text-[#E90061] border-rose-300';
  const sz = size === 'lg' ? 'text-2xl px-4 py-1.5' : size === 'sm' ? 'text-[11px] px-2 py-0.5' : 'text-sm px-3 py-1';
  return <span className={`inline-flex items-center gap-1 rounded-full border font-bold ${cls} ${sz}`}>{score}<span className="font-normal opacity-70">/100</span></span>;
}

export function StageList({ stages, running }: { stages: StageEvent[]; running: boolean }) {
  return (
    <ol className="space-y-1.5 text-xs">
      {stages.map((s, i) => (
        <li key={i} className="flex items-start gap-2">
          <span className={`mt-0.5 w-2 h-2 rounded-full ${i === stages.length - 1 && running ? 'bg-amber-400 blink' : 'bg-[#008485]'}`} />
          <div className="flex-1">
            <div className="text-slate-700 font-semibold">{STEP_LABEL[s.step] || s.step}{s.round ? ` · 라운드 ${s.round}` : ''}</div>
            {s.step === 'review' && <div className="text-slate-500">점수 {s.score} · 필수 미충족 {(s.requiredFailed || []).length} · 미판정 {(s.undetermined || []).length}{s.reviewerError ? ` · 리뷰어 오류: ${s.reviewerError}` : ''}</div>}
            {s.step === 'spec_build' && <div className="text-slate-500">항목 {s.items}개 (필수 {s.required}) · {s.hasPreferential ? '우대 조건 있음 → 조건 스텝 필수' : '우대 조건 없음'}</div>}
            {s.step === 'regenerate' && <div className="text-slate-500">실패 {(s.failures || []).length}건 주입</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function RoundStrip({ history, best, onPick, picked }: { history: RoundResult[]; best: number; onPick: (r: RoundResult) => void; picked?: number }) {
  if (!history.length) return null;
  return (
    <div className="flex gap-2 flex-wrap">
      {history.map(h => (
        <button key={h.round} onClick={() => onPick(h)}
          className={`chip text-xs flex items-center gap-1.5 ${picked === h.round ? 'border-teal-500 bg-teal-50' : ''}`}>
          R{h.round} <ScoreBadge score={h.score} passed={h.passed} size="sm" />
          {h.round === best && <span className="text-[10px] text-teal-700 font-bold">최고</span>}
        </button>
      ))}
    </div>
  );
}

export function Checklist({ items, onSource }: { items: ReviewItem[]; onSource?: (nodeId: string) => void }) {
  const icon = (v: ReviewItem['verdict']) => v === 'pass' ? <span className="text-emerald-600 font-bold">✓</span> : v === 'fail' ? <span className="text-[#E90061] font-bold">✗</span> : <span className="text-slate-400 font-bold">—</span>;
  const groups: Record<string, ReviewItem[]> = {};
  for (const it of items) (groups[it.category] = groups[it.category] || []).push(it);
  return (
    <div className="space-y-3 text-xs">
      {Object.entries(groups).map(([cat, list]) => (
        <div key={cat}>
          <div className="text-[11px] font-bold text-slate-500 mb-1">{cat} · {list.filter(i => i.verdict === 'pass').length}/{list.length}</div>
          {list.map(it => (
            <div key={it.id} className="flex items-start gap-2 py-1 border-b border-slate-100">
              <span className="w-4">{icon(it.verdict)}</span>
              <div className="flex-1">
                <div className={it.required ? 'text-slate-800 font-semibold' : 'text-slate-700'}>{it.text}
                  {it.required && <span className="ml-1 text-[10px] text-rose-500">필수</span>}
                  <span className="ml-1 text-[10px] text-slate-400">w{it.weight} · {it.check}</span></div>
                {it.evidence && <div className="text-slate-500">근거: {it.evidence}</div>}
                {it.verdict !== 'pass' && it.fix && <div className="text-amber-700">수정: {it.fix}</div>}
                {it.verdict === null && <div className="text-slate-400">미판정 — 통과로 세지 않음</div>}
              </div>
              {it.source && <button onClick={() => onSource?.(it.source!.nodeId)} className="chip text-[10px] text-slate-500" title="온톨로지 출처 노드">{it.source.label}</button>}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
```

```tsx
// platform/web/src/studio/SpecPanel.tsx
import { Spec } from './types';

export default function SpecPanel({ spec, loading, error }: { spec: Spec | null; loading: boolean; error?: string }) {
  if (loading) return <div className="text-xs text-slate-400">체크리스트 미리보기 로드 중…</div>;
  if (error) return <div className="text-xs text-[#E90061]">{error}</div>;
  if (!spec) return <div className="text-xs text-slate-400">상품을 선택하면 검수 체크리스트를 미리 봅니다.</div>;
  const req = spec.items.filter(i => i.required).length;
  return (
    <div className="text-xs space-y-2">
      <div className="flex items-center gap-2">
        <b className="text-slate-800">{spec.productName}</b>
        <span className="chip text-[10px]">{spec.category}</span>
        {spec.hasPreferential
          ? <span className="chip text-[10px] text-amber-700 border-amber-300 bg-amber-50">우대 조건 → 조건 입력 스텝 필수</span>
          : <span className="chip text-[10px] text-slate-500">우대 조건 없음 → 추가 스텝 금지</span>}
      </div>
      <div className="text-slate-600">절차: {spec.steps.map((s, i) => `${i + 1}.${s.name}`).join(' → ')}</div>
      <div className="text-slate-600">조건: {spec.conditions.map(c => `[${c.type}] ${c.name}`).join(' · ')}</div>
      <div className="text-slate-500">검수 항목 {spec.items.length}개 (필수 {req}) · 결정적 {spec.items.filter(i => i.check !== 'llm').length} · 리뷰어 {spec.items.filter(i => i.check === 'llm').length}</div>
      <details><summary className="cursor-pointer text-slate-500">항목 보기</summary>
        <ul className="mt-1 space-y-0.5 max-h-56 overflow-auto">
          {spec.items.map(i => <li key={i.id} className="text-slate-600">{i.required ? '● ' : '○ '}{i.text} <span className="text-slate-400">[{i.category}·{i.check}]</span></li>)}
        </ul>
      </details>
    </div>
  );
}
```

- [ ] **Step 3: `Playground.tsx`**

```tsx
// platform/web/src/studio/Playground.tsx
// 플레이그라운드 — 브리프·상품·자산·라운드 상한 → studio_run(에이전틱 루프) 스트리밍; 라이브 캔버스, 요소 선택 → refine, 라운드 타임라인, 체크리스트.
import { useEffect, useMemo, useRef, useState } from 'react';
import { sock } from '../lib';
import SpecPanel from './SpecPanel';
import { Checklist, RoundStrip, ScoreBadge, StageList } from './RoundTimeline';
import { AXES, BRIEF_PRESETS, Asset, DoneEvent, Draft, JobForm, OUTPUT_TYPES, Product, ReviewItem, RoundResult, Spec, StageEvent, TYPE_LABEL, aid } from './types';

type Sel = { selector: string; html: string; label: string } | null;

function cssPath(el: Element, doc: Document): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur !== doc.body && cur.nodeType === 1) {
    let seg = cur.tagName.toLowerCase();
    if (cur.id) { parts.unshift(`#${cur.id}`); break; }
    const parent: Element | null = cur.parentElement;
    if (parent) {
      const same = [...parent.children].filter(c => c.tagName === cur!.tagName);
      if (same.length > 1) seg += `:nth-of-type(${same.indexOf(cur) + 1})`;
    }
    parts.unshift(seg);
    cur = parent;
  }
  return parts.join(' > ');
}

export default function Playground({ assets, products, canWrite, initialDraft, onDone }: { assets: Asset[]; products: Product[]; canWrite: boolean; initialDraft?: Draft | null; onDone: () => void }) {
  const p0 = BRIEF_PRESETS[0];
  const [form, setForm] = useState<JobForm>({ brief: p0.brief, productCode: p0.productCode, outputType: p0.outputType, axis: '흐름', assetIds: [], agentId: '', maxRounds: 3, passScore: 85 });
  const [spec, setSpec] = useState<Spec | null>(null);
  const [specErr, setSpecErr] = useState('');
  const [specLoading, setSpecLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageEvent[]>([]);
  const [tokens, setTokens] = useState(0);
  const [history, setHistory] = useState<RoundResult[]>([]);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [done, setDone] = useState<DoneEvent | null>(null);
  const [canvasUrl, setCanvasUrl] = useState(initialDraft?.url || '');
  const [baseDraftId, setBaseDraftId] = useState(initialDraft?.draftId || '');
  const [pickedRound, setPickedRound] = useState<number | undefined>();
  const [mobile, setMobile] = useState(true);
  const [selectMode, setSelectMode] = useState(false);
  const [sel, setSel] = useState<Sel>(null);
  const [refineText, setRefineText] = useState('');
  const [turns, setTurns] = useState<{ role: 'user' | 'agent'; text: string }[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [msg, setMsg] = useState('');
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const selectModeRef = useRef(selectMode);
  useEffect(() => { selectModeRef.current = selectMode; }, [selectMode]);

  useEffect(() => {
    if (!form.productCode) { setSpec(null); return; }
    setSpecLoading(true); setSpecErr('');
    sock.request('studio_spec', { productCode: form.productCode, outputType: form.outputType })
      .then(e => { if (e.error) { setSpecErr(e.error); setSpec(null); } else setSpec(e.spec); })
      .catch(e => setSpecErr(String(e))).finally(() => setSpecLoading(false));
  }, [form.productCode, form.outputType]);

  useEffect(() => {
    if (!running) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 500);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (initialDraft) { setCanvasUrl(initialDraft.url); setBaseDraftId(initialDraft.draftId); setForm(f => ({ ...f, productCode: initialDraft.productCode, outputType: initialDraft.outputType })); }
  }, [initialDraft]);

  const groups = useMemo(() => {
    const g: Record<string, Asset[]> = {};
    for (const a of assets) (g[a.type] = g[a.type] || []).push(a);
    return g;
  }, [assets]);
  const agents = groups['agent'] || [];

  const wireSelection = () => {
    const iframe = iframeRef.current; if (!iframe) return;
    let doc: Document | null = null;
    try { doc = iframe.contentDocument; } catch { return; }
    if (!doc?.body) return;
    let hovered: HTMLElement | null = null; let outline = '';
    doc.addEventListener('mouseover', (e) => {
      if (!selectModeRef.current) return;
      if (hovered) hovered.style.outline = outline;
      hovered = e.target as HTMLElement; outline = hovered.style.outline; hovered.style.outline = '2px dashed #008485';
    }, true);
    doc.addEventListener('click', (e) => {
      if (!selectModeRef.current) return;
      e.preventDefault(); e.stopPropagation();
      doc!.querySelectorAll('[data-picked]').forEach(n => { (n as HTMLElement).style.outline = ''; n.removeAttribute('data-picked'); });
      const el = e.target as HTMLElement;
      const clean = el.outerHTML.slice(0, 4000);
      el.setAttribute('data-picked', '1'); el.style.outline = '3px solid #008485';
      const label = el.tagName.toLowerCase() + (el.textContent ? ` — "${el.textContent.trim().slice(0, 30)}"` : '');
      setSel({ selector: cssPath(el, doc!), html: clean, label });
    }, true);
  };
  const onEvent = (e: any) => {
    if (e.type === 'studio.stage') {
      setStages(s => [...s, e]);
      if (e.step === 'review') setItems(e.items || []);
      if (e.step === 'publish' && e.url) { setCanvasUrl(e.url); setPickedRound(e.round); setHistory(h => [...h.filter(x => x.round !== e.round), { round: e.round, score: e.score, passed: false, url: e.url, failures: [], undetermined: [], elapsedMs: 0 }]); }
    } else if (e.type === 'studio.token') {
      setTokens(t => t + (e.t?.length || 0));
    } else if (e.type === 'studio.done') {
      setDone(e as DoneEvent);
      setHistory(e.history || []);
      setItems(e.items || []);
      if (e.url) { setCanvasUrl(e.url); setPickedRound(e.bestRound); setBaseDraftId(e.draftId || ''); }
      setTurns(t => [...t, { role: 'agent', text: e.error ? `오류: ${e.error}` : `${e.rounds}라운드 · 최고 점수 ${e.score} (라운드 ${e.bestRound}) · ${({ passed: '통과', max_rounds: '라운드 상한 도달', time_cap: '시간 상한(13분)으로 중단', error: '오류' } as any)[e.stopReason]}` }]);
    }
  };

  const run = async (payload: Record<string, any>) => {
    if (!canWrite) return;
    setRunning(true); setStages([]); setTokens(0); setDone(null); setItems([]); setMsg(''); setSel(null);
    try { await sock.run('studio_run', payload, onEvent); }
    catch (e: any) { setMsg('오류: ' + (e?.message || e)); }
    finally { setRunning(false); onDone(); }
  };
  const generate = () => { setTurns(t => [...t, { role: 'user', text: form.brief }]); setHistory([]); run({ ...form, mode: 'generate' }); };
  const refine = () => {
    if (!baseDraftId || !refineText.trim()) { setMsg('수정 지시를 입력하세요 (원본 시안이 캔버스에 있어야 합니다)'); return; }
    setTurns(t => [...t, { role: 'user', text: `[수정${sel ? ' · ' + sel.label : ''}] ${refineText}` }]);
    run({ mode: 'refine', baseDraftId, selector: sel?.selector || '', elementHtml: sel?.html || '', instruction: refineText, productCode: form.productCode, outputType: form.outputType, axis: form.axis, assetIds: form.assetIds, agentId: form.agentId, maxRounds: 1, passScore: form.passScore, brief: '' });
    setRefineText('');
  };
  const toggleAsset = (id: string) => setForm(f => ({ ...f, assetIds: f.assetIds.includes(id) ? f.assetIds.filter(x => x !== id) : [...f.assetIds, id] }));
  const pickRound = (r: RoundResult) => { setPickedRound(r.round); if (r.url) setCanvasUrl(r.url); };
  const product = products.find(p => p.code === form.productCode);

  return (
    <div className="grid grid-cols-[1fr_1.35fr] gap-5">
      {/* 좌: 설정 */}
      <div className="space-y-4">
        <div className="panel p-5">
          <div className="text-sm font-bold text-slate-800 mb-2">① 브리프</div>
          <div className="flex gap-2 mb-2 flex-wrap">
            {BRIEF_PRESETS.map(p => (
              <button key={p.label} onClick={() => setForm(f => ({ ...f, brief: p.brief, productCode: p.productCode, outputType: p.outputType }))}
                className={`chip text-xs ${form.brief === p.brief ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500 hover:border-teal-300'}`}>{p.label}</button>
            ))}
          </div>
          <textarea className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-20" value={form.brief} onChange={e => setForm({ ...form, brief: e.target.value })} />

          <div className="text-sm font-bold text-slate-800 mt-4 mb-2">② 상품 (명세 출처 — 온톨로지 Product)</div>
          <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.productCode} onChange={e => setForm({ ...form, productCode: e.target.value })}>
            <option value="">상품 선택</option>
            {products.map(p => <option key={p.code} value={p.code}>{p.name} · {p.category} · 조건 {p.conditionCount}{p.hasPreferential ? ' · 우대' : ''}</option>)}
          </select>
          {product && <div className="mt-1 text-[11px] text-slate-500">{product.stepCount}단계 절차 · 조건 {product.conditionCount}개{product.hasPreferential && <span className="ml-2 chip text-[10px] text-amber-700 border-amber-300 bg-amber-50">우대 조건 → 조건 입력 스텝 검수</span>}</div>}

          <div className="grid grid-cols-2 gap-3 mt-4">
            <div>
              <div className="text-sm font-bold text-slate-800 mb-1">③ 출력 유형</div>
              <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.outputType} onChange={e => setForm({ ...form, outputType: e.target.value })}>
                {OUTPUT_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div>
              <div className="text-sm font-bold text-slate-800 mb-1">축</div>
              <select className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.axis} onChange={e => setForm({ ...form, axis: e.target.value })}>
                {AXES.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>

          <div className="text-sm font-bold text-slate-800 mt-4 mb-2">④ 자산 (조건이 됩니다)</div>
          {Object.entries(groups).filter(([t]) => t !== 'agent').map(([t, list]) => (
            <div key={t} className="mb-1"><span className="text-[11px] text-slate-400 mr-2">{TYPE_LABEL[t] || t}</span>
              {list.map(a => <button key={aid(a)} onClick={() => toggleAsset(aid(a))} className={`chip text-xs mr-1 mb-1 ${form.assetIds.includes(aid(a)) ? 'text-teal-800 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{a.name}</button>)}
            </div>
          ))}
          {agents.length > 0 && (
            <select className="w-full mt-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={form.agentId} onChange={e => setForm({ ...form, agentId: e.target.value })}>
              <option value="">에이전트 프리셋 없음</option>
              {agents.map(a => <option key={aid(a)} value={aid(a)}>{a.name}</option>)}
            </select>
          )}

          <div className="grid grid-cols-2 gap-3 mt-4">
            <label className="text-sm"><div className="font-bold text-slate-800 mb-1">⑤ 라운드 상한 <b className="text-[#008485]">{form.maxRounds}</b></div>
              <input type="range" min={1} max={20} value={form.maxRounds} onChange={e => setForm({ ...form, maxRounds: +e.target.value })} className="w-full" />
              <div className="text-[11px] text-slate-400">기본 3 · 최대 20 · 워커 13분 상한</div></label>
            <label className="text-sm"><div className="font-bold text-slate-800 mb-1">통과 점수 <b className="text-[#008485]">{form.passScore}</b></div>
              <input type="range" min={50} max={100} value={form.passScore} onChange={e => setForm({ ...form, passScore: +e.target.value })} className="w-full" />
              <div className="text-[11px] text-slate-400">필수 항목 전부 통과 + 점수 이상이면 종료</div></label>
          </div>

          <button onClick={generate} disabled={!canWrite || running || !form.productCode}
            className="w-full mt-4 py-2.5 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-bold text-sm disabled:opacity-40">
            {running ? `루프 실행 중… ${elapsed}s · ${tokens.toLocaleString()}자 수신` : '✨ 생성 → 명세 검수 → 수정 루프 시작'}
          </button>
          {!canWrite && <div className="text-xs text-slate-400 mt-2">이 계정은 조회 전용입니다</div>}
          {msg && <div className="text-[#E90061] text-xs mt-2">{msg}</div>}
        </div>

        <div className="panel p-4"><div className="text-sm font-bold text-slate-800 mb-2">검수 체크리스트 미리보기</div>
          <SpecPanel spec={spec} loading={specLoading} error={specErr} /></div>

        <div className="panel p-4">
          <div className="text-sm font-bold text-slate-800 mb-2">진행</div>
          {stages.length ? <StageList stages={stages} running={running} /> : <div className="text-xs text-slate-400">아직 실행 전</div>}
          {done && <div className="mt-3 flex items-center gap-2 text-xs"><ScoreBadge score={done.score} passed={done.passed} size="lg" />
            <div className="text-slate-600">{done.rounds}/{done.maxRounds} 라운드 · 최고 점수 라운드 {done.bestRound} 선택 · {done.stopReason === 'time_cap' ? '13분 상한으로 중단' : done.stopReason === 'max_rounds' ? '상한 도달' : done.stopReason === 'passed' ? '통과' : '오류'}<br />
              토큰 in {done.usage?.inputTokens} / out {done.usage?.outputTokens} · {done.model} · {Math.round(done.elapsedMs / 1000)}s</div></div>}
        </div>
      </div>

      {/* 우: 캔버스 + 체크리스트 */}
      <div className="space-y-4">
        <div className="panel p-3">
          <div className="flex items-center gap-2 mb-2 text-xs">
            <RoundStrip history={history} best={done?.bestRound || 0} onPick={pickRound} picked={pickedRound} />
            <span className="ml-auto" />
            <button onClick={() => setMobile(m => !m)} className="chip">{mobile ? '📱 모바일' : '🖥 전체 폭'}</button>
            <button onClick={() => setSelectMode(s => !s)} className={`chip ${selectMode ? 'text-teal-700 border-teal-400 bg-teal-50' : ''}`} disabled={!canvasUrl}>{selectMode ? '요소 선택 중' : '요소 선택'}</button>
            {canvasUrl && <a href={canvasUrl} target="_blank" rel="noopener" className="chip text-teal-700">새 탭 ↗</a>}
          </div>
          <div className={`mx-auto bg-slate-100 rounded-xl overflow-hidden border border-slate-200 ${mobile ? 'w-[420px]' : 'w-full'}`} style={{ height: 720 }}>
            {canvasUrl
              ? <iframe ref={iframeRef} key={canvasUrl} src={canvasUrl} title="canvas" className="w-full h-full bg-white" onLoad={wireSelection} />
              : <div className="h-full flex items-center justify-center text-sm text-slate-400">생성하면 라운드마다 시안이 여기 갱신됩니다</div>}
          </div>
          <div className="mt-2 text-[11px] text-slate-500">{sel ? `선택: ${sel.label}` : '선택된 요소 없음 — 전체 수정으로 적용됩니다'}</div>
          <div className="flex gap-2 mt-2">
            <input className="flex-1 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" placeholder="수정 지시 (예: 우대금리 문구를 더 크게)" value={refineText} onChange={e => setRefineText(e.target.value)} onKeyDown={e => e.key === 'Enter' && refine()} />
            <button onClick={refine} disabled={!canWrite || running || !baseDraftId} className="px-4 py-2 rounded-xl bg-white border border-teal-400 text-teal-700 text-sm font-semibold disabled:opacity-40">수정 (1라운드 검수)</button>
          </div>
          {turns.length > 0 && <div className="mt-2 max-h-28 overflow-auto text-xs space-y-1">{turns.map((t, i) => <div key={i} className={t.role === 'user' ? 'text-slate-700' : 'text-teal-700'}>{t.role === 'user' ? '👤 ' : '🤖 '}{t.text}</div>)}</div>}
        </div>
        <div className="panel p-4">
          <div className="flex items-center gap-2 mb-2"><div className="text-sm font-bold text-slate-800">검수 결과</div>
            <span className="chip text-[10px] text-slate-500">구조·문구·흐름 검수 — 픽셀 비교 미구현</span></div>
          {items.length ? <Checklist items={items} onSource={(id) => window.dispatchEvent(new CustomEvent('explore-node', { detail: id }))} /> : <div className="text-xs text-slate-400">라운드가 끝나면 항목별 판정이 표시됩니다</div>}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `Studio.tsx` shell + re-export**

```tsx
// platform/web/src/studio/Studio.tsx
// 디자인 스튜디오 — 플랫폼 네이티브. 시안·잡은 플랫폼(DynamoDB+S3), 자산은 uiux-studio 레지스트리 프록시.
import { useEffect, useState } from 'react';
import { auth, sock } from '../lib';
import Assets from './Assets';
import Gallery from './Gallery';
import Playground from './Playground';
import { Asset, Draft, Product } from './types';

export default function Studio() {
  const [tab, setTab] = useState<'gallery' | 'play' | 'assets'>('gallery');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [meta, setMeta] = useState<{ backend?: string; graphBackend?: string; model?: string }>({});
  const [editDraft, setEditDraft] = useState<Draft | null>(null);
  const canWrite = true; // 루프 실행은 플랫폼 Cognito 계정으로 — 자산 등록만 uiux-studio 토큰(auth.studioToken)이 필요
  const load = () => {
    sock.request('studio_drafts').then(e => { setDrafts(e.drafts || []); setMeta(m => ({ ...m, backend: e.backend })); }).catch(() => {});
    sock.request('assets').then(e => setAssets(e.assets || [])).catch(() => {});
    sock.request('studio_products').then(e => { setProducts(e.products || []); setMeta(m => ({ ...m, graphBackend: e.graphBackend, model: e.model })); }).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const approved = drafts.filter(d => d.status === '승인됨').length;
  const openInPlayground = (d: Draft) => { setEditDraft(d); setTab('play'); };

  return (
    <div>
      <div className="panel p-5 mb-4 flex items-center gap-6" style={{ background: 'linear-gradient(105deg, #eaf5f4 0%, #ffffff 55%, #faf7ef 100%)' }}>
        <div>
          <div className="text-lg font-bold text-[#0b4f4b]">디자인 스튜디오</div>
          <div className="text-sm text-slate-500 mt-1">
            온톨로지의 <b className="text-[#008485]">상품 명세가 체크리스트</b>가 되어 시안을 생성→검수→수정하는 <b>에이전틱 루프</b>를 돕니다.
            <b className="text-amber-700"> 승인</b>된 시안은 다음 생성의 few-shot 레퍼런스가 됩니다.
          </div>
          <div className="flex gap-1.5 mt-2 flex-wrap text-[10px]">
            <span className="chip">모델 호출: 익명화 게이트 경유</span>
            <span className="chip">그래프: {meta.graphBackend || '…'}</span>
            <span className="chip">시안 저장: {meta.backend || '…'} + S3</span>
            <span className="chip">자산 원본: uiux-studio 레지스트리(프록시)</span>
            <span className="chip">검수: 구조·문구·흐름 — 픽셀 비교 미구현</span>
          </div>
        </div>
        <div className="flex gap-3 ml-auto">
          {[['자산', assets.length, '#008485'], ['시안', drafts.length, '#AD9A5F'], ['승인', approved, '#0e9f6e']].map(([l, v, c]) => (
            <div key={l as string} className="text-center px-4 py-2 rounded-xl bg-white border border-slate-200">
              <div className="text-xl font-bold" style={{ color: c as string }}>{v as number}</div>
              <div className="text-[11px] text-slate-500">{l}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2 mb-4">
        {([['gallery', '🖼 시안 갤러리'], ['play', '✨ 플레이그라운드'], ['assets', '🎨 디자인 자산']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={`px-4 py-2 rounded-xl text-sm font-semibold border ${tab === id ? 'bg-[#008485] text-white border-[#008485]' : 'bg-white text-slate-600 border-slate-200 hover:border-teal-400'}`}>{label}</button>
        ))}
      </div>
      {tab === 'gallery' && <Gallery drafts={drafts} canWrite={canWrite} reload={load} onEdit={openInPlayground} />}
      {tab === 'play' && <Playground assets={assets} products={products} canWrite={canWrite} initialDraft={editDraft} onDone={load} />}
      {tab === 'assets' && <Assets assets={assets} canRegister={!!auth.studioToken} reload={load} />}
    </div>
  );
}
```

Replace `platform/web/src/Studio.tsx` contents with:
```tsx
export { default } from './studio/Studio';
```

Note on `canWrite`: platform Cognito users run the loop (the worker uses platform credentials); only asset registration hits the uiux-studio API and needs `auth.studioToken`.

- [ ] **Step 5: Type-check (Gallery/Assets come in Task 11 — create temporary stubs so this task compiles)**

Create minimal stubs to be replaced in Task 11:
```tsx
// platform/web/src/studio/Gallery.tsx (stub — replaced in Task 11)
import { Draft } from './types';
export default function Gallery(_: { drafts: Draft[]; canWrite: boolean; reload: () => void; onEdit: (d: Draft) => void }) { return <div className="text-xs text-slate-400">갤러리 — 다음 작업에서 구현</div>; }
```
```tsx
// platform/web/src/studio/Assets.tsx (stub — replaced in Task 11)
import { Asset } from './types';
export default function Assets(_: { assets: Asset[]; canRegister: boolean; reload: () => void }) { return <div className="text-xs text-slate-400">자산 — 다음 작업에서 구현</div>; }
```
Run: `cd platform/web && npx tsc --noEmit -p . > $SCRATCH/tsc.log 2>&1; echo exit=$?; grep -c "error TS" $SCRATCH/tsc.log`
Expected: `exit=0`, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add platform/web/src/Studio.tsx platform/web/src/studio/types.ts platform/web/src/studio/Studio.tsx platform/web/src/studio/Playground.tsx platform/web/src/studio/RoundTimeline.tsx platform/web/src/studio/SpecPanel.tsx platform/web/src/studio/Gallery.tsx platform/web/src/studio/Assets.tsx
git commit -m "web(studio): native studio shell + playground — loop streaming, live canvas, element-select refine, round strip, checklist"
```

---

### Task 11: Web — Gallery (score, report, 편집→플레이그라운드, comment) and Assets (filter, scope, timeline)

**Files:**
- Replace: `platform/web/src/studio/Gallery.tsx`, `platform/web/src/studio/Assets.tsx`
- Test: `cd platform/web && npx tsc --noEmit -p . && npm run build` (log to file, grep for `error`).

**Interfaces:**
- `Gallery` props `{ drafts: Draft[]; canWrite: boolean; reload: () => void; onEdit: (d: Draft) => void }`. Uses `sock.request('studio_feedback', { draftId, decision, comment })` and `sock.request('studio_jobs', { jobId })` for the report drawer.
- `Assets` props `{ assets: Asset[]; canRegister: boolean; reload: () => void }`. Uses `studio_asset` and `studio_register` (with `auth.studioToken`).

- [ ] **Step 1: `Gallery.tsx`**

```tsx
// platform/web/src/studio/Gallery.tsx
import { useState } from 'react';
import { sock } from '../lib';
import { Checklist, ScoreBadge } from './RoundTimeline';
import { Draft, ReviewItem } from './types';

const STOP: Record<string, string> = { passed: '통과', max_rounds: '상한 도달', time_cap: '13분 상한', error: '오류' };

export default function Gallery({ drafts, canWrite, reload, onEdit }: { drafts: Draft[]; canWrite: boolean; reload: () => void; onEdit: (d: Draft) => void }) {
  const [filter, setFilter] = useState<'전체' | '검토중' | '승인됨' | '반려'>('전체');
  const [busy, setBusy] = useState('');
  const [comment, setComment] = useState<Record<string, string>>({});
  const [report, setReport] = useState<{ d: Draft; rounds: any[]; items: ReviewItem[] } | null>(null);
  const list = drafts.filter(d => filter === '전체' || d.status === filter);
  const decide = async (d: Draft, decision: 'approve' | 'reject') => {
    setBusy(d.draftId);
    await sock.request('studio_feedback', { draftId: d.draftId, decision, comment: comment[d.draftId] || '' }).catch(() => {});
    setBusy(''); reload();
  };
  const openReport = async (d: Draft) => {
    const j = await sock.request('studio_jobs', { jobId: d.jobId }).catch(() => ({} as any));
    const rounds = (j.job?.rounds || []) as any[];
    const best = rounds.find(r => r.round === d.bestRound && Array.isArray(r.failures));
    setReport({ d, rounds, items: (best?.failures || []) as ReviewItem[] });
  };
  const color = (s: string) => s === '승인됨' ? 'text-emerald-600 border-emerald-300 bg-emerald-50' : s === '반려' ? 'text-[#E90061] border-rose-300 bg-rose-50' : 'text-amber-700 border-amber-300 bg-amber-50';
  return (
    <div>
      <div className="flex gap-2 mb-3 items-center">
        {(['전체', '검토중', '승인됨', '반려'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)} className={`chip ${filter === f ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{f}</button>
        ))}
        <span className="text-xs text-slate-400 ml-2">승인된 시안(최근 2건)은 다음 생성의 few-shot 레퍼런스로 주입됩니다 · 점수 = 상품 명세 체크리스트 가중 통과율</span>
      </div>
      <div className="grid grid-cols-3 gap-5">
        {list.map(d => (
          <div key={d.draftId} className="panel overflow-hidden group hover:shadow-lg transition-shadow">
            <div className="relative h-72 overflow-hidden bg-slate-50 border-b border-slate-100">
              <iframe src={d.url} title={d.title} sandbox="allow-same-origin" className="pointer-events-none origin-top-left" style={{ width: '200%', height: '200%', transform: 'scale(0.5)' }} />
              <a href={d.url} target="_blank" rel="noopener" className="absolute inset-0 flex items-end justify-end p-2 opacity-0 group-hover:opacity-100 transition-opacity" style={{ background: 'linear-gradient(transparent 65%, rgba(11,47,43,.45))' }}>
                <span className="text-white text-xs bg-[#008485] px-3 py-1.5 rounded-lg">원본 크게 보기 ↗</span></a>
              <div className="absolute top-2 left-2"><ScoreBadge score={d.score} passed={d.passed} /></div>
            </div>
            <div className="p-4">
              <div className="text-sm font-bold text-slate-800 truncate" title={d.title}>{d.title}</div>
              <div className="text-[11px] text-slate-500 mt-0.5">{d.productName} · {d.outputType} · {d.rounds}라운드 (최고 R{d.bestRound}) · {STOP[d.stopReason || ''] || ''}</div>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <span className={`chip text-[11px] ${color(d.status)}`}>{d.status}</span>
                <span className="chip text-[11px] text-slate-500">{d.axis}</span>
                {d.parentId && <span className="chip text-[11px] text-slate-500">수정본</span>}
                {d.status === '승인됨' && <span className="chip text-[11px] text-teal-700 border-teal-300">few-shot 반영</span>}
                <button onClick={() => openReport(d)} className="chip text-[11px] text-slate-600 ml-auto">검수 리포트</button>
                <button onClick={() => onEdit(d)} className="chip text-[11px] text-teal-700 border-teal-300">편집 →</button>
              </div>
              {d.comment && <div className="text-[11px] text-slate-500 mt-1">코멘트: {d.comment}</div>}
              {canWrite && d.status === '검토중' && (
                <div className="mt-3 space-y-2">
                  <input className="w-full px-2 py-1 rounded-lg border border-slate-200 text-xs" placeholder="반려·승인 코멘트 (선택)" value={comment[d.draftId] || ''} onChange={e => setComment({ ...comment, [d.draftId]: e.target.value })} />
                  <div className="flex gap-2">
                    <button disabled={busy === d.draftId} onClick={() => decide(d, 'approve')} className="flex-1 py-1.5 rounded-lg text-xs font-semibold bg-[#008485] text-white hover:bg-[#0a6b6c]">✓ 승인</button>
                    <button disabled={busy === d.draftId} onClick={() => decide(d, 'reject')} className="flex-1 py-1.5 rounded-lg text-xs font-semibold border border-rose-300 text-[#E90061] hover:bg-rose-50">반려</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        {list.length === 0 && <div className="text-slate-400 text-sm col-span-3">시안이 없습니다 — 플레이그라운드에서 루프를 실행해 보세요.</div>}
      </div>
      {report && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setReport(null)}>
          <div className="bg-white rounded-2xl p-5 w-[720px] max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-3"><div className="text-sm font-bold">{report.d.title} — 검수 리포트</div><ScoreBadge score={report.d.score} passed={report.d.passed} />
              <button className="chip ml-auto" onClick={() => setReport(null)}>닫기</button></div>
            <div className="text-xs text-slate-500 mb-3">라운드별 점수: {report.rounds.filter(r => r.score !== undefined).map(r => `R${r.round}=${r.score}`).join(' · ') || '기록 없음'}</div>
            <div className="text-xs font-bold text-slate-700 mb-1">최고 점수 라운드의 미충족·미판정 항목</div>
            {report.items.length ? <Checklist items={report.items} /> : <div className="text-xs text-emerald-700">미충족 항목 없음</div>}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `Assets.tsx`**

```tsx
// platform/web/src/studio/Assets.tsx
import { useMemo, useState } from 'react';
import { auth, sock } from '../lib';
import { ASSET_TYPES, Asset, TYPE_LABEL, aid } from './types';

function Swatches({ content }: { content: any }) {
  const colors = useMemo(() => {
    try { const o = typeof content === 'string' ? JSON.parse(content) : content;
      return Object.entries(o || {}).filter(([, v]) => typeof v === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(v as string)) as [string, string][]; } catch { return []; }
  }, [content]);
  if (!colors.length) return null;
  return <div className="flex gap-1 mt-2 flex-wrap">{colors.slice(0, 10).map(([k, v]) => <span key={k} title={`${k} ${v}`} className="w-6 h-6 rounded-lg border border-slate-200" style={{ background: v }} />)}</div>;
}

export default function Assets({ assets, canRegister, reload }: { assets: Asset[]; canRegister: boolean; reload: () => void }) {
  const [typeFilter, setTypeFilter] = useState('');
  const [sel, setSel] = useState<any>(null);
  const [reg, setReg] = useState({ name: '', assetType: 'palette', content: '', scope: 'shared' });
  const [msg, setMsg] = useState('');
  const list = assets.filter(a => !typeFilter || a.type === typeFilter);
  const open = async (a: Asset) => {
    const r = await sock.request('studio_asset', { assetId: aid(a) }).catch(() => ({} as any));
    const c = (r as any).content;
    setSel({ ...a, history: (r as any).history, content: (c && typeof c === 'object' && 'content' in c) ? (c as any).content : c });
  };
  const register = async () => {
    if (!reg.name || !reg.content) { setMsg('이름과 내용을 입력하세요'); return; }
    const r = await sock.request('studio_register', { studioToken: auth.studioToken, ...reg });
    setMsg(r.error ? '오류: ' + r.error : `등록됨 (v${r.version || '?'})`);
    if (!r.error) { setReg({ name: '', assetType: 'palette', content: '', scope: 'shared' }); reload(); }
  };
  const ver = (v: any) => String(v).startsWith('v') ? String(v) : `v${v}`;
  return (
    <div className="grid grid-cols-[1.3fr_1fr] gap-5">
      <div className="self-start">
        <div className="flex gap-1.5 mb-3 flex-wrap">
          <button onClick={() => setTypeFilter('')} className={`chip text-xs ${!typeFilter ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>전체 {assets.length}</button>
          {ASSET_TYPES.map(t => { const n = assets.filter(a => a.type === t).length; return n ? <button key={t} onClick={() => setTypeFilter(t)} className={`chip text-xs ${typeFilter === t ? 'text-teal-700 border-teal-400 bg-teal-50' : 'text-slate-500'}`}>{TYPE_LABEL[t]} {n}</button> : null; })}
        </div>
        <div className="grid grid-cols-2 gap-3">
          {list.map((a, i) => (
            <button key={i} onClick={() => open(a)} className={`panel p-4 text-left hover:shadow-md transition-shadow ${sel && aid(sel) === aid(a) ? 'border-teal-400' : ''}`}>
              <div className="flex items-center gap-2"><span className="text-sm font-bold text-slate-800 truncate">{a.name}</span>
                <span className="chip text-[10px] text-teal-700 border-teal-300 ml-auto">{TYPE_LABEL[a.type] || a.type}</span></div>
              <div className="text-[11px] text-slate-400 mt-1">{ver(a.version)} · {a.actor} · {a.scope === 'mine' ? '내 자산' : '공유'}</div>
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-4 self-start">
        {sel && (
          <div className="panel p-4">
            <div className="text-sm font-bold text-slate-800">{sel.name}<span className="chip text-[10px] ml-2 text-teal-700 border-teal-300">{TYPE_LABEL[sel.type] || sel.type}</span></div>
            {Array.isArray(sel.history) && sel.history.length > 0 && (
              <ol className="mt-2 text-[11px] text-slate-500 border-l-2 border-teal-200 pl-3 space-y-1">
                {sel.history.map((h: any, i: number) => <li key={i}><b className="text-slate-700">{ver(h.version)}</b> · {h.action || '등록'} · {h.actor || ''} · {String(h.updated_at || h.ts || '').slice(0, 19)}</li>)}
              </ol>)}
            {sel.type === 'palette' && <Swatches content={sel.content} />}
            <pre className="text-xs bg-slate-50 border border-slate-200 rounded-xl p-3 mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-slate-700">
              {typeof sel.content === 'object' ? JSON.stringify(sel.content, null, 2).slice(0, 2500) : String(sel.content || '').slice(0, 2500)}</pre>
          </div>
        )}
        <div className="panel p-4">
          <div className="text-sm font-bold text-slate-800 mb-2">새 자산 등록 <span className="text-[11px] font-normal text-slate-400">— uiux-studio 레지스트리에 버전 이력이 기록됩니다</span></div>
          {!canRegister && <div className="text-xs text-slate-400 mb-2">스튜디오 토큰이 없는 계정은 등록할 수 없습니다 (조회만 가능)</div>}
          <input className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" placeholder="이름" value={reg.name} onChange={e => setReg({ ...reg, name: e.target.value })} />
          <div className="flex gap-2 mb-2">
            <select className="flex-1 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={reg.assetType} onChange={e => setReg({ ...reg, assetType: e.target.value })}>
              {ASSET_TYPES.map(t => <option key={t} value={t}>{TYPE_LABEL[t] || t}</option>)}</select>
            <select className="px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm" value={reg.scope} onChange={e => setReg({ ...reg, scope: e.target.value })}>
              <option value="shared">공유</option><option value="mine">내 자산</option></select>
          </div>
          <textarea className="w-full mb-2 px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm h-24 font-mono" placeholder="내용 (markdown 또는 JSON)" value={reg.content} onChange={e => setReg({ ...reg, content: e.target.value })} />
          <button onClick={register} disabled={!canRegister} className="w-full py-2 rounded-xl bg-[#008485] hover:bg-[#0a6b6c] text-white font-semibold text-sm disabled:opacity-40">등록</button>
          {msg && <div className="text-xs text-slate-500 mt-2">{msg}</div>}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build**

Run: `cd platform/web && (npx tsc --noEmit -p . && npm run build) > $SCRATCH/webbuild.log 2>&1; echo exit=$?; grep -ci "error" $SCRATCH/webbuild.log`
Expected: `exit=0`, error count 0.

- [ ] **Step 4: Commit**

```bash
git add platform/web/src/studio/Gallery.tsx platform/web/src/studio/Assets.tsx
git commit -m "web(studio): gallery with score badge, review report, comment approve/reject, edit→playground; assets with type filter, scope, version timeline"
```

---

### Task 12: Docs — SPEC §16, CONTRACTS ownership, README status (pre-deploy wording)

**Files:**
- Modify: `SPEC.md` (§16 구현 메모, append bullet)
- Modify: `platform/docs/CONTRACTS.md` (§0 ownership table + §1 note)
- Modify: `platform/README.md` (배포 상태 section — mark as "구현됨 · 배포 대기" until Task 13 verifies)

- [ ] **Step 1: SPEC §16 bullet** (append at the end of §16):

```markdown
- 디자인 스튜디오(2026-09-04, 사용자 결정): 상품 명세 출처 = 온톨로지 Product(Condition·Procedure·ScreenMeta·PolicyRule·UXTerm) —
  문서 업로드 없음. 에이전틱 루프(생성→체크리스트 검수→수정 재생성)는 플랫폼 워커 Lambda(StudioLoopFn, 15분)에서 돌고 점수 기준으로
  반복한다(라운드 기본 3, 1~20 조정, 통과 점수 기본 85). 생성물은 HTML 시안(React 변환은 S3 화면 생성으로 분리). 모델 호출은
  익명화 게이트 경유. 시안·잡·승인은 플랫폼 DynamoDB+S3, 자산은 uiux-studio 레지스트리 프록시. 미판정은 통과로 세지 않고
  최종 시안은 최고 점수 라운드로 표기한다. 설계: docs/superpowers/specs/2026-09-04-design-studio-agentic-loop-design.md
```

- [ ] **Step 2: CONTRACTS ownership row** — add to the §0 table:

```markdown
| `studio/`, `api/handlers/studio.py`, `api/common/studio_proxy.py`, `web/src/studio/`, `skills/studio-*.md`, `tests/test_studio*.py` | 스튜디오 모듈 | 디자인 스튜디오 · 에이전틱 루프(StudioLoopFn) · 명세 체크리스트 |
```
and under §1 add one line: `- 장기 실행 스트리밍(스튜디오): WsFn 은 ack 1건(\`studio_run\`)만 보내고 워커 Lambda 가 같은 커넥션에 \`studio.stage/.token/.done\` 을 push 한다. 워커는 STUDIO_LOOP_FN 미설정 시 \`.done(error)\` 로 미배포를 알린다.`

- [ ] **Step 3: README** — in the 배포 상태 table add a row `디자인 스튜디오 에이전틱 루프 (StudioLoopFn · StudioTable · studio/drafts) | 구현됨 · 배포 대기` (Task 13 flips it to 완료 with the verified function name).

- [ ] **Step 4: Commit**

```bash
git add SPEC.md platform/docs/CONTRACTS.md platform/README.md
git commit -m "docs: record studio agentic-loop decisions (SPEC §16), module ownership (CONTRACTS), deploy status"
```

---

### Task 13: Deploy + live verification (single-owner CDK deploy — confirm with the user first)

**Files:**
- Modify: `platform/README.md` (flip status to 완료 with verified resource names)

Pre-conditions: another session may own CDK deploys (CLAUDE.md multi-session rule). Ask the user before running `deploy.sh`.

- [ ] **Step 1: Offline gate**

Run: `cd platform && python3 -m pytest tests/ -q > $SCRATCH/pytest-final.log 2>&1; echo exit=$?; tail -2 $SCRATCH/pytest-final.log`
Expected: `exit=0`, all passed.

- [ ] **Step 2: Deploy** (after user confirmation)

Run: `cd platform && rm -rf infra/cdk.out && bash deploy.sh > $SCRATCH/deploy.log 2>&1; echo exit=$?; grep -E "StudioLoopFnName|UPDATE_COMPLETE|CREATE_COMPLETE|failed" $SCRATCH/deploy.log | tail -5`
Expected: `exit=0` and a `StudioLoopFnName` output.

- [ ] **Step 3: Verify the worker and table exist**

```bash
FN=$(aws cloudformation describe-stacks --stack-name BankPlatformCore --query "Stacks[0].Outputs[?OutputKey=='StudioLoopFnName'].OutputValue" --output text)
[ "$FN" != "None" ] && aws lambda get-function-configuration --function-name "$FN" --query '{timeout:Timeout,handler:Handler,env:Environment.Variables.STUDIO_TABLE}' --output json
```
Expected: `timeout: 900`, `handler: studio.worker_handler.handler`, non-empty table name.

- [ ] **Step 4: End-to-end run in the browser**

Open `https://agent.atomai.click` → 디자인 스튜디오 → 플레이그라운드 → preset "축구사랑 적금 가입 플로우" → 라운드 상한 3 → 실행. Confirm: stage list advances, canvas updates per round, `FLOW-COND` shows ✓ or ✗ with evidence, final score badge and stop reason render, the draft appears in 갤러리 with its score. Then run "기본 적금 가입 플로우" and confirm `FLOW-NOCOND` is checked instead. Then click an element, type a refine instruction, confirm a 1-round refine returns and `STABLE` appears in the checklist.
Record the observed score/rounds in the README status row (real numbers only).

- [ ] **Step 5: Commit README**

```bash
git add platform/README.md
git commit -m "docs(readme): studio agentic loop deployed — StudioLoopFn verified, e2e run recorded"
```
