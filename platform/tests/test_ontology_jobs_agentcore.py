"""`ontology_jobs.process` never executes AgentCore work itself (PR #22 review 1, #3).

AGENTCORE_CONTRACT.md's `platform-execution/1` is explicit: "Source analysis
using AgentCore is a new-ledger Runtime execution, not a cloud analyzer
injected into legacy `ontology_jobs.process`." A `RuntimeAnalyzer`-backed job
must stay queued for the separate, already-correctly-fenced
`ontology_runtime.authority.Authority.dispatch()` path; the legacy Worker/
`process()` completion boundary must never publish a generation for it,
because that completion does not re-fence the admissions `Authority.dispatch()`
consumed (the reproduced bug: revoking `ac_admission` right after `Authority`
returns still let the old code publish and complete the artifact/job).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workbench_core import wb  # noqa: F401
from test_agentcore_authorization import admitted
from test_ontology_sources import context
from workspace.collaboration import CollaborationError
from workspace.ontology_analysis import local_analyze
from workspace.ontology_jobs import process


def test_process_never_dispatches_or_completes_an_agentcore_backed_job(wb, monkeypatch):
    """`process()` must stay offline-only; an AgentCore job can never complete through it."""
    artifact = admitted(wb, monkeypatch)
    job = wb.storage.get(wb.owner, "job", artifact["jobId"])
    assert artifact["jobInput"]["backend"]["name"] == "agentcore"
    with pytest.raises(CollaborationError) as error:
        process(context(wb), artifact["jobInput"], job)
    assert error.value.code == "ontology-analyzer-unavailable"
    unchanged = wb.storage.get(wb.owner, "wb_artifact", artifact["id"])
    assert unchanged["status"] != "completed"
    assert "execution" not in unchanged


def test_process_rejects_an_agentcore_pinned_job_even_under_offline_opt_in(wb, monkeypatch):
    """PR #22 review 2, minor finding 5: round 1's fix restored `process()` to the
    offline-only analyzer guard but dropped the backend-pinning check alongside it.
    A job admitted/pinned for AgentCore execution must never silently fall back to
    the legacy offline analyzer, even under explicit offline opt-in (ROLL-02)."""
    artifact = admitted(wb, monkeypatch)
    job = wb.storage.get(wb.owner, "job", artifact["jobId"])
    assert artifact["jobInput"]["backend"]["name"] == "agentcore"
    wb.api.ontology_analyzer_ready = True
    wb.api.ontology_analyzer = local_analyze
    wb.api.allow_offline_ontology_analysis = True
    with pytest.raises(CollaborationError) as error:
        process(context(wb), artifact["jobInput"], job)
    assert error.value.code == "ontology-analysis-backend"
    unchanged = wb.storage.get(wb.owner, "wb_artifact", artifact["id"])
    assert unchanged["status"] != "completed"
    assert "execution" not in unchanged


def test_process_still_refuses_an_arbitrary_callable_offline(wb):
    """Non-AgentCore path keeps its existing offline-only guard (no regression)."""
    from test_ontology_analysis import collection
    from workspace.ontology_jobs import submit

    files = collection(wb)
    wb.api.ontology_analyzer_ready = True
    wb.api.ontology_analyzer = lambda payload: {"analysis": {}, "execution": {}}  # not local_analyze
    queued = submit(context(wb), {"requestId": "offline", "name": "offline", "files": files})
    wb.storage.claim_job(wb.owner, queued["job"]["id"])
    job = wb.storage.get(wb.owner, "job", queued["artifact"]["jobId"])
    with pytest.raises(CollaborationError) as error:
        process(context(wb), queued["artifact"]["jobInput"], job)
    assert error.value.code == "ontology-analysis-backend"
