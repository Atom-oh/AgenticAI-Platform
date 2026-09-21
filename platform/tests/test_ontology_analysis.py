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


def test_actual_node_parser_projects_exact_source_refs_and_unclassified_components(wb):
    payload, bindings = source_input(context(wb), collection(wb))
    result = local_analyze(payload)
    graph = project_analysis(context(wb), "example", payload, bindings, result["analysis"])
    assert result["execution"]["backend"] == "local-offline"
    assert any(node["type"] == "Component" and node["title"] == "Button" for node in graph["nodes"])
    assert any(node["type"] == "Foundation" for node in graph["nodes"])
    assert any(edge["type"] == "IMPLEMENTS" for edge in graph["edges"])
    assert all(node["reviewState"] == "candidate" for node in graph["nodes"])
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


def test_analysis_retry_after_publication_returns_the_same_receipt(wb):
    wb.api.ontology_analyzer_ready = True
    body = {"requestId": "retry", "name": "example", "files": collection(wb)}
    queued = submit(context(wb), body)
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze,
                             allow_offline_ontology_analysis=True)
    process(worker, wb.owner, queued["job"])
    assert submit(context(wb), body)["artifact"]["status"] == "completed"


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


def test_queued_source_change_and_actor_revocation_block_analysis(wb):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "analysis", "name": "example", "files": collection(wb)})
    row = wb.storage.get(wb.owner, "asset", "code-app")
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab,
                             ontology_analyzer=lambda payload: pytest.fail("must not run"))
    with pytest.raises(CollaborationError):
        process(worker, wb.owner, queued["job"])
