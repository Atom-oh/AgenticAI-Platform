"""Verification graph: reviewer, tester and coverage -> one fail-closed master verdict (engine plan, Task E16;
V-01, V-02, V-04, V-05; Codex #11, #22, #23).

- **Reviewer:** deterministic `composition.validate` of every page, `rules_v2` for `method: "rule"` checklist items,
  and the injected judge for the others, once per page each item targets. `deps["generate"]` judges all of a
  page's items in one batched call (`batch_judge`); otherwise `deps["llm_judge"]` judges item by item. Both go
  through the verify-only `deps["normalize"]` boundary first. A cached judgment is reused only when the injected
  `deps["verify_lineage"]` confirms its lineage is current. No judge, no normalization hook, an empty checklist, a
  page text over 6 000 characters or any `incomplete` item makes the reviewer `unavailable`.
- **Tester:** the assembled report (`evidence.assemble`) must match the bundle's build (`stale-evidence`), carry the
  contract's catalog and contract hashes, and satisfy the production predicate injected as
  `deps["react_report_passes"]` (`workspace.react_quality.react_report_passes`), with the contract hash from
  `deps["contract_hash"]` (`workspace.rules.contract_hash`). Failed required checks are critical, accessibility violations
  major. The large-text second pass (`large_text_report`, text_scale >= 2, same bundle and contract hash) must
  pass with empty `overflow`/`unscaled`; anything else is critical. No report or contract means the tester is
  `unavailable`.
- **Coverage:** `coverage.check` (E15). A blocked coverage result blocks the verdict.

`blocked` when any required role is unavailable or coverage is blocked, `fail` on any critical finding, `pass`
otherwise. `approvable` needs `pass` and no `new-asset-candidate` finding. `run` repairs only `fail` verdicts, always
rebuilds the evidence after a regeneration, and escalates at the cap without ever granting approvability.

`design_loop` imports only the standard library and the pure schema modules (PR #30 review 1, #9), so the tester's
production predicate and contract hasher are injected through `deps`; a missing adapter makes the tester
`unavailable` (blocked), never a pass.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from workspace.ontology_schema import digest, seal, validate_edge, validate_node
from workspace.ontology_schema import source_ref as _source_ref

from . import coverage as coverage_mod
from . import rules_v2
from .composition import text_of, validate
from .convention import Registry
from .evidence import EvidenceUnavailable, contract_hash
from .model_call import call

RESOURCES = Path(__file__).resolve().parent / "resources"
ROLES = ("reviewer", "tester", "coverage")
MAX_FLOW_TEXT = 6000
PROMPT_VERSION = "verify-judge-1"
JUDGE_SYSTEM = (
    "당신은 은행 UX 디자인 검수자다. 한 화면의 가시 문구에 대해 체크리스트 항목들을 판정한다. 각 항목의 verdict 는 "
    "pass|fail|incomplete 중 하나, evidence 는 화면 문구를 인용한 한국어 한두 문장이다. 출력은 JSON 하나만: "
    '{"verdicts": {"<itemId>": {"verdict": "pass|fail|incomplete", "evidence": "..."}}}'
)
SEVERITIES = ("critical", "major", "minor")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


# ---- checklist ------------------------------------------------------------------------------------------------

def _resource(name):
    manifest = json.loads((RESOURCES / "manifest.json").read_text(encoding="utf-8"))
    data = (RESOURCES / name).read_bytes()
    import hashlib
    if hashlib.sha256(data).hexdigest() != manifest["resources"][name]["sha256"]:
        raise ValueError("packaged resource hash mismatch")
    return json.loads(data.decode("utf-8"))


def checklist_resource_hash():
    """The sha256 the job's backend configuration revision records for the packaged checklist (Z6)."""
    return json.loads((RESOURCES / "manifest.json").read_text(encoding="utf-8"))["resources"]["checklists.json"]["sha256"]


def _value(entry):
    return entry.get("value") if isinstance(entry, dict) else None


def _applies(applies_to, prd):
    for key, wanted in (applies_to or {}).items():
        if key not in ("productType", "category") or _value(prd.get(key)) != wanted:
            return False                               # a key the PRD cannot answer is not applicable
    return True


def design_checklist(prd, k):
    """Base items from the packaged, versioned resource plus one item per preferential condition, notice and
    required PolicyRule. Every item carries its `source`."""
    items = []
    for cl in _resource("checklists.json"):
        if not _applies(cl.get("appliesTo"), prd):
            continue
        for item in cl.get("items") or []:
            method = item.get("method", "llm")
            out = {**copy.deepcopy(item), "method": method, "severity": item.get("severity", "major"),
                   "source": {"kind": "base", "set": cl["id"]}}
            if method == "rule" and (item.get("rule") or {}).get("fn") not in rules_v2.RULES:
                out["method"] = "llm"                  # a base item without a v2 port is judged
                out.pop("rule", None)
            items.append(out)
    for n, p in enumerate(prd.get("preferential") or [], 1):
        base = f"$spec.preferential.pref-{n}"
        items.append({"id": f"d-pref-{n}", "method": "rule", "target": "screen", "severity": "major",
                      "text": f"우대조건 '{_value(p.get('condition'))}'과 우대금리 {_value(p.get('rate'))}가 화면에 고지된다",
                      "rule": {"fn": "text_present", "args": {"texts": [f"{base}.condition", f"{base}.rate"],
                                                             "steps": ["preferential", "confirm"], "any": True}},
                      "source": {"kind": "prd", "field": f"preferential.{p.get('id')}",
                                 "cite": (p.get("rate") or {}).get("cite")}})
    for n, notice in enumerate(prd.get("notices") or [], 1):
        items.append({"id": f"d-notice-{n}", "method": "rule", "target": "screen", "severity": "critical",
                      "text": f"필수 고지 '{_value(notice.get('text'))}'가 약관 또는 확인 화면에 있다",
                      "rule": {"fn": "text_present", "args": {"texts": [f"$spec.notice.notice-{n}"],
                                                             "steps": ["terms", "confirm"], "any": True}},
                      "source": {"kind": "prd", "field": f"notices.{notice.get('id')}",
                                 "cite": (notice.get("text") or {}).get("cite")}})
    for rid in sorted(k.rules):
        rule = k.rules[rid]
        if not rule.get("required") or rule.get("reviewState", "approved") != "approved":
            continue
        items.append({"id": f"d-rule-{rid}", "method": "llm", "target": "screen",
                      "severity": rule.get("severity") if rule.get("severity") in SEVERITIES else "major",
                      "text": rule["statement"], "targets": list(rule.get("targets", [])),
                      "source": {"kind": "policy-rule", "ruleId": rule.get("ruleId", rid),
                                 "citation": rule.get("citation")}})
    return items


# ---- judge context, batching and the review key -------------------------------------------------------------

def _values(bundle):
    return bundle.get("binding_values") or {}


def review_context(bundle, screen_id, state, k=None):
    """Per-page judge context: the flow's step list and only this page's text, with resolved PRD values."""
    flow = bundle["flow"]
    titles = {s: ((k.screens.get(s) or {}).get("title") if k is not None else None) or s for s in flow["screens"]}
    page = bundle["screens"][(screen_id, state)]
    return {"prd": {"steps": [{"id": s, "title": titles[s]} for s in flow["screens"]]},
            "flowText": f"[{screen_id}:{state}] " + text_of(page, _values(bundle), k)}


def judge_payload(items, context):
    """The exact `(system, user)` strings `batch_judge` sends; the review key hashes these."""
    user = json.dumps({"items": [{"id": i["id"], "text": i["text"], "target": "screen"} for i in items],
                       "steps": context["prd"]["steps"], "flowText": context["flowText"]},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return JUDGE_SYSTEM, user


def rule_revisions(k):
    return {rid: [r.get("revision"), r.get("contentHash")] for rid, r in sorted(k.rules.items())}


def review_key(system, user, *, model, prompt_version, profile_hash, revisions):
    return digest({"judgeSystem": system, "judgeUser": user, "model": model, "promptVersion": prompt_version,
                          "profileHash": profile_hash, "ruleRevisions": revisions})


def _incomplete(items, reason):
    return {i["id"]: {"verdict": "incomplete", "evidence": reason} for i in items}


def batch_judge(items, page_context, deps):
    """One model call judging every item of one page. A missing or unparsable item verdict is `incomplete`."""
    system, user = judge_payload(items, page_context)
    text, blocked = call(deps, system, user)
    if blocked:
        return _incomplete(items, blocked)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return _incomplete(items, "parse-failed")
    verdicts = parsed.get("verdicts") if isinstance(parsed, dict) else None
    out = {}
    for item in items:
        v = verdicts.get(item["id"]) if isinstance(verdicts, dict) else None
        verdict = v.get("verdict") if isinstance(v, dict) else None
        out[item["id"]] = ({"verdict": verdict, "evidence": str(v.get("evidence", ""))[:600]}
                           if verdict in ("pass", "fail", "incomplete") else {"verdict": "incomplete",
                                                                             "evidence": "missing-verdict"})
    return out


def _judge_one_by_one(items, context, deps):
    judge, normalize = deps.get("llm_judge"), deps.get("normalize")
    if not callable(normalize):
        return _incomplete(items, "normalization-unavailable")
    try:
        normalize(context["flowText"])
        normalize(json.dumps(context["prd"], ensure_ascii=False))
        for item in items:
            normalize(item["text"])
    except Exception:  # noqa: BLE001 - a residual identifier blocks the call (fail closed)
        return _incomplete(items, "normalization-blocked")
    out = {}
    for item in items:
        try:
            v = judge({**item, "target": "screen"}, context)
        except Exception:  # noqa: BLE001
            v = None
        verdict = v.get("verdict") if isinstance(v, dict) else None
        out[item["id"]] = ({"verdict": verdict, "evidence": str(v.get("evidence", ""))[:600]}
                           if verdict in ("pass", "fail", "incomplete") else {"verdict": "incomplete",
                                                                             "evidence": "judge-failed"})
    return out


def receipt(key, verdicts, admissions=()):
    return digest({"reviewKey": key, "verdicts": verdicts, "admissions": list(admissions)})


def stored_judgment(key, verdicts, admissions=()):
    return {"reviewKey": key, "verdicts": copy.deepcopy(verdicts), "receiptHash": receipt(key, verdicts, admissions),
            "admissions": list(admissions)}


def _local_lineage(stored):
    return stored.get("receiptHash") == receipt(stored.get("reviewKey"), stored.get("verdicts"), stored.get("admissions", ()))


def page_review_key(bundle, k, deps, screen_id, state, items):
    context = review_context(bundle, screen_id, state, k)
    system, user = judge_payload(items, context)
    return review_key(system, user, model=(deps or {}).get("model"), prompt_version=PROMPT_VERSION,
                      profile_hash=(deps or {}).get("profileHash"), revisions=rule_revisions(k))


def _current_lineage(stored, verify_lineage):
    """Reuse needs BOTH the local receipt (integrity of the stored record) and the injected current-lineage
    verifier (the admissions it names are still current). A content digest alone cannot establish admission
    authority, so a missing, failing or rejecting verifier prevents reuse (PR #30 review 1, #7)."""
    if not callable(verify_lineage) or not isinstance(stored, dict) or not _local_lineage(stored):
        return False
    try:
        return verify_lineage(copy.deepcopy(stored)) is True
    except Exception:  # noqa: BLE001 - a lineage check that cannot run is missing evidence
        return False


def compose_review(stored, bundle, k, deps, screen_id, state, items, *, verify_lineage=None):
    """`design.compose` reuses a stored judgment only when the recomputed review key matches and its lineage
    re-verifies through `verify_lineage`, injected by C (no default: without it nothing is reused)."""
    if not isinstance(stored, dict):
        return {"status": "needs_changes", "code": "review-evidence-missing"}
    try:
        key = page_review_key(bundle, k, deps, screen_id, state, items)
        ok = stored.get("reviewKey") == key and _current_lineage(stored, verify_lineage)
    except Exception:  # noqa: BLE001 - a lineage check that cannot run is missing evidence
        ok = False
    if not ok:
        return {"status": "needs_changes", "code": "review-evidence-missing"}
    return {"status": "reused", "verdicts": copy.deepcopy(stored["verdicts"])}


# ---- roles ----------------------------------------------------------------------------------------------------

def _finding(role, severity, code, message="", **extra):
    return {"role": role, "severity": severity, "code": code, "message": message, **extra}


def _page_order(bundle):
    order = {s: i for i, s in enumerate(bundle["flow"]["screens"])}
    return sorted(bundle["screens"], key=lambda key: (order.get(key[0], len(order)), key[1] != "default", key[1]))


def _unresolved_targets(item, k):
    """Targets that are not a known Screen, renderable asset, PageTemplate or Procedure of this knowledge."""
    known = (k.screens, k.assets, k.templates, getattr(k, "procedures", {}))
    return sorted(t for t in item.get("targets") or [] if not any(t in table for table in known))


def _item_pages(item, bundle, k):
    """Pages an item targets: the screen itself, a screen that renders the target asset, a screen composed from
    the target PageTemplate, or a screen of the target Procedure (PR #30 review 1, #4)."""
    pages = _page_order(bundle)
    targets = item.get("targets")
    if not targets:
        return pages
    from .route import shown_assets
    wanted = set(targets)

    def hit(s):
        screen = k.screens.get(s)
        if s in wanted:
            return True
        if screen is None:
            return False
        return (screen.get("templateId") in wanted or screen.get("procedureId") in wanted
                or bool(wanted & set(shown_assets(k, s))))
    return [(s, st) for s, st in pages if hit(s)]


def _reviewer(bundle, k, deps, mode, judgments):
    findings, incomplete = [], []
    flow, values = bundle["flow"], _values(bundle)
    for screen, state in _page_order(bundle):
        for f in validate(bundle["screens"][(screen, state)], k, flow=flow, binding_paths=frozenset(values), mode=mode):
            findings.append(_finding("reviewer", f["severity"], f["code"], f.get("message", ""), screen=screen,
                                     state=state, path=f.get("path")))
    checklist = bundle["checklist"] if "checklist" in bundle else design_checklist(bundle["prd"], k)
    if not checklist:
        return findings, ["empty-checklist"], {"items": 0}
    registry = bundle.get("registry") or Registry(k).register_flow(flow)
    ctx = {"flow": flow, "screens": bundle["screens"], "k": k, "binding_values": values, "registry": registry,
           "report": bundle.get("browser_report")}
    llm = []
    for item in checklist:
        severity = item.get("severity") if item.get("severity") in SEVERITIES else "major"
        if item.get("method") == "rule" and (item.get("rule") or {}).get("fn") in rules_v2.RULES:
            v = rules_v2.run_rule(item["rule"], ctx)
            if v["verdict"] == "fail":
                findings.append(_finding("reviewer", severity, "checklist-fail", v["evidence"], item=item["id"]))
            elif v["verdict"] != "pass":
                incomplete.append({"item": item["id"], "reason": v["evidence"]})
        else:
            llm.append(item)
    deps = deps or {}
    batched = callable(deps.get("generate"))
    if llm and not batched and not callable(deps.get("llm_judge")):
        return findings, incomplete + [{"item": "*", "reason": "judge-unavailable"}], {"items": len(checklist)}
    calls = 0
    by_page = {}
    for item in llm:
        unresolved = _unresolved_targets(item, k)
        if unresolved:                  # a required target that names nothing known cannot be reviewed: block
            incomplete.append({"item": item["id"], "reason": "target-unresolved", "targets": unresolved})
            continue
        for page in _item_pages(item, bundle, k):
            by_page.setdefault(page, []).append(item)
    for (screen, state), items in by_page.items():
        context = review_context(bundle, screen, state, k)
        if len(context["flowText"]) > MAX_FLOW_TEXT:
            incomplete += [{"item": i["id"], "screen": screen, "state": state, "reason": "page-text-limit"} for i in items]
            continue
        if batched:
            system, user = judge_payload(items, context)
            key = review_key(system, user, model=deps.get("model"), prompt_version=PROMPT_VERSION,
                             profile_hash=deps.get("profileHash"), revisions=rule_revisions(k))
            stored = judgments.get(key) if judgments is not None else None
            if stored is not None and _current_lineage(stored, deps.get("verify_lineage")):
                verdicts = stored["verdicts"]
            else:
                verdicts = batch_judge(items, context, deps)
                calls += 1
                if judgments is not None and all(v["verdict"] != "incomplete" for v in verdicts.values()):
                    judgments[key] = stored_judgment(key, verdicts)
        else:
            verdicts = _judge_one_by_one(items, context, deps)
            calls += len(items)
        for item in items:
            v = verdicts.get(item["id"]) or {"verdict": "incomplete", "evidence": "missing-verdict"}
            severity = item.get("severity") if item.get("severity") in SEVERITIES else "major"
            if v["verdict"] == "fail":
                findings.append(_finding("reviewer", severity, "checklist-fail", v["evidence"], item=item["id"],
                                         screen=screen, state=state))
            elif v["verdict"] != "pass":
                incomplete.append({"item": item["id"], "screen": screen, "state": state, "reason": v["evidence"]})
    return findings, incomplete, {"items": len(checklist), "judgeCalls": calls}


def _predicates(deps):
    """The injected production adapters, or None (tester unavailable) when either is missing."""
    passes, hasher = (deps or {}).get("react_report_passes"), (deps or {}).get("contract_hash")
    if not callable(passes) or not callable(hasher):
        return None
    return passes, hasher


def _tester(bundle, deps):
    report, contract = bundle.get("browser_report"), bundle.get("contract")
    if not isinstance(report, dict) or not isinstance(contract, dict):
        return [], "missing-evidence"
    build = bundle.get("build")
    if not isinstance(build, dict) or build.get("bundleHash") != report.get("bundleHash"):
        return [_finding("tester", "critical", "stale-evidence", "the Browser report is not for this build")], "stale"
    predicates = _predicates(deps)
    if predicates is None:
        return [], "predicate-unavailable"
    passes, hasher = predicates
    findings = []
    inner = report.get("build") or {}
    if (inner.get("bundleHash") != report.get("bundleHash") or inner.get("catalogHash") != contract.get("catalogHash")
            or report.get("catalogHash") != contract.get("catalogHash")):
        findings.append(_finding("tester", "critical", "react-evidence-mismatch",
                                 "report, build and contract catalog/bundle hashes must match"))
    try:
        expected_hash = contract_hash(contract, hasher)
    except ValueError:
        expected_hash = None
    except EvidenceUnavailable:
        return [], "predicate-unavailable"
    if expected_hash is None or report.get("contractHash") != expected_hash:
        findings.append(_finding("tester", "critical", "contract-hash-mismatch", "the report is not for this contract"))
    required = {r["id"] for r in contract.get("rules", []) if r.get("required", True)}
    for check in report.get("checks") or []:
        if check.get("caseId") in required and check.get("status") != "pass":
            findings.append(_finding("tester", "critical", "browser-check-failed", "required Browser check failed",
                                     rule=check.get("caseId")))
    for v in (report.get("accessibility") or {}).get("violations") or []:
        findings.append(_finding("tester", "major", "accessibility-violation", "",
                                 rule=v.get("id") if isinstance(v, dict) else str(v)))
    try:
        passed = passes(contract, report) is True
    except Exception:  # noqa: BLE001 - a predicate that cannot run is missing evidence
        return findings, "predicate-unavailable"
    if not passed and not any(f["severity"] == "critical" for f in findings):
        findings.append(_finding("tester", "critical", "react-report-failed",
                                 "the report does not satisfy react_report_passes"))
    findings += _large_text(bundle.get("large_text_report"), build, expected_hash)
    return findings, None


MIN_TEXT_SCALE = 2.0


def _large_text(large, build, expected_hash):
    """V-02 (E13): the second Browser pass at text_scale >= 2 over the SAME bundle and contract must pass with no
    overflow and no unscaled text. Missing, failed, unscaled or foreign evidence is a critical tester failure
    (PR #30 review 1, #5)."""
    if not isinstance(large, dict):
        return [_finding("tester", "critical", "large-text-missing", "the large-text second pass is missing")]
    if (large.get("bundleHash") != build.get("bundleHash") or (large.get("build") or {}).get("bundleHash")
            not in (None, build.get("bundleHash")) or expected_hash is None
            or large.get("contractHash") != expected_hash):
        return [_finding("tester", "critical", "large-text-mismatch",
                         "the large-text report is not for this bundle and contract")]
    result = large.get("largeText")
    scale = large.get("textScale")
    ok = (isinstance(result, dict) and result.get("status") == "pass" and result.get("overflow") == []
          and result.get("unscaled") == [] and isinstance(scale, (int, float)) and not isinstance(scale, bool)
          and scale >= MIN_TEXT_SCALE and large.get("passed") is True)
    if not ok:
        return [_finding("tester", "critical", "large-text-failed", "the large-text second pass did not pass",
                         overflow=list((result or {}).get("overflow") or [])[:20] if isinstance(result, dict) else [],
                         unscaled=list((result or {}).get("unscaled") or [])[:20] if isinstance(result, dict) else [])]
    return []


def verify(bundle, k, deps, *, mode="fill", required=ROLES, judgments=None):
    findings, unavailable, roles = [], [], {}
    r_findings, r_incomplete, r_meta = _reviewer(bundle, k, deps, mode, judgments)
    findings += r_findings
    if r_incomplete:
        unavailable.append("reviewer")
    roles["reviewer"] = {"status": "unavailable" if r_incomplete else "ran", "incomplete": r_incomplete, **r_meta}
    t_findings, t_missing = _tester(bundle, deps)
    findings += t_findings
    if t_missing:
        unavailable.append("tester")
    roles["tester"] = {"status": "unavailable" if t_missing else "ran", **({"reason": t_missing} if t_missing else {})}
    cov = coverage_mod.check(bundle["prd"], bundle["flow"], bundle["expectation"], bundle["screens"], k,
                             _values(bundle))
    findings += [{"role": "coverage", **f} for f in cov["findings"]]
    if cov["blocked"]:
        unavailable.append("coverage")
    roles["coverage"] = {"status": "blocked" if cov["blocked"] else "ran", "findings": len(cov["findings"])}
    for role in ROLES:
        roles[role]["findings"] = len([f for f in findings if f["role"] == role])
    missing = [role for role in unavailable if role in required]
    if missing:
        verdict = "blocked"
    elif any(f["severity"] == "critical" for f in findings):
        verdict = "fail"
    else:
        verdict = "pass"
    approvable = verdict == "pass" and not any(f["code"] == "new-asset-candidate" for f in findings)
    return {"verdict": verdict, "approvable": approvable, "findings": findings, "unavailable": unavailable,
            "roles": roles}


def run(bundle, k, deps, *, regenerate, rebuild, max_rounds=3, emit=None, mode="fill", judgments=None):
    if not callable(regenerate) or not callable(rebuild):
        raise ValueError("regenerate and rebuild are required")
    if not 1 <= max_rounds <= 5:
        raise ValueError("max_rounds out of range")
    history, current = [], bundle
    for number in range(1, max_rounds + 1):
        result = verify(current, k, deps, mode=mode, judgments=judgments)
        history.append({"round": number, **result})
        if emit:
            emit({"type": "verify-round", "round": number, "verdict": result["verdict"]})
        if result["verdict"] == "blocked":
            return {"verdict": "blocked", "approvable": False, "rounds": number, "history": history, "escalated": False}
        if result["verdict"] == "pass":
            return {"verdict": "pass", "approvable": result["approvable"], "rounds": number, "history": history,
                    "escalated": False}
        if number == max_rounds:
            break
        current = rebuild(regenerate(current, result["findings"]))   # never judge a repair with old evidence
    return {"verdict": "fail", "approvable": False, "rounds": len(history), "history": history, "escalated": True}


# ---- HITL metrics (V-04, V-05; O-11) --------------------------------------------------------------------------

KINDS = ("approve", "edit", "reject", "correction")


class Metrics:
    def __init__(self):
        self.events = []

    def record(self, kind, *, screenId, reason=None, scope=None):  # noqa: N803 - interface name
        if kind not in KINDS:
            raise ValueError("Unknown metric kind")
        if not isinstance(screenId, str) or not screenId:
            raise ValueError("screenId is required")
        if kind == "reject" and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("A rejection needs a reason")
        self.events.append({"kind": kind, "screenId": screenId, "reason": reason, "scope": scope})

    def summary(self):
        edits, reasons, corrections, approved = {}, {}, [], []
        for e in self.events:
            if e["kind"] == "edit":
                edits[e["screenId"]] = edits.get(e["screenId"], 0) + 1
            elif e["kind"] == "reject":
                reasons[e["reason"].strip()] = reasons.get(e["reason"].strip(), 0) + 1
            elif e["kind"] == "correction":
                corrections.append({"screenId": e["screenId"], "scope": e["scope"], "reason": e["reason"]})
            elif e["kind"] == "approve":
                approved.append(e["screenId"])
        return {"approvedWithoutEdit": len([s for s in dict.fromkeys(approved) if not edits.get(s)]),
                "editsPerScreen": edits, "rejectReasons": reasons, "humanCorrections": corrections}


def rejection_candidates(metrics, *, project_id, source_ref, revisions=None):
    """Each distinct human rejection reason -> a declared PolicyRule candidate (V-05). Approval needs a business
    source first (`ontology_store` refuses otherwise), so these stay candidates: "업무 근거 연결 필요"."""
    ref = _source_ref(source_ref)
    if ref["sourceKind"] != "run-round":
        raise ValueError("rejection candidates cite the run round")
    nodes, edges, screens = [], [], {}
    for e in metrics.events:
        if e["kind"] == "reject":
            screens.setdefault(e["reason"].strip(), []).append(e["screenId"])
    for reason in sorted(screens):
        rule_id = "rej-" + digest([project_id, reason])[:24]
        node = seal({"id": rule_id, "scope": {"kind": "project", "projectId": project_id}, "type": "PolicyRule",
                            "title": reason[:300], "revision": 1, "sourceRefs": [ref], "provenance": "declared",
                            "reviewState": "candidate", "tombstone": False,
                            "properties": {"ruleId": rule_id, "statement": reason, "required": False,
                                           "severity": "major"}})
        nodes.append(validate_node(node))
        for screen in dict.fromkeys(screens[reason]):
            revision = (revisions or {}).get(screen)
            if type(revision) is int and revision >= 1:
                edges.append(validate_edge(seal({
                    "id": "gov-" + digest([screen, rule_id])[:24], "type": "GOVERNED_BY",
                    "src": {"id": screen, "revision": revision}, "dst": {"id": rule_id, "revision": 1},
                    "sourceRefs": [ref], "provenance": "declared", "reviewState": "candidate", "tombstone": False})))
    return {"nodes": nodes, "edges": edges, "queueLabel": "업무 근거 연결 필요"}


def failure_checklist(history):
    """Distinct (role, code) failures across rounds with the rounds they appeared in (P-04 input)."""
    out, last = {}, history[-1]["round"] if history else None
    for entry in history:
        for f in entry.get("findings", []):
            key = (f.get("role"), f.get("code"))
            item = out.setdefault(key, {"id": f"f-{f.get('role')}-{f.get('code')}", "role": f.get("role"),
                                        "code": f.get("code"), "severity": f.get("severity"), "rounds": []})
            if _RANK.get(f.get("severity"), 9) < _RANK.get(item["severity"], 9):
                item["severity"] = f["severity"]
            if entry["round"] not in item["rounds"]:
                item["rounds"].append(entry["round"])
    return [{**item, "resolved": last not in item["rounds"]} for item in out.values()]
