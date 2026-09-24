"""Composition schema v1 and full validation (engine plan, Task E10; G-01; Codex #10).

A composition tree is the engine's single intermediate representation: one composition per screen and state holds
every node the page can show; case-dependent nodes carry `visibleWhen`, derived only from an applicable
`uxModel.conditions` entry. Financial values reach a page only through PRD bindings: a literal quantity anywhere in
the tree is a critical finding. Validation never repairs; it reports.
"""
from __future__ import annotations

import copy
import json
import math
import re

from workspace.ontology_ux import STATES, parse_when
from workspace.ontology_ux import visible as _visible

from .financial import contains_quantity

SCHEMA_VERSION = 1
TOP = frozenset({"schemaVersion", "screenId", "templateId", "state", "variant", "surface", "slots"})
NODE_KEYS = frozenset({"id", "asset", "props", "bind", "on", "visibleWhen", "children"})
SURFACES = frozenset({"page", "bottom-sheet", "full-popup", "layer", "tab"})
MODES = frozenset({"fill", "adapt", "compose"})
MAX_DEPTH, MAX_NODES, MAX_JSON_DEPTH = 6, 200, 32
MAX_TEXT = 500
_NODE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_VARIANT = re.compile(r"[a-z0-9][a-z0-9-]{0,15}\Z")
# Props a trusted adapter wires in codegen (react_project, E12); a composition never supplies them.
ADAPTER_PROPS = {"controlled-text": {"value", "onChange"}, "controlled-choice": {"value", "onChange"},
                 "controlled-bool": {"checked", "onChange"}, "action": {"onClick"}}
SUMMARY_TYPE = {"list": {"label": "string", "value": "string"}}


def _finding(code, path, message, severity="critical", **extra):
    return {"severity": severity, "code": code, "path": path, "message": message, **extra}


# ---- bounded serializer (not schema.digest; review round 3, F6) -----------------------------------------------

def _check_json(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ValueError("Composition nesting exceeds the serializer limit")
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValueError("Composition integer out of range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Composition numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            _check_json(item, depth + 1)
        return
    raise ValueError("Composition values must be JSON")


def canonical(c):
    _check_json(c)
    return json.dumps(c, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(c):
    import hashlib
    return hashlib.sha256(canonical(c)).hexdigest()


# ---- traversal ------------------------------------------------------------------------------------------------

def walk(c):
    """Yield `(path, node, parent)` in document order; `parent` is None for top-level slot nodes."""
    slots = c.get("slots") if isinstance(c, dict) else None
    if not isinstance(slots, dict):
        return
    stack = []
    for name, nodes in slots.items():
        if isinstance(nodes, list):
            stack += [(f"slots.{name}[{i}]", n, None, 1) for i, n in enumerate(nodes)]
    stack.reverse()
    while stack:
        path, node, parent, _ = stack.pop()
        yield path, node, parent
        children = node.get("children") if isinstance(node, dict) else None
        if isinstance(children, list):
            stack += reversed([(f"{path}.children[{i}]", ch, node, 0) for i, ch in enumerate(children)])


def _depths(c):
    out, stack = [], []
    for nodes in (c.get("slots") or {}).values():
        if isinstance(nodes, list):
            stack += [(n, 1) for n in nodes]
    while stack:
        node, depth = stack.pop()
        out.append(depth)
        if depth > MAX_DEPTH + 1:
            continue                                   # already over the limit: stop descending
        children = node.get("children") if isinstance(node, dict) else None
        if isinstance(children, list):
            stack += [(ch, depth + 1) for ch in children]
    return out


def visible(c, case):
    """The tree shown in `case`: nodes whose `visibleWhen` is false are removed with their subtrees."""
    def keep(nodes):
        out = []
        for n in nodes:
            if not _visible(n.get("visibleWhen"), case):
                continue
            n = dict(n)
            if isinstance(n.get("children"), list):
                n["children"] = keep(n["children"])
            out.append(n)
        return out
    result = copy.deepcopy(c)
    result["slots"] = {name: keep(nodes) for name, nodes in c["slots"].items()}
    return result


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def text_of(c, values=None, k=None):
    """Visible literal text plus resolved binding values, in document order (used by coverage and review).

    With `k`, enum-typed props (tone, kind, as) are left out because they are not rendered text."""
    lines = []
    for _, node, _ in walk(c):
        if not isinstance(node, dict):
            continue
        code = ((k.assets.get(node.get("asset")) or {}).get("code") or {}) if k is not None else {}
        for name, literal in (node.get("props") or {}).items():
            if code.get("props", {}).get(name, {}).get("type") == "enum":
                continue
            lines += list(_strings(literal))
        for path in (node.get("bind") or {}).values():
            if values is not None and path in values:
                lines += list(_strings(values[path]))
    return "\n".join(line for line in lines if line.strip())


# ---- validation -----------------------------------------------------------------------------------------------

def _binding_type(binding_paths, path):
    if isinstance(binding_paths, dict):
        return binding_paths.get(path)
    return SUMMARY_TYPE if path == "product.summaryItems" else "string"


def _literal_ok(spec, value):
    kind = spec["type"]
    if kind == "string":
        return isinstance(value, str) and len(value) <= MAX_TEXT
    if kind == "number":
        return type(value) in (int, float) and math.isfinite(value)
    if kind == "boolean":
        return type(value) is bool
    if kind == "enum":
        return isinstance(value, str) and value in spec.get("values", [])
    if kind == "list":
        item = spec.get("item", {})
        return isinstance(value, list) and len(value) <= 50 and all(
            isinstance(v, dict) and set(v) == set(item) and all(
                isinstance(v[f], str) and len(v[f]) <= MAX_TEXT if t == "string" else type(v[f]) in (int, float)
                for f, t in item.items()) for v in value)
    return False                                           # node and callback props have no literal form


def _bind_ok(spec_type, spec, btype):
    if spec_type == "string":
        return btype == "string"
    if spec_type == "list":
        return isinstance(btype, dict) and btype.get("list") == spec.get("item")
    return False


def _conditions(c, k):
    """Applicable include/exclude conditions: from the assets on this composition and on the screen."""
    owners = [n.get("asset") for _, n, _ in walk(c) if isinstance(n, dict)]
    owners += list((k.screens.get(c.get("screenId")) or {}).get("assets", []))
    out, seen = [], set()
    for a in owners:
        for cond in (k.assets.get(a) or {}).get("conditions", []):
            key = (a, cond.get("id"))
            if cond.get("effect") in ("include", "exclude") and key not in seen:
                seen.add(key)
                out.append(cond)
    return out


def _strip(c, targets):
    def keep(nodes):
        out = []
        for n in nodes if isinstance(nodes, list) else []:
            if isinstance(n, dict) and n.get("asset") in targets:
                continue
            n = dict(n) if isinstance(n, dict) else n
            if isinstance(n, dict) and isinstance(n.get("children"), list):
                n["children"] = keep(n["children"])
            out.append(n)
        return out
    result = {key: value for key, value in c.items() if key != "slots"}
    result["slots"] = {name: keep(nodes) for name, nodes in (c.get("slots") or {}).items()}
    return result


def validate(c, k, *, flow=None, binding_paths=frozenset(), mode="fill", base=None):
    findings = []
    add = lambda code, path, message, **kw: findings.append(_finding(code, path, message, **kw))  # noqa: E731
    if mode not in MODES:
        raise ValueError("Unknown generation mode")
    if not isinstance(c, dict) or set(c) != TOP or c.get("schemaVersion") != SCHEMA_VERSION \
            or not isinstance(c.get("slots"), dict) or not isinstance(c.get("screenId"), str) \
            or not isinstance(c.get("variant"), str) or not _VARIANT.fullmatch(c["variant"]):
        add("shape", "", "top-level keys, schemaVersion 1, slots object and variant are required")
        return findings
    try:
        canonical(c)
    except ValueError as error:
        add("shape", "", str(error))
        return findings
    if c["state"] not in STATES:
        add("bad-state", "state", "unknown state")
    if c["surface"] not in SURFACES:
        add("bad-surface", "surface", "unknown surface")
    screen = k.screens.get(c["screenId"])
    if screen is None:
        add("shape", "screenId", "unknown screen")
        return findings
    template = k.templates.get(c["templateId"])
    if template is None:
        add("unknown-template", "templateId", "unknown template")
    elif screen.get("templateId") != c["templateId"] and not (mode == "compose" and screen.get("templateId") is None):
        add("template-mismatch", "templateId", "the screen's template differs")
    # tree limits and node shape
    depths = _depths(c)
    if len(depths) > MAX_NODES or (depths and max(depths) > MAX_DEPTH):
        add("tree-limit", "slots", f"at most {MAX_NODES} nodes and depth {MAX_DEPTH}")
        return findings
    ids = set()
    for path, node, _ in walk(c):
        if (not isinstance(node, dict) or not {"id", "asset"} <= set(node) or set(node) - NODE_KEYS
                or not isinstance(node.get("asset"), str)
                or any(not isinstance(node.get(key, {}), dict) for key in ("props", "bind", "on"))
                or not isinstance(node.get("children", []), list)):
            add("shape", path, "node keys")
            return findings
        if not isinstance(node["id"], str) or not _NODE_ID.fullmatch(node["id"]):
            add("bad-id", path + ".id", "node id")
        elif node["id"] in ids:
            add("duplicate-id", path + ".id", "duplicate node id")
        ids.add(node["id"])
    # slots
    slots = (template or {}).get("slots", {})
    for name in c["slots"]:
        if template is not None and name not in slots:
            add("unknown-slot", f"slots.{name}", "slot not declared by the template")
        if not isinstance(c["slots"][name], list):
            add("shape", f"slots.{name}", "slot must be a list")
            return findings
    for name, spec in slots.items():
        if name not in c["slots"]:
            add("missing-slot", f"slots.{name}", "declared slot is missing")
        elif spec.get("required") and not c["slots"][name]:
            add("slot-empty", f"slots.{name}", "required slot is empty")
    # visibility: applicable conditions targeting each node's asset
    conditions = _conditions(c, k)
    outgoing = [t for t in (flow or {}).get("transitions", []) if t["src"] == c["screenId"]]
    terminal = flow is not None and c["screenId"] in flow.get("terminals", [])
    for path, node, parent in walk(c):
        asset_id = node["asset"]
        asset = k.assets.get(asset_id)
        if parent is None:
            slot = path.split("[", 1)[0].split(".", 1)[1]
            if slot in slots and asset_id not in slots[slot].get("allowed", []):
                add("not-allowed", path, "asset not allowed in this slot")
        elif asset_id not in (k.assets.get(parent["asset"]) or {}).get("composes", []):
            add("not-composed", path, "parent does not compose this asset")
        if asset is None:
            if mode == "compose":
                add("new-asset-candidate", path, "unknown asset proposed", severity="major", approvable=False)
            else:
                add("unknown-asset", path, "unknown asset")
            continue
        code = asset.get("code")
        if not code:
            add("no-code-layer", path, "asset has no code layer")
            continue
        specs, managed = code.get("props", {}), ADAPTER_PROPS.get(code.get("adapter"), set())
        text_child = code.get("childrenProp") == "children"
        props, bind = node.get("props", {}), node.get("bind", {})
        for name, literal in props.items():
            ppath = f"{path}.props.{name}"
            if name == "children" and text_child:
                if not isinstance(literal, str) or len(literal) > MAX_TEXT:
                    add("prop-type", ppath, "text child must be a string")
            elif name not in specs:
                add("unknown-prop", ppath, "prop not declared by the code layer")
                continue
            elif specs[name]["type"] == "callback":
                add("callback-literal", ppath, "callbacks are wired by trusted adapters only")
                continue
            elif name in managed:
                add("prop-conflict", ppath, "prop is owned by the trusted adapter")
                continue
            elif not _literal_ok(specs[name], literal):
                add("prop-type", ppath, "literal does not match the declared prop type")
            if name in bind:
                add("prop-conflict", ppath, "prop is both literal and bound")
            if any(contains_quantity(s) for s in _strings(literal)):
                add("literal-financial-value", ppath, "financial quantities must be bound from the PRD")
        for name, bpath in bind.items():
            ppath = f"{path}.bind.{name}"
            spec = {"type": "string"} if name == "children" and text_child else specs.get(name)
            if spec is None:
                add("unknown-prop", ppath, "prop not declared by the code layer")
                continue
            if spec["type"] == "callback" or name in managed:
                add("callback-literal" if spec["type"] == "callback" else "prop-conflict", ppath,
                    "adapter-owned props cannot be bound")
                continue
            if not isinstance(bpath, str) or bpath not in binding_paths:
                add("unknown-binding", ppath, "path is not in the PRD binding catalog")
                continue
            if not _bind_ok(spec["type"], spec, _binding_type(binding_paths, bpath)):
                add("binding-type", ppath, "binding value type differs from the prop type")
        for name, spec in specs.items():
            if spec["required"] and name not in props and name not in bind and name not in managed:
                add("prop-required", f"{path}.props.{name}", "required prop is missing")
        for event, target in node.get("on", {}).items():
            opath = f"{path}.on.{event}"
            if event != "click":
                add("shape", opath, "only on.click is supported")
                continue
            if code.get("adapter") != "action" or not ("onClick" in specs or code.get("exportName") == "Button"):
                add("unknown-transition", opath, "on.click needs an action adapter")
            elif flow is None:
                add("unknown-transition", opath, "no flow to resolve the transition")
            elif target == "next":
                if not outgoing:
                    add("unknown-transition", opath, "no outgoing transition from this screen")
            elif target == "finish":
                if not terminal:
                    add("unknown-transition", opath, "finish is valid only on terminal screens")
            elif not any(t["id"] == target for t in outgoing):
                add("unknown-transition", opath, "transition does not leave this screen")
        targeting = [cond for cond in conditions if cond.get("target") == asset_id]
        spec = node.get("visibleWhen")
        if spec is not None:
            derived = [{"when": cond["when"], "negate": cond["effect"] == "exclude"} for cond in targeting]
            well_formed = isinstance(spec, dict) and set(spec) == {"when", "negate"} and type(spec["negate"]) is bool
            if well_formed:
                try:
                    parse_when(spec["when"])
                except ValueError:
                    well_formed = False
            if not well_formed or spec not in derived:
                add("visibility-unbound", f"{path}.visibleWhen", "visibleWhen must come from an applicable condition")
        elif targeting:
            add("visibility-missing", path, "conditional target needs visibleWhen")
    if mode == "adapt":
        if base is None:
            add("adapt-scope", "", "adapt mode requires the base composition")
        else:
            targets = {cond["target"] for cond in conditions}
            try:
                same = canonical(_strip(c, targets)) == canonical(_strip(base, targets))
            except ValueError:
                same = False
            if not same:
                add("adapt-scope", "", "adapt may only add or remove condition-governed assets")
    return findings
