"""Versioned UX change scope. Imported identities are claims, never approvals."""
from __future__ import annotations

import hashlib
import re
from datetime import date

SOURCE_PATH = re.compile(r"src/(?:App\.tsx|pages/[a-z][a-z0-9-]*\.tsx|logic/[a-z][a-z0-9-]*\.ts)\Z")
ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


def text(value, maximum=500):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError("변경 요청의 텍스트 길이와 내용을 확인하세요.")
    return value.strip()


def normalize_request(value):
    from workspace.rules import normalize_required_states
    if not isinstance(value, dict) or value.get("kind") not in ("new", "change", "fix"):
        raise ValueError("업무 요청은 신규·변경·수정 중 하나로 지정하세요.")
    result = {"kind": value["kind"], **{key: text(value.get(key, ""), maximum) for key, maximum in
              (("channel", 120), ("requester", 120), ("dueDate", 10), ("baselineNote", 2000), ("preserve", 2000))}}
    if result["dueDate"]:
        try:
            if date.fromisoformat(result["dueDate"]).isoformat() != result["dueDate"]:
                raise ValueError()
        except ValueError:
            raise ValueError("목표일은 YYYY-MM-DD 날짜로 입력하세요.") from None
    baseline = value.get("baseline")
    if baseline is not None:
        if (not isinstance(baseline, dict) or not isinstance(baseline.get("runId"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", baseline["runId"])
                or type(baseline.get("round")) is not int or not 1 <= baseline["round"] <= 5
                or not isinstance(baseline.get("sourceHash"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", baseline["sourceHash"])):
            raise ValueError("기준 시안의 실행·라운드·소스 해시를 함께 선택하세요.")
        result["baseline"] = {key: baseline[key] for key in ("runId", "round", "sourceHash")}
    allowed = value.get("allowedFiles", [])
    if (not isinstance(allowed, list) or len(allowed) > 24
            or any(not isinstance(path, str) or not SOURCE_PATH.fullmatch(path) or path == "src/logic/assets.ts" for path in allowed)
            or len(set(allowed)) != len(allowed)):
        raise ValueError("변경할 파일은 허용된 React 화면·로직 경로를 중복 없이 지정하세요.")
    result["allowedFiles"] = sorted(allowed)
    screens = value.get("screens", [])
    if not isinstance(screens, list) or len(screens) > 20:
        raise ValueError("한 작업의 화면·팝업·슬롯은 최대 20개입니다.")
    result["screens"], ids = [], set()
    for screen in screens:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str) or not ID.fullmatch(screen["id"]) or screen["id"] in ids:
            raise ValueError("화면 식별자는 중복 없는 영문 소문자·숫자·하이픈으로 지정하세요.")
        ids.add(screen["id"])
        if screen.get("kind") not in ("page", "bottom-sheet", "popup", "tab", "slot"):
            raise ValueError("화면·바텀시트·팝업·탭·슬롯 유형을 확인하세요.")
        if screen.get("change") not in ("add", "modify", "keep", "remove"):
            raise ValueError("화면의 추가·수정·유지·제외 범위를 확인하세요.")
        clean = {key: screen[key] for key in ("id", "kind", "change")}
        clean.update({key: text(screen.get(key, ""), limit) for key, limit in
                      (("title", 180), ("instruction", 1000), ("uiuxId", 128), ("developerId", 128),
                       ("canonicalId", 128), ("sourceNote", 1000))})
        if not clean["title"]:
            raise ValueError("화면 이름을 입력하세요.")
        clean["states"] = normalize_required_states(screen.get("states", []))
        if "sourceRefs" in screen:
            from workspace.guidelines import normalize_refs
            refs = screen["sourceRefs"]
            if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
                raise ValueError("화면의 원문 연결을 확인하세요.")
            clean["sourceRefs"] = normalize_refs(refs, [ref.get("assetId") for ref in refs])
        result["screens"].append(clean)
    transitions = value.get("transitions", [])
    if not isinstance(transitions, list) or len(transitions) > 30:
        raise ValueError("화면 연결은 최대 30개입니다.")
    result["transitions"], links = [], set()
    for transition in transitions:
        if (not isinstance(transition, dict) or not isinstance(transition.get("id"), str)
                or not ID.fullmatch(transition["id"]) or transition["id"] in links
                or not isinstance(transition.get("from"), str) or not isinstance(transition.get("to"), str)
                or transition["from"] not in ids or transition["to"] not in ids):
            raise ValueError("화면 연결의 식별자와 출발·도착 화면을 확인하세요.")
        links.add(transition["id"])
        clean = {key: transition[key] for key in ("id", "from", "to")}
        clean.update({key: text(transition.get(key, ""), 1000) for key in ("action", "condition", "retention")})
        result["transitions"].append(clean)
    return result


def coverage_issues(contract):
    """A generic error rule must not cover every screen or transition."""
    request = contract.get("changeRequest")
    if not request:
        return []
    issues = []
    rules = [rule for rule in contract.get("rules", []) if rule.get("required", True)]
    if not request["screens"]:
        issues.append("변경 대상 화면·슬롯을 하나 이상 정하세요.")
    for screen in request["screens"]:
        if screen["change"] == "remove":
            continue
        if not screen["states"]:
            issues.append(f"{screen['title']}: 확인할 상태를 선택하세요.")
        for state in screen["states"]:
            matching = [rule for rule in rules if rule.get("screenId") == screen["id"] and rule.get("scenario") == state]
            if not any(any(step["action"] == "expectVisible" and step["target"] == screen["id"] and step.get("value") is True
                           for step in rule["steps"]) for rule in matching):
                issues.append(f"{screen['title']} · {state}: 해당 화면 표시를 포함한 필수 규칙이 필요합니다.")
    for transition in request["transitions"]:
        if not transition["action"] or not transition["condition"] or not transition["retention"]:
            issues.append(f"{transition['id']}: 이동 행동·조건·값 유지 기준을 정하세요.")
        def observes_link(rule):
            steps = rule["steps"]
            start = next((i for i, step in enumerate(steps) if step["action"] == "expectVisible"
                          and step["target"] == transition["from"] and step.get("value") is True), None)
            end = next((i for i, step in enumerate(steps) if step["action"] == "expectVisible"
                        and step["target"] == transition["to"] and step.get("value") is True and
                        (start is not None and i > start)), None)
            return start is not None and end is not None and any(
                step["action"] in ("click", "press", "select", "check") for step in steps[start + 1:end])
        if not any(rule.get("transitionId") == transition["id"] and observes_link(rule) for rule in rules):
            issues.append(f"{transition['id']}: 출발 화면 → 조작 → 도착 화면을 확인하는 필수 규칙이 필요합니다.")
    if request.get("baseline") and not request["allowedFiles"]:
        issues.append("기준 시안에서 변경할 파일을 선택하세요.")
    return issues


def baseline_project(storage, owner, request):
    """Resolve within the authenticated storage scope; never accept source bytes."""
    from workspace.react_artifacts import read_archive
    reference = request.get("baseline")
    if not reference:
        return None
    run = storage.get(owner, "run", reference["runId"])
    if not run:
        raise ValueError("현재 작업 공간에서 기준 시안을 찾을 수 없습니다.")
    row = next((row for row in run.get("rounds", []) if row["number"] == reference["round"]), None)
    approval = run.get("approval") or {}
    contract = storage.get(owner, "contract", run.get("contractId"))
    if (run.get("outputType") != "react" or not row or row.get("passed") is not True
            or approval.get("round") != reference["round"] or not contract or contract.get("status") != "approved"
            or contract["version"] != run["contractVersion"]
            or (contract.get("approval") or {}).get("hash") != run.get("contractHash")
            or any(not row.get(key) or approval.get(key) != row[key] for key in ("sourceHash", "bundleHash", "catalogHash"))):
        raise ValueError("기준 시안의 현재 승인과 소스 버전을 확인하세요.")
    if row["sourceHash"] != reference["sourceHash"]:
        raise ValueError("선택한 기준 소스가 바뀌었습니다. 기준 시안을 다시 확인하세요.")
    key = row.get("sourceKey")
    if not key or not storage.owns_key(owner, key) or storage.blob_info(key)["size"] > 8_000_000:
        raise ValueError("기준 소스의 비공개 경로·크기를 확인하세요.")
    source = storage.get_blob(key)
    if hashlib.sha256(source).hexdigest() != row.get("sourceArchiveSha256"):
        raise ValueError("기준 소스 파일이 변경되었습니다.")
    return read_archive(source, row["sourceHash"])


def file_changes(before, after):
    result = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        def digest(value):
            return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest() if value is not None else None
        result.append({"path": path, "change": "added" if old is None else "deleted" if new is None else "modified",
                       "beforeSha256": digest(old), "afterSha256": digest(new)})
    return result


def enforce_scope(request, baseline, files):
    changes = file_changes(baseline, files)
    if request.get("baseline"):
        extra = [item["path"] for item in changes if item["path"] not in request["allowedFiles"]]
        if extra:
            raise ValueError("승인 범위 밖의 파일을 변경했습니다: " + ", ".join(extra))
    return changes
