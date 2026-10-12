"""Pure, serializable design stages used by the durable application workflow."""
from __future__ import annotations

import copy

from design_loop import composition, contract, coverage, flow, gui, prd_extract, react_project, route
from design_loop.convention import Registry, Convention
from design_loop.knowledge import from_snapshot
from workspace import ontology_schema as schema
from workspace.rules import contract_hash, validate_contract


def knowledge(snapshot):
    result = from_snapshot(snapshot)
    if not result.complete:
        raise ValueError("knowledge-incomplete")
    return result


def prepare_flow(snapshot, procedure_id, prd, *, catalog_hash, registry_state=None, published_pages=(), document_version=1, design_id=None):
    k = knowledge(snapshot)
    proposed = flow.build_flow(k, procedure_id)
    expectation = flow.expected(prd, k, published_pages)
    issues = flow.flow_issues(proposed, expectation, k)
    cases = flow.enumerate_cases(expectation["conditions"])
    if cases["truncated"]:
        issues.append({"code": "case-capacity"})
    convention = {"schemaVersion": 1, "screenIdPattern": "^(?P<area>[a-z]{3})-(?P<num>[0-9]{4})$", "area": "app",
        "pathTemplate": "{area}/{screenId}{suffix}.tsx", "statePathTemplate": "{area}/{screenId}{suffix}.{state}.tsx",
        "surfaceSuffix": {"page": "", "bottom-sheet": "_B01", "full-popup": "_F01", "layer": "_L01", "tab": "_T01"},
        "typeCodes": {"page": "P", "bottom-sheet": "B", "full-popup": "F", "layer": "L", "tab": "T"},
        "meta": ["sid", "type", "dver", "status", "level1", "level2", "level3"]}
    registry = Registry(k, state=registry_state, convention=Convention(convention)).register_flow(proposed)
    for screen in proposed["screens"]:
        registry.assign(screen)
    states = {screen: gui.states_for(screen, k, proposed, expectation) for screen in proposed["screens"]}
    result = contract.derive(prd, proposed, k, registry, published_pages,
        cases=cases["cases"], criteria={"catalogHash": catalog_hash})
    issues.extend(result["findings"])
    if design_id is not None and result["contract"]:
        result["contract"].update(designId=design_id, projectId=snapshot["projectId"], designSnapshotHash=schema.digest(snapshot))
    checked = validate_contract(result["contract"]) if result["contract"] else None
    return {"flow": proposed, "expectation": expectation, "cases": cases["cases"], "states": states,
            "registry": registry.to_json(), "convention": convention, "documentVersion": str(document_version), "contract": checked,
            "contractHash": contract_hash(checked) if checked else None, "issues": issues,
            "routes": {screen: route.classify(k, proposed, screen) for screen in proposed["screens"]}}


def packed(screens):
    return [{"screenId": screen, "state": state, "composition": copy.deepcopy(value)}
            for (screen, state), value in sorted(screens.items())]


def unpacked(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 19:
        raise ValueError("composition-capacity")
    result = {}
    for row in rows:
        schema._fields(row, {"screenId", "state", "composition"})
        key = row["screenId"], row["state"]
        if key in result:
            raise ValueError("duplicate-composition")
        result[key] = copy.deepcopy(row["composition"])
    return result


def generate(snapshot, prepared, prd, deps, *, variant=0, max_rounds=3):
    k = knowledge(snapshot)
    values = prd_extract.bindings(prd)
    screens, histories, modes = {}, [], []
    for screen in prepared["flow"]["screens"]:
        strategy = route.strategy(route.classify(k, prepared["flow"], screen)["route"])
        modes.append(strategy["mode"])
        generated = gui.generate_screen(screen, k, prepared["flow"], deps, strategy=strategy,
            binding_paths=values, variants=1, variant_offset=variant, retries=min(max_rounds - 1, gui.MAX_RETRIES))
        if generated.get("blocked") or not generated.get("variants"):
            raise ValueError(generated.get("blocked", "composition-missing"))
        choices = generated["variants"]
        selected = choices[min(variant, len(choices) - 1)]
        if not selected.get("composition"):
            raise ValueError("composition-invalid")
        base = selected["composition"]
        screens[screen, "default"] = base
        states = gui.generate_states(base, prepared["states"][screen], k, deps, strategy=strategy,
            flow=prepared["flow"], binding_paths=values, retries=min(max_rounds - 1, gui.MAX_RETRIES))
        if states.get("blocked"):
            raise ValueError(states["blocked"])
        for item in states.get("states", []):
            if not item.get("composition"):
                raise ValueError("composition-invalid")
            screens[screen, item["state"]] = item["composition"]
        histories.append({"screenId": screen, "mode": strategy["mode"], "generation": generated, "states": states})
    registry = Registry(k, state=prepared["registry"]).register_flow(prepared["flow"])
    findings = contract.missing_targets(prepared["contract"], screens, k, registry)
    findings += coverage.check(prd, prepared["flow"], prepared["expectation"], screens, k, values)["findings"]
    if any(item["severity"] == "critical" for item in findings):
        raise ValueError("design-coverage-failed")
    files = source_files(snapshot, prepared, prd, screens, registry.to_json())
    return {"screens": packed(screens), "files": files, "registry": registry.to_json(),
            "history": histories, "mode": "fill" if set(modes) == {"fill"} else "compose",
            "compositionHashes": [composition.digest(value) for value in screens.values()]}


def source_files(snapshot, prepared, prd, screens, registry_state):
    k = knowledge(snapshot)
    registry = Registry(k, state=registry_state, convention=Convention(prepared["convention"]))
    metadata = {(screen, state): {"sid": registry.sid_for(screen),
        "type": prepared["convention"]["typeCodes"][composition.get("surface", "page")],
        "dver": prepared["documentVersion"], "status": "draft",
        "level1": k.procedures[prepared["flow"]["procedureId"]]["title"],
        "level2": k.screens[screen]["title"], "level3": state} for (screen, state), composition in screens.items()}
    return react_project.project(prepared["flow"], screens, k, prd_extract.bindings(prd),
                                 cases=prepared["cases"], registry=registry, meta=metadata)


class RepairRefused(ValueError):
    """Bounded non-content evidence for a rejected model repair."""
    def __init__(self, code, text=None, findings=()):
        import hashlib
        super().__init__(code)
        raw = text.encode() if isinstance(text, str) else b""
        self.evidence = {"code": code, "responseHash": hashlib.sha256(raw).hexdigest(), "responseBytes": len(raw),
                         "findings": [{key: item[key] for key in ("code", "severity", "path") if key in item}
                                      for item in findings[:100]]}


def repair(snapshot, prepared, prd, previous, findings, deps):
    """One feedback-driven repair of the permitted compositions under frozen criteria."""
    from design_loop.model_call import call
    import json
    k = knowledge(snapshot)
    values = prd_extract.bindings(prd)
    screens = unpacked(previous["screens"])
    addressed = {item.get("screen") or item.get("screenId") for item in findings}
    targets = [(screen, state) for screen, state in screens if screen in addressed] if addressed and None not in addressed else list(screens)
    if not targets:
        targets = list(screens)
    requests = []
    for screen, state in targets:
        base = screens[screen, state]
        requests.append({"screenId": screen, "state": state, "base": base,
                         "catalog": gui._catalog(k, screen, prepared["flow"], values)})
    feedback = [{key: item[key] for key in ("role", "severity", "code", "message", "path", "item", "screen", "state")
                 if key in item} for item in findings][:100]
    request = {"task": "repair-project", "targets": requests, "previousFindings": feedback}
    system = (gui._SYSTEM + " Repair every requested screen/state under its catalog and frozen criteria. "
              "Keep each screenId, state and variant. Reply with exactly {\"screens\": ["
              "{\"screenId\": \"...\", \"state\": \"...\", \"composition\": COMPOSITION}]} for the requested targets only.")
    text, blocked = call(deps, system, json.dumps(request, ensure_ascii=False, sort_keys=True))
    if blocked:
        raise RepairRefused(blocked)
    try:
        payload = json.loads(text)
        schema._fields(payload, {"screens"})
        replacements = unpacked(payload["screens"])
    except (ValueError, TypeError, KeyError):
        raise RepairRefused("design-repair-invalid", text) from None
    if set(replacements) != set(targets):
        raise RepairRefused("design-repair-targets", text)
    for key, candidate in replacements.items():
        if candidate.get("screenId") != key[0] or candidate.get("state") != key[1] or candidate.get("variant") != screens[key]["variant"]:
            raise RepairRefused("design-repair-page-changed", text)
        checked = composition.validate(candidate, k, flow=prepared["flow"], binding_paths=frozenset(values), mode=previous["mode"])
        if any(finding["severity"] == "critical" for finding in checked):
            raise RepairRefused("design-repair-invalid", text, checked)
    screens.update(replacements)
    output = copy.deepcopy(previous)
    output.update(screens=packed(screens),
                  files=source_files(snapshot, prepared, prd, screens, previous["registry"]),
                  compositionHashes=[composition.digest(value) for value in screens.values()])
    output.setdefault("repairs", []).append({"findings": copy.deepcopy(findings), "targets": copy.deepcopy(targets)})
    return output


def validation_plan(snapshot, prepared, prd, max_rounds, *, stage="generate"):
    """Conservative service/model call ceiling before admitting paid validation work."""
    from design_loop import verify_graph
    from design_loop.rules_v2 import RULES
    k = knowledge(snapshot)
    frames = sum(len(set(["default", *prepared["states"][screen]])) for screen in prepared["flow"]["screens"])
    if not 1 <= frames <= 19 or not prepared["cases"] or len(prepared["cases"]) > 64:
        raise ValueError("design-validation-scope")
    checklist = verify_graph.design_checklist(prd, k)
    judged = [item for item in checklist if item.get("method") != "rule"
              or (item.get("rule") or {}).get("fn") not in RULES]
    reviewer_max = frames * len(prepared["cases"]) if judged else 0
    structured = all(value.get("mode") == "fill" for value in (
        route.strategy(route.classify(k, prepared["flow"], screen)["route"]) for screen in prepared["flow"]["screens"]))
    if structured:
        generated = generate(snapshot, prepared, prd, {}, max_rounds=1)
        bundle = {"prd": prd, "flow": prepared["flow"], "expectation": prepared["expectation"],
                  "screens": unpacked(generated["screens"]), "binding_values": prd_extract.bindings(prd),
                  "registry": Registry(k, state=generated["registry"])}
        review, incomplete = verify_graph.review_plan(bundle, k, judged)
        if incomplete:
            raise ValueError("design-validation-scope")
        initial = len(review) + (2 + len(prepared["cases"]) if stage == "edit" else 0)
    else:
        # Generation's bounded composition attempts; scoped edit has one parse retry.
        initial = (2 if stage == "edit" else frames * min(max_rounds, gui.MAX_RETRIES + 1)) + reviewer_max
    repair_calls = 2 if stage == "edit" else 1  # one bounded project repair batch
    if stage == "edit":
        reviewer_max = len(prepared["cases"]) if judged else 0
    models = initial + (max_rounds - 1) * (repair_calls + reviewer_max)
    return {"schemaVersion": 1, "rounds": max_rounds, "frames": frames, "modelCalls": models,
            "serviceCalls": 3 * max_rounds, "totalCalls": models + 3 * max_rounds}


def selected_screens(rows, flow_screens):
    """One exact existing artifact for every screen; its required states travel together."""
    if not isinstance(rows, list) or len(rows) != len(flow_screens) or not 1 <= len(rows) <= 19:
        raise ValueError("design-selection-incomplete")
    selected = {}
    for row in rows:
        schema._fields(row, {"screenId", "artifactId"})
        if (row["screenId"] not in flow_screens or row["screenId"] in selected
                or not isinstance(row["artifactId"], str)):
            raise ValueError("design-selection-invalid")
        schema._identifier(row["artifactId"])
        selected[row["screenId"]] = row["artifactId"]
    return [{"screenId": screen, "artifactId": selected[screen]} for screen in flow_screens]


def compose(snapshot, prepared, prd, selected, deps, verify_lineage):
    """Reassemble exact selected screens and reuse every applicable reviewed context.

    No generator is used. Missing states, changed review profiles, incomplete cases
    or unavailable judgments require fresh generation rather than inferred approval.
    """
    from design_loop import rules_v2, verify_graph
    k = knowledge(snapshot)
    selected_screens([{key: row[key] for key in ("screenId", "artifactId")} for row in selected],
                     prepared["flow"]["screens"])
    screens, origins, histories = {}, {}, []
    profile = {key: deps[key] for key in ("model", "profileHash")}
    modes = set()
    for row in selected:
        result = row["result"]
        if result.get("reviewProfile") != profile:
            raise ValueError("design-compose-review-required")
        source = result["generated"]
        if source["registry"] != prepared["registry"]:
            raise ValueError("design-compose-registry-changed")
        pages = unpacked(source["screens"])
        screen = row["screenId"]
        required = {"default", *prepared["states"][screen]}
        states = {state for sid, state in pages if sid == screen}
        if states != required:
            raise ValueError("design-compose-states-required")
        for state in sorted(states):
            key = screen, state
            page = pages[key]
            if page.get("screenId") != screen or page.get("state") != state:
                raise ValueError("design-compose-page-mismatch")
            screens[key] = copy.deepcopy(page)
            origins[key] = result
        modes.add(source["mode"])
        histories.append({"screenId": screen, "artifactId": row["artifactId"],
                          "generation": copy.deepcopy([item for item in source["history"] if item["screenId"] == screen])})
    mode = "fill" if modes == {"fill"} else "compose"
    bundle = {"prd": prd, "flow": prepared["flow"], "expectation": prepared["expectation"],
              "screens": screens, "binding_values": prd_extract.bindings(prd), "contract": prepared["contract"],
              "registry": Registry(k, state=prepared["registry"])}
    checklist = verify_graph.design_checklist(prd, k)
    if not checklist:
        raise ValueError("design-compose-review-required")
    judged = [item for item in checklist if item.get("method") != "rule"
              or (item.get("rule") or {}).get("fn") not in rules_v2.RULES]
    judgments, evidence = {}, {}
    for (screen, state), page in sorted(screens.items()):
        plan, incomplete = verify_graph.review_plan(bundle, k, judged, pages={(screen, state)})
        if incomplete:
            raise ValueError("design-compose-review-required")
        used = []
        if plan:
            stored = origins[screen, state].get("judgments", {})
            reused = verify_graph.compose_review(stored, bundle, k, deps, screen, state, judged,
                                                 verify_lineage=verify_lineage)
            if reused["status"] != "reused":
                raise ValueError("design-compose-review-required")
            for _, key in verify_graph.page_review_keys(bundle, k, deps, screen, state, judged):
                record = stored[key]
                if key in judgments and judgments[key] != record:
                    raise ValueError("design-compose-review-conflict")
                judgments[key] = copy.deepcopy(record)
                used.append(record["receiptHash"])
        elif flow.enumerate_cases(prepared["expectation"]["conditions"])["truncated"]:
            raise ValueError("design-compose-review-required")
        # Rule-only pages still bind the full frozen checklist and exact composition.
        digest = composition.digest(page)
        evidence[digest] = schema.digest({"composition": digest, "checklist": checklist,
                                         "contractHash": prepared["contractHash"], "judgments": sorted(used)})
    generated = {"screens": packed(screens),
                 "files": source_files(snapshot, prepared, prd, screens, prepared["registry"]),
                 "registry": copy.deepcopy(prepared["registry"]), "history": histories, "mode": mode,
                 "compositionHashes": [composition.digest(value) for value in screens.values()]}
    return generated, judgments, evidence


def edit_layout(manifest, report):
    from design_loop.edit import layout_stable, subtree_testids
    base = manifest["baseResult"]["generated"]
    k = knowledge(manifest["snapshot"])
    target = manifest["edit"]
    key = target["screenId"], target["state"]
    before = unpacked(base["screens"])[key]
    excluded = subtree_testids(before, target["targetId"], Registry(k, state=base["registry"]))
    return layout_stable(manifest["baseBrowser"]["layoutBoxes"], report.get("layoutBoxes"),
                         excluded_testids=excluded, tolerance_px=1)
