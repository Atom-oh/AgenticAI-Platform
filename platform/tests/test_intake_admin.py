"""IAM-only administration of admission policy, provenance and grants (Task I2)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workspace_storage import FakeS3, FakeTable  # noqa: E402
from intake import admin_handler, records  # noqa: E402
from intake.records import INTAKE_OWNER  # noqa: E402
from workspace.storage import Storage  # noqa: E402

NOW = 1_800_000_000_000
DAY = 86_400_000
LAMBDA = SimpleNamespace(invoked_function_arn="arn:aws:lambda:ap-northeast-2:000000000000:function:IntakeAdminFn",
                         aws_request_id="req-1")


@pytest.fixture
def storage(monkeypatch):
    store = Storage(table=FakeTable(), s3=FakeS3(), bucket="private-test", clock=lambda: NOW)
    monkeypatch.setattr(admin_handler, "storage_factory", lambda: store)
    return store


def policy_body(**extra):
    return {"id": "policy-1", "scope": {"deployment": "offline-test", "projectIds": ["p1"]},
            "dataClasses": ["synthetic", "public", "internal-non-sensitive"],
            "profiles": {"inspection": "inspect-1", "normalization": "identifier-normalization-1"},
            "requiresReviewer": {"internal-non-sensitive": True},
            "trustedProvenance": {"public": True, "synthetic": True},
            "promptText": "review", "expiresAt": NOW + 30 * DAY, **extra}


def invoke(event, context=LAMBDA):
    return admin_handler.handler(event, context)


def audits(storage):
    return storage.list(INTAKE_OWNER, "adm_audit")


def test_lambda_invoke_event_writes_a_sealed_policy_and_an_audit_event(storage):
    result = invoke({"op": "put_policy", "record": policy_body(), "operator": "security-operator"})
    assert result["ok"] is True
    stored = storage.get(INTAKE_OWNER, "adm_policy", "policy-1")
    assert stored["status"] == "draft" and stored["revision"] == 1
    assert records.validate("adm_policy", stored)
    events = audits(storage)
    assert [(e["op"], e["recordId"], e["revision"]) for e in events] == [("put_policy", "policy-1", 1)]
    assert all(records.validate("adm_audit", e) for e in events)
    activated = invoke({"op": "activate_policy", "id": "policy-1", "expectedRevision": 1,
                        "operator": "security-operator"})
    assert activated["record"]["status"] == "active" and activated["record"]["revision"] == 2
    assert records.validate("adm_policy", storage.get(INTAKE_OWNER, "adm_policy", "policy-1"))
    assert {e["op"] for e in audits(storage)} == {"put_policy", "activate_policy"}


@pytest.mark.parametrize("shape", ["requestContext", "headers", "routeKey", "rawPath"])
def test_api_gateway_or_function_url_shaped_event_is_refused_without_a_write(storage, shape):
    event = {"op": "put_policy", "record": policy_body(), "operator": "security-operator",
             shape: {"http": {"method": "POST"}} if shape == "requestContext" else "x"}
    assert invoke(event) == {"error": "forbidden-transport"}
    assert storage.get(INTAKE_OWNER, "adm_policy", "policy-1") is None
    assert audits(storage) == []


def test_api_gateway_v2_event_is_refused(storage):
    event = {"version": "2.0", "routeKey": "POST /intake/policies", "rawPath": "/intake/policies",
             "headers": {"authorization": "Bearer x"},
             "requestContext": {"http": {"method": "POST"},
                                "authorizer": {"jwt": {"claims": {"sub": "alice",
                                                                  "cognito:groups": ["platform-operators"]}}}},
             "body": '{"op":"put_policy"}'}
    assert invoke(event) == {"error": "forbidden-transport"}
    assert storage.get(INTAKE_OWNER, "adm_policy", "policy-1") is None


@pytest.mark.parametrize("context", [None, SimpleNamespace(), SimpleNamespace(invoked_function_arn=""),
                                     SimpleNamespace(invoked_function_arn="https://example.test")])
def test_context_without_an_invoked_function_arn_is_refused(storage, context):
    assert invoke({"op": "put_policy", "record": policy_body(), "operator": "op"}, context) == {
        "error": "forbidden-transport"}
    assert storage.get(INTAKE_OWNER, "adm_policy", "policy-1") is None


def test_in_app_operator_claim_in_the_body_confers_nothing(storage):
    event = {"op": "put_policy", "record": policy_body(), "operator": "alice",
             "groups": ["platform-operators"]}
    # Without the IAM invoke the claim is not authority ...
    assert invoke(event, SimpleNamespace()) == {"error": "forbidden-transport"}
    # ... and with it the claim is an unknown field, not an elevation.
    assert invoke(event) == {"error": "invalid-request"}
    assert storage.get(INTAKE_OWNER, "adm_policy", "policy-1") is None
    assert audits(storage) == []


def test_stale_expected_revision_is_a_conflict(storage):
    invoke({"op": "put_policy", "record": policy_body(), "operator": "op"})
    invoke({"op": "activate_policy", "id": "policy-1", "expectedRevision": 1, "operator": "op"})
    assert invoke({"op": "retire_policy", "id": "policy-1", "expectedRevision": 1, "operator": "op"}) == {
        "error": "conflict"}
    assert storage.get(INTAKE_OWNER, "adm_policy", "policy-1")["status"] == "active"
    assert invoke({"op": "put_policy", "record": policy_body(), "operator": "op"}) == {"error": "conflict"}


def test_grant_revocation_writes_revoked_status_with_a_new_revision(storage):
    invoke({"op": "put_policy", "record": policy_body(), "operator": "op"})
    invoke({"op": "activate_policy", "id": "policy-1", "expectedRevision": 1, "operator": "op"})
    granted = invoke({"op": "grant_reviewer", "operator": "op", "record": {
        "id": "grant-1", "actor": "reviewer-sub", "policyId": "policy-1", "scope": {"projectIds": ["p1"]},
        "operations": ["review-internal"], "expiresAt": NOW + 7 * DAY}})
    assert granted["record"]["status"] == "active" and granted["record"]["revision"] == 1
    revoked = invoke({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1, "operator": "op"})
    assert revoked["record"]["status"] == "revoked" and revoked["record"]["revision"] == 2
    stored = storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    assert records.validate("adm_grant", stored)["status"] == "revoked"
    assert ("revoke_grant", "grant-1", 2) in {(e["op"], e["recordId"], e["revision"]) for e in audits(storage)}


def test_provenance_registration_requires_the_current_policy_revision(storage):
    invoke({"op": "put_policy", "record": policy_body(), "operator": "op"})
    provenance = {"id": "prov-1", "policyId": "policy-1", "policyRevision": 2, "kind": "fixture",
                  "reference": {"sourceKind": "document-revision", "sourceId": "doc-1",
                                "revision": "doc-1--r000001", "sha256": "a" * 64},
                  "scope": {"deployment": "offline-test", "projectIds": ["p1"]}, "expiresAt": NOW + DAY}
    assert invoke({"op": "register_provenance", "record": provenance, "operator": "op"}) == {
        "error": "policy-not-active"}
    invoke({"op": "activate_policy", "id": "policy-1", "expectedRevision": 1, "operator": "op"})
    registered = invoke({"op": "register_provenance", "record": provenance, "operator": "op"})
    assert registered["record"]["status"] == "active"
    revoked = invoke({"op": "revoke_provenance", "id": "prov-1", "expectedRevision": 1, "operator": "op"})
    assert revoked["record"]["status"] == "revoked"


def test_invalid_records_unknown_ops_and_caller_lifecycle_fields_never_write(storage):
    assert invoke({"op": "put_policy", "record": policy_body(promptText="auto"), "operator": "op"}) == {
        "error": "invalid-record"}
    assert invoke({"op": "put_policy", "record": policy_body(status="active"), "operator": "op"}) == {
        "error": "invalid-record"}
    assert invoke({"op": "put_policy", "record": policy_body(expiresAt=NOW - 1), "operator": "op"}) == {
        "error": "invalid-record"}
    assert invoke({"op": "delete_everything", "operator": "op"}) == {"error": "invalid-request"}
    assert invoke({"op": "put_policy", "record": policy_body()}) == {"error": "invalid-request"}
    assert storage.get(INTAKE_OWNER, "adm_policy", "policy-1") is None
    assert audits(storage) == []
