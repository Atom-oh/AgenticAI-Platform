# platform/tests/test_design_verify_graph.py
"""Task E16: fail-closed verification graph, escalation, judge budget and V-05 candidates."""
import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ is a package; expose design_fixtures
from design_fixtures import ROOT, knowledge
from design_loop.flow import build_flow, enumerate_cases, expected
from design_loop.gui import fill, generate_screen
from design_loop.prd_extract import bindings
from design_loop.route import strategy
from design_loop.verify_graph import (Metrics, batch_judge, compose_review, design_checklist, failure_checklist,
                                      page_review_key, rejection_candidates, review_context, run, stored_judgment,
                                      verify)
from test_design_prd_extract import GOOD

K = knowledge()
FLOW = build_flow(K, "savings-signup")
EXP = expected(GOOD, K)
# CONTRACT_FULL and PASS_REPORT are generated once from the E19 chain (a real compile + evaluate_bundle of the seed) and
# stored as tests/fixtures/design_pass_report.json; the fixture regeneration script is tests/fixtures/make_design_pass.py.
import json as _json, pathlib as _pl  # noqa: E401,E402
_FIX = _json.loads((_pl.Path(__file__).parent / "fixtures" / "design_pass_report.json").read_text(encoding="utf-8"))
CONTRACT_FULL, PASS_REPORT = _FIX["contract"], _FIX["report"]
OK = lambda text: text  # noqa: E731  verify-only normalization fake (Global Constraints; as in E6/E7)
JUDGE = {"llm_judge": lambda item, ctx: {"verdict": "pass", "evidence": "ok"}, "normalize": OK}
CHROMIUM = "/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell"
needs = pytest.mark.skipif(not (ROOT / "react-kit" / "node_modules").exists() or not shutil.which("node"), reason="react-kit")


def bundle(**over):
    screens = {(s, "default"): fill(K, FLOW, s, bindings(GOOD)) for s in FLOW["screens"]}
    screens[("eligibility", "ineligible")] = {**screens[("eligibility", "default")], "state": "ineligible"}
    base = {"prd": GOOD, "flow": FLOW, "expectation": EXP, "screens": screens, "binding_values": bindings(GOOD),
            "contract": CONTRACT_FULL, "browser_report": copy.deepcopy(PASS_REPORT),
            "build": {"bundleHash": PASS_REPORT["bundleHash"]},
            "checklist": [{"id": "c1", "text": "톤", "severity": "major"}]}
    base.update(over)
    return base


def test_pass_fixture_satisfies_the_real_checker():
    # The canned report must be accepted by the production predicate, not only by verify_graph (review round 3, #11).
    from workspace.react_quality import react_report_passes
    assert react_report_passes(CONTRACT_FULL, PASS_REPORT)


def test_clean_bundle_passes_and_is_approvable():
    r = verify(bundle(), K, JUDGE)
    assert r["verdict"] == "pass" and r["approvable"] and not r["unavailable"]


def test_missing_tester_is_blocked_not_pass():
    r = verify(bundle(browser_report=None), K, JUDGE)
    assert r["verdict"] == "blocked" and r["unavailable"] == ["tester"] and not r["approvable"]


def test_missing_judge_is_blocked():
    r = verify(bundle(), K, {})
    assert r["verdict"] == "blocked" and "reviewer" in r["unavailable"]


def test_missing_normalization_hook_is_blocked():
    r = verify(bundle(), K, {"llm_judge": JUDGE["llm_judge"]})
    assert r["verdict"] == "blocked" and r["unavailable"] == ["reviewer"]


def test_browser_required_check_failure_is_critical_and_not_approvable():
    report = copy.deepcopy(PASS_REPORT)
    report["checks"][0]["status"] = "fail"; report["passed"] = False; report["functionalStatus"] = "fail"
    r = verify(bundle(browser_report=report), K, JUDGE)
    assert r["verdict"] == "fail" and any(f["severity"] == "critical" and f["role"] == "tester" for f in r["findings"])


def test_accessibility_violations_are_major_and_the_production_predicate_still_fails():
    report = copy.deepcopy(PASS_REPORT)
    report["accessibility"] = {"status": "fail", "violations": [{"id": "label"}]}
    r = verify(bundle(browser_report=report), K, JUDGE)
    tester = [f for f in r["findings"] if f["role"] == "tester"]
    assert {(f["code"], f["severity"]) for f in tester} == {("accessibility-violation", "major"),
                                                           ("react-report-failed", "critical")}
    assert r["verdict"] == "fail" and not r["approvable"]


def test_catalog_or_contract_hash_mismatch_is_critical():
    report = copy.deepcopy(PASS_REPORT); report["catalogHash"] = "0" * 64
    assert "react-evidence-mismatch" in {f["code"] for f in verify(bundle(browser_report=report), K, JUDGE)["findings"]}
    contract = copy.deepcopy(CONTRACT_FULL); contract["title"] = "다른 계약"
    r = verify(bundle(contract=contract), K, JUDGE)
    assert r["verdict"] == "fail" and "contract-hash-mismatch" in {f["code"] for f in r["findings"]}


def test_cap_reached_escalates_without_approvability():
    bad = bundle(); bad["screens"].pop(("terms", "default"))
    out = run(bad, K, JUDGE, regenerate=lambda b, findings: b, rebuild=lambda b: b, max_rounds=2)
    assert out["escalated"] and not out["approvable"] and out["rounds"] == 2 and len(out["history"]) == 2


def test_repair_rebuilds_and_retests():
    bad = bundle(); bad["screens"].pop(("terms", "default"))
    rebuilt = []
    def rebuild(b):
        rebuilt.append(1)
        report = copy.deepcopy(PASS_REPORT); report["checks"][0]["status"] = "fail"   # Browser-only defect
        report["passed"] = False; report["functionalStatus"] = "fail"
        return {**b, "browser_report": report, "build": {**b.get("build", {}), "bundleHash": report["bundleHash"]}}
    out = run(bad, K, JUDGE, regenerate=lambda b, f: bundle(), rebuild=rebuild, max_rounds=2)
    assert rebuilt and out["verdict"] == "fail"      # the new Browser failure is seen, not the old pass


def test_stale_browser_evidence_is_blocked():
    b = bundle(); b["build"] = {"bundleHash": "e" * 64}
    r = verify(b, K, JUDGE)
    assert r["verdict"] == "blocked" and "stale-evidence" in {f["code"] for f in r["findings"]}


def test_blocked_does_not_regenerate():
    calls = []
    out = run(bundle(browser_report=None), K, JUDGE, regenerate=lambda b, f: calls.append(1) or b, rebuild=lambda b: b, max_rounds=3)
    assert out["verdict"] == "blocked" and out["rounds"] == 1 and calls == []


def test_new_asset_candidate_is_never_approvable():
    b = bundle()
    comp = copy.deepcopy(b["screens"][("amount", "default")])
    comp["slots"]["body"].append({"id": "nx", "asset": "rate-simulator", "props": {}, "children": []})
    b["screens"][("amount", "default")] = comp
    r = verify(b, K, JUDGE, mode="compose")
    assert any(f["code"] == "new-asset-candidate" for f in r["findings"]) and not r["approvable"]


def test_an_unbound_financial_literal_fails_deterministically_with_a_passing_judge():
    # E10 deferred part: the literal is caught by composition.validate inside the reviewer, not by the judge.
    b = bundle()
    b["screens"][("amount", "default")]["slots"]["body"].append(
        {"id": "lit", "asset": "body-text", "props": {"children": "최대 999만원까지 납입"}})
    r = verify(b, K, JUDGE)
    assert r["verdict"] == "fail" and not r["approvable"]
    assert any(f["code"] == "literal-financial-value" and f["role"] == "reviewer" and f["severity"] == "critical"
               for f in r["findings"])


def test_rejection_reasons_become_policy_candidates():
    m = Metrics(); m.record("reject", screenId="amount", reason="금리 강조가 약함")
    ref = {"sourceKind": "run-round", "sourceId": "run-1", "revision": "2", "sha256": "d" * 64,
           "audienceRevision": "current-project-members-v1"}
    g = rejection_candidates(m, project_id="p1", source_ref=ref)
    node = g["nodes"][0]
    assert node["type"] == "PolicyRule" and node["reviewState"] == "candidate" and node["provenance"] == "declared"
    assert node["properties"]["statement"] == "금리 강조가 약함" and node["properties"]["required"] is False
    assert node["properties"]["severity"] == "major" and node["sourceRefs"] == [ref] and g["queueLabel"] == "업무 근거 연결 필요"
    m.record("reject", screenId="terms", reason="금리 강조가 약함")
    g = rejection_candidates(m, project_id="p1", source_ref=ref, revisions={"amount": 3, "terms": 1})
    assert len(g["nodes"]) == 1 and {e["src"]["id"] for e in g["edges"]} == {"amount", "terms"}
    with pytest.raises(ValueError):
        rejection_candidates(m, project_id="p1", source_ref={**ref, "sourceKind": "document-revision"})


def test_metrics_summary_math():
    m = Metrics()
    for screen in ("intro", "terms", "amount"):
        m.record("approve", screenId=screen)
    m.record("edit", screenId="amount", scope="n3"); m.record("edit", screenId="amount", scope="n4")
    m.record("reject", screenId="terms", reason="톤이 딱딱함"); m.record("reject", screenId="amount", reason="톤이 딱딱함")
    m.record("correction", screenId="terms", scope="copy", reason="존댓말")
    assert m.summary() == {"approvedWithoutEdit": 2, "editsPerScreen": {"amount": 2},
                           "rejectReasons": {"톤이 딱딱함": 2},
                           "humanCorrections": [{"screenId": "terms", "scope": "copy", "reason": "존댓말"}]}
    with pytest.raises(ValueError):
        m.record("reject", screenId="x")
    with pytest.raises(ValueError):
        m.record("like", screenId="x")


def test_failure_checklist_groups_rounds():
    history = [{"round": 1, "findings": [{"role": "coverage", "code": "missing-screen", "severity": "critical"},
                                         {"role": "tester", "code": "browser-check-failed", "severity": "critical"}]},
               {"round": 2, "findings": [{"role": "tester", "code": "browser-check-failed", "severity": "critical"}]}]
    items = {i["id"]: i for i in failure_checklist(history)}
    assert items["f-coverage-missing-screen"]["resolved"] and items["f-coverage-missing-screen"]["rounds"] == [1]
    assert not items["f-tester-browser-check-failed"]["resolved"] and items["f-tester-browser-check-failed"]["rounds"] == [1, 2]


# ---- checklist adapter, rules_v2 and judge context ----------------------------------------------------------------

def test_design_checklist_is_non_empty_and_sourced():
    items = design_checklist(GOOD, K)
    ids = {i["id"] for i in items}
    assert {"b01", "b04", "b12", "d-pref-1", "d-notice-1", "d-rule-r-terms-required", "d-rule-r-ineligible-reason"} <= ids
    assert not {i["id"] for i in items if i["id"].startswith("sp")}           # the sports set does not apply
    assert all(i.get("source") for i in items)
    assert next(i for i in items if i["id"] == "b01")["source"] == {"kind": "base", "set": "cl-deposit-base"}


def test_packaged_checklist_matches_its_manifest_and_the_seed_copy():
    import hashlib
    from design_loop.verify_graph import RESOURCES, checklist_resource_hash
    data = (RESOURCES / "checklists.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == checklist_resource_hash()
    assert data == (ROOT / "seed" / "design" / "checklists.json").read_bytes()


def test_an_empty_checklist_makes_the_reviewer_unavailable():
    r = verify(bundle(checklist=[]), K, JUDGE)
    assert r["verdict"] == "blocked" and r["unavailable"] == ["reviewer"]


def test_full_design_checklist_on_the_seed_passes_with_majors():
    b = bundle(); del b["checklist"]
    r = verify(b, K, JUDGE)
    assert r["verdict"] == "pass" and r["approvable"] and not r["unavailable"]
    failed = {f["item"] for f in r["findings"] if f["code"] == "checklist-fail"}
    assert failed == {"b05", "b06", "b08", "d-pref-1"}                       # honest gaps of the synthetic seed
    assert all(f["severity"] != "critical" for f in r["findings"] if f["code"] == "checklist-fail")


def rule_ctx(b):
    from design_loop.convention import Registry
    return {"flow": b["flow"], "screens": b["screens"], "k": K, "binding_values": b["binding_values"],
            "registry": Registry(K).register_flow(b["flow"]), "report": b["browser_report"]}


def test_rules_v2_predicates():
    from design_loop.rules_v2 import run_rule
    b = bundle()
    ctx = rule_ctx(b)
    for item in design_checklist(GOOD, K):
        if item["method"] == "rule" and item["id"] not in ("b05", "b06", "b08", "d-pref-1"):
            assert run_rule(item["rule"], ctx)["verdict"] == "pass", item["id"]
    notice = {"fn": "text_present", "args": {"texts": ["$spec.notice.notice-1"], "steps": ["terms", "confirm"], "any": True}}
    assert run_rule(notice, ctx)["verdict"] == "pass"
    missing = copy.deepcopy(b)
    missing["screens"][("confirm", "default")]["slots"]["body"] = [
        n for n in missing["screens"][("confirm", "default")]["slots"]["body"] if n["asset"] != "notice-alert"]
    assert run_rule(notice, rule_ctx(missing))["verdict"] == "fail"           # missing notice
    step = copy.deepcopy(b); step["screens"].pop(("amount", "default"))
    assert run_rule({"fn": "steps_exist", "args": {}}, rule_ctx(step))["verdict"] == "fail"   # missing step
    assert run_rule({"fn": "text_present", "args": {"texts": ["x"], "steps": ["nowhere"]}}, ctx)["verdict"] == "incomplete"
    assert run_rule({"fn": "text_present", "args": {"texts": "$spec.partnerNames"}}, ctx)["verdict"] == "incomplete"
    assert run_rule({"fn": "text_present", "args": {"texts": [], "steps": ["intro"]}}, ctx)["verdict"] == "incomplete"
    assert run_rule({"fn": "a11y_labels", "args": {}}, {**ctx, "report": None})["verdict"] == "incomplete"
    assert run_rule({"fn": "text_present", "args": {"texts": ["조회"], "steps": ["complete"]}}, ctx)["verdict"] == "fail"
    assert run_rule({"fn": "nope"}, ctx)["verdict"] == "incomplete"


def test_the_judge_sees_one_page_and_the_step_list():
    seen = []
    deps = {"llm_judge": lambda item, ctx: seen.append((item, ctx)) or {"verdict": "pass", "evidence": "ok"}, "normalize": OK}
    assert verify(bundle(), K, deps)["verdict"] == "pass"
    item, ctx = next((i, c) for i, c in seen if c["flowText"].startswith("[amount:default]"))
    assert item["target"] == "screen"
    assert [s["id"] for s in ctx["prd"]["steps"]] == FLOW["screens"]
    assert "금액 입력" in ctx["flowText"] and "예금자보호법" not in ctx["flowText"]
    users = []

    def generate(system, user, on_token):
        users.append(user)
        ids = [i["id"] for i in json.loads(user)["items"]]
        return json.dumps({"verdicts": {i: {"verdict": "pass", "evidence": "ok"} for i in ids}})

    assert verify(bundle(), K, {"generate": generate, "normalize": OK})["verdict"] == "pass"
    user = json.loads(next(u for u in users if "[confirm:default]" in u))
    assert "예금자보호법에 따라 보호됩니다" in user["flowText"] and "[amount:default]" not in user["flowText"]
    assert [s["id"] for s in user["steps"]] == FLOW["screens"]


def test_page_text_over_6000_characters_is_incomplete_not_truncated():
    b = bundle()
    b["screens"][("amount", "default")]["slots"]["header"][0]["props"]["children"] = "가" * 499
    long = copy.deepcopy(b)
    body = long["screens"][("amount", "default")]["slots"]["body"]
    body += [{"id": f"x{i}", "asset": "body-text", "props": {"children": "나" * 500}} for i in range(12)]
    r = verify(long, K, JUDGE)
    assert r["verdict"] == "blocked" and "reviewer" in r["unavailable"]
    assert any(i.get("reason") == "page-text-limit" and i["screen"] == "amount" for i in r["roles"]["reviewer"]["incomplete"])


def test_batch_judge_missing_or_unparsable_items_are_incomplete():
    items = [{"id": "a", "text": "톤"}, {"id": "b", "text": "용어"}]
    ctx = review_context(bundle(), "intro", "default", K)
    half = {"generate": lambda s, u, t: json.dumps({"verdicts": {"a": {"verdict": "pass", "evidence": "ok"}}}), "normalize": OK}
    assert batch_judge(items, ctx, half) == {"a": {"verdict": "pass", "evidence": "ok"},
                                             "b": {"verdict": "incomplete", "evidence": "missing-verdict"}}
    junk = {"generate": lambda s, u, t: "not json", "normalize": OK}
    assert {v["verdict"] for v in batch_judge(items, ctx, junk).values()} == {"incomplete"}
    assert {v["evidence"] for v in batch_judge(items, ctx, {"generate": half["generate"]}).values()} == {"normalization-unavailable"}


def test_compose_reuses_a_judgment_only_for_the_same_review_key_and_lineage():
    b = bundle()
    items = [{"id": "c1", "text": "톤"}]
    key = page_review_key(b, K, {}, "intro", "default", items)
    stored = stored_judgment(key, {"c1": {"verdict": "pass", "evidence": "ok"}})
    assert compose_review(stored, b, K, {}, "intro", "default", items)["status"] == "reused"
    changed = copy.deepcopy(b)                                                     # same composition IR, new PRD value
    changed["binding_values"] = {**b["binding_values"], "product.summaryItems": [{"label": "기본금리", "value": "연 2.1%"},
                                                                                 {"label": "가입기간", "value": "12개월"}]}
    assert changed["screens"] == b["screens"]
    assert compose_review(stored, changed, K, {}, "intro", "default", items) == \
        {"status": "needs_changes", "code": "review-evidence-missing"}
    k = copy.deepcopy(K); k.rules["r-terms-required"]["revision"] += 1             # a rule revision changes
    assert compose_review(stored, b, k, {}, "intro", "default", items)["code"] == "review-evidence-missing"
    tampered = {**stored, "verdicts": {"c1": {"verdict": "fail", "evidence": "x"}}}
    assert compose_review(tampered, b, K, {}, "intro", "default", items)["code"] == "review-evidence-missing"
    assert compose_review(stored, b, K, {}, "intro", "default", items, verify_lineage=lambda s: False)["status"] == "needs_changes"
    assert compose_review(None, b, K, {}, "intro", "default", items)["status"] == "needs_changes"


def test_judge_budget_three_fill_variants_by_ten_pages():
    # B0's ledger intent/outcome wrappers are not in this branch; a counting stand-in enforces the same ceiling.
    budget = 120                                                                   # maxCallsByOperation["design.generate"]
    calls = {"judge": 0}

    def generate(system, user, on_token):
        calls["judge"] += 1
        if calls["judge"] > budget:
            raise RuntimeError("call-budget")
        ids = [i["id"] for i in json.loads(user)["items"]]
        return json.dumps({"verdicts": {i: {"verdict": "pass", "evidence": "ok"} for i in ids}})

    deps, judgments, gui_calls = {"generate": generate, "normalize": OK}, {}, 0
    for variant in ("a", "b", "c"):
        screens = {}
        for s in FLOW["screens"]:
            out = generate_screen(s, K, FLOW, {}, strategy=strategy("structured"), binding_paths=frozenset(bindings(GOOD)))
            gui_calls += out["modelCalls"]
            chosen = [v for v in out["variants"] if v["variant"] == variant] or out["variants"][:1]
            screens[(s, "default")] = chosen[0]["composition"]
        screens[("eligibility", "ineligible")] = {**screens[("eligibility", "default")], "state": "ineligible"}
        assert len(screens) == 10
        r = verify(bundle(screens=screens), K, deps, judgments=judgments)
        assert r["verdict"] == "pass", r["roles"]
    assert gui_calls == 0 and 1 <= calls["judge"] <= 30 <= budget


# ---- tester evidence: the assembled report is what the real run approval compares ----------------------------------

def test_assemble_mirrors_the_runtime_and_raises_on_mismatch():
    from design_loop.evidence import assemble
    from workspace.rules import contract_hash
    build = {**PASS_REPORT["build"], "previewHtml": "<html>x</html>", "files": {"a": "b"}, "sourceFiles": {}}
    browser = {k: v for k, v in PASS_REPORT.items() if k not in ("build", "sourceHash", "catalogHash", "artifactSha256",
                                                                 "contractHash", "outputType")}
    report = assemble(build, browser, contract=CONTRACT_FULL)
    assert "files" not in report["build"] and "previewHtml" not in report["build"] and "sourceFiles" not in report["build"]
    assert report["contractHash"] == contract_hash(CONTRACT_FULL) and report["outputType"] == "react"
    assert report["artifactSha256"] == __import__("hashlib").sha256(b"<html>x</html>").hexdigest()
    with pytest.raises(ValueError):
        assemble(build, {**browser, "bundleHash": "0" * 64}, contract=CONTRACT_FULL)
    with pytest.raises(ValueError):
        assemble(build, browser, contract={**CONTRACT_FULL, "catalogHash": "0" * 64})
    with pytest.raises(ValueError):
        assemble({**build, "ok": False}, browser, contract=CONTRACT_FULL)


@pytest.fixture
def local_browser(monkeypatch):
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and os.path.isfile(CHROMIUM):
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", CHROMIUM)


@needs
def test_real_run_approval_accepts_a_round_whose_report_came_from_assemble(local_browser):
    import base64
    import hashlib
    from test_workspace_http import FakeLambda
    from test_workspace_storage import FakeS3, FakeTable
    from test_workspace_worker import request
    from fixtures.make_design_pass import seed_screens
    from design_loop.contract import derive
    from design_loop.convention import Registry
    from design_loop.evidence import assemble
    from design_loop.react_project import compile_local, compile_request, kit_catalog_hash, project
    from workspace.browser import evaluate_bundle
    from workspace.http import WorkspaceAPI
    from workspace.react_artifacts import preview_html
    from workspace.storage import Storage
    from workspace.worker import Worker
    values = bindings(GOOD)
    cases = enumerate_cases(EXP["conditions"])["cases"]
    registry = Registry(K).register_flow(FLOW)
    derived = derive(GOOD, FLOW, K, registry, [], cases=cases, criteria={"catalogHash": kit_catalog_hash()})["contract"]
    files = project(FLOW, seed_screens(K, FLOW, values, EXP), K, values, cases=cases, registry=registry)
    assembled = []

    def react_call(payload):                      # the engine path: compile -> Browser -> assemble
        build = compile_local(compile_request(payload["files"], catalog_hash=payload["catalogHash"],
                                              contract=payload["contract"]))
        dist = {n: base64.b64decode(v) for n, v in build["files"].items()}
        browser = evaluate_bundle(dist, payload["contract"], expected_hash=build["bundleHash"])
        report = assemble(build, browser, contract=payload["contract"])
        assembled.append(copy.deepcopy(report))
        assert report["artifactSha256"] == hashlib.sha256(preview_html(dist).encode()).hexdigest()
        return report

    def model(system, user, images, model_id, maximum, trace_id, purpose):
        return json.dumps({"files": files}, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}, {"modelId": model_id}

    store = Storage(table=FakeTable(), s3=FakeS3(), bucket="private")
    api = WorkspaceAPI(storage=store, lambda_client=FakeLambda(), worker_fn="worker")
    worker = Worker(storage=store, model_call=model, react_call=react_call)
    status, data = request(api, "POST", "/contracts", derived)
    assert status == 201, data
    status, data = request(api, "POST", f"/contracts/{data['contract']['id']}/approve", {"version": data["contract"]["version"]})
    assert status == 200, data
    contract = data["contract"]
    status, data = request(api, "POST", "/runs", {"contractId": contract["id"], "contractVersion": contract["version"],
                           "outputType": "react", "model": "global.openai.gpt-6-astra", "variant": "baseline",
                           "generationMode": "guided", "maxRounds": 1, "requestId": "assembled-1"})
    assert status == 202, data
    assert worker.handle({"owner": "designer", "jobId": data["job"]["id"]})["status"] == "completed"
    run_record = store.get("designer", "run", data["run"]["id"])
    [row] = run_record["rounds"]
    [report] = assembled
    stored = json.loads(store.get_blob(row["reportKey"]))          # the worker's own stamped report
    for key in ("sourceHash", "bundleHash", "catalogHash", "artifactSha256", "contractHash"):
        assert report[key] == stored[key], key
    assert (report["sourceHash"], report["bundleHash"], report["artifactSha256"]) == \
        (row["sourceHash"], row["bundleHash"], row["artifactSha256"])
    assert report["contractHash"] == run_record["contractHash"]
    # Replace the stored report with the pure assembled report: the real approval must still accept it.
    store.put_blob(row["reportKey"], json.dumps(report, ensure_ascii=False).encode(), "application/json")
    body = {"round": 1, "artifactSha256": row["artifactSha256"], "contractVersion": run_record["contractVersion"],
            "sourceHash": row["sourceHash"], "bundleHash": row["bundleHash"]}
    status, accepted = request(api, "POST", f"/runs/{run_record['id']}/approve", body)
    assert status == 200, accepted


def test_engine_modules_import_only_stdlib_pure_schema_and_the_lazy_verifier_helpers():
    import ast
    root = Path(__file__).resolve().parents[1]
    allowed = {"workspace.ontology_schema", "workspace.ontology_ux", "workspace.rules", "workspace.react_quality"}
    for name in ("verify_graph.py", "rules_v2.py", "evidence.py"):
        for item in ast.walk(ast.parse((root / "design_loop" / name).read_text(encoding="utf-8"))):
            if isinstance(item, ast.ImportFrom) and item.level == 0:
                assert item.module in allowed or item.module.split(".")[0] in sys.stdlib_module_names, (name, item.module)
            elif isinstance(item, ast.Import):
                assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in item.names), name


        top = ast.parse((root / "design_loop" / name).read_text(encoding="utf-8")).body
        for item in top:                           # the verifier helpers are imported lazily, never at module level
            if isinstance(item, ast.ImportFrom) and item.level == 0:
                assert item.module not in {"workspace.rules", "workspace.react_quality"}, (name, item.module)


def _with_rule(targets, rid="r-review-template"):
    k = copy.deepcopy(K)
    k.rules[rid] = {"id": rid, "ruleId": rid, "statement": "템플릿 공통 고지를 유지한다", "required": True,
                    "severity": "critical", "targets": list(targets), "citation": None, "appliesWhen": None,
                    "reviewState": "approved", "revision": 1, "contentHash": "c" * 64}
    return k


def test_required_rule_on_a_page_template_is_judged_on_every_consuming_page():
    """PR #30 review 1, #4: a PageTemplate target resolves to the screens composed from it."""
    tid = K.screens[FLOW["screens"][0]]["templateId"]
    assert tid in K.templates
    k = _with_rule([tid])
    seen = []
    judge = {"llm_judge": lambda item, ctx: seen.append((item["id"], ctx["flowText"].split("]")[0])) or
             {"verdict": "pass", "evidence": "ok"}, "normalize": OK}
    r = verify(bundle(checklist=design_checklist(GOOD, k)), k, judge)
    consuming = {s for s in FLOW["screens"] if k.screens[s]["templateId"] == tid}
    judged = {page[1:].split(":")[0] for item, page in seen if item == "d-rule-r-review-template"}
    assert consuming and judged == consuming, (judged, consuming)
    assert r["verdict"] == "pass"


def test_unresolved_required_rule_target_blocks():
    k = _with_rule(["no-such-node"])
    r = verify(bundle(checklist=design_checklist(GOOD, k)), k, JUDGE)
    assert r["verdict"] == "blocked" and "reviewer" in r["unavailable"] and not r["approvable"]
    assert any(i.get("reason") == "target-unresolved" for i in r["roles"]["reviewer"]["incomplete"])
