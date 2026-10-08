"""Bounded UX SM model property for design ontology nodes (REQUIREMENTS O-01; platform-ontology/1)."""
from __future__ import annotations

import copy
import re

STATES = frozenset({"default", "empty", "error", "ineligible", "loading", "done", "zero", "many"})
PROP_TYPES = frozenset({"string", "number", "boolean", "node", "enum", "list", "callback"})
ADAPTERS = frozenset({"controlled-text", "controlled-bool", "controlled-choice", "action"})
_ITEM_TYPES = frozenset({"string", "number"})
SOURCES = frozenset({"product", "session", "static"})
EFFECTS = frozenset({"include", "exclude", "state"})
KEYS = frozenset({"intent", "conditions", "dataBindings", "requiredStates", "stateProps", "slots", "layers", "changeReason"})
LEVELS = frozenset({"Atom", "Molecule", "Organism", "Pattern", "PageTemplate", "Component", "Foundation"})
FOUNDATION_KEYS = frozenset({"layers", "changeReason"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_PROP = re.compile(r"[a-z][A-Za-z0-9]{0,39}\Z")
_IMPORT = re.compile(r"(?:@[a-z0-9-]+/)?[a-z0-9][a-z0-9._/-]{0,199}\Z")
_EXPORT = re.compile(r"[A-Z][A-Za-z0-9]{0,79}\Z")
_TERM = re.compile(r"!?cond:[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
# The fixed binding catalog of prd_extract.bindings (E7); anything else is not a structural symbol.
# Dynamic segments are server-assigned ordinals (pref-1, notice-1), never user or model names (review round 13, AF1).
BINDING_PATH = re.compile(r"(?:product\.(?:productName|productType|category|eligibility|term|baseRate|summaryItems"
                          r"|preferential\.pref-[1-9][0-9]{0,2}\.(?:condition|rate)|notice\.notice-[1-9][0-9]{0,2})"
                          r"|session\.(?:amount|agreed|branch|preference))\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _fail(message):
    raise ValueError(f"Invalid uxModel: {message}")


def _text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(name)
    return value


def _list(value, limit, name):
    if not isinstance(value, list) or len(value) > limit:
        _fail(name)
    return value


def _id(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail(name)
    return value


def _exact(value, required, optional=(), name="object"):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        _fail(name)
    return value


def parse_when(expr):
    terms = [t.strip() for t in _text(expr, 300, "when").split("&")]
    if not terms or any(not _TERM.fullmatch(t) for t in terms):
        _fail("when grammar")
    return [(t.startswith("!"), t.lstrip("!")[5:]) for t in terms]


def evaluate(expr, case):
    """The one `when` evaluator: `None` holds; every term needs a boolean assignment in `case` (fail closed)."""
    if expr is None:
        return True
    result = True
    for negated, cid in parse_when(expr):
        value = case.get(cid) if isinstance(case, dict) else None
        if type(value) is not bool:
            raise ValueError(f"Case does not assign condition {cid}")
        result = result and (value != negated)
    return result


def visible(spec, case):
    """Shared visibility rule (engine plan E10, review round 16/17, AJ1): whole-expression negation."""
    if spec is None:
        return True
    return evaluate(spec["when"], case) != bool(spec["negate"])


def _code(code):
    _exact(code, {"importPath", "exportName", "props"}, {"childrenProp", "adapter"}, "code layer")
    if "adapter" in code and code["adapter"] not in ADAPTERS:
        _fail("adapter")
    if not isinstance(code["importPath"], str) or not _IMPORT.fullmatch(code["importPath"]):
        _fail("importPath")
    if not isinstance(code["exportName"], str) or not _EXPORT.fullmatch(code["exportName"]):
        _fail("exportName")
    if code.get("childrenProp") not in (None, "children"):
        _fail("childrenProp")
    props = code["props"]
    if not isinstance(props, dict) or len(props) > 40:
        _fail("props")
    for name, spec in props.items():
        if not _PROP.fullmatch(name):
            _fail("prop name")
        _exact(spec, {"type", "required"}, {"values", "item"}, "prop spec")
        if spec["type"] not in PROP_TYPES or type(spec["required"]) is not bool:
            _fail("prop type")
        if spec["type"] == "list":
            item = spec.get("item")
            if (not isinstance(item, dict) or not 1 <= len(item) <= 8
                    or any(not _PROP.fullmatch(k) or v not in _ITEM_TYPES for k, v in item.items())):
                _fail("list item shape")
        elif "item" in spec:
            _fail("item on non-list")
        if spec["type"] == "callback" and not code.get("adapter"):
            _fail("callback props require a trusted adapter")
        if spec["type"] == "enum":
            values = _list(spec.get("values"), 30, "enum values")
            if not values or any(not isinstance(v, str) or not v or len(v) > 80 for v in values):
                _fail("enum values")
        elif "values" in spec:
            _fail("values on non-enum")


def validate_ux_model(value, node_type):
    if node_type not in LEVELS:
        _fail("uxModel on a non-design level")
    if not isinstance(value, dict) or value.keys() - KEYS:
        _fail("fields")
    if node_type == "Foundation" and value.keys() - FOUNDATION_KEYS:
        _fail("Foundation uxModel allows layers and changeReason only")
    for key in ("intent", "changeReason"):
        if key in value:
            _text(value[key], 500, key)
    for c in _list(value.get("conditions", []), 50, "conditions"):
        _exact(c, {"id", "when", "effect", "target"}, {"state"}, "condition")
        _id(c["id"], "condition id"), parse_when(c["when"]), _id(c["target"], "target")
        if c["effect"] not in EFFECTS or (c["effect"] == "state") != ("state" in c):
            _fail("effect")
        if "state" in c and c["state"] not in STATES:
            _fail("condition state")
    for b in _list(value.get("dataBindings", []), 50, "dataBindings"):
        _exact(b, {"field", "source", "path", "required"}, name="binding")
        _id(b["field"], "field"), _text(b["path"], 200, "path")
        if not BINDING_PATH.fullmatch(b["path"]):          # trusted binding catalog only (review round 12, AF1)
            _fail("binding path outside the trusted catalog")
        if b["source"] not in SOURCES or type(b["required"]) is not bool:
            _fail("binding source")
    states = _list(value.get("requiredStates", []), len(STATES), "requiredStates")
    if set(states) - STATES or len(set(states)) != len(states):
        _fail("requiredStates")
    state_props = value.get("stateProps", {})          # review round 5, Y2
    if not isinstance(state_props, dict) or set(state_props) - set(states) or len(state_props) > len(STATES):
        _fail("stateProps keys must be declared requiredStates")
    for state, props in state_props.items():
        if not isinstance(props, dict) or not 1 <= len(props) <= 20:
            _fail("stateProps")
        for name, literal in props.items():
            if not _PROP.fullmatch(name) or not isinstance(literal, (str, int, bool)) or isinstance(literal, str) and len(literal) > 500:
                _fail("stateProps value")
    slots = value.get("slots")
    if slots is not None:
        if node_type != "PageTemplate" or not isinstance(slots, dict) or not 1 <= len(slots) <= 12:
            _fail("slots")
        for name, slot in slots.items():
            _id(name, "slot")
            _exact(slot, {"required", "allowed"}, name="slot spec")
            if type(slot["required"]) is not bool:
                _fail("slot required")
            allowed = _list(slot["allowed"], 100, "allowed")
            if not allowed:
                _fail("empty allow-list")
            for item in allowed:
                _id(item, "allowed id")
    layers = value.get("layers", {})
    if not isinstance(layers, dict) or layers.keys() - {"wireframe", "gui", "code"}:
        _fail("layers")
    if "wireframe" in layers:
        _exact(layers["wireframe"], {"blocks"}, name="wireframe")
        for block in _list(layers["wireframe"]["blocks"], 50, "blocks"):
            _id(block, "block")
    if "gui" in layers:
        gui = _exact(layers["gui"], {"snapshotHash", "width", "height"}, name="gui")
        if not isinstance(gui["snapshotHash"], str) or not _HASH.fullmatch(gui["snapshotHash"]):
            _fail("gui snapshot")
        if any(type(gui[k]) is not int or not 1 <= gui[k] <= 4096 for k in ("width", "height")):
            _fail("gui size")
    if "code" in layers:
        _code(layers["code"])
        if node_type == "PageTemplate" and slots:
            props = layers["code"]["props"]
            if any(props.get(name, {}).get("type") != "node" for name in slots):
                _fail("template slots must be node props")
    return value


def references(value):
    refs = [c["target"] for c in value.get("conditions", [])]
    for slot in (value.get("slots") or {}).values():
        refs += slot["allowed"]
    return refs


def rewrite(value, identities):
    out = copy.deepcopy(value)
    for c in out.get("conditions", []):
        c["target"] = identities.get(c["target"], c["target"])
    for slot in (out.get("slots") or {}).values():
        slot["allowed"] = [identities.get(i, i) for i in slot["allowed"]]
    return out
