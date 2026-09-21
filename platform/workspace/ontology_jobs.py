"""Durable source-analysis jobs; source text never becomes a queue command."""
from __future__ import annotations

import copy

from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.ontology_analysis import source_input, project_analysis, validate_analysis, validate_execution
from workspace.ontology_sources import Sources, asset_reference
from workspace.ontology_store import Ontology


def submit(ctx, body):
    fields(body, {"requestId", "name", "files", "resolverProfileId", "expectedGeneration"})
    ctx.fresh()
    ctx.host._worker_ready()
    if not getattr(ctx.host, "ontology_analyzer_ready", False):
        fail(503, "ontology-analyzer-unavailable", "소스 분석 실행 환경이 아직 구성되지 않았습니다.")
    name = schema._identifier(body.get("name"))
    store = Ontology(ctx)
    store.authorize_publication(name)
    identifier = ctx.identity("wb_artifact", body.get("requestId"))
    prior = ctx.existing("wb_artifact", identifier, body)
    if prior:
        Sources(ctx).verify(prior["sourceRefs"])
        job = ctx.storage.get(ctx.owner, "job", prior["jobId"])
        return {"artifact": prior, "job": job or ctx.queue_job(prior["jobId"], prior["jobInput"], prior["requestHash"])}
    files = body.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 100:
        fail(422, "ontology-analysis-limit", "분석 단위는 파일 1~100개로 구성하세요.")
    refs, seen = [], set()
    for file in files:
        fields(file, {"assetId", "path"})
        schema._identifier(file.get("assetId"))
        path = schema._text(file.get("path"), 500)
        if path in seen:
            fail(400, "ontology-analysis-path", "분석 파일 경로가 중복됩니다.")
        seen.add(path)
        refs.append(asset_reference(ctx.get("asset", file["assetId"])))
    profile_id = body.get("resolverProfileId", "default")
    profiles = getattr(ctx.host, "ontology_resolver_profiles", {})
    profile = {"aliases": {}, "packages": {}, "jsonAssetFields": []} if profile_id == "default" else profiles.get(profile_id)
    if not isinstance(profile, dict):
        fail(400, "ontology-resolver-unavailable", "승인된 경로 해석 프로필을 선택하세요.")
    reader = Sources(ctx)
    checks = reader.verify(refs)
    current = Ontology(ctx).current()
    if (current or {}).get("generation") != body.get("expectedGeneration"):
        fail(409, "ontology-changed", "온톨로지 기준이 변경되었습니다.")
    job_id = schema.identity("ontology-job", identifier)
    pinned = {**ctx.authorization(), "operation": "ontology-analyze", "artifactId": identifier,
              "name": name, "files": copy.deepcopy(files), "sourceRefs": refs,
              "resolver": copy.deepcopy(profile), "resolverHash": schema.digest(profile),
              "expectedGeneration": body.get("expectedGeneration")}
    artifact = {"id": identifier, "projectId": ctx.project_id, "kind": "ontology-analysis",
                "status": "queued", "name": name, "sourceRefs": refs, "jobId": job_id, "jobInput": pinned,
                "createdBy": ctx.actor, "requestHash": schema.digest(body)}
    saved = ctx.commit([ctx.write("wb_artifact", artifact)], checks)[0]
    return {"artifact": saved, "job": ctx.queue_job(job_id, pinned, saved["requestHash"])}


def process(ctx, pinned):
    artifact = ctx.get("wb_artifact", pinned["artifactId"])
    if artifact.get("kind") != "ontology-analysis" or artifact["jobInput"] != pinned:
        fail(409, "ontology-job-mismatch", "분석 작업의 승인 입력이 다릅니다.")
    if artifact["status"] == "completed":
        return {"artifactId": artifact["id"], "generation": artifact["generation"]}
    if artifact["status"] not in {"queued", "processing"}:
        fail(409, "ontology-job-state", "분석 가능한 작업 상태가 아닙니다.")
    analyzer = getattr(ctx.host, "ontology_analyzer", None)
    if not callable(analyzer):
        fail(503, "ontology-analyzer-unavailable", "구성된 소스 분석기를 호출할 수 없습니다.")
    backend = getattr(analyzer, "backend", None)
    if backend == "local-offline" and not getattr(ctx.host, "allow_offline_ontology_analysis", False):
        fail(503, "ontology-analysis-backend", "운영 분석을 로컬 실행으로 대체할 수 없습니다.")
    if backend not in {"local-offline", "agentcore-code-interpreter"}:
        fail(503, "ontology-analysis-backend", "검증된 분석 실행 환경이 필요합니다.")
    store = Ontology(ctx)
    current, _ = store.authorize_publication(pinned["name"])
    if (current or {}).get("generation") != pinned["expectedGeneration"]:
        fail(409, "ontology-changed", "온톨로지 기준이 변경되었습니다.")
    refs = Sources(ctx)
    refs.verify(pinned["sourceRefs"])
    payload, bindings = source_input(ctx, pinned["files"], pinned["resolver"])
    if schema.digest(payload["resolver"]) != pinned["resolverHash"]:
        fail(409, "ontology-resolver-changed", "분석 프로필이 변경되었습니다.")
    result = analyzer(payload)
    if not isinstance(result, dict) or not isinstance(result.get("execution"), dict) or "analysis" not in result:
        fail(503, "ontology-analysis-incomplete", "분석 실행 근거를 확인하지 못했습니다.")
    validate_execution(result["execution"])
    if result["execution"]["backend"] != backend:
        fail(503, "ontology-analysis-backend", "구성된 분석 환경과 실행 근거가 다릅니다.")
    validate_analysis(payload, result["analysis"])
    graph = project_analysis(ctx, pinned["name"], payload, bindings, result["analysis"])
    refs.recheck()
    key, digest = ctx.put_json("wb_artifact", artifact["id"], "analysis.json", result)
    graph_key, graph_hash = ctx.put_json("wb_artifact", artifact["id"], "candidate.json", graph)

    def completion(marker):
        refs.recheck()
        return [ctx.write("wb_artifact", {**artifact, "status": "completed",
            "analysisKey": key, "analysisHash": digest, "graphKey": graph_key, "graphHash": graph_hash,
            "execution": result["execution"], "coverage": result["analysis"]["coverage"],
            "generation": marker["generation"], "partitionId": marker["partitionId"],
            "identities": marker["identities"]}, artifact["version"])]

    published = store.publish_candidate(pinned["name"], graph,
        expected_generation=pinned["expectedGeneration"], request_id=artifact["id"],
        _producer="parser-extracted", _completion_writes=completion)
    return {"artifactId": artifact["id"], "generation": published["generation"],
            "coverage": result["analysis"]["coverage"]}
