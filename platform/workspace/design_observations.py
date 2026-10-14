"""Frozen page/case mappings and bounded actual Browser journey observations."""
from __future__ import annotations

import copy
import json
import re
import time

MAX_TEXT = 16000
MAX_POINTS = 1024
MAX_BYTES = 2 * 1024 * 1024
MAX_HOPS = 50
TARGET = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def normalize(plan):
    if not isinstance(plan, dict) or set(plan) != {"schemaVersion", "caseControl", "cases", "entry", "pages", "terminals"}:
        raise ValueError("Invalid Browser observation plan")
    if plan["schemaVersion"] != 1 or type(plan["schemaVersion"]) is not int:
        raise ValueError("Invalid Browser observation version")
    cases, pages, terminals = plan["cases"], plan["pages"], plan["terminals"]
    if (not isinstance(cases, list) or not 1 <= len(cases) <= 64
            or any(not isinstance(case, dict) or len(case) > 6
                   or any(not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or type(value) is not bool
                          for key, value in case.items()) for case in cases)):
        raise ValueError("Invalid Browser observation cases")
    keys = set(cases[0])
    if (any(set(case) != keys for case in cases) or len(cases) != 2 ** len(keys)
            or len({json.dumps(case, sort_keys=True) for case in cases}) != len(cases)):
        raise ValueError("Incomplete Browser observation cases")
    if (not isinstance(plan["caseControl"], str) or not TARGET.fullmatch(plan["caseControl"])
            or not isinstance(plan["entry"], str) or not TARGET.fullmatch(plan["entry"])
            or not isinstance(pages, list) or not 1 <= len(pages) <= 19):
        raise ValueError("Invalid Browser observation pages")
    targets, identities = set(), set()
    for page in pages:
        if (not isinstance(page, dict) or set(page) != {"target", "screen", "state", "next"}
                or any(not isinstance(page[key], str) or not IDENTIFIER.fullmatch(page[key]) for key in ("screen", "state"))
                or not isinstance(page["target"], str) or not TARGET.fullmatch(page["target"])
                or page["next"] is not None and (not isinstance(page["next"], str) or not TARGET.fullmatch(page["next"]))):
            raise ValueError("Invalid Browser observation page")
        identity = page["screen"], page["state"]
        if page["target"] in targets or identity in identities:
            raise ValueError("Duplicate Browser observation page")
        targets.add(page["target"]); identities.add(identity)
    entry = next((page for page in pages if page["target"] == plan["entry"]), None)
    if entry is None or entry["state"] != "default":
        raise ValueError("Invalid Browser observation entry")
    if (not isinstance(terminals, list) or not 1 <= len(terminals) <= 19
            or len(set(terminals)) != len(terminals)
            or any((screen, "default") not in identities for screen in terminals)):
        raise ValueError("Invalid Browser observation terminals")
    return copy.deepcopy(plan)


def point(page, plan, evaluate):
    """Native isolated-world DOM observations; no application postMessage or graph text."""
    value = evaluate(page, """((input) => {
      const visible = element => {
        const r=element.getBoundingClientRect();
        if(!r.width||!r.height||!element.getClientRects().length) return false;
        for(let node=element;node;node=node.parentElement){
          const s=getComputedStyle(node);
          if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;
        }
        return true;
      };
      const found=[];
      for(const mapping of input.pages){
        const nodes=document.querySelectorAll('[data-testid="'+CSS.escape(mapping.target)+'"]');
        if(nodes.length>1) throw new Error('duplicate-observation-page');
        if(nodes.length===1&&visible(nodes[0])){
          const text=nodes[0].innerText;
          found.push({target:mapping.target,visibleText:text.slice(0,input.limit),truncated:text.length>input.limit});
        }
      }
      const controls=document.querySelectorAll('[data-testid="'+CSS.escape(input.control)+'"]');
      if(controls.length!==1||!(controls[0] instanceof HTMLSelectElement))throw new Error('observation-case-control');
      return {found,choice:controls[0].value};
    })(""" + json.dumps({"pages": plan["pages"], "control": plan["caseControl"], "limit": MAX_TEXT}) + ")")
    if not isinstance(value, dict) or len(value.get("found", [])) != 1:
        raise ValueError("Browser observation page is missing or ambiguous")
    observed = value["found"][0]
    mapped = next(item for item in plan["pages"] if item["target"] == observed["target"])
    selected = value.get("choice", "").split(":")
    if len(selected) not in (3, 4) or not selected[0].isdigit() or int(selected[0]) >= len(plan["cases"]):
        raise ValueError("Browser observation case is unavailable")
    return {**observed, "page": mapped["screen"], "state": mapped["state"],
            "case": copy.deepcopy(plan["cases"][int(selected[0])])}, mapped


def record(report, key, observed):
    if (len(report["checkpoints"]) >= MAX_POINTS
            or report["bytes"] + len(observed["visibleText"].encode()) > MAX_BYTES):
        report["incomplete"].append({"code": "observation-capacity"})
        return False
    report["checkpoints"][key] = observed
    report["bytes"] += len(observed["visibleText"].encode())
    if observed["truncated"]:
        report["incomplete"].append({"code": "observation-text-truncated", "checkpoint": key})
    return True


def journey(page, plan, index, report, *, evaluate, deadline):
    """Select only the entry, then follow actual rendered controls to an observed terminal."""
    entry = next(item for item in plan["pages"] if item["target"] == plan["entry"])
    page.get_by_test_id(plan["caseControl"]).select_option(f"{index}:{entry['screen']}:default", timeout=3000)
    page.wait_for_timeout(50)
    seen = set()
    for hop in range(MAX_HOPS):
        if time.monotonic() >= deadline:
            report["incomplete"].append({"caseIndex": index, "code": "observation-timeout"})
            return
        observed, mapped = point(page, plan, evaluate)
        if observed["case"] != plan["cases"][index] or observed["state"] != "default":
            report["incomplete"].append({"caseIndex": index, "code": "observation-case-state-changed"})
            return
        if not record(report, f"journey-{index}:{hop}", observed):
            return
        if mapped["screen"] in plan["terminals"]:
            report["ends"].append({"case": copy.deepcopy(plan["cases"][index]), "screen": mapped["screen"]})
            return
        if mapped["screen"] in seen or mapped["next"] is None:
            report["incomplete"].append({"caseIndex": index, "code": "observation-loop-or-dead-end"})
            return
        seen.add(mapped["screen"])
        page.get_by_test_id(mapped["next"]).click(timeout=3000)
        page.wait_for_timeout(50)
    report["incomplete"].append({"caseIndex": index, "code": "observation-hop-limit"})
