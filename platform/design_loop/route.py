"""Per-screen coverage route selects the generation strategy (engine plan, Task E9; U-03; Codex #22).

A screen is scored on three dimensions: an approved template, approved assets with a code layer (including nested
COMPOSES children and conditional include/exclude targets, which the page may render), and approved rules. Only a
fully covered screen is generated deterministically; anything less gives the model a bounded role.
"""
from __future__ import annotations

STRATEGIES = {
    "structured": {"mode": "fill", "modelCalls": 0},       # deterministic; copy proposals use the separate edit path
    "hybrid": {"mode": "adapt", "modelCalls": 1},          # model may only add/remove condition- or slot-allowed assets
    "unstructured": {"mode": "compose", "modelCalls": 1},  # model composes from allow-listed assets
}


def _approved(entry):
    return entry is not None and entry.get("reviewState", "approved") == "approved"


def shown_assets(k, screen_id):
    """Ordered closure of the assets a screen can render: direct, nested COMPOSES and conditional targets."""
    order, stack = [], list(reversed(k.screens[screen_id]["assets"]))
    while stack:
        a = stack.pop()
        if a in order:
            continue
        order.append(a)
        asset = k.assets.get(a) or {}
        nested = list(asset.get("composes", [])) + [c["target"] for c in asset.get("conditions", [])
                                                     if c.get("effect") in ("include", "exclude") and c["target"] != a]
        stack += reversed(nested)
    return order


def classify(k, flow, screen_id):
    if screen_id not in k.screens or screen_id not in flow["screens"]:
        raise ValueError("Unknown flow screen")
    screen, missing = k.screens[screen_id], {}
    tid = screen.get("templateId")
    if not tid or not _approved(k.templates.get(tid)):
        missing["template"] = [tid]
    assets = shown_assets(k, screen_id)
    bad = [a for a in assets if not _approved(k.assets.get(a)) or not (k.assets.get(a) or {}).get("code")]
    if bad:
        missing["assets"] = bad
    targets = {screen_id, *assets}
    rules = sorted(rid for rid, r in k.rules.items() if set(r["targets"]) & targets and not _approved(r))
    if rules:
        missing["rules"] = rules
    score = 3 - len(missing)
    route = "structured" if score == 3 else "unstructured" if score == 0 or not tid else "hybrid"
    return {"route": route, "score": score, "missing": missing}


def strategy(route):
    if route not in STRATEGIES:
        raise ValueError("Unknown coverage route")
    return dict(STRATEGIES[route])
