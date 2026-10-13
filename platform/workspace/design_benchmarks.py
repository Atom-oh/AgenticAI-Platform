"""Immutable human reference criteria and observed before/after ontology comparisons."""
from __future__ import annotations

import copy
import re

from design_loop import benchmark
from workbench.service import fields, fail, check_source_deadlines
from workspace import ontology_schema as schema
from workspace.execution_sources import ExecutionSources, unique
from workspace.ontology_sources import Sources
from workspace.storage import Conflict


def artifact(ctx, design_id, artifact_id, *, historical=False):
    """Historical inspection does not authorize generation, approval, release or export."""
    from workspace.design_api import inputs, current_inputs, read_object, observe, variant_authority
    design = ctx.get("design", design_id)
    if not historical:
        admitted, checks, _ = current_inputs(ctx, design)
        variant, stored, more, expiry = variant_authority(ctx, design, artifact_id)
        checks = unique([*checks, *more])
    else:
        admitted = inputs(ctx, design["decisionIds"])
        if admitted["admissions"] != design["admissions"]:
            fail(404, "not-found", "기준 화면의 원본 권한을 확인하지 못했습니다.")
        variant = next((item for item in design["variants"] if item["artifactId"] == artifact_id), None)
        if variant is None:
            fail(404, "not-found", "기준 화면을 찾을 수 없습니다.")
        stored = ctx.get("wb_artifact", artifact_id)
        run = ctx.get("run", variant["runId"])
        rows = [row for row in run["rounds"] if row["number"] == 1]
        if (stored.get("designId") != design_id or stored.get("jobId") != variant["jobId"]
                or run.get("designId") != design_id or len(rows) != 1
                or any(rows[0].get(key) != variant[key] for key in ("sourceHash", "bundleHash"))):
            fail(404, "not-found", "기준 화면의 근거가 다릅니다.")
        Sources(ctx)._round_permission(run, rows[0])
        checks = unique([*admitted["checks"], ctx.check("design", design),
                         ctx.check("wb_artifact", stored), ctx.check("run", run)])
        expiry = admitted["expiresAt"]
        resolver = ExecutionSources(ctx.host)
        for binding in stored.get("admissions", []):
            value = resolver(ctx.owner, binding, job={"actor": ctx.actor, "projectId": ctx.project_id,
                "authorizationExpiresAt": ctx.authorization()["authorizationExpiresAt"]})
            checks = unique([*checks, value["check"], *value.get("checks", [])])
            expiry = min(expiry, value["expiresAt"])
        if not stored.get("admissions"):
            fail(404, "not-found", "기준 화면의 입력 근거가 없습니다.")
    result = read_object(ctx, stored["result"])
    report = read_object(ctx, result["browser"])
    if (any(result.get(key) != variant[key] for key in ("sourceHash", "bundleHash"))
            or report.get("bundleHash") != variant["bundleHash"]
            or not isinstance(stored.get("profileHash"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", stored["profileHash"])):
        fail(409, "benchmark-source-changed", "관찰된 빌드·실행 근거를 확인하지 못했습니다.")
    prepared = read_object(ctx, design["flow"])
    plan = prepared["contract"].get("observationPlan")
    observed = report.get("observations")
    if (plan is None or not isinstance(observed, dict) or observed.get("schemaVersion") != 1
            or observed.get("planHash") != schema.digest(plan)
            or observed.get("status") != "complete" or observed.get("incomplete") != []
            or len(observed.get("ends", [])) != len(plan["cases"])):
        fail(409, "benchmark-observations-required", "완전한 실제 화면·경로 관찰이 필요합니다.")
    observe(ctx, checks, min(expiry, admitted["expiresAt"]))
    binding = {"designId": design_id, "artifactId": artifact_id, "sourceHash": variant["sourceHash"],
               "bundleHash": variant["bundleHash"], "generation": design["generation"],
               "model": design["model"], "profileHash": stored.get("profileHash"),
               "catalogHash": prepared["contract"]["catalogHash"], "prdHash": design["prd"]["sha256"],
               "observationPlanHash": observed["planHash"], "browserHash": result["browser"]["sha256"]}
    return binding, observed, checks, min(expiry, admitted["expiresAt"])


def definition(ctx, record):
    from workspace.design_api import read_object
    binding, observed, checks, expiry = artifact(ctx, record["designId"], record["artifactId"], historical=True)
    value = read_object(ctx, record["definition"])
    if binding != value["baseline"]:
        fail(409, "benchmark-source-changed", "기준 화면의 관찰 근거가 변경되었습니다.")
    checks = unique([*checks, ctx.check("design_reference", record)])
    from workspace.design_api import observe
    observe(ctx, checks, expiry)
    return value, observed, checks, expiry


def create(ctx, body):
    from workspace.design_api import _object
    fields(body, {"requestId", "title", "designId", "artifactId", "sourceHash", "bundleHash", "golden"})
    ctx.fresh({"owner", "designer"})
    title = schema._text(body.get("title"), 160)
    golden = body.get("golden")
    if not isinstance(golden, list) or not 1 <= len(golden) <= 64:
        fail(422, "benchmark-reference-limit", "독립 기준은 1~64개, 64KiB 이하로 정리하세요.")
    try:
        for case in golden:
            schema._fields(case, {"id", "case", "mustShow", "mustNotShow", "endsAt"})
            schema._identifier(case["id"])
            schema._identifier(case["endsAt"])
            for field in ("mustShow", "mustNotShow"):
                if not isinstance(case[field], list):
                    raise ValueError("Reference assertions must be arrays")
                for assertion in case[field]:
                    schema._fields(assertion, {"screen", "text"})
                    if field != "mustNotShow" or assertion["screen"] != "*":
                        schema._identifier(assertion["screen"])
                    schema._text(assertion["text"], 16000)
        benchmark._validate_golden(golden)
        size = len(schema.canonical(golden))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        fail(422, "benchmark-reference-invalid", "조건·필수 문구·금지 문구·도착 화면의 기준 형식을 확인하세요.")
    if size > 64 * 1024:
        fail(422, "benchmark-reference-limit", "독립 기준은 1~64개, 64KiB 이하로 정리하세요.")
    identifier = ctx.identity("reference", body.get("requestId"))
    fingerprint = schema.digest(body)
    old = ctx.storage.get(ctx.owner, "design_reference", identifier)
    if old:
        definition(ctx, old)
        if old["requestHash"] != fingerprint or old["createdBy"] != ctx.actor:
            fail(409, "request-changed", "같은 요청 ID에 다른 기준이 지정되었습니다.")
        return public(ctx, old)
    binding, observations, checks, expiry = artifact(ctx, body.get("designId"), body.get("artifactId"))
    if any(body.get(key) != binding[key] for key in ("sourceHash", "bundleHash")):
        fail(409, "benchmark-source-changed", "선택한 기준 시안의 정확한 근거를 확인하세요.")
    value = {"schemaVersion": 1, "title": title, "golden": copy.deepcopy(golden), "baseline": binding,
             "baselineScore": benchmark.score(observations, golden), "criteriaHash": schema.digest(golden)}
    record = {"id": identifier, "projectId": ctx.project_id, "title": title, "createdBy": ctx.actor,
              "designId": binding["designId"], "artifactId": binding["artifactId"], "requestHash": fingerprint,
              "definition": _object(ctx, binding["designId"], "reference", value)}
    commit(ctx, "design_reference", record, checks, expiry)
    return public(ctx, ctx.get("design_reference", identifier))


def public(ctx, record):
    value, _, _, _ = definition(ctx, record)
    return {"id": record["id"], "title": record["title"], "version": record["version"],
            "criteriaHash": value["criteriaHash"], "golden": value["golden"], "baseline": value["baseline"],
            "baselineScore": value["baselineScore"]}


def compare(ctx, identifier, body):
    from workspace.design_api import _object
    fields(body, {"requestId", "criteriaHash", "designId", "artifactId", "sourceHash", "bundleHash"})
    ctx.fresh({"owner", "designer"})
    reference = ctx.get("design_reference", identifier)
    value, before, checks, expiry = definition(ctx, reference)
    if body.get("criteriaHash") != value["criteriaHash"]:
        fail(409, "benchmark-criteria-changed", "확정한 독립 기준 버전을 다시 확인하세요.")
    current, after, more, current_expiry = artifact(ctx, body.get("designId"), body.get("artifactId"))
    if any(body.get(key) != current[key] for key in ("sourceHash", "bundleHash")):
        fail(409, "benchmark-source-changed", "재생성 결과의 정확한 근거를 확인하세요.")
    baseline = value["baseline"]
    if any(baseline.get(key) != current.get(key) for key in ("model", "profileHash", "catalogHash", "prdHash")):
        fail(409, "benchmark-scope-changed", "모델·컴포넌트·실행 프로필·상품 기준이 같은 결과를 비교하세요.")
    before_score, after_score = benchmark.score(before, value["golden"]), benchmark.score(after, value["golden"])
    result = {"referenceId": identifier, "criteriaHash": value["criteriaHash"], "before": baseline, "after": current,
              "beforeScore": before_score, "afterScore": after_score,
              "comparison": benchmark.compare(before_score, after_score), "approvalEffect": False}
    record_id = ctx.identity("comparison", body.get("requestId"))
    request_hash = schema.digest([identifier, body])
    old = ctx.storage.get(ctx.owner, "design_comparison", record_id)
    if old:
        if old["requestHash"] != request_hash or old["createdBy"] != ctx.actor:
            fail(409, "request-changed", "같은 요청 ID에 다른 비교가 지정되었습니다.")
        saved = read_comparison(ctx, old)
        return saved
    record = {"id": record_id, "projectId": ctx.project_id, "createdBy": ctx.actor,
              "referenceId": identifier, "designId": current["designId"], "artifactId": current["artifactId"],
              "requestHash": request_hash, "result": _object(ctx, current["designId"], "comparison", result)}
    commit(ctx, "design_comparison", record, unique([*checks, *more]), min(expiry, current_expiry))
    return {"id": record_id, **result}


def read_comparison(ctx, record):
    from workspace.design_api import read_object
    reference = ctx.get("design_reference", record["referenceId"])
    value, _, _, _ = definition(ctx, reference)
    current, _, checks, expiry = artifact(ctx, record["designId"], record["artifactId"], historical=True)
    result = read_object(ctx, record["result"])
    if result["criteriaHash"] != value["criteriaHash"] or result["after"] != current:
        fail(409, "benchmark-source-changed", "비교 근거를 다시 확인하세요.")
    from workspace.design_api import observe
    observe(ctx, unique([*checks, ctx.check("design_comparison", record)]), expiry)
    return {"id": record["id"], **result}


def commit(ctx, kind, value, checks, expiry):
    checks = unique([*checks, ctx.check("project", ctx.scope["project"])])
    if len(checks) + 1 > 100:
        fail(422, "benchmark-scope-limit", "원자적 권한 확인 범위를 줄여 새 기준을 만드세요.")
    try:
        ctx.storage.put_many([ctx.write(kind, value)], checks, retry_conflicts=False,
            before_attempt=lambda: check_source_deadlines(ctx.storage, checks, ctx.claims, expiry))
    except Conflict:
        fail(409, "benchmark-source-changed", "기준 또는 원본이 변경되었습니다. 다시 확인하세요.")
