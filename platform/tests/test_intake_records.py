"""source-admission/1 closed record schemas (Task I1)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake import records  # noqa: E402

SHA = "a" * 64
NOW = 1_800_000_000_000
DAY = 86_400_000


def policy(**extra):
    return {"id": "policy-1", "revision": 1,
            "scope": {"deployment": "offline-test", "projectIds": ["p1"]},
            "dataClasses": ["synthetic", "public", "internal-non-sensitive"],
            "profiles": {"inspection": "inspect-1", "normalization": "identifier-normalization-1"},
            "requiresReviewer": {"internal-non-sensitive": True},
            "trustedProvenance": {"public": True, "synthetic": True},
            "promptText": "review", "status": "active", "expiresAt": NOW + DAY, **extra}


def provenance(**extra):
    return {"id": "prov-1", "revision": 1, "policyId": "policy-1", "policyRevision": 1,
            "kind": "public-reference",
            "reference": {"sourceKind": "document-revision", "sourceId": "doc-1",
                          "revision": "doc-1--r000001", "sha256": SHA},
            "publicUrl": "https://example.test/reference",
            "scope": {"deployment": "offline-test", "projectIds": ["p1"]},
            "status": "active", "expiresAt": NOW + DAY, **extra}


def grant(**extra):
    return {"id": "grant-1", "revision": 1, "actor": "reviewer-sub", "policyId": "policy-1",
            "scope": {"projectIds": ["p1"]}, "operations": ["review-internal"],
            "status": "active", "expiresAt": NOW + DAY, **extra}


def decision(**extra):
    return {"id": "decision-1", "revision": 1, "projectId": "p1",
            "source": {"sourceKind": "document-revision", "sourceId": "doc-1",
                       "revision": "doc-1--r000001", "sha256": SHA, "audienceRevision": "1"},
            "artifact": {"kind": "document-pages", "key": "workspace/x/adm_decision/decision-1/pages.json",
                         "sha256": "b" * 64, "pages": 1},
            "derivation": {"profile": "identifier-normalization-1", "originalHash": SHA,
                           "derivativeHash": "c" * 64},
            "policy": {"id": "policy-1", "revision": 1, "hash": "d" * 64},
            "inspection": {"receiptKey": "workspace/x/adm_decision/decision-1/inspection.json",
                           "hash": "e" * 64},
            "dataClass": "internal-non-sensitive", "status": "pending-review",
            "expiresAt": NOW + DAY, **extra}


def audit(**extra):
    return {"id": "audit-1", "op": "activate_policy", "kind": "adm_policy", "recordId": "policy-1",
            "revision": 2, "operator": "security-operator", "at": NOW, **extra}


def resolver(**extra):
    return {"id": "resolver-1", "revision": 1, "aliases": {"@parts/*": "src/parts/*"},
            "packages": {"@synthetic/ui": {"version": "1.0.0", "sha256": SHA}}, "jsonAssetFields": [],
            "status": "active", "expiresAt": NOW + DAY, **extra}


BUILDERS = {"adm_policy": policy, "adm_provenance": provenance, "adm_grant": grant,
            "adm_decision": decision, "adm_audit": audit, "adm_resolver": resolver}


@pytest.mark.parametrize("change", [{"aliases": {"@x/*": "../escape/*"}}, {"aliases": {"@x/*": "src/x"}},
                                    {"packages": {"Bad Name": {"version": "1", "sha256": SHA}}},
                                    {"packages": {"@s/ui": {"version": "1", "sha256": "short"}}}])
def test_resolver_profile_rejects_unsafe_aliases_and_package_identities(change):
    with pytest.raises(ValueError):
        records.seal("adm_resolver", resolver(**change))


@pytest.mark.parametrize("kind", sorted(BUILDERS))
def test_valid_record_of_each_kind_passes_after_sealing(kind):
    record = BUILDERS[kind]()
    sealed = records.seal(kind, record)
    assert records.validate(kind, sealed) == sealed
    if kind != "adm_audit":
        assert sealed["hash"] == records.digest_record(sealed)


def test_storage_owned_fields_do_not_change_the_hash():
    sealed = records.seal("adm_policy", policy())
    stored = {**sealed, "version": 1, "createdAt": NOW, "updatedAt": NOW}
    assert records.validate("adm_policy", stored)["hash"] == sealed["hash"]
    assert records.validate("adm_audit", {**audit(), "status": "active", "version": 1,
                                          "createdAt": NOW, "updatedAt": NOW})


@pytest.mark.parametrize("kind", sorted(BUILDERS))
def test_unknown_field_fails(kind):
    record = records.seal(kind, BUILDERS[kind]()) if kind != "adm_audit" else audit()
    with pytest.raises(ValueError):
        records.validate(kind, {**record, "unexpected": True})


def test_unknown_kind_fails():
    with pytest.raises(ValueError):
        records.validate("adm_other", policy())


@pytest.mark.parametrize("kind", sorted(BUILDERS))
@pytest.mark.parametrize("value", ["1", 0, 1.0, True, None])
def test_wrong_revision_type_fails(kind, value):
    with pytest.raises(ValueError):
        records.seal(kind, {**BUILDERS[kind](), "revision": value})


def test_http_public_url_fails():
    with pytest.raises(ValueError):
        records.seal("adm_provenance", provenance(publicUrl="http://example.test/reference"))
    with pytest.raises(ValueError):
        records.seal("adm_provenance", provenance(kind="fixture"))  # publicUrl is for public references


def test_sensitive_data_class_fails():
    with pytest.raises(ValueError):
        records.seal("adm_policy", policy(dataClasses=["synthetic", "sensitive"]))


def test_prompt_text_auto_is_rejected_in_v1():
    with pytest.raises(ValueError):
        records.seal("adm_policy", policy(promptText="auto"))


def test_reviewer_requirement_cannot_be_disabled():
    with pytest.raises(ValueError):
        records.seal("adm_policy", policy(requiresReviewer={"internal-non-sensitive": False}))


def test_admitted_internal_decision_requires_a_reviewer():
    with pytest.raises(ValueError):
        records.seal("adm_decision", decision(status="admitted", provenance={"id": "prov-1", "revision": 1}))
    with pytest.raises(ValueError):
        records.seal("adm_decision", decision(status="blocked"))


def test_decision_without_derivation_fails():
    record = decision()
    record.pop("derivation")
    with pytest.raises(ValueError):
        records.seal("adm_decision", record)


def test_hash_mismatch_fails():
    sealed = records.seal("adm_grant", grant())
    tampered = copy.deepcopy(sealed)
    tampered["scope"]["projectIds"] = ["p2"]
    with pytest.raises(ValueError):
        records.validate("adm_grant", tampered)
    with pytest.raises(ValueError):
        records.validate("adm_grant", {**sealed, "hash": "f" * 64})


def test_unknown_status_and_grant_operation_fail():
    with pytest.raises(ValueError):
        records.seal("adm_grant", grant(operations=["review-internal", "admin"]))
    with pytest.raises(ValueError):
        records.seal("adm_decision", decision(status="approved"))


def test_is_current_requires_active_status_and_future_expiry():
    active = records.seal("adm_policy", policy())
    assert records.is_current(active, NOW)
    assert not records.is_current(active, NOW + DAY)
    assert not records.is_current({**active, "status": "retired"}, NOW)
    assert records.is_current({**records.seal("adm_decision", decision(
        status="admitted", review={"actor": "reviewer-sub", "grantId": "grant-1", "grantRevision": 1, "at": NOW}))}, NOW)
    assert not records.is_current(records.seal("adm_decision", decision()), NOW)


def test_admission_kinds_are_storage_kinds_under_the_intake_owner():
    from workspace import storage
    assert set(records.KINDS) <= storage.KINDS
    assert records.INTAKE_OWNER == "intake:deployment"
    assert storage.key_for(records.INTAKE_OWNER, "adm_policy", "policy-1", "x.json")
