import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_analysis import collection
from test_ontology_sources import context
from intake import admin_handler
from ontology_runtime.authorization import active_job
from ontology_runtime.capability import AuthorizationDenied
from ontology_runtime.dispatch import RuntimeAnalyzer
from ontology_runtime.admission import classify
from workspace.collaboration import CollaborationError
from workspace.ontology_jobs import submit
from workspace.ontology_sources import asset_reference

INTAKE_DEPLOYMENT = "offline-test"
_ADMIN_LAMBDA = SimpleNamespace(
    invoked_function_arn="arn:aws:lambda:ap-northeast-2:180294183052:function:IntakeAdminFn")


def _admin(storage, monkeypatch, event):
    monkeypatch.setattr(admin_handler, "storage_factory", lambda: storage)
    result = admin_handler.handler({"operator": "security-operator", **event}, _ADMIN_LAMBDA)
    assert result.get("ok"), result
    return result["record"]


def authorize_admission(wb, monkeypatch, refs, *, data_class="synthetic"):
    """Register the real IAM-administered policy/provenance a `require()` call now demands.

    Mirrors `Env.policy`/`Env.provenance` in test_intake_admission.py (B0 intake):
    no ontology_runtime fixture may dispatch on a bare self-asserted label alone.
    """
    monkeypatch.setenv("INTAKE_DEPLOYMENT", INTAKE_DEPLOYMENT)
    draft = _admin(wb.storage, monkeypatch, {"op": "put_policy", "record": {
        "id": "ontology-admission-policy", "scope": {"deployment": INTAKE_DEPLOYMENT, "projectIds": [wb.project["id"]]},
        "dataClasses": ["synthetic", "public", "internal-non-sensitive"],
        "profiles": {"inspection": "inspect-1", "normalization": "identifier-normalization-1"},
        "requiresReviewer": {"internal-non-sensitive": True},
        "trustedProvenance": {"public": True, "synthetic": True},
        "promptText": "review", "expiresAt": wb.storage.clock() + 60 * 86_400_000}})
    policy = _admin(wb.storage, monkeypatch,
                     {"op": "activate_policy", "id": draft["id"], "expectedRevision": draft["revision"]})
    if data_class in ("synthetic", "public"):
        for index, ref in enumerate(refs):
            _admin(wb.storage, monkeypatch, {"op": "register_provenance", "record": {
                "id": f"ontology-admission-provenance-{index}", "policyId": policy["id"],
                "policyRevision": policy["revision"], "kind": "fixture" if data_class == "synthetic" else "public-reference",
                "reference": {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256")},
                "scope": {"deployment": INTAKE_DEPLOYMENT, "projectIds": [wb.project["id"]]},
                "expiresAt": wb.storage.clock() + 30 * 86_400_000}})
    else:
        _admin(wb.storage, monkeypatch, {"op": "grant_reviewer", "record": {
            "id": "ontology-admission-grant", "actor": "alice", "policyId": policy["id"],
            "scope": {"projectIds": [wb.project["id"]]}, "operations": ["review-internal"],
            "expiresAt": wb.storage.clock() + 30 * 86_400_000}})
    return policy


def admitted(wb, monkeypatch, *, data_class="synthetic"):
    wb.api.ontology_analyzer_ready = True
    wb.api.ontology_analyzer = RuntimeAnalyzer(
        "arn:aws:lambda:ap-northeast-2:180294183052:function:synthetic-authority:1", "a" * 64)
    files = collection(wb)
    refs = [asset_reference(wb.storage.get(wb.owner, "asset", file["assetId"])) for file in files]
    authorize_admission(wb, monkeypatch, refs, data_class=data_class)
    for file, ref in zip(files, refs):
        classify(context(wb), {"requestId": file["assetId"], "sourceRef": ref,
            "classification": data_class, "reason": "Synthetic test fixture"})
    queued = submit(context(wb), {"requestId": "runtime", "name": "runtime", "files": files})
    wb.storage.claim_job(wb.owner, queued["job"]["id"])
    return queued["artifact"]


@pytest.mark.parametrize("mutation", ["cancel", "artifact", "membership", "actor", "expired", "title"])
def test_runtime_boundary_checks_the_original_job_and_audience(wb, monkeypatch, mutation):
    artifact = admitted(wb, monkeypatch)
    if mutation == "cancel":
        job = wb.storage.get(wb.owner, "job", artifact["jobId"])
        wb.storage.put(wb.owner, "job", {**job, "status": "cancelled"}, job["version"])
    elif mutation == "artifact":
        wb.storage.put(wb.owner, "wb_artifact", {**artifact, "status": "failed"}, artifact["version"])
    elif mutation in {"membership", "actor", "title"}:
        project = wb.storage.get(wb.owner, "project", wb.project["id"])
        if mutation == "membership":
            project["members"].pop("bob")
        elif mutation == "actor":
            project["members"]["alice"]["role"] = "designer"
        else:
            project["title"] = "Content-only update"
        wb.storage.put(wb.owner, "project", project, project["version"])
    deadline = wb.now // 1000 if mutation == "expired" else wb.now // 1000 + 60
    if mutation == "title":
        ctx, _, _, _, checks = active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"], deadline=deadline)
        assert ctx.claims["exp"] == deadline
        assert {row["kind"] for row in checks} == {"wb_artifact", "job"}
    else:
        with pytest.raises((AuthorizationDenied, CollaborationError)):
            active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"], deadline=deadline)


def test_runtime_job_cancellation_during_a_tool_transaction_cannot_commit(wb, monkeypatch):
    artifact = admitted(wb, monkeypatch)
    ctx, _, job, sources, checks = active_job(wb.storage, wb.collab, wb.project["id"], artifact["id"])
    checks.extend(sources.verify(artifact["sourceRefs"]))
    def cancel():
        wb.storage.put(wb.owner, "job", {**job, "status": "cancelled"}, job["version"])
    wb.storage.table().before_transaction = cancel
    with pytest.raises(CollaborationError) as error:
        ctx.commit([ctx.write("ac_operation", {"id": "cancel-race", "ttl": wb.now // 1000 + 60})], checks)
    assert error.value.code == "conflict"
    assert wb.storage.get(wb.owner, "ac_operation", "cancel-race") is None


def test_runtime_retention_is_bounded_and_preserved(wb):
    now = wb.now // 1000
    for kind in ("ac_execution", "ac_operation"):
        row = wb.storage.put(wb.owner, kind, {"id": "retained", "ttl": now + 86400})
        assert row["ttl"] == now + 86400
        for expiry in (None, True, now, now + 32 * 86400):
            with pytest.raises(ValueError, match="retention"):
                wb.storage.put(wb.owner, kind, {"id": "invalid", "ttl": expiry})


def test_classify_requires_authority_to_exist_now_and_pins_its_exact_revision(wb, monkeypatch):
    """PR #22 review 2, finding 1, race (a): a classification recorded before any
    policy/provenance/grant exists must never be grandfathered in once authority
    appears afterward, with no fresh review. The authority check now runs inside
    `classify()` itself (not only `require()`), so the call simply fails closed
    while no authority exists -- a later `classify()` call, once authority is
    registered, is the fresh review that binds and pins it."""
    files = collection(wb)
    source = wb.storage.get(wb.owner, "asset", files[0]["assetId"])
    ref = asset_reference(source)
    body = {"requestId": files[0]["assetId"], "sourceRef": ref,
            "classification": "synthetic", "reason": "No authority yet"}
    with pytest.raises(CollaborationError) as error:
        classify(context(wb), body)
    assert error.value.code == "agentcore-admission-authority-missing"
    assert wb.storage.list_page(wb.owner, "ac_admission")["items"] == []
    policy = authorize_admission(wb, monkeypatch, [ref], data_class="synthetic")
    record = classify(context(wb), body)
    assert record["binding"]["policy"] == {"id": policy["id"], "revision": policy["revision"], "hash": policy["hash"]}
    assert "provenance" in record["binding"]


def test_classification_binds_workbench_document_identity():
    from ontology_runtime.admission import identifier
    reference = {"sourceKind": "workbench-document", "sourceId": "source", "revision": "v1",
                 "sha256": "a" * 64, "audienceRevision": "1", "location": {"documentId": "first"},
                 "allowedRoles": ["owner"]}
    other = {**reference, "location": {"documentId": "second"}}
    assert identifier(reference) != identifier(other)
    assert identifier(reference) == identifier({**reference, "location": {**reference["location"], "page": 1}})
