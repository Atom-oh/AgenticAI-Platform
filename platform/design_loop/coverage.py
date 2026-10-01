"""Coverage verifier: expected graph x compositions (engine plan, Task E15; V-03; Codex #9, #11).

Every per-case check reads `composition.visible(screens[(screen, "default")], case)`, the tree actually shown in
that case, never the full tree (review round 16, AJ1). Requirements come from the independent expectation
(`flow.expected`) and the knowledge view, never from the compositions under test.

- `flow_issues` results are critical.
- Every screen on a case path needs a default composition (`missing-screen`).
- Every product binding the screen's shown assets declare must be visible as text (`missing-required`), unless its
  asset is a conditional target hidden in that case.
- `include` targets must be present when the condition holds and absent when it does not; `exclude` targets must be
  absent when the condition holds (`missing-conditional`, `forbidden-present`).
- Asset targets of applicable required rules must be present on the screens that show them (`missing-rule-target`).
- Every PRD notice must be bound on a designated screen of each case path that has one (`missing-notice`): the
  flow's confirm screen (id or pageId `confirm`) and screens showing a rule-targeted asset that binds the notice. A
  flow with no designated screen at all fails closed.
- Every state `gui.states_for` lists needs a composition (`missing-state`, major).

`blocked` is true when case enumeration was truncated or `k.complete` is false; a blocked result never lets a screen
become approvable.
"""
from __future__ import annotations

from workspace.ontology_ux import evaluate
from workspace.ontology_schema import digest

from .composition import text_of, visible, walk
from .contract import required_texts
from .flow import enumerate_cases, flow_issues, traverse
from .gui import states_for
from .route import shown_assets

CONFIRM = "confirm"


def _norm(text):
    return " ".join(str(text).split())


def _approved(entry):
    return entry.get("reviewState", "approved") == "approved"


def _holds(when, case):
    try:
        return evaluate(when, case)
    except ValueError:
        return None


def _conditions(k, screen):
    out, seen = [], set()
    for a in shown_assets(k, screen):
        for cond in (k.assets.get(a) or {}).get("conditions", []):
            key = (a, cond.get("id"))
            if cond.get("effect") in ("include", "exclude") and key not in seen:
                seen.add(key)
                out.append(cond)
    return out


def _visibility(k, screen):
    """Asset id -> visibleWhen spec derived from the screen's conditions (same rule as gui.fill)."""
    out = {}
    for cond in _conditions(k, screen):
        out.setdefault(cond["target"], {"when": cond["when"], "negate": cond["effect"] == "exclude"})
    return out


def notice_screens(flow, k, path):
    """Screens designated to carry the notice bound at `path`."""
    rule_targets = {t for r in k.rules.values() if _approved(r) for t in r.get("targets", [])}
    out = []
    for s in flow["screens"]:
        screen = k.screens.get(s) or {}
        if s == CONFIRM or screen.get("pageId") == CONFIRM:
            out.append(s)
            continue
        for a in shown_assets(k, s) if s in k.screens else []:
            binds = any(b.get("path") == path for b in (k.assets.get(a) or {}).get("bindings", []))
            if binds and (a in rule_targets or s in rule_targets):
                out.append(s)
                break
    return out


def _bound(c, path):
    return any(path in (n.get("bind") or {}).values() for _, n, _ in walk(c))


def check(prd, flow, expectation, screens, k, binding_values):
    findings, seen = [], set()

    def add(severity, code, **extra):
        item = {"severity": severity, "code": code, **extra}
        key = digest(item)
        if key not in seen:
            seen.add(key)
            findings.append(item)

    enumerated = enumerate_cases(expectation.get("conditions", []))
    blocked = bool(enumerated["truncated"]) or not k.complete
    for issue in flow_issues(flow, expectation, k):
        add("critical", issue["code"], **{key: v for key, v in issue.items() if key != "code"})
    rules = [r for r in k.rules.values() if r.get("required") and _approved(r)]
    notices = [r for r in expectation.get("requirements", []) if r.get("kind") == "notice"]
    designated = {n["path"]: notice_screens(flow, k, n["path"]) for n in notices}
    for n in notices:
        if not designated[n["path"]]:
            add("critical", "missing-notice", notice=n.get("id"), path=n["path"], reason="no-designated-screen")
    shown_cache = {}
    for case in enumerated["cases"]:
        run = traverse(flow, case)
        for s in run["path"]:
            c = screens.get((s, "default"))
            if c is None:
                add("critical", "missing-screen", screen=s, case=case)
                shown_cache[s] = None
                continue
            try:
                shown = visible(c, case)
            except ValueError:
                add("critical", "condition-undecidable", screen=s, case=case)
                shown_cache[s] = None
                continue
            shown_cache[s] = shown
            present = {n.get("asset") for _, n, _ in walk(shown)}
            text = _norm(text_of(shown, binding_values, k))
            spec_of = _visibility(k, s)
            for asset_id, path, value in required_texts(binding_values, k, s):
                spec = spec_of.get(asset_id)
                applies = True if spec is None else _holds(spec["when"], case)
                if applies is None:
                    add("critical", "condition-undecidable", screen=s, case=case, asset=asset_id)
                    continue
                if spec is not None and spec["negate"]:
                    applies = not applies
                if applies and _norm(value) not in text:
                    add("critical", "missing-required", screen=s, case=case, path=path, value=value)
            for cond in _conditions(k, s):
                holds = _holds(cond["when"], case)
                if holds is None:
                    add("critical", "condition-undecidable", screen=s, case=case, condition=cond.get("id"))
                    continue
                target = cond["target"]
                if cond["effect"] == "include" and holds and target not in present:
                    add("critical", "missing-conditional", screen=s, case=case, target=target, condition=cond.get("id"))
                elif (cond["effect"] == "include" and not holds or cond["effect"] == "exclude" and holds) \
                        and target in present:
                    add("critical", "forbidden-present", screen=s, case=case, target=target, condition=cond.get("id"))
            screen_assets = set(shown_assets(k, s))
            for rule in rules:
                targets = set(rule.get("targets", []))
                if s not in targets and not targets & screen_assets:
                    continue
                applies = _holds(rule.get("appliesWhen"), case)
                if applies is None:
                    add("critical", "condition-undecidable", screen=s, case=case, ruleId=rule["id"])
                    continue
                if not applies:
                    continue
                for t in sorted(targets & screen_assets):
                    spec = spec_of.get(t)
                    if spec is not None:
                        holds = _holds(spec["when"], case)
                        if holds is not None and holds == spec["negate"]:
                            continue                      # a hidden conditional target is not required here
                    if t not in present:
                        add("critical", "missing-rule-target", screen=s, case=case, ruleId=rule["id"], target=t)
        for n in notices:
            on_path = [s for s in run["path"] if s in designated[n["path"]]]
            if not on_path:
                continue
            ok = any(shown_cache.get(s) is not None and _bound(shown_cache[s], n["path"])
                     and n["path"] in binding_values
                     and _norm(binding_values[n["path"]]) in _norm(text_of(shown_cache[s], binding_values, k))
                     for s in on_path)
            if not ok:
                add("critical", "missing-notice", notice=n.get("id"), path=n["path"], case=case, screens=on_path)
    for s in flow["screens"]:
        if s not in k.screens:
            continue
        for state in states_for(s, k, flow, expectation):
            if state != "default" and (s, state) not in screens:
                add("major", "missing-state", screen=s, state=state)
    return {"findings": findings, "blocked": blocked}
