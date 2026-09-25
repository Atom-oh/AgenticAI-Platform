"""Browser verification contract from the PRD and the flow (engine plan, Task E13; V-02 tester input).

The contract is composition-independent and frozen at flow approval (review round 7, AA4): every target is a
deterministic test id that E12 must emit, taken from `convention.Registry` (AZ1). `derive` returns exactly one
contract (review round 3, F8) in the normalized form `workspace.rules.validate_contract` produces, so
`workspace.rules.contract_hash` of it is stable. Assertions are never dropped: a design that needs more than 20 rules
or a page that needs more than 20 steps yields the critical finding `contract-capacity` and no contract.

Rule budget (N16, AL1, AJ1, X5): one rule per (screen, state) page, packed branch-outcome rules per branching screen,
packed visibility rules per page with conditional nodes, one interaction rule per interactive page, and one rule per
required published page (Y3, Z5).
"""
from __future__ import annotations

from workspace.ontology_ux import visible as _visible

from .convention import VALUE_FIELD, field_key, node_key
from .flow import expected, traverse
from .gui import states_for
from .react_project import CASE_SELECT, FINISHED_TEST_ID, FIXTURE
from .route import shown_assets

MAX_RULES, MAX_STEPS, MAX_QUOTE, MAX_VALUE = 20, 20, 2000, 4000
CRITERIA = ("projectId", "productId", "guidelineId", "guidelineAssetId", "catalogHash", "ontologyHash",
            "designSnapshotHash")


def _finding(code, message, severity="critical", **extra):
    return {"severity": severity, "code": code, "message": message, **extra}


def excerpt(quote, limit=MAX_QUOTE):
    """Citation quote cut at the last whitespace before the limit and suffixed with an ellipsis (AW2)."""
    quote = " ".join(str(quote).split())
    if len(quote) <= limit:
        return quote
    head = quote[:limit - 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip() + "…"


def _step(action, target, value=None, **extra):
    step = {"action": action, "target": target, "targetLabel": target}
    if value is not None:
        step["value"] = value
    if action == "expectText":
        step["match"] = extra.get("match", "contains")
        if "normalizeWhitespace" in extra:
            step["normalizeWhitespace"] = extra["normalizeWhitespace"]
    return step


def _cites(prd):
    """Binding path -> PRD citation, plus condition id -> citation."""
    paths, conditions = {}, {}
    for name in ("productName", "category", "term", "baseRate"):
        entry = prd.get(name)
        if isinstance(entry, dict) and entry.get("cite"):
            paths[f"product.{name}"] = entry["cite"]
    elig = prd.get("eligibility") if isinstance(prd.get("eligibility"), dict) else {}
    if isinstance(elig.get("text"), dict) and elig["text"].get("cite"):
        paths["product.eligibility"] = elig["text"]["cite"]
        if elig.get("conditionId"):
            conditions[elig["conditionId"]] = elig["text"]["cite"]
    for n, p in enumerate(prd.get("preferential") or [], 1):
        for part in ("condition", "rate"):
            if isinstance(p.get(part), dict) and p[part].get("cite"):
                paths[f"product.preferential.pref-{n}.{part}"] = p[part]["cite"]
        cite = (p.get("condition") or {}).get("cite") if isinstance(p.get("condition"), dict) else None
        if cite:
            conditions[p.get("conditionId") or p.get("id")] = cite
    for n, notice in enumerate(prd.get("notices") or [], 1):
        if isinstance(notice.get("text"), dict) and notice["text"].get("cite"):
            paths[f"product.notice.notice-{n}"] = notice["text"]["cite"]
    if "product.baseRate" in paths:
        paths["product.summaryItems"] = paths["product.baseRate"]
    return paths, conditions


def _source(cite):
    if not cite:
        return {"kind": "inferred"}
    source = {"kind": "inferred", "quote": excerpt(cite["quote"])}
    if isinstance(cite.get("page"), int) and not isinstance(cite["page"], bool) and cite["page"] >= 1:
        source["page"] = cite["page"]
    return source


def _visibility(k, screen_id):
    """Asset id -> visibleWhen spec for the screen's conditional include/exclude targets (same rule as gui.fill)."""
    out = {}
    for a in shown_assets(k, screen_id):
        for cond in (k.assets.get(a) or {}).get("conditions", []):
            if cond.get("effect") in ("include", "exclude") and cond["target"] not in out:
                out[cond["target"]] = {"when": cond["when"], "negate": cond["effect"] == "exclude"}
    return out


def _shows(spec, case):
    try:
        return _visible(spec, case)
    except ValueError:
        return None


def required_texts(prd_bindings, k, screen_id):
    """[(asset id, bound value)] a page must show: every product-source binding of its shown assets."""
    out = []
    for a in shown_assets(k, screen_id):
        for b in (k.assets.get(a) or {}).get("bindings", []):
            if b["source"] != "product" or b["path"] not in prd_bindings:
                continue
            value = prd_bindings[b["path"]]
            items = [item["value"] for item in value] if isinstance(value, list) else [value]
            out += [(a, b["path"], v) for v in items if isinstance(v, str) and v.strip()]
    return out


def coverage_texts(prd_bindings, k, flow, published_pages=()):
    """The coverage requirement set of expected texts (the union a derived contract must assert)."""
    values = {" ".join(v.split()) for s in flow["screens"] for _, _, v in required_texts(prd_bindings, k, s)}
    values |= {" ".join(p["content"].split()) for p in published_pages or () if p.get("required")}
    return values


def _controls(k, screen_id):
    """Controlled inputs of a screen in shown-asset order: (asset id, adapter, export, required)."""
    out = []
    for a in shown_assets(k, screen_id):
        code = (k.assets.get(a) or {}).get("code") or {}
        adapter = code.get("adapter")
        if adapter in VALUE_FIELD:
            field = VALUE_FIELD[adapter]
            required = any(b["field"] == field and b["required"] for b in k.assets[a].get("bindings", []))
            out.append((a, adapter, code.get("exportName"), required))
    return out


def _has_action(k, screen_id):
    return any(((k.assets.get(a) or {}).get("code") or {}).get("adapter") == "action" for a in shown_assets(k, screen_id))


def _pack(prefix, title, source, sequences, rules):
    """Append step sequences into as few rules as possible (<= 20 steps each); nothing is dropped."""
    current = []
    for sequence in sequences:
        if current and len(current) + len(sequence) > MAX_STEPS:
            rules.append({"id": f"{prefix}-{len([r for r in rules if r['id'].startswith(prefix + '-')]) + 1}",
                          "title": title, "required": True, "source": source, "steps": current})
            current = []
        current = current + sequence
    if current:
        rules.append({"id": f"{prefix}-{len([r for r in rules if r['id'].startswith(prefix + '-')]) + 1}",
                      "title": title, "required": True, "source": source, "steps": current})


def derive(prd, flow, k, registry, published_pages, *, cases, criteria, viewport=None, prd_bindings=None):
    """-> {"contract": normalized contract | None, "findings": [...]}. One contract per run, or none."""
    from .prd_extract import bindings as _bindings
    viewport = dict(viewport or {"width": 390, "height": 844})
    values = prd_bindings if prd_bindings is not None else _bindings(prd)
    cites, condition_cites = _cites(prd)
    published_pages = [p for p in (published_pages or []) if p.get("required")]
    expectation = expected(prd, k, published_pages)
    findings, rules = [], []
    if not cases:
        return {"contract": None, "findings": [_finding("contract-capacity", "no cases to verify")]}
    registry.register_flow(flow)
    runs = [traverse(flow, case) for case in cases]
    product_cite = cites.get("product.productName")
    screens = flow["screens"]
    for t in flow["transitions"]:
        if t.get("navigation", "forward") != "forward":
            findings.append(_finding("contract-unsupported", "back/cancel edges need a designated action target",
                                     transition=t["id"]))

    def reachable(screen):
        return [i for i, run in enumerate(runs) if screen in run["path"]]

    def select(i, screen, state="default", empty=False):
        return _step("select", CASE_SELECT, f"{i}:{screen}:{state}" + (":empty" if empty else ""))

    def successor(i, screen):
        """The screen `next` selects from `screen` in case i (the harness may start any case anywhere)."""
        moves = [t for t in flow["transitions"] if t.get("navigation", "forward") == "forward" and t["src"] == screen]
        try:
            enabled = [t for t in moves if _shows({"when": t["when"], "negate": False}, cases[i]) is True]
        except ValueError:
            return None
        return enabled[0]["dst"] if len(enabled) == 1 else None

    def state_case(screen, state, candidates):
        if state == "default":
            return candidates[0]
        for i in candidates:
            for a in shown_assets(k, screen):
                for cond in (k.assets.get(a) or {}).get("conditions", []):
                    if cond.get("effect") == "state" and cond.get("state") == state \
                            and _shows({"when": cond["when"], "negate": False}, cases[i]) is True:
                        return i
        return candidates[0]

    pages = [(s, st) for s in screens for st in states_for(s, k, flow, expectation)]
    # 1. one rule per (screen, state) page
    for screen, state in pages:
        candidates = reachable(screen) or list(range(len(cases)))
        i = state_case(screen, state, candidates)
        ids = registry.test_ids(screen)
        page_id = registry.state_page_id(screen, state)
        visibility = _visibility(k, screen)
        steps = [select(i, screen, state), _step("expectVisible", page_id, True)]
        texts = required_texts(values, k, screen)
        extra = []
        for asset_id, _, value in texts:
            target = ids["nodes"][node_key(asset_id)]
            spec = visibility.get(asset_id)
            if spec is None or _shows(spec, cases[i]) is True:
                steps.append(_step("expectText", target, value))
                continue
            j = next((j for j in range(len(cases)) if _shows(spec, cases[j]) is True), None)
            if j is None:
                findings.append(_finding("visibility-unverifiable", "no case shows a required text", asset=asset_id))
                continue
            extra += [select(j, screen, state), _step("expectText", target, value)]
        steps += extra
        if state == "default" and _has_action(k, screen):
            if screen in flow["terminals"]:
                steps += [_step("click", ids["cta"]), _step("expectVisible", FINISHED_TEST_ID, True)]
            else:
                dst = successor(i, screen)
                if dst is None:
                    findings.append(_finding("contract-unsupported", "no single successor for the page action",
                                             screen=screen))
                else:
                    steps += [_step("click", ids["cta"]), _step("expectVisible", registry.page_id(dst), True)]
        # A notice on the page is the binding citation of record; otherwise the first required text's.
        paths = [p for _, p, _ in texts if p.startswith("product.notice.")] + [p for _, p, _ in texts]
        cite = cites.get(paths[0]) if paths else product_cite
        rid = f"page-{registry.page_key(screen)}" + ("" if state == "default" else f"-{state}")
        if len(steps) > MAX_STEPS:
            findings.append(_finding("contract-capacity", "a page needs more than 20 steps", page=page_id))
        rules.append({"id": rid, "title": f"{k.screens[screen]['title']} 화면 확인 ({state})", "required": True,
                      "source": _source(cite), "steps": steps})
    # 2. packed branch outcomes: every (conditional transition, case taking it) through the generic action
    for screen in screens:
        moves = [t for t in flow["transitions"] if t["src"] == screen and t.get("navigation", "forward") == "forward"
                 and t.get("when")]
        if not moves:
            continue
        ids, sequences, cite = registry.test_ids(screen), [], None
        for t in moves:
            for i, run in enumerate(runs):
                if t["id"] in run["transitions"]:
                    sequences.append([select(i, screen), _step("click", ids["cta"]),
                                      _step("expectVisible", registry.page_id(t["dst"]), True)])
            cite = cite or next((condition_cites[c] for c in _terms(t["when"]) if c in condition_cites), None)
        _pack(f"branch-{registry.page_key(screen)}", f"{k.screens[screen]['title']} 분기 결과", _source(cite or product_cite),
              sequences, rules)
    # 3. packed visibility outcomes per page: one case where the expression holds, one where it does not
    for screen, state in pages:
        ids, sequences, cite = registry.test_ids(screen), [], None
        for asset_id, spec in _visibility(k, screen).items():
            target = ids["nodes"][node_key(asset_id)]
            shown = next((i for i in range(len(cases)) if _shows(spec, cases[i]) is True), None)
            hidden = next((i for i in range(len(cases)) if _shows(spec, cases[i]) is False), None)
            if shown is None or hidden is None:
                findings.append(_finding("visibility-unverifiable", "both visibility outcomes need a case",
                                         asset=asset_id, screen=screen))
                continue
            sequences.append([select(shown, screen, state), _step("expectVisible", target, True),
                              select(hidden, screen, state), _step("expectVisible", target, False)])
            cite = cite or next((condition_cites[c] for c in _terms(spec["when"]) if c in condition_cites), None)
        if sequences:
            suffix = "" if state == "default" else f"-{state}"
            _pack(f"visible-{registry.page_key(screen)}{suffix}", f"{k.screens[screen]['title']} 조건부 표시",
                  _source(cite or product_cite), sequences, rules)
    # 4. one interaction rule per interactive page, starting with no prefilled form state
    for screen in screens:
        controls = _controls(k, screen)
        if not controls:
            continue
        for a, _, _, _ in controls:
            props = (((k.assets.get(a) or {}).get("code") or {}).get("props") or {})
            if "min" in props or "max" in props:
                findings.append(_finding("contract-unsupported", "min/max invalid-input checks are not derived yet",
                                         asset=a))
        candidates = reachable(screen) or list(range(len(cases)))
        i = candidates[0]
        ids, visibility = registry.test_ids(screen), _visibility(k, screen)
        steps, checks = [select(i, screen, empty=True)], []
        guarded = any(required and (visibility.get(a) is None or _shows(visibility[a], cases[i]) is True)
                      for a, _, _, required in controls)
        if guarded and _has_action(k, screen):
            steps.append(_step("expectEnabled", ids["cta"], False))
        seen = {}
        for a, adapter, export, _ in controls:
            seen[a] = seen.get(a, 0) + 1
            spec = visibility.get(a)
            if spec is not None and _shows(spec, cases[i]) is not True:
                continue
            target = registry.field_id(screen, field_key(k, a, seen[a]))
            if adapter == "controlled-text":
                steps.append(_step("fill", target, FIXTURE[adapter]))
                checks.append(_step("expectValue", target, FIXTURE[adapter]))
            elif adapter == "controlled-bool":
                steps.append(_step("check", target, True))
                checks.append(_step("expectChecked", target, True))
            elif export == "RadioGroup":
                option, other = f"{target}-{FIXTURE[adapter]}", f"{target}-o1"
                steps.append(_step("check", option, True))
                checks += [_step("expectChecked", option, True), _step("expectChecked", other, False)]
            else:
                steps.append(_step("select", target, FIXTURE[adapter]))
                checks.append(_step("expectValue", target, FIXTURE[adapter]))
        steps += checks
        if _has_action(k, screen):
            steps += [_step("expectEnabled", ids["cta"], True), _step("click", ids["cta"])]
            if screen in flow["terminals"]:
                steps.append(_step("expectVisible", FINISHED_TEST_ID, True))
            elif successor(i, screen) is None:
                findings.append(_finding("contract-unsupported", "no single successor for the page action",
                                         screen=screen))
            else:
                steps.append(_step("expectVisible", registry.page_id(successor(i, screen)), True))
        if len(steps) > MAX_STEPS:
            findings.append(_finding("contract-capacity", "an interaction rule needs more than 20 steps", screen=screen))
        rules.append({"id": f"input-{registry.page_key(screen)}", "title": f"{k.screens[screen]['title']} 입력 동작",
                      "required": True, "source": _source(product_cite), "steps": steps})
    # 5. one rule per required published page: its complete normalized content in one step (Z5)
    for page in published_pages:
        match = [s for s in screens if k.screens[s].get("pageId") == page["pageId"]
                 and registry.page_id(s) == page["pageId"]]
        if not match:
            findings.append(_finding("published-page-missing", "no procedure Screen carries the published pageId",
                                     pageId=page["pageId"]))
            continue
        screen = match[0]
        content = " ".join(page["content"].split())
        if len(content) > MAX_VALUE:
            findings.append(_finding("contract-capacity", "published content exceeds the step value limit",
                                     pageId=page["pageId"]))
        candidates = reachable(screen) or list(range(len(cases)))
        rules.append({"id": f"notice-{registry.page_key(screen)}", "title": f"필수 안내 {page['pageId']} 전체 문구",
                      "required": True, "source": {"kind": "inferred", "quote": excerpt(content)},
                      "steps": [select(candidates[0], screen), _step("expectText", page["pageId"], content,
                                                                     normalizeWhitespace=True)]})
    if len(rules) > MAX_RULES:
        findings.append(_finding("contract-capacity", "the design needs more than 20 rules; split the procedure",
                                 rules=len(rules)))
    if any(f["severity"] == "critical" for f in findings):
        return {"contract": None, "findings": findings, "ruleCount": len(rules)}
    contract = {"schemaVersion": 1, "title": f"{k.procedures.get(flow['procedureId'], {}).get('title', '설계')} 동작 검증",
                "brief": "", "assetIds": [], "viewport": viewport, "rules": rules, "unresolved": [], "bindings": {}}
    for key in CRITERIA:
        if criteria and criteria.get(key) is not None:
            contract[key] = criteria[key]
    if contract.get("guidelineAssetId"):
        contract["assetIds"] = [contract["guidelineAssetId"]]
    return {"contract": contract, "findings": findings, "ruleCount": len(rules)}


def _terms(when):
    return [term.strip().lstrip("!")[5:] for term in (when or "").split("&") if term.strip()]


def missing_targets(contract, screens, k, registry):
    """`missing-contract-target` for every contract target no composition emits (E10 presence check)."""
    emitted = {CASE_SELECT, FINISHED_TEST_ID}
    for (screen, state), composition in screens.items():
        emitted.add(registry.state_page_id(screen, state))
        ids = registry.test_ids(screen, composition)
        emitted.update(ids["byNode"].values())
        for _, node in _walk(composition):
            if node["id"] in ids["fieldOf"]:
                options = (node.get("props") or {}).get("options")
                if isinstance(options, list):
                    emitted.update(f"{ids['byNode'][node['id']]}-o{j}" for j in range(1, len(options) + 1))
    out = []
    for rule in (contract or {}).get("rules", []):
        for index, step in enumerate(rule["steps"]):
            if step["target"] not in emitted:
                out.append(_finding("missing-contract-target", "no composition emits this contract target",
                                    rule=rule["id"], step=index, target=step["target"]))
    return out


def _walk(c):
    from .composition import walk
    for path, node, _ in walk(c):
        yield path, node
