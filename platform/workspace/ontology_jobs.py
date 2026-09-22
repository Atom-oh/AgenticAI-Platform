"""Durable source-analysis jobs; source text never becomes a queue command."""
from __future__ import annotations

import copy
import hashlib

from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.ontology_analysis import source_input, project_analysis, validate_analysis, validate_execution, local_analyze, ANALYZER_ROOT
from workspace.ontology_sources import Sources, asset_reference
from workspace.ontology_store import Ontology
from ontology_runtime.dispatch import RuntimeAnalyzer, selected_backend


def reconcile(ctx, artifact):
    """Read-triggered recovery never reruns paid work or revives old authorization."""
    if artifact.get("status") not in {"queued", "processing"}:
        return artifact
    job = ctx.storage.get(ctx.owner, "job", artifact["jobId"])
    if job:
        job = ctx.host._expire_job(ctx.owner, job, ctx.scope)
    if (job and job.get("status") == "failed" or not job and
            ctx.storage.clock() - artifact.get("updatedAt", ctx.storage.clock()) > 16 * 60 * 1000):
        from workbench.worker import _mark_failed
        _mark_failed(ctx.host, ctx.owner, job or {"id": artifact["jobId"], "input": artifact["jobInput"]},
                     (job or {}).get("errorCode", "dispatch-interrupted"))
        return ctx.get("wb_artifact", artifact["id"])
    return artifact


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
        prior = reconcile(ctx, prior)
        Sources(ctx).verify(prior["sourceRefs"])
        job = ctx.storage.get(ctx.owner, "job", prior["jobId"])
        if prior["status"] == "failed" and not job:
            fail(409, "ontology-analysis-interrupted", "작업 전달이 중단되었습니다. 새 요청으로 다시 실행하세요.")
        if job and job["status"] == "queued" and prior["status"] in {"queued", "processing"}:
            if prior["jobInput"]["authorizationExpiresAt"] <= ctx.storage.clock():
                fail(401, "authorization-expired", "작업 인증이 만료되었습니다. 새 요청으로 다시 실행하세요.")
            # A crash can occur after durable creation but before invocation.
            # Re-delivery is safe: Worker.claim_job admits the analyzer once.
            job = ctx.queue_job(prior["jobId"], prior["jobInput"], prior["requestHash"])
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
    schema._identifier(profile_id)
    profiles = getattr(ctx.host, "ontology_resolver_profiles", {})
    profile = {"aliases": {}, "packages": {}, "jsonAssetFields": []} if profile_id == "default" else profiles.get(profile_id)
    if not isinstance(profile, dict):
        fail(400, "ontology-resolver-unavailable", "승인된 경로 해석 프로필을 선택하세요.")
    reader = Sources(ctx)
    checks = reader.verify(refs)
    if selected_backend(ctx.host)["name"] == "agentcore":
        if len(refs) > 40:
            fail(422, "agentcore-source-budget", "AgentCore 분석 묶음은 원본 40개 이하로 나누세요.")
        from ontology_runtime.admission import require
        checks.extend(require(ctx, ref) for ref in refs)
    current = Ontology(ctx).current()
    if (current or {}).get("generation") != body.get("expectedGeneration"):
        fail(409, "ontology-changed", "온톨로지 기준이 변경되었습니다.")
    job_id = schema.identity("ontology-job", identifier)
    pinned = {**ctx.authorization(), "operation": "ontology-analyze", "artifactId": identifier,
              "name": name, "files": copy.deepcopy(files), "sourceRefs": refs,
              "resolver": copy.deepcopy(profile), "resolverHash": schema.digest(profile),
              "expectedGeneration": body.get("expectedGeneration"), "backend": selected_backend(ctx.host)}
    artifact = {"id": identifier, "projectId": ctx.project_id, "kind": "ontology-analysis",
                "status": "queued", "name": name, "sourceRefs": refs, "jobId": job_id, "jobInput": pinned,
                "createdBy": ctx.actor, "requestId": body["requestId"], "requestHash": schema.digest(body)}
    saved = ctx.commit([ctx.write("wb_artifact", artifact)], checks)[0]
    return {"artifact": saved, "job": ctx.queue_job(job_id, pinned, saved["requestHash"])}


def process(ctx, pinned, job=None):
    artifact = ctx.get("wb_artifact", pinned["artifactId"])
    if artifact.get("kind") != "ontology-analysis" or artifact["jobInput"] != pinned:
        fail(409, "ontology-job-mismatch", "분석 작업의 승인 입력이 다릅니다.")
    if artifact["status"] == "completed":
        return {"artifactId": artifact["id"], "generation": artifact["generation"]}
    if artifact["status"] not in {"queued", "processing"}:
        fail(409, "ontology-job-state", "분석 가능한 작업 상태가 아닙니다.")
    job = job or ctx.get("job", artifact["jobId"])
    if (job["id"] != artifact["jobId"] or job.get("input") != pinned
            or job.get("task") != "workbench" or job.get("status") not in {"queued", "running"}):
        fail(409, "ontology-job-state", "현재 실행 작업과 승인 입력이 다릅니다.")
    analyzer = getattr(ctx.host, "ontology_analyzer", None)
    remote = type(analyzer) is RuntimeAnalyzer
    if not remote and not callable(analyzer):
        fail(503, "ontology-analyzer-unavailable", "구성된 소스 분석기를 호출할 수 없습니다.")
    if pinned.get("backend") != selected_backend(ctx.host):
        fail(409, "ontology-backend-changed", "요청 당시의 분석 실행 환경이 변경되었습니다. 새 작업을 시작하세요.")
    if not remote and analyzer is not local_analyze:
        fail(503, "ontology-analysis-backend", "검증된 분석 실행 환경이 필요합니다.")
    backend = "agentcore-code-interpreter" if remote else "local-offline"
    if not remote and not getattr(ctx.host, "allow_offline_ontology_analysis", False):
        fail(503, "ontology-analysis-backend", "운영 분석을 로컬 실행으로 대체할 수 없습니다.")
    expected = ({"toolArchiveHash": analyzer.archive} if remote else {
        "analyzerCodeHash": hashlib.sha256((ANALYZER_ROOT / "analyze.cjs").read_bytes()).hexdigest(),
        "dependencyLockHash": hashlib.sha256((ANALYZER_ROOT / "package-lock.json").read_bytes()).hexdigest()})
    store = Ontology(ctx)
    current, _ = store.authorize_publication(pinned["name"])
    if (current or {}).get("generation") != pinned["expectedGeneration"]:
        fail(409, "ontology-changed", "온톨로지 기준이 변경되었습니다.")
    refs = Sources(ctx)
    refs.verify(pinned["sourceRefs"])
    payload, bindings = source_input(ctx, pinned["files"], pinned["resolver"])
    if schema.digest(payload["resolver"]) != pinned["resolverHash"]:
        fail(409, "ontology-resolver-changed", "분석 프로필이 변경되었습니다.")
    if (len(pinned["files"]) != len(pinned["sourceRefs"]) or any(
            bindings[file["path"]]["ref"] != ref for file, ref in zip(pinned["files"], pinned["sourceRefs"]))):
        fail(409, "ontology-source-changed", "분석 요청의 원본 버전이 변경되었습니다.")
    refs.recheck()
    result = analyzer.analyze(ctx, pinned, payload) if remote else analyzer(payload)
    if not isinstance(result, dict) or not isinstance(result.get("execution"), dict) or "analysis" not in result:
        fail(503, "ontology-analysis-incomplete", "분석 실행 근거를 확인하지 못했습니다.")
    validate_execution(result["execution"])
    if result["execution"]["backend"] != backend:
        fail(503, "ontology-analysis-backend", "구성된 분석 환경과 실행 근거가 다릅니다.")
    if (result["execution"]["inputHash"] != schema.digest(payload)
            or any(result["execution"].get(key) != digest for key, digest in expected.items())):
        fail(409, "ontology-analysis-integrity", "분석 실행 근거가 요청·도구 버전과 다릅니다.")
    validate_analysis(payload, result["analysis"])
    graph = project_analysis(ctx, pinned["name"], payload, bindings, result["analysis"])
    refs.recheck()
    key, digest = ctx.put_json("wb_artifact", artifact["id"], "analysis.json", result)
    graph_key, graph_hash = ctx.put_json("wb_artifact", artifact["id"], "candidate.json", graph)

    def completion(marker):
        refs.recheck()
        result = {"artifactId": artifact["id"], "generation": marker["generation"],
                  "coverage": result_coverage}
        return [ctx.write("wb_artifact", {**artifact, "status": "completed",
            "analysisKey": key, "analysisHash": digest, "graphKey": graph_key, "graphHash": graph_hash,
            "execution": execution, "coverage": result_coverage,
            "generation": marker["generation"], "partitionId": marker["partitionId"],
            "identities": marker["identities"]}, artifact["version"]),
            ctx.write("job", {**job, "status": "completed", "progress": 100, "result": result}, job["version"])]

    execution, result_coverage = result["execution"], result["analysis"]["coverage"]
    from workspace.collaboration import CollaborationError
    for attempt in range(3):
        try:
            published = store.publish_candidate(pinned["name"], graph,
                expected_generation=pinned["expectedGeneration"], request_id=artifact["id"],
                _producer="parser-extracted", _completion_writes=completion)
            break
        except CollaborationError as error:
            if error.code != "conflict" or attempt == 2:
                raise
            # Retry only publication, never the paid analysis. Membership,
            # source, artifact and graph fences must all still be current.
            refs.recheck()
            store = Ontology(ctx)
    return {"artifactId": artifact["id"], "generation": published["generation"],
            "coverage": result["analysis"]["coverage"]}
