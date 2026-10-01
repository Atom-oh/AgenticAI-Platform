"""Targeted edit with complete-document and layout-box stability (engine plan, Task E14; G-04; Codex #12).

The model returns only the replacement node for the target. `stability` compares the *complete* documents: every
top-level field and every node outside the target subtree (order, props, bind, on, children), with the target subtree
replaced on both sides by a mask carrying its location, so a template, surface, state or variant change, a moved or
deleted target and any neighbour change are all reported. `layout_stable` compares the Browser checkpoint boxes of
every non-excluded test id; approval in C needs both. Test ids come only from `convention.Registry` (AZ1).
"""
from __future__ import annotations

import copy
import json

from . import composition as comp
from .model_call import call

_SYSTEM = (
    "You edit exactly one node of a mobile banking screen composition. Reply with JSON only: the replacement NODE "
    '{"id", "asset", "props"?, "bind"?, "on"?, "visibleWhen"?, "children"?}. Keep the same "id". Change only what the '
    "instruction asks. Never write a financial number as literal text: bind it. Callback props are platform-wired."
)


def locate(c, target_id):
    """-> (slot, [index path]) of the node with `target_id`; a missing target raises KeyError."""
    for slot, nodes in (c.get("slots") or {}).items():
        stack = [([i], n) for i, n in enumerate(nodes or [])]
        while stack:
            indices, node = stack.pop(0)
            if isinstance(node, dict) and node.get("id") == target_id:
                return slot, indices
            children = node.get("children") if isinstance(node, dict) else None
            if isinstance(children, list):
                stack = [(indices + [j], ch) for j, ch in enumerate(children)] + stack
    raise KeyError(target_id)


def _at(c, location):
    slot, indices = location
    node = c["slots"][slot][indices[0]]
    for index in indices[1:]:
        node = node["children"][index]
    return node


def _replace(c, location, value):
    slot, indices = location
    if len(indices) == 1:
        c["slots"][slot][indices[0]] = value
    else:
        parent = _at(c, (slot, indices[:-1]))
        parent["children"][indices[-1]] = value


def apply_edit(c, target_id, replacement):
    if not isinstance(replacement, dict) or replacement.get("id") != target_id:
        raise ValueError("target-id-changed")
    location = locate(c, target_id)
    out = copy.deepcopy(c)
    _replace(out, location, copy.deepcopy(replacement))
    return out


def _masked(c, location):
    out = copy.deepcopy(c)
    _replace(out, location, {"__target__": [location[0], list(location[1])]})
    return out


def _diff(a, b, path, out):
    if type(a) is not type(b):
        out.append(path or "<document>")
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else key
            if key not in a or key not in b:
                out.append(sub)
            else:
                _diff(a[key], b[key], sub, out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(path)
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(path)


def stability(before, after, target_id):
    """-> {"stable", "changes": [path or code]}; the target must keep its slot and index path."""
    here = locate(before, target_id)
    changes = []
    try:
        there = locate(after, target_id)
    except KeyError:
        _diff(_masked(before, here), after, "", changes)
        return {"stable": False, "changes": ["target-missing"] + changes}
    if there != here:
        changes.append("target-moved")
        _diff(_masked(before, here), _masked(after, there), "", changes)
        return {"stable": False, "changes": changes}
    _diff(_masked(before, here), _masked(after, here), "", changes)
    return {"stable": not changes, "changes": changes}


def subtree_testids(c, target_id, registry):
    """Every registry test id generated for the target node and its descendants (including choice options)."""
    target = _at(c, locate(c, target_id))
    ids = registry.test_ids(c["screenId"], c)
    out = set()
    for _, node, _ in comp.walk({"slots": {"_": [target]}}):
        test_id = ids["byNode"].get(node["id"])
        if test_id is None:
            continue
        out.add(test_id)
        options = (node.get("props") or {}).get("options")
        if node["id"] in ids["fieldOf"] and isinstance(options, list):
            out.update(f"{test_id}-o{j}" for j in range(1, len(options) + 1))
    return out


def layout_stable(before, after, *, excluded_testids, tolerance_px=1):
    """Compare B2 S5 checkpoint box maps `{"<ruleId>:<step>": {testId: [x, y, w, h]}}` outside the edited subtree."""
    moved, missing, blocked = [], [], []
    if not isinstance(before, dict) or not before:
        return {"stable": False, "moved": [], "missing": [], "blocked": ["no-checkpoints"]}
    for checkpoint in sorted(before):
        if checkpoint not in (after or {}):
            blocked.append(checkpoint)
            continue
        for test_id in sorted(before[checkpoint]):
            if test_id in excluded_testids:
                continue
            box, other = before[checkpoint][test_id], after[checkpoint].get(test_id)
            if other is None:
                missing.append({"checkpoint": checkpoint, "testId": test_id})
            elif len(other) != len(box) or any(abs(float(x) - float(y)) > tolerance_px for x, y in zip(box, other)):
                moved.append({"checkpoint": checkpoint, "testId": test_id})
    return {"stable": not (moved or missing or blocked), "moved": moved, "missing": missing, "blocked": blocked}


def _context(c, k, location):
    target = _at(c, location)
    slot, indices = location
    if len(indices) == 1:
        template = k.templates.get(c.get("templateId")) or {}
        alternatives = (template.get("slots", {}).get(slot) or {}).get("allowed", [])
    else:
        parent = _at(c, (slot, indices[:-1]))
        alternatives = (k.assets.get(parent.get("asset")) or {}).get("composes", [])
    assets = {}
    for a in sorted(set(alternatives) | {target.get("asset")}):
        asset = k.assets.get(a)
        if asset:
            code = asset.get("code") or {}
            assets[a] = {"title": asset["title"], "intent": asset.get("intent"), "adapter": code.get("adapter"),
                         "props": code.get("props", {}), "composes": asset.get("composes", [])}
    return target, assets


def edit(c, target_id, instruction, k, deps, *, flow, binding_paths, mode="fill"):
    """-> {composition, findings, stability} (or `blocked`). Approval in C also needs a later `layout_stable` pass."""
    location = locate(c, target_id)
    deps = deps or {}
    if not callable(deps.get("generate")):
        return {"composition": None, "findings": [], "stability": None, "blocked": "model-unavailable"}
    target, assets = _context(c, k, location)
    user = json.dumps({"instruction": instruction, "targetId": target_id, "target": target, "assets": assets,
                       "bindingPaths": sorted(binding_paths), "composition": c}, ensure_ascii=False, sort_keys=True)
    text, blocked = call(deps, _SYSTEM, user)
    if blocked:
        return {"composition": None, "findings": [], "stability": None, "blocked": blocked}
    try:
        replacement = json.loads(text)
    except (TypeError, ValueError):
        replacement = None
    if not isinstance(replacement, dict):
        return {"composition": None, "findings": [comp._finding("parse-failed", "", "the reply is not one JSON node")],
                "stability": {"stable": False, "changes": ["parse-failed"]}}
    if replacement.get("id") != target_id:
        return {"composition": None,
                "findings": [comp._finding("target-id-changed", comp_path(location), "the replacement must keep the id")],
                "stability": {"stable": False, "changes": ["target-id-changed"]}}
    after = apply_edit(c, target_id, replacement)
    findings = comp.validate(after, k, flow=flow, binding_paths=binding_paths, mode=mode,
                             base=c if mode == "adapt" else None)
    return {"composition": after, "findings": findings, "stability": stability(c, after, target_id)}


def comp_path(location):
    slot, indices = location
    return f"slots.{slot}[{indices[0]}]" + "".join(f".children[{i}]" for i in indices[1:])
