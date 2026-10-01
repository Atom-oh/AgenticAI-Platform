"""Injected model-call gate shared by the design engine extractors (engine plan, Global Constraints).

The engine never imports a model client. `deps["normalize"]` is a verify-only boundary check over every prompt
string (it raises on a residual identifier); `deps["generate"](system, user, on_token) -> str` is the model. A
missing callable is an explicit blocked outcome, never a pass, and nothing reaches the model before the check.
"""
from __future__ import annotations


def call(deps, system, user):
    """Return `(text, None)` or `(None, blocked_code)`; the model is called only after normalization passes."""
    deps = deps or {}
    normalize, generate = deps.get("normalize"), deps.get("generate")
    if not callable(normalize):
        return None, "normalization-unavailable"
    if not callable(generate):
        return None, "model-unavailable"
    try:
        for text in (system, user):
            normalize(text)
    except Exception:  # noqa: BLE001 - any hit or checker failure blocks the call (fail closed)
        return None, "normalization-blocked"
    try:
        out = generate(system, user, None)
    except Exception:  # noqa: BLE001 - the model error text is never echoed back
        return None, "model-failed"
    if not isinstance(out, str):
        return None, "model-failed"
    return out, None
