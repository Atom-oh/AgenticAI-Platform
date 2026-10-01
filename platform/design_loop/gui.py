"""Strategy-driven GUI generation: fill, adapt, compose; layout and state variants (engine plan, Task E11; G-02, G-03).

`fill` is deterministic: template slots in order, each screen asset in the first slot whose allow-list names it,
nested COMPOSES children, required props bound from the asset's `dataBindings`, copy from the asset title (never a
financial value) and the generic `next` action (`finish` on terminal screens). Conditional include/exclude targets
carry the `visibleWhen` derived from their condition (AJ1).

`generate_screen` requires the E9 strategy. Structured screens get layout variants from the kit's declared
variation axes with no model call. Hybrid (`adapt`) and unstructured (`compose`) screens call the injected model;
every attempt is validated with `composition.validate` and kept (GEN-05). A missing model or normalization hook is
an explicit blocked outcome, never a pass, and incomplete knowledge never generates.
"""
from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

from workspace.ontology_ux import STATES, visible

from . import composition as comp
from .flow import enumerate_cases, traverse
from .model_call import call
from .route import classify, shown_assets
from .route import strategy as route_strategy

KIT_CATALOG = Path(__file__).resolve().parents[1] / "react-kit" / "catalog.json"
LETTERS = "abcdefgh"
MAX_VARIANTS, MAX_RETRIES = len(LETTERS), 3
_SYSTEM = (
    "You design one mobile banking screen as a composition tree. Reply with JSON only: one composition object "
    '{"schemaVersion": 1, "screenId", "templateId", "state", "variant", "surface", "slots": {slot: [NODE]}} where '
    'NODE = {"id", "asset", "props"?, "bind"?, "on"?, "visibleWhen"?, "children"?}. Use only the listed assets, '
    "template slots, binding paths and transition actions. Never write a financial number as literal text: bind it. "
    "Callback props are wired by the platform and must not appear."
)


def kit_axes(path=KIT_CATALOG):
    """`{exportName: [prop, ...]}` from the pinned kit catalog's declared `variationAxes`."""
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    return {c["name"]: list(c.get("variationAxes") or []) for c in catalog.get("components", [])}


def _require_complete(k):
    if not k.complete:
        raise ValueError("knowledge-incomplete")


def _visibility(conditions, asset_id):
    for cond in conditions:
        if cond.get("target") == asset_id and cond.get("effect") in ("include", "exclude"):
            return {"when": cond["when"], "negate": cond["effect"] == "exclude"}
    return None


def _screen_conditions(k, screen_id):
    out = []
    for a in shown_assets(k, screen_id):
        out += [c for c in (k.assets.get(a) or {}).get("conditions", []) if c.get("effect") in ("include", "exclude")]
    return out


def fill(k, flow, screen_id, binding_values):
    """Deterministic composition for one screen (structured route; also the `adapt` base)."""
    _require_complete(k)
    screen = k.screens.get(screen_id)
    if screen is None or screen_id not in flow["screens"]:
        raise ValueError("Unknown flow screen")
    template = k.templates.get(screen.get("templateId"))
    if template is None:
        raise ValueError("fill needs the screen's template")
    terminal = screen_id in flow["terminals"]
    conditions = _screen_conditions(k, screen_id)
    counter = itertools.count(1)
    placed = set()

    def build(asset_id, ancestors):
        asset = k.assets.get(asset_id)
        if asset is None:
            raise ValueError(f"Unknown asset {asset_id}")
        placed.add(asset_id)
        node = {"id": f"n{next(counter)}", "asset": asset_id}
        code = asset.get("code") or {}
        specs = code.get("props", {})
        managed = comp.ADAPTER_PROPS.get(code.get("adapter"), set())
        text_child = code.get("childrenProp") == "children"
        props, bind = {}, {}
        for b in asset.get("bindings", []):
            field = b["field"]
            if field in managed or b["path"] not in binding_values:
                continue                                   # adapter-owned fields are wired in codegen (E12)
            if field in specs and specs[field]["type"] != "callback" or field == "children" and text_child:
                bind[field] = b["path"]
        for name, spec in specs.items():
            if spec["required"] and spec["type"] == "string" and name not in managed and name not in bind:
                props[name] = asset["title"]
        if code.get("adapter") == "action":
            node["on"] = {"click": "finish" if terminal else "next"}
        children = [build(c, ancestors | {asset_id}) for c in asset.get("composes", []) if c not in ancestors | {asset_id}]
        if text_child and not children and "children" not in bind:
            props["children"] = asset["title"]
        if props:
            node["props"] = props
        if bind:
            node["bind"] = bind
        spec = _visibility(conditions, asset_id)
        if spec is not None:
            node["visibleWhen"] = spec
        if children:
            node["children"] = children
        return node

    slots = {name: [] for name in template["slots"]}

    def place(asset_id):
        slot = next((name for name, s in template["slots"].items() if asset_id in s.get("allowed", [])), None)
        if slot is None:
            raise ValueError(f"asset {asset_id} fits no template slot")
        slots[slot].append(build(asset_id, frozenset()))

    for asset_id in screen["assets"]:
        place(asset_id)
    for cond in conditions:                                # conditional targets the owners do not compose
        if cond["target"] not in placed:
            place(cond["target"])
    return {"schemaVersion": comp.SCHEMA_VERSION, "screenId": screen_id, "templateId": template["id"],
            "state": "default", "variant": "a", "surface": "page", "slots": slots}


def _axis_variant(base, k, letter, index, axes):
    c = copy.deepcopy(base)
    c["variant"] = letter
    for _, node, _ in comp.walk(c):
        code = (k.assets.get(node["asset"]) or {}).get("code") or {}
        for axis in axes.get(code.get("exportName"), []):
            spec = code.get("props", {}).get(axis)
            if spec and spec["type"] == "enum" and spec.get("values") and axis not in (node.get("bind") or {}):
                node.setdefault("props", {})[axis] = spec["values"][(index - 1) % len(spec["values"])]
    return c


def _layout_key(c):
    return comp.digest({**c, "variant": "-"})


def _parse(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _critical(findings):
    return [f for f in findings if f["severity"] == "critical"]


def _catalog(k, screen_id, flow, binding_paths):
    screen = k.screens[screen_id]
    templates = [screen["templateId"]] if screen.get("templateId") else sorted(k.templates)
    allowed = set(shown_assets(k, screen_id))
    for tid in templates:
        for slot in (k.templates.get(tid) or {}).get("slots", {}).values():
            allowed.update(slot.get("allowed", []))
    stack = list(allowed)
    while stack:                                           # nested children an allowed asset may compose
        for child in (k.assets.get(stack.pop()) or {}).get("composes", []):
            if child not in allowed:
                allowed.add(child)
                stack.append(child)
    assets = []
    for a in sorted(allowed):
        asset = k.assets.get(a)
        if asset is None:
            continue
        code = asset.get("code") or {}
        assets.append({"id": a, "level": asset["level"], "title": asset["title"], "intent": asset.get("intent"),
                       "composes": asset.get("composes", []), "adapter": code.get("adapter"),
                       "props": {n: {key: v for key, v in s.items() if key != "required"} | {"required": s["required"]}
                                 for n, s in code.get("props", {}).items()},
                       "conditions": asset.get("conditions", [])})
    return {"screen": {"id": screen_id, "title": screen["title"], "assets": screen["assets"],
                       "terminal": screen_id in flow["terminals"]},
            "templates": [{"id": t, "slots": k.templates[t]["slots"]} for t in templates if t in k.templates],
            "assets": assets, "bindingPaths": sorted(binding_paths),
            "actions": (["finish"] if screen_id in flow["terminals"] else ["next"])
            + [t["id"] for t in flow["transitions"] if t["src"] == screen_id]}


def _model_loop(deps, user_base, letter, validate_one, retries):
    """Run one model-produced variant with bounded retries; every attempt is kept."""
    attempts, findings = [], None
    for number in range(1, retries + 2):
        user = dict(user_base, variant=letter)
        if findings:
            user["previousFindings"] = findings
        text, blocked = call(deps, _SYSTEM, json.dumps(user, ensure_ascii=False, sort_keys=True))
        if blocked:
            attempts.append({"attempt": number, "composition": None, "findings": [], "blocked": blocked})
            return {"variant": letter, "composition": None, "findings": [], "attempts": attempts, "blocked": blocked}
        proposal = _parse(text)
        if proposal is None:
            findings = [comp._finding("parse-failed", "", "the model reply is not one JSON object")]
        else:
            proposal["variant"] = letter                  # engine-owned metadata
            findings = validate_one(proposal)
        attempts.append({"attempt": number, "composition": proposal, "findings": findings})
        if proposal is not None and not _critical(findings):
            return {"variant": letter, "composition": proposal, "findings": findings, "attempts": attempts}
    return {"variant": letter, "composition": None, "findings": findings, "attempts": attempts}


def generate_screen(screen_id, k, flow, deps, *, strategy, binding_paths, variants=3, retries=1, axes=None):
    if not isinstance(strategy, dict) or strategy.get("mode") not in comp.MODES:
        raise ValueError("A generation strategy from route.strategy is required")
    if not 1 <= variants <= MAX_VARIANTS or not 0 <= retries <= MAX_RETRIES:
        raise ValueError("variants or retries out of range")
    if not k.complete:
        return {"screenId": screen_id, "blocked": "knowledge-incomplete"}
    expected_mode = route_strategy(classify(k, flow, screen_id)["route"])["mode"]
    if strategy["mode"] != expected_mode:
        return {"screenId": screen_id, "blocked": "strategy-mismatch"}
    mode = strategy["mode"]
    if mode == "fill":
        axes = kit_axes() if axes is None else axes
        base = fill(k, flow, screen_id, binding_paths)
        out, seen = [], set()
        for index, letter in enumerate(LETTERS[:variants]):
            c = base if index == 0 else _axis_variant(base, k, letter, index, axes)
            key = _layout_key(c)
            if key in seen:
                continue                                   # identical layout: fewer variants, never duplicates
            seen.add(key)
            out.append({"variant": letter, "composition": c, "attempts": [],
                        "findings": comp.validate(c, k, flow=flow, binding_paths=binding_paths, mode="fill")})
        return {"screenId": screen_id, "mode": mode, "modelCalls": 0, "variants": out}
    deps = deps or {}
    if not callable(deps.get("generate")):
        return {"screenId": screen_id, "mode": mode, "blocked": "model-unavailable"}
    base = fill(k, flow, screen_id, binding_paths) if mode == "adapt" else None
    catalog = _catalog(k, screen_id, flow, binding_paths)
    out, calls = [], 0
    for letter in LETTERS[:variants]:
        user = {"task": mode, "catalog": catalog}
        variant_base = None
        if base is not None:
            variant_base = {**base, "variant": letter}
            user["base"] = variant_base
            user["allowedChanges"] = sorted({c["target"] for c in _screen_conditions(k, screen_id)})

        def check(c, variant_base=variant_base):
            return comp.validate(c, k, flow=flow, binding_paths=binding_paths, mode=mode, base=variant_base)

        result = _model_loop(deps, user, letter, check, retries)
        calls += len(result["attempts"])
        out.append(result)
        if result.get("blocked"):
            return {"screenId": screen_id, "mode": mode, "blocked": result["blocked"], "modelCalls": calls,
                    "variants": out}
    return {"screenId": screen_id, "mode": mode, "modelCalls": calls, "variants": out}


def states_for(screen_id, k, flow, expectation):
    """`default`, the shown assets' `requiredStates`, and states required by terminal-state expectations here."""
    if screen_id not in k.screens:
        raise ValueError("Unknown screen")
    states = ["default"]
    for a in shown_assets(k, screen_id):
        states += [s for s in (k.assets.get(a) or {}).get("requiredStates", []) if s not in states]
    cases = enumerate_cases(expectation.get("conditions", []))["cases"]
    for req in expectation.get("requirements", []):
        if req.get("kind") != "terminal-state" or req["state"] in states:
            continue
        for case in cases:
            try:
                applies = visible({"when": req["when"], "negate": False}, case)
            except ValueError:
                continue
            run = traverse(flow, case)
            if applies and run["end"] == "terminal" and run["path"] and run["path"][-1] == screen_id:
                states.append(req["state"])
                break
    return states


def _state_applies(k, node_asset, state, conditions):
    asset = k.assets.get(node_asset) or {}
    return state in asset.get("requiredStates", []) or any(
        c.get("effect") == "state" and c.get("target") == node_asset and c.get("state") == state for c in conditions)


def _overlay(base, state, k):
    c = copy.deepcopy(base)
    c["state"] = state
    findings = []
    conditions = [cond for _, n, _ in comp.walk(c) for cond in (k.assets.get(n["asset"]) or {}).get("conditions", [])]
    for path, node, _ in comp.walk(c):
        asset = k.assets.get(node["asset"]) or {}
        if not _state_applies(k, node["asset"], state, conditions):
            continue
        props = (asset.get("stateProps") or {}).get(state) or {}
        declared = ((asset.get("code") or {}).get("props") or {})
        for name, literal in props.items():
            if name not in declared:
                findings.append(comp._finding("state-prop-unknown", f"{path}.props.{name}",
                                              "stateProps may only set props of the asset's code layer"))
                continue
            node.setdefault("props", {})[name] = literal
            (node.get("bind") or {}).pop(name, None)
    return c, findings


def generate_states(base, states, k, deps, *, strategy, flow=None, binding_paths=None, retries=1):
    """State variants of one default composition. `fill` overlays `stateProps` deterministically; other modes ask
    the model under the same validation rules."""
    if not isinstance(strategy, dict) or strategy.get("mode") not in comp.MODES:
        raise ValueError("A generation strategy from route.strategy is required")
    if not k.complete:
        return {"screenId": base.get("screenId"), "blocked": "knowledge-incomplete"}
    wanted = [s for s in dict.fromkeys(states) if s != "default"]
    if any(s not in STATES for s in wanted):
        raise ValueError("Unknown state")
    paths = binding_paths if binding_paths is not None else frozenset()
    mode = "compose" if strategy["mode"] == "compose" else "fill"

    def check(c):
        return comp.validate(c, k, flow=flow, binding_paths=paths, mode=mode)

    out = []
    if strategy["mode"] == "fill":
        for state in wanted:
            c, findings = _overlay(base, state, k)
            out.append({"state": state, "composition": c, "attempts": [], "findings": findings + check(c)})
        return {"screenId": base.get("screenId"), "modelCalls": 0, "states": out}
    deps = deps or {}
    if not callable(deps.get("generate")):
        return {"screenId": base.get("screenId"), "blocked": "model-unavailable"}
    calls = 0
    for state in wanted:
        seeded, _ = _overlay(base, state, k)
        user = {"task": "state", "state": state, "base": seeded,
                "stateProps": {n["asset"]: (k.assets.get(n["asset"]) or {}).get("stateProps", {}).get(state)
                               for _, n, _ in comp.walk(base) if (k.assets.get(n["asset"]) or {}).get("stateProps")}}

        def check_state(c, state=state):
            c["state"] = state
            return check(c)

        result = _model_loop(deps, user, base.get("variant", "a"), check_state, retries)
        calls += len(result["attempts"])
        out.append({"state": state, **{key: result[key] for key in ("composition", "findings", "attempts")}})
        if result.get("blocked"):
            return {"screenId": base.get("screenId"), "blocked": result["blocked"], "modelCalls": calls, "states": out}
    return {"screenId": base.get("screenId"), "modelCalls": calls, "states": out}
