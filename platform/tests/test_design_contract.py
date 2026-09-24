# platform/tests/test_design_contract.py
"""Task E13: browser verification contract derived from the PRD and the flow (V-02 tester input)."""
import base64
import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT, knowledge, published_knowledge, sealed_graph
from design_loop.contract import coverage_texts, derive, excerpt, missing_targets
from design_loop.convention import Registry
from design_loop.flow import build_flow, enumerate_cases, expected, flow_issues, traverse
from design_loop.gui import fill, generate_screen, generate_states, states_for
from design_loop.prd_extract import bindings
from design_loop.route import strategy
from test_design_prd_extract import GOOD
from test_workbench_core import wb  # noqa: F401  (fixture)
from workspace import ontology_schema as schema
from workspace.criteria import notice_coverage_issues
from workspace.rules import contract_hash, validate_contract

K = knowledge()
FLOW = build_flow(K, "savings-signup")
CASES = enumerate_cases(expected(GOOD, K)["conditions"])["cases"]
VALUES = bindings(GOOD)
needs = pytest.mark.skipif(not (ROOT / "react-kit" / "node_modules").exists() or not shutil.which("node"), reason="react-kit")
CHROMIUM = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"


def seed(k=K, published=(), criteria=None, cases=CASES, prd=GOOD):
    registry = Registry(k)
    flow = build_flow(k, "savings-signup") if k is K or "savings-signup" in k.procedures else None
    return derive(prd, flow, k, registry, list(published), cases=cases, criteria=criteria), registry, flow


def steps_of(contract):
    return [[(s["action"], s["target"], s.get("value")) for s in rule["steps"]] for rule in contract["rules"]]


def occurrences(contract, sequence):
    count = 0
    for steps in steps_of(contract):
        for i in range(len(steps) - len(sequence) + 1):
            count += steps[i:i + len(sequence)] == sequence
    return count


def test_seed_contract_is_one_valid_contract_with_16_rules():
    out, registry, flow = seed()
    contract = out["contract"]
    assert out["findings"] == [] and contract is not None
    assert validate_contract(contract, asset_texts=None) == contract          # already normalized: stable hash
    assert len(contract["rules"]) == 16
    assert all(len(rule["steps"]) <= 20 for rule in contract["rules"])
    # every (conditional transition, case taking it) check appears exactly once as a step sequence (AL1)
    branch = []
    for i, case in enumerate(CASES):
        run = traverse(flow, case)
        for t in flow["transitions"]:
            if t["when"] and t["id"] in run["transitions"]:
                branch.append([("select", "case-select", f"{i}:{t['src']}:default"),
                               ("click", registry.cta_id(t["src"]), None),
                               ("expectVisible", registry.page_id(t["dst"]), True)])
    assert len(branch) == 6
    assert all(occurrences(contract, sequence) == 1 for sequence in branch)
    assert len([r for r in contract["rules"] if r["id"].startswith("branch-")]) == 2
    # both visibility outcomes of branch-select on the same screen and state (AJ1)
    node = registry.test_ids("evidence-auto")["nodes"]["branch-select#1"]
    shown = next(i for i, c in enumerate(CASES) if c["autoTransfer"])
    hidden = next(i for i, c in enumerate(CASES) if not c["autoTransfer"])
    assert occurrences(contract, [("select", "case-select", f"{shown}:evidence-auto:default"),
                                  ("expectVisible", node, True),
                                  ("select", "case-select", f"{hidden}:evidence-auto:default"),
                                  ("expectVisible", node, False)]) == 1
    assert len([r for r in contract["rules"] if r["id"].startswith("visible-")]) == 1
    # rule budget as derived for the seed (see report: 9 default + 2 state pages + 2 branch + 1 visibility + 2 input)
    kinds = [r["id"].split("-")[0] for r in contract["rules"]]
    assert kinds.count("page") == 11 and kinds.count("input") == 2
    assert all(r["source"]["kind"] == "inferred" and len(r["source"].get("quote", "")) <= 2000 for r in contract["rules"])


def test_expect_text_union_equals_the_coverage_requirement_set():
    out, _, flow = seed()
    values = {" ".join(s["value"].split()) for r in out["contract"]["rules"] for s in r["steps"] if s["action"] == "expectText"}
    assert values == coverage_texts(VALUES, K, flow) == {"연 2.0%", "12개월", "예금자보호법에 따라 보호됩니다"}


def test_the_ineligible_state_page_has_its_own_rule():
    out, registry, _ = seed()
    rule = next(r for r in out["contract"]["rules"] if r["id"] == f"page-{registry.page_key('eligibility')}-ineligible")
    select, visible = rule["steps"][:2]
    i = int(select["value"].split(":")[0])
    assert CASES[i]["eligible"] is False and select["value"].endswith(":eligibility:ineligible")
    assert visible == {"action": "expectVisible", "target": registry.state_page_id("eligibility", "ineligible"),
                       "targetLabel": registry.state_page_id("eligibility", "ineligible"), "value": True}


def test_both_eligibility_outcomes_use_the_generic_action():
    out, registry, _ = seed()
    rule = next(r for r in out["contract"]["rules"] if r["id"] == f"branch-{registry.page_key('eligibility')}-1")
    clicks = [s for s in rule["steps"] if s["action"] == "click"]
    assert {s["target"] for s in clicks} == {registry.cta_id("eligibility")}
    assert {s["target"] for s in rule["steps"] if s["action"] == "expectVisible"} == {"terms", "ineligible"}


def test_rule_and_page_capacity_is_a_finding_never_a_dropped_assertion():
    published = [{"pageId": p, "required": True, "content": f"{p} 필수 안내"}
                 for p in ("intro", "terms", "amount", "preferential", "confirm")]
    out, _, _ = seed(published=published)                               # 16 + 5 published pages = 21 rules
    assert out["contract"] is None and out["ruleCount"] == 21
    assert {f["code"] for f in out["findings"]} == {"contract-capacity"}
    k = copy.deepcopy(K)                                                 # a synthetic 30+ page design
    k.assets["title"]["requiredStates"] = ["default", "error", "empty", "loading"]
    out, _, _ = seed(k=k)
    assert out["contract"] is None and "contract-capacity" in {f["code"] for f in out["findings"]}


def test_a_composition_missing_a_contract_target_is_critical():
    out, registry, flow = seed()
    screens = {(s, "default"): fill(K, flow, s, VALUES) for s in flow["screens"]}
    for s in ("eligibility", "ineligible"):
        [state] = generate_states(screens[(s, "default")], ["ineligible"], K, {}, strategy=strategy("structured"),
                                  flow=flow, binding_paths=frozenset(VALUES))["states"]
        screens[(s, "ineligible")] = state["composition"]
    assert missing_targets(out["contract"], screens, K, registry) == []
    evidence = screens[("evidence-auto", "default")]
    evidence["slots"]["body"] = [n for n in evidence["slots"]["body"] if n["asset"] != "branch-select"]
    missing = missing_targets(out["contract"], screens, K, registry)
    assert missing and {f["code"] for f in missing} == {"missing-contract-target"} and all(f["severity"] == "critical" for f in missing)
    assert {f["target"] for f in missing} == {registry.test_ids("evidence-auto")["nodes"]["branch-select#1"]}


def test_contract_carries_top_level_criteria_and_the_design_snapshot_hash():
    criteria = {"projectId": "project-1", "productId": "product-1", "guidelineId": "guide-1",
                "guidelineAssetId": "asset-1", "catalogHash": "c" * 64, "ontologyHash": "d" * 64,
                "designSnapshotHash": schema.digest(sealed_graph())}
    contract = seed(criteria=criteria)[0]["contract"]
    assert {key: contract[key] for key in criteria} == criteria and contract["assetIds"] == ["asset-1"]
    assert validate_contract(contract)["designSnapshotHash"] == criteria["designSnapshotHash"]
    with pytest.raises(ValueError):
        validate_contract({**contract, "designSnapshotHash": "not-a-hash"})
    legacy = {"title": "기존", "rules": [{"id": "R1", "title": "보임", "steps": [
        {"action": "expectText", "target": "summary", "value": "x" * 2000}]}]}
    assert "designSnapshotHash" not in validate_contract(legacy)           # existing contracts hash as before
    assert contract_hash(legacy) == contract_hash(validate_contract(legacy))


def test_dsl_holds_a_complete_4000_character_notice_and_rejects_4001():
    spaced, unbroken = ("가 " * 2000)[:4000], "가" * 4000
    for value in (spaced, unbroken):
        rule = {"id": "N1", "title": "안내", "steps": [{"action": "expectText", "target": "notice-consent", "value": value}]}
        assert validate_contract({"title": "안내", "rules": [rule]})["rules"][0]["steps"][0]["value"] == value
        assert notice_coverage_issues({"rules": [rule]}, [{"pageId": "notice-consent", "required": True, "content": value}]) == []
    with pytest.raises(ValueError):
        validate_contract({"title": "안내", "rules": [{"id": "N1", "title": "안내", "steps": [
            {"action": "expectText", "target": "notice-consent", "value": "가" * 4001}]}]})
    with pytest.raises(ValueError):                                       # other text actions keep 2 000
        validate_contract({"title": "입력", "rules": [{"id": "N1", "title": "입력", "steps": [
            {"action": "fill", "target": "amount", "value": "1" * 2001}, {"action": "expectVisible", "target": "a", "value": True}]}]})


def long_notice_prd(length=4000):
    text = ("예금자보호 안내 " * 500)[:length].strip()
    prd = copy.deepcopy(GOOD)
    prd["notices"][0]["text"] = {"value": text, "cite": {**prd["notices"][0]["text"]["cite"], "quote": text}}
    return prd, text


def test_a_4000_character_cited_notice_keeps_a_bounded_quote_and_the_complete_value():
    prd, text = long_notice_prd()
    assert len(text) > 3990
    out, _, _ = seed(prd=prd)
    contract = out["contract"]
    rule = next(r for r in contract["rules"] if any(s.get("value") == text for s in r["steps"]))
    assert rule["source"]["quote"] == excerpt(text) and len(rule["source"]["quote"]) <= 2000
    assert rule["source"]["quote"].endswith("…") and " ".join(text.split()).startswith(rule["source"]["quote"][:-1])
    assert validate_contract(contract) == contract
    assert excerpt("가" * 4000).endswith("…") and len(excerpt("가" * 4000)) == 2000   # unbroken text is hard-cut


def test_a_4000_character_cited_notice_contract_is_approved_through_the_real_api():
    from test_workspace_http import FakeLambda
    from test_workspace_storage import FakeS3, FakeTable
    from test_workspace_worker import request
    from workspace.http import WorkspaceAPI
    from workspace.storage import Storage
    api = WorkspaceAPI(storage=Storage(table=FakeTable(), s3=FakeS3(), bucket="private"), lambda_client=FakeLambda(),
                       worker_fn="worker")
    contract = seed(prd=long_notice_prd()[0])[0]["contract"]
    status, data = request(api, "POST", "/contracts", contract)
    assert status == 201, data
    status, data = request(api, "POST", f"/contracts/{data['contract']['id']}/approve", {"version": data["contract"]["version"]})
    assert status == 200, data


def test_128_character_binding_fields_and_canonical_ids_stay_bounded(wb):  # noqa: F811
    k, ids = published_knowledge(wb)
    k = copy.deepcopy(k)
    amount_input = k.assets[ids["amount-input"]]
    amount_input["bindings"] = [{"field": "f" * 128, "source": "session", "path": "session.branch", "required": False}]
    flow = build_flow(k, ids["savings-signup"])
    out = derive(GOOD, flow, k, Registry(k), [], cases=CASES, criteria=None)
    contract = out["contract"]
    assert out["findings"] == [] and validate_contract(contract) == contract
    targets = {s["target"] for r in contract["rules"] for s in r["steps"]}
    assert all(len(t) <= 50 for t in targets) and not any(ids["amount"] in t or "f" * 20 in t for t in targets)


# ---- published mandatory pages through the real approval path (Y3, Z5) -------------------------------------------

def published_product(content=None, notice_id=None):
    from test_workspace_collaboration import DRAFT
    from test_workspace_project_http import make_api, request, shared
    draft = copy.deepcopy(DRAFT)
    if content is not None:
        draft["notices"][0]["content"] = content
    if notice_id is not None:
        draft["notices"][0]["id"] = notice_id
    api = make_api()
    project = shared(api)
    _, data = request(api, "POST", "/products", draft, actor="bob", project=project["id"])
    _, publication = request(api, "POST", f"/products/{data['product']['id']}/publish",
                             {"version": data["product"]["version"]}, actor="bob", project=project["id"])
    product = publication["product"]
    scope = api.collaboration.resolve_scope("carol", project["id"])
    context = api.collaboration.published_context(scope, product["id"])
    from workspace.component_catalog import read_catalog
    criteria = {"projectId": project["id"], "productId": product["id"], "guidelineId": context["guideline"]["id"],
                "guidelineAssetId": context["assetId"], "catalogHash": read_catalog()["hash"],
                "ontologyHash": context["ontology"]["hash"], "designSnapshotHash": schema.digest(sealed_graph())}
    from workspace.criteria import resolve_generation_context
    pages = resolve_generation_context(api.storage, "project:" + project["id"],
                                       {**criteria, "actor": "carol"}, "edit_rules")["pages"]
    return api, request, project, scope, criteria, pages


def with_notice_screen(page_id="notice-consent"):
    k = copy.deepcopy(K)
    k.screens["confirm"]["pageId"] = page_id
    return k


@pytest.mark.parametrize("content", [None, ("안내 " * 1334)[:4000], "안" * 4000])
def test_published_notice_page_contract_passes_the_real_approval(content):
    api, request, project, scope, criteria, pages = published_product(content)
    assert [p["pageId"] for p in pages] == ["notice-consent"] and pages[0]["required"]
    out, _, flow = seed(k=with_notice_screen(), published=pages, criteria=criteria)
    contract = out["contract"]
    assert out["findings"] == [] and len(contract["rules"]) == 17
    notice = [r for r in contract["rules"] if r["id"].startswith("notice-")]
    assert len(notice) == 1 and len([s for s in notice[0]["steps"] if s["action"] == "expectText"]) == 1
    assert notice_coverage_issues(contract, pages) == []
    assert api.collaboration.is_current(scope, contract)                 # ontologyHash keeps its meaning
    body = {key: contract[key] for key in contract if key not in ("unresolved",)} | {"productId": criteria["productId"]}
    status, data = request(api, "POST", "/contracts", body, actor="carol", project=project["id"])
    assert status == 201, data
    assert data["contract"]["designSnapshotHash"] == criteria["designSnapshotHash"]
    status, data = request(api, "POST", f"/contracts/{data['contract']['id']}/approve",
                           {"version": data["contract"]["version"]}, actor="carol", project=project["id"])
    assert status == 200, data


@needs
@pytest.mark.parametrize("length", [40, 41, 64])
def test_published_page_ids_of_40_41_and_64_characters_approve_compile_and_verify(length, local_browser):
    from workspace.browser import evaluate_bundle
    api, request, project, scope, criteria, pages = published_product(notice_id="a" * (length - len("notice-")))
    [page] = pages
    assert len(page["pageId"]) == length
    k = with_notice_screen(page["pageId"])
    out, registry, flow = seed(k=k, published=pages, criteria=criteria)
    contract = out["contract"]
    assert registry.page_id("confirm") == page["pageId"] and len(registry.state_page_id("confirm", "error")) < 12
    body = {key: contract[key] for key in contract if key != "unresolved"} | {"productId": criteria["productId"]}
    status, data = request(api, "POST", "/contracts", body, actor="carol", project=project["id"])
    assert status == 201, data
    status, data = request(api, "POST", f"/contracts/{data['contract']['id']}/approve",
                           {"version": data["contract"]["version"]}, actor="carol", project=project["id"])
    assert status == 200, data                                            # notice coverage gate passed
    # Generated pages do not render published notice content yet (report concern); verify every other rule.
    rest = {**contract, "rules": [r for r in contract["rules"] if not r["id"].startswith("notice-")]}
    bundle, files = compiled(k, rest, registry, flow)
    assert f"src/pages/{page['pageId']}.tsx" in files
    report = evaluate_bundle(bundle, rest)
    assert report["passed"], ([c for c in report["checks"] if c["status"] != "pass"], report["blockingFindings"])


@needs
@pytest.mark.parametrize("content", [("안내 " * 1334)[:4000], "안" * 4000])
def test_a_4000_character_notice_step_passes_the_browser(content, local_browser):
    from design_loop.react_project import compile_local, compile_request, kit_catalog_hash
    from workspace.browser import evaluate_bundle
    app = ("import {Screen,Stack,Text} from '@studio/approved-ui';\n"
           "export default function App(){\n return <Screen pageId=\"notice-consent\" testId=\"notice-consent\" "
           f"title=\"필수 안내\"><Stack><Text>{{{json.dumps(content, ensure_ascii=False)}}}</Text></Stack></Screen>;\n}}")
    rules = {"title": "필수 안내", "rules": [{"id": "N1", "title": "전체 안내문", "steps": [
        {"action": "expectText", "target": "notice-consent", "value": " ".join(content.split()), "normalizeWhitespace": True}]}]}
    assert len(validate_contract(rules)["rules"][0]["steps"][0]["value"]) >= 3999
    out = compile_local(compile_request({"src/App.tsx": app}, catalog_hash=kit_catalog_hash(), contract=rules))
    assert out["ok"], out.get("diagnostics")
    report = evaluate_bundle({n: base64.b64decode(v) for n, v in out["files"].items()}, rules)
    assert report["passed"], (report["checks"], report["blockingFindings"])
    assert report["checks"][0]["steps"][0]["actualComponent"] == "Screen"


def test_a_flow_without_the_published_screen_is_published_page_missing():
    _, _, _, _, criteria, pages = published_product()
    out, _, flow = seed(published=pages, criteria=criteria)
    assert out["contract"] is None and {f["code"] for f in out["findings"]} == {"published-page-missing"}
    assert {"code": "published-page-missing", "detail": {"pageId": "notice-consent"}} in \
        flow_issues(flow, expected(GOOD, K, pages), K)


# ---- node-enabled: the real verifier on the compiled project ----------------------------------------------------

@pytest.fixture
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and os.path.isfile(CHROMIUM):
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", CHROMIUM)


def seed_screens(k, flow, variant="a"):
    screens = {}
    for s in flow["screens"]:
        if variant == "a":
            screens[(s, "default")] = fill(k, flow, s, VALUES)
        else:
            out = generate_screen(s, k, flow, {}, strategy=strategy("structured"), binding_paths=frozenset(VALUES))
            screens[(s, "default")] = next(v for v in out["variants"] if v["variant"] == variant)["composition"]
    for s in flow["screens"]:
        for st in states_for(s, k, flow, expected(GOOD, k)):
            if st != "default":
                [state] = generate_states(screens[(s, "default")], [st], k, {}, strategy=strategy("structured"),
                                          flow=flow, binding_paths=frozenset(VALUES))["states"]
                screens[(s, st)] = state["composition"]
    return screens


def compiled(k, contract, registry, flow, screens=None, mutate=None):
    from design_loop.react_project import compile_local, compile_request, kit_catalog_hash, project
    screens = screens or seed_screens(k, flow)
    files = project(flow, screens, k, VALUES, cases=CASES, registry=registry)
    if mutate:
        files = mutate(files)
    out = compile_local(compile_request(files, catalog_hash=kit_catalog_hash(), contract=contract))
    assert out["ok"], out.get("diagnostics")
    return {name: base64.b64decode(value) for name, value in out["files"].items()}, files


def only(contract, *prefixes):
    return {**contract, "rules": [r for r in contract["rules"] if r["id"].startswith(prefixes)]}


@needs
@pytest.mark.parametrize("confirm_page_id", [None, "notice-" + "c" * 57])
def test_complete_generated_contract_passes_the_real_verifier(confirm_page_id, local_browser):
    from workspace.browser import evaluate_bundle
    k = copy.deepcopy(K)
    if confirm_page_id:
        k.screens["confirm"]["pageId"] = confirm_page_id
    out, registry, flow = seed(k=k)
    contract = out["contract"]
    assert registry.page_id("intro") != registry.page_key("intro")
    if confirm_page_id:
        assert registry.page_id("confirm") == confirm_page_id and len(confirm_page_id) == 64
    screens = seed_screens(k, flow)
    assert missing_targets(contract, screens, k, registry) == []
    bundle, _ = compiled(k, contract, registry, flow, screens)
    report = evaluate_bundle(bundle, contract)
    assert report["functionalStatus"] == "pass", [c for c in report["checks"] if c["status"] != "pass"]
    assert report["passed"], report["blockingFindings"]


@needs
def test_interaction_rules_without_prefill_catch_a_noop_setfield_and_a_removed_guard(local_browser):
    from workspace.browser import evaluate_bundle
    out, registry, flow = seed()
    contract = only(out["contract"], "input-")
    amount_field = registry.field_id("amount", "session.amount")
    terms = f"src/pages/{registry.page_id('terms')}.tsx"
    good, _ = compiled(K, contract, registry, flow)
    assert evaluate_bundle(good, contract)["functionalStatus"] == "pass"

    def broken(files):
        files = dict(files)
        page = f"src/pages/{registry.page_id('amount')}.tsx"
        files[page] = files[page].replace(f'setField("{amount_field}", v)', 'setField("unused", v)')
        files[terms] = files[terms].replace(" disabled={!ready}", "")
        return files

    bad, _ = compiled(K, contract, registry, flow, mutate=broken)
    report = evaluate_bundle(bad, contract)
    failed = {c["caseId"]: next(s for s in c["steps"] if s["status"] != "pass") for c in report["checks"] if c["status"] == "fail"}
    assert failed[f"input-{registry.page_key('amount')}"]["action"] == "expectValue"
    assert failed[f"input-{registry.page_key('terms')}"] == {**failed[f"input-{registry.page_key('terms')}"],
                                                              "action": "expectEnabled", "expected": False}


def choice_knowledge():
    k = copy.deepcopy(K)
    common = {"level": "Atom", "intent": "선택", "requiredStates": [], "stateProps": {}, "conditions": [], "bindings": [],
              "composes": [], "reviewState": "approved"}
    props = {"label": {"type": "string", "required": True}, "value": {"type": "string", "required": True},
             "onChange": {"type": "callback", "required": True},
             "options": {"type": "list", "required": True, "item": {"value": "string", "label": "string"}}}
    for asset_id, title, export in (("plan-select", "납입 주기", "Select"), ("channel-radio", "안내 방법", "RadioGroup")):
        k.assets[asset_id] = {**copy.deepcopy(common), "id": asset_id, "title": title,
                              "code": {"importPath": "@studio/approved-ui", "exportName": export, "props": copy.deepcopy(props),
                                       "childrenProp": None, "adapter": "controlled-choice"}}
    k.templates["single-task"]["slots"]["body"]["allowed"] += ["plan-select", "channel-radio"]
    k.screens["amount"]["assets"] = ["title", "amount-field", "plan-select", "channel-radio", "cta-next"]
    return k


def choice_screens(k, flow):
    screens = seed_screens(k, flow)
    options = {"plan-select": [{"value": "monthly", "label": "매월"}, {"value": "yearly", "label": "매년"}],
               "channel-radio": [{"value": "email", "label": "이메일"}, {"value": "sms", "label": "문자"}]}
    for node in screens[("amount", "default")]["slots"]["body"]:
        if node["asset"] in options:
            node["props"]["options"] = options[node["asset"]]
    return screens


@needs
def test_select_and_radio_contract_passes_and_noop_mutations_fail(local_browser):
    from design_loop.composition import validate
    from workspace.browser import evaluate_bundle
    k = choice_knowledge()
    out, registry, flow = seed(k=k)
    contract = out["contract"]
    screens = choice_screens(k, flow)
    assert validate(screens[("amount", "default")], k, flow=flow, binding_paths=frozenset(VALUES)) == []
    rule = next(r for r in contract["rules"] if r["id"] == f"input-{registry.page_key('amount')}")
    select_id = registry.field_id("amount", "asset:plan-select")
    radio_id = registry.field_id("amount", "asset:channel-radio")
    assert ("select", select_id, "o2") in [(s["action"], s["target"], s.get("value")) for s in rule["steps"]]
    assert ("expectChecked", f"{radio_id}-o1", False) in [(s["action"], s["target"], s.get("value")) for s in rule["steps"]]
    assert missing_targets(contract, screens, k, registry) == []
    bundle, files = compiled(k, contract, registry, flow, screens)
    report = evaluate_bundle(bundle, contract)
    assert report["passed"], ([c for c in report["checks"] if c["status"] != "pass"], report["blockingFindings"])
    assert '"o1": "monthly"' in files["src/logic/data.ts"] or '"o1":"monthly"' in files["src/logic/data.ts"]
    page = f"src/pages/{registry.page_id('amount')}.tsx"
    # Playwright's own post-check (a radio click that never checks) can fire before expectChecked; either fails.
    for target, action in ((select_id, {"expectValue"}), (radio_id, {"check", "expectChecked"})):
        def noop(files, target=target):
            return {**files, page: files[page].replace(f'setField("{target}", v)', 'setField("unused", v)')}
        bad, _ = compiled(k, only(contract, "input-"), registry, flow, screens, mutate=noop)
        report = evaluate_bundle(bad, only(contract, "input-"))
        [failed] = [c for c in report["checks"] if c["status"] == "fail"]
        assert next(s for s in failed["steps"] if s["status"] != "pass")["action"] in action


@needs
def test_frozen_contract_binds_two_variants_and_the_real_run_approval(local_browser):
    from test_workspace_http import FakeLambda
    from test_workspace_storage import FakeS3, FakeTable
    from test_workspace_worker import request
    from design_loop.react_project import project
    from workspace.http import WorkspaceAPI
    from workspace.react_runtime import evaluate_react
    from workspace.storage import Storage
    from workspace.worker import Worker
    out, registry, flow = seed()
    derived = out["contract"]
    variants = {v: project(flow, seed_screens(K, flow, v), K, VALUES, cases=CASES, registry=registry) for v in ("a", "b")}
    assert variants["a"] != variants["b"]
    queue = []

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": queue.pop(0)}, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}, {"modelId": model_id}

    store = Storage(table=FakeTable(), s3=FakeS3(), bucket="private")
    api = WorkspaceAPI(storage=store, lambda_client=FakeLambda(), worker_fn="worker")
    worker = Worker(storage=store, model_call=model, react_call=evaluate_react)
    status, data = request(api, "POST", "/contracts", derived)
    assert status == 201, data
    status, data = request(api, "POST", f"/contracts/{data['contract']['id']}/approve", {"version": data["contract"]["version"]})
    assert status == 200, data
    contract = data["contract"]
    frozen = contract_hash(contract)
    assert contract["approval"]["hash"] == frozen
    for variant in ("a", "b"):
        queue.append(variants[variant])
        status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                               "outputType": "react", "model": "global.openai.gpt-6-astra", "variant": "baseline",
                               "generationMode": "guided", "maxRounds": 1, "requestId": f"frozen-{variant}"})
        assert status == 202, data
        assert worker.handle({"owner": "designer", "jobId": data["job"]["id"]})["status"] == "completed"
        run = store.get("designer", "run", data["run"]["id"])
        [row] = run["rounds"]
        report = json.loads(store.get_blob(row["reportKey"]))
        assert row["passed"], report.get("blockingFindings")
        assert report["contractHash"] == run["contractHash"] == frozen
        body = {"round": 1, "artifactSha256": row["artifactSha256"], "contractVersion": run["contractVersion"],
                "sourceHash": row["sourceHash"], "bundleHash": row["bundleHash"]}
        status, accepted = request(api, "POST", f"/runs/{run['id']}/approve", body)
        assert status == 200, accepted


def test_contract_module_imports_only_stdlib_and_pure_schema():
    import ast
    root = Path(__file__).resolve().parents[1] / "design_loop"
    for item in ast.walk(ast.parse((root / "contract.py").read_text(encoding="utf-8"))):
        if isinstance(item, ast.ImportFrom) and item.level == 0:
            assert item.module in {"workspace.ontology_schema", "workspace.ontology_ux"} \
                or item.module.split(".")[0] in sys.stdlib_module_names, item.module
        elif isinstance(item, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names)
