"""Golden benchmark oracle and success-criteria metrics (engine plan, Task E18; O-05, REQUIREMENTS section 8).

`golden.json` is human-authored and independent of the ontology rules, so deleting a rule can never raise the score
(Codex #22): the expectation stays while the generated pages lose what the rule used to produce.

`score(outputs, golden)` reads rendered visible text **per page checkpoint** (review round 5, Y6). The Browser
verifier records `checkpoints: {"<ruleId>:<step>": {"case", "page", "state", "visibleText", "truncated"}}` at every
`expectVisible` of a page root, before any navigation, so a defect on a page a rule leaves by navigation is still
seen. `outputs` is `{"checkpoints": {...}, "ends": [{"case", "screen"}]}`; `page` is the screen id (callers map
page ids through `Registry`). A golden case with a missing or truncated checkpoint, or no recorded end, is
`incomplete` and the result is `blocked`; it never counts as a pass.
"""
from __future__ import annotations

import math

MAX_VISIBLE = 16000
ALL = "*"


def _norm(text):
    return " ".join(str(text or "").split())


def _matches(golden_case, case):
    return isinstance(case, dict) and all(case.get(key) == value for key, value in golden_case.items())


def _validate_golden(golden):
    ids = set()
    for g in golden:
        if (not isinstance(g, dict) or not isinstance(g.get("id"), str) or g["id"] in ids
                or not isinstance(g.get("case"), dict) or not g["case"]
                or not all(type(v) is bool for v in g["case"].values())
                or not isinstance(g.get("endsAt"), str)
                or not all(isinstance(e, dict) and isinstance(e.get("screen"), str) and isinstance(e.get("text"), str)
                           and e["text"].strip() for e in g.get("mustShow", []) + g.get("mustNotShow", []))):
            raise ValueError("Invalid golden case")
        ids.add(g["id"])


def _checkpoints(outputs):
    points = (outputs or {}).get("checkpoints")
    if not isinstance(points, dict):
        raise ValueError("outputs need checkpoints")
    return list(points.values())


def _unusable(point):
    text = point.get("visibleText")
    return point.get("truncated") is not False or not isinstance(text, str) or len(text) > MAX_VISIBLE


def score_case(g, points, ends):
    """-> (verdict, reasons, evidence_missing). An observed defect fails the case; missing evidence still blocks."""
    mine = [p for p in points if isinstance(p, dict) and _matches(g["case"], p.get("case"))]
    reasons = []

    def text_of(point):
        return _norm(point.get("visibleText"))

    incomplete = False
    for expect in g.get("mustShow", []):
        pages = [p for p in mine if p.get("page") == expect["screen"]]
        if not pages or any(_unusable(p) for p in pages):
            incomplete = True
            reasons.append({"kind": "checkpoint-missing", "screen": expect["screen"]})
        elif not any(_norm(expect["text"]) in text_of(p) for p in pages):
            reasons.append({"kind": "must-show", **expect})
    for expect in g.get("mustNotShow", []):
        pages = mine if expect["screen"] == ALL else [p for p in mine if p.get("page") == expect["screen"]]
        if not pages or any(_unusable(p) for p in pages):
            incomplete = True
            reasons.append({"kind": "checkpoint-missing", "screen": expect["screen"]})
        elif any(_norm(expect["text"]) in text_of(p) for p in pages):
            reasons.append({"kind": "must-not-show", **expect,
                            "at": sorted({p.get("page") for p in pages if _norm(expect["text"]) in text_of(p)})})
    ended = [e.get("screen") for e in ends if isinstance(e, dict) and _matches(g["case"], e.get("case"))]
    if len(set(ended)) != 1:
        incomplete = True
        reasons.append({"kind": "end-missing" if not ended else "end-ambiguous"})
    elif ended[0] != g["endsAt"]:
        reasons.append({"kind": "ends-at", "expected": g["endsAt"], "actual": ended[0]})
    failed = [r for r in reasons if r["kind"] in ("must-show", "must-not-show", "ends-at")]
    verdict = "fail" if failed else "incomplete" if incomplete else "pass"
    return verdict, reasons, incomplete


def score(outputs, golden):
    _validate_golden(golden)
    points = _checkpoints(outputs)
    ends = (outputs or {}).get("ends") or []
    passed, failed, incomplete, details, blocked = [], [], [], {}, False
    for g in golden:
        verdict, reasons, missing = score_case(g, points, ends)
        details[g["id"]] = reasons
        blocked = blocked or missing
        {"pass": passed, "fail": failed, "incomplete": incomplete}[verdict].append(g["id"])
    return {"passed": passed, "failed": failed, "incomplete": incomplete, "blocked": blocked,
            "rate": len(passed) / len(golden) if golden else None, "details": details}


def compare(before, after):
    """Judge an ontology change by the golden set only: a case that stops passing is a regression."""
    improved = sorted(set(after["passed"]) - set(before["passed"]))
    regressed = sorted(set(before["passed"]) - set(after["passed"]))
    return {"improved": improved, "regressed": regressed, "blocked": bool(before.get("blocked") or after.get("blocked"))}


def _number(value, name, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"Invalid run field {name}")
    return value


def success_metrics(runs):
    """REQUIREMENTS section 8 indicators from recorded runs.

    Each run: `{"coveragePassed": bool, "rounds": int >= 1, "screens": int >= 1, "approvedWithoutEdit": int,
    "edits": int, "stableEdits": int, "startedAt": seconds, "handoffAt": seconds | None}`."""
    if not isinstance(runs, list) or not runs:
        raise ValueError("at least one recorded run is required")
    covered, rounds, screens, unedited, edits, stable, handoff = 0, [], 0, 0, 0, 0, []
    for run in runs:
        if not isinstance(run, dict) or type(run.get("coveragePassed")) is not bool:
            raise ValueError("Invalid run record")
        covered += run["coveragePassed"]
        rounds.append(_number(run.get("rounds"), "rounds", 1))
        count = _number(run.get("screens"), "screens", 1)
        approved = _number(run.get("approvedWithoutEdit"), "approvedWithoutEdit")
        made = _number(run.get("edits"), "edits")
        kept = _number(run.get("stableEdits"), "stableEdits")
        if approved > count or kept > made or any(type(v) is not int for v in (run["rounds"], count, approved, made, kept)):
            raise ValueError("Invalid run counts")
        screens, unedited, edits, stable = screens + count, unedited + approved, edits + made, stable + kept
        start = _number(run.get("startedAt"), "startedAt")
        if run.get("handoffAt") is not None:
            end = _number(run["handoffAt"], "handoffAt")
            if end < start:
                raise ValueError("handoff before start")
            handoff.append(end - start)
    return {"runs": len(runs), "coveragePassRate": covered / len(runs),
            "meanRegenerationRounds": sum(rounds) / len(rounds), "maxRegenerationRounds": max(rounds),
            "approvedWithoutEditRate": unedited / screens,
            "editStabilityRate": stable / edits if edits else None,
            "handedOff": len(handoff), "meanTimeToHandoffSeconds": sum(handoff) / len(handoff) if handoff else None}
