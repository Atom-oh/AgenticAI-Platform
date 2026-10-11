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
