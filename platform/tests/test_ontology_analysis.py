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


def test_production_cannot_silently_fall_back_to_the_offline_analyzer(wb):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "analysis", "name": "example", "files": collection(wb)})
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab, ontology_analyzer=local_analyze)
    with pytest.raises(CollaborationError, match="로컬"):
        process(worker, wb.owner, queued["job"])
    assert wb.storage.get(wb.owner, "ontology", "project-current") is None


def test_queued_source_change_and_actor_revocation_block_analysis(wb):
    wb.api.ontology_analyzer_ready = True
    queued = submit(context(wb), {"requestId": "analysis", "name": "example", "files": collection(wb)})
    row = wb.storage.get(wb.owner, "asset", "code-app")
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    worker = SimpleNamespace(storage=wb.storage, collaboration=wb.collab,
                             ontology_analyzer=lambda payload: pytest.fail("must not run"))
    with pytest.raises(CollaborationError):
        process(worker, wb.owner, queued["job"])
