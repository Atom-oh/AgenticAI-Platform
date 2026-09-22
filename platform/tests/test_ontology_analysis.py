import copy
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_sources import context
from workspace.ontology_analysis import source_input, local_analyze, project_analysis, validate_analysis
from workspace.ontology_jobs import submit
from workspace.ontology_store import Ontology
from workspace.collaboration import CollaborationError
from workbench.worker import process


def collection(wb):
    data = {
        "App.tsx": ("code-app", "import {Button} from './Button';import hero from './hero.png';export default ()=> <><Button/><img src={hero}/></>"),
        "Button.tsx": ("code-button", "export function Button(){return <button>Next</button>}"),
        "hero.png": ("image-hero", b"synthetic-reference-bytes"),
    }
    files = []
    for path, (identifier, content) in data.items():
        raw = content.encode() if isinstance(content, str) else content
        key = wb.storage.key_for(wb.owner, "asset", identifier, "original")
        wb.storage.put_blob_once(key, raw, "application/octet-stream")
        wb.storage.put(wb.owner, "asset", {"id": identifier, "projectId": wb.project["id"], "name": path,
            "originalKey": key, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            "uploadStatus": "stored", "parseStatus": "complete", "importRevision": 1})
        files.append({"assetId": identifier, "path": path})
    return files


@pytest.mark.parametrize("change", ["other-role", "actor-role", "remove-member", "title"])
def test_queued_analysis_pins_membership_but_allows_project_title_edits(wb, change, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "audience", "name": "audience", "files": collection(wb)})
    project = wb.storage.get(wb.owner, "project", wb.project["id"])
    if change == "other-role":
        project["members"]["bob"]["role"] = "developer"
    elif change == "actor-role":
        project["members"]["alice"]["role"] = "designer"
    elif change == "remove-member":
        project["members"].pop("bob")
    else:
        project["title"] = "New project title"
    wb.storage.put(wb.owner, "project", project, project["version"])
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    if change == "title":
        assert process(worker, wb.owner, queued["job"])["artifactId"] == queued["artifact"]["id"]
    else:
        from workspace import ontology_jobs
        monkeypatch.setattr(ontology_jobs, "source_input", lambda *args: pytest.fail("changed audience reached analysis"))
        with pytest.raises(CollaborationError) as error:
            process(worker, wb.owner, queued["job"])
        assert error.value.code == "ontology-authority-changed"
        assert wb.storage.get(wb.owner, "ontology", "project-current") is None


def test_actual_node_parser_projects_exact_source_refs_and_unclassified_components(wb):
    payload, bindings = source_input(context(wb), collection(wb))
    result = local_analyze(payload)
    graph = project_analysis(context(wb), "example", payload, bindings, result["analysis"])
    assert result["execution"]["backend"] == "local-offline"
    assert any(node["type"] == "Component" and node["title"] == "Button" for node in graph["nodes"])
    assert any(node["type"] == "Foundation" for node in graph["nodes"])
    assert any(edge["type"] == "IMPLEMENTS" for edge in graph["edges"])
    assert all(node["reviewState"] == "candidate" for node in graph["nodes"])
    for item in [*graph["nodes"], *graph["edges"]]:
        for reference in item["sourceRefs"]:
            binding = next(value["ref"] for value in bindings.values() if value["ref"]["sourceId"] == reference["sourceId"])
            assert all(reference[key] == value for key, value in binding.items())
            if reference.get("location", {}).get("path"):
                assert bindings[reference["location"]["path"]]["ref"] == binding
    from workspace.ontology_impact import analyze
    from workspace import ontology_schema as schema
    symbol = next(node for node in graph["nodes"] if node["type"] == "CodeSymbol" and node["title"] == "Button")
    generation = schema.digest(graph)
    impact = analyze(graph, {"id": "symbol-change", "kind": "code", "nodeIds": [symbol["id"]],
        "baseGeneration": generation}, generation=generation, can_read=lambda refs: True)
    assert "App.tsx" in {item["title"] for item in impact["items"]}
    tampered = copy.deepcopy(result["analysis"])
    tampered["inputHash"] = "0" * 64
    with pytest.raises(CollaborationError):
        validate_analysis(payload, tampered)


def test_repeated_jsx_usages_preserve_every_observed_location(wb):
    files = collection(wb)
    original = wb.storage.get(wb.owner, "asset", "code-app")
    raw = b'import {Button} from "./Button";export const App=()=> <><Button/><Button/></>;'
    key = wb.storage.key_for(wb.owner, "asset", "code-app", "repeat.tsx")
    wb.storage.put_blob_once(key, raw, "text/plain")
    wb.storage.put(wb.owner, "asset", {**original, "originalKey": key, "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw), "importRevision": 2}, original["version"])
    payload, bindings = source_input(context(wb), files)
    graph = project_analysis(context(wb), "repeat", payload, bindings, local_analyze(payload)["analysis"])
    app = next(node["id"] for node in graph["nodes"] if node["type"] == "CodeFile" and node["title"] == "App.tsx")
    usages = [edge for edge in graph["edges"] if edge["src"]["id"] == app and edge["type"] == "USES"]
    assert len(usages) == 2
    assert len({edge["sourceRefs"][0]["location"]["column"] for edge in usages}) == 2


def test_analysis_is_a_durable_job_and_local_backend_requires_explicit_test_opt_in(wb):
    files = collection(wb)
    with pytest.raises(CollaborationError, match="구성"):
        submit(context(wb), {"requestId": "analysis", "name": "example", "files": files})
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "analysis", "name": "example", "files": files})
    assert queued["artifact"]["status"] == "queued"
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    result = process(worker, wb.owner, queued["job"])
    assert result["artifactId"] == queued["artifact"]["id"]
    saved = wb.storage.get(wb.owner, "wb_artifact", result["artifactId"])
    assert saved["status"] == "completed"
    assert saved["execution"]["backend"] == "local-offline"
    graph = Ontology(context(wb)).read()
    assert graph["nodes"] and graph["edges"]
    assert all(node["provenance"] == "parser-extracted" for node in graph["nodes"])
    assert wb.storage.get(wb.owner, "wb_index", "current") is None


def test_production_cannot_silently_fall_back_to_the_offline_analyzer(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "analysis", "name": "example", "files": collection(wb)})
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze)
    monkeypatch.setattr("workspace.ontology_analysis.subprocess.run", lambda *a, **k: pytest.fail("local execution prohibited"))
    with pytest.raises(CollaborationError, match="로컬"):
        process(worker, wb.owner, queued["job"])
    assert wb.storage.get(wb.owner, "ontology", "project-current") is None


def test_receipt_conflict_cannot_publish_a_parser_partition(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "atomic", "name": "example", "files": collection(wb)})
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    original = wb.storage.put_many

    def conflict(writes, *args, **kwargs):
        if any(w["kind"] == "ontology" for w in writes):
            assert any(w["kind"] == "wb_artifact" and w["item"]["status"] == "completed" for w in writes)
            row = wb.storage.get(wb.owner, "wb_artifact", queued["artifact"]["id"])
            wb.storage.put(wb.owner, "wb_artifact", {**row, "status": "cancelled"}, row["version"])
        return original(writes, *args, **kwargs)

    monkeypatch.setattr(wb.storage, "put_many", conflict)
    with pytest.raises(CollaborationError):
        process(worker, wb.owner, queued["job"])
    assert wb.storage.get(wb.owner, "ontology", "project-current") is None


def test_analysis_retry_after_publication_returns_the_same_receipt(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    body = {"requestId": "retry", "name": "example", "files": collection(wb)}
    queued = submit(context(wb), body)
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    process(worker, wb.owner, queued["job"])
    saved = wb.storage.get(wb.owner, "wb_artifact", queued["artifact"]["id"])
    job = wb.storage.get(wb.owner, "job", queued["job"]["id"])
    monkeypatch.setattr(wb.api, "_invoke", lambda *args: pytest.fail("completed work must not redispatch"))
    retried = submit(context(wb), body)
    assert retried["artifact"] == saved and retried["job"] == job


def test_backend_labels_cannot_replace_the_exact_trusted_analyzer(wb):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "lookalike", "name": "example", "files": collection(wb)})
    def lookalike(payload):
        pytest.fail("untrusted callable executed")
    lookalike.backend = "local-offline"
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=lookalike,
                             allow_offline_ontology_analysis=True)
    with pytest.raises(CollaborationError) as error:
        process(worker, wb.owner, queued["job"])
    assert error.value.code == "ontology-analysis-backend"


@pytest.mark.parametrize("field", ["inputHash", "analyzerCodeHash", "dependencyLockHash"])
def test_execution_hash_tampering_is_rejected_before_publication(wb, monkeypatch, field):
    from workspace import ontology_jobs
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "tampered-" + field, "name": "example", "files": collection(wb)})
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    validate = ontology_jobs.validate_execution
    def tamper(value):
        value[field] = "0" * 64
        return validate(value)
    monkeypatch.setattr(ontology_jobs, "validate_execution", tamper)
    with pytest.raises(CollaborationError) as error:
        process(worker, wb.owner, queued["job"])
    assert error.value.code == "ontology-analysis-integrity"
    assert wb.storage.get(wb.owner, "ontology", "project-current") is None


def test_real_offline_analyzer_process_has_no_inherited_aws_credentials(wb, monkeypatch):
    from workspace import ontology_analysis
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "synthetic-test-sentinel")
    original = ontology_analysis.subprocess.run
    def inspect(*args, **kwargs):
        assert not any(name.startswith("AWS_") for name in kwargs["env"])
        return original(*args, **kwargs)
    monkeypatch.setattr(ontology_analysis.subprocess, "run", inspect)
    payload, _ = source_input(context(wb), collection(wb))
    assert local_analyze(payload)["execution"]["sourceExecuted"] is False


def test_retry_redelivers_a_durable_job_after_a_crash_before_invocation(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    body = {"requestId": "lost-dispatch", "name": "example", "files": collection(wb)}

    def crash(*args):
        raise SystemExit("synthetic process termination after durable job creation")

    monkeypatch.setattr(wb.api, "_invoke", crash)
    with pytest.raises(SystemExit):
        submit(context(wb), body)
    artifact = wb.storage.get(wb.owner, "wb_artifact", context(wb).identity("wb_artifact", body["requestId"]))
    job = wb.storage.get(wb.owner, "job", artifact["jobId"])
    assert artifact["status"] == job["status"] == "queued"
    deliveries = []
    monkeypatch.setattr(wb.api, "_invoke", lambda owner, saved: deliveries.append(saved["id"]))
    retried = submit(context(wb), body)
    assert retried["job"]["id"] == job["id"] and deliveries == [job["id"]]
    assert len(wb.storage.list(wb.owner, "wb_artifact")) == 1


def test_missing_job_cannot_be_dispatched_after_the_pinned_authorization_expires(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    ctx = context(wb)
    ctx.claims["exp"] = wb.now // 1000 + 1
    body = {"requestId": "expired-orphan", "name": "example", "files": collection(wb)}
    monkeypatch.setattr(wb.api, "_new_job", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic interruption")))
    with pytest.raises(RuntimeError):
        submit(ctx, body)
    wb.now += 2000
    wb.storage.clock = lambda: wb.now
    monkeypatch.setattr(wb.api, "_new_job", lambda *args: pytest.fail("expired dispatch"))
    with pytest.raises(CollaborationError) as error:
        submit(context(wb), body)
    assert error.value.code == "ontology-analysis-interrupted"
    artifact = wb.storage.get(wb.owner, "wb_artifact", context(wb).identity("wb_artifact", body["requestId"]))
    assert artifact["status"] == "failed" and artifact["errorCode"] == "authorization-expired"


def test_optional_generation_pins_the_current_manifest(wb):
    from test_ontology_store import candidate
    initial = Ontology(context(wb)).publish_candidate("existing", candidate(wb),
        expected_generation=None, request_id="existing")
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "implicit-generation", "name": "example", "files": collection(wb)})
    assert queued["artifact"]["jobInput"]["expectedGeneration"] == initial["generation"]


def test_ambiguous_dispatch_does_not_fail_an_artifact_after_the_worker_claims_it(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    body = {"requestId": "accepted-dispatch", "name": "example", "files": collection(wb)}

    def accepted(owner, job):
        assert wb.storage.claim_job(owner, job["id"])
        raise TimeoutError("synthetic timeout after Lambda accepted the delivery")

    monkeypatch.setattr(wb.api, "_invoke", accepted)
    with pytest.raises(TimeoutError):
        submit(context(wb), body)
    artifact = wb.storage.get(wb.owner, "wb_artifact", context(wb).identity("wb_artifact", body["requestId"]))
    assert artifact["status"] == "queued"
    assert wb.storage.get(wb.owner, "job", artifact["jobId"])["status"] == "running"


def test_large_valid_projection_returns_an_actionable_scope_error(wb):
    files = collection(wb)
    source = wb.storage.get(wb.owner, "asset", "code-app")
    raw = "\n".join(f"export const symbol{i}={i};" for i in range(501)).encode()
    key = wb.storage.key_for(wb.owner, "asset", source["id"], "many-symbols")
    wb.storage.put_blob_once(key, raw, "text/plain")
    wb.storage.put(wb.owner, "asset", {**source, "originalKey": key, "sha256": hashlib.sha256(raw).hexdigest(),
                                     "size": len(raw), "importRevision": 2}, source["version"])
    payload, bindings = source_input(context(wb), files)
    result = local_analyze(payload)
    with pytest.raises(CollaborationError) as error:
        project_analysis(context(wb), "large", payload, bindings, result["analysis"])
    assert error.value.status == 422 and error.value.code == "ontology-analysis-scope"


def test_sibling_file_change_preserves_unchanged_file_revision_and_node_hash(wb):
    wb.api.ontology_analyzer_ready = True
    files = collection(wb)
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    queued = submit(context(wb), {"requestId": "first-unit", "name": "example", "files": files})
    process(worker, wb.owner, queued["job"])
    initial = Ontology(context(wb)).read()
    old = {node["properties"]["path"]: node for node in initial["nodes"] if node["type"] == "CodeFile"}
    source = wb.storage.get(wb.owner, "asset", "code-app")
    raw = wb.storage.get_blob(source["originalKey"]) + b"\nexport const added = true;"
    key = wb.storage.key_for(wb.owner, "asset", source["id"], "revision-2")
    wb.storage.put_blob_once(key, raw, "text/plain")
    wb.storage.put(wb.owner, "asset", {**source, "originalKey": key, "sha256": hashlib.sha256(raw).hexdigest(),
                                     "size": len(raw), "importRevision": 2}, source["version"])
    queued = submit(context(wb), {"requestId": "second-unit", "name": "example", "files": files,
                                  "expectedGeneration": initial["generation"]})
    process(worker, wb.owner, queued["job"])
    current = {node["properties"]["path"]: node for node in Ontology(context(wb)).read()["nodes"] if node["type"] == "CodeFile"}
    assert current["Button.tsx"] == old["Button.tsx"]
    assert current["App.tsx"]["revision"] == old["App.tsx"]["revision"] + 1
    assert all("analyzerHash" not in node["properties"] for node in current.values())


def test_accepted_analysis_list_recovers_ids_without_exposing_inaccessible_sources(wb):
    from test_ontology_api import call as api_call
    wb.api.ontology_analyzer_ready = True
    body = {"requestId": "recover-list", "name": "example", "files": collection(wb)}
    queued = submit(context(wb), body)
    status, page = api_call(wb, "GET", "/analyses")
    assert status == 200 and len(page["items"]) == 1
    saved = page["items"][0]
    assert saved["id"] == queued["artifact"]["id"] and saved["jobId"] == queued["job"]["id"]
    assert saved["requestId"] == body["requestId"] and saved["status"] == "queued"
    assert "jobInput" not in saved and "sourceRefs" not in saved
    source = wb.storage.get(wb.owner, "asset", "code-app")
    wb.storage.put(wb.owner, "asset", {**source, "accessRevoked": True}, source["version"])
    status, page = api_call(wb, "GET", "/analyses")
    assert status == 200 and page["items"] == []


def test_denied_partition_never_invokes_analyzer(wb):
    wb.api.ontology_analyzer_ready = True
    with pytest.raises(CollaborationError) as error:
        submit(context(wb), {"requestId": "denied", "name": "product-protected", "files": collection(wb)})
    assert error.value.code == "ontology-managed-partition"


def test_korean_paths_and_exports_have_identical_python_node_hashes(wb):
    files = collection(wb)
    files[0]["path"] = "화면.tsx"
    payload, bindings = source_input(context(wb), files)
    result = local_analyze(payload)
    assert project_analysis(context(wb), "korean", payload, bindings, result["analysis"])["nodes"]


def test_source_replaced_between_verification_and_loading_never_runs_analysis(wb, monkeypatch):
    from workspace import ontology_jobs
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "race", "name": "example", "files": collection(wb)})
    original = ontology_jobs.source_input

    def changed(ctx, files, resolver):
        row = wb.storage.get(wb.owner, "asset", "code-app")
        key = wb.storage.key_for(wb.owner, "asset", row["id"], "new-original")
        raw = b"export default ()=>null;"
        wb.storage.put_blob_once(key, raw, "text/plain")
        wb.storage.put(wb.owner, "asset", {**row, "originalKey": key, "sha256": hashlib.sha256(raw).hexdigest(),
                       "size": len(raw), "importRevision": 2}, row["version"])
        return original(ctx, files, resolver)

    monkeypatch.setattr(ontology_jobs, "source_input", changed)
    monkeypatch.setattr("workspace.ontology_analysis.subprocess.run", lambda *a, **k: pytest.fail("must not execute changed source"))
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    with pytest.raises(CollaborationError) as error:
        process(worker, wb.owner, queued["job"])
    assert error.value.code == "ontology-source-changed"


def test_unrelated_project_commit_during_analysis_does_not_discard_the_result(wb, monkeypatch):
    from workspace import ontology_analysis
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "collaborative", "name": "example", "files": collection(wb)})
    original = ontology_analysis.subprocess.run

    def analyze(*args, **kwargs):
        result = original(*args, **kwargs)
        row = wb.storage.get(wb.owner, "project", wb.project["id"])
        wb.storage.put(wb.owner, "project", {**row, "name": "Updated project title"}, row["version"])
        return result

    monkeypatch.setattr(ontology_analysis.subprocess, "run", analyze)
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    result = process(worker, wb.owner, queued["job"])
    assert wb.storage.get(wb.owner, "wb_artifact", result["artifactId"])["status"] == "completed"


def test_real_worker_claim_prevents_duplicate_analyzer_delivery(wb, monkeypatch):
    from workspace.worker import Worker
    from workspace import ontology_analysis
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "delivery", "name": "example", "files": collection(wb)})
    worker = Worker(storage=wb.storage)
    worker.collaboration = wb.collab
    worker.ontology_analyzer = local_analyze
    worker.allow_offline_ontology_analysis = True
    event = {"owner": wb.owner, "jobId": queued["job"]["id"]}
    original, calls = ontology_analysis.subprocess.run, []

    def overlap(*args, **kwargs):
        calls.append(1)
        assert worker.handle(event)["status"] == "duplicate-or-unavailable"
        return original(*args, **kwargs)

    monkeypatch.setattr(ontology_analysis.subprocess, "run", overlap)
    worker.handle(event)
    assert worker.handle(event)["status"] == "duplicate-or-unavailable"
    assert len(calls) == 1


def test_real_worker_records_matching_job_and_artifact_failure_without_execution(wb, monkeypatch):
    from workspace.worker import Worker
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "failure-state", "name": "example", "files": collection(wb)})
    worker = Worker(storage=wb.storage)
    worker.collaboration = wb.collab
    worker.ontology_analyzer = local_analyze
    monkeypatch.setattr("workspace.ontology_analysis.subprocess.run", lambda *a, **k: pytest.fail("offline execution"))
    assert worker.handle({"owner": wb.owner, "jobId": queued["job"]["id"]})["status"] == "failed"
    job = wb.storage.get(wb.owner, "job", queued["job"]["id"])
    artifact = wb.storage.get(wb.owner, "wb_artifact", queued["artifact"]["id"])
    assert job["status"] == artifact["status"] == "failed"
    assert job["errorCode"] == artifact["errorCode"] == "ontology-analysis-backend"


def test_interrupted_claim_becomes_a_terminal_failure_on_job_or_artifact_read(wb):
    from test_ontology_api import call as api_call
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "interrupted", "name": "example", "files": collection(wb)})
    assert wb.storage.claim_job(wb.owner, queued["job"]["id"])
    wb.now += 17 * 60 * 1000
    wb.storage.clock = lambda: wb.now
    status, result = api_call(wb, "GET", "/analyses/" + queued["artifact"]["id"])
    assert status == 200
    assert result["artifact"]["status"] == "failed"
    assert wb.storage.get(wb.owner, "job", queued["job"]["id"])["errorCode"] == "job-timeout"
    assert wb.storage.claim_job(wb.owner, queued["job"]["id"]) is None


def test_timeout_artifact_repair_retries_after_job_is_already_failed(wb, monkeypatch):
    from workbench import worker
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "repair", "name": "example", "files": collection(wb)})
    wb.now += 17 * 60 * 1000
    wb.storage.clock = lambda: wb.now
    original = worker._mark_failed
    monkeypatch.setattr(worker, "_mark_failed", lambda *args: None)
    expired = wb.api._expire_job(wb.owner, queued["job"], context(wb).scope)
    assert expired["status"] == "failed"
    assert wb.storage.get(wb.owner, "wb_artifact", queued["artifact"]["id"])["status"] == "queued"
    monkeypatch.setattr(worker, "_mark_failed", original)
    wb.api._expire_job(wb.owner, expired, context(wb).scope)
    assert wb.storage.get(wb.owner, "wb_artifact", queued["artifact"]["id"])["status"] == "failed"


def test_orphaned_artifact_is_reconciled_without_dispatching_paid_work(wb, monkeypatch):
    from test_ontology_api import call as api_call
    wb.api.ontology_analyzer_ready = True
    body = {"requestId": "orphan", "name": "example", "files": collection(wb)}
    def interrupted(*args, **kwargs):
        raise RuntimeError("synthetic process interruption before job creation")
    monkeypatch.setattr(wb.api, "_new_job", interrupted)
    with pytest.raises(RuntimeError):
        submit(context(wb), body)
    identifier = context(wb).identity("wb_artifact", "orphan")
    wb.now += 17 * 60 * 1000
    wb.storage.clock = lambda: wb.now
    status, result = api_call(wb, "GET", "/analyses/" + identifier)
    assert status == 200 and result["artifact"]["status"] == "failed"
    with pytest.raises(CollaborationError) as error:
        submit(context(wb), body)
    assert error.value.code == "ontology-analysis-interrupted"


def test_queued_source_change_blocks_the_real_configured_analyzer(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "analysis", "name": "example", "files": collection(wb)})
    row = wb.storage.get(wb.owner, "asset", "code-app")
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab,
                             ontology_analyzer=local_analyze, allow_offline_ontology_analysis=True)
    monkeypatch.setattr("workspace.ontology_analysis.subprocess.run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(CollaborationError) as error:
        process(worker, wb.owner, queued["job"])
    assert error.value.code == "ontology-source-stale"


def test_revoked_actor_cannot_start_the_real_configured_analyzer(wb, monkeypatch):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "revoke-actor", "name": "example", "files": collection(wb)})
    project = wb.storage.get(wb.owner, "project", wb.project["id"])
    wb.storage.put(wb.owner, "project", {**project, "members": {
        key: member for key, member in project["members"].items() if key != "alice"}}, project["version"])
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab,
                             ontology_analyzer=local_analyze, allow_offline_ontology_analysis=True)
    monkeypatch.setattr("workspace.ontology_analysis.subprocess.run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(CollaborationError) as error:
        process(worker, wb.owner, queued["job"])
    assert error.value.status == 403


def test_terminal_job_and_partition_publish_in_one_transaction_without_later_update(wb, monkeypatch):
    from workspace.worker import Worker
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "job-atomic", "name": "example", "files": collection(wb)})
    worker = Worker(storage=wb.storage)
    worker.collaboration = wb.collab
    worker.ontology_analyzer = local_analyze
    worker.allow_offline_ontology_analysis = True
    original, publications = wb.storage.put_many, []

    def observe(writes, *args, **kwargs):
        if any(write["kind"] == "ontology" for write in writes):
            terminal = next(write for write in writes if write["kind"] == "job")
            assert terminal["item"]["status"] == "completed"
            assert any(write["kind"] == "wb_artifact" and write["item"]["status"] == "completed" for write in writes)
            publications.append(terminal["item"]["result"])
        return original(writes, *args, **kwargs)

    monkeypatch.setattr(wb.storage, "put_many", observe)
    monkeypatch.setattr(worker, "_update", lambda *a, **k: pytest.fail("unfenced terminal write"))
    assert worker.handle({"owner": wb.owner, "jobId": queued["job"]["id"]})["status"] == "completed"
    job = wb.storage.get(wb.owner, "job", queued["job"]["id"])
    assert publications == [job["result"]]
    assert job["result"]["generation"] == wb.storage.get(wb.owner, "ontology", "project-current")["generation"]
