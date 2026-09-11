"""Validated, non-executable test contracts approved by the designer."""
from __future__ import annotations

import hashlib
import json
import re

TARGET = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
TEXT_ACTIONS = {"fill", "select", "expectText", "expectValue", "expectStyle"}
BOOL_ACTIONS = {"check", "expectVisible", "expectEnabled", "expectChecked"}
EXPECT_ACTIONS = {"expectText", "expectValue", "expectVisible", "expectEnabled", "expectChecked", "expectStyle"}
ACTIONS = TEXT_ACTIONS | BOOL_ACTIONS | {"click", "press"}
KEYS = {"Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space"}
STYLE_PROPERTIES = {"color", "backgroundColor", "fontSize", "fontWeight", "fontFamily", "borderRadius",
                    "padding", "margin", "gap", "minHeight", "height", "width", "borderColor", "borderWidth", "display"}
CRITERIA_IDS = ("projectId", "productId", "guidelineId", "guidelineAssetId")
CRITERIA_HASHES = ("catalogHash", "ontologyHash")


def _text(value, label: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f"{label}: 1~{maximum}자의 텍스트가 필요합니다.")
    return value


def _integer(value, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label}: {low}~{high} 범위의 정수가 필요합니다.")
    return value


def validate_contract(data: dict, asset_texts: dict[str, str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("동작 규칙은 객체여야 합니다.")
    if type(data.get("schemaVersion", 1)) is not int or data.get("schemaVersion", 1) != 1:
        raise ValueError("지원하지 않는 동작 규칙 버전입니다.")
    asset_ids = data.get("assetIds", [])
    if (not isinstance(asset_ids, list) or len(asset_ids) > 20
            or any(not isinstance(x, str) or not x or len(x) > 128 for x in asset_ids)
            or len(set(asset_ids)) != len(asset_ids)):
        raise ValueError("입력 자산은 중복 없이 최대 20개까지 선택하세요.")
    viewport = data.get("viewport", {"width": 390, "height": 844})
    if not isinstance(viewport, dict):
        raise ValueError("화면 크기가 올바르지 않습니다.")
    normalized = {
        "schemaVersion": 1,
        "title": _text(data.get("title", "동작 규칙"), "규칙 이름", 180),
        "brief": _text(data.get("brief", ""), "화면 설명", 4000, empty=True),
        "assetIds": list(asset_ids),
        "viewport": {
            "width": _integer(viewport.get("width"), "화면 너비", 320, 1920),
            "height": _integer(viewport.get("height"), "화면 높이", 480, 2160),
        },
        "rules": [],
        "unresolved": [],
        "bindings": {},
    }
    for name in CRITERIA_IDS:
        if name in data:
            value = data[name]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
                raise ValueError(f"{name}: 기준 버전 식별자가 올바르지 않습니다.")
            normalized[name] = value
    for name in CRITERIA_HASHES:
        if name in data:
            if not isinstance(data[name], str) or not re.fullmatch(r"[a-f0-9]{64}", data[name]):
                raise ValueError(f"{name}: 기준 코드/온톨로지 해시가 올바르지 않습니다.")
            normalized[name] = data[name]
    if any(name in normalized for name in CRITERIA_IDS):
        if not all(name in normalized for name in (*CRITERIA_IDS, *CRITERIA_HASHES)):
            raise ValueError("프로젝트·상품·가이드·컴포넌트 기준을 함께 고정해야 합니다.")
        if normalized["guidelineAssetId"] not in asset_ids:
            raise ValueError("확정된 기획 가이드가 입력 자산에 포함되어야 합니다.")
    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict) or len(bindings) > 100:
        raise ValueError("원본 요소 연결은 최대 100개까지 지정할 수 있습니다.")
    for target, selector in bindings.items():
        if (not isinstance(target, str) or not TARGET.fullmatch(target) or not isinstance(selector, str)
                or not selector.strip() or len(selector) > 300 or ">>" in selector
                or any(char in selector for char in "\r\n\x00") or selector.startswith(("xpath=", "text=", "javascript:"))):
            raise ValueError("원본 요소는 안전한 CSS 선택자로 연결하세요.")
        normalized["bindings"][target] = selector
    unresolved = data.get("unresolved", [])
    if not isinstance(unresolved, list) or len(unresolved) > 30:
        raise ValueError("미정의 요구사항은 최대 30개까지 기록할 수 있습니다.")
    normalized["unresolved"] = [_text(x, "미정의 요구사항", 1000) for x in unresolved]
    rules = data.get("rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 20:
        raise ValueError("검증할 동작 규칙을 1~20개 작성하세요.")
    ids = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("각 동작 규칙은 객체여야 합니다.")
        rid = _text(rule.get("id"), "규칙 ID", 80)
        if rid in ids or not TARGET.fullmatch(rid):
            raise ValueError("규칙 ID는 중복 없는 영문 식별자여야 합니다.")
        ids.add(rid)
        if not isinstance(rule.get("required", True), bool):
            raise ValueError("필수 여부는 참/거짓으로 지정하세요.")
        source = rule.get("source", {"kind": "manual"})
        if not isinstance(source, dict) or source.get("kind") not in ("explicit", "inferred", "manual"):
            raise ValueError("규칙의 출처를 명시/추정/직접 작성으로 구분하세요.")
        clean_source = {"kind": source["kind"]}
        if source.get("assetId"):
            if source["assetId"] not in asset_ids:
                raise ValueError("규칙 출처는 선택한 반입 파일이어야 합니다.")
            clean_source["assetId"] = source["assetId"]
        if source["kind"] == "explicit":
            if not clean_source.get("assetId"):
                raise ValueError("명시된 규칙에는 출처 파일이 필요합니다.")
            quote = _text(source.get("quote"), "원문 근거", 2000)
            if asset_texts is not None:
                original = asset_texts.get(source["assetId"], "")
                # Whitespace normalization only; no fabricated or fuzzy quotes.
                if " ".join(quote.split()) not in " ".join(original.split()):
                    raise ValueError(f"{rid}: 원문 근거가 선택한 파일에 없습니다.")
            clean_source["quote"] = quote
        elif source.get("quote"):
            clean_source["quote"] = _text(source["quote"], "참고 설명", 2000)
        if "page" in source:
            clean_source["page"] = _integer(source["page"], "출처 페이지", 1, 10000)
        steps = rule.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
            raise ValueError(f"{rid}: 조작과 확인 단계를 1~20개 지정하세요.")
        clean_steps = []
        for step in steps:
            if not isinstance(step, dict) or step.get("action") not in ACTIONS:
                raise ValueError(f"{rid}: 지원하지 않는 검증 동작입니다.")
            action, target = step["action"], step.get("target")
            if not isinstance(target, str) or not TARGET.fullmatch(target):
                raise ValueError(f"{rid}: 대상은 안전한 data-testid 식별자여야 합니다.")
            clean = {"action": action, "target": target,
                     "targetLabel": _text(step.get("targetLabel", target), "대상 이름", 180)}
            if action in TEXT_ACTIONS:
                clean["value"] = _text(step.get("value"), "입력/기대값", 2000, empty=True)
            elif action in BOOL_ACTIONS:
                if not isinstance(step.get("value"), bool):
                    raise ValueError(f"{rid}: {action} 값은 참/거짓이어야 합니다.")
                clean["value"] = step["value"]
            elif action == "press":
                if step.get("value") not in KEYS:
                    raise ValueError(f"{rid}: 지원하지 않는 키 입력입니다.")
                clean["value"] = step["value"]
            if action == "expectText":
                match = step.get("match", "contains")
                if match not in ("contains", "equals"):
                    raise ValueError("문구 비교는 포함 또는 일치만 지원합니다.")
                clean["match"] = match
                if "normalizeWhitespace" in step:
                    if type(step["normalizeWhitespace"]) is not bool:
                        raise ValueError("공백 정규화 여부는 참/거짓이어야 합니다.")
                    clean["normalizeWhitespace"] = step["normalizeWhitespace"]
                if not clean["value"]:
                    raise ValueError("문구 확인의 기대값을 비워둘 수 없습니다.")
            if action == "expectStyle":
                if step.get("property") not in STYLE_PROPERTIES or not clean["value"].strip():
                    raise ValueError("스타일 검사는 지원하는 속성과 기대값을 지정하세요.")
                clean["property"] = step["property"]
            clean_steps.append(clean)
        if not any(s["action"] in EXPECT_ACTIONS for s in clean_steps):
            raise ValueError(f"{rid}: 실제 결과를 확인하는 단계가 필요합니다.")
        normalized["rules"].append({
            "id": rid, "title": _text(rule.get("title"), "동작 설명", 500),
            "required": rule.get("required", True), "source": clean_source, "steps": clean_steps,
        })
    if not any(r["required"] for r in normalized["rules"]):
        raise ValueError("최소 한 개의 필수 동작 규칙이 필요합니다.")
    if not any(step["action"] in EXPECT_ACTIONS and
               (step["action"] != "expectVisible" or step["value"] is True)
               for rule in normalized["rules"] for step in rule["steps"]):
        raise ValueError("실제로 존재하는 화면 요소를 확인하는 단계를 최소 하나 추가하세요.")
    return normalized


def contract_hash(contract: dict) -> str:
    normalized = validate_contract(contract)
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def report_passes(contract: dict, report: dict, *, visual_required: bool = False) -> bool:
    """A passing boolean alone is never sufficient evidence."""
    if (not isinstance(report, dict) or report.get("passed") is not True
            or report.get("functionalStatus") != "pass"
            or report.get("networkRequests") != [] or report.get("consoleErrors") != []
            or report.get("blockingFindings") != []):
        return False
    accessibility, visual = report.get("accessibility") or {}, report.get("visual") or {}
    if accessibility.get("status") != "pass" or accessibility.get("incomplete"):
        return False
    if visual.get("status") not in (("pass",) if visual_required else ("pass", "not-run")):
        return False
    rules, checks = contract.get("rules", []), report.get("checks")
    if not rules or not isinstance(checks, list) or len(checks) != len(rules):
        return False
    by_id = {}
    for check in checks:
        if not isinstance(check, dict) or check.get("caseId") in by_id or check.get("status") not in ("pass", "fail"):
            return False
        by_id[check.get("caseId")] = check
    for rule in rules:
        check = by_id.get(rule["id"])
        if not check or (rule.get("required", True) and check["status"] != "pass"):
            return False
        if check["status"] == "pass":
            actual = check.get("steps")
            if not isinstance(actual, list) or len(actual) != len(rule["steps"]):
                return False
            for index, (step, evidence) in enumerate(zip(rule["steps"], actual)):
                if (not isinstance(evidence, dict) or evidence.get("index") != index
                        or evidence.get("status") != "pass"
                        or evidence.get("action") != step["action"] or evidence.get("target") != step["target"]
                        or evidence.get("expected") != step.get("value")
                        or (step["action"] == "expectStyle" and evidence.get("property") != step["property"])):
                    return False
    return True
