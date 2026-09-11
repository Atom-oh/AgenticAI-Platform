"""Mandatory React evidence and explicitly reviewed visual variations."""
from __future__ import annotations

import copy

from workspace.rules import report_passes


def apply_visual_policy(report: dict, run: dict) -> None:
    visual = report.get("visual", {})
    if (run.get("visualPolicy") != "variation-review" or not run.get("referenceAssetId")
            or visual.get("status") not in ("pass", "fail")):
        return
    original = visual["status"]
    visual["comparisonStatus"] = original
    visual["status"] = "review-required"
    report["visualPolicy"] = "variation-review"
    # The comparison is retained as a comparison, never renamed to pixel pass.
    report["blockingFindings"] = [item for item in report.get("blockingFindings", [])
                                  if item != "시각 비교 실패 또는 미판정"]
    report["passed"] = not report["blockingFindings"] and not report.get("engineError")


def react_report_passes(contract: dict, report: dict, *, visual_required=False, visual_policy="exact") -> bool:
    build = report.get("build") or {}
    if (build.get("ok") is not True or build.get("catalogHash") != contract.get("catalogHash")
            or not build.get("sourceHash") or not build.get("bundleHash") or report.get("engineError")):
        return False
    gates = build.get("gates") or {}
    if any(gates.get(name, {}).get("status") != "pass" for name in ("components", "policy", "types", "build")):
        return False
    checked = report
    if report.get("visual", {}).get("status") == "review-required":
        if (visual_policy != "variation-review" or report.get("visualPolicy") != "variation-review"
                or report["visual"].get("comparisonStatus") not in ("pass", "fail")):
            return False
        checked = copy.deepcopy(report)
        checked["visual"]["status"] = "not-run"
        visual_required = False
    return report_passes(contract, checked, visual_required=visual_required)
